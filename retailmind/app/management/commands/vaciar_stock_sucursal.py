# -*- coding: utf-8 -*-
"""
Deja en STOCK 0 todas las tallas de una o más sucursales completas.

Pensado para sucursales que no deben figurar con existencias en los reportes
(ej. EDEL FALLADOS, NICK3). Por cada Producto_Talla con stock distinto de 0:

- stock > 0  → movimiento AJUSTE_INVENTARIO_SALIDA por -stock (consume lotes FIFO)
- stock < 0  → movimiento AJUSTE_INVENTARIO_ENTRADA por +|stock| (sin crear lote)

Después del barrido, cualquier lote FIFO que quede activo con disponible > 0
en la sucursal también se deja en 0 (drift lotes-vs-stock conocido).

SEGURIDAD:
- DRY-RUN POR DEFECTO: sin --aplicar solo muestra lo que haría.
- Con --aplicar escribe un snapshot JSON (_vaciado_stock_<ts>.json) con el
  estado previo de tallas y lotes + los ids de movimientos creados.
- --revertir <snapshot.json> restaura stock y lotes, y borra los movimientos
  creados por este comando. Se niega si el stock cambió después del vaciado
  (usar --forzar para restaurar igual).

Uso:
    python manage.py vaciar_stock_sucursal --sucursal "EDEL FALLADOS" --sucursal NICK3
    python manage.py vaciar_stock_sucursal --sucursal "EDEL FALLADOS" --sucursal NICK3 --aplicar
    python manage.py vaciar_stock_sucursal --revertir _vaciado_stock_20260814_120000.json
"""
import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from app.models import LoteProducto, Movimientos_Producto, Producto_Talla, Sucursal


