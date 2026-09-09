# -*- coding: utf-8 -*-
"""
Rellena el Nº de operación de Mercado Pago (y los datos del voucher) en las
transacciones ya registradas, leyéndolos de la API.

POR QUÉ: el cobro se crea con la Orders API, que devuelve un id ULID
(`PAY01M1S0Y7D27DTM81ZE8SWMKGBC`). Ese identificador NO existe en el panel, la
app ni los reportes de liquidación de Mercado Pago, donde la misma operación
es un número (`177422093000`). Además la orden Point no trae el código de
autorización ni los últimos 4 dígitos de la tarjeta: en producción esos campos
quedaron vacíos ("MP Point Aut: -"). Todo eso sí viene en
`GET /v1/payments/search`, que se cruza por `external_reference`.

QUÉ RELLENA (solo campos vacíos; nunca pisa un valor ya cargado):
  * `payment_id_mp`        Nº de operación del panel de MP
  * `codigo_autorizacion`  código de autorización real
  * `ultimos_4_digitos`    últimos 4 de la tarjeta
  * `metodo_pago_mp`       payment_type_id, si faltaba
  * `monto_neto` / `fee_mp`  neto acreditado y comisión de MP

Con `--voucher` además copia el Nº de operación al `voucher` del pago del
ticket y del DTE, que es lo que se ve y se busca en Consulta de Documentos.
Sin esa opción los pagos quedan intactos.

USO:
    # Diagnóstico de ayer (no escribe nada)
    python manage.py backfill_payment_id_mp

    python manage.py backfill_payment_id_mp --desde 2026-09-01 --hasta 2026-09-08
    python manage.py backfill_payment_id_mp --desde 2026-09-05 --hasta 2026-09-05 --apply
    python manage.py backfill_payment_id_mp --desde 2026-09-05 --hasta 2026-09-05 --apply --voucher
"""
import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from django.utils import timezone

from app.models import (
    Dte_Detalle_Pago,
    MercadoPagoConfig,
    TicketDetallePago,
    TransaccionMercadoPago,
)
from app.services import mercadopago_service as mp

logger = logging.getLogger('app')


def _fechas(desde, hasta):
    dia = desde
    while dia <= hasta:
        yield dia
        dia += timedelta(days=1)


