"""
Service para VERIFICAR que las fotos de portada sincronizadas desde un ecommerce
externo "realmente se pasaron". Complementa a ``realsport_imagenes_service`` (que
SINCRONIZA): aquí solo se mide, nunca se escribe catálogo.

Dos capas:

  1. **Cobertura** — cuántos ``articulo`` del catálogo (por empresa y por sucursal)
     resuelven a una URL de portada. Reusa ``resolver_fotos_portada_bulk()``, o sea
     mide EXACTAMENTE lo que el usuario ve en la UI (incluye el fallback entre
     empresas: la propia empresa primero, si no la de mayor ``prioridad``).

  2. **Liveness de URL** — chequea por HTTP que cada ``url_foto`` devuelve una
     imagen viva (200/206 + ``Content-Type: image/*``). Detecta 404, CDN muerto,
     timeouts y redirecciones a HTML (login/Cloudflare). El sync NUNCA valida esto:
     ``con_foto`` solo dice que el SKU matcheó un articulo local, no que la foto sirva.

Es read-only sobre la BD y hace GET/HEAD read-only contra el CDN de las fotos.
"""
from __future__ import annotations

import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional

from django.utils import timezone

from app.models import CredencialesEcommerce, FotoPortadaArticulo, Producto, Sucursal
from app.services.realsport_imagenes_service import resolver_fotos_portada_bulk

try:
    import requests  # type: ignore
    _REQUESTS_OK = True
except ImportError:  # pragma: no cover
    requests = None  # type: ignore
    _REQUESTS_OK = False

logger = logging.getLogger('app')

TIMEOUT_DEFAULT = 10
WORKERS_DEFAULT = 12
MAX_EJEMPLOS_MUERTAS = 50

# Estados de liveness de una URL.
ESTADOS = ('ok', 'http_404', 'http_otro', 'no_imagen', 'error_red')


class VerificacionFotosError(Exception):
    """Error al verificar fotos (p. ej. falta ``requests``)."""


def _ensure_requests():
    if not _REQUESTS_OK:
        raise VerificacionFotosError(
            "El paquete 'requests' no está instalado en este entorno. "
            "Ejecutá: pip install requests"
        )


# ───────────────────────── Cobertura ─────────────────────────

def verificar_cobertura_credencial(credencial: CredencialesEcommerce) -> Dict:
    """Cobertura de portadas del catálogo de la empresa de ``credencial``.

    Para cada sucursal activa de la empresa toma sus ``articulo`` distintos y los
    resuelve con ``resolver_fotos_portada_bulk`` (mismo helper del template tag),
    contando cuántos terminan con URL y cuántos no.
    """
    empresa = credencial.empresa
    sucursales = Sucursal.objects.filter(empresa=empresa, activa=True).order_by('alias')

    por_sucursal: List[Dict] = []
    art_empresa = set()
    art_con_foto = set()

    for suc in sucursales:
        articulos = list(
            Producto.objects.filter(sucursal=suc)
            .exclude(articulo='')
            .exclude(articulo__isnull=True)
            .values_list('articulo', flat=True)
            .distinct()
        )
        if not articulos:
            por_sucursal.append({
                'sucursal': suc.alias, 'sucursal_id': suc.id,
                'articulos': 0, 'con_foto': 0, 'sin_foto': 0,
            })
            continue

        fotos = resolver_fotos_portada_bulk(articulos, empresa_id=empresa.id)
        con = [a for a in articulos if fotos.get(a)]
        por_sucursal.append({
            'sucursal': suc.alias, 'sucursal_id': suc.id,
            'articulos': len(articulos), 'con_foto': len(con),
            'sin_foto': len(articulos) - len(con),
        })
        art_empresa.update(articulos)
        art_con_foto.update(con)

    return {
        'empresa': empresa.nombre,
        'empresa_id': empresa.id,
        'articulos': len(art_empresa),
        'con_foto': len(art_con_foto),
        'sin_foto': len(art_empresa) - len(art_con_foto),
        'por_sucursal': por_sucursal,
    }


# ───────────────────────── Liveness ─────────────────────────

def _clasificar(status: Optional[int], content_type: str) -> str:
    ct = (content_type or '').lower()
    if status == 404:
        return 'http_404'
    if status in (200, 206):
        return 'ok' if ct.startswith('image/') else 'no_imagen'
    return 'http_otro'


def _check_url(url: str, session, timeout: int, auth_headers: Optional[dict]):
    """Devuelve ``(estado, status_code, content_type)`` para una URL de foto.

    Intenta HEAD; si el CDN no lo soporta (405) o no da Content-Type, reintenta
    con GET de 1 byte (``Range``). Si da 403 y hay credenciales, reintenta con el
    header de la integración (por si el CDN está detrás del mismo auth).
    """
    try:
        r = session.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code in (403, 405) or (
            r.status_code in (200, 206) and not r.headers.get('Content-Type')
        ):
            hdrs = {'Range': 'bytes=0-0'}
            if r.status_code == 403 and auth_headers:
                hdrs.update(auth_headers)
            r = session.get(url, timeout=timeout, allow_redirects=True,
                            headers=hdrs, stream=True)
            r.close()
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de red => error_red
        return ('error_red', None, str(exc)[:120])

    ct = r.headers.get('Content-Type') or ''
    return (_clasificar(r.status_code, ct), r.status_code, ct)


