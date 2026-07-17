# -*- coding: utf-8 -*-
"""
Remediación de la NC folio 961 emitida por error sobre el DTE #17032
(id 2192706, FACTURA ELECTRONICA de traspaso EDEL→PAO4, pre-recepción).

Qué pasó (diagnóstico 2026-07-14): se quería devolver 1 unidad del SKU
4837142 T-38 (motivo "no llego"), pero el modal interpretó el "1" como
cantidad RESTANTE y la NC salió por 2 unidades ($24.178 neto).

Qué hace este script (todo en una transacción; valida el estado exacto
antes de tocar nada y aborta si algo no calza):
  1. Línea original dp#1061768: stock 1 → 2 (queda 1 sola unidad retirada).
  2. Movimiento TRASPASO_SALIDA mov#3083632: cantidad -1 → -2.
  3. Producto_Talla SKU 4837142 en EDEL: stock -1 (revierte 1 de las 2
     unidades que el ajuste erróneo reintegró al origen).
  4. NC id 2192711 (folio 961): 2 uds / $24.178 / $28.772 → 1 ud / $12.089
     / $14.386, incluida su línea dp#1061824.
  5. Recalcula neto/IVA/unidades del DTE #17032 desde sus líneas activas
     (debe quedar en 59 uds / $753.511 neto / $896.678 total).
  6. Deja bitácora en `referencias` de ambos documentos.
  7. Genera el TXT Acepta CORREGIDO (1 unidad, razón 3 = corrige montos)
     en `_NC_961_corregido.txt` para subirlo a Acepta.

⚠️  SOLO ejecutar con DRY_RUN=False si el TXT original de la NC 961 NO fue
    subido a Acepta. Si ya se subió, NO usar este script: corresponde emitir
    una ND 56 + NC nueva (avisar para preparar ese flujo).

Ejecutar desde retailmind/:
  python manage.py shell -c "exec(open('_remediar_nc961_dte17032.py', encoding='utf-8').read())"
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from app.models import Dte, Dte_Productos, Movimientos_Producto, Producto_Talla

# ────────────────────────────────────────────────────────────────────
DRY_RUN = True   # ← cambiar a False para aplicar de verdad
# ────────────────────────────────────────────────────────────────────

# Identidades exactas según diagnóstico del 2026-07-14
DTE_ID = 2192706          # DTE #17032 FACTURA ELECTRONICA EDEL→PAO4
NC_ID = 2192711           # NOTA DE CREDITO folio 961
DP_ORIGINAL_ID = 1061768  # línea SKU 4837142 T-38 del DTE original
DP_NC_ID = 1061824        # línea de la NC
MOV_ID = 3083632          # TRASPASO_SALIDA del SKU 4837142
SKU = 4837142
PRECIO = Decimal('12089')

# Estado erróneo actual → estado correcto
STOCK_LINEA_ACTUAL, STOCK_LINEA_CORRECTO = 1, 2      # de 3 originales
MOV_CANT_ACTUAL, MOV_CANT_CORRECTO = -1, -2
NC_UDS_ACTUAL, NC_UDS_CORRECTO = 2, 1

IVA = Decimal('0.19')


def _q(v):
    return Decimal(v).quantize(Decimal('1'))


def fail(msg):
    raise SystemExit(f"\n*** ABORTADO (nada fue modificado): {msg}\n")


ahora = timezone.now()
marca = f"[REMEDIACION NC961 {ahora.strftime('%Y-%m-%d %H:%M')}]"

with transaction.atomic():
    dte = Dte.objects.select_for_update().get(id=DTE_ID)
    nc = Dte.objects.select_for_update().get(id=NC_ID)
    dp = Dte_Productos.objects.select_for_update().get(id=DP_ORIGINAL_ID)
    dp_nc = Dte_Productos.objects.select_for_update().get(id=DP_NC_ID)
    mov = Movimientos_Producto.objects.select_for_update().get(id=MOV_ID)

    # ── Validaciones de precondición ────────────────────────────────
    if dte.numero_documento != 17032 or dte.tipo_transaccion != 'TRASPASO':
        fail("el DTE 2192706 no es el traspaso #17032 esperado")
    if dte.fecha_recepcion is not None or dte.estado_dte != 'EMITIDO':
        fail(f"el DTE ya no está pre-recepción (estado={dte.estado_dte}, "
             f"fecha_recepcion={dte.fecha_recepcion}); revisar manualmente")
    if nc.documento_afectado_id != dte.id or nc.numero_documento != 961:
        fail("la NC 2192711 no referencia al DTE esperado o cambió de folio")
    if int(nc.unidades_productos or 0) != NC_UDS_ACTUAL:
        fail(f"la NC ya no tiene {NC_UDS_ACTUAL} unidades "
             f"(tiene {nc.unidades_productos}); ¿ya fue remediada?")
    if dp.dte_id != dte.id or int(dp.stock or 0) != STOCK_LINEA_ACTUAL:
        fail(f"la línea original no está en stock={STOCK_LINEA_ACTUAL} "
             f"(está en {dp.stock})")
    if dp_nc.dte_id != nc.id or int(dp_nc.stock or 0) != NC_UDS_ACTUAL:
        fail("la línea de la NC no está en la cantidad esperada")
    if mov.dte_id != dte.id or int(mov.cantidad) != MOV_CANT_ACTUAL:
        fail(f"el movimiento {MOV_ID} no está en cantidad {MOV_CANT_ACTUAL} "
             f"(está en {mov.cantidad})")

    talla = dp.productoTalla
    if talla is None or talla.sku != SKU:
        fail("la línea original no apunta al SKU esperado")
    talla = Producto_Talla.objects.select_for_update().get(id=talla.id)
    if int(talla.stock or 0) < 1:
        fail(f"stock de SKU {SKU} en EDEL es {talla.stock}; no se puede "
             f"restar la unidad reintegrada de más")

    print("Precondiciones OK. Estado actual verificado contra el diagnóstico.")

    # ── 1-2. Línea y movimiento del DTE original ────────────────────
    dp.stock = STOCK_LINEA_CORRECTO
    dp.save(update_fields=['stock'])
    mov.cantidad = MOV_CANT_CORRECTO
    mov.observaciones = ((mov.observaciones or '') +
                         f" {marca} cantidad {MOV_CANT_ACTUAL}->{MOV_CANT_CORRECTO}")[:500]
    mov.save(update_fields=['cantidad', 'observaciones'])

    # ── 3. Stock origen (EDEL): revertir 1 unidad reintegrada de más ─
    Producto_Talla.objects.filter(id=talla.id).update(stock=F('stock') - 1)

    # ── 4. NC 961 → 1 unidad ────────────────────────────────────────
    nc_neto = PRECIO * NC_UDS_CORRECTO                      # 12.089
    nc_iva = _q(nc_neto * IVA)                              # 2.297
    nc_total = _q(nc_neto + nc_neto * IVA)                  # 14.386
    dp_nc.stock = NC_UDS_CORRECTO
    dp_nc.descripcion = (dp_nc.descripcion or '').replace('[AJUSTE -2]', '[AJUSTE -1]')
    dp_nc.save(update_fields=['stock', 'descripcion'])
    nc.monto_neto = nc_neto
    nc.monto_con_iva = nc_total
    nc.unidades_productos = NC_UDS_CORRECTO
    nc.referencias = ((nc.referencias or '') +
                      f"\n{marca} Corregida de 2 uds ($24.178) a 1 ud "
                      f"($12.089): el ajuste original interpretó mal la cantidad.")
    nc.save(update_fields=['monto_neto', 'monto_con_iva', 'unidades_productos', 'referencias'])

    # ── 5. Recalcular totales del DTE original desde líneas activas ─
    activos = Dte_Productos.objects.filter(dte=dte, activo=True)
    nuevo_neto = sum((Decimal(str(x.stock or 0)) * Decimal(str(x.precio or 0))
                      for x in activos), Decimal('0'))
    nuevas_uds = sum(int(x.stock or 0) for x in activos)
    dte.monto_neto = nuevo_neto
    dte.monto_con_iva = _q(nuevo_neto + nuevo_neto * IVA)
    dte.unidades_productos = nuevas_uds
    dte.referencias = ((dte.referencias or '') +
                       f"\n{marca} NC 961 corregida a 1 ud; línea SKU {SKU} "
                       f"restaurada a {STOCK_LINEA_CORRECTO} uds.")
    dte.save(update_fields=['monto_neto', 'monto_con_iva', 'unidades_productos', 'referencias'])

    print(f"\nResultado DTE #17032 : {nuevas_uds} uds / neto ${nuevo_neto:,.0f} / total ${dte.monto_con_iva:,.0f}")
    print(f"Resultado NC 961     : {NC_UDS_CORRECTO} ud  / neto ${nc_neto:,.0f} / total ${nc_total:,.0f}")
    if nuevas_uds != 59 or _q(nuevo_neto) != Decimal('753511'):
        fail(f"el recálculo no dio 59 uds / $753.511 (dio {nuevas_uds} / {nuevo_neto}); revisar")

    talla.refresh_from_db()
    print(f"Stock EDEL SKU {SKU} : {talla.stock}")

    # ── 7. TXT Acepta corregido (razón 3 = corrige montos) ──────────
    try:
        from app.views_modulo_documentos import generar_txt_dte_acepta, limpiar_texto

        mov_destino = (dte.dte_movimientos
                       .filter(sucursal_destino__isnull=False)
                       .select_related('sucursal_destino').first())
        suc_destino = mov_destino.sucursal_destino if mov_destino else None

        datos_txt = {
            'documento': {
                'tipo_documento': '61',
                'folio': str(nc.numero_documento),
                'fecha_emision': nc.fecha_emision.strftime('%Y-%m-%d'),
                'fecha_vencimiento': (nc.fecha_vencimiento or nc.fecha_emision).strftime('%Y-%m-%d'),
                'tipo_despacho': '2',
                'ind_traslado': '1',
                'forma_pago': '1',
            },
            'emisor': {
                'rut': nc.emisor.rut if nc.emisor else '',
                'razon_social': limpiar_texto(nc.emisor.razon_social if nc.emisor else ''),
                'giro': limpiar_texto((nc.emisor.giro if nc.emisor and nc.emisor.giro else '') or 'COMERCIALIZADORA'),
                'acteco': nc.emisor.acteco if nc.emisor else '',
                'direccion': limpiar_texto((nc.sucursal.direccion if nc.sucursal else '') or (nc.emisor.direccion if nc.emisor else '') or ''),
                'comuna': limpiar_texto((nc.sucursal.comuna if nc.sucursal else '') or (nc.emisor.comuna if nc.emisor else '') or ''),
                'ciudad': limpiar_texto((nc.sucursal.ciudad if nc.sucursal else '') or (nc.emisor.ciudad if nc.emisor else '') or ''),
                'sucursal': limpiar_texto(nc.sucursal.alias if nc.sucursal else ''),
                'codigo_vendedor': limpiar_texto(nc.responsable or 'USUARIO'),
                'nombre_impresora_boleta': getattr(nc.sucursal, 'nombre_impresora_boleta', 'boleta') if nc.sucursal else 'boleta',
                'nombre_impresora_factura': getattr(nc.sucursal, 'nombre_impresora_factura', 'factura') if nc.sucursal else 'factura',
            },
            'receptor': {
                'rut': nc.receptor.rut if nc.receptor else '',
                'codigo_interno': str(nc.receptor.id) if nc.receptor else '',
                'razon_social': limpiar_texto(nc.receptor.razon_social if nc.receptor else 'SIN RAZON SOCIAL'),
                'giro': limpiar_texto((nc.receptor.giro if nc.receptor and nc.receptor.giro else '') or 'COMERCIALIZADORA'),
                'contacto': limpiar_texto(nc.receptor.contacto1 if (nc.receptor and getattr(nc.receptor, 'contacto1', None)) else ''),
                'direccion': limpiar_texto(suc_destino.direccion if suc_destino else (nc.receptor.direccion if nc.receptor else '')),
                'comuna': limpiar_texto(nc.receptor.comuna if nc.receptor else ''),
                'ciudad': limpiar_texto(nc.receptor.ciudad if nc.receptor else ''),
            },
            'totales': {
                'monto_neto': int(nc_neto),
                'monto_exento': 0,
                'iva': int(nc_iva),
                'monto_total': int(nc_total),
            },
            'detalle': [{
                'codigo': str(SKU),
                'sku': str(SKU),
                'nombre': limpiar_texto(talla.producto.articulo if talla.producto else ''),
                'descripcion': limpiar_texto(dp_nc.descripcion or ''),
                'cantidad': NC_UDS_CORRECTO,
                'unidad': 'UN',
                'precio_unitario': int(PRECIO),
                'monto_item': int(nc_neto),
                'indicador_exencion': '',
            }],
            'referencias': [{
                'tipo_documento': '33',
                'folio': str(dte.numero_documento),
                'fecha': dte.fecha_emision.strftime('%Y-%m-%d') if dte.fecha_emision else '',
                'razon': '3',  # corrige montos (NC parcial)
            }],
        }
        contenido = generar_txt_dte_acepta(datos_txt)
        with open('_NC_961_corregido.txt', 'w', encoding='utf-8') as fh:
            fh.write(contenido)
        print("\nTXT corregido escrito en retailmind/_NC_961_corregido.txt "
              "(1 unidad, razón 3). Súbelo a Acepta en reemplazo del anterior.")
    except Exception as e:
        print(f"\nOJO: no se pudo generar el TXT corregido ({e}). "
              f"La remediación de BD sigue siendo válida; el TXT se puede "
              f"regenerar después.")

    if DRY_RUN:
        transaction.set_rollback(True)
        print("\n=== DRY_RUN=True: todo lo anterior fue SIMULADO y revertido. ===")
        print("=== Cambia DRY_RUN=False y vuelve a correr para aplicar.      ===")
    else:
        print("\n=== CAMBIOS APLICADOS Y CONFIRMADOS ===")