class Command(BaseCommand):
    help = ('Rellena el Nº de operación de Mercado Pago y los datos del voucher '
            'en las transacciones ya registradas, leyéndolos de la API')

    def add_arguments(self, parser):
        parser.add_argument('--desde', type=str, default=None, help='YYYY-MM-DD (default: ayer)')
        parser.add_argument('--hasta', type=str, default=None, help='YYYY-MM-DD (default: --desde)')
        parser.add_argument('--apply', action='store_true',
                            help='ESCRIBE los cambios (sin esto es dry-run)')
        parser.add_argument('--voucher', action='store_true',
                            help='Además copia el Nº de MP al voucher del pago del ticket y del DTE')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        aplicar = options['apply']
        tocar_voucher = options['voucher']
        etiqueta = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{etiqueta}] Backfill del Nº de operacion Mercado Pago'
            + (' (+ voucher del pago)' if tocar_voucher else '')
        ))

        ayer = timezone.localdate() - timedelta(days=1)
        try:
            desde = (datetime.strptime(options['desde'], '%Y-%m-%d').date()
                     if options['desde'] else ayer)
            hasta = (datetime.strptime(options['hasta'], '%Y-%m-%d').date()
                     if options['hasta'] else desde)
        except ValueError:
            raise CommandError('Las fechas deben tener formato AAAA-MM-DD')
        if hasta < desde:
            raise CommandError('--hasta no puede ser anterior a --desde')

        # Una consulta por CUENTA (token resuelto), no por caja: varias
        # sucursales comparten la misma cuenta MP.
        configs, tokens = [], set()
        for config in MercadoPagoConfig.objects.select_related('sucursal', 'cuenta'):
            try:
                token = mp._token(config)
            except mp.MercadoPagoError:
                self.stderr.write(f'  Caja {config.id} sin credenciales resolubles — omitida')
                continue
            if token in tokens:
                continue
            tokens.add(token)
            configs.append(config)
        if not configs:
            raise CommandError('Ninguna caja Mercado Pago con credenciales resolubles')

        total = {'pagos': 0, 'trx': 0, 'campos': 0, 'vouchers': 0, 'sin_local': 0}
        for config in configs:
            for dia in _fechas(desde, hasta):
                total_dia = self._procesar_dia(config, dia, aplicar, tocar_voucher)
                for k, v in total_dia.items():
                    total[k] += v

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f"Pagos leidos de MP: {total['pagos']} | transacciones tocadas: {total['trx']} "
            f"| campos rellenados: {total['campos']} | vouchers: {total['vouchers']}"))
        if total['sin_local']:
            self.stdout.write(self.style.WARNING(
                f"Pagos de MP sin transaccion local: {total['sin_local']} "
                '(cobros fuera del sistema — revisar con conciliar_mercadopago)'))
        if not aplicar and total['trx']:
            self.stdout.write('\nPara aplicar, repetir el comando con --apply'
                              + (' --voucher' if tocar_voucher else ''))

    # ------------------------------------------------------------------
    def _procesar_dia(self, config, dia, aplicar, tocar_voucher):
        cuenta = mp._cuenta_de(config)
        etiqueta = (f'empresa {cuenta.empresa.rut}' if cuenta
                    else f'caja {config.id}')
        try:
            pagos = mp.buscar_pagos_dia(config, dia)
        except mp.MercadoPagoError as e:
            self.stderr.write(f'  {dia} ({etiqueta}): no se pudo consultar MP — {e.mensaje}')
            return {'pagos': 0, 'trx': 0, 'campos': 0, 'vouchers': 0, 'sin_local': 0}

        refs = [str(p.get('external_reference') or '') for p in pagos]
        locales = {
            t.external_reference: t
            for t in TransaccionMercadoPago.objects.filter(
                external_reference__in=[r for r in refs if r])
        }
        conteo = {'pagos': len(pagos), 'trx': 0, 'campos': 0, 'vouchers': 0, 'sin_local': 0}
        self.stdout.write(f'\n== {dia} ({etiqueta}): {len(pagos)} pago(s) en MP ==')

        for pago in pagos:
            if str(pago.get('status') or '') not in ('approved', 'refunded', 'charged_back'):
                continue
            ext = str(pago.get('external_reference') or '')
            trx = locales.get(ext)
            if trx is None:
                conteo['sin_local'] += 1
                continue
            cambios = self._campos_a_rellenar(trx, pago)
            if not cambios:
                continue
            conteo['trx'] += 1
            conteo['campos'] += len(cambios)
            resumen = ', '.join(f'{k}={v}' for k, v in cambios.items())
            self.stdout.write(f'  {ext} ${trx.monto:>8}: {resumen}')
            if not aplicar:
                if tocar_voucher and cambios.get('payment_id_mp'):
                    conteo['vouchers'] += self._contar_vouchers(trx)
                continue
            with db_transaction.atomic():
                for campo, valor in cambios.items():
                    setattr(trx, campo, valor)
                trx.save(update_fields=list(cambios) + ['actualizado_en'])
                if tocar_voucher and cambios.get('payment_id_mp'):
                    conteo['vouchers'] += self._escribir_voucher(
                        trx, cambios['payment_id_mp'])
        return conteo

    # ------------------------------------------------------------------
    def _campos_a_rellenar(self, trx, pago):
        """Solo campos VACÍOS: este comando no corrige, completa."""
        cambios = {}
        id_mp = str(pago.get('id') or '')
        if id_mp.isdigit() and not trx.payment_id_mp:
            cambios['payment_id_mp'] = id_mp[:40]
        aut = str(pago.get('authorization_code') or '')
        if aut and not trx.codigo_autorizacion:
            cambios['codigo_autorizacion'] = aut[:30]
        last4 = str((pago.get('card') or {}).get('last_four_digits') or '')
        if last4 and not trx.ultimos_4_digitos:
            cambios['ultimos_4_digitos'] = last4[:4]
        medio = str(pago.get('payment_type_id') or '')
        if medio and not trx.metodo_pago_mp:
            cambios['metodo_pago_mp'] = medio[:40]
        neto = (pago.get('transaction_details') or {}).get('net_received_amount')
        if neto is not None and trx.monto_neto is None:
            # CLP es entero pero MP manda decimales: half-up, nunca int() directo
            cambios['monto_neto'] = int(round(float(neto)))
            cambios['fee_mp'] = trx.monto - cambios['monto_neto']
        return cambios

    def _pagos_del_cobro(self, trx):
        """Los pagos (ticket + espejo del DTE) que representan este cobro.

        Se ubican por el voucher, que es donde el POS dejó el id de la Orders
        API — la FK `detalle_pago` puede venir nula en cobros reparados a mano.
        """
        pagos_ticket = []
        if trx.detalle_pago_id:
            pagos_ticket = list(TicketDetallePago.objects.filter(id=trx.detalle_pago_id))
        elif trx.payment_id:
            pagos_ticket = list(TicketDetallePago.objects.filter(
                voucher=trx.payment_id, metodo_pago__startswith='MP_'))
        pagos_dte = []
        if trx.payment_id:
            pagos_dte = list(Dte_Detalle_Pago.objects.filter(
                voucher=trx.payment_id, metodo_pago__startswith='MP_'))
        return pagos_ticket, pagos_dte

    def _contar_vouchers(self, trx):
        pagos_ticket, pagos_dte = self._pagos_del_cobro(trx)
        return len(pagos_ticket) + len(pagos_dte)

    def _escribir_voucher(self, trx, numero_mp):
        """Deja el Nº de MP en el voucher del pago (lo que se ve en Documentos).

        El id de la Orders API no se pierde: sigue en `TransaccionMercadoPago
        .payment_id`, y se anota en las notas del pago para no perder el rastro.
        """
        pagos_ticket, pagos_dte = self._pagos_del_cobro(trx)
        escritos = 0
        marca = f'MP Orders id: {trx.payment_id}'
        for pago in pagos_ticket:
            notas = (pago.notas or '').strip()
            pago.voucher = numero_mp[:100]
            pago.notas = notas if marca in notas else f'{notas} | {marca}'.strip(' |')
            pago.save(update_fields=['voucher', 'notas', 'actualizado_en'])
            escritos += 1
        for pago in pagos_dte:
            notas = (pago.notas or '').strip()
            pago.voucher = numero_mp[:50]
            pago.notas = notas if marca in notas else f'{notas} | {marca}'.strip(' |')
            pago.save(update_fields=['voucher', 'notas'])
            escritos += 1
        if escritos:
            logger.info('MP: voucher del cobro %s actualizado al Nº de MP %s (%s pago/s)',
                        trx.external_reference, numero_mp, escritos)
        return escritos
