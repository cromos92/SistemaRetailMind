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

from django.db.models import F

# Orden CANÓNICO de fichas de producto: la más recientemente creada primero.
# Es OBLIGATORIO ordenar explícitamente: sin ORDER BY, Postgres devuelve las
# filas en orden físico del heap, que CAMBIA cada vez que la fila se actualiza
# (un UPDATE la reescribe al final). Con dos fichas duplicadas del mismo código
# eso hacía que cada recepción cayera en una ficha distinta — caso real F35542
# en EDEL: la factura del 25-jul entró en la ficha A y las tres del 27-jul en la
# ficha B, partiendo el histórico y los SKUs del artículo en dos.
# `fecha_creacion` es nullable (fichas legacy), de ahí el nulls_last.
_ORDEN_RECIENTE = (F('fecha_creacion').desc(nulls_last=True), F('id').desc())


def ordenar_por_reciente(qs):
    """Aplica el orden canónico (más reciente primero) a un queryset de Producto."""
    return qs.order_by(*_ORDEN_RECIENTE)


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


def fichas_por_identidad(articulo, atributo1_id, atributo2_id, atributo3_id,
                         categoria_id, sucursal_id):
    """TODAS las fichas que coinciden en IDENTIDAD, la más reciente primero.

    Identidad = articulo normalizado + marca + color + género + categoría +
    sucursal. Los atributos/categoría se filtran por id en la BD; el articulo
    se compara normalizado en Python sobre el (pequeño) conjunto resultante.

    Devuelve una LISTA porque en producción existen fichas duplicadas heredadas
    de la migración Laravel: quien llama necesita saber que hay más de una para
    poder avisar antes de escribir stock (ver `verificar_producto_existente` y
    `crear_producto_manual`).
    """
    from .models import Producto
    objetivo = normalizar_articulo(articulo)
    qs = ordenar_por_reciente(Producto.objects.filter(
        atributo1_id=atributo1_id,
        atributo2_id=atributo2_id,
        atributo3_id=atributo3_id,
        categoria_id=categoria_id,
        sucursal_id=sucursal_id,
    ))
    return [p for p in qs if normalizar_articulo(p.articulo) == objetivo]


def buscar_producto_por_identidad(articulo, atributo1_id, atributo2_id,
                                  atributo3_id, categoria_id, sucursal_id):
    """Devuelve el Producto que coincide en IDENTIDAD, o None.

    Con varias fichas duplicadas gana SIEMPRE la más reciente (orden canónico),
    de modo que dos recepciones seguidas del mismo código caen en la MISMA ficha.
    """
    fichas = fichas_por_identidad(articulo, atributo1_id, atributo2_id,
                                  atributo3_id, categoria_id, sucursal_id)
    return fichas[0] if fichas else None


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


def qs_fichas_identidad_otras_sucursales(articulo, atributo1_id, atributo2_id,
                                         atributo3_id, categoria_id,
                                         excluir_sucursal_id):
    """Fichas de OTRAS sucursales que son el MISMO producto (identidad completa).

    Clave que DEBE usar la sincronización de precios: código + marca + color +
    **género + categoría**, la misma identidad con la que se decide si un
    producto ya existe (`fichas_por_identidad`).

    Sin género/categoría la sincronización pisaba el precio de productos
    DISTINTOS que comparten código, marca y color. Caso real 25-07-2026: unos
    GUANTES creados en EDEL reutilizando el código de una ZAPATILLA (ambos
    EVERLAST ROJO UNISEX) dejaron las zapatillas de NICK2 a precio de guante
    ($109.990 → $44.990) con 8 pares en venta.

    `articulo` se compara con iexact: las fichas legacy con el código en otra
    caja quedaban fuera de la sincronización. Devuelve un QuerySet para que los
    llamadores sigan anotando stock / iterando como antes.
    """
    from .models import Producto
    return Producto.objects.filter(
        articulo__iexact=(articulo or '').strip(),
        atributo1_id=atributo1_id,
        atributo2_id=atributo2_id,
        atributo3_id=atributo3_id,
        categoria_id=categoria_id,
    ).exclude(sucursal_id=excluir_sucursal_id)


def qs_fichas_codigo_otra_identidad(articulo, atributo1_id, atributo2_id,
                                    atributo3_id, categoria_id,
                                    excluir_sucursal_id):
    """"Casi-coincidencias": mismo código+marca+color pero OTRO género/categoría.

    Son exactamente las fichas que `qs_fichas_identidad_otras_sucursales` deja
    fuera. Se listan para poder AVISAR, porque significan una de dos cosas y
    ambas importan:

    - un código reutilizado por productos distintos (el bug del caso guantes), o
    - la misma ficha mal categorizada en otra bodega, que por eso ya no recibe
      la sincronización de precio.
    """
    from .models import Producto
    return (
        Producto.objects
        .filter(
            articulo__iexact=(articulo or '').strip(),
            atributo1_id=atributo1_id,
            atributo2_id=atributo2_id,
        )
        .exclude(sucursal_id=excluir_sucursal_id)
        .exclude(atributo3_id=atributo3_id, categoria_id=categoria_id)
    )


