"""
Precios de venta: redondeo del modal (...990) y reglas por factor de costo.
"""
from decimal import ROUND_HALF_UP, Decimal


def redondeo_js(valor):
    """Math.round de JS para positivos."""
    return int(Decimal(valor).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def redondear_990(valor):
    """redondearPrecio990() del modal: baja al millar y suma 990."""
    return valor if valor < 1000 else (valor // 1000) * 1000 + 990


def precio_por_factor(costo, factor):
    """costo × factor, redondeado como el modal (...990)."""
    return redondear_990(redondeo_js(Decimal(costo) * Decimal(factor)))


def precio_por_regla(costo, umbral, factor_bajo, factor_alto):
    """Venta = costo × factor_bajo bajo el umbral de costo, × factor_alto desde él."""
    return precio_por_factor(costo, factor_bajo if costo < umbral else factor_alto)


def fmt(n):
    """12345 → '12.345'."""
    return f'{int(n):,}'.replace(',', '.')
