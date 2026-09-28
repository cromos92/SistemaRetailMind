"""
Worker de tareas periódicas para Digital Ocean App Platform.

App Platform NO tiene cron nativo para componentes (los "jobs" solo corren en el
deploy). La forma soportada de tener tareas recurrentes sin servicios extra es un
componente `worker` que ejecuta un proceso de larga vida. Este comando ES ese
proceso: un bucle que cada `--intervalo` segundos expira reservas y vales
vencidos, y una vez al día (a `SCHEDULER_HORA_PUNTOS`, hora Chile) expira los
lotes de puntos vencidos.

Despliegue (resumen; ver DESPLIEGUE_DIGITALOCEAN.md):
    Componente worker, misma imagen Docker que la web, run_command:
        cd retailmind && python manage.py run_scheduler
    instance_count: 1   (NO escalar: una sola instancia hace el trabajo)

También sirve para un cron externo (una pasada y termina):
    python manage.py run_scheduler --once

Todas las operaciones son idempotentes y no tocan el ledger salvo la expiración
de lotes (que crea movimientos EXPIRACION). Si una pasada falla, se loguea y el
bucle sigue: nunca se cae el worker por un error transitorio de BD.
"""
import logging
import os
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.services import campanas_service, correlativos_service, fidelizacion_service

logger = logging.getLogger('app')

INTERVALO_DEFAULT = int(os.environ.get('SCHEDULER_INTERVALO_SEG', '300'))   # 5 min
HORA_PUNTOS = int(os.environ.get('SCHEDULER_HORA_PUNTOS', '4'))             # 04:00 Chile
# Sincronizar + verificar las fotos de portada de los ecommerce en la pasada
# diaria. Apagar con SCHEDULER_SYNC_FOTOS=0. Antes era 100 % manual y en
# producción las integraciones llevaban meses sin sincronizarse.
SYNC_FOTOS = (os.environ.get('SCHEDULER_SYNC_FOTOS', '1') or '1').strip() != '0'


class Command(BaseCommand):
    help = ('Bucle de tareas periódicas (DO App Platform): expira reservas, vales '
            'y lotes de puntos vencidos.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--once', action='store_true',
            help='Corre una sola pasada (incluida la diaria) y termina. Para cron externo.',
        )
        parser.add_argument(
            '--intervalo', type=int, default=INTERVALO_DEFAULT,
            help=f'Segundos entre pasadas (default {INTERVALO_DEFAULT}).',
        )

    def handle(self, *args, **options):
        if options['once']:
            self._pasada(incluir_diario=True)
            return

        intervalo = max(30, int(options['intervalo']))
        ultimo_dia_puntos = None
        self.stdout.write(self.style.SUCCESS(
            f'>> Scheduler iniciado (intervalo={intervalo}s, '
            f'expiración de puntos a las {HORA_PUNTOS:02d}:00 Chile).'
        ))
        while True:
            try:
                ahora = timezone.localtime()
                incluir_diario = (
                    ahora.hour == HORA_PUNTOS and ultimo_dia_puntos != ahora.date()
                )
                self._pasada(incluir_diario=incluir_diario)
                if incluir_diario:
                    ultimo_dia_puntos = ahora.date()
            except Exception:
                logger.exception('Pasada del scheduler falló (continúa el bucle)')
            time.sleep(intervalo)

    def _pasada(self, *, incluir_diario):
        reservas = fidelizacion_service.expirar_reservas_vencidas()
        vales = fidelizacion_service.expirar_vales_vencidos()
        if reservas or vales:
            logger.info('Scheduler: %s reservas y %s vales expirados.', reservas, vales)
        campanas = campanas_service.cerrar_campanas_vencidas()
        if campanas:
            logger.info('Scheduler: %s campañas de liquidación cerradas por vencimiento.', campanas)
        if incluir_diario:
            lotes = fidelizacion_service.expirar_lotes_vencidos()
            logger.info('Scheduler (diario): %s puntos expirados.', lotes)
            self.stdout.write(self.style.SUCCESS(
                f'   pasada diaria: {lotes} puntos expirados.'
            ))
            try:
                alerta = correlativos_service.alertar_correlativos_en_rojo(enviar=True)
                logger.info(
                    'Scheduler (diario): correlativos en rojo=%s, correo=%s.',
                    alerta['total_en_rojo'], alerta['motivo'],
                )
            except Exception:
                # Un fallo acá (SMTP caído, etc.) no debe tumbar el resto del
                # scheduler ni impedir que mañana se vuelva a intentar.
                logger.exception('Scheduler (diario): falló la alerta de correlativos en rojo')
            if SYNC_FOTOS:
                try:
                    from app.services.verificacion_fotos_service import (
                        sincronizar_y_verificar_todas,
                    )
                    fotos = sincronizar_y_verificar_todas()
                    self.stdout.write(self.style.SUCCESS(
                        f'   fotos ecommerce: {len(fotos)} integración(es) sincronizada(s) y verificada(s).'
                    ))
                except Exception:
                    # Un ecommerce caído no debe frenar el resto de la pasada diaria.
                    logger.exception('Scheduler (diario): falló la sincronización de fotos ecommerce')
