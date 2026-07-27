# -*- coding: utf-8 -*-
"""
Caduca los CUPOS de crédito que quedaron sin usar más de N días.

REGLA (la misma que aplica el bloqueo en el POS, ver el bloque
"CADUCIDAD DEL CUPO SIN USAR" en `app/views_modulo_creditos.py`):

  * El reloj arranca cuando el cupo queda DISPONIBLE (`fecha_aprobacion`, y si
    no existe, `fecha_solicitud`).
  * Cada uso en el POS reinicia el reloj (configurable con
    CREDITOS_CADUCIDAD_RENUEVA_CON_USO=0).
  * Tope duro: nunca después de `fecha_vencimiento`.
  * Plazo por defecto 10 días, configurable con CREDITOS_DIAS_VIGENCIA_CUPO
    o con --dias.

QUÉ ESCRIBE Y QUÉ NO (importante, es plata por cobrar):

  * Escribe SOLO créditos NATIVOS del ERP (CR-*), ACTIVO/APROBADO, con CERO
    consumo en el POS: pasan a CANCELADO. Al no haberse consumido nada, no
    deben nada, así que sacarlos de la cartera es correcto.

  * NO toca los créditos PARCIALMENTE usados. Su remanente igual queda
    bloqueado para nuevas compras (la regla es calculada, no depende de este
    comando), pero cambiarles el estado los sacaría de
    `_calcular_cartera_creditos` -que sólo mira ACTIVO/APROBADO/PAGADO- y
    borraría de la cartera la deuda que sí generaron. Se listan como informativos.

  * NO toca los créditos IMPORTADOS (CP-*). Su monto no es un cupo: es
    mercadería ya retirada, o sea deuda. Cancelarlos borraría $84,7 MM de
    cartera. Su uso en el POS igual queda bloqueado por la regla calculada.

USO:
    python manage.py caducar_creditos_sin_uso                 # dry-run (default)
    python manage.py caducar_creditos_sin_uso --detalle
    python manage.py caducar_creditos_sin_uso --dias 15
    python manage.py caducar_creditos_sin_uso --desde-fecha 2026-08-01
    python manage.py caducar_creditos_sin_uso --apply         # ESCRIBE
"""
import logging
from datetime import date, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import CreditoTrabajador
from app.views_modulo_creditos import (
    METODOS_CONSUMO_CREDITO,
    _dias_vigencia_cupo,
    _es_credito_legacy,
    _estado_caducidad_cupo,
)

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = ('Caduca (CANCELADO) los cupos de credito nativos sin usar pasados N dias. '
            'Por defecto es dry-run: no escribe nada.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica los cambios. Sin este flag el comando solo reporta (dry-run).',
        )
        parser.add_argument(
            '--dias', type=int, default=None,
            help='Plazo de vigencia del cupo en dias. Default: CREDITOS_DIAS_VIGENCIA_CUPO (10).',
        )
        parser.add_argument(
            '--desde-fecha', type=str, default=None,
            help=('Solo creditos que quedaron disponibles desde esta fecha (AAAA-MM-DD). '
                  'Sirve para aplicar la regla "de aqui en adelante" en vez de retroactiva.'),
        )
        parser.add_argument(
            '--empresa-id', type=int, default=None,
            help='Limita el proceso a una empresa de origen.',
        )
        parser.add_argument(
            '--detalle', action='store_true',
            help='Lista credito por credito, no solo los totales.',
        )

    def handle(self, *args, **options):
        aplicar = options['apply']
        detalle = options['detalle']
        dias = options['dias'] if options['dias'] else _dias_vigencia_cupo()
        if dias <= 0:
            raise CommandError('--dias debe ser mayor a 0')

        desde = None
        if options['desde_fecha']:
            try:
                desde = datetime.strptime(options['desde_fecha'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--desde-fecha debe tener formato AAAA-MM-DD')

        hoy = timezone.localdate()
        etiqueta = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{etiqueta}] Caducidad de cupos - plazo {dias} dias - corte {hoy.strftime("%d/%m/%Y")}'
        ))
        if desde:
            self.stdout.write(f'  Alcance: solo cupos disponibles desde {desde.strftime("%d/%m/%Y")}')

        queryset = (
            CreditoTrabajador.objects
            .filter(estado__in=['ACTIVO', 'APROBADO'])
            .select_related('beneficiario', 'empresa_origen', 'sucursal')
            .prefetch_related('pagos')
            .order_by('numero_credito')
        )
        if options['empresa_id']:
            queryset = queryset.filter(empresa_origen_id=options['empresa_id'])

        a_cancelar = []       # nativos intactos -> se escriben
        parciales = []        # nativos parcialmente usados -> solo informativos
        legacy_bloqueados = []  # importados -> nunca se escriben
        revisados = 0

        for credito in queryset:
            revisados += 1
            estado = _estado_caducidad_cupo(credito, dias=dias)

            if _es_credito_legacy(credito.numero_credito):
                legacy_bloqueados.append((credito, estado))
                continue
            if not estado['caducado']:
                continue
            if desde and estado['fecha_base'] and estado['fecha_base'] < desde:
                continue

            if estado['consumido'] > 0:
                parciales.append((credito, estado))
            else:
                a_cancelar.append((credito, estado))

        monto_cancelar = sum(Decimal(str(e['remanente'])) for _, e in a_cancelar)
        monto_parciales = sum(Decimal(str(e['remanente'])) for _, e in parciales)
        monto_legacy = sum(Decimal(str(e['remanente'])) for _, e in legacy_bloqueados)

        def clp(monto):
            return '$' + f'{monto:,.0f}'.replace(',', '.')

        self.stdout.write('')
        self.stdout.write(f'  Creditos vivos revisados             : {revisados}')
        self.stdout.write(self.style.WARNING(
            f'  A CANCELAR (nativos, cupo intacto)   : {len(a_cancelar)}  '
            f'cupo {clp(monto_cancelar)}'
        ))
        self.stdout.write(
            f'  Bloqueados sin tocar (uso parcial)   : {len(parciales)}  '
            f'remanente {clp(monto_parciales)}'
        )
        self.stdout.write(
            f'  Bloqueados sin tocar (importados CP-): {len(legacy_bloqueados)}  '
            f'monto {clp(monto_legacy)}'
        )

        if detalle:
            self._listar('A CANCELAR', a_cancelar)
            self._listar('USO PARCIAL (no se escribe, solo se bloquea el remanente)', parciales)

        if not aplicar:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                '[DRY-RUN] No se escribio nada. Repita con --apply para aplicar.'
            ))
            self.stdout.write(
                '  Nota: el bloqueo del uso en el POS ya rige SIN este comando '
                '(la caducidad es una regla calculada). Este comando solo '
                'persiste el estado de los cupos intactos para que dejen de '
                'figurar como disponibles.'
            )
            return

        if not a_cancelar:
            self.stdout.write(self.style.SUCCESS('>> No hay creditos que cancelar.'))
            return

        marca = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
        cancelados = 0
        with transaction.atomic():
            for credito, estado in a_cancelar:
                bloqueado = CreditoTrabajador.objects.select_for_update().get(id=credito.id)
                if bloqueado.estado not in ('ACTIVO', 'APROBADO'):
                    continue
                # Relectura del consumo bajo lock: si alguien lo uso mientras
                # corria el comando, ya no corresponde cancelarlo.
                consumo = sum(
                    Decimal(str(p.monto_pago or 0))
                    for p in bloqueado.pagos.all()
                    if p.metodo_pago in METODOS_CONSUMO_CREDITO
                )
                if consumo > 0:
                    self.stdout.write(self.style.WARNING(
                        f'  ~ {bloqueado.numero_credito}: se uso durante el proceso, se omite'
                    ))
                    continue

                cupo_txt = f'{estado["remanente"]:,.0f}'.replace(',', '.')
                nota = (
                    f'\n[CUPO CADUCADO - {marca}] Sin uso en {dias} dias desde '
                    f'{estado["fecha_base"].strftime("%d/%m/%Y") if estado["fecha_base"] else "s/f"}. '
                    f'Limite: {estado["fecha_limite"].strftime("%d/%m/%Y") if estado["fecha_limite"] else "s/f"}. '
                    f'Cupo no utilizado: ${cupo_txt}. '
                    f'Aplicado por: manage.py caducar_creditos_sin_uso'
                )
                bloqueado.estado = 'CANCELADO'
                bloqueado.observaciones_solicitud = (
                    (bloqueado.observaciones_solicitud or '') + nota
                ).strip()
                bloqueado.save(update_fields=['estado', 'observaciones_solicitud', 'updated_at'])
                cancelados += 1
                logger.info(
                    'caducar_creditos_sin_uso: %s CANCELADO por cupo sin uso (limite=%s, cupo=%s)',
                    bloqueado.numero_credito, estado['fecha_limite'], estado['remanente'],
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'>> {cancelados} creditos marcados CANCELADO por cupo caducado.'
        ))

    def _listar(self, titulo, items):
        if not items:
            return
        self.stdout.write('')
        self.stdout.write(f'  --- {titulo} ({len(items)}) ---')
        self.stdout.write(
            f'  {"numero":16s} {"beneficiario":28s} {"disponible":11s} {"limite":11s} '
            f'{"consumido":>11s} {"remanente":>11s}'
        )
        for credito, estado in items[:200]:
            base = estado['fecha_base'].strftime('%d/%m/%Y') if estado['fecha_base'] else '-'
            limite = estado['fecha_limite'].strftime('%d/%m/%Y') if estado['fecha_limite'] else '-'
            nombre = (credito.nombre_beneficiario or '')[:28]
            consumido = f'{estado["consumido"]:,.0f}'.replace(',', '.')
            remanente = f'{estado["remanente"]:,.0f}'.replace(',', '.')
            self.stdout.write(
                f'  {credito.numero_credito:16s} {nombre:28s} {base:11s} {limite:11s} '
                f'{consumido:>11s} {remanente:>11s}'
            )
        if len(items) > 200:
            self.stdout.write(f'  ... y {len(items) - 200} mas')
