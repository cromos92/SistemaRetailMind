# -*- coding: utf-8 -*-
"""
Importa creditos_personal (MySQL) hacia modelos existentes en Django.
"""
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, time
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.models import (
    CreditoTrabajador,
    PagoCreditoTrabajador,
    Dte,
    Dte_Detalle_Pago,
    Empresa,
    Sucursal,
    Vendedor,
)


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


def _estado_credito(estado_raw, monto, pagado, force_aprobado=False):
    estado = _normalizar_texto(estado_raw)
    monto = monto or 0
    pagado = pagado or 0
    if pagado >= monto and monto > 0:
        return 'PAGADO'
    if force_aprobado:
        return 'ACTIVO'
    if 'PENDIENTE' in estado:
        return 'PENDIENTE'
    return 'ACTIVO'


class Command(BaseCommand):
    help = 'Importa creditos_personal (MySQL) a créditos/pagos/DTE'

    def add_arguments(self, parser):
        parser.add_argument('--table', type=str, default='creditos_personal', help='Tabla MySQL a importar')
        parser.add_argument('--limit', type=int, default=None, help='Limitar cantidad de registros')
        parser.add_argument('--dry-run', action='store_true', help='Simular sin guardar')
        parser.add_argument('--empresa-id', type=int, default=None, help='Empresa fallback si no hay sucursal')
        parser.add_argument('--user-id', type=int, default=None, help='Usuario para solicitado_por')
        parser.add_argument('--crear-vendedor', action='store_true', help='Crear vendedor si no existe')
        parser.add_argument('--externo-en-creditos', action='store_true', help='Crear CreditoTrabajador para externos')
        parser.add_argument('--actualizar', action='store_true', help='Actualizar créditos ya importados')
        parser.add_argument('--solo-internos', action='store_true', help='Procesar solo internos')
        parser.add_argument('--solo-externos', action='store_true', help='Procesar solo externos')

    def handle(self, *args, **options):
        table_name = options.get('table', 'creditos_personal')
        limit = options.get('limit')
        dry_run = options.get('dry_run', False)
        empresa_fallback_id = options.get('empresa_id')
        user_id = options.get('user_id')
        crear_vendedor = options.get('crear_vendedor', False)
        externo_en_creditos = options.get('externo_en_creditos', False)
        actualizar = options.get('actualizar', False)
        solo_internos = options.get('solo_internos', False)
        solo_externos = options.get('solo_externos', False)

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

        # Resolver usuario
        User = get_user_model()
        user = None
        if user_id:
            user = User.objects.filter(id=user_id).first()
        if not user:
            user = User.objects.filter(is_superuser=True).first() or User.objects.first()

        if not user and not dry_run:
            raise RuntimeError('No se encontró usuario para solicitado_por. Use --user-id')

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
            cursor.close()
            conn.close()
            raise RuntimeError(f'No existe la tabla "{table_name}" en {mysql_db}')

        query = f"""
            SELECT
                ID, rut, cliente, empresa, folio, tipo_cliente,
                monto_a_credito, pagado, fecha, estado,
                dteAsociado, sucursal
            FROM {table_name}
            ORDER BY ID
            {f"LIMIT {int(limit)}" if limit else ""}
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            self.stdout.write(self.style.WARNING('No hay registros para importar.'))
            return

        # caches
        vendedores = Vendedor.objects.exclude(rut__isnull=True).exclude(rut='').values_list('id', 'rut')
        vendedores_by_rut = {_normalizar_rut(rut): vid for vid, rut in vendedores}

        sucursales = Sucursal.objects.values_list('id', 'alias', 'direccion', 'empresa_id')
        sucursales_by_alias = {}
        sucursales_by_direccion = {}
        sucursales_direccion_list = []
        sucursal_empresa = {}
        for sid, alias, direccion, empresa_id in sucursales:
            if alias:
                sucursales_by_alias[_normalizar_texto(alias)] = sid
            if direccion:
                sucursales_by_direccion[_normalizar_texto(direccion)] = sid
                sucursales_direccion_list.append((sid, _normalizar_compacto(direccion)))
            if empresa_id:
                sucursal_empresa[sid] = empresa_id

        empresas = Empresa.objects.values_list('id', 'rut')
        empresas_by_rut = {_normalizar_rut(rut): eid for eid, rut in empresas if rut}
        empresa_default = Empresa.objects.first()
        sucursal_default = Sucursal.objects.first()

        stats = Counter()
        errors = defaultdict(list)

        for row in rows:
            tipo_cliente_raw = row.get('tipo_cliente') or ''
            tipo_cliente_norm = _normalizar_texto(tipo_cliente_raw)

            is_interno = 'INTERNO' in tipo_cliente_norm
            is_externo = 'EXTERNO' in tipo_cliente_norm or 'ORDEN COMPRA' in tipo_cliente_norm or not is_interno

            if solo_internos and not is_interno:
                stats['skip_no_interno'] += 1
                continue
            if solo_externos and not is_externo:
                stats['skip_no_externo'] += 1
                continue

            rut = row.get('rut') or ''
            rut_norm = _normalizar_rut(rut)
            cliente = (row.get('cliente') or '').strip()
            empresa_nombre = (row.get('empresa') or '').strip()
            folio = row.get('folio')
            dte_asociado = row.get('dteAsociado')
            monto = row.get('monto_a_credito') or 0
            pagado = row.get('pagado') or 0
            fecha = row.get('fecha')
            estado = row.get('estado')

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
                elif len(matches) > 1:
                    stats['sucursal_ambigua'] += 1

            empresa_id = None
            if sucursal_id:
                empresa_id = sucursal_empresa.get(sucursal_id)
            if not empresa_id and rut_norm:
                empresa_id = empresas_by_rut.get(rut_norm)
            if not empresa_id and empresa_fallback_id:
                empresa_id = empresa_fallback_id
            if not empresa_id and empresa_default:
                empresa_id = empresa_default.id

            # Fechas
            if fecha:
                fecha_dt = timezone.make_aware(datetime.combine(fecha, time.min))
                fecha_venc = fecha
            else:
                fecha_dt = timezone.now()
                fecha_venc = timezone.now().date()

            # === INTERNOS ===
            if is_interno:
                vendedor_id = vendedores_by_rut.get(rut_norm)
                if not vendedor_id:
                    if not dry_run:
                        nuevo = Vendedor.objects.create(
                            codigo_vendedor=f'INT-{row["ID"]}',
                            rut=rut or None,
                            nombre=cliente or f'Interno {row["ID"]}',
                            empresa_id=empresa_id,
                        )
                        vendedor_id = nuevo.id
                        vendedores_by_rut[_normalizar_rut(nuevo.rut)] = nuevo.id if nuevo.rut else vendedor_id
                    else:
                        vendedor_id = -1

                if not vendedor_id:
                    stats['skip_sin_vendedor_interno'] += 1
                    errors['sin_vendedor'].append(row['ID'])
                    continue
                if not empresa_id:
                    stats['skip_sin_empresa'] += 1
                    errors['sin_empresa'].append(row['ID'])
                    continue
                if not sucursal_id:
                    # usar primera sucursal de la empresa si existe
                    suc = Sucursal.objects.filter(empresa_id=empresa_id).first()
                    sucursal_id = suc.id if suc else None
                if not sucursal_id and sucursal_default:
                    sucursal_id = sucursal_default.id
                    stats['sucursal_fallback_default'] += 1

                numero_credito = f'CP-INT-{row["ID"]}'
                credito_existente = CreditoTrabajador.objects.filter(numero_credito=numero_credito).first()
                if credito_existente and not actualizar:
                    stats['skip_dup_credito'] += 1
                    continue

                if not dry_run:
                    with transaction.atomic():
                        if credito_existente:
                            credito = credito_existente
                            credito.trabajador_id = vendedor_id
                            credito.empresa_origen_id = empresa_id
                            credito.sucursal_id = sucursal_id
                            credito.tipo_credito = 'PRESTAMO_EMPRESA'
                            credito.monto_solicitado = monto
                            credito.monto_aprobado = monto
                            credito.monto_pagado = pagado
                            credito.fecha_solicitud = fecha_dt
                            credito.fecha_vencimiento = fecha_venc
                            credito.estado = _estado_credito(estado, monto, pagado, force_aprobado=True)
                            credito.solicitado_por = user
                            credito.autorizado_por = user
                            credito.fecha_aprobacion = fecha_dt
                            credito.motivo_solicitud = 'Importado desde creditos_personal'
                            credito.observaciones_solicitud = (
                                f'ID:{row["ID"]} | tipo:{tipo_cliente_raw} | empresa:{empresa_nombre} | '
                                f'dte:{dte_asociado} | folio:{folio} | estado:{estado} | pagado:{pagado}'
                            )
                            credito.save()
                            stats['creditos_internos_actualizados'] += 1
                        else:
                            credito = CreditoTrabajador.objects.create(
                                trabajador_id=vendedor_id,
                                empresa_origen_id=empresa_id,
                                sucursal_id=sucursal_id,
                                numero_credito=numero_credito,
                                tipo_credito='PRESTAMO_EMPRESA',
                                monto_solicitado=monto,
                                monto_aprobado=monto,
                                monto_pagado=pagado,
                                fecha_solicitud=fecha_dt,
                                fecha_vencimiento=fecha_venc,
                                estado=_estado_credito(estado, monto, pagado, force_aprobado=True),
                                solicitado_por=user,
                                autorizado_por=user,
                                fecha_aprobacion=fecha_dt,
                                motivo_solicitud='Importado desde creditos_personal',
                                observaciones_solicitud=(
                                    f'ID:{row["ID"]} | tipo:{tipo_cliente_raw} | empresa:{empresa_nombre} | '
                                    f'dte:{dte_asociado} | folio:{folio} | estado:{estado} | pagado:{pagado}'
                                )
                            )
                            stats['creditos_internos'] += 1
                        if pagado and pagado > 0:
                            if not credito.pagos.exists():
                                PagoCreditoTrabajador.objects.create(
                                    credito=credito,
                                    monto_pago=pagado,
                                    fecha_pago=fecha_venc,
                                    metodo_pago='CREDITO_TRABAJADOR',
                                    registrado_por=user,
                                    sucursal_cobro_id=sucursal_id,
                                    referencia_pago=f'CP:{row["ID"]}'
                                )
                continue

            # === EXTERNOS ===
            dte = None
            if dte_asociado:
                dte = Dte.objects.filter(id=dte_asociado).first()
            if not dte and folio:
                dte = Dte.objects.filter(numero_documento=folio).first()

            if dte:
                nota = f'CP:{row["ID"]} | estado:{estado} | pagado:{pagado}'
                if Dte_Detalle_Pago.objects.filter(dte_id=dte.id, notas__contains=f'CP:{row["ID"]}').exists():
                    stats['skip_dup_dte_pago'] += 1
                else:
                    if not dry_run:
                        Dte_Detalle_Pago.objects.create(
                            dte_id=dte.id,
                            metodo_pago='ORDEN_COMPRA' if 'ORDEN COMPRA' in tipo_cliente_norm else 'CREDITO_EXTERNO',
                            tipo_tarjeta=None,
                            voucher=None,
                            monto=monto,
                            notas=nota
                        )
                    stats['dte_pagos_externos'] += 1
            else:
                stats['externos_sin_dte'] += 1

            if externo_en_creditos:
                vendedor_id = vendedores_by_rut.get(rut_norm)
                if not vendedor_id:
                    if not dry_run:
                        nuevo = Vendedor.objects.create(
                            codigo_vendedor=f'EXT-{row["ID"]}',
                            rut=rut or None,
                            nombre=cliente or f'Externo {row["ID"]}',
                            empresa_id=empresa_id,
                        )
                        vendedor_id = nuevo.id
                        vendedores_by_rut[_normalizar_rut(nuevo.rut)] = nuevo.id if nuevo.rut else vendedor_id
                    else:
                        vendedor_id = -1

                if vendedor_id and empresa_id:
                    if not sucursal_id:
                        suc = Sucursal.objects.filter(empresa_id=empresa_id).first()
                        sucursal_id = suc.id if suc else None
                    if not sucursal_id and sucursal_default:
                        sucursal_id = sucursal_default.id
                        stats['sucursal_fallback_default'] += 1
                    numero_credito = f'CP-EXT-{row["ID"]}'
                    credito_existente = CreditoTrabajador.objects.filter(numero_credito=numero_credito).first()
                    if credito_existente and not actualizar:
                        stats['skip_dup_credito'] += 1
                    else:
                        if not dry_run:
                            with transaction.atomic():
                                if credito_existente:
                                    credito = credito_existente
                                    credito.trabajador_id = vendedor_id
                                    credito.empresa_origen_id = empresa_id
                                    credito.sucursal_id = sucursal_id
                                    credito.tipo_credito = 'CREDITO_COMPRA'
                                    credito.monto_solicitado = monto
                                    credito.monto_aprobado = monto
                                    credito.monto_pagado = pagado
                                    credito.fecha_solicitud = fecha_dt
                                    credito.fecha_vencimiento = fecha_venc
                                    credito.estado = _estado_credito(estado, monto, pagado, force_aprobado=True)
                                    credito.solicitado_por = user
                                    credito.autorizado_por = user
                                    credito.fecha_aprobacion = fecha_dt
                                    credito.motivo_solicitud = 'Crédito externo importado'
                                    credito.observaciones_solicitud = (
                                        f'ID:{row["ID"]} | cliente:{cliente} | empresa:{empresa_nombre} | '
                                        f'dte:{dte_asociado} | folio:{folio} | estado:{estado} | pagado:{pagado}'
                                    )
                                    credito.save()
                                    stats['creditos_externos_actualizados'] += 1
                                else:
                                    credito = CreditoTrabajador.objects.create(
                                        trabajador_id=vendedor_id,
                                        empresa_origen_id=empresa_id,
                                        sucursal_id=sucursal_id,
                                        numero_credito=numero_credito,
                                        tipo_credito='CREDITO_COMPRA',
                                        monto_solicitado=monto,
                                        monto_aprobado=monto,
                                        monto_pagado=pagado,
                                        fecha_solicitud=fecha_dt,
                                        fecha_vencimiento=fecha_venc,
                                        estado=_estado_credito(estado, monto, pagado, force_aprobado=True),
                                        solicitado_por=user,
                                        autorizado_por=user,
                                        fecha_aprobacion=fecha_dt,
                                        motivo_solicitud='Crédito externo importado',
                                        observaciones_solicitud=(
                                            f'ID:{row["ID"]} | cliente:{cliente} | empresa:{empresa_nombre} | '
                                            f'dte:{dte_asociado} | folio:{folio} | estado:{estado} | pagado:{pagado}'
                                        )
                                    )
                                    stats['creditos_externos'] += 1
                                if pagado and pagado > 0:
                                    if not credito.pagos.exists():
                                        PagoCreditoTrabajador.objects.create(
                                            credito=credito,
                                            monto_pago=pagado,
                                            fecha_pago=fecha_venc,
                                            metodo_pago='CREDITO_EXTERNO',
                                            registrado_por=user,
                                            sucursal_cobro_id=sucursal_id,
                                            referencia_pago=f'CP:{row["ID"]}'
                                        )
                else:
                    stats['skip_externo_sin_vendedor_o_empresa'] += 1

        self.stdout.write(self.style.SUCCESS('=== RESULTADO IMPORTACION CREDITOS ==='))
        for key, val in stats.most_common():
            self.stdout.write(f'  - {key}: {val}')
        if errors:
            self.stdout.write('=== ERRORES ===')
            for key, ids in errors.items():
                self.stdout.write(f'  - {key}: {len(ids)} (ej: {ids[:10]})')
