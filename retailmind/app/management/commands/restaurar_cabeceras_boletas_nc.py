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

VÍA MANUAL EXPLÍCITA (--fuente, cierre de los REVISION_MANUAL):
Los documentos que el análisis automático rechaza (SIN_NC_VINCULADA,
FUENTE_INCONSISTENTE, SIN_MONTO_ITEM) solo se pueden cerrar declarando A MANO
de dónde sale el monto correcto, documento por documento:

    --ids <id[,id...]> --fuente {monto_item,pagos,ticket}

- `--fuente` SIN `--ids` es un error (nunca masivo, nunca por defecto).
- Con `--fuente`, los ids indicados se resuelven con ESA fuente y se saltan las
  compuertas del análisis automático — pero se mantienen las de integridad:
  el documento debe seguir con déficit real (Σ pagos > cabecera), la fuente
  debe traer un monto > 0 y ESTRICTAMENTE MAYOR que la cabecera actual.
- COMPUERTA DE DISCREPANCIA (--acepto-discrepancia): elegir la fuente
  equivocada NO se acepta en silencio. Antes de escribir se comparan las TRES
  fuentes del documento (Σ monto_item / Σ pagos / Ticket.total, las que
  existan). Si alguna de las OTRAS no coincide con la elegida dentro de ±$5,
  el documento se BLOQUEA, se imprimen las tres lado a lado con el Δ de cada
  una y el command exige repetir la corrida con `--acepto-discrepancia`.
  Excepción única: Σ monto_item se considera coincidente si cuadra tras restar
  el `descuento` global del documento (Σ monto_item − descuento == elegido).
  Cuando se acepta, el bloque de las tres fuentes se imprime igual (marcado
  DISCREPANCIA ACEPTADA) y queda en la columna `discrepancia` del CSV.
- AVISO DE DÉFICIT RESIDUAL (pre-escritura): si el monto elegido queda por
  debajo de Σ pagos, se avisa ANTES de escribir cuánto déficit deja y que el
  documento volverá a aparecer en la verificación post-escritura.
- AVISO DE EFECTO COLATERAL DTE↔TICKET: este command NUNCA toca `Ticket`. Si
  hoy la cabecera coincide con `Ticket.total` y el monto elegido no, se avisa
  que la escritura CREA un descuadre DTE↔ticket (caso real: dte 2178247, hoy
  cab $173.950 == ticket #334; con --fuente pagos queda $188.940 vs ticket
  $173.950). También se avisa cuando la escritura RESUELVE un descuadre.
- `unidades_productos` solo se reconstruye si la fuente elegida queda
  CORROBORADA por las líneas (Σ monto_item, o Σ monto_item − descuento global);
  si no, las unidades se dejan intactas y se reporta. En los 4 legacy migrados
  (836011/900643/901236/1166683) NO son reconstruibles: quedan en 0 y cualquier
  indicador monto/unidad seguirá mintiendo en ellos.
