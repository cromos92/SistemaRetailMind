"""Helpers de ordenamiento de tallas.

Las tallas son texto libre ('36', '7,5', 'XL', '12K'): el orden natural es
numéricas primero (por valor, tolerando coma decimal chilena) y después las
alfabéticas. Espejo del `ordenarTallas()` JS de buscar_productos_sucursal.html.
"""


def clave_orden_talla(texto):
    """Clave de orden para tallas: numéricas primero y en orden natural."""
    t = (texto or "").strip()
    try:
        return (0, float(t.replace(",", ".")), t)
    except ValueError:
        return (1, 0.0, t)
