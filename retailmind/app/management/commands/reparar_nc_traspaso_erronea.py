# -*- coding: utf-8 -*-
"""Repone en el DESTINO la mercadería de un traspaso que se retiró por error
con una NC/ajuste pre-recepción, y la saca del ORIGEN al que volvió.

Caso que lo motiva (29-sep-2026): sobre el DTE 17172 (EDEL→NICK2) se usó
«Ajustar DTE emitido» con motivo «NO LLEGO» y salió la NC #981 por 27 uds
(BQ4639-012 T7-T11 y CN9675-403). El ajuste pre-recepción devolvió esas 27 uds
al stock de EDEL, dejó las líneas inactivas y la recepción posterior ya no las
ingresó a NICK2. La mercadería SÍ había llegado: NICK2 la tiene físicamente,
EDEL no. El sistema no tiene un camino para esto: volver a recepcionar ignora
las líneas inactivas, «Mercadería encontrada» exige una línea con faltante y
anular la NC no mueve stock.

Qué hace, por cada línea de la NC con talla:

  1. ORIGEN: egreso TRASPASO_SALIDA (dte = el traspaso) por la cantidad de la
     NC. Consume los lotes FIFO que repuso el ajuste y baja el stock plano.
     Aborta si el origen no tiene esas unidades (significa que ya se vendieron
     o movieron: hay que mirar antes de reparar).
  2. DESTINO: ingreso TRASPASO_ENTRADA (dte = el traspaso) por la misma
     cantidad, con el costo/sobreprecio del documento y lote FIFO. Aborta si
     el SKU no existe en el destino (crearlo antes desde «Crear en destino»).
  3. Si en el destino ya se sumaron unidades A MANO después de emitida la NC
     (AJUSTE_POSITIVO / AJUSTE_INVENTARIO_ENTRADA sobre esa talla —el «le sumo
     1 para poder venderlo»—), esas unidades ya están contadas: se revierten
     con un AJUSTE_NEGATIVO que cita el movimiento manual. Así el kardex del
     traspaso queda completo y el stock final es el físico.
  4. Deja una línea de recepción (Productos_Recepcionados RECEPCIONADO_OK)
     por cada línea repuesta, para que «Despachos recibidos» y el detalle del
     DTE muestren que esa mercadería entró. Se puede omitir con
     --sin-lineas-recepcion.
  5. Anota la reparación en `Dte.referencias` del traspaso.

NO toca los documentos: el DTE sigue con las líneas reducidas y la NC sigue
vigente. Si la NC ya se declaró al SII, el lado tributario se corrige aparte
(Nota de Débito que la referencie) — eso no es stock y este comando no lo hace.

Idempotente: cada movimiento lleva referencia_externa 'REPARACION_NC_<id>'; si
ya existe para esa talla, la línea se salta.

Por defecto corre en seco. Para escribir: --aplicar

    python manage.py reparar_nc_traspaso_erronea --nc-id 2205752
    python manage.py reparar_nc_traspaso_erronea --nc-id 2205752 --aplicar
    python manage.py reparar_nc_traspaso_erronea --folio 981 --emisor EDEL --aplicar
"""
import logging
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, Producto_Talla, Productos_Recepcionados, Sucursal,
)
from app.services import inventario_service

logger = logging.getLogger('app')

CONCEPTOS_MANUALES = ('AJUSTE_POSITIVO', 'AJUSTE_INVENTARIO_ENTRADA')
ESTADOS_RECEPCIONADO = (
    'RECEPCIONADO_COMPLETO', 'RECEPCIONADO_PARCIAL', 'RECEPCIONADO_SOBRANTE', 'EN_REGULARIZACION',
)


