# -*- coding: utf-8 -*-
"""
Analiza los primeros N registros de credito_personal (MySQL)
para proponer mapeo hacia los modelos existentes en Django.
"""
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv
import mysql.connector

from django.conf import settings
from django.core.management.base import BaseCommand

from app.models import Dte, Empresa, Sucursal, Vendedor


def _normalizar_rut(rut):
    if not rut:
        return ''
    return rut.replace('.', '').replace('-', '').upper().strip()


def _normalizar_texto(valor):
    if not valor:
        return ''
    return str(valor).strip().upper()


def _normalizar_compacto(valor):
    if not valor:
        return ''
    return re.sub(r'\s+', ' ', str(valor).strip().upper())


class Command(BaseCommand):
    help = 'Analiza credito_personal (MySQL) y sugiere mapeo'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=400, help='Cantidad de registros a analizar')
        parser.add_argument('--sample', type=int, default=25, help='Cantidad de ejemplos a mostrar')
        parser.add_argument('--table', type=str, default='credito_personal', help='Tabla MySQL a analizar')

    def handle(self, *args, **options):
        limit = options.get('limit', 400)
        sample = options.get('sample', 25)
        table_name = options.get('table', 'credito_personal')

        if not re.match(r'^[A-Za-z0-9_]+$', table_name or ''):
            raise ValueError('Nombre de tabla inválido')

        env_path = Path(settings.BASE_DIR).parent / '.env'
        load_dotenv(env_path)

        mysql_host = os.getenv('MYSQL_HOST')
        mysql_port = int(os.getenv('MYSQL_PORT', 3306))
        mysql_db = os.getenv('MYSQL_DATABASE')
        mysql_user = os.getenv('MYSQL_USER')
        mysql_password = os.getenv('MYSQL_PASSWORD')

        if not all([mysql_host, mysql_db, mysql_user, mysql_password]):
            raise RuntimeError('Faltan variables MYSQL_* en .env')

        conn = mysql.connector.connect(
            host=mysql_host,
            port=mysql_port,
            database=mysql_db,
            user=mysql_user,
            password=mysql_password,
        )

        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """,
            (mysql_db, table_name)
        )
        exists = cursor.fetchone()['total']
        if not exists:
            cursor.execute(
                """
                SELECT TABLE_NAME
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name LIKE %s
                ORDER BY TABLE_NAME
                """,
                (mysql_db, '%credito%')
            )
            candidates = [row['TABLE_NAME'] for row in cursor.fetchall()]
            self.stdout.write(self.style.ERROR(
                f'No existe la tabla "{table_name}" en {mysql_db}.'
            ))
            if candidates:
                self.stdout.write('Tablas con "credito":')
                for name in candidates:
                    self.stdout.write(f'  - {name}')
            cursor.close()
            conn.close()
            return

        cursor.execute(
            f"""
            SELECT
                ID, rut, cliente, empresa, folio, tipo_cliente,
                monto_a_credito, pagado, fecha, estado,
                dteAsociado, sucursal
            FROM {table_name}
            ORDER BY ID
            LIMIT %s
            """,
            (limit,)
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            self.stdout.write(self.style.WARNING('No hay registros para analizar.'))
            return

        # Preparar caches locales
        vendedores = Vendedor.objects.exclude(rut__isnull=True).exclude(rut='').values_list('id', 'rut')
        vendedores_by_rut = {_normalizar_rut(rut): vid for vid, rut in vendedores}

        empresas = Empresa.objects.exclude(rut__isnull=True).exclude(rut='').values_list('id', 'rut')
        empresas_by_rut = {_normalizar_rut(rut): eid for eid, rut in empresas}

        sucursales = Sucursal.objects.values_list('id', 'alias', 'direccion')
        sucursales_by_alias = {}
        sucursales_by_direccion = {}
        sucursales_direccion_list = []
        for sid, alias, direccion in sucursales:
            if alias:
                sucursales_by_alias[_normalizar_texto(alias)] = sid
            if direccion:
                sucursales_by_direccion[_normalizar_texto(direccion)] = sid
                sucursales_direccion_list.append((sid, _normalizar_compacto(direccion)))

        # DTEs relevantes solo para los IDs consultados
        dte_ids = {row.get('dteAsociado') for row in rows if row.get('dteAsociado')}
        dte_ids = {int(d) for d in dte_ids if str(d).isdigit()}
        folios = {row.get('folio') for row in rows if row.get('folio')}
        folios = {int(f) for f in folios if str(f).isdigit()}
        dtes_by_id = set(Dte.objects.filter(id__in=dte_ids).values_list('id', flat=True))
        dtes_by_num = set(
            Dte.objects.filter(numero_documento__in=(dte_ids | folios)).values_list('numero_documento', flat=True)
        )

        tipo_cliente_count = Counter()
        estado_count = Counter()
        mapping_count = Counter()
        reasons = Counter()

        ejemplos = defaultdict(list)

        for row in rows:
            rut = row.get('rut') or ''
            rut_norm = _normalizar_rut(rut)
            tipo_cliente = row.get('tipo_cliente') or ''
            tipo_cliente_norm = _normalizar_texto(tipo_cliente)
            estado = row.get('estado') or ''
            estado_count[estado] += 1
            tipo_cliente_count[tipo_cliente_norm or '(VACIO)'] += 1

            dte_asociado = row.get('dteAsociado')
            folio = row.get('folio')
            dte_match = None
            if dte_asociado:
                if dte_asociado in dtes_by_id:
                    dte_match = 'id'
                elif dte_asociado in dtes_by_num:
                    dte_match = 'numero_documento'
            if not dte_match and folio:
                if folio in dtes_by_num:
                    dte_match = 'folio_numero_documento'

            vendedor_id = vendedores_by_rut.get(rut_norm)
            empresa_id = empresas_by_rut.get(rut_norm)

            sucursal_raw = row.get('sucursal') or ''
            sucursal_norm = _normalizar_texto(sucursal_raw)
            sucursal_compact = _normalizar_compacto(sucursal_raw)
            sucursal_id = (
                sucursales_by_alias.get(sucursal_norm)
                or sucursales_by_direccion.get(sucursal_norm)
            )
            if not sucursal_id and sucursal_compact:
                matches = [
                    sid for sid, direccion in sucursales_direccion_list
                    if sucursal_compact in direccion or direccion in sucursal_compact
                ]
                if len(matches) == 1:
                    sucursal_id = matches[0]
                    reasons['sucursal_fuzzy'] += 1
                elif len(matches) > 1:
                    reasons['sucursal_ambigua'] += 1

            is_interno = tipo_cliente_norm in {'INTERNO', 'TRABAJADOR', 'VENDEDOR', 'PERSONAL'}

            if vendedor_id or is_interno:
                mapping = 'CREDITO_TRABAJADOR'
                reasons['interno_o_vendedor'] += 1
            elif dte_match:
                mapping = 'DTE_DETALLE_PAGO'
                reasons[f'dte_match_{dte_match}'] += 1
            else:
                mapping = 'TICKET_DETALLE_PAGO'
                reasons['sin_dte'] += 1

            if not rut_norm:
                reasons['rut_vacio'] += 1
            if not sucursal_id:
                reasons['sucursal_no_map'] += 1
            if (dte_asociado or folio) and not dte_match:
                reasons['dte_no_encontrado'] += 1

            mapping_count[mapping] += 1

            if len(ejemplos[mapping]) < sample:
                ejemplos[mapping].append({
                    'ID': row.get('ID'),
                    'rut': rut,
                    'cliente': row.get('cliente'),
                    'tipo_cliente': tipo_cliente,
                    'monto_a_credito': row.get('monto_a_credito'),
                    'pagado': row.get('pagado'),
                    'fecha': row.get('fecha'),
                    'estado': row.get('estado'),
                    'dteAsociado': dte_asociado,
                    'sucursal': row.get('sucursal'),
                    'dte_match': dte_match,
                    'vendedor_id': vendedor_id,
                    'empresa_id': empresa_id,
                    'sucursal_id': sucursal_id,
                })

        self.stdout.write(self.style.SUCCESS(f'Total analizados: {len(rows)}'))
        self.stdout.write('=== Conteo por tipo_cliente ===')
        for k, v in tipo_cliente_count.most_common():
            self.stdout.write(f'  - {k}: {v}')
        self.stdout.write('=== Conteo por estado ===')
        for k, v in estado_count.most_common():
            self.stdout.write(f'  - {k or "(VACIO)"}: {v}')

        self.stdout.write('=== Sugerencia de mapeo ===')
        for k, v in mapping_count.most_common():
            self.stdout.write(f'  - {k}: {v}')

        self.stdout.write('=== Razones / alertas ===')
        for k, v in reasons.most_common():
            self.stdout.write(f'  - {k}: {v}')

        for key, items in ejemplos.items():
            self.stdout.write(f'=== Ejemplos {key} (max {sample}) ===')
            for item in items:
                self.stdout.write(str(item))
