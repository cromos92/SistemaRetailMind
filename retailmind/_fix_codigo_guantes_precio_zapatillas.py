# -*- coding: utf-8 -*-
"""
Correccion caso BOX ELITE (28-jul-2026).

Problema: los GUANTES EVERLAST ROJO creados el 25-jul en EDEL (ficha 138379)
reutilizaron el codigo 009283623 de las ZAPATILLAS BOX ELITE 2, y la
"Sincronizacion automatica desde creacion de producto" (que matchea solo
codigo+marca+color) piso el precio de las zapatillas ROJO de NICK2
(ficha 136745): $109.990 -> $44.990.

Este script:
 1) Renombra el codigo de los guantes (138379) a NUEVO_CODIGO_GUANTES.
 2) Restaura el precio de las zapatillas ROJO NICK2 (136745) a $109.990
    (ficha + lotes activos) y deja registro en HistorialCambioPrecio.

NO toca: 136744 (EDEL, ya esta en $109.990), NAVY (516/517), NEGRO (520/521),
ni el precio de los guantes ($44.990 se mantiene).

USO (desde retailmind/):
  python manage.py shell -c "exec(open('_fix_codigo_guantes_precio_zapatillas.py', encoding='utf-8').read())"

Con DRY_RUN = True muestra los cambios y hace ROLLBACK (no guarda nada).
Cambiar a False para aplicar de verdad.
"""
from django.contrib.auth import get_user_model
from django.db import transaction

from app.models import Producto, LoteProducto, HistorialCambioPrecio
from app.utils_producto_match import normalizar_articulo

DRY_RUN = False
NUEVO_CODIGO_GUANTES = "009283623G"   # <-- CONFIRMAR este codigo antes de aplicar
PRECIO_ZAPATILLAS = 109990

ID_GUANTES = 138379      # EDEL, categoria Guantes, tallas 12/14/16
ID_ZAP_NICK2 = 136745    # NICK2, zapatillas ROJO, 8 unidades

SKUS_GUANTES = [4837256, 4837257, 4837264]

guantes = Producto.objects.select_related("sucursal", "categoria").get(id=ID_GUANTES)
zap = Producto.objects.select_related("sucursal").get(id=ID_ZAP_NICK2)

# --- Validaciones de seguridad: aborta si el estado no es el esperado ---
assert normalizar_articulo(guantes.articulo) == "009283623", \
    f"ABORT: la ficha guantes ya no tiene el codigo esperado: {guantes.articulo!r}"
assert guantes.categoria and "guante" in guantes.categoria.nombre.lower(), \
    f"ABORT: la ficha {ID_GUANTES} no es de Guantes: {guantes.categoria}"
assert int(zap.precioventa) == 44990, \
    f"ABORT: las zapatillas NICK2 ya no estan en $44.990: {zap.precioventa}"

# Los guantes salieron de EDEL a NICK2 en el traspaso 17049 (emitido 28-jul).
# Al RECEPCIONARLO, NICK2 crea su propia ficha copiando el codigo del origen:
# si eso ya pasó, hay MAS de una ficha de guantes con el codigo colisionado y
# hay que renombrarlas TODAS. Se detectan por ser dueñas de los SKUs de guante.
fichas_guantes = list(
    Producto.objects
    .filter(producto_talla__sku__in=SKUS_GUANTES)
    .filter(articulo__iexact="009283623")
    .select_related("sucursal", "categoria")
    .distinct()
)
assert fichas_guantes, "ABORT: no se encontro ninguna ficha de guantes con el codigo 009283623"

objetivo = normalizar_articulo(NUEVO_CODIGO_GUANTES)
_ids_guantes = {f.id for f in fichas_guantes}
en_uso = [p for p in Producto.objects.filter(articulo__iexact=NUEVO_CODIGO_GUANTES)
          if p.id not in _ids_guantes and normalizar_articulo(p.articulo) == objetivo]
assert not en_uso, f"ABORT: el codigo {NUEVO_CODIGO_GUANTES!r} ya lo usan las fichas {[p.id for p in en_uso]}"

modo = "DRY-RUN (rollback al final, no guarda nada)" if DRY_RUN else "APLICANDO CAMBIOS REALES"
print(f"=== {modo} ===")
for f in fichas_guantes:
    print(f"1) Ficha {f.id} ({f.sucursal.alias if f.sucursal else '-'}, "
          f"{f.categoria.nombre if f.categoria else '-'}): "
          f"codigo {f.articulo!r} -> {NUEVO_CODIGO_GUANTES!r} (precio ${f.precioventa} se mantiene)")
print(f"2) Ficha {zap.id} ({zap.sucursal.alias}, zapatillas ROJO): "
      f"precio ${zap.precioventa} -> ${PRECIO_ZAPATILLAS}")

usuario = get_user_model().objects.filter(username="javier").first()
precio_anterior = int(zap.precioventa)

with transaction.atomic():
    # 1) Codigo propio para los guantes, en TODAS las fichas que lo comparten
    #    (update() directo: sin señales ni save() custom)
    n_renombradas = Producto.objects.filter(
        id__in=[f.id for f in fichas_guantes]
    ).update(articulo=NUEVO_CODIGO_GUANTES)
    print(f"   fichas de guantes renombradas: {n_renombradas}")

    # 2) Restaurar precio zapatillas NICK2: ficha + lotes activos (igual que actualizar_precio)
    Producto.objects.filter(id=ID_ZAP_NICK2).update(precioventa=PRECIO_ZAPATILLAS)
    lotes = LoteProducto.objects.filter(
        producto_talla__producto_id=ID_ZAP_NICK2,
        cantidad_disponible__gt=0,
        activo=True,
    ).update(precio_venta_unitario=PRECIO_ZAPATILLAS)

    HistorialCambioPrecio.objects.create(
        producto=zap,
        precio_anterior=precio_anterior,
        precio_nuevo=PRECIO_ZAPATILLAS,
        diferencia=PRECIO_ZAPATILLAS - precio_anterior,
        porcentaje_cambio=round((PRECIO_ZAPATILLAS - precio_anterior) / precio_anterior * 100, 2),
        motivo=("Correccion: revierte la 'Sincronizacion automatica desde creacion de producto en EDEL' "
                "del 25-07-2026. Los GUANTES (ficha 138379) reutilizaban el codigo de las zapatillas y "
                "el sync piso este precio; los guantes ahora tienen codigo propio "
                f"({NUEVO_CODIGO_GUANTES})."),
        tipo_cambio="MANUAL",
        usuario=usuario,
        tallas_afectadas=zap.producto_talla.count(),
        lotes_afectados=lotes,
    )
    print(f"   lotes activos actualizados: {lotes} | historial registrado (usuario: {usuario})")

    if DRY_RUN:
        transaction.set_rollback(True)
        print("=== DRY_RUN=True -> ROLLBACK ejecutado, la BD quedo intacta. ===")
        print("=== Para aplicar: editar DRY_RUN = False y volver a correr.   ===")
    else:
        print("=== CAMBIOS APLICADOS. Verificar en POS NICK2 escaneando SKU 4832116. ===")