class Command(BaseCommand):
    help = (
        'Repone en el destino la mercadería retirada por error de un traspaso con NC/ajuste '
        'pre-recepción (y la saca del origen). Dry-run por defecto; --aplicar escribe.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--nc-id', type=int, help='Id (Dte.id) de la NC/ajuste emitido por error.')
        parser.add_argument('--folio', type=int, help='Folio de la NC (alternativa a --nc-id; exige --emisor).')
        parser.add_argument('--emisor', help='Alias de la sucursal que emitió la NC (con --folio).')
        parser.add_argument('--destino', help='Alias de la sucursal destino. Por defecto se deduce del traspaso.')
        parser.add_argument('--usuario', default='reparacion_nc',
                            help='Responsable que queda en los movimientos (default: reparacion_nc).')
        parser.add_argument('--sin-lineas-recepcion', action='store_true',
                            help='No crea líneas de Productos_Recepcionados para lo repuesto.')
        parser.add_argument('--no-revertir-manuales', action='store_true',
                            help='No revierte los ajustes manuales positivos hechos en el destino tras la NC.')
        parser.add_argument('--aplicar', action='store_true',
                            help='Escribe los cambios. Sin este flag solo muestra el plan.')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        aplicar = options['aplicar']
        usuario = (options['usuario'] or 'reparacion_nc')[:50]

        nc = self._cargar_nc(options)
        dte = nc.documento_afectado
        if dte is None or dte.tipo_transaccion != 'TRASPASO':
            raise CommandError('La NC no afecta a un traspaso: este comando solo repara traspasos.')
        if not getattr(nc, 'redujo_lineas_documento', False):
            raise CommandError(
                'La NC no redujo las líneas del documento (no fue un ajuste pre-recepción): '
                'esas unidades nunca salieron del documento, revisa el caso antes de reparar.'
            )
        if dte.estado_dte not in ESTADOS_RECEPCIONADO and not dte.fecha_recepcion:
            raise CommandError(
                f'El traspaso #{dte.numero_documento} está {dte.estado_dte}, todavía no se recepcionó: '
                'basta con volver a ajustar/recepcionar el documento; este comando es para documentos ya recibidos.'
            )
        if nc.estado_dte in ('ANULADO', 'CANCELADO'):
            raise CommandError('La NC está anulada: no hay nada que reparar.')

        origen = dte.sucursal
        destino = self._resolver_destino(dte, options.get('destino'))
        if origen is None or destino is None:
            raise CommandError('No se pudo determinar origen/destino del traspaso. Usa --destino ALIAS.')
        if origen.id == destino.id:
            raise CommandError('Origen y destino son la misma sucursal.')

        momento_nc = self._momento_nc(nc)
        tag = f'REPARACION_NC_{nc.id}'

        lineas_nc = list(
            Dte_Productos.objects.filter(dte=nc, productoTalla__isnull=False, stock__gt=0)
            .select_related('productoTalla__producto')
            .order_by('id')
        )
        if not lineas_nc:
            raise CommandError('La NC no tiene líneas con talla: no hay unidades que reponer.')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'NC #{nc.numero_documento} (id {nc.id}) sobre traspaso #{dte.numero_documento} '
            f'({origen.alias} -> {destino.alias}), emitida {nc.fecha_emision} {nc.hora or ""}'
        ))
        self.stdout.write(f'Modo: {"APLICAR" if aplicar else "SIMULACIÓN (sin cambios)"} · responsable: {usuario}')

        plan, errores = self._armar_plan(lineas_nc, dte, origen, destino, momento_nc, tag,
                                         revertir_manuales=not options['no_revertir_manuales'])
        self._imprimir_plan(plan, origen, destino)

        if errores:
            self.stdout.write(self.style.ERROR('\nNo se puede reparar:'))
            for e in errores:
                self.stdout.write(f'  - {e}')
            raise CommandError('Corrige lo anterior y vuelve a correr.')

        pendientes = [p for p in plan if not p['ya_reparada']]
        if not pendientes:
            self.stdout.write(self.style.SUCCESS('\nTodas las líneas ya estaban reparadas. Nada que hacer.'))
            return
        if not aplicar:
            self.stdout.write(self.style.WARNING(
                f'\nSimulación: {len(pendientes)} línea(s) por reparar. Para escribir agrega --aplicar.'
            ))
            return

        with transaction.atomic():
            Dte.objects.select_for_update().get(pk=dte.pk)
            for p in pendientes:
                self._aplicar_linea(p, dte, nc, origen, destino, usuario, tag,
                                    crear_recepcion=not options['sin_lineas_recepcion'])
            ahora = timezone.localtime()
            uds = sum(p['cantidad'] for p in pendientes)
            nota = (
                f"\n[REPARACION NC #{nc.numero_documento}] {ahora.strftime('%Y-%m-%d %H:%M')} {usuario}: "
                f"{uds} uds de {len(pendientes)} línea(s) repuestas en {destino.alias} y descontadas de "
                f"{origen.alias} (la NC se emitió por error, la mercadería sí llegó)."
            )
            dte.referencias = ((dte.referencias or '') + nota).strip()
            dte.save(update_fields=['referencias'])

        logger.info(
            'reparar_nc_traspaso_erronea: NC #%s (id %s) traspaso #%s %s->%s: %s líneas repuestas por %s',
            nc.numero_documento, nc.id, dte.numero_documento, origen.alias, destino.alias,
            len(pendientes), usuario,
        )
        self._imprimir_resultado(pendientes, origen, destino)

    # ------------------------------------------------------------------
    def _cargar_nc(self, options):
        if options.get('nc_id'):
            qs = Dte.objects.filter(id=options['nc_id'])
        elif options.get('folio') and options.get('emisor'):
            qs = Dte.objects.filter(
                numero_documento=options['folio'], sucursal__alias=options['emisor'],
                documento_afectado__isnull=False,
            )
        else:
            raise CommandError('Indica --nc-id, o --folio junto con --emisor.')
        qs = qs.select_related('documento_afectado__sucursal', 'sucursal')
        ncs = list(qs[:2])
        if not ncs:
            raise CommandError('No se encontró la NC.')
        if len(ncs) > 1:
            raise CommandError('Hay más de una NC con ese folio en esa sucursal: usa --nc-id.')
        nc = ncs[0]
        if not nc.es_nota_credito and nc.tipo_documento not in ('AJUSTE TRASPASO', 'AJUSTE TRASPASO POST'):
            raise CommandError(f'El documento {nc.id} no es una NC ni un ajuste de traspaso ({nc.tipo_documento}).')
        return nc

    def _resolver_destino(self, dte, alias):
        if alias:
            suc = Sucursal.objects.filter(alias=alias).first()
            if suc is None:
                raise CommandError(f'No existe la sucursal {alias!r}.')
            return suc
        mov = (
            Movimientos_Producto.objects
            .filter(dte=dte, concepto='TRASPASO_SALIDA', sucursal_destino__isnull=False)
            .select_related('sucursal_destino')
            .order_by('id')
            .first()
        )
        return mov.sucursal_destino if mov else None

    @staticmethod
    def _momento_nc(nc):
        """Fecha-hora local de emisión de la NC; los ajustes manuales del destino
        posteriores a esto son los que ya "adelantaron" unidades."""
        hora = nc.hora or datetime.min.time()
        naive = datetime.combine(nc.fecha_emision, hora)
        return timezone.make_aware(naive, timezone.get_current_timezone())

    def _armar_plan(self, lineas_nc, dte, origen, destino, momento_nc, tag, revertir_manuales):
        plan, errores = [], []
        for dp in lineas_nc:
            talla_origen = dp.productoTalla
            cantidad = int(dp.stock or 0)
            item = {
                'sku': talla_origen.sku,
                'talla': talla_origen.talla,
                'articulo': talla_origen.producto.articulo if talla_origen.producto else '',
                'cantidad': cantidad,
                'talla_origen': talla_origen,
                'talla_destino': None,
                'dp_nc': dp,
                'dp_dte': None,
                'stock_origen': int(talla_origen.stock or 0),
                'stock_destino': None,
                'manuales': [],
                'revertir': 0,
                'ya_reparada': False,
            }
            plan.append(item)

            if talla_origen.producto is None or talla_origen.producto.sucursal_id != origen.id:
                errores.append(f'SKU {talla_origen.sku}: la talla de la NC no pertenece al origen {origen.alias}.')
                continue

            item['ya_reparada'] = Movimientos_Producto.objects.filter(
                ProductoTalla=talla_origen, dte=dte, concepto='TRASPASO_SALIDA',
                referencia_externa=tag,
            ).exists()

            talla_destino = (
                Producto_Talla.objects
                .filter(sku=talla_origen.sku, producto__sucursal_id=destino.id)
                .select_related('producto')
                .order_by('id')
                .first()
            )
            item['talla_destino'] = talla_destino
            if talla_destino is None:
                errores.append(
                    f'SKU {talla_origen.sku} (talla {talla_origen.talla}) no existe en {destino.alias}: '
                    f'créalo primero («Crear en destino» en el detalle del DTE).'
                )
                continue
            item['stock_destino'] = int(talla_destino.stock or 0)

            item['dp_dte'] = (
                Dte_Productos.objects.filter(dte=dte, productoTalla=talla_origen).order_by('id').first()
            )

            if not item['ya_reparada'] and item['stock_origen'] < cantidad:
                errores.append(
                    f'SKU {talla_origen.sku} (talla {talla_origen.talla}): {origen.alias} tiene '
                    f'{item["stock_origen"]} y hay que sacar {cantidad}. Esas unidades ya se vendieron o '
                    f'movieron en el origen: revisa el kardex antes de reparar.'
                )

            if revertir_manuales:
                manuales = list(
                    Movimientos_Producto.objects
                    .filter(
                        ProductoTalla=talla_destino, concepto__in=CONCEPTOS_MANUALES,
                        estado='COMPLETADO', cantidad__gt=0, fecha__gte=momento_nc.date(),
                    )
                    .exclude(referencia_externa=tag)
                    .order_by('fecha', 'hora', 'id')
                )
                manuales = [
                    m for m in manuales
                    if timezone.make_aware(datetime.combine(m.fecha, m.hora or datetime.min.time()),
                                           timezone.get_current_timezone()) >= momento_nc
                ]
                item['manuales'] = manuales
                item['revertir'] = min(cantidad, sum(int(m.cantidad) for m in manuales))
        return plan, errores

    def _imprimir_plan(self, plan, origen, destino):
        self.stdout.write('')
        self.stdout.write(
            f"{'SKU':>8} {'Talla':>6} {'Artículo':14} {'NC':>3} | "
            f"{origen.alias:>6}: antes→desp | {destino.alias:>6}: antes→desp | manual | estado"
        )
        for p in plan:
            c = p['cantidad']
            so = p['stock_origen']
            sd = p['stock_destino']
            if p['ya_reparada']:
                estado = 'ya reparada (se salta)'
                o_txt = f'{so}→{so}'
                d_txt = f'{sd}→{sd}' if sd is not None else '?'
            else:
                estado = 'por reparar'
                o_txt = f'{so}→{so - c}'
                d_txt = f'{sd}→{sd + c - p["revertir"]}' if sd is not None else 'SKU no existe'
            manual = (
                f"-{p['revertir']} (rev. mov {', '.join(str(m.id) for m in p['manuales'])})"
                if p['revertir'] else '-'
            )
            self.stdout.write(
                f"{p['sku']:>8} {str(p['talla']):>6} {p['articulo'][:14]:14} {c:>3} | "
                f"{o_txt:>18} | {d_txt:>18} | {manual} | {estado}"
            )
        pend = [p for p in plan if not p['ya_reparada']]
        self.stdout.write(
            f"\nTotal por reparar: {sum(p['cantidad'] for p in pend)} uds en {len(pend)} línea(s); "
            f"ajustes manuales a revertir en {destino.alias}: {sum(p['revertir'] for p in pend)} uds."
        )

    def _aplicar_linea(self, p, dte, nc, origen, destino, usuario, tag, crear_recepcion):
        dp = p['dp_nc']
        cantidad = p['cantidad']
        talla_origen = p['talla_origen']
        talla_destino = p['talla_destino']
        motivo = (
            f'Reparación NC #{nc.numero_documento} emitida por error sobre DTE #{dte.numero_documento}: '
            f'la mercadería sí llegó a {destino.alias}'
        )

        inventario_service.egresar(
            talla_origen, cantidad, 'TRASPASO_SALIDA', usuario,
            sucursal_origen=origen, sucursal_destino=destino, dte=dte,
            precio_unitario=dp.precio,
            observaciones=f'{motivo} — sale de {origen.alias}'[:500],
            referencia_externa=tag,
        )
        precio_destino = int(getattr(talla_destino.producto, 'precioventa', 0) or 0)
        mov_entrada = inventario_service.ingresar(
            talla_destino, cantidad, 'TRASPASO_ENTRADA', usuario,
            sucursal_origen=origen, sucursal_destino=destino, dte=dte,
            costo_unitario=dp.costo, sobreprecio_unitario=dp.sobreprecio, precio_unitario=precio_destino,
            observaciones=f'{motivo} — entra a {destino.alias}'[:500],
            referencia_externa=tag,
        )
        if p['revertir'] > 0:
            ids = ', '.join(str(m.id) for m in p['manuales'])
            inventario_service.egresar(
                talla_destino, p['revertir'], 'AJUSTE_NEGATIVO', usuario,
                sucursal_origen=destino, sucursal_destino=destino, dte=dte,
                precio_unitario=precio_destino,
                observaciones=(
                    f'Revierte ajuste manual (mov {ids}): esas unidades venían en el DTE '
                    f'#{dte.numero_documento} y ya se reponen con la reparación de la NC #{nc.numero_documento}'
                )[:500],
                referencia_externa=tag,
            )
        if crear_recepcion:
            ahora = timezone.now()
            Productos_Recepcionados.objects.create(
                dte=dte,
                dte_producto=p['dp_dte'],
                producto_talla=talla_origen,
                sucursal_destino=destino,
                stockArribado=cantidad,
                cantidad_esperada=cantidad,
                cantidad_danada=0,
                cantidad_faltante=0,
                cantidad_sobrante=0,
                estado='RECEPCIONADO_OK',
                observaciones=(
                    f'[{timezone.localtime(ahora).strftime("%Y-%m-%d %H:%M")}] {motivo}. '
                    f'Repuesto por reparar_nc_traspaso_erronea ({usuario}).'
                ),
                fecha_recepcion=ahora,
                recepcionado_por=usuario,
                movimiento_ingreso=mov_entrada,
            )

    def _imprimir_resultado(self, pendientes, origen, destino):
        self.stdout.write(self.style.SUCCESS('\nAplicado. Stock actual:'))
        for p in pendientes:
            p['talla_origen'].refresh_from_db(fields=['stock'])
            p['talla_destino'].refresh_from_db(fields=['stock'])
            self.stdout.write(
                f"  SKU {p['sku']} talla {p['talla']}: {origen.alias}={p['talla_origen'].stock} "
                f"{destino.alias}={p['talla_destino'].stock}"
            )
