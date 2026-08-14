# -*- coding: utf-8 -*-
"""
READ-ONLY: por que el articulo 403718L-BKSL sale con DOS precios distintos en
las etiquetas. Mide catalogo, duplicados, campanas, historial de impresion y
replica la logica de precio de views_etiquetas_zebra para los ultimos
documentos de despacho que lo movieron. NO escribe nada.

Uso (desde retailmind/):
  python manage.py shell -c "exec(open('_diag_etiqueta_403718L_readonly.py', encoding='utf-8').read())"
"""
from collections import defaultdict

from django.db.models import Q, Count
from django.utils import timezone

from app.models import (
    Producto, Producto_Talla, Sucursal, Dte, Dte_Productos,
    Traspaso, Traspaso_Detalle, HistorialImpresionEtiqueta,
    DetalleImpresionEtiqueta, Movimientos_Producto,
)

ARTICULO = '403718L-BKSL'
RAIZ = '403718'          # para pillar variantes de codigo (sufijos, espacios)

sep = lambda t: print('\n' + '=' * 110 + '\n' + t + '\n' + '=' * 110)


# ----------------------------------------------------------------------
# 1. FICHAS DE CATALOGO (Producto es POR SUCURSAL)
# ----------------------------------------------------------------------
sep('1. FICHAS Producto QUE MATCHEAN EL ARTICULO')

productos = list(
    Producto.objects
    .filter(Q(articulo__iexact=ARTICULO) | Q(articulo__icontains=RAIZ))
    .select_related('sucursal', 'sucursal__empresa', 'atributo1', 'atributo2', 'categoria')
    .order_by('articulo', 'sucursal__alias', 'id')
)

if not productos:
    print('NO existe ningun Producto con ese articulo. Revisar el codigo exacto.')
else:
    print(f"{'prod_id':>8} {'articulo':<20} {'sucursal':<14} {'empresa':<18} "
          f"{'marca':<10} {'color':<10} {'costo':>8} {'sobrep':>7} {'PVENTA':>9} "
          f"{'tallas':>6} {'stock':>7} {'creado':<10} {'excl.anal':>9}")
    print('-' * 150)
    for p in productos:
        agg = Producto_Talla.objects.filter(producto=p).aggregate(
            n=Count('id'))
        stock = sum((pt.stock or 0) for pt in Producto_Talla.objects.filter(producto=p))
        creado = p.fecha_creacion.strftime('%d/%m/%y') if p.fecha_creacion else '-'
        print(f"{p.id:>8} {p.articulo[:20]:<20} "
              f"{(p.sucursal.alias if p.sucursal else '-')[:14]:<14} "
              f"{(p.sucursal.empresa.nombre if p.sucursal and p.sucursal.empresa else '-')[:18]:<18} "
              f"{(p.atributo1.valor if p.atributo1 else '-')[:10]:<10} "
              f"{(p.atributo2.valor if p.atributo2 else '-')[:10]:<10} "
              f"{p.costo or 0:>8,} {p.sobreprecio or 0:>7,} {p.precioventa or 0:>9,} "
              f"{agg['n'] or 0:>6} {stock:>7} {creado:<10} "
              f"{'SI' if p.excluir_de_analitica else '':>9}")

# ---- duplicados dentro de la MISMA sucursal ----
sep('1b. FICHAS DUPLICADAS EN LA MISMA SUCURSAL (misma tienda, 2 fichas)')
por_suc = defaultdict(list)
for p in productos:
    por_suc[(p.articulo.strip().upper(), p.sucursal_id)].append(p)

hay_dup = False
for (art, suc_id), lista in sorted(por_suc.items()):
    if len(lista) > 1:
        hay_dup = True
        precios = sorted({p.precioventa or 0 for p in lista})
        alias = lista[0].sucursal.alias if lista[0].sucursal else suc_id
        marca = 'PRECIOS DISTINTOS <<<<<<' if len(precios) > 1 else 'mismo precio'
        print(f"  {art} en {alias}: {len(lista)} fichas -> ids {[p.id for p in lista]} "
              f"precios {['{:,}'.format(x) for x in precios]}  {marca}")
if not hay_dup:
    print('  (ninguna: una sola ficha por sucursal)')


# ----------------------------------------------------------------------
# 2. SKUs / TALLAS: aca se ve si dos etiquetas del mismo articulo
#    y la misma tienda llevan precio distinto
# ----------------------------------------------------------------------
sep('2. SKUs (Producto_Talla) CON EL PRECIO QUE LLEVARIA LA ETIQUETA')

