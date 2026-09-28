"""
Estado de pago de documentos de COMPRA (Dte.estado_pago).

Historia: el campo se escribió con 6 grafías distintas según la vista
('PENDIENTE'/'Pendiente', 'PAGADO'/'Pagado', 'Abonado', 'Parcial') y se leía
con igualdad exacta, así que un abono parcial o un 'PENDIENTE' en mayúsculas
sacaba la factura de los filtros de deuda (hallazgo B3-02).

Regla desde este módulo:
- ESCRIBIR siempre el valor canónico en mayúsculas (los de ESTADO_PAGO_CHOICES
  del modelo más 'PARCIAL' para abonos): usar `recalcular_estado_pago(dte)`.
- LEER siempre sin distinguir mayúsculas: `q_estado_pago_pendiente()` o
  `normalizar_estado_pago(valor)`.

No se toca lo que escriben los módulos de ventas (siempre 'PAGADO'/'PENDIENTE').
Sin migración: 'PARCIAL' no está en choices, pero choices no se valida en BD y
el front (pagoBadge) ya lo reconoce sin distinguir mayúsculas.
"""
from django.db.models import Q, Sum

ESTADO_PAGO_PENDIENTE = 'PENDIENTE'
ESTADO_PAGO_PARCIAL = 'PARCIAL'
ESTADO_PAGO_PAGADO = 'PAGADO'
ESTADO_PAGO_VENCIDO = 'VENCIDO'

# Grafías históricas que significan "abonado, con saldo".
_SINONIMOS_PARCIAL = {'PARCIAL', 'ABONADO'}

# Tolerancia por redondeo: Dte_Detalle_Pago.monto es IntegerField y
# Dte.monto_con_iva puede traer decimales.
TOLERANCIA_PESOS = 1


def normalizar_estado_pago(valor):
    """Devuelve la grafía canónica de un estado_pago leído de la BD o del
    cliente ('Pagado' -> 'PAGADO', 'Abonado'/'Parcial' -> 'PARCIAL').
    Valores desconocidos o vacíos -> 'PENDIENTE'."""
    v = (valor or '').strip().upper()
    if v == ESTADO_PAGO_PAGADO:
        return ESTADO_PAGO_PAGADO
    if v in _SINONIMOS_PARCIAL:
        return ESTADO_PAGO_PARCIAL
    if v == ESTADO_PAGO_VENCIDO:
        return ESTADO_PAGO_VENCIDO
    return ESTADO_PAGO_PENDIENTE


def esta_pagado(valor):
    return (valor or '').strip().upper() == ESTADO_PAGO_PAGADO


def q_estado_pago_pendiente(prefijo=''):
    """Q para 'tiene saldo por pagar' sin distinguir mayúsculas: PENDIENTE,
    PARCIAL y ABONADO (grafía que dejaba registrarPagoDTE hasta sep-2026).
    `prefijo` permite usarlo sobre relaciones ('dte__')."""
    campo = f'{prefijo}estado_pago__iexact'
    return (
        Q(**{campo: ESTADO_PAGO_PENDIENTE})
        | Q(**{campo: ESTADO_PAGO_PARCIAL})
        | Q(**{campo: 'ABONADO'})
    )


def calcular_estado_pago(total_pagos, monto_total):
    """Estado canónico para un total pagado (efectivo + NC + compensaciones)
    frente al monto del documento, con tolerancia de $1."""
    total = float(total_pagos or 0)
    monto = float(monto_total or 0)
    if total > 0 and total >= monto - TOLERANCIA_PESOS:
        return ESTADO_PAGO_PAGADO
    if total > 0:
        return ESTADO_PAGO_PARCIAL
    return ESTADO_PAGO_PENDIENTE


def total_pagado(dte):
    """Suma de todas las filas Dte_Detalle_Pago del documento."""
    from app.models import Dte_Detalle_Pago
    return Dte_Detalle_Pago.objects.filter(dte=dte).aggregate(t=Sum('monto'))['t'] or 0


def recalcular_estado_pago(dte, guardar=True):
    """Recalcula estado_pago desde los pagos y lo guarda (solo ese campo).
    Devuelve el estado canónico escrito."""
    estado = calcular_estado_pago(total_pagado(dte), dte.monto_con_iva)
    dte.estado_pago = estado
    if guardar:
        dte.save(update_fields=['estado_pago'])
    return estado
