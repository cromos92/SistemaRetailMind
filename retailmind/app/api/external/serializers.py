"""
Serializadores para la API externa de RetailMind (contrato v1).

Recibe dicts con columnas planas por (sku, sucursal) y los agrupa en el
formato que espera AllConnected / VicentAllEcommercesConected:

  {
    "articulo": "XTREME_GUARD",
    "marca": "...",
    "color": "BLANCO/NEGRO",
    "genero": "HOMBRE",
    "categoria": "ZAPATILLAS",
    ...precios...,
    "imagenes": [],
    "tallas": [
      {
        "sku": "4810070",
        "talla": "10",
        "total_stock": 5,
        "sucursales": [{"nombre": "PAO1", "stock": 3}, {"nombre": "PAO2", "stock": 2}]
      },
      ...
    ]
  }

Clave de agrupación: (articulo, marca, color, genero, categoria)
— idéntica a la lógica de crear_producto_desde_recepcion y crear_producto_manual
  para determinar si un producto "ya existe" (sin la dimensión sucursal).
"""

from rest_framework import serializers


#: Prefijo con el que la recategorización v1.2 deprecó las categorías planas
#: viejas. NO se borran porque `Producto.categoria` es on_delete=CASCADE.
PREFIJO_CATEGORIA_OBSOLETA = '_ZZ_'

#: Nombre del atributo multi-etiqueta que reemplazó al eje deporte/uso.
NOMBRE_ATRIBUTO_ESPECIALIDAD = 'Especialidad'


def agrupar_por_producto(
    rows: list, fotos_map: dict = None, especialidades_map: dict = None,
) -> list:
    """
    Toma una lista de dicts planos (una fila por sku×sucursal) y los agrupa
    en una lista de dicts donde cada entrada representa un PRODUCTO ÚNICO
    (articulo + marca + color + genero + categoria) con sus tallas anidadas.

    Si ``fotos_map`` ({articulo: url}) se pasa, la portada se resuelve desde
    ese dict (resuelto en bloque, sin N+1). Si es None, cae al resolver
    puntual ``resolver_foto_portada_url`` (1-2 queries por producto) — útil
    para llamadas con pocos productos (ej. /tallas/ de un solo articulo).

    Cada talla incluye:
      - sku, talla
      - total_stock: suma de stock de todas las sucursales
      - sucursales: [{nombre, stock}, ...]

    Columnas esperadas en cada row (del queryset de views.py):
      sku, talla, stock,
      producto__articulo, producto__descripcion,
      producto__atributo1__valor,   ← marca
      producto__atributo2__valor,   ← color
      producto__atributo3__valor,   ← género
      producto__categoria__nombre,
      producto__costo, producto__precioventa, producto__precioSugerido,
      producto__sucursal__alias,
      producto__sucursal__empresa__nombre_fantasia,
    """
    productos: dict = {}   # key → dict producto
    tallas_idx: dict = {}  # key → {talla_key → dict talla}

    for row in rows:
        sku_val  = str(row.get('sku', '') or '')
        talla_val = str(row.get('talla', '') or '')
        if not sku_val:
            continue

        articulo  = row.get('producto__articulo', '') or ''
        marca     = row.get('producto__atributo1__valor', '') or ''
        color     = row.get('producto__atributo2__valor', '') or ''
        genero    = row.get('producto__atributo3__valor', '') or ''
        categoria = row.get('producto__categoria__nombre', '') or ''
        empresa_id = row.get('producto__sucursal__empresa_id')

        # Clave de producto: identidad completa sin sucursal
        prod_key = (articulo, marca, color, genero, categoria)

        if prod_key not in productos:
            if fotos_map is not None:
                foto_portada_url = fotos_map.get(articulo, '')
            else:
                from app.services.realsport_imagenes_service import resolver_foto_portada_url
                foto_portada_url = resolver_foto_portada_url(articulo, empresa_id)
            productos[prod_key] = {
                'articulo':         articulo,
                'descripcion':      row.get('producto__descripcion', '') or '',
                'marca':            marca,
                'color':            color,
                'genero':           genero,
                # Categoría HOJA del árbol v1.2 ("Zapatillas", "Protecciones").
                'categoria':        categoria,
                # DEPRECADO: siempre vacío. Se conserva porque sacar la clave
                # rompería a los consumidores que la leen. Lo que antes se
                # esperaba acá hoy vive en `categoria_padre` y `especialidades`.
                'subcategoria':     '',
                # ── Taxonomía v1.2 ────────────────────────────────────────
                # El árbol de 2 niveles y el eje multi-etiqueta que reemplazó
                # al deporte/uso. AllConnected los necesita para poder filtrar
                # y publicar por categoría real y por especialidad.
                'categoria_id':       row.get('producto__categoria__id'),
                'categoria_padre':    row.get('producto__categoria__padre__nombre') or '',
                'categoria_padre_id': row.get('producto__categoria__padre__id'),
                # Se MARCA, no se excluye: este endpoint es un espejo de
                # catálogo, y dropear filas haría desaparecer productos del
                # espejo del consumidor (y con el soft-delete de ausentes,
                # despublicarlos). Que decida quien consume.
                'categoria_obsoleta': categoria.startswith(PREFIJO_CATEGORIA_OBSOLETA),
                'especialidades':   (especialidades_map or {}).get(
                    row.get('producto__id')) or [],
                'precio_venta':     int(row.get('producto__precioventa', 0) or 0),
                'precio_costo':     int(row.get('producto__costo', 0) or 0),
                'precio_interno':   int(row.get('producto__precioSugerido', 0) or 0),
                'imagenes':         [],   # llegan por webhook Shopify
                'foto_portada_url': foto_portada_url,
                'guia_talla_id':    row.get('producto__guia_talla_id'),
                'tipo_talla':       row.get('producto__tipo_talla', '') or '',
                'tallas':           [],
            }
            tallas_idx[prod_key] = {}

        # Clave de talla: sku es único por (articulo+color+talla) entre sucursales
        talla_key = sku_val

        if talla_key not in tallas_idx[prod_key]:
            talla_entry = {
                'sku':         sku_val,
                'talla':       talla_val,
                'total_stock': 0,
                'sucursales':  [],
            }
            tallas_idx[prod_key][talla_key] = talla_entry
            productos[prod_key]['tallas'].append(talla_entry)

        nombre_sucursal = row.get('producto__sucursal__alias', '') or ''
        stock_sucursal  = int(row.get('stock', 0) or 0)

        tallas_idx[prod_key][talla_key]['sucursales'].append({
            'nombre': nombre_sucursal,
            'stock':  stock_sucursal,
        })
        tallas_idx[prod_key][talla_key]['total_stock'] += stock_sucursal

    return list(productos.values())


