"""
Serializers y armadores de payload para la API móvil (JWT).

Se usan en el "verificador de etiquetas" de la app de staff (appNexoStaff):
el vendedor escanea el código de barras o fotografía la etiqueta, el OCR
ocurre en el teléfono y el backend compara etiqueta vs sistema.

Notas de dominio que condicionan todo este archivo:

- `Producto` es POR SUCURSAL (`Producto.sucursal`): el mismo artículo vive
  como filas distintas en cada bodega/tienda. Todo se scopea por sucursal.
- Marca/color/género NO son texto libre: son FKs a `AtributoOpcion`
  (`atributo1`=Marca, `atributo2`=Color, `atributo3`=Sexo). `atributo4` está
  muerto (0 productos lo usan) y no se expone.
- Las especialidades v1.2 son multi-etiqueta y viven en `ProductoAtributoValor`
  contra el atributo "Especialidad" — no hay modelo `Especialidad`.
- El precio vigente es SIEMPRE `Producto.precioventa`: las campañas de
  liquidación %/PRECIO_FIJO reescriben ese campo al activarse y guardan el
  precio de lista en `CampanaLiquidacionProducto.precio_original`. Las NxM no
  tocan el precio (se materializan como línea gratis en el POS), por eso no
  entran en `precio_oferta`.
"""

import hashlib
import json
import logging

from django.db.models import Max, Q
from django.utils import timezone

from rest_framework import serializers

from app.models import (
    AtributoOpcion,
    CampanaLiquidacionProducto,
    Categoria,
    Movimientos_Producto,
    ProductoAtributoValor,
    Productos_Atributos,
)

logger = logging.getLogger('app')


# Categorías dadas de baja en la recategorización v1.2: se ocultan por prefijo
# de nombre, igual que `categorias_existentes` en la web (app/views.py).
PREFIJO_CATEGORIA_OBSOLETA = '_ZZ_'

NOMBRE_ATRIBUTO_ESPECIALIDAD = 'Especialidad'
NOMBRE_ATRIBUTO_MARCA = 'Marca'
NOMBRE_ATRIBUTO_COLOR = 'Color'
# atributo3 apunta a "Sexo" (el atributo realmente poblado: 137.947 productos).
# "Género" existe como atributo aparte pero con valores distintos y sin uso.
NOMBRE_ATRIBUTO_GENERO = 'Sexo'
NOMBRE_ATRIBUTO_GENERO_ALT = 'Género'


# ─────────────────────────── atributos ───────────────────────────

def obtener_atributo(nombre, nombre_alternativo=None):
    """Devuelve el `Productos_Atributos` por nombre (case-insensitive) o None."""
    attr = Productos_Atributos.objects.filter(nombre__iexact=nombre).first()
    if attr is None and nombre_alternativo:
        attr = Productos_Atributos.objects.filter(
            nombre__iexact=nombre_alternativo).first()
    return attr


def obtener_atributo_especialidad():
    return obtener_atributo(NOMBRE_ATRIBUTO_ESPECIALIDAD)


def especialidades_de(producto):
    """[{id, nombre}] de las especialidades v1.2 de un producto.

    `nombre` es el slug estable (`running`, `futbol`, …), que es lo que guarda
    `AtributoOpcion.valor`; la web tampoco envía otra etiqueta.
    """
    if producto is None:
        return []
    filas = (ProductoAtributoValor.objects
             .filter(producto=producto,
                     atributo__nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD)
             .select_related('opcion')
             .order_by('opcion__valor'))
    return [{'id': f.opcion_id, 'nombre': f.opcion.valor} for f in filas]


def valor_opcion(opcion):
    """Texto de una `AtributoOpcion` (marca/color/género), '' si es None."""
    return opcion.valor if opcion else ''


# ─────────────────────────── precios ───────────────────────────

def oferta_vigente_producto(producto_id, sucursal_id, ahora=None):
    """Campaña de liquidación %/PRECIO_FIJO vigente sobre un producto, o None.

    Replica el predicado canónico de `_ofertas_activas_sucursal`
    (app/views_modulo_campanas_liquidacion.py) pero acotado a UN producto: esa
    función carga todas las campañas de la sucursal, lo que es demasiado para
    un escaneo de etiqueta. Si ese predicado cambia, hay que cambiar este.

    Las NxM quedan fuera a propósito: no modifican el precio.
    """
    ahora = ahora or timezone.now()
    vigente = (
        Q(activo=True,
          campana__estado='ACTIVA',
          campana__fecha_inicio__lte=ahora,
          campana__sucursales__id=sucursal_id)
        & (Q(campana__fecha_fin__isnull=True) | Q(campana__fecha_fin__gte=ahora))
    )
    return (CampanaLiquidacionProducto.objects
            .filter(vigente,
                    producto_id=producto_id,
                    campana__tipo_regla__in=['PORCENTAJE', 'PRECIO_FIJO'])
            .select_related('campana')
            .first())


