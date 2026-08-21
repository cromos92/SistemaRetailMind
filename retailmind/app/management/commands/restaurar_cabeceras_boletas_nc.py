# -*- coding: utf-8 -*-
"""
Backfill P0-5 (AUDITORIA_REPORTES_2026-08, sección 9): restaura las cabeceras de
boletas/facturas de VENTA que `anular_factura_dte` (NC por línea) reescribió entre
el 23-abr y el 12-ago-2026 (commit 321e4531):

- Devolución TOTAL  → la cabecera quedó en `monto_con_iva = 0` con todas las
  líneas `activo=False` (226 casos medidos).
- Devolución PARCIAL → la cabecera quedó en Σ(líneas activas) × 1.19 (el precio
  IVA-inclusivo se trató como neto; 7 casos medidos).

El fix de código ya está aplicado (la cabecera del original no se reescribe más);
este command corrige el daño histórico: 234 documentos / $11.981.499 de déficit
medidos en prod el 20-ago (232 BOLETA ELECTRONICA + 2 FACTURA ELECTRONICA).

FUENTE DE VERDAD: `Σ Dte_Productos.monto_item` de TODAS las líneas del documento
(activas + inactivas) — es lo que llevó el TXT al SII y la NC nunca lo tocó.
Cross-check obligatorio contra `Σ Dte_Detalle_Pago.monto` (±$5): si no cuadra,
el documento va a REVISION_MANUAL y no se toca. El cruce con Ticket.total
(referencias TICKET-<correlativo>) es informativo.

ESCRITURA (solo con --aplicar; SOLO cabecera, jamás `Dte_Productos` — dp.stock /
dp.activo son el tope anti-doble-NC):
- `monto_con_iva` = Σ monto_item
- `monto_neto`    = (total / 1.19) redondeado HALF-UP vía Decimal.quantize
  (REGLA DURA del proyecto: nunca int() ni round() de float sobre montos DTE)
- `unidades_productos` = cantidades originales del documento: derivadas de
  Σ(monto_item / precio) cuando todas las líneas lo permiten (misma fuente
  intacta que los montos), con validación/fallback vía Σ stock actual de líneas
  + Σ unidades acreditadas por sus NC por-línea (Dte_Productos de las NC
  hijas). Si ninguna fuente sirve, las unidades se dejan SIN tocar y se reporta.

SEGURIDAD:
- DRY-RUN POR DEFECTO: sin --aplicar no escribe nada en la BD (solo puede dejar
  un CSV *_preview.csv en disco con lo que haría).
- Con --aplicar: snapshot CSV `_restauracion_cabeceras_<ts>.csv` ANTES de
  escribir + transacción atómica + verificación post-escritura.
- --revertir <csv> restaura los valores old_*; se niega si el valor actual no
  es el new_* esperado (usar --forzar para pisar igual).

Uso:
    python manage.py restaurar_cabeceras_boletas_nc                    # dry-run boletas
    python manage.py restaurar_cabeceras_boletas_nc --tipo todos       # + facturas
    python manage.py restaurar_cabeceras_boletas_nc --tipo todos --aplicar
    python manage.py restaurar_cabeceras_boletas_nc --ids 2193698,2191857 --aplicar
    python manage.py restaurar_cabeceras_boletas_nc --revertir _restauracion_cabeceras_20260820_120000.csv
"""
import collections
import csv
import datetime
import os
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from app.models import Dte, Dte_Productos, Ticket

TOL = 5  # pesos de tolerancia en todos los cruces
IVA = Decimal('1.19')
ESTADOS_NC_VALIDOS = ('EMITIDO', 'ACEPTADO')
TIPOS_OPCION = {
    'boleta': ['BOLETA ELECTRONICA'],
    'factura': ['FACTURA ELECTRONICA'],
    'todos': ['BOLETA ELECTRONICA', 'FACTURA ELECTRONICA'],
}
CSV_CAMPOS = [
    'dte_id', 'folio', 'tipo', 'sucursal_id', 'fecha_emision',
    'old_neto', 'old_con_iva', 'old_unidades',
    'new_neto', 'new_con_iva', 'new_unidades', 'unidades_tocadas',
    'fuente', 'cross_check', 'ncs_vinculadas', 'deficit', 'nota_unidades',
]


