"""
rango_folios_boletas — Rango de folios de BOLETA ELECTRONICA emitidos en un mes,
desglosado por sucursal y consolidado por RUT emisor.

Por qué el doble corte: el SII no ve sucursales, ve un RUT. La correlación de
folios es única por (RUT emisor, tipo de documento), así que el rango "de
verdad" es el del RUT; el corte por sucursal sirve para operación interna y para
ver si dos sucursales del mismo RUT se pisaron los folios (caso PAO4/PAO3).

Sobre los huecos: un folio ausente dentro del rango del mes NO es
necesariamente un folio perdido — puede haberse emitido en otro mes. Por eso el
comando contrasta contra las boletas del emisor fuera del período, PERO acotado
al rango de folios [min, max] que se observó en el mes; sin ese acote la
consulta se trae la historia completa de folios y tarda muchísimo.

RENDIMIENTO: todos los índices de `Dte` empiezan por `sucursal` (ver
Dte.Meta.indexes), así que un filtro por tipo_documento + fecha_emision SIN
sucursal obliga a Postgres a un seq scan de la tabla entera y contra la BD
remota el servidor corta la conexión antes de terminar. Por eso el comando
recorre SUCURSAL POR SUCURSAL (más un pase para sucursal NULL): cada consulta
entra por dte_suc_tipo_fecha_idx. Si aun así molesta la espera, `--rapido`
omite el segundo barrido (el cruce con folios de otros meses).

Modo SOLO LECTURA. No modifica nada.

Uso:
    python manage.py rango_folios_boletas                      # mes anterior
    python manage.py rango_folios_boletas --anio 2026 --mes 7
    python manage.py rango_folios_boletas --anio 2026 --mes 7 --rapido
    python manage.py rango_folios_boletas --anio 2026 --mes 7 --sucursal 5
    python manage.py rango_folios_boletas --anio 2026 --mes 7 --excel folios_julio.xlsx
    python manage.py rango_folios_boletas --mes 7 --detalle-huecos
"""
import calendar
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from app.models import Correlativo, Dte, Sucursal

# Boletas recibidas de proveedores no consumen folios propios: no entran al rango.
TRANSACCIONES_EMITIDAS = ['VENTA', 'VENTA_PUBLICO']
ESTADOS_ANULADOS = ['ANULADO', 'CANCELADO', 'RECHAZADO']


