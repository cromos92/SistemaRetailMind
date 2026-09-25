"""
Correcciones después de cargar una factura: precio de venta y color.

Precio de venta — nunca baja nada (salvo un precio indicado explícitamente):
  - Si una ficha BAJÓ de precio en la carga (HistorialCambioPrecio desde el día
    de la carga), vuelve a su precio anterior, el más caro.
  - Líneas con precio por regla (sin precio a mano en el JSON): si quedaron
    bajo costo × factor piso (1,9 en Nike), suben a ese piso.
  Se aplica a la ficha de la bodega del JSON y a la misma variante en otras
  bodegas, con historial, lotes FIFO activos y aviso a las tiendas.

Color: solo en las fichas CREADAS en la carga que siguen con el color del JSON
(MULTI). Las que ya existían no se tocan.
"""
from datetime import datetime, time
from pathlib import Path

from django.db.models import Min
from django.utils import timezone

from app.models import (
    AtributoOpcion, Compras_Producto, Dte, HistorialCambioPrecio, LoteProducto,
    Movimientos_Producto, Producto, Productos_Recepcionados,
)
from app.utils_producto_match import normalizar_articulo, ordenar_por_reciente

from .facturas import ErrorCarga, variantes_rut
from .perfiles import clave_marca, perfil_para
from .precios import fmt, precio_por_factor


def dte_de_factura(data, ruta):
    """DTE de una factura YA cargada (por "dte_id" o folio + RUT, solo facturas)."""
    if data.get('dte_id'):
        return Dte.objects.get(id=data['dte_id'])
    dtes = [d for d in Dte.objects.filter(numero_documento=data['folio'],
                                          emisor__rut__in=variantes_rut(data['proveedor_rut']))
            if 'FACTURA' in str(d.tipo_documento or '').upper() and not d.es_nota_credito]
    if len(dtes) != 1:
        raise ErrorCarga(f'{Path(ruta).name}: el folio {data["folio"]} calza con {len(dtes)} '
                         f'facturas; pon "dte_id" en el JSON')
    return dtes[0]


def inicio_de_carga(dte, data, ruta):
    """Inicio (aware) del día en que se cargó la factura: desde ahí se mira el historial."""
    carga = (Movimientos_Producto.objects.filter(dte=dte, concepto='INGRESO_MANUAL')
             .aggregate(d=Min('fecha'))['d'])
    if carga is None:
        raise ErrorCarga(f'{Path(ruta).name}: la factura {data["folio"]} no tiene carga '
                         f'(ningún INGRESO_MANUAL): corre primero cargar_productos_factura')
    return timezone.make_aware(datetime.combine(carga, time.min))


def opciones_de_color(colores):
    """{COLOR: AtributoOpcion} para los colores pedidos; error si alguno no existe."""
    opciones = {}
    for valor in set(colores.values()):
        op = (AtributoOpcion.objects.filter(atributo__nombre__iexact='Color', valor__iexact=valor)
              .order_by('id').first())
        if op is None:
            raise ErrorCarga(f'No existe el color «{valor}» en el sistema')
        opciones[valor] = op
    return opciones


