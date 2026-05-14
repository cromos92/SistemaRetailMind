"""
Service para sincronizar fotos de portada desde ecommerces externos.

Lee las credenciales desde ``CredencialesEcommerce`` (tabla en BD, gestionada
desde la pantalla de Configuración → Integraciones Ecommerce). NO depende de
variables de entorno, así una integración nueva (paola.cl, etc.) se agrega
desde la UI sin redeploy.

Contrato esperado del ecommerce remoto:
    GET <url_api>/api/v1/products/images/?skus=SKU1,SKU2,...
    Header: <header_name>: <api_key>
    Respuesta JSON: {"SKU1": "https://.../thumb.webp", "SKU2": null, ...}
"""
from __future__ import annotations

import hashlib
import logging
from typing import Dict, Iterable, List, Optional

from django.utils import timezone

from app.models import CredencialesEcommerce, FotoPortadaArticulo

# ``requests`` se importa lazy: si el venv aún no lo tiene, Django igual
# puede arrancar y la pantalla de configuración se carga. Sólo las acciones
# que tocan el endpoint remoto (probar, sincronizar) van a fallar con un
# mensaje claro pidiendo `pip install requests`.
try:
    import requests  # type: ignore
    _REQUESTS_OK = True
except ImportError:  # pragma: no cover
    requests = None  # type: ignore
    _REQUESTS_OK = False

logger = logging.getLogger('app')

TIMEOUT_SEGUNDOS = 15
LOTE_DEFAULT = 100


class RealsportImagenesError(Exception):
    """Error al consultar el endpoint de portadas de un ecommerce."""


def _ensure_requests():
    if not _REQUESTS_OK:
        raise RealsportImagenesError(
            "El paquete 'requests' no está instalado en este entorno. "
            "Ejecutá: pip install requests"
        )


def _construir_url(credencial: CredencialesEcommerce) -> str:
    base = (credencial.url_api or '').strip().rstrip('/')
    return f'{base}/api/v1/products/images/'


def _headers(credencial: CredencialesEcommerce) -> Dict[str, str]:
    return {
        credencial.header_name or 'X-AllConnected-Key': credencial.api_key,
        'Accept': 'application/json',
        'User-Agent': 'RetailMind-PortadaSync/1.0',
    }


def probar_conexion(credencial: CredencialesEcommerce) -> Dict[str, object]:
    """Pega un GET /health/ con la API key para validar credenciales.

    Devuelve ``{ok: bool, status: int|None, detalle: str}``. Nunca lanza.
    """
    if not _REQUESTS_OK:
        return {
            'ok': False,
            'status': None,
            'detalle': "Falta el paquete 'requests'. Ejecutá: pip install requests",
        }
    base = (credencial.url_api or '').strip().rstrip('/')
    url = f'{base}/api/v1/health/'
    try:
        r = requests.get(url, headers=_headers(credencial), timeout=TIMEOUT_SEGUNDOS)
        return {
            'ok': r.status_code == 200,
            'status': r.status_code,
            'detalle': r.text[:200] if r.text else '',
        }
    except requests.RequestException as exc:
        logger.warning('probar_conexion(%s) error: %s', credencial.codigo, exc)
        return {'ok': False, 'status': None, 'detalle': str(exc)[:200]}


def obtener_urls_portada(
    credencial: CredencialesEcommerce,
    skus: Iterable[str],
) -> Dict[str, Optional[str]]:
    """Modo lookup puntual: consulta SKUs específicos.

    Devuelve ``{sku: url|None}``. Útil para `--articulo`.
    Para sync masivo usar ``traer_catalogo_portadas`` (mucho más eficiente).
    """
    _ensure_requests()

    skus_lista = [s for s in skus if s]
    if not skus_lista:
        return {}

    try:
        r = requests.get(
            _construir_url(credencial),
            params={'skus': ','.join(skus_lista)},
            headers=_headers(credencial),
            timeout=TIMEOUT_SEGUNDOS,
        )
    except requests.RequestException as exc:
        raise RealsportImagenesError(f'conexión fallida: {exc}') from exc

    if r.status_code != 200:
        raise RealsportImagenesError(
            f'HTTP {r.status_code}: {r.text[:200] if r.text else ""}'
        )

    try:
        data = r.json()
    except ValueError as exc:
        raise RealsportImagenesError(f'respuesta no es JSON: {exc}') from exc

    if not isinstance(data, dict):
        raise RealsportImagenesError(f'respuesta inesperada: {type(data).__name__}')

    return data


def _chunks(lista: List[str], tamanio: int) -> Iterable[List[str]]:
    for i in range(0, len(lista), tamanio):
        yield lista[i:i + tamanio]


