"""
Alertas de cambio de precio hacia las tiendas.

Una "alerta" es un `CambioPrecioPendiente` (una fila por ficha/sucursal
afectada, visible en el hub Control de Precios) más una
`NotificacionCambioPrecio` por cada usuario activo de esa sucursal (campana
del menú, filtrada por la sucursal de la sesión).

Antes cada flujo que sincronizaba precios a otras sucursales armaba su alerta
inline y con criterios distintos:

- la creación manual sólo avisaba si la ficha destino tenía stock > 0, así
  que a una tienda con el producto en 0 se le cambiaba el precio en silencio;
- la edición rápida nunca avisaba a la sucursal de la ficha editada cuando el
  usuario trabajaba desde OTRA sucursal (buscando "en toda la red").

Regla única desde 22-sep-2026: se avisa a la sucursal de una ficha SIEMPRE que
alguien que no está en esa sucursal le cambió el precio, tenga o no stock (el
stock se informa en el mensaje). Nunca lanza: una alerta fallida no debe
abortar el cambio de precio que la originó.
"""
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger('app')

DIAS_VENCIMIENTO_ALERTA = 7


def prioridad_por_porcentaje(porcentaje):
    """MEDIA hasta 20 %, ALTA sobre 20 %, URGENTE sobre 50 % (en valor absoluto)."""
    pct = abs(float(porcentaje or 0))
    if pct > 50:
        return 'URGENTE'
    if pct > 20:
        return 'ALTA'
    return 'MEDIA'


def usuarios_de_sucursal(sucursal):
    """Usuarios activos (EmpresaUser.status=True) de la sucursal, sin repetir."""
    from app.models import EmpresaUser

    vistos = set()
    usuarios = []
    qs = (EmpresaUser.objects
          .filter(sucursal=sucursal, status=True)
          .select_related('user')
          .order_by('id'))
    for eu in qs:
        if eu.user_id in vistos or eu.user is None:
            continue
        vistos.add(eu.user_id)
        usuarios.append(eu.user)
    return usuarios


def alertar_precio_sucursal(ficha, precio_anterior, precio_nuevo, usuario=None,
                            desde_alias='', origen='', estado='APLICADO',
                            motivo=None, mensaje=None, stock_sucursal=None,
                            tipo_cambio='SINCRONIZACION'):
    """
    Crea la alerta + notificaciones para la sucursal de `ficha`.

    - ficha: Producto cuyo precio fue (o quedó pendiente de ser) cambiado.
    - precio_anterior / precio_nuevo: enteros.
    - desde_alias: sucursal desde la que trabaja quien hizo el cambio.
    - origen: texto corto para el mensaje ("edición rápida", "creación manual").
    - estado: 'APLICADO' (ya sincronizado, informativo) o 'PENDIENTE' (requiere
      aprobación; lo usa el umbral de divergencia de la edición rápida).
    - stock_sucursal: si se conoce, se informa en el mensaje (no condiciona nada).

    Devuelve un dict con el detalle (para la respuesta al frontend) o None si
    no se pudo crear (p.ej. ficha sin tallas: el modelo exige producto_talla).
    """
    from app.models import CambioPrecioPendiente, NotificacionCambioPrecio, Producto_Talla

    try:
        precio_anterior = int(precio_anterior or 0)
        precio_nuevo = int(precio_nuevo or 0)
        sucursal = ficha.sucursal
        if sucursal is None:
            return None

        # Talla ancla de la alerta: la de más stock (sólo para mostrar; el
        # cambio aplica a todas las tallas de la ficha). NO se exige stock.
        talla = (Producto_Talla.objects
                 .filter(producto=ficha)
                 .order_by('-stock', 'id')
                 .first())
        if talla is None:
            logger.info("Alerta de precio omitida: ficha sin tallas producto_id=%s", ficha.id)
            return None

        if stock_sucursal is None:
            stock_sucursal = sum(
                Producto_Talla.objects.filter(producto=ficha).values_list('stock', flat=True)
            )
        stock_sucursal = int(stock_sucursal or 0)

        diferencia = precio_nuevo - precio_anterior
        porcentaje = round(diferencia / precio_anterior * 100, 2) if precio_anterior else 0
        prioridad = prioridad_por_porcentaje(porcentaje)
        articulo = ficha.articulo

        if motivo is None:
            motivo = (f'Precio sincronizado automáticamente desde {desde_alias}'
                      if estado == 'APLICADO'
                      else f'Cambio de precio desde {desde_alias} pendiente de aprobación')
            if origen:
                motivo += f' ({origen})'

        if usuario is not None and not getattr(usuario, 'is_authenticated', False):
            usuario = None

        cambio = CambioPrecioPendiente.objects.create(
            producto_talla=talla,
            sucursal=sucursal,
            precio_anterior=precio_anterior,
            precio_nuevo=precio_nuevo,
            diferencia=diferencia,
            porcentaje_cambio=porcentaje,
            tipo_cambio=tipo_cambio,
            estado=estado,
            motivo=motivo,
            creado_por=usuario,
            prioridad=prioridad,
            fecha_vencimiento=timezone.now() + timedelta(days=DIAS_VENCIMIENTO_ALERTA),
            notificado=True,
        )

        if mensaje is None:
            signo = '+' if diferencia > 0 else ''
            if estado == 'PENDIENTE':
                mensaje = (f"⚠️ Cambio de precio pendiente de aprobación en {articulo}: "
                           f"${precio_anterior:,} → ${precio_nuevo:,} ({signo}{porcentaje:.1f}%). "
                           f"Enviado desde {desde_alias}. Requiere revisión.")
            else:
                mensaje = (f"💰 Precio actualizado en {articulo}: "
                           f"${precio_anterior:,} → ${precio_nuevo:,} ({signo}{porcentaje:.1f}%) "
                           f"desde {desde_alias}")
                if origen:
                    mensaje += f" · {origen}"
            if stock_sucursal <= 0:
                mensaje += " · sin stock en tu sucursal"
            else:
                mensaje += f" · {stock_sucursal} uds en tu sucursal"

        usuarios = usuarios_de_sucursal(sucursal)
        tipo_notif = 'NUEVA'
        for u in usuarios:
            NotificacionCambioPrecio.objects.create(
                cambio_precio=cambio,
                usuario=u,
                tipo=tipo_notif,
                mensaje=mensaje,
            )

        logger.info(
            "Alerta de precio creada: cambio_id=%s sucursal=%s producto_id=%s %s->%s estado=%s usuarios=%s",
            cambio.id, sucursal.alias, ficha.id, precio_anterior, precio_nuevo, estado, len(usuarios),
        )
        return {
            'cambio_id': cambio.id,
            'sucursal_id': sucursal.id,
            'sucursal': sucursal.alias,
            'producto_id': ficha.id,
            'precio_anterior': precio_anterior,
            'precio_nuevo': precio_nuevo,
            'porcentaje': float(porcentaje),
            'estado': estado,
            'prioridad': prioridad,
            'stock': stock_sucursal,
            'usuarios_notificados': len(usuarios),
        }
    except Exception:
        logger.exception(
            "No se pudo crear la alerta de precio para producto_id=%s",
            getattr(ficha, 'id', None),
        )
        return None
