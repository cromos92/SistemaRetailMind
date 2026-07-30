# -*- coding: utf-8 -*-
"""READ-ONLY: por que la recepcion del DTE #17058 (EDEL -> NICK2) no descuenta la NC.

Responde:
  - las lineas del DTE original (Dte_Productos.stock) fueron reducidas por la NC?
  - por que camino se emitio la NC (tiene lineas? tiene productoTalla? unidades?)
  - hay movimientos de reversa al origen?
  - cuanto entraria al stock de NICK2 si se confirma la recepcion tal cual?

NO ESCRIBE NADA. Solo selects + prints.
"""
from django.db.models import Sum
from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, Producto_Talla,
    Productos_Recepcionados,
)

NUM = '17058'

print("=" * 110)
print("1) DTE(s) con numero_documento =", NUM)
print("=" * 110)
candidatos = list(
    Dte.objects.filter(numero_documento=NUM)
    .select_related('emisor', 'receptor', 'sucursal')
    .order_by('id')
)
for d in candidatos:
    print(f"  id={d.id} | {d.tipo_documento} | tipo_transaccion={d.tipo_transaccion} | "
          f"estado={d.estado_dte} | emisor={d.emisor.nombre if d.emisor else '-'} | "
          f"suc_emisora={d.sucursal.alias if d.sucursal else '-'} | "
          f"f_emision={d.fecha_emision} | f_recepcion={d.fecha_recepcion} | "
          f"uds_header={d.unidades_productos} | neto=${d.monto_neto} | c/iva=${d.monto_con_iva} | "
          f"es_nc={d.es_nota_credito} | doc_afectado={d.documento_afectado_id}")

traspasos = [d for d in candidatos if d.tipo_transaccion == 'TRASPASO']
if not traspasos:
    print("  !! No hay DTE de TRASPASO con ese numero. Abortando.")
    raise SystemExit

dte = traspasos[-1]
print(f"\n  --> Analizando id={dte.id}")
print(f"  referencias: {(dte.referencias or '')[:1500]}")

print()
print("=" * 110)
print("2) LINEAS del DTE original (todas, activas e inactivas)")
print("=" * 110)
lineas = list(
    Dte_Productos.objects.filter(dte=dte)
    .select_related('productoTalla__producto')
    .order_by('id')
)
tot_act = tot_inact = 0
for dp in lineas:
    pt = dp.productoTalla
    if dp.activo:
        tot_act += int(dp.stock or 0)
    else:
        tot_inact += int(dp.stock or 0)
    print(f"  dp={dp.id} | activo={dp.activo} | stock={dp.stock} | precio={dp.precio} | "
          f"sku={pt.sku if pt else 'SIN TALLA'} | talla={repr(pt.talla) if pt else '-'} | "
          f"pt_id={dp.productoTalla_id} | {(dp.descripcion or '')[:45]}")
print(f"\n  TOTAL activas={tot_act}  | TOTAL inactivas={tot_inact} | lineas={len(lineas)}")
print(f"  header unidades_productos={dte.unidades_productos}")

print()
print("=" * 110)
print("3) DOCUMENTOS HIJOS (documento_afectado = este DTE)")
print("=" * 110)
hijos = list(
    Dte.objects.filter(documento_afectado=dte)
    .select_related('sucursal')
    .order_by('id')
)
if not hijos:
    print("  (ninguno)")
for h in hijos:
    print(f"\n  HIJO id={h.id} | {h.tipo_documento} #{h.numero_documento} | estado={h.estado_dte} | "
          f"es_nc={h.es_nota_credito} | tipo_transaccion={h.tipo_transaccion} | "
          f"uds_header={h.unidades_productos} | neto=${h.monto_neto} | c/iva=${h.monto_con_iva} | "
          f"f_emision={h.fecha_emision} | resp={h.responsable} | "
          f"requiere_dev_fisica={getattr(h, 'requiere_devolucion_fisica', 'n/a')} | "
          f"descartado={getattr(h, 'descartado', 'n/a')}")
    print(f"    motivo_nc: {(h.motivo_nc or '')[:300]}")
    print(f"    referencias: {(h.referencias or '')[:400]}")
    hl = list(
        Dte_Productos.objects.filter(dte=h)
        .select_related('productoTalla')
        .order_by('id')
    )
    if not hl:
        print("    LINEAS: (NINGUNA)  <-- NC sin detalle: no se puede imputar a ninguna talla")
    for dp in hl:
        pt = dp.productoTalla
        print(f"    linea dp={dp.id} | activo={dp.activo} | stock={dp.stock} | precio={dp.precio} | "
              f"pt_id={dp.productoTalla_id} | sku={pt.sku if pt else 'SIN TALLA'} | "
              f"talla={repr(pt.talla) if pt else '-'} | {(dp.descripcion or '')[:50]}")
    print(f"    suma uds lineas hijo = {sum(int(x.stock or 0) for x in hl)}")

    movs_h = list(
        Movimientos_Producto.objects.filter(dte=h)
        .select_related('ProductoTalla', 'sucursal_origen', 'sucursal_destino')
        .order_by('id')
    )
    if not movs_h:
        print("    MOVIMIENTOS del hijo: (NINGUNO)  <-- la NC no movio stock")
    for m in movs_h:
        print(f"    mov={m.id} | {m.concepto} | {m.tipo_movimiento} | cant={m.cantidad} | "
              f"estado={m.estado} | origen={m.sucursal_origen.alias if m.sucursal_origen else '-'} | "
              f"destino={m.sucursal_destino.alias if m.sucursal_destino else '-'} | "
              f"sku={m.ProductoTalla.sku if m.ProductoTalla else '-'} | fecha={m.fecha}")