def traer_catalogo_portadas(
    credencial: CredencialesEcommerce,
    page_size: int = 500,
    on_page=None,
) -> Iterable[Dict[str, str]]:
    """Generador que pagina el endpoint en modo catálogo completo.

    Cada iteración devuelve un dict ``{sku: url}`` con las portadas de una
    página. El ecommerce solo devuelve productos que tienen al menos una
    imagen, así no perdemos tiempo con productos sin foto.

    Si ``on_page`` es callable, se invoca con ``(page, total, len(results))``
    para logging en vivo desde el management command.
    """
    _ensure_requests()

    page = 1
    while True:
        try:
            r = requests.get(
                _construir_url(credencial),
                params={'page': page, 'page_size': page_size},
                headers=_headers(credencial),
                timeout=TIMEOUT_SEGUNDOS,
            )
        except requests.RequestException as exc:
            raise RealsportImagenesError(
                f'conexión fallida (page={page}): {exc}'
            ) from exc

        if r.status_code != 200:
            raise RealsportImagenesError(
                f'HTTP {r.status_code} (page={page}): {r.text[:200] if r.text else ""}'
            )

        try:
            data = r.json()
        except ValueError as exc:
            raise RealsportImagenesError(f'respuesta no es JSON: {exc}') from exc

        results = data.get('results') or {}
        total = int(data.get('count') or 0)
        next_page = data.get('next_page')

        if on_page:
            try:
                on_page(page, total, len(results))
            except Exception:
                pass

        if results:
            yield results

        if not next_page:
            break
        page = int(next_page)


CACHE_TTL_RESOLUCION = 3600


def _cache_key(articulo: str, empresa_id: Optional[int]) -> str:
    """Genera una clave de cache válida para memcached/redis.

    Memcached rechaza claves con espacios o caracteres no-ASCII. Hashear con
    sha1 (12 caracteres) garantiza claves cortas y seguras para cualquier
    backend, y nunca colisiona en este uso porque articulo+empresa ya es
    suficientemente único.
    """
    raw = f'{articulo}|{empresa_id or 0}'
    h = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]
    return f'foto_portada:{h}'


