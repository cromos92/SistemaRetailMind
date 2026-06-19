"""Throttling de la API de cliente final (app de fidelización).

`ScopedRateThrottle` de DRF usa por defecto el cache `default` (LocMem por
worker), así que el tope por scope se multiplica por el número de workers y NO es
exacto. `SharedScopedRateThrottle` apunta al cache `throttle` (Redis compartido
cuando hay REDIS_URL; ver settings.CACHES), de modo que el límite sea GLOBAL y
exacto entre workers. Si Redis no está disponible, el cache `throttle` cae a
LocMem (degradación: vuelve al comportamiento por-worker, no rompe).
"""
from django.core.cache import caches
from rest_framework.throttling import ScopedRateThrottle


class SharedScopedRateThrottle(ScopedRateThrottle):
    cache = caches['throttle']
