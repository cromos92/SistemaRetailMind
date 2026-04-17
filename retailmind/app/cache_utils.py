# -*- coding: utf-8 -*-
"""
Utilidades de caching para el módulo ventas.

Este módulo expone helpers para cachear respuestas de endpoints de dashboards
(solo lectura) y para invalidar granularmente por sucursal desde signals.

Diseño:
- Todo el caching del módulo ventas usa el alias `ventas` configurado en
  `settings.CACHES`. Es Redis si `REDIS_URL` está seteado, si no LocMemCache.
- Las claves se construyen con `make_cache_key(prefix, **parts)` que genera un
  hash SHA1 determinístico a partir de los argumentos. Esto evita problemas de
  tamaño y caracteres no permitidos en keys.
- La invalidación por sucursal usa un contador de versión: cada sucursal
  tiene una clave `ventas:ver:<sucursal_id>`. Cuando algún signal modifica
  datos (Ticket, Dte, etc.) se incrementa ese contador, haciendo que las
  claves previas expiren lógicamente sin necesidad de iterar keys.
- La invalidación global (todas las sucursales) usa `ventas:ver:__global__`
  para endpoints que no filtran por sucursal (p.ej. comparativa multi-sucursal).
"""
from __future__ import annotations

import hashlib
import json
from functools import wraps
from typing import Any, Callable

from django.core.cache import caches
from django.http import JsonResponse

VENTAS_CACHE = 'ventas'
VERSION_KEY_PREFIX = 'ventas:ver'
GLOBAL_VERSION_KEY = f'{VERSION_KEY_PREFIX}:__global__'


def _get_cache():
    return caches[VENTAS_CACHE]


def _version_key(sucursal_id: Any) -> str:
    if sucursal_id is None:
        return GLOBAL_VERSION_KEY
    return f'{VERSION_KEY_PREFIX}:{sucursal_id}'


def get_version(sucursal_id: Any = None) -> int:
    """Devuelve el número de versión actual para la sucursal (o global)."""
    cache = _get_cache()
    key = _version_key(sucursal_id)
    value = cache.get(key)
    if value is None:
        value = 1
        cache.set(key, value, timeout=None)
    return value


def make_cache_key(prefix: str, sucursal_id: Any = None, **parts) -> str:
    """
    Construye una clave estable para el cache `ventas`.

    Incluye el número de versión actual de la sucursal y un hash SHA1 de los
    kwargs ordenados — invalidar la sucursal (incrementando la versión) hace
    que todas las keys anteriores sean ignoradas sin iterar el cache.
    """
    version_local = get_version(sucursal_id)
    version_global = get_version(None)
    payload = json.dumps(parts, sort_keys=True, default=str)
    digest = hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]
    suc_segment = sucursal_id if sucursal_id is not None else 'all'
    return f'{prefix}:v{version_global}.{version_local}:{suc_segment}:{digest}'


def invalidate_ventas_cache(sucursal_id: Any = None, *, global_too: bool = False) -> None:
    """
    Incrementa el contador de versión de una sucursal (o global) para
    invalidar todas las claves que dependan de ella.

    - `sucursal_id=None` invalida solo el bucket global.
    - `global_too=True` invalida también el global además de la sucursal.
    """
    cache = _get_cache()
    targets = []
    if sucursal_id is None or global_too:
        targets.append(GLOBAL_VERSION_KEY)
    if sucursal_id is not None:
        targets.append(_version_key(sucursal_id))

    for key in targets:
        try:
            cache.incr(key)
        except ValueError:
            # La key no existía aún → inicializar en 2 (cualquier >1 sirve para invalidar)
            cache.set(key, 2, timeout=None)


def cache_ventas_json(
    prefix: str,
    timeout: int = 60,
    sucursal_param: str = 'sucursal_id',
    vary_on_session: bool = False,
):
    """
    Decorator para cachear respuestas `JsonResponse` de endpoints GET de ventas.

    Usa el cache `ventas` (Redis o LocMem). Deriva la clave de:
    - `prefix` + query params ordenados
    - sucursal (del param o de la sesión)
    - usuario si `vary_on_session=True` (dashboards que cambian según permisos)

    Requiere que la view sea GET. Si no lo es, ejecuta la view sin cachear.
    Si el JsonResponse tiene `success=False`, NO se cachea (para no persistir errores).
    """
    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.method != 'GET':
                return view_func(request, *args, **kwargs)

            suc = request.GET.get(sucursal_param)
            if not suc:
                suc = (
                    request.session.get('idSucursalActual')
                    or request.session.get('sucursalActual')
                )

            # Construir payload para hash: query params + user si aplica
            parts = {
                'q': dict(sorted(request.GET.items())),
                'view_args': args,
                'view_kwargs': kwargs,
            }
            if vary_on_session and request.user.is_authenticated:
                parts['user'] = request.user.id

            cache_key = make_cache_key(prefix, sucursal_id=suc, **parts)
            cache = _get_cache()

            cached = cache.get(cache_key)
            if cached is not None:
                return JsonResponse(cached, safe=False)

            response = view_func(request, *args, **kwargs)

            if isinstance(response, JsonResponse):
                try:
                    payload = json.loads(response.content.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    return response
                # No cachear respuestas de error explícito
                if isinstance(payload, dict) and payload.get('success') is False:
                    return response
                cache.set(cache_key, payload, timeout=timeout)

            return response

        return wrapper
    return decorator