def resumen_casi_coincidencias(qs, limite=8):
    """Texto corto 'SUC:categoría/género' para loguear las casi-coincidencias."""
    partes = []
    for p in qs.select_related('sucursal', 'categoria', 'atributo3')[:limite]:
        partes.append('{}:{}/{}'.format(
            p.sucursal.alias if p.sucursal else '?',
            p.categoria.nombre if p.categoria else 'sin-categoria',
            p.atributo3.valor if p.atributo3 else 'sin-genero',
        ))
    return ', '.join(partes)


def productos_por_codigo_sucursales(articulo, sucursal_ids):
    """Todos los Producto con el mismo código normalizado en las sucursales dadas.

    Sirve para mostrar en qué bodegas existe el código y sus variantes (colores/
    géneros distintos) diferenciadas, y para propagar la descripción multi-bodega.
    """
    from .models import Producto
    objetivo = normalizar_articulo(articulo)
    if not objetivo or not sucursal_ids:
        return []
    token = objetivo.split(' ')[0]
    qs = ordenar_por_reciente(
        Producto.objects.filter(sucursal_id__in=sucursal_ids, articulo__icontains=token)
        .select_related('sucursal', 'atributo1', 'atributo2', 'atributo3', 'categoria'))
    return [p for p in qs if normalizar_articulo(p.articulo) == objetivo]


def productos_por_identidad_sucursales(articulo, atributo1_id, atributo2_id,
                                       atributo3_id, categoria_id, sucursal_ids):
    """El MISMO producto (identidad completa) en todas las sucursales dadas.

    A diferencia de `productos_por_codigo_sucursales` (que trae TODAS las
    variantes del código), este filtra además por marca+color+género+categoría,
    o sea la MISMA variante en distintas bodegas. Se usa para propagar la
    descripción sin pisar la de otras variantes (colores/géneros) del código.
    """
    from .models import Producto
    objetivo = normalizar_articulo(articulo)
    if not objetivo or not sucursal_ids:
        return []
    token = objetivo.split(' ')[0]
    qs = ordenar_por_reciente(Producto.objects.filter(
        sucursal_id__in=sucursal_ids, articulo__icontains=token,
        atributo1_id=atributo1_id, atributo2_id=atributo2_id,
        atributo3_id=atributo3_id, categoria_id=categoria_id,
    ))
    return [p for p in qs if normalizar_articulo(p.articulo) == objetivo]


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
    qs = ordenar_por_reciente(
        Producto.objects.filter(sucursal_id=sucursal_id, articulo__icontains=token)
        .select_related('atributo1', 'atributo2', 'atributo3', 'categoria'))
    out = []
    for p in qs:
        if p.id != excluir_id and normalizar_articulo(p.articulo) == objetivo:
            out.append(p)
    return out


def qs_fichas_identidad_todas_sucursales(articulo, atributo1_id, atributo2_id,
                                         atributo3_id, categoria_id):
    """Todas las fichas del MISMO producto (identidad completa) en CUALQUIER bodega.

    Hermana de `qs_fichas_identidad_otras_sucursales`, pero incluyendo la
    sucursal de origen: es la que necesita la EDICIÓN de producto, que reescribe
    la ficha seleccionada y sus gemelas de las demás bodegas en una sola pasada.

    Antes la edición propagaba con `filter(articulo=...)` a secas —sin marca,
    color, género ni categoría—, o sea la MISMA clave floja que ya había dejado
    unas zapatillas a precio de guante (ver el docstring de
    `qs_fichas_identidad_otras_sucursales`). Con esa clave, editar el color de
    la ficha NEGRA reescribía el color de la ficha BLANCA del mismo código, en
    todas las empresas.
    """
    from .models import Producto
    return Producto.objects.filter(
        articulo__iexact=(articulo or '').strip(),
        atributo1_id=atributo1_id,
        atributo2_id=atributo2_id,
        atributo3_id=atributo3_id,
        categoria_id=categoria_id,
    )


def qs_fichas_identidad_de(productos):
    """Fichas gemelas (identidad completa, todas las bodegas) de VARIAS fichas base.

    Una sola query con un OR por identidad distinta, para que la edición masiva
    no dispare N queries ni caiga en la clave floja `articulo__in`.
    """
    from .models import Producto
    from django.db.models import Q

    q = Q()
    vistas = set()
    for p in productos:
        clave = (
            normalizar_articulo(p.articulo),
            p.atributo1_id, p.atributo2_id, p.atributo3_id, p.categoria_id,
        )
        if clave in vistas:
            continue
        vistas.add(clave)
        q |= Q(
            articulo__iexact=(p.articulo or '').strip(),
            atributo1_id=p.atributo1_id,
            atributo2_id=p.atributo2_id,
            atributo3_id=p.atributo3_id,
            categoria_id=p.categoria_id,
        )
    if not vistas:
        return Producto.objects.none()
    return Producto.objects.filter(q)
