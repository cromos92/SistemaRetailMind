"""
Management command: Procesa alertas pendientes (mark-then-evaluate).
Uso: python manage.py evaluar_alertas_pendientes

Pensado para cron cada 30 minutos.
"""
import time

from django.core.management.base import BaseCommand

from ...services.prediccion_compras import procesar_pendientes_reevaluacion


class Command(BaseCommand):
    help = 'Procesa productos marcados por señales de venta para reevaluar alertas de velocidad y quiebre de talle'

    def handle(self, *args, **options):
        from ...models.predicciones import PendienteReevaluacion

        pendientes_count = PendienteReevaluacion.objects.filter(procesado=False).count()
        if pendientes_count == 0:
            self.stdout.write('Sin pendientes de reevaluación.')
            return

        inicio = time.time()
        self.stdout.write(
            self.style.NOTICE(f'Procesando {pendientes_count} pendientes...')
        )

        resultado = procesar_pendientes_reevaluacion()

        elapsed = round(time.time() - inicio, 2)
        self.stdout.write(
            self.style.SUCCESS(
                f'Listo en {elapsed}s — '
                f'{resultado["velocidad"]} alertas velocidad, '
                f'{resultado["quiebre"]} alertas quiebre'
            )
        )