def resolver_precios(producto, sucursal_id, ahora=None):
    """(precio_venta, precio_oferta, precio_vigente) de un producto.

    - `precio_vigente`: lo que cobra el POS = `Producto.precioventa`.
    - `precio_oferta`: precio de liquidación si hay campaña vigente, si no None.
    - `precio_venta`: precio de lista. Con campaña vigente es el snapshot
      `precio_original` (el precio que muy probablemente está impreso en la
      etiqueta); sin campaña es el mismo `precioventa`.
    """
    precio_vigente = int(producto.precioventa or 0)
    item = oferta_vigente_producto(producto.id, sucursal_id, ahora)
    if item is None:
        return precio_vigente, None, precio_vigente

    precio_oferta = int(item.precio_liquidacion or precio_vigente)
    precio_venta = int(item.precio_original or precio_vigente)
    return precio_venta, precio_oferta, precio_vigente


# ─────────────────────────── fechas ───────────────────────────

# Conceptos que cuentan como ENTRADA REAL de mercadería. Mismo criterio que
# `CONCEPTOS_INGRESO_MERCADERIA` en app/api/external/views.py (contrato con
# AllConnected): recepciones + el despacho interno de bodega central a tienda.
CONCEPTOS_INGRESO_MERCADERIA = (
    'RECEPCION_COMPRA',
    'INGRESO_INICIAL',
    'INGRESO_MANUAL',
    'REPOSICION_STOCK',
    'SOBRANTE_INGRESO',
    'TRASPASO_SUCURSAL',
)

# El saldo de apertura de la migración Laravel entró como INGRESO_INICIAL con
# la fecha de la carga (~2026-01-22), no la llegada real: se excluye o toda
# etiqueta antigua parecería "recién ingresada".
REF_SALDO_INICIAL_SINTETICO = 'MIGRACION_LARAVEL'


def fecha_ingreso_producto(producto):
    """Fecha de alta del producto en catálogo (date) o None."""
    fecha = getattr(producto, 'fecha_creacion', None)
    if not fecha:
        return None
    return timezone.localtime(fecha).date() if timezone.is_aware(fecha) else fecha.date()


def ultima_fecha_ingreso_talla(producto_talla, fallback=None):
    """Fecha de la última entrada real de mercadería del SKU (date) o fallback."""
    ultima = (Movimientos_Producto.objects
              .filter(ProductoTalla=producto_talla,
                      concepto__in=CONCEPTOS_INGRESO_MERCADERIA)
              .exclude(referencia_externa=REF_SALDO_INICIAL_SINTETICO)
              .aggregate(m=Max('fecha'))['m'])
    return ultima or fallback


# ─────────────────────────── payload de producto ───────────────────────────

def _fecha_iso(valor):
    return valor.isoformat() if valor else None


def serializar_producto(producto_talla, sucursal_id, ahora=None):
    """Payload completo de un `Producto_Talla` para el verificador de etiquetas.

    Forma fija del contrato con la app Flutter: no quitar ni renombrar claves.
    """
    producto = producto_talla.producto
    precio_venta, precio_oferta, precio_vigente = resolver_precios(
        producto, sucursal_id, ahora)

    categoria = producto.categoria
    padre = categoria.padre if categoria else None
    fecha_alta = fecha_ingreso_producto(producto)

    return {
        'producto_id': producto.id,
        'producto_talla_id': producto_talla.id,
        'sku': str(producto_talla.sku or ''),
        'articulo': producto.articulo or '',
        'descripcion': producto.descripcion or '',
        'marca': valor_opcion(producto.atributo1),
        'color': valor_opcion(producto.atributo2),
        'genero': valor_opcion(producto.atributo3),
        'talla': producto_talla.talla or '',
        'categoria': ({'id': categoria.id, 'nombre': categoria.nombre}
                      if categoria else None),
        'categoria_padre': ({'id': padre.id, 'nombre': padre.nombre}
                            if padre else None),
        'especialidades': especialidades_de(producto),
        'precio_venta': precio_venta,
        'precio_oferta': precio_oferta,
        'precio_vigente': precio_vigente,
        'fecha_ingreso': _fecha_iso(fecha_alta),
        'ultima_fecha_ingreso': _fecha_iso(
            ultima_fecha_ingreso_talla(producto_talla, fallback=fecha_alta)),
        'stock_sucursal': producto_talla.stock_sucursal(sucursal_id),
        'sucursal_id': producto.sucursal_id,
    }


