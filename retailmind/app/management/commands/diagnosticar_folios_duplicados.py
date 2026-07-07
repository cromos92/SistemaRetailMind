"""
diagnosticar_folios_duplicados — Detecta folios de DTE REPETIDOS dentro del
mismo emisor tributario (RUT). Para el SII, el número de folio debe ser único
por (emisor, tipo de documento). Dos DTE con el mismo (emisor, tipo, número)
= folio reusado (problema tributario / de dinero).

Caso conocido: PAO4 reusó 368 folios de boleta electrónica que PAO3 ya había
emitido bajo el MISMO RUT 78503140-7 (empresa_id=1319). Este comando lo
cuantifica y encuentra CUALQUIER otro solapamiento re-ejecutable.

Distingue lo peligroso: **cross-sucursal** = el mismo folio aparece en 2+
sucursales del mismo RUT (la firma del caso PAO); vs mismo-sucursal (suele ser
dato duplicado / reintento).

Modo SOLO LECTURA (resumen + Excel). No modifica nada. La re-foliación es una
decisión tributaria que NO automatiza este comando.

Uso:
    python manage.py diagnosticar_folios_duplicados
    python manage.py diagnosticar_folios_duplicados --emisor 1319
    python manage.py diagnosticar_folios_duplicados --tipo "BOLETA ELECTRONICA"
    python manage.py diagnosticar_folios_duplicados --solo-cross-sucursal
    python manage.py diagnosticar_folios_duplicados --incluir-descartados --sin-excel
"""
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count

from app.models import Dte


