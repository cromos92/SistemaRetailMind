"""
Repara cotizaciones que quedaron marcadas como FACTURADAS sin documento
tributario real ("cotizaciones zombi").

Origen del problema
-------------------
El botón "Convertir a Factura" del módulo de cotizaciones llamaba a
`convertir_cotizacion_factura`, que marcaba `facturada=True` con un
`numero_factura` inventado con formato "F-COT-YYYYMM-NNNN", y RECIÉN DESPUÉS
redirigía al POS. Pero al llegar al POS:

  - `cargar_cotizacion_como_ticket` exige `esta_vigente`, que es False cuando
    `facturada=True` → "Solo se pueden facturar cotizaciones vigentes".
  - `registrar_pagos_ticket` la rechaza con "ya fue facturada".

Resultado: la cotización queda FACTURADA sin DTE, sin stock descontado y sin
movimientos de inventario. Tampoco se puede anular, porque `anular_cotizacion`
bloquea las cotizaciones facturadas.

Qué hace este comando
---------------------
Detecta esas cotizaciones y las devuelve a VIGENTE para que se puedan facturar
de verdad por el flujo corregido.

Criterio de detección (deben cumplirse TODOS):
  1. `facturada=True` o `estado=FACTURADA`.
  2. `numero_factura` vacío o con el prefijo sintético "F-COT".
  3. No existe ningún `Movimientos_Producto` con
     `referencia_externa=numero_cotizacion`.

El discriminador real es el criterio 2: cuando el POS factura de verdad, guarda
en `numero_factura` el número del DTE emitido (numérico), nunca "F-COT-…".

El criterio 3 es un guard adicional para el caso de despacho diferido: esos
movimientos sí se registran con `referencia_externa=numero_cotizacion` (ver
`asignar_sku_pendiente`), así que si ya se despachó algo contra la cotización,
no se toca. OJO: los movimientos de la venta normal usan `DTE_<folio>` /
`TICKET_<n>` como referencia, así que el criterio 3 NO los detecta — para esos
casos la protección es el criterio 2.

NO borra nada: solo revierte campos de estado y deja un `Historial_Cotizacion`
explicando la reparación.

Uso:
    python manage.py reparar_cotizaciones_zombi                  # dry-run
    python manage.py reparar_cotizaciones_zombi --sucursal 5     # una sucursal
    python manage.py reparar_cotizaciones_zombi --apply          # aplica
"""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from app.models import (
    Cotizacion_Empresa,
    Historial_Cotizacion,
    Movimientos_Producto,
)

logger = logging.getLogger('app')

PREFIJO_SINTETICO = 'F-COT'