def serializar_coincidencia(producto_talla, sucursal_id):
    """Fila liviana para `otras_coincidencias` (no dispara queries de precio)."""
    return {
        'producto_talla_id': producto_talla.id,
        'sku': str(producto_talla.sku or ''),
        'talla': producto_talla.talla or '',
        'stock_sucursal': producto_talla.stock_sucursal(sucursal_id),
    }


# ─────────────────────────── serializers de entrada ───────────────────────────

class VerificarEtiquetaSerializer(serializers.Serializer):
    """Body de POST producto/verificar-etiqueta/."""

    producto_talla_id = serializers.IntegerField(min_value=1)
    precio_etiqueta = serializers.IntegerField(min_value=0)
    fecha_etiqueta = serializers.DateField(required=False, allow_null=True)
    sucursal_id = serializers.IntegerField(required=False, allow_null=True)


class ActualizarProductoSerializer(serializers.Serializer):
    """Body de POST producto/actualizar/.

    Solo llegan los campos que se quieren cambiar. `request_id` es obligatorio:
    es la clave de idempotencia contra el doble toque y el reintento de red.

    `especialidades_ids` tiene semántica de REEMPLAZO: si la clave viene, el
    producto queda exactamente con esas especialidades (lista vacía = quitar
    todas). Si la clave no viene, no se toca nada.
    """

    producto_id = serializers.IntegerField(min_value=1)
    sucursal_id = serializers.IntegerField(required=False, allow_null=True)
    request_id = serializers.CharField(max_length=64, allow_blank=False)

    precio_venta = serializers.IntegerField(required=False, min_value=0)
    categoria_id = serializers.IntegerField(required=False, allow_null=True)
    especialidades_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False)
    # Marca/color/género aceptan el id de la AtributoOpcion o su texto exacto.
    marca = serializers.CharField(required=False, allow_blank=False, max_length=100)
    color = serializers.CharField(required=False, allow_blank=False, max_length=100)
    genero = serializers.CharField(required=False, allow_blank=False, max_length=100)

    CAMPOS_EDITABLES = (
        'precio_venta', 'categoria_id', 'especialidades_ids',
        'marca', 'color', 'genero',
    )

    def validate(self, attrs):
        if not any(campo in self.initial_data for campo in self.CAMPOS_EDITABLES):
            raise serializers.ValidationError(
                'No se envió ningún campo para actualizar')
        return attrs


# ─────────────────────── resolución de valores editables ───────────────────────

def resolver_opcion_atributo(valor_bruto, nombre_atributo, nombre_alternativo=None):
    """Resuelve marca/color/género a una `AtributoOpcion` existente.

    Acepta el id numérico de la opción o su texto exacto (case-insensitive).
    NUNCA crea opciones nuevas: dejar que un teléfono dé de alta marcas es la
    receta para tener 'NIKE', 'nike' y 'Nlke' en el catálogo.

    Devuelve (opcion, error_texto). Solo uno de los dos es no-None.
    """
    attr = obtener_atributo(nombre_atributo, nombre_alternativo)
    if attr is None:
        return None, f'El atributo "{nombre_atributo}" no existe en el sistema'

    texto = str(valor_bruto).strip()
    if not texto:
        return None, f'El valor de "{nombre_atributo}" viene vacío'

    if texto.isdigit():
        opcion = AtributoOpcion.objects.filter(atributo=attr, id=int(texto)).first()
        if opcion:
            return opcion, None

    opcion = AtributoOpcion.objects.filter(atributo=attr, valor__iexact=texto).first()
    if opcion:
        return opcion, None

    return None, (f'"{texto}" no es un valor válido de {nombre_atributo}. '
                  f'Elige una de las opciones existentes.')


def resolver_categoria(categoria_id):
    """Valida una categoría v1.2. Devuelve (categoria, error_texto).

    Se acepta tanto un padre ("Calzado", la rama completa) como una hija
    ("Calzado › Zapatillas"), igual que el modal Crear Producto Manual de la
    web: elegir el padre nunca es un error. Solo se rechazan las categorías
    dadas de baja (prefijo `_ZZ_`), que la web tampoco ofrece.
    """
    categoria = Categoria.objects.filter(id=categoria_id).select_related('padre').first()
    if categoria is None:
        return None, f'La categoría {categoria_id} no existe'
    if (categoria.nombre or '').startswith(PREFIJO_CATEGORIA_OBSOLETA):
        return None, (f'La categoría "{categoria.nombre}" está dada de baja '
                      f'(recategorización v1.2) y no se puede asignar')
    return categoria, None


