"""Script de mantenimiento: revertir el `estado_dte` de DTEs marcados como
ANULADO cuya NC asociada NO es OCULTA.

Contexto:
  Hasta el 30-04-2026 `anular_factura_dte` marcaba el DTE original como
  `estado_dte='ANULADO'` siempre que la NC cubriera el monto total,
  independiente de la modalidad de la NC (DEVOLUCION / INFORMATIVA / OCULTA).

  Eso descalzaba cuadratura:
    * DEVOLUCION: doble descuento (DTE excluido del día original + NC restando del día de la NC).
    * INFORMATIVA: el día original perdía la venta sin compensación
      (la NC no resta, solo es informativa).
    * OCULTA: comportamiento correcto (todo invisible).

  Tras el fix, las nuevas NC DEVOLUCION/INFORMATIVA NO marcan el DTE como
  ANULADO. Este script repara los DTEs históricos que quedaron descalzados.

USO:
    # Dry-run (default): NO modifica nada, solo lista lo que cambiaría.
    python reparar_dtes_anulados_no_ocultos.py

    # Aplicar cambios (requiere confirmación interactiva).
    python reparar_dtes_anulados_no_ocultos.py --apply

    # Filtrar por sucursal o por rango de fechas.
    python reparar_dtes_anulados_no_ocultos.py --sucursal-id 4 \\
        --fecha-desde 2026-01-01 --fecha-hasta 2026-04-30

CRITERIO DE REPARACIÓN:
  Un DTE ANULADO es candidato si TIENE al menos una NC asociada
  (vía Dte.documento_afectado) Y NINGUNA de esas NCs está descartada
  (es decir, ninguna es OCULTA). En ese caso revertimos a 'ACEPTADO'.

  Si todas las NCs asociadas están descartadas (OCULTAs), dejamos el
  DTE como ANULADO (es el comportamiento esperado de OCULTA: borra todo
  el rastro).

  Los DTEs ANULADOS sin NCs asociadas NO se tocan (su anulación viene
  de otro flujo, por ejemplo cancelación SII).

EFECTOS:
  Cuadratura del día de emisión del DTE volverá a contar la venta.
  La NC asociada sigue funcionando igual:
    - DEVOLUCION: resta del día de la NC (sin cambio).
    - INFORMATIVA: solo informativa, no resta (sin cambio).
  El balance del mes queda correcto.

CONSIDERACIONES:
  Si vos cerraste arqueos (ArqueoCaja) en el ínterin con la lógica vieja,
  el snapshot de los teóricos quedó cerrado con valores menores. Este
  script NO reabre arqueos; solo repara el estado del DTE para que
  cuadratura PUESTA HOY refleje correctamente.

  Si necesitás recalcular teóricos de arqueos cerrados, eso es otro
  script y otro nivel de cuidado.
"""
import argparse
import os
import sys
from datetime import date, datetime

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db import transaction  # noqa: E402
from django.db.models import Count, Q  # noqa: E402

from app.models import Dte  # noqa: E402


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true',
                   help='Aplica los cambios (default: dry-run).')
    p.add_argument('--sucursal-id', type=int, default=None,
                   help='Filtrar por sucursal_id.')
    p.add_argument('--fecha-desde', type=str, default=None,
                   help='Fecha desde (YYYY-MM-DD).')
    p.add_argument('--fecha-hasta', type=str, default=None,
                   help='Fecha hasta (YYYY-MM-DD).')
    p.add_argument('--limite', type=int, default=None,
                   help='Procesar como máximo N DTEs (útil para testing).')
    p.add_argument('--estado-destino', type=str, default='ACEPTADO',
                   choices=['EMITIDO', 'ACEPTADO'],
                   help='Estado al que revertir (default: ACEPTADO).')
    p.add_argument('--no-confirm', action='store_true',
                   help='No pedir confirmación interactiva al aplicar.')
    return p.parse_args()


