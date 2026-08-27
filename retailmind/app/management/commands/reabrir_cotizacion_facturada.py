"""
Devuelve a VIGENTE una cotización marcada FACTURADA cuyo documento tributario
ya fue eliminado o anulado, para poder re-facturarla.

Para qué sirve
--------------
El módulo de cotizaciones no factura: lo hace el POS al cobrar. Cuando el DTE
emitido se elimina (`eliminar_documento_venta`) o se anula por NC total, la
cotización NO se toca y queda en un estado sin salida:

  * `cargar_cotizacion_como_ticket` la rechaza (exige `esta_vigente`, que
    incluye `not facturada`) → no se puede volver a facturar.
  * `editar_cotizacion` y `anular_cotizacion` bloquean las facturadas.
  * `reparar_cotizaciones_zombi` NO la detecta: exige `numero_factura` vacío o
    con prefijo `F-COT`, y acá hay un folio numérico real.

Caso que lo motivó: una cotización facturada por error con BOLETA PAPEL que
debía re-emitirse como FACTURA ELECTRONICA.

Guards (todos en `app/services/cotizacion_reapertura.py`)
--------------------------------------------------------
No reabre si el DTE sigue vigente, si quedan unidades de despacho diferido sin
revertir, o si el ticket de la venta no está ANULADO (o sea: si el stock no
volvió a bodega). Reabrir en cualquiera de esos casos permitiría vender dos
veces la misma mercadería.

NO borra nada: revierte campos de estado, renueva la vigencia y deja un
`Historial_Cotizacion` con el detalle.

Uso:
    # dry-run: dice qué haría y por qué se puede o no
    python manage.py reabrir_cotizacion_facturada --cotizacion COT-202608-0007

    # aplicar (motivo obligatorio)
    python manage.py reabrir_cotizacion_facturada --cotizacion COT-202608-0007 \
        --motivo "Facturada por error con boleta de papel, se re-emite factura" --apply

    # listar todas las candidatas de una sucursal, sin tocar nada
    python manage.py reabrir_cotizacion_facturada --listar --sucursal 5
"""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from app.models import Cotizacion_Empresa
from app.services.cotizacion_reapertura import (
    DIAS_VALIDEZ_POR_DEFECTO,
    evaluar_reapertura,
    reabrir_cotizacion,
)

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = (
        'Devuelve a VIGENTE una cotización FACTURADA cuyo DTE fue eliminado o '
        'anulado, para poder re-facturarla. Por defecto dry-run; usa --apply.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--cotizacion', type=str, default=None,
            help='numero_cotizacion (ej. COT-202608-0007) o ID numérico.',
        )
        parser.add_argument(
            '--listar', action='store_true',
            help='Lista las cotizaciones facturadas con documento muerto y sale.',
        )
        parser.add_argument(
            '--sucursal', type=int, default=None,
            help='Limita --listar a una sucursal.',
        )
        parser.add_argument(
            '--motivo', type=str, default=None,
            help='Motivo de la reapertura (obligatorio con --apply, 5+ caracteres).',
        )
        parser.add_argument(
            '--dias', type=int, default=DIAS_VALIDEZ_POR_DEFECTO,
            help=(
                'Días de validez a devolverle si ya venció '
                f'(default {DIAS_VALIDEZ_POR_DEFECTO}).'
            ),
        )
        parser.add_argument(
            '--usuario', type=str, default=None,
            help=(
                'Username al que se atribuye la reapertura en el historial. '
                'Por defecto, el primer superusuario.'
            ),
        )
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica el cambio. Sin este flag solo informa (dry-run).',
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        if options['listar']:
            return self._listar(options)

        if not options['cotizacion']:
            raise CommandError(
                'Indicá --cotizacion <numero|id>, o usá --listar para ver las candidatas.'
            )

        cot = self._resolver(options['cotizacion'])
        aplicar = options['apply']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{cot.numero_cotizacion} — {cot.sucursal.alias if cot.sucursal else "?"} — '
            f'{cot.cliente.nombre if cot.cliente else "?"}'
        ))
        self.stdout.write(
            f'  estado={cot.estado}  facturada={cot.facturada}  '
            f'numero_factura={cot.numero_factura!r}  total=${int(cot.total):,}'
        )
        doc = cot.dte
        self.stdout.write(
            '  documento: ' + (
                f'{doc.tipo_documento} #{doc.numero_documento} '
                f'(estado={doc.estado_dte}, descartado={doc.descartado})'
                if doc else 'SIN DTE ENLAZADO (zombi)'
            )
        )
        self.stdout.write(
            f'  despacho: facturadas={cot.unidades_facturadas} '
            f'despachadas={cot.unidades_despachadas} '
            f'pendientes={cot.unidades_pendientes_despacho} '
            f'validado={cot.despacho_validado}'
        )

        evaluacion = evaluar_reapertura(cot)

        for aviso in evaluacion['avisos']:
            self.stdout.write(self.style.NOTICE(f'  · aviso: {aviso}'))

        if not evaluacion['ok']:
            self.stdout.write(self.style.ERROR('\n  NO se puede reabrir:'))
            for b in evaluacion['bloqueos']:
                self.stdout.write(self.style.ERROR(f'    ✗ {b}'))
            self.stdout.write('')
            raise CommandError('Reapertura bloqueada (ver motivos arriba).')

        self.stdout.write(self.style.SUCCESS('\n  ✓ Se puede reabrir.'))

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '\n  DRY-RUN: no se escribió nada. Para aplicar:\n'
                f'    python manage.py reabrir_cotizacion_facturada '
                f'--cotizacion {cot.numero_cotizacion} '
                f'--motivo "..." --apply\n'
            ))
            return

        motivo = (options['motivo'] or '').strip()
        if len(motivo) < 5:
            raise CommandError(
                '--motivo es obligatorio con --apply (mínimo 5 caracteres): '
                'reabrir una cotización facturada es una corrección sobre un '
                'documento tributario y tiene que quedar justificada.'
            )

        usuario = self._resolver_usuario(options['usuario'])

        ok, payload = reabrir_cotizacion(
            cot, usuario, motivo, dias_validez=options['dias'],
        )
        if not ok:
            self.stdout.write(self.style.ERROR('\n  Reapertura rechazada al aplicar:'))
            for b in payload.get('bloqueos', []):
                self.stdout.write(self.style.ERROR(f'    ✗ {b}'))
            raise CommandError('No se aplicó ningún cambio.')

        self.stdout.write(self.style.SUCCESS(
            f'\n  REABIERTA: {payload["numero_cotizacion"]} → {payload["estado"]}, '
            f'validez hasta {payload["fecha_validez"]}'
            + ('  (validez renovada)' if payload['validez_renovada'] else '')
        ))
        self.stdout.write(
            '\n  Siguiente paso: en el POS, cargar la cotización y facturarla '
            'eligiendo el tipo de documento correcto.\n'
        )

    # ------------------------------------------------------------------
    def _listar(self, options):
        qs = (
            Cotizacion_Empresa.objects
            .filter(Q(facturada=True) | Q(estado=Cotizacion_Empresa.ESTADO_FACTURADA))
            .filter(
                Q(dte__isnull=True)
                | Q(dte__descartado=True)
                | Q(dte__estado_dte='ANULADO')
            )
            .select_related('dte', 'sucursal', 'cliente')
            .prefetch_related('items__skus_asociados')
            .order_by('sucursal__alias', '-fecha_facturacion')
        )
        if options['sucursal']:
            qs = qs.filter(sucursal_id=options['sucursal'])

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{"Cotizacion":<18} {"Suc":<8} {"Documento":<34} {"Pend":>5}  ¿Reabrible?'
        ))
        self.stdout.write('-' * 96)

        total = reabribles = 0
        for cot in qs:
            total += 1
            ev = evaluar_reapertura(cot)
            doc = cot.dte
            doc_txt = (
                f'{doc.tipo_documento} #{doc.numero_documento} '
                f'({"descartado" if doc.descartado else doc.estado_dte})'
                if doc else 'sin DTE (zombi)'
            )
            if ev['ok']:
                reabribles += 1
                veredicto = self.style.SUCCESS('SI')
            else:
                veredicto = self.style.ERROR('NO — ' + ev['bloqueos'][0][:56])
            self.stdout.write(
                f'{cot.numero_cotizacion:<18} '
                f'{(cot.sucursal.alias if cot.sucursal else "?"):<8} '
                f'{doc_txt:<34} '
                f'{cot.unidades_pendientes_despacho:>5}  {veredicto}'
            )

        self.stdout.write('-' * 96)
        self.stdout.write(
            f'Total con documento muerto: {total}  |  Reabribles ahora: {reabribles}'
        )
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                'Ninguna cotización facturada quedó sin documento vivo.'
            ))

    # ------------------------------------------------------------------
    def _resolver(self, referencia):
        referencia = referencia.strip()
        cot = (
            Cotizacion_Empresa.objects
            .select_related('dte', 'sucursal', 'cliente')
            .prefetch_related('items__skus_asociados')
            .filter(numero_cotizacion__iexact=referencia)
            .first()
        )
        if cot:
            return cot
        if referencia.isdigit():
            cot = (
                Cotizacion_Empresa.objects
                .select_related('dte', 'sucursal', 'cliente')
                .prefetch_related('items__skus_asociados')
                .filter(pk=int(referencia))
                .first()
            )
            if cot:
                return cot
        raise CommandError(f'No existe la cotización "{referencia}".')

    def _resolver_usuario(self, username):
        Usuario = get_user_model()
        if username:
            usuario = Usuario.objects.filter(username=username).first()
            if not usuario:
                raise CommandError(f'No existe el usuario "{username}".')
            return usuario
        usuario = Usuario.objects.filter(is_superuser=True).order_by('id').first()
        if not usuario:
            raise CommandError(
                'No hay superusuarios para atribuir la reapertura. Pasá --usuario <username>.'
            )
        return usuario