class Command(BaseCommand):
    help = ('Rango de folios de boleta electrónica emitidos en un mes, '
            'por sucursal y por RUT emisor (solo lectura).')

    def add_arguments(self, parser):
        hoy = timezone.localdate()
        mes_ant = hoy.month - 1 or 12
        anio_ant = hoy.year if hoy.month > 1 else hoy.year - 1
        parser.add_argument('--anio', type=int, default=anio_ant,
                            help=f'Año a consultar (default {anio_ant}).')
        parser.add_argument('--mes', type=int, default=mes_ant,
                            help=f'Mes a consultar 1-12 (default {mes_ant}).')
        parser.add_argument('--tipo', default='BOLETA ELECTRONICA',
                            help='tipo_documento a consultar (default "BOLETA ELECTRONICA").')
        parser.add_argument('--emisor', type=int, default=None,
                            help='Limitar a un emisor (empresa_id).')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='Limitar a una sucursal (sucursal_id).')
        parser.add_argument('--rapido', action='store_true',
                            help='Omitir el cruce con folios de otros meses (un solo barrido). '
                                 'Los huecos quedan calculados solo contra el mes consultado.')
        parser.add_argument('--incluir-compras', action='store_true',
                            help='Incluir tipo_transaccion=COMPRA (documentos recibidos).')
        parser.add_argument('--incluir-descartados', action='store_true',
                            help='Incluir DTE descartado=True (por defecto se omiten).')
        parser.add_argument('--detalle-huecos', action='store_true',
                            help='Listar todos los folios faltantes, no solo los primeros.')
        parser.add_argument('--excel', default=None,
                            help='Ruta del Excel de salida (si se omite, solo consola).')

    # -------------------------------------------------------------- handle
    def handle(self, *args, **opts):
        anio, mes, tipo = opts['anio'], opts['mes'], opts['tipo']
        if not 1 <= mes <= 12:
            raise CommandError('--mes debe estar entre 1 y 12.')

        desde = date(anio, mes, 1)
        hasta = date(anio, mes, calendar.monthrange(anio, mes)[1])

        base = Dte.objects.filter(tipo_documento=tipo)
        if not opts['incluir_descartados']:
            base = base.filter(descartado=False)
        if opts['emisor']:
            base = base.filter(emisor_id=opts['emisor'])
        if opts['sucursal']:
            base = base.filter(sucursal_id=opts['sucursal'])

        del_mes = base.filter(fecha_emision__gte=desde, fecha_emision__lte=hasta)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== RANGO DE FOLIOS · {tipo} · {desde} a {hasta} ==='))
        t0 = time.monotonic()

        # Se consulta SUCURSAL POR SUCURSAL a propósito. Un filtro global por
        # tipo_documento + fecha_emision no puede usar ningún índice de Dte
        # (todos empiezan por `sucursal`) y degenera en un seq scan de la tabla
        # entera; contra la BD remota el servidor corta la conexión antes de
        # terminar. Acotando por sucursal, cada consulta entra por
        # dte_suc_tipo_fecha_idx. El paso extra con sucursal NULL existe porque
        # hay DTE sin sucursal asignada (facturas PAO4, compras legacy) y un
        # btree de Postgres sí indexa los NULL.
        if opts['sucursal']:
            sucursal_ids = [opts['sucursal']]
        else:
            sucursal_ids = list(
                Sucursal.objects.order_by('id').values_list('id', flat=True)
            ) + [None]

        campos = ('id', 'emisor_id', 'emisor__rut', 'emisor__nombre',
                  'sucursal_id', 'sucursal__alias', 'numero_documento',
                  'fecha_emision', 'estado_dte', 'monto_con_iva',
                  'tipo_transaccion')
        filas = []
        for suc_id in sucursal_ids:
            q = (del_mes.filter(sucursal__isnull=True) if suc_id is None
                 else del_mes.filter(sucursal_id=suc_id))
            trozo = list(q.values(*campos))
            filas.extend(trozo)
            if trozo:
                alias = trozo[0]['sucursal__alias'] or 'SIN SUCURSAL'
                self.stdout.write(f'  · {alias[:22]:<22} {len(trozo):>7,} docs '
                                  f'({time.monotonic() - t0:.1f}s)')
                self.stdout.flush()
        self.stdout.write(f'  · TOTAL leído: {len(filas):,} documentos '
                          f'({time.monotonic() - t0:.1f}s)')

        # El split COMPRA/emitidas se hace en memoria: una consulta más contra
        # esta tabla cuesta otro seq scan completo.
        n_compras = sum(1 for f in filas if f['tipo_transaccion'] == 'COMPRA')
        if not opts['incluir_compras']:
            filas = [f for f in filas if f['tipo_transaccion'] in TRANSACCIONES_EMITIDAS]

        if not filas:
            self.stdout.write(self.style.WARNING(
                'No hay documentos de ese tipo en ese período con esos filtros.'))
            self._tipos_disponibles(desde, hasta)
            return

        # ---------------------------------------------------- agrupaciones
        por_suc = defaultdict(lambda: self._acc())
        por_rut = defaultdict(lambda: self._acc())
        for f in filas:
            k_suc = (f['emisor_id'], f['sucursal_id'])
            self._acumular(por_suc[k_suc], f)
            self._acumular(por_rut[f['emisor__rut'] or '—'], f)

        # Folios de OTROS meses, para distinguir "hueco real" de "folio emitido
        # en otro mes". Acotado al rango [min, max] visto en el período: sin ese
        # acote la consulta arrastra la historia completa de folios del emisor y
        # el servidor corta la conexión antes de terminar.
        emisores = {f['emisor_id'] for f in filas}
        historicos = defaultdict(set)
        if not opts['rapido']:
            rangos = defaultdict(lambda: [None, None])
            for (emisor_id, _suc_id), a in por_suc.items():
                r = rangos[emisor_id]
                r[0] = a['min'] if r[0] is None else min(r[0], a['min'])
                r[1] = a['max'] if r[1] is None else max(r[1], a['max'])

            # Una consulta por emisor (no un OR gigante): así `emisor_id=X`
            # entra por el índice de la FK y el rango de folios recorta el resto.
            t1 = time.monotonic()
            for emisor_id, (lo, hi) in rangos.items():
                for num in (base.filter(emisor_id=emisor_id,
                                        numero_documento__range=(lo, hi))
                            .exclude(fecha_emision__gte=desde,
                                     fecha_emision__lte=hasta)
                            .values_list('numero_documento', flat=True).iterator()):
                    historicos[emisor_id].add(num)
            self.stdout.write(
                f'  · {sum(len(v) for v in historicos.values()):,} folios del mismo rango '
                f'emitidos en otros meses ({time.monotonic() - t1:.1f}s)')

        # ------------------------------------------------------- por sucursal
        self.stdout.write('\n' + self.style.MIGRATE_LABEL('POR SUCURSAL'))
        cab = (f"{'RUT emisor':<13} {'Sucursal':<22} {'Boletas':>8} "
               f"{'Desde':>9} {'Hasta':>9} {'Rango':>7} {'Huecos':>7} "
               f"{'Rep':>4} {'Anul':>5} {'Monto':>15}")
        self.stdout.write(cab)
        self.stdout.write('-' * len(cab))

        detalle_excel = []
        orden = sorted(por_suc.items(),
                       key=lambda kv: (kv[1]['rut'] or '', kv[1]['sucursal'] or ''))
        for (emisor_id, _suc_id), a in orden:
            huecos = self._huecos(a, historicos[emisor_id])
            span = a['max'] - a['min'] + 1
            self.stdout.write(
                f"{a['rut'] or '—':<13} {(a['sucursal'] or 'SIN SUCURSAL')[:22]:<22} "
                f"{a['n']:>8,} {a['min']:>9,} {a['max']:>9,} {span:>7,} "
                f"{len(huecos):>7,} {len(a['repetidos']):>4,} {a['anulados']:>5,} "
                f"${a['monto']:>14,.0f}"
            )
            if huecos:
                muestra = huecos if opts['detalle_huecos'] else huecos[:12]
                extra = '' if len(muestra) == len(huecos) else f' … (+{len(huecos) - len(muestra)})'
                etiqueta = ('folios ausentes en el período (pueden estar emitidos en otro mes)'
                            if opts['rapido'] else 'folios sin emitir en ningún mes')
                self.stdout.write(self.style.WARNING(
                    f"{'':<13} └ {etiqueta}: {self._compactar(muestra)}{extra}"))
            if a['repetidos']:
                self.stdout.write(self.style.ERROR(
                    f"{'':<13} └ folios REPETIDOS: {self._compactar(sorted(a['repetidos']))}"))
            detalle_excel.append((a, huecos))

        # ----------------------------------------------------- por RUT emisor
        self.stdout.write('\n' + self.style.MIGRATE_LABEL(
            'CONSOLIDADO POR RUT EMISOR  (lo que ve el SII)'))
        cab2 = (f"{'RUT emisor':<13} {'Emisor':<28} {'Boletas':>8} "
                f"{'Desde':>9} {'Hasta':>9} {'Sucs':>5} {'Rep':>4} {'Monto':>15}")
        self.stdout.write(cab2)
        self.stdout.write('-' * len(cab2))
        for rut, a in sorted(por_rut.items()):
            marca = ''
            if a['repetidos'] and len(a['sucursales']) > 1:
                marca = '  ⚠️ folios repetidos entre sucursales del mismo RUT'
            self.stdout.write(
                f"{rut:<13} {(a['nombre'] or '')[:28]:<28} {a['n']:>8,} "
                f"{a['min']:>9,} {a['max']:>9,} {len(a['sucursales']):>5} "
                f"{len(a['repetidos']):>4,} ${a['monto']:>14,.0f}{marca}"
            )

        total_n = sum(a['n'] for a in por_rut.values())
        total_monto = sum(a['monto'] for a in por_rut.values())
        self.stdout.write(
            f"\nTOTAL: {total_n:,} boletas · ${total_monto:,.0f} · "
            f"{len(por_suc)} sucursales · {len(por_rut)} RUT emisores")
        if n_compras and not opts['incluir_compras']:
            self.stdout.write(self.style.WARNING(
                f"({n_compras:,} documentos con tipo_transaccion=COMPRA quedaron fuera "
                f"—son recibidos, no consumen folio propio. Usa --incluir-compras para verlos.)"))

        self._correlativos(tipo, emisores)

        if opts['excel']:
            self._exportar_excel(detalle_excel, por_rut, opts['excel'], tipo, desde, hasta)

    # --------------------------------------------------------- helpers
    @staticmethod
    def _acc():
        return {'n': 0, 'min': None, 'max': None, 'monto': Decimal('0'),
                'anulados': 0, 'folios': set(), 'repetidos': set(),
                'rut': '', 'nombre': '', 'sucursal': '', 'sucursales': set(),
                'f_min': None, 'f_max': None}

    @staticmethod
    def _acumular(a, f):
        num = f['numero_documento']
        a['n'] += 1
        a['monto'] += (f['monto_con_iva'] or Decimal('0'))
        if f['estado_dte'] in ESTADOS_ANULADOS:
            a['anulados'] += 1
        if num in a['folios']:
            a['repetidos'].add(num)
        a['folios'].add(num)
        a['min'] = num if a['min'] is None else min(a['min'], num)
        a['max'] = num if a['max'] is None else max(a['max'], num)
        fe = f['fecha_emision']
        a['f_min'] = fe if a['f_min'] is None else min(a['f_min'], fe)
        a['f_max'] = fe if a['f_max'] is None else max(a['f_max'], fe)
        a['rut'] = a['rut'] or f['emisor__rut']
        a['nombre'] = a['nombre'] or f['emisor__nombre']
        a['sucursal'] = a['sucursal'] or f['sucursal__alias']
        a['sucursales'].add(f['sucursal_id'])

    @staticmethod
    def _huecos(a, historico_emisor):
        """Folios ausentes en [min, max] que tampoco existen en otro mes."""
        if a['min'] is None:
            return []
        return [n for n in range(a['min'], a['max'] + 1)
                if n not in a['folios'] and n not in historico_emisor]

    @staticmethod
    def _compactar(nums):
        """[1,2,3,7,9,10] -> '1-3, 7, 9-10'."""
        if not nums:
            return ''
        tramos, ini, prev = [], nums[0], nums[0]
        for n in nums[1:]:
            if n == prev + 1:
                prev = n
                continue
            tramos.append((ini, prev))
            ini = prev = n
        tramos.append((ini, prev))
        return ', '.join(f'{a:,}' if a == b else f'{a:,}-{b:,}' for a, b in tramos)

    def _correlativos(self, tipo, emisores):
        """Estado del correlativo interno, para ver cuánto folio queda."""
        variantes = [tipo, tipo.replace(' ', '_')]
        corrs = (Correlativo.objects
                 .filter(sucursal__empresa_id__in=emisores)
                 .filter(Q(tipo_dte__in=variantes))
                 .select_related('sucursal', 'sucursal__empresa')
                 .order_by('sucursal__empresa__rut', 'sucursal__alias'))
        if not corrs:
            return
        self.stdout.write('\n' + self.style.MIGRATE_LABEL(
            'CORRELATIVO INTERNO (siguiente folio a emitir)'))
        for c in corrs:
            estado = {'agotado': self.style.ERROR, 'critico': self.style.WARNING}.get(
                c.estado, lambda s: s)
            self.stdout.write(estado(
                f"   {c.sucursal.empresa.rut or '—':<13} {c.sucursal.alias[:22]:<22} "
                f"siguiente={c.inicio:,}  término={c.termino:,}  "
                f"disponibles={c.disponibles:,}  [{c.estado}]"))

    def _tipos_disponibles(self, desde, hasta):
        from django.db.models import Count
        tipos = (Dte.objects.filter(fecha_emision__gte=desde, fecha_emision__lte=hasta)
                 .values('tipo_documento').annotate(n=Count('id')).order_by('-n')[:15])
        if tipos:
            self.stdout.write('\nTipos con datos en ese período (usa --tipo):')
            for t in tipos:
                self.stdout.write(f"   {t['tipo_documento']:<25} {t['n']:,}")

    # ---------------------------------------------------------- excel
    def _exportar_excel(self, detalle, por_rut, ruta, tipo, desde, hasta):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.stdout.write(self.style.ERROR('openpyxl no disponible; omito Excel.'))
            return

        wb = Workbook()
        ws = wb.active
        ws.title = 'Por sucursal'
        ws.append(['RUT_emisor', 'Emisor', 'Sucursal', 'Tipo_documento',
                   'Periodo_desde', 'Periodo_hasta', 'Boletas',
                   'Folio_desde', 'Folio_hasta', 'Folios_en_rango',
                   'Huecos', 'Folios_faltantes', 'Folios_repetidos',
                   'Anuladas', 'Primera_emision', 'Ultima_emision', 'Monto_con_iva'])
        for a, huecos in detalle:
            ws.append([
                a['rut'], (a['nombre'] or '')[:60], a['sucursal'] or 'SIN SUCURSAL', tipo,
                desde, hasta, a['n'], a['min'], a['max'], a['max'] - a['min'] + 1,
                len(huecos), self._compactar(huecos),
                self._compactar(sorted(a['repetidos'])),
                a['anulados'], a['f_min'], a['f_max'], float(a['monto']),
            ])

        ws2 = wb.create_sheet('Por RUT emisor')
        ws2.append(['RUT_emisor', 'Emisor', 'Boletas', 'Folio_desde', 'Folio_hasta',
                    'Sucursales', 'Folios_repetidos', 'Monto_con_iva'])
        for rut, a in sorted(por_rut.items()):
            ws2.append([rut, (a['nombre'] or '')[:60], a['n'], a['min'], a['max'],
                        len(a['sucursales']), self._compactar(sorted(a['repetidos'])),
                        float(a['monto'])])

        wb.save(ruta)
        self.stdout.write(self.style.SUCCESS(f'\nExcel generado: {ruta}'))
