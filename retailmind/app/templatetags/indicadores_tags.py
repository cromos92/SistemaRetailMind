"""Template tags del glosario de indicadores (app/services/indicadores.py).

Uso:
    {% load indicadores_tags %}
    <div class="nexo-kpi-label" title="{% definicion 'cobertura_dias' 30 %}">Cobertura (30 d)</div>
    {% nombre_indicador 'pct_stock_viejo' %}

La salida se autoescapa (va dentro de atributos `title`). Una clave
desconocida devuelve cadena vacía y deja un warning en el log, nunca rompe
la página.
"""
import logging

from django import template

from app.services.indicadores import GLOSARIO
from app.services.indicadores import definicion as _definicion
from app.services.indicadores import nombre as _nombre

register = template.Library()
logger = logging.getLogger('app')


@register.simple_tag(name='definicion')
def definicion(clave, ventana=None):
    """Fórmula del indicador `clave`, con la ventana (días) sustituida."""
    if clave not in GLOSARIO:
        logger.warning('indicadores_tags: clave desconocida en el glosario: %r', clave)
        return ''
    return _definicion(clave, ventana)


@register.simple_tag(name='nombre_indicador')
def nombre_indicador(clave):
    """Nombre canónico del indicador `clave`."""
    if clave not in GLOSARIO:
        logger.warning('indicadores_tags: clave desconocida en el glosario: %r', clave)
        return ''
    return _nombre(clave)