class Command(BaseCommand):
    help = 'Deja en stock 0 todas las tallas de las sucursales indicadas (dry-run por defecto)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sucursal', action='append', default=[],
            help='Alias exacto o id de la sucursal a vaciar (repetible)',
        )
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Ejecuta de verdad (sin esto es dry-run)',
        )
        parser.add_argument(
            '--snapshot-dir', default='.',
            help='Carpeta donde escribir el snapshot de reversión (default: actual)',
        )
        parser.add_argument(
            '--revertir', default=None, metavar='SNAPSHOT.JSON',
            help='Revierte un vaciado anterior usando su snapshot',
        )
        parser.add_argument(
            '--forzar', action='store_true',
            help='Con --revertir: restaura aunque el stock haya cambiado después del vaciado',
        )

    # ------------------------------------------------------------------ #

    def handle(self, *args, **opts):
        if opts['revertir']:
            return self._revertir(opts['revertir'], opts['forzar'])

        if not opts['sucursal']:
            raise CommandError('Indica al menos una sucursal con --sucursal "ALIAS" (o su id)')

        sucursales = [self._resolver_sucursal(ref) for ref in opts['sucursal']]
        aplicar = opts['aplicar']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{'APLICANDO' if aplicar else 'DRY-RUN (nada se modifica)'} — vaciado de stock"
        ))

        resumen = []
        for s in sucursales:
            tallas = Producto_Talla.objects.filter(producto__sucursal=s).exclude(stock=0)
            pos = tallas.filter(stock__gt=0).aggregate(
                n=Count('id'), u=Sum('stock'), v=Sum(F('stock') * F('producto__costo')))
            neg = tallas.filter(stock__lt=0).aggregate(n=Count('id'), u=Sum('stock'))
            lotes = LoteProducto.objects.filter(
                producto_talla__producto__sucursal=s, activo=True, cantidad_disponible__gt=0)
            resumen.append((s, tallas, pos, neg, lotes.count()))
            self.stdout.write(
                f"  {s.alias} (id {s.id}): {pos['n'] or 0} tallas con {pos['u'] or 0} unidades "
                f"(${(pos['v'] or 0):,.0f} costo) a descontar; "
                f"{neg['n'] or 0} tallas negativas ({neg['u'] or 0} u) a subir a 0; "
                f"{lotes.count()} lotes FIFO activos a agotar"
            )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '\nDry-run. Para ejecutar de verdad agrega --aplicar'
            ))
            return

        # ------------------------- APLICAR ------------------------------ #
        # Todo en operaciones MASIVAS: la primera versión llamaba
        # registrar_movimiento_producto() talla por talla (~6 queries c/u) y con
        # RTT ~220ms a la BD remota tardaba >10 min. Ahora son ~6 queries por
        # sucursal + los bulk_create: segundos.
        ts = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        referencia = f'VACIADO-{ts}'
        snapshot = {
            'fecha': timezone.localtime().isoformat(),
            'referencia': referencia,
            'sucursales': [{'id': s.id, 'alias': s.alias} for s, *_ in resumen],
            'tallas': [],
            'lotes': [],
            'movimientos_creados': [],
        }

        hoy = timezone.localdate()
        ahora_hora = timezone.localtime().time()

        with transaction.atomic():
            for s, _tallas, _pos, _neg, _nlotes in resumen:
                # Lock de las filas a tocar (el POS puede estar vendiendo en paralelo)
                tallas = list(
                    Producto_Talla.objects.select_for_update()
                    .filter(producto__sucursal=s).exclude(stock=0)
                    .select_related('producto')
                )
                pt_ids = [pt.id for pt in tallas]

                # Snapshot de lotes ANTES de tocarlos (todos los de la sucursal
                # con disponible, tengan o no stock plano)
                for lote in LoteProducto.objects.filter(
                    producto_talla__producto__sucursal=s, activo=True, cantidad_disponible__gt=0
                ).values('id', 'producto_talla_id', 'cantidad_disponible', 'activo', 'agotado'):
                    snapshot['lotes'].append(lote)

                # Tallas que YA tienen kardex (a las demás con stock>0 se les crea
                # el saldo inicial legacy, igual que registrar_movimiento_producto)
                con_kardex = set(
                    Movimientos_Producto.objects.filter(ProductoTalla_id__in=pt_ids)
                    .values_list('ProductoTalla_id', flat=True).distinct()
                )

                nuevos = []
                for pt in tallas:
                    snapshot['tallas'].append(
                        {'pt_id': pt.id, 'sku': pt.sku, 'stock_antes': pt.stock}
                    )
                    prod = pt.producto
                    base = dict(
                        ProductoTalla=pt,
                        sucursal_origen=s,
                        sucursal_destino=s,
                        costo=prod.costo,
                        sobreprecio=prod.sobreprecio,
                        precio=prod.precioventa,
                        estado='COMPLETADO',
                        fecha=hoy,
                        hora=ahora_hora,
                    )
                    if pt.stock > 0 and pt.id not in con_kardex:
                        fecha_creacion = getattr(prod, 'fecha_creacion', None)
                        nuevos.append(Movimientos_Producto(**{
                            **base,
                            'cantidad': pt.stock,
                            'concepto': 'INGRESO_INICIAL',
                            'tipo_movimiento': 'INGRESO',
                            'responsable': 'Sistema',
                            'observaciones': 'Saldo inicial legacy',
                            'referencia_externa': 'SALDO_INICIAL',
                            'fecha': (timezone.localtime(fecha_creacion).date()
                                      if fecha_creacion else hoy),
                        }))
                    if pt.stock > 0:
                        nuevos.append(Movimientos_Producto(**{
                            **base,
                            'cantidad': -pt.stock,
                            'concepto': 'AJUSTE_INVENTARIO_SALIDA',
                            'tipo_movimiento': 'EGRESO',
                            'responsable': 'vaciar_stock_sucursal',
                            'observaciones': f'Vaciado de sucursal {s.alias}',
                            'referencia_externa': referencia,
                        }))
                    else:
                        nuevos.append(Movimientos_Producto(**{
                            **base,
                            'cantidad': -pt.stock,  # negativo → cantidad positiva
                            'concepto': 'AJUSTE_INVENTARIO_ENTRADA',
                            'tipo_movimiento': 'INGRESO',
                            'responsable': 'vaciar_stock_sucursal',
                            'observaciones': f'Vaciado de sucursal {s.alias} (normaliza negativo)',
                            'referencia_externa': referencia,
                        }))

                creados = Movimientos_Producto.objects.bulk_create(nuevos, batch_size=500)
                snapshot['movimientos_creados'].extend(
                    m.id for m in creados if m.id is not None
                )

                # Stock plano a 0: UN update masivo (no talla por talla)
                Producto_Talla.objects.filter(id__in=pt_ids).update(stock=0)

                # Lotes FIFO a 0 (equivale a consumirlos todos: el objetivo es 0)
                LoteProducto.objects.filter(
                    producto_talla__producto__sucursal=s, activo=True, cantidad_disponible__gt=0
                ).update(cantidad_disponible=0, agotado=True)

        ruta = os.path.join(opts['snapshot_dir'], f'_vaciado_stock_{ts}.json')
        with open(ruta, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=1)

        # Verificación final
        for s, *_ in resumen:
            restante = Producto_Talla.objects.filter(
                producto__sucursal=s).exclude(stock=0).count()
            lotes_rest = LoteProducto.objects.filter(
                producto_talla__producto__sucursal=s, activo=True, cantidad_disponible__gt=0
            ).count()
            marca = self.style.SUCCESS('OK') if restante == 0 and lotes_rest == 0 \
                else self.style.ERROR(f'QUEDAN {restante} tallas / {lotes_rest} lotes')
            self.stdout.write(f'  {s.alias}: {marca}')

        self.stdout.write(self.style.SUCCESS(
            f"\nListo. {len(snapshot['tallas'])} tallas dejadas en 0, "
            f"{len(snapshot['movimientos_creados'])} movimientos creados (ref {referencia}).\n"
            f"Snapshot de reversión: {ruta}\n"
            f"Para revertir: python manage.py vaciar_stock_sucursal --revertir {ruta}"
        ))

    # ------------------------------------------------------------------ #

    def _resolver_sucursal(self, ref):
        ref = str(ref).strip()
        qs = Sucursal.objects.all()
        s = qs.filter(id=int(ref)).first() if ref.isdigit() else qs.filter(alias__iexact=ref).first()
        if s is None:
            parecidas = list(
                qs.filter(alias__icontains=ref.split()[0] if ref.split() else ref)
                .values_list('alias', flat=True)[:10]
            )
            raise CommandError(
                f'Sucursal "{ref}" no encontrada. '
                + (f'Parecidas: {", ".join(parecidas)}' if parecidas else 'Revisa el alias exacto.')
            )
        return s

    def _revertir(self, ruta, forzar):
        if not os.path.exists(ruta):
            raise CommandError(f'No existe el snapshot {ruta}')
        with open(ruta, encoding='utf-8') as f:
            snap = json.load(f)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Revirtiendo vaciado {snap['referencia']} de "
            f"{', '.join(s['alias'] for s in snap['sucursales'])}"
        ))

        with transaction.atomic():
            # Un solo SELECT ... FOR UPDATE con todas las tallas del snapshot
            actuales = {
                pt.id: pt
                for pt in Producto_Talla.objects.select_for_update()
                .filter(id__in=[t['pt_id'] for t in snap['tallas']])
            }

            cambiadas = []
            a_restaurar = []
            for t in snap['tallas']:
                pt = actuales.get(t['pt_id'])
                if pt is None:
                    continue
                if pt.stock != 0 and not forzar:
                    cambiadas.append(f"{t['sku']} (stock actual {pt.stock})")
                    continue
                pt.stock = t['stock_antes']
                a_restaurar.append(pt)

            if cambiadas and not forzar:
                raise CommandError(
                    f'{len(cambiadas)} tallas cambiaron de stock después del vaciado '
                    f'(ej: {", ".join(cambiadas[:5])}). Nada se revirtió. '
                    f'Usa --forzar para restaurar igual (pisa el stock actual).'
                )

            Producto_Talla.objects.bulk_update(a_restaurar, ['stock'], batch_size=500)

            lotes_snap = {l['id']: l for l in snap['lotes']}
            lotes_obj = list(LoteProducto.objects.filter(id__in=lotes_snap))
            for lote in lotes_obj:
                datos = lotes_snap[lote.id]
                lote.cantidad_disponible = datos['cantidad_disponible']
                lote.activo = datos['activo']
                lote.agotado = datos['agotado']
            LoteProducto.objects.bulk_update(
                lotes_obj, ['cantidad_disponible', 'activo', 'agotado'], batch_size=500
            )

            borrados, _ = Movimientos_Producto.objects.filter(
                id__in=snap['movimientos_creados']
            ).delete()

        self.stdout.write(self.style.SUCCESS(
            f"Revertido: {len(snap['tallas'])} tallas restauradas, "
            f"{len(snap['lotes'])} lotes restaurados, {borrados} movimientos borrados."
        ))