tallas = list(
    Producto_Talla.objects
    .filter(producto__in=productos)
    .select_related('producto', 'producto__sucursal')
    .order_by('producto__sucursal__alias', 'talla', 'sku')
)

print(f"{'sku':>14} {'talla':<8} {'sucursal':<14} {'prod_id':>8} {'PVENTA':>9} {'stock':>7} {'articulo':<20}")
print('-' * 95)
for pt in tallas:
    p = pt.producto
    print(f"{pt.sku:>14} {str(pt.talla or '-')[:8]:<8} "
          f"{(p.sucursal.alias if p.sucursal else '-')[:14]:<14} {p.id:>8} "
          f"{p.precioventa or 0:>9,} {pt.stock or 0:>7} {p.articulo[:20]:<20}")

# resumen por sucursal: cuantos precios distintos conviven
sep('2b. PRECIOS DISTINTOS CONVIVIENDO POR SUCURSAL (esto es lo que sale en la etiqueta)')
precios_suc = defaultdict(set)
for pt in tallas:
    p = pt.producto
    alias = p.sucursal.alias if p.sucursal else '-'
    precios_suc[alias].add(p.precioventa or 0)
for alias, pset in sorted(precios_suc.items()):
    flag = '  <<<<<< DOS O MAS PRECIOS EN LA MISMA TIENDA' if len(pset) > 1 else ''
    print(f"  {alias:<16} {sorted(pset)}{flag}")

# ---- SKU repetido en mas de una fila (legacy) ----
sep('2c. SKUs REPETIDOS (el mismo codigo de barras en 2 filas distintas)')
skus = [pt.sku for pt in tallas]
repes = (Producto_Talla.objects.filter(sku__in=skus)
         .values('sku').annotate(n=Count('id')).filter(n__gt=1).order_by('-n'))
if not repes:
    print('  (ninguno)')
for r in repes:
    filas = (Producto_Talla.objects.filter(sku=r['sku'])
             .select_related('producto', 'producto__sucursal'))
    detalle = ', '.join(
        f"pt={f.id}/prod={f.producto_id}/{(f.producto.sucursal.alias if f.producto.sucursal else '-')}"
        f"/${f.producto.precioventa or 0:,}" for f in filas)
    print(f"  sku {r['sku']} x{r['n']}: {detalle}")


# ----------------------------------------------------------------------
# 3. CAMPANAS DE LIQUIDACION VIGENTES (reescriben precioventa)
# ----------------------------------------------------------------------
sep('3. CAMPANAS DE LIQUIDACION SOBRE ESTAS FICHAS')
try:
    from app.models import CampanaLiquidacionProducto
    items = (CampanaLiquidacionProducto.objects
             .filter(producto__in=productos)
             .select_related('campana', 'producto', 'producto__sucursal')
             .order_by('-campana__fecha_inicio'))
    if not items:
        print('  (ninguna)')
    for it in items:
        c = it.campana
        try:
            sucs = ', '.join(s.alias for s in c.sucursales.all())
        except Exception:
            sucs = '?'
        desde = c.fecha_inicio.strftime('%d/%m/%y') if c.fecha_inicio else '-'
        hasta = c.fecha_fin.strftime('%d/%m/%y') if c.fecha_fin else 'sin fin'
        print(f"  prod={it.producto_id} ({it.producto.sucursal.alias if it.producto.sucursal else '-'}) "
              f"campana='{c.nombre}' estado={c.estado} tipo={c.tipo_regla} activo_item={it.activo} "
              f"lista=${int(it.precio_original or 0):,} liq=${int(it.precio_liquidacion or 0):,} "
              f"desde={desde} hasta={hasta} sucursales=[{sucs}]")
except Exception as exc:
    print(f'  no se pudo consultar campanas: {exc}')


# ----------------------------------------------------------------------
# 4. QUE PRECIO SE IMPRIMIO REALMENTE (historial de etiquetas)
# ----------------------------------------------------------------------
sep('4. HISTORIAL REAL DE ETIQUETAS IMPRESAS PARA ESTOS SKUs')
skus_str = {str(s) for s in skus}
detalles = (DetalleImpresionEtiqueta.objects
            .filter(Q(sku__in=skus_str) | Q(articulo__icontains=RAIZ))
            .select_related('historial', 'historial__sucursal', 'historial__usuario')
            .order_by('-historial__fecha_impresion')[:60])
if not detalles:
    print('  (sin impresiones registradas)')