def verificar_liveness_urls(
    urls: Iterable[str], *,
    auth_headers: Optional[dict] = None,
    workers: int = WORKERS_DEFAULT,
    timeout: int = TIMEOUT_DEFAULT,
    on_progress=None,
) -> Dict:
    """Chequea (en paralelo) que cada URL devuelva una imagen viva.

    Deduplica las URLs. Devuelve ``{'por_url': {url: (estado, status, ct)},
    'counters': {estado: n}, 'verificadas': n}``.
    """
    urls = [u for u in dict.fromkeys(urls) if u]  # dedup preservando orden
    counters = {e: 0 for e in ESTADOS}
    por_url: Dict[str, tuple] = {}
    if not urls:
        return {'por_url': por_url, 'counters': counters, 'verificadas': 0}

    _ensure_requests()

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=workers, pool_maxsize=workers, max_retries=0,
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({'User-Agent': 'RetailMind-PortadaVerify/1.0'})

    done = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futuros = {
                ex.submit(_check_url, u, session, timeout, auth_headers): u
                for u in urls
            }
            for fut in as_completed(futuros):
                u = futuros[fut]
                estado, status, ct = fut.result()
                por_url[u] = (estado, status, ct)
                counters[estado] = counters.get(estado, 0) + 1
                done += 1
                if on_progress and done % 100 == 0:
                    on_progress(done, len(urls))
    finally:
        session.close()

    return {'por_url': por_url, 'counters': counters, 'verificadas': len(urls)}


# ───────────────────────── Orquestación ─────────────────────────

def verificar_credencial(
    credencial: CredencialesEcommerce, *,
    muestra: Optional[int] = None,
    solo_cobertura: bool = False,
    workers: int = WORKERS_DEFAULT,
    timeout: int = TIMEOUT_DEFAULT,
    on_progress=None,
) -> Dict:
    """Verifica cobertura + liveness de las portadas de una integración.

    ``muestra``: si se indica y hay más URLs que eso, se chequea una muestra
    aleatoria (rápido para la UI). ``solo_cobertura``: salta el chequeo HTTP.
    """
    cobertura = verificar_cobertura_credencial(credencial)

    urls_info = {
        'total_urls': 0, 'verificadas': 0, 'muestra': False,
        'counters': {e: 0 for e in ESTADOS}, 'muertas_ejemplos': [],
    }

    if not solo_cobertura:
        filas = (
            FotoPortadaArticulo.objects
            .filter(origen=credencial)
            .exclude(url_foto='')
            .values_list('articulo', 'url_foto')
        )
        url_a_articulo: Dict[str, str] = {}
        for art, url in filas:
            if url:
                url_a_articulo.setdefault(url, art)

        urls = list(url_a_articulo.keys())
        total_urls = len(urls)
        es_muestra = False
        if muestra and total_urls > muestra:
            urls = random.sample(urls, muestra)
            es_muestra = True

        auth_headers = {
            credencial.header_name or 'X-AllConnected-Key': credencial.api_key,
        }
        liveness = verificar_liveness_urls(
            urls, auth_headers=auth_headers, workers=workers,
            timeout=timeout, on_progress=on_progress,
        )

        muertas: List[Dict] = []
        for url, (estado, status, ct) in liveness['por_url'].items():
            if estado != 'ok' and len(muertas) < MAX_EJEMPLOS_MUERTAS:
                muertas.append({
                    'articulo': url_a_articulo.get(url, ''),
                    'url': url, 'motivo': estado,
                    'status': status, 'content_type': ct,
                })

        urls_info = {
            'total_urls': total_urls,
            'verificadas': liveness['verificadas'],
            'muestra': es_muestra,
            'counters': liveness['counters'],
            'muertas_ejemplos': muertas,
        }

    return {
        'codigo': credencial.codigo,
        'empresa': credencial.empresa.nombre,
        'cobertura': cobertura,
        'urls': urls_info,
    }


def construir_resumen(resultado: Dict) -> str:
    """String compacto para ``ultima_verif_resultado`` (CharField 255)."""
    cob = resultado['cobertura']
    u = resultado['urls']
    base = f"cobertura {cob['con_foto']}/{cob['articulos']} articulos con foto"
    if u.get('verificadas'):
        c = u['counters']
        base += (
            f" | urls {c.get('ok', 0)} ok, {c.get('http_404', 0)} 404, "
            f"{c.get('no_imagen', 0)} no-img, {c.get('http_otro', 0)} otro, "
            f"{c.get('error_red', 0)} red"
            f"{' (muestra)' if u.get('muestra') else ''}"
        )
    return base[:255]


def persistir_resultado(credencial: CredencialesEcommerce, resultado: Dict) -> None:
    """Guarda ``ultima_verif_*`` en la credencial (espejo de ``ultima_sync_*``)."""
    credencial.ultima_verif_at = timezone.now()
    credencial.ultima_verif_resultado = construir_resumen(resultado)
    credencial.ultima_verif_detalle = json.dumps(
        resultado['urls'].get('muertas_ejemplos', []), ensure_ascii=False,
    )[:8000]
    credencial.save(update_fields=[
        'ultima_verif_at', 'ultima_verif_resultado', 'ultima_verif_detalle',
    ])
