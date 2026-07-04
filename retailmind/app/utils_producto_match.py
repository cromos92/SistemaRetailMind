"""
Utilidades de identidad/deduplicación de productos.

Un Producto se identifica por: código (`articulo`) + marca (atributo1) +
color (atributo2) + género (atributo3) + categoría, dentro de una sucursal.
El `articulo` es texto libre que escribe el usuario, así que históricamente
un mismo producto se duplicaba por diferencias de mayúsculas/espacios/acentos
en el código (bug reportado: "creó un código nuevo agregándole una variante").

`normalizar_articulo` canoniza el código para comparar; el resto de campos
son FKs (se comparan por id). Estas funciones son la fuente única de verdad
para "¿este producto ya existe?" en creación manual y por recepción.
"""
import unicodedata


def normalizar_articulo(valor):
    """Canoniza un código de artículo para comparar identidad.

    - Quita espacios de los extremos y colapsa espacios internos.
    - Pasa a mayúsculas.
    - Elimina acentos/diacríticos.

    'zap-001 ' , ' ZAP-001', 'Zap-001' → 'ZAP-001'
    'Niño  A' → 'NINO A'
    """
    if valor is None:
        return ''
    s = str(valor).strip().upper()
    s = ''.join(c for c in unicodedata.normalize('NFD', s)
                if unicodedata.category(c) != 'Mn')
    s = ' '.join(s.split())
    return s


def buscar_producto_por_identidad(articulo, atributo1_id, atributo2_id,
                                  atributo3_id, categoria_id, sucursal_id):
    """Devuelve el Producto que coincide en IDENTIDAD, o None.

    Identidad = articulo normalizado + marca + color + género + categoría +
    sucursal. Los atributos/categoría se filtran por id en la BD; el articulo
    se compara normalizado en Python sobre el (pequeño) conjunto resultante.
    """
    from .models import Producto
    objetivo = normalizar_articulo(articulo)
    qs = Producto.objects.filter(
        atributo1_id=atributo1_id,
        atributo2_id=atributo2_id,
        atributo3_id=atributo3_id,
        categoria_id=categoria_id,
        sucursal_id=sucursal_id,
    )
    for p in qs:
        if normalizar_articulo(p.articulo) == objetivo:
            return p
    return None


def producto_talla_por_sku(sku, sucursal_id=None, select_related=None,
                           solo_sucursal=False):
    """Devuelve un Producto_Talla por SKU tolerando SKUs duplicados.

    El campo `sku` NO es único en la BD (dato legacy: ~152k SKUs repetidos),
    por lo que `.get(sku=)` revienta con MultipleObjectsReturned. Esta función
    prefiere la talla de la sucursal indicada; si no hay y `solo_sucursal` es
    False, devuelve la primera de cualquier sucursal. Devuelve None si no existe.

    solo_sucursal=True → restringe estrictamente a la sucursal (None si no está).
    """
    from .models import Producto_Talla
    qs = Producto_Talla.objects.all()
    if select_related:
        qs = qs.select_related(*select_related)
    qs = qs.filter(sku=sku)
    if sucursal_id:
        pt = qs.filter(producto__sucursal_id=sucursal_id).first()
        if pt or solo_sucursal:
            return pt
    return qs.first()


def variantes_mismo_codigo(articulo, sucursal_id, excluir_id=None):
    """Productos (variantes) con el mismo código normalizado en la sucursal.

    Sirve para el aviso de la UI: "el código X ya existe en estas variantes
    (colores/géneros)". Prefiltra en BD por Upper(Trim(articulo)) (barato) y
    refina con `normalizar_articulo` para cubrir también acentos/espacios.
    """
    from .models import Producto
    objetivo = normalizar_articulo(articulo)
    if not objetivo:
        return []
    # Prefiltro barato en BD por el primer token del código (robusto a
    # mayúsculas y a espacios raros como \xa0 que Trim() no elimina); el
    # refinamiento fino lo hace normalizar_articulo en Python.
    token = objetivo.split(' ')[0]
    qs = (Producto.objects.filter(sucursal_id=sucursal_id, articulo__icontains=token)
          .select_related('atributo1', 'atributo2', 'atributo3', 'categoria'))
    out = []
    for p in qs:
        if p.id != excluir_id and normalizar_articulo(p.articulo) == objetivo:
            out.append(p)
    return out