else:
    print(f"{'fecha':<17} {'origen':<17} {'doc':<10} {'suc.impr':<12} {'usuario':<14} "
          f"{'sku':>14} {'talla':<6} {'PRECIO IMPRESO':>15} {'etq':>5}")
    print('-' * 125)
    for d in detalles:
        h = d.historial
        print(f"{timezone.localtime(h.fecha_impresion):%d/%m/%y %H:%M}   "
              f"{h.tipo_origen[:17]:<17} {str(h.numero_documento or h.documento_id)[:10]:<10} "
              f"{(h.sucursal.alias if h.sucursal else '-')[:12]:<12} "
              f"{((h.usuario.get_full_name() or h.usuario.username) if h.usuario else 'Sistema')[:14]:<14} "
              f"{d.sku:>14} {str(d.talla or '')[:6]:<6} {d.precio_impreso:>15,} {d.cantidad_etiquetas:>5}")

    # el corazon del reporte: mismo sku, precios distintos entre impresiones
    por_sku = defaultdict(set)
    for d in detalles:
        por_sku[d.sku].add(d.precio_impreso)
    print('\n  SKUs impresos con MAS DE UN precio a lo largo del tiempo:')
    algun = False
    for sku, pset in sorted(por_sku.items()):
        if len(pset) > 1:
            algun = True
            print(f"    sku {sku}: {sorted(pset)}  <<<<<<")
    if not algun:
        print('    (ninguno)')

    # y dentro de UNA misma impresion
    print('\n  Impresiones donde el MISMO articulo salio con precios distintos en la MISMA tanda:')
    por_hist = defaultdict(set)
    meta_hist = {}
    for d in detalles:
        por_hist[d.historial_id].add(d.precio_impreso)
        meta_hist[d.historial_id] = d.historial
    algun = False
    for hid, pset in por_hist.items():
        if len(pset) > 1:
            algun = True
            h = meta_hist[hid]
            print(f"    hist={hid} {timezone.localtime(h.fecha_impresion):%d/%m/%y %H:%M} "
                  f"{h.tipo_origen} doc={h.numero_documento or h.documento_id} -> precios {sorted(pset)}  <<<<<<")
    if not algun:
        print('    (ninguna)')


# ----------------------------------------------------------------------
# 5. REPLICA DE LA LOGICA DE PRECIO EN LOS ULTIMOS DESPACHOS
# ----------------------------------------------------------------------
sep('5. ULTIMOS DOCUMENTOS DE DESPACHO CON ESTE ARTICULO + PRECIO QUE CALCULA EL MODULO')

from app.views_etiquetas_zebra import (
    _resolver_sucursal_destino_dte, _indexar_precios_destino,
    _campanas_vigentes, _aplicar_precio_etiqueta,
)

pt_ids = [pt.id for pt in tallas]

# --- 5a. DTEs (compra / traspaso) ---
lineas_dte = (Dte_Productos.objects
              .filter(productoTalla_id__in=pt_ids)
              .select_related('dte', 'productoTalla__producto__sucursal')
              .order_by('-dte__fecha_emision')[:40])
dte_ids = []
for l in lineas_dte:
    if l.dte_id and l.dte_id not in dte_ids:
        dte_ids.append(l.dte_id)
dte_ids = dte_ids[:5]

for dte_id in dte_ids:
    dte = Dte.objects.select_related('sucursal', 'emisor').get(id=dte_id)
    destino = _resolver_sucursal_destino_dte(dte)
    lineas = list(Dte_Productos.objects.filter(dte=dte, productoTalla_id__in=pt_ids)
                  .select_related('productoTalla__producto__sucursal',
                                  'productoTalla__producto__atributo1',
                                  'productoTalla__producto__atributo2'))
    pts = [l.productoTalla for l in lineas if l.productoTalla]
    por_sku_idx, por_art_idx = _indexar_precios_destino(pts, destino)
    camp = _campanas_vigentes({p.producto_id for p in pts}, destino.id if destino else None)

    print(f"\n  DTE {dte.numero_documento} ({dte.tipo_transaccion}) {dte.fecha_emision} "
          f"origen={dte.sucursal.alias if dte.sucursal else '-'} destino={destino.alias if destino else '-'}")
    print(f"    {'sku':>14} {'talla':<6} {'p.origen':>10} {'p.destino':>10} "
          f"{'-> ETIQUETA':>12} {'estado':<14} {'sku_destino':>14}")
    for l in lineas:
        pt = l.productoTalla
        p = pt.producto
        gem = (por_sku_idx.get(pt.sku)
               or por_art_idx.get((p.articulo, (pt.talla or '').strip())))
        item = {'sku': str(pt.sku)}
        _aplicar_precio_etiqueta(item, p, gem, destino, camp.get(p.id))
        print(f"    {pt.sku:>14} {str(pt.talla or '')[:6]:<6} "
              f"{item['precio_origen']:>10,} "
              f"{(item['precio_destino'] if item['precio_destino'] is not None else 0):>10,} "
              f"{int(item['precio']):>12,} {item['estado_precio']:<14} "
              f"{str(item['sku_destino'] or '-'):>14}")

