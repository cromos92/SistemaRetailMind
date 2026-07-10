"""
Script READ-ONLY para inspeccionar un DTE de traspaso, su recepción y su
regularización. NO modifica absolutamente nada (solo lee).

Uso (desde retailmind/, donde está manage.py):
    python _inspeccionar_dte_regularizacion.py            # usa 17021 por defecto
    python _inspeccionar_dte_regularizacion.py 17021      # número de documento

Muestra:
  - Estado del DTE (tipo doc, estado_dte, sucursal origen, fecha recepción).
  - Cada línea recepcionada (sku, estado, esperado/recibido/faltante/dañado).
  - TODOS los movimientos del DTE (concepto, tipo, cantidad, estado, origen→destino).
  - Chequeo anti doble-reversa: cuántos REGULARIZACION_TRASPASO quedan COMPLETADO
    vs ANULADO y cuántos ANULACION_REGULARIZACION (reversas) hay por sku.
  - Stock actual de la talla en origen y en destino, por sku.
"""
import os
import sys

# Windows: evitar que cp1252 rompa por caracteres especiales en prints.
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, 'reconfigure'):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django
django.setup()

from app.models import Dte, Productos_Recepcionados, Movimientos_Producto, Producto_Talla


def alias(suc):
    return getattr(suc, 'alias', None) or (str(suc.id) if suc else '-')


def linea(txt=''):
    print(txt)


def main():
    numero = int(sys.argv[1]) if len(sys.argv) > 1 else 17021
    dtes = list(
        Dte.objects.filter(numero_documento=numero, tipo_transaccion='TRASPASO')
        .select_related('sucursal', 'emisor', 'receptor')
        .order_by('id')
    )
    if not dtes:
        linea(f"No se encontró ningún DTE de traspaso con numero_documento={numero}.")
        # Puede haber homónimos con otro tipo_transaccion; avisar.
        otros = Dte.objects.filter(numero_documento=numero).count()
        if otros:
            linea(f"(Existen {otros} DTE con ese número pero NO de tipo TRASPASO.)")
        return

    linea("=" * 78)
    linea(f"INSPECCIÓN READ-ONLY — DTE traspaso #{numero}  ({len(dtes)} coincidencia/s)")
    linea("=" * 78)

    for dte in dtes:
        mov_salida = Movimientos_Producto.objects.filter(
            dte=dte, concepto='TRASPASO_SALIDA'
        ).select_related('sucursal_origen', 'sucursal_destino').first()
        suc_origen = dte.sucursal
        suc_destino = mov_salida.sucursal_destino if mov_salida else None

        linea('')
        linea(f"DTE id={dte.id}  tipo={dte.tipo_documento}  estado_dte={dte.estado_dte}")
        linea(f"  origen(sucursal DTE)={alias(suc_origen)}   destino={alias(suc_destino)}")
        linea(f"  fecha_emision={dte.fecha_emision}   fecha_recepcion={dte.fecha_recepcion}")

        # -------- Líneas recepcionadas --------
        recepciones = list(
            Productos_Recepcionados.objects.filter(dte=dte)
            .select_related('producto_talla', 'producto_talla__producto')
            .order_by('id')
        )
        linea('')
        linea(f"  LÍNEAS RECEPCIONADAS ({len(recepciones)}):")
        if not recepciones:
            linea("    (sin registros de recepción — DTE aún no recepcionado)")
        for r in recepciones:
            sku = r.producto_talla.sku if r.producto_talla else '-'
            linea(
                f"    id={r.id}  sku={sku}  estado={r.estado}  "
                f"esperado={r.cantidad_esperada}  recibido={r.stockArribado}  "
                f"faltante={r.cantidad_faltante}  danado={r.cantidad_danada}  "
                f"fecha_regulariz={r.fecha_regularizacion}"
            )

        # -------- Movimientos del DTE --------
        movs = list(
            Movimientos_Producto.objects.filter(dte=dte)
            .select_related('ProductoTalla', 'sucursal_origen', 'sucursal_destino')
            .order_by('id')
        )
        linea('')
        linea(f"  MOVIMIENTOS ({len(movs)}):")
        for m in movs:
            sku = m.ProductoTalla.sku if m.ProductoTalla else '-'
            linea(
                f"    id={m.id}  {m.concepto:<28} {m.tipo_movimiento:<8} "
                f"cant={m.cantidad:>5}  estado={m.estado:<11} "
                f"{alias(m.sucursal_origen)}->{alias(m.sucursal_destino)}  sku={sku}"
            )

        # -------- Chequeo anti doble-reversa --------
        linea('')
        linea("  CHEQUEO ANTI DOBLE-REVERSA (por sku de la línea):")
        skus = sorted({r.producto_talla.sku for r in recepciones if r.producto_talla})
        for sku in skus:
            reg_ok = Movimientos_Producto.objects.filter(
                dte=dte, ProductoTalla__sku=sku,
                concepto='REGULARIZACION_TRASPASO', estado='COMPLETADO'
            ).count()
            reg_anul = Movimientos_Producto.objects.filter(
                dte=dte, ProductoTalla__sku=sku,
                concepto='REGULARIZACION_TRASPASO', estado='ANULADO'
            ).count()
            reversas = Movimientos_Producto.objects.filter(
                dte=dte, ProductoTalla__sku=sku,
                concepto='ANULACION_REGULARIZACION'
            ).count()
            flag = '  <-- REVISAR: más de 1 reversa' if reversas > 1 else ''
            linea(
                f"    sku={sku}: REGULARIZACION_TRASPASO COMPLETADO={reg_ok} "
                f"ANULADO={reg_anul} | reversas(ANULACION_REGULARIZACION)={reversas}{flag}"
            )

            # Stock actual en origen y destino
            t_ori = Producto_Talla.objects.filter(
                sku=sku, producto__sucursal=suc_origen
            ).values_list('stock', flat=True).first() if suc_origen else None
            t_des = Producto_Talla.objects.filter(
                sku=sku, producto__sucursal=suc_destino
            ).values_list('stock', flat=True).first() if suc_destino else None
            linea(f"        stock actual  origen({alias(suc_origen)})={t_ori}  destino({alias(suc_destino)})={t_des}")

    linea('')
    linea("=" * 78)
    linea("FIN (solo lectura, no se modificó nada).")


if __name__ == '__main__':
    main()