class Command(BaseCommand):
    help = (
        'Revierte a VIGENTE las cotizaciones marcadas FACTURADA sin DTE real '
        '(numero_factura "F-COT-..." y sin movimientos de stock). '
        'Por defecto dry-run; usa --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Aplica los cambios. Sin este flag solo lista (dry-run).',
        )
        parser.add_argument(
            '--sucursal', type=int, default=None,
            help='Limita la reparación a una sucursal.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Procesa como máximo N cotizaciones (para probar).',
        )
        parser.add_argument(
            '--usuario', type=str, default=None,
            help=(
                'Username al que se atribuye la reparación en el historial. '
                'Por defecto usa el primer superusuario.'
            ),
        )

    def handle(self, *args, **options):
        aplicar = options['apply']
        sucursal_id = options['sucursal']
        limite = options['limit']

        # Historial_Cotizacion.usuario NO es nullable, así que necesitamos un
        # usuario real para dejar la traza.
        usuario_traza = None
        if aplicar:
            Usuario = get_user_model()
            if options['usuario']:
                usuario_traza = Usuario.objects.filter(
                    username=options['usuario']
                ).first()
                if not usuario_traza:
                    raise CommandError(
                        f'No existe el usuario "{options["usuario"]}".'
                    )
            else:
                usuario_traza = Usuario.objects.filter(
                    is_superuser=True
                ).order_by('id').first()
                if not usuario_traza:
                    raise CommandError(
                        'No hay superusuarios para atribuir la reparación. '
                        'Pasá --usuario <username>.'
                    )

        qs = Cotizacion_Empresa.objects.filter(
            Q(facturada=True) | Q(estado=Cotizacion_Empresa.ESTADO_FACTURADA)
        ).filter(
            Q(numero_factura__isnull=True)
            | Q(numero_factura='')
            | Q(numero_factura__startswith=PREFIJO_SINTETICO)
        ).select_related('sucursal', 'cliente').order_by('id')

        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)

        candidatas = list(qs[:limite] if limite else qs)

        if not candidatas:
            self.stdout.write(self.style.SUCCESS(
                'No hay cotizaciones zombi que reparar.'
            ))
            return

        # Guard adicional: descartar las que ya despacharon stock contra la
        # cotización (despacho diferido). Ver nota del criterio 3 arriba.
        numeros = [c.numero_cotizacion for c in candidatas]
        con_movimientos = set(
            Movimientos_Producto.objects
            .filter(referencia_externa__in=numeros)
            .values_list('referencia_externa', flat=True)
        )

        a_reparar = [c for c in candidatas if c.numero_cotizacion not in con_movimientos]
        descartadas = [c for c in candidatas if c.numero_cotizacion in con_movimientos]

        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no se escribe nada)'
        self.stdout.write(self.style.WARNING(f'=== {modo} ==='))
        self.stdout.write(
            f'Candidatas por estado/número: {len(candidatas)}  |  '
            f'Con movimientos de stock (se respetan): {len(descartadas)}  |  '
            f'A reparar: {len(a_reparar)}'
        )

        for cot in descartadas:
            self.stdout.write(self.style.NOTICE(
                f'  · OMITIDA {cot.numero_cotizacion} — tiene movimientos de stock, '
                f'se facturó de verdad (numero_factura={cot.numero_factura!r})'
            ))

        if not a_reparar:
            self.stdout.write(self.style.SUCCESS('Nada que reparar.'))
            return

        reparadas = 0
        for cot in a_reparar:
            self.stdout.write(
                f'  → {cot.numero_cotizacion} | {cot.sucursal.alias if cot.sucursal else "?"} | '
                f'{cot.cliente.nombre if cot.cliente else "?"} | '
                f'${int(cot.total):,} | numero_factura={cot.numero_factura!r} | '
                f'despacho={cot.estado_despacho!r}'
            )

            if not aplicar:
                continue

            try:
                with transaction.atomic():
                    bloqueada = (
                        Cotizacion_Empresa.objects
                        .select_for_update()
                        .get(pk=cot.pk)
                    )
                    # Revalidar bajo lock: otra sesión pudo facturarla de verdad.
                    if Movimientos_Producto.objects.filter(
                        referencia_externa=bloqueada.numero_cotizacion
                    ).exists():
                        self.stdout.write(self.style.NOTICE(
                            f'    (omitida: aparecieron movimientos de stock)'
                        ))
                        continue

                    datos_anteriores = {
                        'estado': bloqueada.estado,
                        'facturada': bloqueada.facturada,
                        'numero_factura': bloqueada.numero_factura,
                        'fecha_facturacion': (
                            bloqueada.fecha_facturacion.isoformat()
                            if bloqueada.fecha_facturacion else None
                        ),
                        'estado_despacho': bloqueada.estado_despacho,
                    }

                    bloqueada.facturada = False
                    bloqueada.numero_factura = None
                    bloqueada.fecha_facturacion = None
                    bloqueada.estado_despacho = None
                    # save() reevalúa la vigencia por fecha: si ya venció, la
                    # deja VENCIDA en vez de VIGENTE (comportamiento correcto).
                    bloqueada.estado = Cotizacion_Empresa.ESTADO_VIGENTE
                    bloqueada.save()

                    Historial_Cotizacion.objects.create(
                        cotizacion=bloqueada,
                        usuario=usuario_traza,
                        accion='MODIFICADA',
                        descripcion=(
                            'Reparación automática: estaba marcada como FACTURADA '
                            'sin documento tributario ni movimientos de stock '
                            '(bug del flujo "Convertir a Factura"). '
                            f'Revertida a {bloqueada.estado} para poder facturarla.'
                        ),
                        datos_anteriores=datos_anteriores,
                        datos_nuevos={
                            'estado': bloqueada.estado,
                            'facturada': False,
                        },
                    )

                reparadas += 1
                logger.info(
                    'Cotizacion zombi reparada numero=%s estado_final=%s',
                    bloqueada.numero_cotizacion, bloqueada.estado,
                )
            except Exception:
                logger.exception(
                    'Error reparando cotizacion zombi numero=%s', cot.numero_cotizacion
                )
                self.stdout.write(self.style.ERROR(
                    f'    ERROR reparando {cot.numero_cotizacion} (ver logs)'
                ))

        if aplicar:
            self.stdout.write(self.style.SUCCESS(
                f'Listo: {reparadas}/{len(a_reparar)} cotizaciones reparadas.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'Dry-run: {len(a_reparar)} cotizaciones se repararían. '
                'Volvé a correr con --apply para aplicar.'
            ))