class Command(BaseCommand):
    help = ('Detecta folios de DTE repetidos dentro del mismo emisor (RUT); '
            'marca los cross-sucursal (firma del caso PAO4/PAO3).')

    def add_arguments(self, parser):
        parser.add_argument('--emisor', type=int, default=None,
                            help='Limitar a un emisor (empresa_id).')
        parser.add_argument('--tipo', default=None,
                            help='Limitar a un tipo_documento (ej. "BOLETA ELECTRONICA").')
        parser.add_argument('--solo-cross-sucursal', action='store_true',
                            help='Reportar SOLO grupos donde el folio cruza 2+ sucursales '
                                 'del mismo RUT (lo tributariamente grave).')
        parser.add_argument('--incluir-descartados', action='store_true',
                            help='Incluir DTE descartado=True (por defecto se omiten).')
        parser.add_argument('--top', type=int, default=1000,
                            help='Nº de grupos a detallar en el Excel (default 1000).')
        parser.add_argument('--excel', default='folios_duplicados.xlsx',
                            help='Ruta del Excel de salida.')
        parser.add_argument('--sin-excel', action='store_true',
                            help='Solo resumen en consola, sin Excel.')

    # -------------------------------------------------------------- handle
    def handle(self, *args, **opts):
        base = Dte.objects.all()
        if not opts['incluir_descartados']:
            base = base.filter(descartado=False)
        if opts['emisor']:
            base = base.filter(emisor_id=opts['emisor'])
        if opts['tipo']:
            base = base.filter(tipo_documento=opts['tipo'])

        # 1) Claves de folio repetidas (el group-by lo hace la BD).
        dup_keys = (base.values('emisor_id', 'tipo_documento', 'numero_documento')
                    .annotate(n=Count('id'))
                    .filter(n__gt=1))
        dup_set = {
            (d['emisor_id'], d['tipo_documento'], d['numero_documento'])
            for d in dup_keys
        }

        if not dup_set:
            self.stdout.write(self.style.SUCCESS(
                '\nNo hay folios duplicados con ese criterio. 🎉'))
            return

        # 2) Un solo barrido: traer las filas que caen en un grupo duplicado.
        grupos = defaultdict(list)
        for d in (base.select_related('emisor', 'sucursal')
                  .values('id', 'emisor_id', 'emisor__rut', 'emisor__nombre',
                          'tipo_documento', 'numero_documento', 'sucursal_id',
                          'sucursal__alias', 'fecha_emision', 'estado_dte',
                          'monto_con_iva', 'es_nota_credito', 'descartado')
                  .iterator()):
            key = (d['emisor_id'], d['tipo_documento'], d['numero_documento'])
            if key in dup_set:
                grupos[key].append(d)

        # 3) Clasificar cada grupo.
        detalle = []
        tot_docs = tot_extra = grupos_cross = grupos_elec = 0
        monto_extra_total = Decimal('0')
        por_emisor = defaultdict(lambda: {'grupos': 0, 'cross': 0, 'docs': 0,
                                          'rut': '', 'nombre': ''})
        for key, docs in grupos.items():
            emisor_id, tipo, numero = key
            sucursales = {d['sucursal_id'] for d in docs}
            cross = len(sucursales) > 1
            electronico = 'ELECTRONICA' in (tipo or '').upper()
            docs_ord = sorted(docs, key=lambda d: (d['fecha_emision'] or ''))
            # el "original" = el más antiguo; los extra = folios reusados
            extra = docs_ord[1:]
            monto_extra = sum((d['monto_con_iva'] or Decimal('0')) for d in extra)

            tot_docs += len(docs)
            tot_extra += len(extra)
            monto_extra_total += monto_extra
            if cross:
                grupos_cross += 1
            if electronico:
                grupos_elec += 1

            e = por_emisor[emisor_id]
            e['grupos'] += 1
            e['docs'] += len(docs)
            e['rut'] = docs[0]['emisor__rut']
            e['nombre'] = docs[0]['emisor__nombre']
            if cross:
                e['cross'] += 1

            if opts['solo_cross_sucursal'] and not cross:
                continue
            detalle.append({
                'key': key, 'docs': docs_ord, 'cross': cross,
                'electronico': electronico, 'monto_extra': monto_extra,
            })

        detalle.sort(key=lambda g: (-int(g['cross']), -int(g['electronico']),
                                    -g['monto_extra'], -len(g['docs'])))

        # ------------------------------------------------------- resumen
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== DIAGNÓSTICO DE FOLIOS DUPLICADOS (por emisor/RUT) ==='))
        self.stdout.write(f'Grupos de folio repetido        : {len(grupos):,}')
        self.stdout.write(f'  · DTE involucrados            : {tot_docs:,}')
        self.stdout.write(f'  · folios REUSADOS (extra)     : {tot_extra:,}')
        self.stdout.write(self.style.WARNING(
            f'  · grupos CROSS-SUCURSAL       : {grupos_cross:,}  (mismo RUT, folio en 2+ sucursales — grave SII)'))
        self.stdout.write(f'  · grupos electrónicos         : {grupos_elec:,}')
        self.stdout.write(self.style.WARNING(
            f'  · monto de folios reusados    : ${monto_extra_total:,.0f}'))

        self.stdout.write('\nPor emisor (RUT):')
        for eid, e in sorted(por_emisor.items(), key=lambda kv: -kv[1]['cross']):
            marca = '  ⚠️ CROSS' if e['cross'] else ''
            self.stdout.write(
                f"   [{e['rut'] or '—':<12}] {(e['nombre'] or '')[:32]:<32} "
                f"grupos={e['grupos']:<5} cross={e['cross']:<5} docs={e['docs']}{marca}")

        self.stdout.write('\nTop grupos (cross-sucursal y electrónicos primero):')
        for g in detalle[:15]:
            eid, tipo, numero = g['key']
            rut = g['docs'][0]['emisor__rut']
            sucs = '·'.join(sorted({(d['sucursal__alias'] or '—') for d in g['docs']}))
            fechas = f"{g['docs'][0]['fecha_emision']}→{g['docs'][-1]['fecha_emision']}"
            flag = ('CROSS ' if g['cross'] else '') + ('ELEC' if g['electronico'] else '')
            self.stdout.write(
                f"   RUT {rut} {tipo[:18]:<18} folio {numero:<8} "
                f"n={len(g['docs'])} [{sucs}] {fechas} ${g['monto_extra']:,.0f} {flag}")

        if not opts['sin_excel']:
            self._exportar_excel(detalle[:opts['top']], opts['excel'])

    # -------------------------------------------------------------- excel
    def _exportar_excel(self, detalle, ruta):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.stdout.write(self.style.ERROR('openpyxl no disponible; omito Excel.'))
            return

        wb = Workbook()
        ws = wb.active
        ws.title = 'Folios duplicados'
        ws.append(['Grupo', 'Emisor_RUT', 'Emisor', 'Tipo_documento', 'Folio',
                   'Cross_sucursal', 'Electronico', 'Rol', 'DTE_id', 'Sucursal',
                   'Fecha_emision', 'Estado_dte', 'Monto_con_iva', 'Es_NC', 'Descartado'])
        for i, g in enumerate(detalle, start=1):
            _eid, tipo, numero = g['key']
            for j, d in enumerate(g['docs']):
                ws.append([
                    i,
                    d['emisor__rut'], (d['emisor__nombre'] or '')[:60],
                    tipo, numero,
                    'SI' if g['cross'] else '',
                    'SI' if g['electronico'] else '',
                    'original (+antiguo)' if j == 0 else 'REUSADO',
                    d['id'],
                    d['sucursal__alias'] or '',
                    d['fecha_emision'],
                    d['estado_dte'],
                    float(d['monto_con_iva'] or 0),
                    'SI' if d['es_nota_credito'] else '',
                    'SI' if d['descartado'] else '',
                ])
        wb.save(ruta)
        self.stdout.write(self.style.SUCCESS(
            f'\nExcel generado: {ruta}  ({len(detalle)} grupos; una fila por DTE, '
            f'la 1ª de cada grupo es el "original" más antiguo, el resto REUSADOS).'))
