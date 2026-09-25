"""
Deshace UNA línea cargada por el agente "Cargar desde factura" (o por el modal
Crear Producto Manual) que entró en la ficha equivocada, y opcionalmente
restaura los precios de esa ficha.

Caso que lo originó (25-09-2026, sesión #2, factura 10908 de Río Elqui): las
dos líneas «12-REBI-1» (NEGRO y PLATA) entraron en la misma ficha #139320
(BLACK) porque la identidad era artículo + marca; además la venta bajó de
34.990 a 29.990 y el costo quedó en el precio de lista (15.990) en vez del
neto con descuento (14.391).

Un ingreso manual vive en cinco tablas (ver memoria "Ingreso manual: el DTE
vive en 5 tablas"): la línea de compra (Compras_Producto +
Compras_Producto_Talla), la recepción (Productos_Recepcionados), la línea del
DTE (Dte_Productos), el movimiento de kardex (Movimientos_Producto) y el lote
FIFO (LoteProducto, ligado al movimiento). El comando se ancla en la LÍNEA DE
COMPRA, que es la que identifica sin ambigüedad qué se cargó, y desde ahí
llega a las otras cuatro. Por cada talla: baja el stock, borra el lote (solo
si sigue completo: si ya se vendió de él, se detiene), la recepción, la
línea del DTE y el movimiento; al final borra la línea de compra.

Uso (desde retailmind/; sin --apply solo muestra lo que haría):
    python manage.py revertir_ingreso_agente --sesion 2 --factura 1 --linea 2
    python manage.py revertir_ingreso_agente --sesion 2 --factura 1 --linea 2 --apply
    # restaurando además la venta y el costo de la ficha (y sus gemelas de otras bodegas):
    python manage.py revertir_ingreso_agente --sesion 2 --factura 1 --linea 2 --apply \
        --precioventa 34990 --costo 14391 --tambien-otras-bodegas
    # o directo por la línea de compra:
    python manage.py revertir_ingreso_agente --compra-producto 12345 --apply
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from app.models import (
    CargaFacturaPdf, Compras_Producto, Compras_Producto_Talla, Dte_Productos,
    LoteProducto, Movimientos_Producto, Producto, Producto_Talla,
    Productos_Recepcionados,
)
from app.services.carga_factura.facturas import resolver_dte
from app.services.carga_factura.precios import fmt
from app.utils_producto_match import normalizar_articulo


class Command(BaseCommand):
    help = ('Deshace una línea cargada por el agente / Crear Manual que entró en la ficha '
            'equivocada. Sin --apply no escribe nada.')

    def add_arguments(self, parser):
        parser.add_argument('--sesion', type=int, help='Id de la sesión del agente (CargaFacturaPdf)')
        parser.add_argument('--factura', type=int, default=0, help='Índice de la factura en la sesión (0 = primera)')
        parser.add_argument('--linea', type=int, help='N° de la línea (1 = primera) tal como la muestra la tarjeta')
        parser.add_argument('--compra-producto', type=int, default=None,
                            help='Id de Compras_Producto de la línea (en vez de --sesion/--factura/--linea)')
        parser.add_argument('--cual', choices=['ultimo', 'primero'], default='ultimo',
                            help='Si la misma talla tiene varios ingresos iguales contra el DTE, '
                                 'cuál es el de esta línea (default: el último cargado)')
        parser.add_argument('--precioventa', type=int, default=None,
                            help='Restaurar este precio de venta en la ficha (y lotes activos)')
        parser.add_argument('--costo', type=int, default=None,
                            help='Corregir este costo en la ficha, sus lotes activos y las líneas '
                                 'del DTE que quedan')
        parser.add_argument('--tambien-otras-bodegas', action='store_true',
                            help='Aplicar --precioventa también a las fichas de la misma identidad '
                                 'en otras bodegas (las que el modal sincronizó); el costo solo va '
                                 'a la ficha cargada')
        parser.add_argument('--apply', action='store_true', help='Escribir (sin esto, solo muestra)')

    # ----------------------------------------------------------- resolver

    def _linea_de_compra(self, opts):
        """Compras_Producto de la línea + (dte, producto, cantidades esperadas por talla)."""
        if opts['compra_producto']:
            cp = Compras_Producto.objects.select_related('compras', 'sucursal_destino').filter(
                id=opts['compra_producto']).first()
            if cp is None:
                raise CommandError(f'No existe Compras_Producto id={opts["compra_producto"]}')
            return cp, None, None
        if not opts['sesion'] or not opts['linea']:
            raise CommandError('Indica --sesion y --linea (o --compra-producto).')
        sesion = CargaFacturaPdf.objects.filter(id=opts['sesion']).first()
        if sesion is None:
            raise CommandError(f'No existe la sesión {opts["sesion"]}')
        try:
            data = sesion.facturas[opts['factura']]
        except (IndexError, TypeError):
            raise CommandError(f'La sesión {sesion.id} no tiene la factura índice {opts["factura"]}')
        resultado = data.get('_resultado') or {}
        filas = [f for f in resultado.get('lineas', []) if f.get('n') == opts['linea']]
        if not filas or filas[0].get('estado') != 'OK':
            raise CommandError(f'La línea {opts["linea"]} de la factura {data.get("folio")} no se '
                               f'cargó (estado: {filas[0].get("estado") if filas else "sin resultado"})')
        fila = filas[0]
        linea_json = data['lineas'][opts['linea'] - 1]
        dte = resolver_dte(data, data.get('dte_id'), f'factura {data.get("folio")}')
        producto = Producto.objects.select_related('sucursal').filter(id=fila.get('producto_id')).first()
        if producto is None:
            raise CommandError(f'No existe el producto #{fila.get("producto_id")} de esa línea')
        # Líneas de compra de ese código en esa bodega, en el orden en que se
        # cargaron: la k-ésima corresponde a la k-ésima línea de la factura
        # con el mismo código (dos colores del mismo código = dos líneas).
        candidatas = list(Compras_Producto.objects.filter(
            compras__empresa_id=dte.emisor_id, compras__nombre__startswith='Compra Manual -',
            sucursal_destino_id=producto.sucursal_id,
            nombre__iexact=producto.articulo).order_by('id'))
        candidatas = [cp for cp in candidatas
                      if normalizar_articulo(cp.nombre) == normalizar_articulo(linea_json['articulo'])]
        mismas = [f for f in resultado.get('lineas', [])
                  if f.get('estado') == 'OK' and f.get('producto_id') == producto.id
                  and normalizar_articulo(f.get('articulo')) == normalizar_articulo(linea_json['articulo'])]
        k = [f['n'] for f in mismas].index(opts['linea'])
        # Las de esta carga son las últimas len(mismas) de la lista.
        del_lote = candidatas[-len(mismas):] if mismas else []
        if len(del_lote) != len(mismas):
            raise CommandError('No pude identificar las líneas de compra de esta carga: '
                               f'hay {len(candidatas)} línea(s) de compra de {producto.articulo} y '
                               f'{len(mismas)} línea(s) cargadas. Usa --compra-producto.')
        cp = del_lote[k]
        esperadas = {str(t).strip().upper(): int(c) for t, c in (linea_json.get('tallas') or {}).items()}
        return cp, dte, esperadas

    # ------------------------------------------------------------- flujo

    def handle(self, *args, **opts):
        w = self.stdout.write
        cp, dte, esperadas = self._linea_de_compra(opts)
        cpts = list(Compras_Producto_Talla.objects.filter(compra_producto=cp)
                    .select_related('producto_talla__producto__sucursal').order_by('id'))
        if not cpts:
            raise CommandError(f'La línea de compra #{cp.id} no tiene tallas.')
        producto = cpts[0].producto_talla.producto if cpts[0].producto_talla else None
        if producto is None:
            raise CommandError('Las tallas de la línea de compra no apuntan a un producto.')
        if dte is None:
            rec = Productos_Recepcionados.objects.filter(compra_producto_talla__in=cpts).select_related('dte').first()
            dte = rec.dte if rec else None
        w(self.style.MIGRATE_HEADING(
            f'Línea de compra #{cp.id} «{cp.nombre}» ({cp.compras.nombre}) → ficha #{producto.id} '
            f'{producto.sucursal.alias} «{producto.descripcion}» · DTE {getattr(dte, "numero_documento", "?")} '
            f'(id={getattr(dte, "id", "?")})'))
        total_cp = sum(c.stock for c in cpts)
        if esperadas and sum(esperadas.values()) != total_cp:
            raise CommandError(f'La línea de compra suma {total_cp} u y la línea de la factura '
                               f'{sum(esperadas.values())} u: no es la misma línea. Usa --compra-producto.')

        plan, problemas = [], []
        for cpt in cpts:
            pt = cpt.producto_talla
            q = int(cpt.stock or 0)
            if pt is None or q <= 0:
                continue
            movs = Movimientos_Producto.objects.filter(
                ProductoTalla=pt, dte=dte, concepto='INGRESO_MANUAL', cantidad=q).order_by('id')
            mov = movs.last() if opts['cual'] == 'ultimo' else movs.first()
            lote = LoteProducto.objects.filter(movimiento=mov).first() if mov else None
            rec = Productos_Recepcionados.objects.filter(compra_producto_talla=cpt).first()
            dte_prod = rec.dte_producto if rec and rec.dte_producto_id else None
            if dte_prod is None and dte is not None:
                dp = Dte_Productos.objects.filter(dte=dte, productoTalla=pt, stock=q).order_by('id')
                dte_prod = dp.last() if opts['cual'] == 'ultimo' else dp.first()
            fila = {'cpt': cpt, 'pt': pt, 'q': q, 'mov': mov, 'lote': lote, 'rec': rec, 'dte_prod': dte_prod}
            plan.append(fila)
            if mov is None:
                problemas.append(f'talla {pt.talla}: no encuentro el movimiento INGRESO_MANUAL de {q} u')
            if lote is not None and lote.cantidad_disponible != lote.cantidad_inicial:
                problemas.append(f'talla {pt.talla}: del lote #{lote.id} ya se vendieron '
                                 f'{lote.cantidad_inicial - lote.cantidad_disponible} u; no se puede deshacer limpio')
            if int(pt.stock or 0) < q:
                problemas.append(f'talla {pt.talla}: stock actual {pt.stock} < {q} a descontar')
            w(f'  talla {pt.talla:<6} sku {pt.sku}: stock {pt.stock} → {int(pt.stock or 0) - q} · '
              f'mov #{getattr(mov, "id", "?")} · lote #{getattr(lote, "id", "-")} · '
              f'recepción #{getattr(rec, "id", "-")} · línea DTE #{getattr(dte_prod, "id", "-")} · '
              f'compra-talla #{cpt.id}')
        w(f'  total: {total_cp} u en {len(plan)} talla(s)')
        for p in problemas:
            w(self.style.ERROR(f'  ✗ {p}'))
        if problemas:
            raise CommandError('Hay problemas (arriba): no se escribió nada.')

        fichas_precio = self._fichas_para_precio(producto, opts)
        if opts['precioventa'] is not None or opts['costo'] is not None:
            for n, f in enumerate(fichas_precio):
                costo_nuevo = opts['costo'] if (opts['costo'] is not None and n == 0) else None
                w(f'  precios ficha #{f.id} {f.sucursal.alias}: venta {fmt(f.precioventa)} → '
                  f'{fmt(opts["precioventa"]) if opts["precioventa"] is not None else "igual"} · costo '
                  f'{fmt(f.costo)} → {fmt(costo_nuevo) if costo_nuevo is not None else "igual"}')

        if not opts['apply']:
            w(self.style.WARNING('\nVISTA PREVIA: no se escribió nada. Repite con --apply.'))
            return

        with transaction.atomic():
            for fila in plan:
                pt = fila['pt']
                Producto_Talla.objects.filter(id=pt.id).update(stock=int(pt.stock or 0) - fila['q'])
                if fila['lote'] is not None:
                    fila['lote'].delete()
                if fila['rec'] is not None:
                    fila['rec'].delete()
                if fila['dte_prod'] is not None:
                    fila['dte_prod'].delete()
                if fila['mov'] is not None:
                    fila['mov'].delete()
                fila['cpt'].delete()
            if not Compras_Producto_Talla.objects.filter(compra_producto=cp).exists():
                cp.delete()
            self._restaurar_precios(fichas_precio, dte, opts)
        w(self.style.SUCCESS(f'Listo: {total_cp} u descontadas de la ficha #{producto.id} y registros borrados.'))

    # ------------------------------------------------------------ precios

    def _fichas_para_precio(self, producto, opts):
        if opts['precioventa'] is None and opts['costo'] is None:
            return []
        fichas = [producto]
        if opts['tambien_otras_bodegas']:
            fichas += list(Producto.objects.filter(
                atributo1_id=producto.atributo1_id, atributo2_id=producto.atributo2_id,
                atributo3_id=producto.atributo3_id, categoria_id=producto.categoria_id,
            ).exclude(id=producto.id).select_related('sucursal')
                .filter(articulo__iexact=producto.articulo))
        return fichas

    def _restaurar_precios(self, fichas, dte, opts):
        from app.services.historial_precios import registrar_cambios_precio

        for n, f in enumerate(fichas):
            anteriores = {'costo': f.costo, 'precioventa': f.precioventa}
            if opts['precioventa'] is not None:
                f.precioventa = opts['precioventa']
            # El costo solo en la ficha cargada (la primera): el de las gemelas
            # de otras bodegas viene de sus propias compras.
            if opts['costo'] is not None and n == 0:
                f.costo = opts['costo']
            f.save(update_fields=['precioventa', 'costo'])
            registrar_cambios_precio(f, anteriores, motivo='revertir_ingreso_agente', tipo_cambio='MANUAL')
            lotes = LoteProducto.objects.filter(producto_talla__producto=f, activo=True, agotado=False)
            if opts['precioventa'] is not None:
                lotes.update(precio_venta_unitario=opts['precioventa'])
            if opts['costo'] is not None:
                # Solo los lotes de ESTE DTE: el costo con descuento es de esta factura.
                lotes.filter(dte=dte).update(costo_unitario=opts['costo'])
                for dp in Dte_Productos.objects.filter(dte=dte, productoTalla__producto=f):
                    dp.costo = dp.precio_unitario = opts['costo']
                    dp.monto_item = opts['costo'] * int(dp.stock or 0)
                    dp.save(update_fields=['costo', 'precio_unitario', 'monto_item'])
        if fichas:
            self.stdout.write(f'  precios restaurados en {len(fichas)} ficha(s)')
