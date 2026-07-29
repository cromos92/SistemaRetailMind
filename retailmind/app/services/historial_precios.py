"""
Registro centralizado de cambios de precio en HistorialCambioPrecio.

El modelo Producto solo guarda los valores VIGENTES de costo/sobreprecio/
precioventa: cualquier flujo que los pise sin pasar por acá pierde el precio
original para siempre (los lotes FIFO activos también se reescriben). Todo
endpoint que modifique precios debe capturar los valores anteriores y llamar
a registrar_cambios_precio() — una fila por campo que cambió, con el campo
etiquetado en el motivo ([COSTO] / [SOBREPRECIO] / [PRECIO_VENTA]), mismo
formato que usa la Edición rápida de precios (_registrar_cambio_precio).
"""
import logging

logger = logging.getLogger('app')

# (campo del Producto, etiqueta en el motivo)
_CAMPOS_PRECIO = (
    ('costo', 'COSTO'),
    ('sobreprecio', 'SOBREPRECIO'),
    ('precioventa', 'PRECIO_VENTA'),
)


def registrar_cambios_precio(producto, anteriores, usuario=None, motivo='',
                             tipo_cambio='MANUAL', ip_address=None,
                             tallas_afectadas=0, lotes_afectados=0):
    """
    Crea una fila de HistorialCambioPrecio por cada campo de precio que cambió.

    - producto: instancia YA actualizada (con los valores nuevos).
    - anteriores: dict {'costo': X, 'sobreprecio': Y, 'precioventa': Z} con los
      valores ANTES de pisar el producto. Claves ausentes se ignoran.
    - lotes_afectados: se anota solo en la fila de PRECIO_VENTA, que es el
      campo que se replica a los lotes FIFO activos.

    Nunca lanza: un fallo de auditoría no debe abortar la operación de negocio.
    Devuelve la cantidad de filas creadas.
    """
    from app.models import HistorialCambioPrecio

    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None

    creadas = 0
    for campo, etiqueta in _CAMPOS_PRECIO:
        if campo not in (anteriores or {}):
            continue
        try:
            valor_anterior = int(anteriores[campo] or 0)
            valor_nuevo = int(getattr(producto, campo) or 0)
            if valor_anterior == valor_nuevo:
                continue
            diferencia = valor_nuevo - valor_anterior
            porcentaje = round(diferencia / valor_anterior * 100, 2) if valor_anterior else 0
            HistorialCambioPrecio.objects.create(
                producto=producto,
                precio_anterior=valor_anterior,
                precio_nuevo=valor_nuevo,
                diferencia=diferencia,
                porcentaje_cambio=porcentaje,
                tipo_cambio=tipo_cambio,
                motivo=f'[{etiqueta}] {motivo}' if motivo else f'Cambio de {etiqueta}',
                usuario=usuario,
                ip_address=ip_address,
                tallas_afectadas=tallas_afectadas,
                lotes_afectados=lotes_afectados if campo == 'precioventa' else 0,
            )
            creadas += 1
        except Exception as e:
            logger.warning(
                'No se pudo registrar historial de precio (%s) para producto_id=%s: %s',
                campo, getattr(producto, 'id', None), e,
            )
    return creadas
