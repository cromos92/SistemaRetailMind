#!/usr/bin/env python3
"""
check_pos_bundle.py
===================

Mide el tamaño real (gzip) del HTML y los estáticos que descarga el POS al
cargar cada una de las 7 pantallas del flujo.

Metas (plan POS · Fase 6):
    - HTML de `ticket_venta`      < 500 KB (sin gzip).
    - HTML de `generacionVentas`  < 700 KB (sin gzip; tiene mucho wizard).
    - CSS + JS de cabecera con `Cache-Control: immutable` o max-age >= 1 año.

Uso:
    python scripts/check_pos_bundle.py \\
        --base https://retail.webappsolutions.cl \\
        --kiosk \\
        --session-cookie "retailmind=..." \\
        --html-budget 700

El `--session-cookie` es obligatorio para rutas protegidas. Copialo desde el
DevTools del navegador tras hacer login.

Salida: tabla ASCII con bytes transferidos, bytes gzip, y verificación de
cache headers de CSS/JS críticos.
"""
from __future__ import annotations

import argparse
import gzip
import io
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


POS_ROUTES = [
    ('Login',            '/accounts/login/'),
    ('Ticket venta',     '/app/ticket-venta/'),
    ('Generación venta', '/app/generacion-ventas/'),
    ('POS Transbank',    '/app/gestion-pos-transbank/'),
    ('Devoluciones',     '/app/gestion-cambios-devoluciones/'),
    ('Cuadratura',       '/app/cuadratura-caja/'),
    ('Emisión DTE',      '/app/emision-dte/'),
]

# Assets críticos que cargan TODAS las rutas POS (ver header.html).
CRITICAL_ASSETS = [
    '/static/css/bootstrap.min.css',
    '/static/css/icons.min.css',
    '/static/css/app.min.css',
    '/static/css/nexo-design-system.css',
    '/static/css/nexo-responsive.css',
    '/static/css/pos-kiosk.css',
]


@dataclass
class FetchResult:
    url: str
    status: int
    bytes_wire: int
    bytes_raw: int
    cache_control: str
    content_type: str
    elapsed_ms: int

    @property
    def kb_wire(self) -> float:
        return self.bytes_wire / 1024

    @property
    def kb_raw(self) -> float:
        return self.bytes_raw / 1024


def fetch(url: str, cookie: Optional[str], timeout: int = 15) -> FetchResult:
    headers = {
        'User-Agent': 'RetailMind-POS-BundleChecker/1.0',
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
        'Accept-Encoding': 'gzip, br',
        'Accept-Language': 'es-CL,es;q=0.9',
    }
    if cookie:
        headers['Cookie'] = cookie

    req = Request(url, headers=headers)
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
            encoding = (resp.headers.get('Content-Encoding') or '').lower()
            cc = resp.headers.get('Cache-Control') or ''
            ct = resp.headers.get('Content-Type') or ''
            wire_size = len(raw)
            if 'gzip' in encoding:
                try:
                    body = gzip.decompress(raw)
                except OSError:
                    body = raw
            elif 'br' in encoding:
                try:
                    import brotli  # type: ignore
                    body = brotli.decompress(raw)
                except Exception:
                    body = raw
            else:
                body = raw
            elapsed = int((time.perf_counter() - t0) * 1000)
            return FetchResult(url, status, wire_size, len(body), cc, ct, elapsed)
    except HTTPError as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        return FetchResult(url, e.code, 0, 0, '', '', elapsed)
    except URLError as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f"  !! error de red: {e}", file=sys.stderr)
        return FetchResult(url, 0, 0, 0, '', '', elapsed)