def _neto_half_up(total_con_iva):
    """total/1.19 con redondeo HALF-UP vía Decimal (NUNCA int()/round() de float)."""
    return (Decimal(total_con_iva) / IVA).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = ('Restaura cabeceras de boletas/facturas rotas por anular_factura_dte '
            '(NC por línea). Dry-run por defecto; --aplicar para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Escribe de verdad (sin esto es dry-run: 0 escrituras en BD)')
        parser.add_argument('--desde', default='2026-01-01',
                            help='fecha_emision mínima (YYYY-MM-DD, default 2026-01-01)')
        parser.add_argument('--hasta', default=None,
                            help='fecha_emision máxima (YYYY-MM-DD, default: sin tope)')
        parser.add_argument('--ids', default=None,
                            help='Lista de ids de Dte separados por coma (ignora --desde/--hasta)')
        parser.add_argument('--tipo', choices=sorted(TIPOS_OPCION), default='boleta',
                            help='boleta (default) | factura | todos (incluye FACTURA ELECTRONICA)')
        parser.add_argument('--snapshot-dir', default='.',
                            help='Carpeta para el CSV de snapshot/preview (default: actual)')
        parser.add_argument('--revertir', default=None, metavar='SNAPSHOT.CSV',
                            help='Revierte una aplicación anterior usando su CSV de snapshot')
        parser.add_argument('--forzar', action='store_true',
                            help='Con --revertir: restaura aunque los valores hayan cambiado después')

    # ------------------------------------------------------------------ #

    def handle(self, *args, **opts):
        if opts['revertir']:
            return self._revertir(opts['revertir'], opts['forzar'])

        aplicar = opts['aplicar']
        tipos = TIPOS_OPCION[opts['tipo']]
        try:
            desde = datetime.date.fromisoformat(opts['desde'])
            hasta = datetime.date.fromisoformat(opts['hasta']) if opts['hasta'] else None
        except ValueError as e:
            raise CommandError(f'Fecha inválida: {e}')
        ids = None
        if opts['ids']:
            try:
                ids = [int(x) for x in str(opts['ids']).split(',') if x.strip()]
            except ValueError:
                raise CommandError('--ids debe ser una lista de enteros separados por coma')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{'APLICANDO' if aplicar else 'DRY-RUN (0 escrituras en BD)'} — "
            f"restauración de cabeceras rotas por NC por línea "
            f"(tipos: {', '.join(tipos)})"
        ))

        candidatos, manuales = self._analizar(tipos, desde, hasta, ids)
        self._imprimir_resumen(candidatos, manuales)

        ts = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        if not aplicar:
            if candidatos:
                ruta = os.path.join(opts['snapshot_dir'],
                                    f'_restauracion_cabeceras_{ts}_preview.csv')
                self._escribir_csv(ruta, candidatos)
                self.stdout.write(f'\nPreview CSV (lo que ESCRIBIRÍA): {ruta}')
            filtros = f" --tipo {opts['tipo']}" if opts['tipo'] != 'boleta' else ''
            if ids:
                filtros += f" --ids {opts['ids']}"
            self.stdout.write(self.style.WARNING(
                '\nDry-run. Para ejecutar de verdad:\n'
                f'    python manage.py restaurar_cabeceras_boletas_nc{filtros} --aplicar'
            ))
            return

        if not candidatos:
            self.stdout.write(self.style.SUCCESS('\nNada que aplicar: 0 candidatos.'))
            return

        # ------------------------- APLICAR ------------------------------ #
        ruta = os.path.join(opts['snapshot_dir'], f'_restauracion_cabeceras_{ts}.csv')
        self._escribir_csv(ruta, candidatos)  # snapshot ANTES de escribir

        con_unidades = sum(1 for c in candidatos if c['unidades_tocadas'])
        with transaction.atomic():
            actuales = {
                d.id: d for d in Dte.objects.select_for_update()
                .filter(id__in=[c['dte_id'] for c in candidatos])
            }
            a_guardar = []
            for c in candidatos:
                d = actuales.get(c['dte_id'])
                if d is None:
                    raise CommandError(
                        f"Dte {c['dte_id']} desapareció entre el análisis y la escritura. "
                        f"Nada se aplicó (transacción abortada).")
                if d.monto_con_iva != c['old_con_iva']:
                    raise CommandError(
                        f"Dte {c['dte_id']} (folio {c['folio']}) cambió de monto_con_iva "
                        f"({c['old_con_iva']} → {d.monto_con_iva}) entre el análisis y la "
                        f"escritura. Nada se aplicó (transacción abortada).")
                d.monto_con_iva = c['new_con_iva']
                d.monto_neto = c['new_neto']
                if c['unidades_tocadas']:
                    d.unidades_productos = c['new_unidades']
                a_guardar.append(d)
            Dte.objects.bulk_update(
                a_guardar, ['monto_con_iva', 'monto_neto', 'unidades_productos'],
                batch_size=200,
            )

        # Verificación: ninguno de los aplicados debe seguir calificando
        restantes = self._queryset_base(tipos, desde, hasta,
                                        [c['dte_id'] for c in candidatos]).count()
        marca = (self.style.SUCCESS('OK: 0 siguen con déficit') if restantes == 0
                 else self.style.ERROR(f'ATENCIÓN: {restantes} siguen con déficit'))
        self.stdout.write(f'\nVerificación post-escritura: {marca}')
        self.stdout.write(self.style.SUCCESS(
            f"\nListo. {len(candidatos)} cabeceras restauradas "
            f"({con_unidades} con unidades_productos; "
            f"{len(candidatos) - con_unidades} con unidades SIN tocar).\n"
            f"Snapshot de reversión: {ruta}\n"
            f"Para revertir: python manage.py restaurar_cabeceras_boletas_nc --revertir {ruta}"
        ))

    # ------------------------------------------------------------------ #
    # Selección y análisis (todo SELECT, cero escrituras)
    # ------------------------------------------------------------------ #

    def _queryset_base(self, tipos, desde, hasta, ids):
        qs = Dte.objects.filter(
            tipo_documento__in=tipos,
            es_nota_credito=False,
            descartado=False,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        )
        if ids:
            qs = qs.filter(id__in=ids)
        else:
            qs = qs.filter(fecha_emision__gte=desde)
            if hasta:
                qs = qs.filter(fecha_emision__lte=hasta)
        return (qs.annotate(pagos_sum=Sum('dte_asociado__monto'))
                  .filter(pagos_sum__gt=F('monto_con_iva') + TOL))

    def _analizar(self, tipos, desde, hasta, ids):
        afectados = list(self._queryset_base(tipos, desde, hasta, ids).values(
            'id', 'numero_documento', 'tipo_documento', 'fecha_emision',
            'sucursal_id', 'monto_con_iva', 'monto_neto', 'unidades_productos',
            'referencias', 'pagos_sum',
        ))
        ids_af = [a['id'] for a in afectados]

        # Líneas del documento (activas + inactivas): monto_item intacto = fuente de verdad
        lineas = collections.defaultdict(list)
        for r in Dte_Productos.objects.filter(dte_id__in=ids_af).values(
                'dte_id', 'stock', 'activo', 'monto_item', 'precio', 'descuento_monto'):
            lineas[r['dte_id']].append(r)

        # NC hijas
        ncs = collections.defaultdict(list)
        for n in Dte.objects.filter(documento_afectado_id__in=ids_af, es_nota_credito=True).values(
                'id', 'documento_afectado_id', 'estado_dte', 'descartado',
                'redujo_lineas_documento', 'numero_documento'):
            ncs[n['documento_afectado_id']].append(n)
        nc_ids_redujo = [n['id'] for lst in ncs.values() for n in lst
                         if n['redujo_lineas_documento']]
        nc_unidades = {r['dte_id']: int(r['u'] or 0)
                       for r in Dte_Productos.objects.filter(dte_id__in=nc_ids_redujo)
                       .values('dte_id').annotate(u=Sum('stock'))}

        # Cross-check informativo contra el ticket (referencias TICKET-<correlativo>)
        corr_por_dte = {}
        for a in afectados:
            ref = a['referencias'] or ''
            if ref.startswith('TICKET-'):
                try:
                    corr_por_dte[a['id']] = (int(ref.split('TICKET-')[1].split()[0]),
                                             a['sucursal_id'])
                except (ValueError, IndexError):
                    pass
        tickets = {}
        if corr_por_dte:
            corrs = {c for c, _s in corr_por_dte.values()}
            for t in Ticket.objects.filter(correlativo__in=corrs).values(
                    'correlativo', 'sucursal_id', 'total'):
                tickets[(t['correlativo'], t['sucursal_id'])] = int(t['total'] or 0)

        candidatos, manuales = [], []
        for a in sorted(afectados, key=lambda x: (x['fecha_emision'], x['id'])):
            did = a['id']
            pagos = int(a['pagos_sum'] or 0)
            cab = a['monto_con_iva'] or Decimal(0)
            deficit = pagos - int(cab)
            lns = lineas.get(did, [])
            total_mi = sum(int(l['monto_item'] or 0) for l in lns)
            ticket_total = tickets.get(corr_por_dte.get(did))
            base = dict(
                dte_id=did, folio=a['numero_documento'], tipo=a['tipo_documento'],
                sucursal_id=a['sucursal_id'], fecha_emision=a['fecha_emision'],
                old_con_iva=cab, old_neto=a['monto_neto'] or Decimal(0),
                old_unidades=int(a['unidades_productos'] or 0),
                pagos=pagos, total_mi=total_mi, ticket_total=ticket_total,
                deficit=deficit,
            )

            # 1) NC vinculada en EMITIDO/ACEPTADO — obligatoria
            ncs_doc = ncs.get(did, [])
            ncs_ok = [n for n in ncs_doc if n['estado_dte'] in ESTADOS_NC_VALIDOS]
            if not ncs_ok:
                manuales.append({**base, 'razon': 'SIN_NC_VINCULADA'})
                continue

            # 2) Fuente de verdad utilizable
            if not lns or total_mi <= 0:
                manuales.append({**base, 'razon': 'SIN_MONTO_ITEM'})
                continue

            # 3) Cross-check obligatorio: Σ monto_item vs Σ pagos (±$5)
            if abs(total_mi - pagos) > TOL:
                manuales.append({**base, 'razon': 'FUENTE_INCONSISTENTE'})
                continue

            cross = 'PAGOS'
            if ticket_total is not None:
                if abs(total_mi - ticket_total) <= TOL:
                    cross = 'PAGOS+TICKET'
                else:
                    cross = f'PAGOS (ticket difiere ${ticket_total - total_mi:+,})'

            new_con_iva = Decimal(total_mi)
            new_neto = _neto_half_up(new_con_iva)
            new_unidades, tocadas, nota_uds = self._reconstruir_unidades(
                lns, ncs_doc, nc_unidades)

            candidatos.append({
                **base,
                'new_con_iva': new_con_iva,
                'new_neto': new_neto,
                'new_unidades': new_unidades if tocadas else base['old_unidades'],
                'unidades_tocadas': tocadas,
                'nota_unidades': nota_uds,
                'fuente': 'SUM_MONTO_ITEM',
                'cross_check': cross,
                'ncs_vinculadas': ';'.join(str(n['id']) for n in ncs_ok),
            })
        return candidatos, manuales

    def _reconstruir_unidades(self, lns, ncs_doc, nc_unidades):
        """
        Unidades originales del documento tal como se emitió al SII.

        Fuente primaria: Σ(monto_item / precio) por línea — la MISMA fuente de
        verdad intacta que se usa para los montos (monto_item = PrcItem×QtyItem
        − DescuentoMonto), utilizable solo cuando TODAS las líneas son limpias
        (precio > 0, sin descuento, división exacta). En prod la reconstrucción
        vía NC hijas falla en ~75% de los casos porque las NC no siempre llevan
        las unidades en sus líneas (ej. línea única "Devolución parcial").

        Fallback: Σ stock actual de líneas + Σ unidades acreditadas por las NC
        por-línea (dp.stock de las NC hijas con redujo_lineas_documento). Se usa
        como validación cruzada cuando la derivación por precio existe.

        Si ninguna fuente sirve → (None, False, motivo): unidades SIN tocar.
        """
        stock_actual = sum(int(l['stock'] or 0) for l in lns)
        acreditadas = sum(nc_unidades.get(n['id'], 0) for n in ncs_doc
                          if n['redujo_lineas_documento'] and not n['descartado'])
        acreditadas_incl = sum(nc_unidades.get(n['id'], 0) for n in ncs_doc
                               if n['redujo_lineas_documento'])
        rec_nc = stock_actual + acreditadas

        # Derivación desde monto_item/precio (solo si TODAS las líneas son
        # "limpias": precio>0, sin descuento, división exacta)
        q_total, computable = 0, bool(lns)
        for l in lns:
            precio = int(l['precio'] or 0)
            mi = int(l['monto_item'] or 0)
            if precio <= 0 or (l['descuento_monto'] or 0) != 0 or mi <= 0 or mi % precio != 0:
                computable = False
                break
            q_total += mi // precio

        if computable:
            if rec_nc == q_total:
                return q_total, True, ''
            if stock_actual + acreditadas_incl == q_total:
                return q_total, True, 'valida solo incluyendo NC descartadas'
            return q_total, True, f'derivado de monto_item/precio (stock+NC daba {rec_nc})'
        if rec_nc > 0:
            return rec_nc, True, 'desde stock+NC, sin validación por precio'
        return None, False, 'no reconstruible (sin derivación por precio y stock+NC<=0)'

    # ------------------------------------------------------------------ #
    # Salida
    # ------------------------------------------------------------------ #

    def _imprimir_resumen(self, candidatos, manuales):
        por_mes = collections.defaultdict(lambda: dict(n=0, cab0=0, deficit=0))
        uds_sin_tocar, uds_con_nota = [], []
        for c in candidatos:
            m = c['fecha_emision'].strftime('%Y-%m')
            d = por_mes[m]
            d['n'] += 1
            if int(c['old_con_iva']) == 0:
                d['cab0'] += 1
            d['deficit'] += c['deficit']
            if not c['unidades_tocadas']:
                uds_sin_tocar.append(c)
            elif c['nota_unidades']:
                uds_con_nota.append(c)

        self.stdout.write('\n=== CANDIDATOS por mes (n | cabecera==0 | déficit a restaurar) ===')
        for m in sorted(por_mes):
            d = por_mes[m]
            self.stdout.write(
                f"  {m}  n={d['n']:<4} cab0={d['cab0']:<4} deficit=${d['deficit']:>13,}")
        total_def = sum(c['deficit'] for c in candidatos)
        por_tipo = collections.Counter(c['tipo'] for c in candidatos)
        self.stdout.write(
            f"  TOTAL: {len(candidatos)} candidatos "
            f"({', '.join(f'{v} {k}' for k, v in sorted(por_tipo.items()))}) "
            f"| déficit ${total_def:,}")

        if uds_con_nota:
            self.stdout.write(
                f'\n  {len(uds_con_nota)} candidatos con unidades reconstruidas '
                f'con nota (detalle en el CSV, columna nota_unidades); ej:')
            for c in uds_con_nota[:8]:
                self.stdout.write(
                    f"    dte={c['dte_id']} folio={c['folio']} "
                    f"uds {c['old_unidades']}→{c['new_unidades']} ({c['nota_unidades']})")

        if uds_sin_tocar:
            self.stdout.write(self.style.WARNING(
                f'\n  {len(uds_sin_tocar)} candidatos con unidades_productos NO '
                f'reconstruibles (se corrige solo el monto):'))
            for c in uds_sin_tocar:
                self.stdout.write(
                    f"    dte={c['dte_id']} folio={c['folio']} "
                    f"({c['nota_unidades']})")

        self.stdout.write(f'\n=== REVISION_MANUAL: {len(manuales)} documentos '
                          f'(NO se tocan jamás) ===')
        for r in manuales:
            tk = r['ticket_total'] if r['ticket_total'] is not None else '-'
            self.stdout.write(
                f"  dte={r['dte_id']} folio={r['folio']} {r['tipo']} "
                f"fecha={r['fecha_emision']} cab=${int(r['old_con_iva']):,} "
                f"pagos=${r['pagos']:,} Σmonto_item=${r['total_mi']:,} "
                f"ticket={tk} → {r['razon']}")

    def _escribir_csv(self, ruta, candidatos):
        with open(ruta, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=CSV_CAMPOS)
            w.writeheader()
            for c in candidatos:
                w.writerow({
                    'dte_id': c['dte_id'], 'folio': c['folio'], 'tipo': c['tipo'],
                    'sucursal_id': c['sucursal_id'],
                    'fecha_emision': c['fecha_emision'].isoformat(),
                    'old_neto': c['old_neto'], 'old_con_iva': c['old_con_iva'],
                    'old_unidades': c['old_unidades'],
                    'new_neto': c['new_neto'], 'new_con_iva': c['new_con_iva'],
                    'new_unidades': c['new_unidades'],
                    'unidades_tocadas': 1 if c['unidades_tocadas'] else 0,
                    'fuente': c['fuente'], 'cross_check': c['cross_check'],
                    'ncs_vinculadas': c['ncs_vinculadas'], 'deficit': c['deficit'],
                    'nota_unidades': c['nota_unidades'],
                })

    # ------------------------------------------------------------------ #
    # Reversión
    # ------------------------------------------------------------------ #

    def _revertir(self, ruta, forzar):
        if not os.path.exists(ruta):
            raise CommandError(f'No existe el snapshot {ruta}')
        if ruta.endswith('_preview.csv'):
            raise CommandError('Ese CSV es un PREVIEW de dry-run, no un snapshot aplicado.')
        with open(ruta, encoding='utf-8', newline='') as f:
            filas = list(csv.DictReader(f))
        if not filas:
            raise CommandError(f'Snapshot vacío: {ruta}')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Revirtiendo restauración de {len(filas)} cabeceras ({ruta})'))

        with transaction.atomic():
            actuales = {
                d.id: d for d in Dte.objects.select_for_update()
                .filter(id__in=[int(r['dte_id']) for r in filas])
            }
            discrepancias, a_restaurar = [], []
            for r in filas:
                did = int(r['dte_id'])
                d = actuales.get(did)
                if d is None:
                    discrepancias.append(f'dte={did}: ya no existe')
                    continue
                esperado_cab = Decimal(r['new_con_iva'])
                esperado_neto = Decimal(r['new_neto'])
                uds_tocadas = r['unidades_tocadas'] == '1'
                cambio = (d.monto_con_iva != esperado_cab
                          or d.monto_neto != esperado_neto
                          or (uds_tocadas
                              and d.unidades_productos != int(r['new_unidades'])))
                if cambio and not forzar:
                    discrepancias.append(
                        f"dte={did} folio={r['folio']}: valor actual "
                        f"({d.monto_con_iva}/{d.monto_neto}/{d.unidades_productos}) "
                        f"!= aplicado ({esperado_cab}/{esperado_neto}/{r['new_unidades']})")
                    continue
                d.monto_con_iva = Decimal(r['old_con_iva'])
                d.monto_neto = Decimal(r['old_neto'])
                if uds_tocadas:
                    d.unidades_productos = int(r['old_unidades'])
                a_restaurar.append(d)

            if discrepancias:
                detalle = '; '.join(discrepancias[:5])
                if not forzar:
                    raise CommandError(
                        f'{len(discrepancias)} documentos cambiaron después de la '
                        f'aplicación (ej: {detalle}). NADA se revirtió. '
                        f'Usa --forzar para restaurar igual (pisa el valor actual).')
                self.stdout.write(self.style.WARNING(
                    f'--forzar: {len(discrepancias)} filas omitidas por no existir '
                    f'({detalle})'))

            Dte.objects.bulk_update(
                a_restaurar, ['monto_con_iva', 'monto_neto', 'unidades_productos'],
                batch_size=200,
            )

        self.stdout.write(self.style.SUCCESS(
            f'Revertido: {len(a_restaurar)} cabeceras restauradas a sus valores previos.'))
