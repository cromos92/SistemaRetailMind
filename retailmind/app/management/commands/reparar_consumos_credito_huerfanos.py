# -*- coding: utf-8 -*-
"""
Repara los consumos de Crédito Trabajador que el POS cobró pero nunca debitó.

CONTEXTO (diagnóstico 28-08-2026): el POS cierra la venta en
`registrar_pagos_ticket` y recién DESPUÉS el navegador llama a
`/app/api/creditos/usar-en-venta/` para crear el `PagoCreditoTrabajador`.
Desde el 04-08-2026 ese endpoint aplica un control de alcance
(`_usuario_puede_acceder_credito`) que rechaza con 403 el débito cuando el
crédito es de otra empresa/sucursal que la sesión del cajero — pero
`validar_codigo_credito` NO aplica ese control, así que la boleta se emite
igual y el consumo queda huérfano: mercadería entregada, crédito con el cupo
íntegro.

QUÉ HACE: busca `TicketDetallePago` con metodo CREDITO_TRABAJADOR o
CREDITO_EXTERNO cuyas notas referencian un crédito nativo (CR-AAAA-NNNN) y que
NO tienen su `PagoCreditoTrabajador` correspondiente, y lo crea replicando
EXACTAMENTE lo que habría escrito `usar_credito_en_venta`:

  * referencia_pago  = "<tipo_documento>-<folio>" del DTE (o "TKT-<correlativo>")
  * observaciones    = "Compra en POS - Ticket #<correlativo> - Boleta: <ref>"
                       + marca de reparación
  * sucursal_cobro   = sucursal del ticket
  * fecha_pago       = fecha real del COBRO (creado_en del pago del ticket;
                       `Ticket.fecha` es auto_now y no sirve de fuente)
  * registrado_por   = el usuario `responsable` del ticket (o --usuario)

El save() del modelo recalcula `monto_pagado` del crédito y su estado
(PAGADO si el cupo quedó agotado), igual que el endpoint.

GUARDAS (mismas condiciones bajo las que habría debitado el endpoint, salvo
caducidad/alcance, que aquí no aplican porque la mercadería YA salió):
  * crédito en estado ACTIVO o APROBADO; cualquier otro va a "revisar a mano";
  * el monto no puede sobregirar el saldo — considerando también las otras
    reparaciones de la misma corrida sobre el mismo crédito, y re-verificado
    bajo select_for_update igual que el endpoint.

IDEMPOTENCIA (doble red, para no duplicar débitos):
  1. misma que el endpoint: existe pago con (crédito, referencia, monto);
  2. histórica: existe pago del crédito cuyas observaciones mencionen
     "Ticket #<correlativo>" como número completo (regex con borde, para que
     el ticket 12298 no matchee con el 122980).

USO:
    python manage.py reparar_consumos_credito_huerfanos            # dry-run
    python manage.py reparar_consumos_credito_huerfanos --apply    # ESCRIBE
    python manage.py reparar_consumos_credito_huerfanos --desde-fecha 2026-08-01
    python manage.py reparar_consumos_credito_huerfanos --usuario javier
"""
import logging
import re
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import CreditoTrabajador, Dte, PagoCreditoTrabajador
from app.models.ventas import TicketDetallePago

logger = logging.getLogger('app')

# Solo créditos nativos del ERP. Los CP-* (importados de Laravel) no se usan
# en el POS y sus pagos legacy tienen otro formato de referencia.
RE_CREDITO_NATIVO = re.compile(r'\b(CR-\d{4}-\d{4})\b')

# Estados en los que el endpoint habría aceptado debitar (ACTIVO) o que el
# save() del modelo transforma de forma coherente (APROBADO -> ACTIVO).
ESTADOS_DEBITABLES = ('ACTIVO', 'APROBADO')

# Documentos de venta que emite el POS; excluye del lookup los DTE recibidos
# (compras) y guías que comparten tabla y pueden repetir folio.
TIPOS_DTE_VENTA = ('BOLETA ELECTRONICA', 'FACTURA ELECTRONICA')