def format_row(cols: list[str], widths: list[int]) -> str:
    return ' | '.join(c.ljust(w) for c, w in zip(cols, widths))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='URL base (p.ej. https://retail.webappsolutions.cl)')
    parser.add_argument('--kiosk', action='store_true', help='Añade ?kiosk=1 a cada ruta POS')
    parser.add_argument('--session-cookie', dest='cookie', default=None,
                        help="Cookie completa de sesión (ej 'retailmind=xxx; csrftoken=yyy')")
    parser.add_argument('--html-budget', type=int, default=700,
                        help='Presupuesto máximo de HTML en KB raw (default 700).')
    parser.add_argument('--asset-budget', type=int, default=1200,
                        help='Presupuesto máximo por asset crítico en KB raw (default 1200).')
    args = parser.parse_args()

    base = args.base.rstrip('/')
    total_wire = 0
    total_raw = 0
    fail = False

    widths = [22, 7, 10, 10, 9, 18]

    # --- HTMLs ---
    print('\n== HTML de pantallas POS ==')
    print(format_row(['Pantalla', 'Status', 'Wire KB', 'Raw KB', 'ms', 'Cache-Control'], widths))
    print('-+-'.join('-' * w for w in widths))

    for label, path in POS_ROUTES:
        sep = '&' if '?' in path else '?'
        url = urljoin(base + '/', path.lstrip('/')) + (f'{sep}kiosk=1' if args.kiosk else '')
        r = fetch(url, args.cookie)
        total_wire += r.bytes_wire
        total_raw += r.bytes_raw
        status_str = str(r.status)
        if r.status >= 400 or r.status == 0:
            status_str = f'!{r.status}'
            fail = True
        print(format_row([label, status_str,
                          f'{r.kb_wire:,.1f}', f'{r.kb_raw:,.1f}',
                          str(r.elapsed_ms), r.cache_control[:18]], widths))
        if r.kb_raw > args.html_budget:
            print(f'   !! {label} pasa el budget de {args.html_budget} KB.')
            fail = True

    # --- Assets críticos ---
    print('\n== Assets críticos (CSS/JS del header) ==')
    print(format_row(['Asset', 'Status', 'Wire KB', 'Raw KB', 'ms', 'Cache-Control'], widths))
    print('-+-'.join('-' * w for w in widths))

    manifest_re = re.compile(r'<link[^>]+href="([^"]+\.css[^"]*)"', re.IGNORECASE)

    # Intentamos resolver fingerprint hash de ticket-venta (si está detrás de
    # ManifestStaticFilesStorage, los nombres tendrán hash).
    ticket_url = urljoin(base + '/', 'app/ticket-venta/') + ('?kiosk=1' if args.kiosk else '')
    try:
        first_html = fetch(ticket_url, args.cookie).bytes_raw
        if first_html:
            with urlopen(Request(ticket_url, headers={
                'User-Agent': 'RetailMind-POS-BundleChecker/1.0',
                'Cookie': args.cookie or '',
                'Accept-Encoding': 'identity'
            })) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
                found = manifest_re.findall(html)
        else:
            found = []
    except Exception:
        found = []

    for asset in CRITICAL_ASSETS:
        # si hay versión con hash, usamos la primera coincidencia
        hashed = next((u for u in found if asset.split('/')[-1].split('.', 1)[0] in u), None)
        url = urljoin(base + '/', (hashed or asset).lstrip('/'))
        r = fetch(url, args.cookie)
        total_wire += r.bytes_wire
        total_raw += r.bytes_raw
        status_str = str(r.status)
        if r.status == 404:
            status_str = '404'
        print(format_row([asset.split('/')[-1], status_str,
                          f'{r.kb_wire:,.1f}', f'{r.kb_raw:,.1f}',
                          str(r.elapsed_ms), r.cache_control[:18]], widths))
        if r.kb_raw > args.asset_budget:
            print(f'   !! {asset} pasa el budget de {args.asset_budget} KB.')
            fail = True
        if r.status == 200 and ('immutable' not in r.cache_control and 'max-age' not in r.cache_control):
            print(f'   !! {asset} sin Cache-Control con max-age (revisa ManifestStaticFilesStorage).')

    # --- Totales ---
    print('\n== Totales ==')
    print(f'  Wire:   {total_wire/1024:,.1f} KB')
    print(f'  Raw:    {total_raw/1024:,.1f} KB')
    print(f'  Status: {"FAIL" if fail else "OK"}')

    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