def planificar_ajuste(art, linea, data, dte, sucursal, desde, override, color,
                      opts, opciones_color):
    """Plan de corrección de un artículo.

    opts: factor (None = factor piso del perfil), sin_x19 (bool).
    """
    marca = str(linea.get('marca') or data.get('marca') or '')
    factor = opts.get('factor')
    if factor is None:
        factor = perfil_para(marca).factor_piso
    todas = [f for f in ordenar_por_reciente(
                 Producto.objects.filter(articulo__icontains=art.split(' ')[0])
                 .select_related('sucursal', 'atributo1', 'atributo2'))
             if normalizar_articulo(f.articulo) == art
             and clave_marca(getattr(f.atributo1, 'valor', '')) == clave_marca(marca)]
    locales = [f for f in todas if f.sucursal_id == sucursal.id]
    if linea.get('ficha_id'):
        locales = [f for f in locales if f.id == int(linea['ficha_id'])]
    plan = {'art': art, 'folio': data['folio'], 'desc': linea.get('descripcion', ''),
            'costo': int(linea['costo']), 'a_mano': bool(linea.get('precioventa')),
            'duda': linea.get('_precio_duda'), 'precios': [], 'color': None,
            'errores': [], 'ficha': None, 'x19': None, 'factor': factor}
    if not locales:
        plan['errores'].append(f'no hay ficha en {sucursal.alias}')
        return plan
    ficha = plan['ficha'] = locales[0]
    ident = (ficha.atributo1_id, ficha.atributo2_id, ficha.atributo3_id, ficha.categoria_id)
    variantes = [ficha] + [f for f in todas if f.sucursal_id != sucursal.id and
                           (f.atributo1_id, f.atributo2_id, f.atributo3_id, f.categoria_id) == ident]
    plan['nueva'] = ficha.fecha_creacion is not None and ficha.fecha_creacion >= desde

    piso = 0
    if not plan['a_mano'] and not opts.get('sin_x19'):
        piso = plan['x19'] = precio_por_factor(plan['costo'], factor)

    for f in variantes:
        actual = int(f.precioventa or 0)
        hist = list(HistorialCambioPrecio.objects.filter(
            producto=f, fecha_cambio__gte=desde, motivo__startswith='[PRECIO_VENTA]')
            .order_by('fecha_cambio', 'id').values_list('precio_anterior', flat=True)[:1])
        antes = int(hist[0]) if hist else None
        if override is not None:
            objetivo, motivo = override, 'precio indicado (--precio)'
        else:
            objetivo = max(actual, antes or 0, piso)
            motivos = []
            if antes and antes > actual and objetivo == antes:
                motivos.append(f'bajó en la carga ({fmt(antes)} → {fmt(actual)}): vuelve al anterior')
            if piso and objetivo == piso and piso > max(actual, antes or 0):
                motivos.append(f'bajo costo × {factor}')
            motivo = '; '.join(motivos)
        if objetivo != actual:
            plan['precios'].append({'ficha': f, 'actual': actual, 'antes': antes,
                                    'nuevo': objetivo, 'motivo': motivo})

    if color:
        op = opciones_color[color]
        color_json = str(linea.get('color') or data.get('color') or '').upper()
        if not plan['nueva']:
            pass  # ya existía: se respeta su color
        elif ficha.atributo2_id == op.id:
            pass
        elif str(getattr(ficha.atributo2, 'valor', '')).upper() != color_json:
            plan['errores'].append(f'color actual «{ficha.atributo2.valor}» no es el del JSON '
                                   f'({color_json}): no se toca')
        else:
            choque = Producto.objects.filter(
                sucursal=ficha.sucursal, atributo1_id=ficha.atributo1_id, atributo2=op,
                atributo3_id=ficha.atributo3_id, categoria_id=ficha.categoria_id,
            ).exclude(id=ficha.id)
            choque = [c for c in choque if normalizar_articulo(c.articulo) == art]
            if choque:
                plan['errores'].append(f'ya existe #{choque[0].id} con color {color}: no se cambia')
            else:
                plan['color'] = (ficha.atributo2.valor, op)
    return plan


def aplicar_ajuste(plan, user):
    """Aplica la corrección de un artículo. Devuelve las líneas de texto de lo hecho."""
    from app.services.alertas_precio import alertar_precio_sucursal
    from app.services.historial_precios import registrar_cambios_precio

    hecho = []
    origen = plan['ficha'].sucursal.alias
    for p in plan['precios']:
        f = Producto.objects.get(id=p['ficha'].id)
        anterior = int(f.precioventa or 0)
        f.precioventa = p['nuevo']
        f.precioSugerido = p['nuevo']
        f.save(update_fields=['precioventa', 'precioSugerido'])
        lotes = LoteProducto.objects.filter(producto_talla__producto=f, cantidad_disponible__gt=0,
                                            activo=True).update(precio_venta_unitario=p['nuevo'])
        registrar_cambios_precio(
            f, {'precioventa': anterior}, usuario=user,
            motivo=f'Corrección tras carga de factura {plan["folio"]}: {p["motivo"]}',
            tipo_cambio='ACTUALIZACION_MANUAL', lotes_afectados=lotes)
        if f.sucursal_id != plan['ficha'].sucursal_id:
            alertar_precio_sucursal(f, anterior, p['nuevo'], usuario=user, desde_alias=origen,
                                    origen='corrección carga factura', estado='APLICADO',
                                    motivo=f'Corrección de precio tras carga de factura {plan["folio"]}')
        hecho.append(f'  {plan["art"]} {f.sucursal.alias}: precio {fmt(anterior)} → {fmt(p["nuevo"])}')
    if plan['color']:
        viejo, op = plan['color']
        Producto.objects.filter(id=plan['ficha'].id).update(atributo2=op)
        cps = (Productos_Recepcionados.objects.filter(producto_talla__producto_id=plan['ficha'].id)
               .values_list('compra_producto_talla__compra_producto_id', flat=True))
        Compras_Producto.objects.filter(id__in=[c for c in cps if c]).update(atributo2=op.valor)
        hecho.append(f'  {plan["art"]}: color {viejo} → {op.valor}')
    return hecho