class Command(BaseCommand):
    help = ('Crea los PagoCreditoTrabajador que faltan para consumos de '
            'Credito Trabajador ya cobrados en el POS. Por defecto es '
            'dry-run: no escribe nada.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica los cambios. Sin este flag el comando solo reporta (dry-run).',
        )
        parser.add_argument(
            '--desde-fecha', type=str, default=None,
            help='Solo cobros realizados desde esta fecha (AAAA-MM-DD).',
        )
        parser.add_argument(
            '--usuario', type=str, default=None,
            help=('Username a usar como registrado_por cuando el responsable '
                  'del ticket no exista como usuario.'),
        )

    def handle(self, *args, **options):
        aplicar = options['apply']

        desde = None
        if options['desde_fecha']:
            try:
                desde = datetime.strptime(options['desde_fecha'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--desde-fecha debe tener formato AAAA-MM-DD')

        User = get_user_model()
        usuario_fallback = None
        if options['usuario']:
            usuario_fallback = User.objects.filter(
                username__iexact=options['usuario']
            ).first()
            if not usuario_fallback:
                raise CommandError(f"Usuario '{options['usuario']}' no existe")

        etiqueta = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{etiqueta}] Reparacion de consumos de credito huerfanos'
        ))

        pagos_ticket = (
            TicketDetallePago.objects
            .filter(
                metodo_pago__in=['CREDITO_TRABAJADOR', 'CREDITO_EXTERNO'],
                ticket__estado='PAGADO',
                notas__icontains='CR-',
            )
            .select_related('ticket', 'ticket__sucursal')
            .order_by('creado_en', 'id')
        )
        if desde:
            pagos_ticket = pagos_ticket.filter(creado_en__date__gte=desde)

        a_reparar = []    # (pago_ticket, credito, referencia, registrado_por, fecha_venta)
        omitidos = []     # (pago_ticket, motivo)
        ya_registrados = 0
        # Saldo remanente por crédito DENTRO de esta corrida: dos huérfanos del
        # mismo crédito no deben sobregirarlo entre ambos (el segundo habría
        # sido rechazado por el endpoint con el saldo ya debitado).
        restante_por_credito = {}

        def clp(monto):
            return '$' + f'{monto:,.0f}'.replace(',', '.')

        for pt in pagos_ticket:
            ticket = pt.ticket
            m = RE_CREDITO_NATIVO.search(pt.notas or '')
            if not m:
                omitidos.append((pt, 'notas sin numero de credito CR-AAAA-NNNN'))
                continue
            numero_credito = m.group(1)

            credito = CreditoTrabajador.objects.filter(
                numero_credito=numero_credito
            ).select_related('beneficiario').first()
            if not credito:
                omitidos.append((pt, f'credito {numero_credito} no existe'))
                continue

            monto = Decimal(str(pt.monto or 0))
            if monto <= 0:
                omitidos.append((pt, 'monto del pago <= 0'))
                continue

            # `Ticket.fecha` es auto_now (se pisa con cada save del ticket);
            # la fecha real del cobro es el creado_en del pago.
            fecha_venta = timezone.localtime(pt.creado_en).date()
            referencia = self._referencia_boleta(ticket, fecha_venta)

            # Red 1: idempotencia del endpoint (credito, referencia, monto)
            if PagoCreditoTrabajador.objects.filter(
                credito=credito, referencia_pago=referencia, monto_pago=monto,
            ).exists():
                ya_registrados += 1
                continue
            # Red 2: formatos historicos — observaciones con el correlativo
            # COMPLETO (borde \D|$ para que #12298 no matchee con #122980)
            if self._pago_por_correlativo_existe(credito, ticket.correlativo):
                ya_registrados += 1
                continue

            if credito.estado not in ESTADOS_DEBITABLES:
                omitidos.append((pt, (
                    f'{numero_credito}: estado {credito.estado} — el endpoint '
                    f'no habria debitado, revisar a mano'
                )))
                continue

            if credito.id not in restante_por_credito:
                restante_por_credito[credito.id] = Decimal(str(
                    credito.monto_aprobado or credito.monto_solicitado or 0
                )) - Decimal(str(credito.monto_pagado or 0))
            if monto > restante_por_credito[credito.id]:
                omitidos.append((pt, (
                    f'{numero_credito}: monto {clp(monto)} excede el saldo '
                    f'disponible {clp(restante_por_credito[credito.id])} '
                    f'(considerando otras reparaciones de esta corrida) — '
                    f'revisar a mano'
                )))
                continue
            restante_por_credito[credito.id] -= monto

            registrado_por = User.objects.filter(
                username__iexact=(ticket.responsable or '').strip()
            ).first() or usuario_fallback
            if not registrado_por:
                omitidos.append((pt, (
                    f"responsable '{ticket.responsable}' no existe como usuario "
                    f'(use --usuario para dar un fallback)'
                )))
                continue

            a_reparar.append((pt, credito, referencia, registrado_por, fecha_venta))

        total = sum(Decimal(str(pt.monto)) for pt, _, _, _, _ in a_reparar)
        self.stdout.write('')
        self.stdout.write(f'  Consumos POS con credito revisados : {pagos_ticket.count()}')
        self.stdout.write(f'  Ya registrados (idempotencia)      : {ya_registrados}')
        self.stdout.write(self.style.WARNING(
            f'  A REPARAR (sin PagoCreditoTrabajador): {len(a_reparar)}  '
            f'total {clp(total)}'
        ))

        if a_reparar:
            self.stdout.write('')
            self.stdout.write(
                f'  {"credito":15s} {"beneficiario":25s} {"boleta":26s} '
                f'{"ticket":8s} {"fecha":11s} {"suc":5s} {"monto":>10s}'
            )
            for pt, credito, referencia, _, fecha_venta in a_reparar:
                t = pt.ticket
                alias = getattr(t.sucursal, 'alias', '') or str(t.sucursal_id)
                nombre = (credito.nombre_beneficiario or '')[:25]
                self.stdout.write(
                    f'  {credito.numero_credito:15s} {nombre:25s} {referencia[:26]:26s} '
                    f'{t.correlativo!s:8s} {fecha_venta.strftime("%d/%m/%Y"):11s} '
                    f'{alias:5s} {clp(pt.monto):>10s}'
                )

        if omitidos:
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE(f'  --- Omitidos ({len(omitidos)}) ---'))
            for pt, motivo in omitidos:
                self.stdout.write(
                    f'  ~ ticket {pt.ticket.correlativo} pago {pt.id}: {motivo}'
                )

        if not aplicar:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                '[DRY-RUN] No se escribio nada. Repita con --apply para aplicar.'
            ))
            return

        if not a_reparar:
            self.stdout.write(self.style.SUCCESS('>> No hay consumos que reparar.'))
            return

        reparados = 0
        for pt, credito, referencia, registrado_por, fecha_venta in a_reparar:
            ticket = pt.ticket
            monto = Decimal(str(pt.monto))
            with transaction.atomic():
                bloqueado = CreditoTrabajador.objects.select_for_update().get(
                    id=credito.id
                )
                # Re-chequeos bajo lock (mismo orden que el endpoint): las dos
                # redes de idempotencia, el estado y el saldo REAL del credito
                # — este ultimo ya incluye los debitos creados por las vueltas
                # anteriores de este mismo loop.
                if PagoCreditoTrabajador.objects.filter(
                    credito=bloqueado, referencia_pago=referencia, monto_pago=monto,
                ).exists() or self._pago_por_correlativo_existe(
                    bloqueado, ticket.correlativo
                ):
                    self.stdout.write(self.style.WARNING(
                        f'  ~ {bloqueado.numero_credito}: ya registrado, se omite'
                    ))
                    continue
                if bloqueado.estado not in ESTADOS_DEBITABLES:
                    self.stdout.write(self.style.WARNING(
                        f'  ~ {bloqueado.numero_credito}: cambio a estado '
                        f'{bloqueado.estado} durante el proceso, se omite'
                    ))
                    continue
                disponible = Decimal(str(
                    bloqueado.monto_aprobado or bloqueado.monto_solicitado or 0
                )) - Decimal(str(bloqueado.monto_pagado or 0))
                if monto > disponible:
                    self.stdout.write(self.style.WARNING(
                        f'  ~ {bloqueado.numero_credito}: saldo insuficiente '
                        f'({clp(disponible)}) para {clp(monto)}, se omite'
                    ))
                    continue

                pago = PagoCreditoTrabajador.objects.create(
                    credito=bloqueado,
                    monto_pago=monto,
                    fecha_pago=fecha_venta,
                    metodo_pago='CREDITO_TRABAJADOR',
                    referencia_pago=referencia,
                    sucursal_cobro=ticket.sucursal,
                    observaciones=(
                        f'Compra en POS - Ticket #{ticket.correlativo} - '
                        f'Boleta: {referencia} '
                        f'[REPARADO por manage.py reparar_consumos_credito_huerfanos: '
                        f'el POS cobro la venta pero usar-en-venta no registro el debito]'
                    ),
                    registrado_por=registrado_por,
                )
                # PagoCreditoTrabajador.save() ya recalculo monto_pagado y
                # estado del credito (PAGADO si el cupo quedo agotado).
                bloqueado.refresh_from_db()
            reparados += 1
            logger.info(
                'reparar_consumos_credito_huerfanos: %s debitado %s por ticket %s '
                '(pago_id=%s, estado credito=%s, saldo=%s)',
                bloqueado.numero_credito, monto, ticket.correlativo,
                pago.id, bloqueado.estado, bloqueado.saldo_pendiente,
            )
            self.stdout.write(self.style.SUCCESS(
                f'  + {bloqueado.numero_credito}: pago {pago.numero_pago} por '
                f'{clp(monto)} (ticket {ticket.correlativo}) — estado '
                f'{bloqueado.estado}, saldo {clp(bloqueado.saldo_pendiente)}'
            ))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'>> {reparados} consumos reparados.'
        ))

    @staticmethod
    def _pago_por_correlativo_existe(credito, correlativo):
        """Red 2 de idempotencia: algun pago del credito menciona este ticket
        en las observaciones, con el numero COMPLETO (borde no-digito o fin de
        texto, para que 'Ticket #12298' no matchee con 'Ticket #122980')."""
        return PagoCreditoTrabajador.objects.filter(
            credito=credito,
            observaciones__regex=rf'Ticket #{correlativo}(\D|$)',
        ).exists()

    def _referencia_boleta(self, ticket, fecha_venta):
        """Misma referencia que habria construido el POS: '<tipo>-<folio>' del
        DTE de VENTA emitido, o 'TKT-<correlativo>' si no hay documento. El
        folio NO es unico en la tabla Dte (comparte numeracion con documentos
        recibidos y otras sucursales), por eso se acota a tipo de venta +
        sucursal + fecha del cobro."""
        if ticket.dte_generado and ticket.folio_dte:
            dte = Dte.objects.filter(
                numero_documento=ticket.folio_dte,
                sucursal_id=ticket.sucursal_id,
                tipo_documento__in=TIPOS_DTE_VENTA,
                fecha_emision=fecha_venta,
            ).order_by('id').first()
            if dte:
                return f'{dte.tipo_documento}-{dte.numero_documento}'
            # Sin fila Dte ubicable: derivar el tipo del propio ticket en vez
            # de asumir boleta (una factura con referencia equivocada rompe la
            # idempotencia del endpoint en un reintento futuro).
            if 'FACTURA' in (ticket.tipo_dte or '').upper():
                return f'FACTURA ELECTRONICA-{ticket.folio_dte}'
            return f'BOLETA ELECTRONICA-{ticket.folio_dte}'
        return f'TKT-{ticket.correlativo}'