def resolver_especialidades(ids):
    """Valida ids de especialidades. Devuelve (opciones, error_texto).

    Mismo criterio que `crear_producto_manual`: solo se aceptan opciones del
    atributo "Especialidad". A diferencia de la web (que las descarta en
    silencio), aquí un id inválido es un error explícito: desde el celular no
    hay forma de que el usuario note que su selección se perdió.
    """
    attr = obtener_atributo_especialidad()
    if attr is None:
        return None, ('El atributo Especialidad no existe '
                      '(falta correr sembrar_taxonomia_v12)')

    ids_limpios = [int(i) for i in ids]
    if not ids_limpios:
        return [], None

    opciones = list(AtributoOpcion.objects.filter(atributo=attr, id__in=ids_limpios))
    encontrados = {o.id for o in opciones}
    faltantes = [i for i in ids_limpios if i not in encontrados]
    if faltantes:
        return None, (f'Especialidades inexistentes o de otro atributo: '
                      f'{", ".join(str(i) for i in faltantes)}')
    return opciones, None


# ─────────────────────── catálogo de selectores ───────────────────────

def construir_catalogo():
    """Todo lo que la app necesita para armar los selectores de recategorización.

    Mismas fuentes que la web, para que un producto recategorizado desde el
    celular sea indistinguible de uno recategorizado desde el escritorio:

    - categorías → mismo queryset que `categorias_existentes` (app/views.py):
      `Categoria` sin las dadas de baja (`_ZZ_`), ordenadas por nombre. Se
      devuelve PLANO con `padre_id`: la app arma la jerarquía en el cliente y
      así la forma del JSON no cambia cuando el árbol crezca.
    - marcas / colores / géneros / especialidades → mismo queryset que
      `opciones_atributo`: `AtributoOpcion` del atributo, ordenado por valor.

    Los atributos se resuelven con `obtener_atributo`, EL MISMO resolutor que
    usa el update. Si el catálogo ofreciera opciones de un atributo distinto
    del que se escribe (p. ej. "Género" en vez de "Sexo"), la app mostraría
    valores que el update rechazaría con 400.

    `es_padre` = la categoría TIENE subcategorías (sirve para pintar el
    drill-down). Que sea raíz se sabe por `padre_id is None`.

    4 queries en total.
    """
    filas = list(
        Categoria.objects
        .exclude(nombre__startswith=PREFIJO_CATEGORIA_OBSOLETA)
        .order_by('nombre')
        .values('id', 'nombre', 'padre_id')
    )
    con_hijos = {f['padre_id'] for f in filas if f['padre_id'] is not None}
    categorias = [{
        'id': f['id'],
        'nombre': f['nombre'],
        'padre_id': f['padre_id'],
        'es_padre': f['id'] in con_hijos,
    } for f in filas]

    # Un solo golpe a AtributoOpcion para los 4 atributos.
    atributos = {
        'especialidades': obtener_atributo(NOMBRE_ATRIBUTO_ESPECIALIDAD),
        'marcas': obtener_atributo(NOMBRE_ATRIBUTO_MARCA),
        'colores': obtener_atributo(NOMBRE_ATRIBUTO_COLOR),
        'generos': obtener_atributo(NOMBRE_ATRIBUTO_GENERO, NOMBRE_ATRIBUTO_GENERO_ALT),
    }
    ids_attr = [a.id for a in atributos.values() if a is not None]
    opciones = list(
        AtributoOpcion.objects.filter(atributo_id__in=ids_attr)
        .order_by('valor').values('id', 'valor', 'atributo_id')
    )
    por_atributo = {}
    for o in opciones:
        por_atributo.setdefault(o['atributo_id'], []).append(
            {'id': o['id'], 'nombre': o['valor']})

    faltantes = [k for k, v in atributos.items() if v is None]
    if faltantes:
        logger.warning('Catálogo móvil: atributos ausentes en BD: %s',
                       ', '.join(faltantes))

    payload = {'categorias': categorias}
    for clave, attr in atributos.items():
        payload[clave] = por_atributo.get(attr.id, []) if attr else []
    return payload


def version_catalogo(payload):
    """Huella del catálogo, estable entre workers (se deriva del contenido)."""
    crudo = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(crudo.encode('utf-8')).hexdigest()[:16]