print()
print("=" * 110)
print("4) MOVIMIENTOS del DTE original")
print("=" * 110)
movs = list(
    Movimientos_Producto.objects.filter(dte=dte)
    .select_related('ProductoTalla', 'sucursal_origen', 'sucursal_destino')
    .order_by('concepto', 'id')
)
por_concepto = {}
for m in movs:
    por_concepto.setdefault(m.concepto, []).append(m)
for concepto, ms in por_concepto.items():
    print(f"  {concepto}: {len(ms)} movs | suma cantidad = {sum(int(x.cantidad or 0) for x in ms)}")
    for m in ms[:200]:
        print(f"      mov={m.id} | {m.tipo_movimiento} | cant={m.cantidad} | estado={m.estado} | "
              f"origen={m.sucursal_origen.alias if m.sucursal_origen else '-'} -> "
              f"destino={m.sucursal_destino.alias if m.sucursal_destino else '-'} | "
              f"sku={m.ProductoTalla.sku if m.ProductoTalla else '-'} | "
              f"talla={repr(m.ProductoTalla.talla) if m.ProductoTalla else '-'} | "
              f"obs={(m.observaciones or '')[:70]}")

print()
print("=" * 110)
print("5) Productos_Recepcionados de este DTE")
print("=" * 110)
recs = list(
    Productos_Recepcionados.objects.filter(dte=dte)
    .select_related('dte_producto__productoTalla')
    .order_by('id')
)
if not recs:
    print("  (ninguno - nunca se confirmo recepcion)")
for r in recs:
    pt = r.dte_producto.productoTalla if r.dte_producto else None
    print(f"  rec={r.id} | estado={r.estado} | esperada={r.cantidad_esperada} | "
          f"arribado={r.stockArribado} | danada={getattr(r, 'cantidad_danada', '-')} | "
          f"faltante={getattr(r, 'cantidad_faltante', '-')} | sobrante={getattr(r, 'cantidad_sobrante', '-')} | "
          f"sku={pt.sku if pt else '-'}")

print()
print("=" * 110)
print("6) LO QUE IMPORTA: por talla, esperado del DTE vs ya acreditado por NC viva")
print("=" * 110)
ya_por_talla = {}
for row in (
    Dte_Productos.objects
    .filter(
        dte__documento_afectado_id=dte.id,
        dte__es_nota_credito=True,
        dte__estado_dte__in=['EMITIDO', 'ACEPTADO'],
    )
    .values('productoTalla_id')
    .annotate(total=Sum('stock'))
):
    ya_por_talla[row['productoTalla_id']] = int(row['total'] or 0)

print(f"  mapa 'ya acreditado por NC' por productoTalla_id: {ya_por_talla or '(VACIO)'}")
print()
neto_deberia = 0
for dp in lineas:
    if not dp.activo:
        continue
    pt = dp.productoTalla
    ya = ya_por_talla.get(dp.productoTalla_id, 0)
    esperado = int(dp.stock or 0)
    neto = esperado - ya
    neto_deberia += neto
    flag = '  <<< DESCUADRE' if ya else ''
    print(f"  sku={pt.sku if pt else '-':>10} talla={str(pt.talla) if pt else '-':>4} | "
          f"DTE dice={esperado:4} | NC acredito={ya:4} | deberia entrar={neto:4}{flag}")
print(f"\n  ENTRARIA HOY a NICK2 (lo que usa confirmar_recepcion_api) = {tot_act}")
print(f"  DEBERIA entrar (neteando NC vivas)                        = {neto_deberia}")

print()
print("=" * 110)
print("7) Stock actual en origen y destino de cada SKU del DTE")
print("=" * 110)
mov_ref = (
    Movimientos_Producto.objects
    .filter(dte=dte, concepto='TRASPASO_SALIDA', sucursal_destino__isnull=False)
    .select_related('sucursal_destino', 'sucursal_origen')
    .first()
)
suc_dest = mov_ref.sucursal_destino if mov_ref else None
suc_orig = dte.sucursal
print(f"  origen={suc_orig.alias if suc_orig else '-'} (id={suc_orig.id if suc_orig else '-'}) | "
      f"destino={suc_dest.alias if suc_dest else 'NO IDENTIFICADO'} (id={suc_dest.id if suc_dest else '-'})")
skus = sorted({dp.productoTalla.sku for dp in lineas if dp.productoTalla})
for pt in (
    Producto_Talla.objects
    .filter(sku__in=skus)
    .select_related('producto__sucursal')
    .order_by('sku', 'producto__sucursal__alias')
):
    if pt.producto and pt.producto.sucursal_id in (
        getattr(suc_orig, 'id', None), getattr(suc_dest, 'id', None)
    ):
        print(f"  sku={pt.sku:>10} | talla={str(pt.talla):>4} | "
              f"suc={pt.producto.sucursal.alias:>6} | stock={pt.stock} | pt_id={pt.id}")

print()
print("FIN (read-only)")
