"""
Script CLI para diagnosticar y reparar retroactivamente las NCs históricas
que no generaron los `Movimientos_Producto` de reversa (stock nunca volvió
al origen / no se descontó del destino tras la NC).

Uso típico (desde la carpeta retailmind/):

    # Dry-run: lista todos los casos detectados.
    python _reparar_ncs_historicas.py

    # Exportar a CSV para revisión offline.
    python _reparar_ncs_historicas.py --export csv --out ncs_pendientes.csv

    # Aplicar la reparación sobre IDs específicos (lote controlado).
    python _reparar_ncs_historicas.py --apply --nc 123,456,789

    # Acotar por sucursal emisora o rango de fechas:
    python _reparar_ncs_historicas.py --sucursal 1 --desde 2026-01-01 --hasta 2026-04-30

    # Motivo textual que se adjunta a los movimientos y al tag en referencias.
    python _reparar_ncs_historicas.py --apply --nc 123 --motivo "Barrido abril 2026"

Salida:
  - Dry-run: tabla por consola con [NC id | tipo | #] [padre tipo | #]
    [sucursal origen → destino] [fase pre/post] [faltantes] [stock destino].
  - --apply: aplica llamando al mismo servicio que el endpoint
    (`reparar_nc_stock`) para garantizar consistencia con la UI. Reporta
    éxito, ya_reparado, stock_insuficiente, error por cada NC.

Exit codes:
  0 — sin pendientes (o aplicación exitosa completa).
  1 — hay pendientes detectados (dry-run) o aplicación parcial.
  2 — error de argumentos / setup.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime


def _bootstrap_django():
    try:
        import django  # noqa: F401
    except ImportError:
        print("Django no está instalado en este entorno.")
        sys.exit(2)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
    import django
    django.setup()


# Permite ejecutar `python _reparar_ncs_historicas.py` directamente sin
# `manage.py shell`: inicializa Django ANTES de importar los modelos.
if __name__ == '__main__' or 'django' not in sys.modules:
    _bootstrap_django()


from app.models import Dte  # noqa: E402
from app.views import (  # noqa: E402
    detectar_ncs_sin_stock,
    reparar_nc_stock,
)


def _parse_fecha(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        print(f"Fecha inválida: {s!r} (esperado YYYY-MM-DD)")
        sys.exit(2)


def _parse_ids(s):
    if not s:
        return set()
    try:
        return {int(x.strip()) for x in s.split(',') if x.strip()}
    except ValueError:
        print(f"IDs inválidos: {s!r} (esperado lista separada por comas)")
        sys.exit(2)


def cmd_dryrun(args):
    casos = detectar_ncs_sin_stock(
        sucursal_id=args.sucursal,
        fecha_inicio=_parse_fecha(args.desde),
        fecha_fin=_parse_fecha(args.hasta),
        tipo_documento_padre=args.tipo_padre,
    )
    if not casos:
        print("Sin NCs por reparar. Todo OK.")
        return 0

    print(f"Detectados {len(casos)} casos por reparar.\n")
    header = (
        f"{'NC ID':>6} {'NC#':>8} {'Tipo NC':<20} "
        f"{'Padre#':>8} {'Tipo padre':<20} "
        f"{'Origen':<12} {'→':<1} {'Destino':<12} "
        f"{'Fase':<6} {'Faltan':>7}"
    )
    print(header)
    print('-' * len(header))
    for c in casos:
        fase = 'POST' if c['recepcionado'] else 'PRE'
        print(
            f"{c['nc_id']:>6} "
            f"{str(c['nc_numero']):>8} "
            f"{c['nc_tipo'][:20]:<20} "
            f"{str(c['dte_padre_numero']):>8} "
            f"{c['dte_padre_tipo'][:20]:<20} "
            f"{(c['sucursal_origen'] or '-')[:12]:<12} → "
            f"{(c['sucursal_destino'] or '-')[:12]:<12} "
            f"{fase:<6} "
            f"{c['total_faltantes']:>7}"
        )

    if args.export == 'csv':
        out = args.out or 'ncs_pendientes.csv'
        with open(out, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow([
                'nc_id', 'nc_numero', 'nc_tipo', 'nc_fecha',
                'dte_padre_id', 'dte_padre_numero', 'dte_padre_tipo',
                'sucursal_origen', 'sucursal_destino', 'fase',
                'total_faltantes',
                'sku', 'descripcion', 'cantidad_nc', 'movimientos_existentes',
                'stock_origen', 'stock_destino', 'reparable',
            ])
            for c in casos:
                for l in c['lineas']:
                    w.writerow([
                        c['nc_id'], c['nc_numero'], c['nc_tipo'], c['nc_fecha'],
                        c['dte_padre_id'], c['dte_padre_numero'], c['dte_padre_tipo'],
                        c['sucursal_origen'], c['sucursal_destino'],
                        'POST' if c['recepcionado'] else 'PRE',
                        c['total_faltantes'],
                        l['sku'], l['descripcion'], l['cantidad_nc'],
                        l['movimientos_existentes'],
                        l['stock_origen_actual'], l['stock_destino_actual'],
                        int(l['reparable']),
                    ])
        print(f"\nExportado a: {out}")

    return 1  # hay pendientes


def cmd_apply(args):
    ids = _parse_ids(args.nc)
    if not ids:
        print("Debe indicar --nc con la lista de IDs a reparar (p.ej. --nc 123,456).")
        print("No hay modo masivo sin lista explícita por seguridad.")
        return 2

    motivo = args.motivo or 'Barrido retroactivo (script CLI)'
    usuario = args.usuario or os.environ.get('USERNAME') or 'cli'

    # Respetamos también los filtros por sucursal / fechas: sólo aplicamos
    # si el caso cae dentro del scope pedido.
    casos = {
        c['nc_id']: c
        for c in detectar_ncs_sin_stock(
            sucursal_id=args.sucursal,
            fecha_inicio=_parse_fecha(args.desde),
            fecha_fin=_parse_fecha(args.hasta),
            tipo_documento_padre=args.tipo_padre,
        )
    }

    exit_code = 0
    for nc_id in sorted(ids):
        if nc_id not in casos:
            print(f"[{nc_id}] Saltado: no aparece como pendiente (filtros y/o ya reparado).")
            exit_code = 1
            continue
        diag = casos[nc_id]
        lineas = [
            {'sku': l['sku'], 'cantidad': l['faltantes']}
            for l in diag['lineas']
            if l['reparable']
        ]
        if not lineas:
            print(f"[{nc_id}] Saltado: ninguna línea reparable (stock destino insuficiente).")
            exit_code = 1
            continue

        nc = Dte.objects.filter(id=nc_id).first()
        if nc is None:
            print(f"[{nc_id}] Saltado: NC no encontrada.")
            exit_code = 1
            continue

        status, payload = reparar_nc_stock(
            nc=nc,
            lineas_solicitadas=lineas,
            usuario=usuario,
            motivo=motivo,
        )
        ok = bool(payload.get('success'))
        etiqueta = 'OK ' if ok else (
            'YA ' if payload.get('ya_reparado')
            else ('SIN_STOCK' if payload.get('stock_insuficiente') else 'ERR')
        )
        msg = payload.get('message') or payload.get('error') or ''
        print(f"[{nc_id}] {etiqueta} status={status} {msg}")
        if not ok:
            exit_code = 1

    return exit_code


def main():
    p = argparse.ArgumentParser(
        description='Diagnostica y repara NCs históricas sin movimientos de stock.'
    )
    p.add_argument('--apply', action='store_true',
                   help='Aplica reparación sobre los IDs de --nc (dry-run si se omite).')
    p.add_argument('--nc', type=str, default='',
                   help='IDs de NC separados por coma (requerido para --apply).')
    p.add_argument('--sucursal', type=int, default=None,
                   help='Sucursal emisora (nc.sucursal_id).')
    p.add_argument('--desde', type=str, default=None, help='Fecha desde YYYY-MM-DD.')
    p.add_argument('--hasta', type=str, default=None, help='Fecha hasta YYYY-MM-DD.')
    p.add_argument('--tipo-padre', dest='tipo_padre', type=str, default=None,
                   help='Acota por tipo del DTE padre (ej. GUIA, FACTURA ELECTRONICA).')
    p.add_argument('--motivo', type=str, default='',
                   help='Motivo textual (para movimientos y tag de referencias).')
    p.add_argument('--usuario', type=str, default='',
                   help='Username a registrar como responsable (default: env USERNAME).')
    p.add_argument('--export', choices=['csv'], default=None,
                   help='Exportar dry-run a formato indicado.')
    p.add_argument('--out', type=str, default=None,
                   help='Archivo de salida para --export.')
    args = p.parse_args()

    if args.apply:
        return cmd_apply(args)
    return cmd_dryrun(args)


if __name__ == '__main__':
    sys.exit(main())
