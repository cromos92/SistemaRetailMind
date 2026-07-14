# -*- coding: utf-8 -*-
"""
Diagnóstico READ-ONLY: ajustes emitidos sobre DTEs #17032 y #17033.

Muestra, por cada folio:
  - El/los DTE originales (traspaso) con ese numero_documento
  - Sus líneas Dte_Productos (stock actual vs histórico en referencias)
  - Documentos hijos (NC / AJUSTE TRASPASO) vía documento_afectado
  - Líneas de cada hijo (cantidad acreditada en la NC)
  - Movimientos_Producto asociados al original y a los hijos
  - Archivos TXT generados en media/documentos_electronicos/nc que calcen con el folio de la NC

NO modifica nada. Ejecutar desde retailmind/:
  python manage.py shell -c "exec(open('_diagnostico_ajuste_dte_17032_17033.py', encoding='utf-8').read())"
"""
import os
from django.conf import settings
from app.models import Dte, Dte_Productos, Movimientos_Producto

FOLIOS = [17032, 17033]

SEP = '=' * 90


def _fmt(v):
    return '-' if v is None else v


def mostrar_lineas(dte, titulo):
    lineas = Dte_Productos.objects.filter(dte=dte).select_related('productoTalla__producto')
    print(f"    {titulo} ({lineas.count()} lineas):")
    for dp in lineas:
        sku = dp.productoTalla.sku if dp.productoTalla else '-'
        talla = dp.productoTalla.talla if dp.productoTalla else '-'
        print(
            f"      [dp#{dp.id}] SKU={sku} T={talla} activo={dp.activo} "
            f"stock(cant)={dp.stock} precio={dp.precio} desc={_fmt(dp.descripcion)[:60]}"
        )


def mostrar_movimientos(dte, titulo):
    movs = Movimientos_Producto.objects.filter(dte=dte).select_related(
        'ProductoTalla', 'sucursal_destino'
    )
    print(f"    {titulo} ({movs.count()} movimientos):")
    for m in movs:
        sku = m.ProductoTalla.sku if m.ProductoTalla_id else '-'
        dest = m.sucursal_destino.alias if getattr(m, 'sucursal_destino_id', None) else '-'
        estado = getattr(m, 'estado', None)
        print(
            f"      [mov#{m.id}] {m.tipo_movimiento}/{m.concepto} SKU={sku} "
            f"cant={m.cantidad} destino={dest} estado={_fmt(estado)} "
            f"obs={_fmt(getattr(m, 'observaciones', None))[:80]}"
        )


def buscar_txt(folio_nc):
    txt_dir = os.path.join(settings.MEDIA_ROOT, 'documentos_electronicos', 'nc')
    if not os.path.isdir(txt_dir):
        return []
    return sorted(
        f for f in os.listdir(txt_dir)
        if f.startswith(f'NC_{folio_nc}_') and f.endswith('.txt')
    )


for folio in FOLIOS:
    print(SEP)
    print(f"FOLIO ORIGINAL #{folio}")
    print(SEP)
    originales = (
        Dte.objects.filter(numero_documento=folio)
        .select_related('sucursal', 'emisor', 'receptor')
        .order_by('id')
    )
    if not originales.exists():
        print("  (sin DTEs con ese numero_documento)")
        continue

    for dte in originales:
        print(
            f"\n  DTE id={dte.id} tipo={dte.tipo_documento} trans={dte.tipo_transaccion} "
            f"estado={dte.estado_dte} suc={dte.sucursal.alias if dte.sucursal_id else '-'}"
        )
        print(
            f"    fecha_emision={dte.fecha_emision} fecha_recepcion={_fmt(dte.fecha_recepcion)} "
            f"unidades={dte.unidades_productos} neto={dte.monto_neto} total={dte.monto_con_iva}"
        )
        refs = (dte.referencias or '').strip()
        if refs:
            print("    referencias/bitacora:")
            for ln in refs.splitlines():
                if ln.strip():
                    print(f"      | {ln.strip()[:120]}")

        mostrar_lineas(dte, "Lineas del DTE original")
        mostrar_movimientos(dte, "Movimientos del DTE original")

        hijos = (
            Dte.objects.filter(documento_afectado=dte)
            .select_related('sucursal')
            .order_by('id')
        )
        print(f"\n    Documentos hijos (NC/AJUSTE): {hijos.count()}")
        for h in hijos:
            print(
                f"\n    >> HIJO id={h.id} tipo={h.tipo_documento} folio={h.numero_documento} "
                f"es_nc={h.es_nota_credito} estado={h.estado_dte} "
                f"unidades={h.unidades_productos} neto={h.monto_neto} total={h.monto_con_iva}"
            )
            print(f"       motivo_nc={_fmt(h.motivo_nc)}")
            print(f"       referencias={_fmt((h.referencias or '')[:160])}")
            mostrar_lineas(h, "Lineas del hijo (cantidad acreditada)")
            mostrar_movimientos(h, "Movimientos del hijo")
            if h.es_nota_credito:
                txts = buscar_txt(h.numero_documento)
                if txts:
                    print(f"       TXT en media/documentos_electronicos/nc: {txts}")
                    ruta = os.path.join(
                        settings.MEDIA_ROOT, 'documentos_electronicos', 'nc', txts[-1]
                    )
                    print(f"       --- contenido {txts[-1]} ---")
                    with open(ruta, encoding='utf-8') as fh:
                        for ln in fh.read().splitlines():
                            print(f"       {ln}")
                    print("       --- fin TXT ---")
                else:
                    print("       (sin TXT en disco para ese folio de NC)")

print(SEP)
print("FIN DIAGNOSTICO (read-only, nada fue modificado)")