# ── Serializers ──────────────────────────────────────────────────────────────

class TallaExternalSerializer(serializers.Serializer):
    """Serializa una talla anidada dentro de un producto."""
    sku         = serializers.CharField()
    talla       = serializers.CharField()
    total_stock = serializers.IntegerField()
    sucursales  = serializers.ListField(child=serializers.DictField(), default=list)


class ProductoExternalSerializer(serializers.Serializer):
    """Serializa un producto agrupado (salida de agrupar_por_producto)."""
    articulo         = serializers.CharField()
    descripcion      = serializers.CharField()
    marca            = serializers.CharField()
    color            = serializers.CharField()
    genero           = serializers.CharField()
    categoria        = serializers.CharField()
    subcategoria     = serializers.CharField()
    # Taxonomía v1.2. Todos con required=False + default para que payloads
    # viejos (y los que queden en la caché de 1 h tras el deploy) sigan validando.
    categoria_id       = serializers.IntegerField(allow_null=True, required=False)
    categoria_padre    = serializers.CharField(allow_blank=True, required=False, default='')
    categoria_padre_id = serializers.IntegerField(allow_null=True, required=False)
    categoria_obsoleta = serializers.BooleanField(required=False, default=False)
    especialidades     = serializers.ListField(
        child=serializers.DictField(), required=False, default=list,
    )
    precio_venta     = serializers.IntegerField()
    precio_costo     = serializers.IntegerField()
    precio_interno   = serializers.IntegerField()
    imagenes         = serializers.ListField(child=serializers.CharField(), default=list)
    foto_portada_url = serializers.CharField(allow_blank=True, required=False, default='')
    guia_talla_id    = serializers.IntegerField(allow_null=True, required=False)
    tipo_talla       = serializers.CharField(allow_blank=True, required=False, default='')
    tallas           = TallaExternalSerializer(many=True)


# Mantener alias para compatibilidad con imports existentes
SkuExternalSerializer = ProductoExternalSerializer