# --- 5b. Traspasos internos ---
det_tr = (Traspaso_Detalle.objects
          .filter(producto_talla_id__in=pt_ids)
          .select_related('traspaso', 'traspaso__sucursal_origen', 'traspaso__sucursal_destino')
          .order_by('-traspaso__fecha_solicitud')[:40])
tr_ids = []
for d in det_tr:
    if d.traspaso_id not in tr_ids:
        tr_ids.append(d.traspaso_id)
tr_ids = tr_ids[:5]

for tr_id in tr_ids:
    tr = Traspaso.objects.select_related('sucursal_origen', 'sucursal_destino').get(id=tr_id)
    destino = tr.sucursal_destino
    detalles_tr = list(Traspaso_Detalle.objects.filter(traspaso=tr, producto_talla_id__in=pt_ids)
                       .select_related('producto_talla__producto__sucursal'))
    pts = [d.producto_talla for d in detalles_tr if d.producto_talla]
    por_sku_idx, por_art_idx = _indexar_precios_destino(pts, destino)
    camp = _campanas_vigentes({p.producto_id for p in pts}, destino.id if destino else None)

    print(f"\n  TRASPASO TR-{tr.id} {tr.fecha_solicitud} estado={tr.estado} "
          f"origen={tr.sucursal_origen.alias if tr.sucursal_origen else '-'} "
          f"destino={destino.alias if destino else '-'}")
    print(f"    {'sku':>14} {'talla':<6} {'p.origen':>10} {'p.destino':>10} "
          f"{'-> ETIQUETA':>12} {'estado':<14} {'TD.precio_venta':>15}")
    for d in detalles_tr:
        pt = d.producto_talla
        p = pt.producto
        gem = (por_sku_idx.get(pt.sku)
               or por_art_idx.get((p.articulo, (pt.talla or '').strip())))
        item = {'sku': str(pt.sku)}
        _aplicar_precio_etiqueta(item, p, gem, destino, camp.get(p.id))
        print(f"    {pt.sku:>14} {str(pt.talla or '')[:6]:<6} "
              f"{item['precio_origen']:>10,} "
              f"{(item['precio_destino'] if item['precio_destino'] is not None else 0):>10,} "
              f"{int(item['precio']):>12,} {item['estado_precio']:<14} "
              f"{d.precio_venta or 0:>15,}")


# ----------------------------------------------------------------------
# 6. HISTORIAL DE CAMBIOS DE PRECIO (si el modelo existe)
# ----------------------------------------------------------------------
sep('6. HISTORIAL DE CAMBIOS DE PRECIO DE ESTAS FICHAS')
try:
    from app.models import HistorialCambioPrecio
    hp = list(HistorialCambioPrecio.objects.filter(producto__in=productos)
              .select_related('producto', 'producto__sucursal', 'usuario')
              .order_by('-fecha_cambio')[:40])
    if not hp:
        print('  (sin registros)')
    else:
        print(f"{'fecha':<17} {'prod':>8} {'sucursal':<14} {'antes':>10} {'despues':>10} "
              f"{'tipo':<24} {'usuario':<14} motivo")
        print('-' * 130)
        for h in hp:
            print(f"{timezone.localtime(h.fecha_cambio):%d/%m/%y %H:%M}   {h.producto_id:>8} "
                  f"{(h.producto.sucursal.alias if h.producto.sucursal else '-')[:14]:<14} "
                  f"{h.precio_anterior:>10,} {h.precio_nuevo:>10,} {h.tipo_cambio[:24]:<24} "
                  f"{((h.usuario.get_full_name() or h.usuario.username) if h.usuario else 'Sistema')[:14]:<14} "
                  f"{(h.motivo or '')[:45]}")
except Exception as exc:
    print(f'  no disponible: {exc}')

print('\n\n(read-only: no se modifico nada)')