def resolver_foto_portada_url(articulo: str, empresa_id: Optional[int] = None) -> str:
    """Devuelve la URL de portada para ``articulo`` (string vacío si no hay).

    Lógica de preferencia:
      1. ``FotoPortadaArticulo`` cuyo ``origen.empresa_id == empresa_id``
         (la propia empresa del producto).
      2. Fallback: cualquier ``FotoPortadaArticulo`` activa, ordenada por
         ``origen.prioridad`` DESC.

    Resultado cacheado en el backend ``default`` con TTL 1h. Usar este helper
    desde template tags y serializers para mantener una sola fuente de verdad.
    """
    if not articulo:
        return ''

    from django.core.cache import cache
    cache_key = _cache_key(articulo, empresa_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    foto = None
    if empresa_id:
        foto = (
            FotoPortadaArticulo.objects
            .filter(
                articulo=articulo,
                es_principal=True,
                origen__empresa_id=empresa_id,
                origen__activo=True,
            )
            .only('url_foto')
            .first()
        )
    if foto is None:
        foto = (
            FotoPortadaArticulo.objects
            .filter(articulo=articulo, es_principal=True, origen__activo=True)
            .select_related('origen')
            .order_by('-origen__prioridad')
            .only('url_foto', 'origen__prioridad')
            .first()
        )

    url = foto.url_foto if foto else ''
    cache.set(cache_key, url, timeout=CACHE_TTL_RESOLUCION)
    return url


def sincronizar_credencial(
    credencial: CredencialesEcommerce,
    page_size: int = 500,
    on_page=None,
) -> Dict[str, int]:
    """Sincroniza TODAS las portadas del ecommerce hacia RetailMind.

    Estrategia: pide al ecommerce **su** catálogo completo de SKUs con
    foto (paginado), y por cada par ``(sku, url)`` hace upsert en
    ``FotoPortadaArticulo``. Es N veces más eficiente que iterar todos
    los articulos locales preguntando uno por uno.

    Solo se guardan URLs cuyo ``sku`` exista como ``articulo`` en algún
    ``Producto`` de RetailMind — los SKUs huérfanos del ecommerce se
    descartan (no nos sirven). Devuelve resumen.
    """
    from app.models import Producto, Producto_Talla  # import diferido

    procesados = 0
    con_foto = 0
    sin_match_local = 0
    match_exacto = 0
    match_flexible = 0
    match_por_talla = 0
    paginas = 0
    articulos_actualizados: List[str] = []

    # Tres niveles de match. Por qué tres: realsport.cl puede tener su
    # `Product.sku` poblado con cualquiera de estas tres cosas según cómo
    # AllConected publique:
    #   1. articulo "tal cual" → match directo.
    #   2. articulo normalizado (trim + upper) → match flexible.
    #   3. Producto_Talla.sku (código numérico por talla, BigInt) → en este
    #      caso encontramos el articulo padre y guardamos la foto contra él.
    todos_articulos = (
        Producto.objects.values_list('articulo', flat=True).distinct()
    )
    articulos_exactos = set()
    articulos_norm: Dict[str, str] = {}
    for a in todos_articulos:
        if not a:
            continue
        articulos_exactos.add(a)
        articulos_norm.setdefault(a.strip().upper(), a)

    # Mapping sku_de_talla (string) → articulo padre.
    talla_sku_a_articulo: Dict[str, str] = {}
    for sku_int, articulo in Producto_Talla.objects.values_list(
        'sku', 'producto__articulo',
    ):
        if sku_int and articulo:
            talla_sku_a_articulo.setdefault(str(sku_int), articulo)

    logger.info(
        'sync %s: articulos=%d, normalizados=%d, tallas_sku=%d',
        credencial.codigo, len(articulos_exactos),
        len(articulos_norm), len(talla_sku_a_articulo),
    )

    try:
        for pagina_resultados in traer_catalogo_portadas(
            credencial, page_size=page_size, on_page=on_page,
        ):
            paginas += 1
            for sku, url in pagina_resultados.items():
                procesados += 1
                if not url or not sku:
                    continue

                # Intento 1: match exacto contra Producto.articulo.
                articulo_real = sku if sku in articulos_exactos else None
                if articulo_real:
                    match_exacto += 1
                else:
                    # Intento 2: trim + uppercase contra Producto.articulo.
                    articulo_real = articulos_norm.get(sku.strip().upper())
                    if articulo_real:
                        match_flexible += 1
                    else:
                        # Intento 3: el SKU de realsport es Producto_Talla.sku.
                        # Buscamos el articulo padre y guardamos la foto a ese.
                        articulo_real = talla_sku_a_articulo.get(sku.strip())
                        if articulo_real:
                            match_por_talla += 1

                if not articulo_real:
                    sin_match_local += 1
                    continue

                FotoPortadaArticulo.objects.update_or_create(
                    articulo=articulo_real,
                    origen=credencial,
                    defaults={'url_foto': url, 'es_principal': True},
                )
                con_foto += 1
                articulos_actualizados.append(articulo_real)
    except RealsportImagenesError as exc:
        logger.error('sync %s: %s', credencial.codigo, exc)
        credencial.ultima_sync_at = timezone.now()
        credencial.ultima_sync_resultado = f'ERROR: {str(exc)[:200]}'
        credencial.save(update_fields=['ultima_sync_at', 'ultima_sync_resultado'])
        raise

    credencial.ultima_sync_at = timezone.now()
    credencial.ultima_sync_resultado = (
        f'paginas={paginas}, procesados={procesados}, '
        f'con_foto={con_foto} (exacto={match_exacto}, '
        f'flexible={match_flexible}, talla={match_por_talla}), '
        f'sin_match={sin_match_local}'
    )
    credencial.save(update_fields=['ultima_sync_at', 'ultima_sync_resultado'])

    _invalidar_cache(articulos_actualizados)

    return {
        'paginas': paginas,
        'procesados': procesados,
        'con_foto': con_foto,
        'match_exacto': match_exacto,
        'match_flexible': match_flexible,
        'match_por_talla': match_por_talla,
        'sin_match_local': sin_match_local,
    }


def sincronizar_articulos_puntuales(
    credencial: CredencialesEcommerce,
    articulos: List[str],
    lote: int = LOTE_DEFAULT,
) -> Dict[str, int]:
    """Variante para --articulo/--refrescar puntual.

    Sigue usando el modo lookup por SKUs (POST de obtener_urls_portada).
    Útil cuando vos sabés exactamente qué articulos querés actualizar.
    """
    procesados = 0
    con_foto = 0
    sin_foto = 0
    errores = 0
    articulos_actualizados: List[str] = []

    for batch in _chunks(articulos, lote):
        try:
            data = obtener_urls_portada(credencial, batch)
        except RealsportImagenesError as exc:
            errores += 1
            logger.warning('sync %s: lote falló (%s)', credencial.codigo, exc)
            continue

        for sku in batch:
            procesados += 1
            url = data.get(sku)
            if url:
                con_foto += 1
                FotoPortadaArticulo.objects.update_or_create(
                    articulo=sku, origen=credencial,
                    defaults={'url_foto': url, 'es_principal': True},
                )
                articulos_actualizados.append(sku)
            else:
                sin_foto += 1

    _invalidar_cache(articulos_actualizados)
    return {
        'procesados': procesados, 'con_foto': con_foto,
        'sin_foto': sin_foto, 'errores': errores,
    }


def _invalidar_cache(articulos: List[str]) -> None:
    """Borra entradas de cache de los articulos sincronizados.

    El template tag ``foto_portada`` cachea por
    ``foto_portada:<articulo>:<empresa_id>``. Como no sabemos las empresas
    acá, hacemos best-effort iterando sobre las empresas activas con
    credenciales. Si no hay Redis configurado, ``cache.delete_many`` es no-op.
    """
    if not articulos:
        return
    try:
        from django.core.cache import caches
        from app.models import Empresa

        cache = caches['default']
        empresas_ids = list(
            Empresa.objects.filter(credenciales_ecommerce__isnull=False)
            .values_list('id', flat=True).distinct()
        )
        # También limpiamos la clave global (empresa_id = 0) por si alguien
        # consultó con empresa desconocida y cacheó el fallback.
        empresas_ids.append(None)
        keys = [
            _cache_key(art, eid)
            for art in articulos for eid in empresas_ids
        ]
        if keys:
            cache.delete_many(keys)
    except Exception as exc:  # cache no esencial — nunca rompe el sync
        logger.debug('invalidar_cache best-effort falló: %s', exc)