def main():
    args = _parse_args()

    qs = Dte.objects.filter(
        estado_dte='ANULADO',
        es_nota_credito=False,
    ).annotate(
        nc_total=Count(
            'notas_credito_relacionadas',
            filter=Q(notas_credito_relacionadas__es_nota_credito=True),
        ),
        nc_no_ocultas=Count(
            'notas_credito_relacionadas',
            filter=Q(notas_credito_relacionadas__es_nota_credito=True,
                     notas_credito_relacionadas__descartado=False),
        ),
    ).filter(
        nc_total__gte=1,           # tiene al menos una NC asociada
        nc_no_ocultas__gte=1,      # al menos una NC NO es OCULTA
    )

    if args.sucursal_id:
        qs = qs.filter(sucursal_id=args.sucursal_id)
    if args.fecha_desde:
        try:
            fd = datetime.strptime(args.fecha_desde, '%Y-%m-%d').date()
        except ValueError:
            print(f'ERROR: fecha-desde inválida: {args.fecha_desde}')
            sys.exit(1)
        qs = qs.filter(fecha_emision__gte=fd)
    if args.fecha_hasta:
        try:
            fh = datetime.strptime(args.fecha_hasta, '%Y-%m-%d').date()
        except ValueError:
            print(f'ERROR: fecha-hasta inválida: {args.fecha_hasta}')
            sys.exit(1)
        qs = qs.filter(fecha_emision__lte=fh)

    qs = qs.select_related('sucursal').order_by('fecha_emision', 'numero_documento')
    if args.limite:
        qs = qs[:args.limite]

    candidatos = list(qs)
    total = len(candidatos)
    monto_total = sum(int(d.monto_con_iva or 0) for d in candidatos)

    print('=' * 80)
    print('REPARACIÓN DE DTEs ANULADOS CON NC NO-OCULTA')
    print('=' * 80)
    print(f'Modo: {"APLICAR" if args.apply else "DRY-RUN (no modifica nada)"}')
    print(f'Estado destino: {args.estado_destino}')
    print(f'Filtros: sucursal={args.sucursal_id or "TODAS"}, '
          f'desde={args.fecha_desde or "INICIO"}, hasta={args.fecha_hasta or "HOY"}')
    print(f'Candidatos encontrados: {total}')
    print(f'Monto total a "recuperar" en cuadratura: ${monto_total:,}')
    print('-' * 80)

    if total == 0:
        print('No hay DTEs para reparar. Salida limpia.')
        return

    print(f'{"#":>4}  {"folio":>10}  {"fecha":>12}  {"sucursal":<25}  '
          f'{"tipo":<22}  {"monto":>12}  ncs')
    for i, d in enumerate(candidatos[:50], 1):
        suc = d.sucursal.alias if d.sucursal else '?'
        ncs_info = list(
            d.notas_credito_relacionadas.filter(es_nota_credito=True)
             .values('numero_documento', 'tipo_transaccion', 'descartado', 'fecha_emision')
        )
        ncs_str = '; '.join(
            f"#{n['numero_documento']}({n['tipo_transaccion']},{'OCULTA' if n['descartado'] else 'visible'},{n['fecha_emision']})"
            for n in ncs_info
        )
        print(f'{i:>4}  {d.numero_documento:>10}  {str(d.fecha_emision):>12}  '
              f'{suc:<25}  {d.tipo_documento:<22}  ${int(d.monto_con_iva or 0):>10,}  '
              f'{ncs_str}')

    if total > 50:
        print(f'... y {total - 50} DTEs más (no listados).')

    print('-' * 80)

    if not args.apply:
        print('\nMODO DRY-RUN: NO se modificaron datos.')
        print('Para aplicar: python reparar_dtes_anulados_no_ocultos.py --apply')
        return

    if not args.no_confirm:
        confirma = input(
            f'\nVas a revertir {total} DTEs de ANULADO a {args.estado_destino} '
            f'(afecta cuadratura de ${monto_total:,}).\n'
            f'Confirmar [s/N]: '
        )
        if confirma.strip().lower() not in ('s', 'si', 'sí', 'y', 'yes'):
            print('Cancelado.')
            return

    actualizados = 0
    with transaction.atomic():
        for d in candidatos:
            d.estado_dte = args.estado_destino
            d.save(update_fields=['estado_dte'])
            actualizados += 1
            if actualizados % 100 == 0:
                print(f'  procesados: {actualizados}/{total}')

    print(f'\nLISTO: {actualizados} DTEs reparados '
          f'(monto total: ${monto_total:,}).')
    print('Cuadratura ya refleja la venta histórica de esos DTEs.')


if __name__ == '__main__':
    main()