- Snapshot, verificación post-escritura y `--revertir` funcionan igual; la
  columna `fuente` del CSV queda como `MANUAL_<FUENTE>`.

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
    python manage.py restaurar_cabeceras_boletas_nc --ids 836011 --fuente pagos
    python manage.py restaurar_cabeceras_boletas_nc --ids 836011 --fuente pagos --acepto-discrepancia
    python manage.py restaurar_cabeceras_boletas_nc --ids 836011 --fuente pagos --acepto-discrepancia --aplicar
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
# Fuentes admitidas por la vía manual explícita (--fuente). El valor es la
# etiqueta que queda escrita en la columna `fuente` del snapshot CSV.
FUENTES_MANUALES = {
    'monto_item': 'MANUAL_SUM_MONTO_ITEM',
    'pagos': 'MANUAL_PAGOS',
    'ticket': 'MANUAL_TICKET',
}
# OJO: las columnas nuevas van al FINAL para que los snapshots viejos (sin
# ellas) sigan siendo revertibles — `_revertir` solo lee dte_id/folio/old_*/new_*.
CSV_CAMPOS = [
    'dte_id', 'folio', 'tipo', 'sucursal_id', 'fecha_emision',
    'old_neto', 'old_con_iva', 'old_unidades',
    'new_neto', 'new_con_iva', 'new_unidades', 'unidades_tocadas',
    'fuente', 'cross_check', 'ncs_vinculadas', 'deficit', 'nota_unidades',
    'discrepancia', 'nota_ticket',
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
        parser.add_argument('--fuente', choices=sorted(FUENTES_MANUALES), default=None,
                            help=('VÍA MANUAL: monto de restauración declarado a mano para los '
                                  'documentos de --ids (obligatorio junto con --ids). '
                                  'monto_item = Σ Dte_Productos.monto_item | '
                                  'pagos = Σ Dte_Detalle_Pago.monto | ticket = Ticket.total'))
        parser.add_argument('--acepto-discrepancia', action='store_true',
                            help=('VÍA MANUAL: reconoce explícitamente que la fuente elegida '
                                  'NO coincide con las otras fuentes del documento. Sin esta '
                                  'bandera, un documento con fuentes en desacuerdo se BLOQUEA '
                                  '(nunca se escribe en silencio).'))
        parser.add_argument('--snapshot-dir', default='.',
                            help='Carpeta para el CSV de snapshot/preview (default: actual)')
        parser.add_argument('--revertir', default=None, metavar='SNAPSHOT.CSV',
                            help='Revierte una aplicación anterior usando su CSV de snapshot')
        parser.add_argument('--forzar', action='store_true',
                            help='Con --revertir: restaura aunque los valores hayan cambiado después')

    # ------------------------------------------------------------------ #

    def handle(self, *args, **opts):
        if opts['revertir']:
            if opts.get('fuente'):
                raise CommandError('--fuente no aplica a --revertir (la reversión usa el CSV).')
            if opts.get('acepto_discrepancia'):
                raise CommandError(
                    '--acepto-discrepancia no aplica a --revertir (la reversión usa el CSV).')
            return self._revertir(opts['revertir'], opts['forzar'])

        aplicar = opts['aplicar']
        fuente = opts.get('fuente')
        acepto_disc = bool(opts.get('acepto_discrepancia'))
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

        # VÍA MANUAL: jamás por defecto, jamás masiva.
        if fuente and not ids:
            raise CommandError(
                '--fuente exige --ids: la restauración manual se decide documento por '
                'documento.\n'
                '    Ej: python manage.py restaurar_cabeceras_boletas_nc '
                '--ids 836011 --fuente pagos'
            )
        if acepto_disc and not fuente:
            raise CommandError(
                '--acepto-discrepancia solo aplica a la vía manual: úsalo junto con '
                '--ids <id> --fuente <fuente>. La vía automática ya exige que Σ '
                'monto_item y Σ pagos cuadren dentro de ±$5 y no admite excepciones.'
            )

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{'APLICANDO' if aplicar else 'DRY-RUN (0 escrituras en BD)'} — "
            f"restauración de cabeceras rotas por NC por línea "
            f"(tipos: {', '.join(tipos)})"
        ))
        if fuente:
            self.stdout.write(self.style.WARNING(
                f"VÍA MANUAL EXPLÍCITA: fuente={fuente} sobre {len(ids)} id(s) "
                f"({', '.join(str(i) for i in ids)}). Se saltan las compuertas del "
                f"análisis automático (NC vinculada / cruce Σmonto_item vs pagos); "
                f"se mantienen déficit real, monto > 0, monto > cabecera actual y "
                f"acuerdo entre las 3 fuentes"
                f"{' (DISCREPANCIA ACEPTADA a mano)' if acepto_disc else ''}."
            ))

        candidatos, manuales = self._analizar(tipos, desde, hasta, ids, fuente, acepto_disc)
        self._imprimir_resumen(candidatos, manuales, fuente)
        bloqueados_disc = [r for r in manuales
                           if str(r.get('razon', '')).startswith('MANUAL: DISCREPANCIA')]

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
            if fuente:
                filtros += f" --fuente {fuente}"
            if acepto_disc:
                filtros += " --acepto-discrepancia"
            if bloqueados_disc:
                # Sin la bandera ese comando volvería a bloquearse: no lo
                # ofrecemos como si fuera a funcionar.
                self.stdout.write(self.style.WARNING(
                    f'\nDry-run. {len(bloqueados_disc)} documento(s) BLOQUEADO(S) por '
                    f'discrepancia entre fuentes: repetir con --aplicar NO los escribe. '
                    f'Usa el comando con --acepto-discrepancia que aparece arriba, o '
                    f'elige otra --fuente.'))
            if candidatos:
                self.stdout.write(self.style.WARNING(
                    f'\nDry-run. Para escribir los {len(candidatos)} candidato(s) de '
                    f'verdad:\n'
                    f'    python manage.py restaurar_cabeceras_boletas_nc{filtros} '
                    f'--aplicar'))
            elif not bloqueados_disc:
                self.stdout.write(self.style.WARNING(
                    '\nDry-run. Para ejecutar de verdad:\n'
                    f'    python manage.py restaurar_cabeceras_boletas_nc{filtros} '
                    f'--aplicar'))
            return

        if not candidatos:
            if bloqueados_disc:
                self.stdout.write(self.style.ERROR(
                    f'\nNada que aplicar: los {len(bloqueados_disc)} documento(s) '
                    f'pedidos están BLOQUEADOS por discrepancia entre fuentes (ver '
                    f'arriba). NADA se escribió.'))
            else:
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

        # Verificación: ninguno de los aplicados debe seguir calificando.
        # (En la vía manual con una fuente por debajo de los pagos —ej. una
        # boleta cuyo marketplace liquidó menos— el documento sigue apareciendo:
        # es correcto y se explica, no se re-escribe.)
        ids_escritos = [c['dte_id'] for c in candidatos]
        restantes = list(self._queryset_base(tipos, desde, hasta, ids_escritos)
                         .values_list('id', flat=True))
        marca = (self.style.SUCCESS('OK: 0 siguen con déficit') if not restantes
                 else self.style.ERROR(
                     f'ATENCIÓN: {len(restantes)} siguen con déficit '
                     f'({", ".join(str(i) for i in restantes)})'))
        self.stdout.write(f'\nVerificación post-escritura: {marca}')
        if restantes and fuente:
            self.stdout.write(self.style.WARNING(
                f'  Esperable con --fuente {fuente}: el monto declarado quedó por '
                f'debajo de Σ pagos. Revisa el detalle de arriba antes de insistir.'))
        # Verificación positiva: la cabecera quedó exactamente en lo declarado
        quedaron = dict(Dte.objects.filter(id__in=ids_escritos)
                        .values_list('id', 'monto_con_iva'))
        desviados = [c['dte_id'] for c in candidatos
                     if quedaron.get(c['dte_id']) != c['new_con_iva']]
        if desviados:
            self.stdout.write(self.style.ERROR(
                f'  ATENCIÓN: {len(desviados)} cabeceras no quedaron en el valor '
                f'declarado: {", ".join(str(i) for i in desviados)}'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'  {len(ids_escritos)}/{len(ids_escritos)} cabeceras quedaron en el '
                f'monto declarado.'))
        # Efecto colateral DTE↔Ticket: se repite DESPUÉS de escribir para que no
        # se pierda entre el detalle (el command nunca toca Ticket).
        colaterales = [c for c in candidatos
                       if 'EFECTO COLATERAL' in (c.get('nota_ticket') or '')]
        if colaterales:
            self.stdout.write(self.style.WARNING(
                f'\n  ⚠ {len(colaterales)} documento(s) quedaron descuadrados contra '
                f'su Ticket (el ticket NO se tocó):'))
            for c in colaterales:
                self.stdout.write(self.style.WARNING(
                    f"    dte={c['dte_id']} folio={c['folio']}: {c['nota_ticket']}"))
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

    def _analizar(self, tipos, desde, hasta, ids, fuente=None, acepto_disc=False):
        afectados = list(self._queryset_base(tipos, desde, hasta, ids).values(
            'id', 'numero_documento', 'tipo_documento', 'fecha_emision',
            'sucursal_id', 'monto_con_iva', 'monto_neto', 'unidades_productos',
            'descuento', 'referencias', 'pagos_sum',
        ))
        ids_af = [a['id'] for a in afectados]
        if ids:
            self._reportar_ids_fuera(ids, set(ids_af), tipos)

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

        # Cruce contra el ticket (informativo en la vía automática; fuente
        # elegible en la vía manual `--fuente ticket`).
        tickets = self._tickets_por_dte(afectados)

        candidatos, manuales = [], []
        for a in sorted(afectados, key=lambda x: (x['fecha_emision'], x['id'])):
            did = a['id']
            pagos = int(a['pagos_sum'] or 0)
            cab = a['monto_con_iva'] or Decimal(0)
            deficit = pagos - int(cab)
            lns = lineas.get(did, [])
            total_mi = sum(int(l['monto_item'] or 0) for l in lns)
            tk = tickets.get(did)
            ticket_total = tk['total'] if tk else None
            base = dict(
                dte_id=did, folio=a['numero_documento'], tipo=a['tipo_documento'],
                sucursal_id=a['sucursal_id'], fecha_emision=a['fecha_emision'],
                old_con_iva=cab, old_neto=a['monto_neto'] or Decimal(0),
                old_unidades=int(a['unidades_productos'] or 0),
                descuento=int(a['descuento'] or 0),
                pagos=pagos, total_mi=total_mi, ticket_total=ticket_total,
                ticket_via=(tk['via'] if tk else None),
                ticket_id=(tk['id'] if tk else None),
                deficit=deficit,
            )
            ncs_doc = ncs.get(did, [])

            # ---------------- VÍA MANUAL EXPLÍCITA (--ids + --fuente) --------
            if fuente:
                cand, razon = self._candidato_manual(
                    base, fuente, lns, ncs_doc, nc_unidades, acepto_disc)
                if cand is None:
                    manuales.append({**base, 'razon': razon})
                else:
                    candidatos.append(cand)
                continue

            # 1) NC vinculada en EMITIDO/ACEPTADO — obligatoria
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
                'discrepancia': '',   # la vía automática exige acuerdo ±$5 por diseño
                'nota_ticket': self._nota_ticket(base, int(new_con_iva)),
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
    # Vía manual explícita (--ids + --fuente)
    # ------------------------------------------------------------------ #

    def _tickets_por_dte(self, afectados):
        """
        {dte_id: {'id':…, 'total': int, 'via': 'TICKET-ref'|'folio_dte'}}

        Resuelve primero por `referencias = 'TICKET-<correlativo>'` (+ sucursal,
        que es la clave única del Ticket) y, para los que no traen esa
        referencia, por `Ticket.folio_dte == numero_documento` + sucursal. El
        fallback exige coincidencia ÚNICA: si dos tickets de la misma sucursal
        comparten folio (boleta y factura con la misma numeración), se descarta
        el cruce en vez de adivinar.
        """
        corr_por_dte = {}
        for a in afectados:
            ref = a['referencias'] or ''
            if ref.startswith('TICKET-'):
                try:
                    corr_por_dte[a['id']] = (int(ref.split('TICKET-')[1].split()[0]),
                                             a['sucursal_id'])
                except (ValueError, IndexError):
                    pass
        res = {}
        if corr_por_dte:
            corrs = {c for c, _s in corr_por_dte.values()}
            idx = {}
            for t in Ticket.objects.filter(correlativo__in=corrs).values(
                    'id', 'correlativo', 'sucursal_id', 'total'):
                idx[(t['correlativo'], t['sucursal_id'])] = t
            for did, clave in corr_por_dte.items():
                t = idx.get(clave)
                if t:
                    res[did] = {'id': t['id'], 'total': int(t['total'] or 0),
                                'via': 'TICKET-ref'}

        faltan = [a for a in afectados if a['id'] not in res]
        if faltan:
            folios = {a['numero_documento'] for a in faltan}
            por_clave = collections.defaultdict(list)
            for t in Ticket.objects.filter(folio_dte__in=folios).values(
                    'id', 'folio_dte', 'sucursal_id', 'total'):
                por_clave[(t['folio_dte'], t['sucursal_id'])].append(t)
            for a in faltan:
                lst = por_clave.get((a['numero_documento'], a['sucursal_id']), [])
                if len(lst) == 1:
                    res[a['id']] = {'id': lst[0]['id'], 'total': int(lst[0]['total'] or 0),
                                    'via': 'folio_dte'}
        return res

    def _fuentes_disponibles(self, base):
        """
        Las TRES fuentes del documento, en orden fijo, para imprimirlas lado a
        lado: [(clave, etiqueta, valor|None, nota)]. `valor is None` = la fuente
        no existe para ese documento (sin líneas / sin pagos / sin ticket).
        """
        desc = int(base['descuento'] or 0)
        mi = int(base['total_mi'] or 0)
        tt = base['ticket_total']
        return [
            ('monto_item', 'Σ Dte_Productos.monto_item', mi if mi > 0 else None,
             f'(− descuento global ${desc:,} = ${mi - desc:,})' if desc else ''),
            ('pagos', 'Σ Dte_Detalle_Pago.monto',
             int(base['pagos'] or 0) or None, ''),
            ('ticket', 'Ticket.total', int(tt) if tt is not None else None,
             f"(ticket #{base['ticket_id']}, vía {base['ticket_via']})"
             if tt is not None else '(no resoluble)'),
        ]

    def _discrepancias(self, base, fuente, total):
        """
        Fuentes DISPONIBLES distintas de la elegida que NO coinciden (±TOL) con
        el monto declarado. Única reconciliación admitida: Σ monto_item cuadra
        también si lo hace tras restar el `descuento` global del documento
        (así el caso legítimo con descuento de cabecera no pide bandera).

        Devuelve [(clave, etiqueta, valor, delta_vs_elegido)].
        """
        desc = int(base['descuento'] or 0)
        fuera = []
        for clave, etiqueta, valor, _nota in self._fuentes_disponibles(base):
            if clave == fuente or valor is None:
                continue
            if abs(valor - total) <= TOL:
                continue
            if clave == 'monto_item' and desc and abs(valor - desc - total) <= TOL:
                continue  # cuadra vía el descuento global del documento
            fuera.append((clave, etiqueta, valor, valor - total))
        return fuera

    def _bloque_fuentes(self, base, fuente, total, disc, sangria='      '):
        """Las 3 fuentes lado a lado + el elegido. Se imprime SIEMPRE, antes de
        cualquier escritura (y también cuando el documento queda bloqueado)."""
        fuera = {c for c, _e, _v, _d in disc}
        out = [f'{sangria}FUENTES DEL DOCUMENTO (elegida: {fuente}):']
        for clave, etiqueta, valor, nota in self._fuentes_disponibles(base):
            if valor is None:
                out.append(f'{sangria}  {clave:<11} {"—":>14}   {etiqueta} {nota}')
                continue
            if clave == fuente:
                marca = '<= ELEGIDA'
            elif clave in fuera:
                marca = f'!= difiere en ${valor - total:+,}'
            else:
                marca = '== coincide'
            out.append(f'{sangria}  {clave:<11} ${valor:>13,}   {marca} {nota}'.rstrip())
        return out

    def _nota_ticket(self, base, total):
        """
        Efecto sobre la relación DTE↔Ticket. Este command NUNCA toca `Ticket`,
        así que mover la cabecera puede CREAR un descuadre donde hoy no lo hay
        (caso real: dte 2178247, cab $173.950 == ticket #334 $173.950; con
        --fuente pagos la cabecera queda en $188.940 y el ticket no se mueve).
        """
        tt = base['ticket_total']
        if tt is None:
            return ''
        tt, old = int(tt), int(base['old_con_iva'])
        cuadra_hoy = abs(old - tt) <= TOL
        cuadra_luego = abs(total - tt) <= TOL
        tk = f"#{base['ticket_id']}"
        if cuadra_hoy and not cuadra_luego:
            return (f'EFECTO COLATERAL: hoy DTE y ticket {tk} COINCIDEN (${old:,}); '
                    f'tras escribir el DTE queda en ${total:,} y el ticket sigue en '
                    f'${tt:,} => se CREA un descuadre DTE↔ticket de ${total - tt:+,} '
                    f'(este command no toca Ticket)')
        if not cuadra_hoy and cuadra_luego:
            return (f'el descuadre DTE↔ticket {tk} actual (${old:,} vs ${tt:,}) queda '
                    f'RESUELTO: ambos en ${tt:,}')
        if not cuadra_hoy and not cuadra_luego:
            return (f'DTE y ticket {tk} siguen descuadrados tras escribir: '
                    f'${total:,} vs ${tt:,} (${total - tt:+,})')
        return f'DTE y ticket {tk} siguen coincidiendo en ${tt:,}'

    def _candidato_manual(self, base, fuente, lns, ncs_doc, nc_unidades,
                          acepto_disc=False):
        """
        Construye el candidato con el monto DECLARADO por el operador.

        Se saltan las compuertas de la vía automática (NC vinculada, cruce
        Σmonto_item↔pagos), pero NO las de integridad:
          - la fuente debe traer un monto > 0,
          - ese monto debe ser ESTRICTAMENTE MAYOR que la cabecera actual
            (este command solo repara déficit; jamás baja una cabecera),
          - las OTRAS fuentes disponibles deben coincidir con la elegida (±$5);
            si no, el documento se BLOQUEA salvo `--acepto-discrepancia`.

        `unidades_productos` solo se reconstruye si la fuente elegida queda
        corroborada por las líneas (Σ monto_item, o Σ monto_item − descuento
        global del documento). Si no, se dejan intactas: preferimos un dato
        viejo declarado a uno nuevo inventado.

        Devuelve (candidato, None) o (None, razón_para_REVISION_MANUAL).
        """
        if fuente == 'monto_item':
            total = base['total_mi']
        elif fuente == 'pagos':
            total = base['pagos']
        else:  # ticket
            total = base['ticket_total']
            if total is None:
                return None, 'MANUAL: SIN_TICKET_RESOLUBLE'
        total = int(total or 0)
        if total <= 0:
            return None, f'MANUAL: FUENTE_{fuente.upper()}_EN_CERO'

        old = int(base['old_con_iva'])
        if total <= old:
            return None, (f'MANUAL: FUENTE_NO_MEJORA (elegido ${total:,} <= '
                          f'cabecera actual ${old:,})')

        # COMPUERTA DE DISCREPANCIA: elegir la fuente equivocada jamás se acepta
        # en silencio. Se compara la elegida contra TODAS las otras disponibles.
        disc = self._discrepancias(base, fuente, total)
        if disc and not acepto_disc:
            detalle = ', '.join(f'{c} ${v:,} (Δ ${d:+,})' for c, _e, v, d in disc)
            return None, (
                f'MANUAL: DISCREPANCIA_ENTRE_FUENTES — elegido {fuente} ${total:,}; '
                f'no coincide con {detalle}. Exige --acepto-discrepancia')
        if disc:
            nota_disc = ('DISCREPANCIA ACEPTADA (--acepto-discrepancia): elegido '
                         f'{fuente} ${total:,} vs '
                         + ', '.join(f'{c} ${v:,} (Δ ${d:+,})' for c, _e, v, d in disc))
        else:
            nota_disc = ''

        # ¿las líneas corroboran el monto elegido?
        desc = int(base['descuento'] or 0)
        corrobora = ''
        if abs(base['total_mi'] - total) <= TOL:
            corrobora = 'Σmonto_item'
        elif desc and abs(base['total_mi'] - desc - total) <= TOL:
            corrobora = 'Σmonto_item - descuento global'

        if corrobora:
            new_unidades, tocadas, nota_uds = self._reconstruir_unidades(
                lns, ncs_doc, nc_unidades)
            if nota_uds:
                nota_uds = f'{nota_uds}; líneas corroboran por {corrobora}'
            else:
                nota_uds = f'líneas corroboran por {corrobora}'
        else:
            new_unidades, tocadas = None, False
            nota_uds = (f'unidades SIN tocar: las líneas no corroboran el monto '
                        f'elegido (Σmonto_item ${base["total_mi"]:,} vs '
                        f'${total:,})')

        partes = [f'Σmonto_item ${base["total_mi"]:,}', f'pagos ${base["pagos"]:,}']
        if base['ticket_total'] is not None:
            partes.append(f'ticket ${base["ticket_total"]:,} ({base["ticket_via"]})')
        ncs_ok = [n for n in ncs_doc if n['estado_dte'] in ESTADOS_NC_VALIDOS]
        return {
            **base,
            'new_con_iva': Decimal(total),
            'new_neto': _neto_half_up(Decimal(total)),
            'new_unidades': new_unidades if tocadas else base['old_unidades'],
            'unidades_tocadas': tocadas,
            'nota_unidades': nota_uds,
            'fuente': FUENTES_MANUALES[fuente],
            'cross_check': 'MANUAL[' + ' | '.join(partes) + ']',
            'ncs_vinculadas': ';'.join(str(n['id']) for n in ncs_ok),
            'discrepancia': nota_disc,
            'fuentes_fuera': disc,
            'nota_ticket': self._nota_ticket(base, total),
        }, None

    def _reportar_ids_fuera(self, ids, ids_en_scope, tipos):
        """Con --ids: dice EN VOZ ALTA qué ids pedidos no entraron y por qué."""
        faltan = [i for i in ids if i not in ids_en_scope]
        if not faltan:
            return
        info = {d['id']: d for d in Dte.objects.filter(id__in=faltan)
                .annotate(pagos_sum=Sum('dte_asociado__monto'))
                .values('id', 'numero_documento', 'tipo_documento', 'monto_con_iva',
                        'pagos_sum', 'es_nota_credito', 'descartado', 'tipo_transaccion')}
        self.stdout.write(self.style.WARNING(
            f'\n=== {len(faltan)} id(s) de --ids FUERA de alcance (no se tocan) ==='))
        for i in faltan:
            d = info.get(i)
            if d is None:
                self.stdout.write(f'  dte={i}: no existe')
                continue
            pagos = int(d['pagos_sum'] or 0)
            cab = int(d['monto_con_iva'] or 0)
            if d['es_nota_credito']:
                motivo = 'es NOTA DE CREDITO'
            elif d['descartado']:
                motivo = 'está descartado'
            elif d['tipo_documento'] not in tipos:
                motivo = f"tipo {d['tipo_documento']} fuera de --tipo ({', '.join(tipos)})"
            elif d['tipo_transaccion'] not in ('VENTA', 'VENTA_PUBLICO'):
                motivo = f"tipo_transaccion {d['tipo_transaccion']} no es venta"
            elif pagos <= cab + TOL:
                motivo = f'sin déficit (cabecera ${cab:,} >= pagos ${pagos:,} - ${TOL})'
            else:
                # Inalcanzable hoy: las 5 ramas de arriba replican exactamente los
                # filtros de _queryset_base. Se deja como red por si ese queryset
                # gana una condición nueva y este reporte deja de explicarla.
                motivo = 'no calificó en el queryset base'
            self.stdout.write(f"  dte={i} folio={d['numero_documento']}: {motivo}")

    # ------------------------------------------------------------------ #
    # Salida
    # ------------------------------------------------------------------ #

    def _imprimir_resumen(self, candidatos, manuales, fuente=None):
        if fuente:
            self._imprimir_bloqueos_por_discrepancia(manuales, fuente)
            self.stdout.write(
                f'\n=== VÍA MANUAL (--fuente {fuente}): {len(candidatos)} '
                f'documento(s) a restaurar ===')
            for c in candidatos:
                self.stdout.write(
                    f"  dte={c['dte_id']} folio={c['folio']} "
                    f"cab ${int(c['old_con_iva']):,} → ${int(c['new_con_iva']):,} "
                    f"(neto ${int(c['old_neto']):,} → ${int(c['new_neto']):,}, "
                    f"uds {c['old_unidades']}"
                    f"{'→' + str(c['new_unidades']) if c['unidades_tocadas'] else ' SIN TOCAR'})")
                # Las 3 fuentes SIEMPRE lado a lado, antes de cualquier escritura.
                for linea in self._bloque_fuentes(c, fuente, int(c['new_con_iva']),
                                                  c.get('fuentes_fuera') or []):
                    self.stdout.write(linea)
                if c.get('discrepancia'):
                    self.stdout.write(self.style.WARNING(f"      ⚠ {c['discrepancia']}"))
                # Aviso PRE-escritura de déficit residual (antes solo se avisaba
                # en la verificación post-escritura, o sea después de escribir).
                resto = int(c['pagos']) - int(c['new_con_iva'])
                if resto > TOL:
                    self.stdout.write(self.style.WARNING(
                        f'      ⚠ el monto elegido deja ${resto:,} de DÉFICIT frente a '
                        f'Σ pagos (${int(c["pagos"]):,}): el documento volverá a '
                        f'aparecer en la verificación post-escritura'))
                if c.get('nota_ticket'):
                    if 'EFECTO COLATERAL' in c['nota_ticket']:
                        self.stdout.write(self.style.WARNING(
                            f"      ⚠ {c['nota_ticket']}"))
                    else:
                        self.stdout.write(f"      ticket: {c['nota_ticket']}")
                self.stdout.write(f"      unidades: {c['nota_unidades']}")
            delta = sum(int(c['new_con_iva']) - int(c['old_con_iva']) for c in candidatos)
            self.stdout.write(
                f'  Δ cabecera a escribir: ${delta:,} '
                f'(el "déficit" de abajo mide pagos − cabecera, que no siempre '
                f'coincide con la fuente elegida)')

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

    def _imprimir_bloqueos_por_discrepancia(self, manuales, fuente):
        """
        Bloque a prueba de distracciones para los documentos que la compuerta de
        discrepancia frenó: las 3 fuentes lado a lado + el comando exacto con
        --acepto-discrepancia. Se imprime ANTES de la lista de candidatos.
        """
        bloqueados = [r for r in manuales
                      if str(r.get('razon', '')).startswith('MANUAL: DISCREPANCIA')]
        if not bloqueados:
            return
        self.stdout.write(self.style.ERROR(
            f'\n=== BLOQUEADO POR DISCREPANCIA ENTRE FUENTES: {len(bloqueados)} '
            f'documento(s) — NADA se escribe ==='))
        for r in bloqueados:
            total = self._monto_de_fuente(r, fuente)
            self.stdout.write(
                f"  dte={r['dte_id']} folio={r['folio']} {r['tipo']} "
                f"fecha={r['fecha_emision']} cab actual ${int(r['old_con_iva']):,}")
            for linea in self._bloque_fuentes(
                    r, fuente, total, self._discrepancias(r, fuente, total)):
                self.stdout.write(linea)
            resto = int(r['pagos']) - total
            if resto > TOL:
                self.stdout.write(self.style.WARNING(
                    f'      ⚠ con {fuente} la cabecera quedaría en ${total:,} y '
                    f'dejaría ${resto:,} de DÉFICIT frente a Σ pagos'))
            nt = self._nota_ticket(r, total)
            if nt:
                self.stdout.write(f'      ticket: {nt}')
        ids_txt = ','.join(str(r['dte_id']) for r in bloqueados)
        self.stdout.write(self.style.WARNING(
            '\n  Revisa las fuentes de arriba y decide. Si la fuente elegida es la '
            'correcta pese al desacuerdo, reconócelo explícitamente:\n'
            f'    python manage.py restaurar_cabeceras_boletas_nc --ids {ids_txt} '
            f'--fuente {fuente} --acepto-discrepancia\n'
            '  (y agrega --aplicar cuando el dry-run diga lo que esperas)'))

    @staticmethod
    def _monto_de_fuente(base, fuente):
        if fuente == 'monto_item':
            return int(base['total_mi'] or 0)
        if fuente == 'pagos':
            return int(base['pagos'] or 0)
        return int(base['ticket_total'] or 0)

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
                    'discrepancia': c.get('discrepancia', ''),
                    'nota_ticket': c.get('nota_ticket', ''),
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
