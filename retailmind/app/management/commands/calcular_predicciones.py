"""
Management command: Pipeline completo de predicción de compras.
Uso: python manage.py calcular_predicciones [opciones]

Pensado para cron diario (ej: 3am).
"""
import time

from django.core.management.base import BaseCommand

from ...services.prediccion_compras import ejecutar_pipeline_completo


class Command(BaseCommand):
    help = 'Ejecuta el pipeline completo de predicción de compras: ABC-XYZ → velocidades → curvas → predicciones → sugerencias → alertas'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sucursal', type=int, default=None,
            help='ID de sucursal para filtrar (default: todas)',
        )
        parser.add_argument(
            '--temporada', type=str, default=None,
            help='Temporada específica (ej: verano)',
        )
        parser.add_argument(
            '--anio', type=int, default=None,
            help='Año específico',
        )
        parser.add_argument(
            '--solo-clasificacion', action='store_true',
            help='Solo ejecutar clasificación ABC-XYZ',
        )
        parser.add_argument(
            '--solo-alertas', action='store_true',
            help='Solo ejecutar evaluación de alertas',
        )

    def handle(self, *args, **options):
        inicio = time.time()
        self.stdout.write(self.style.NOTICE('Iniciando pipeline de predicción...'))

        resultados = ejecutar_pipeline_completo(
            temporada=options['temporada'],
            anio=options['anio'],
            sucursal_id=options['sucursal'],
            solo_clasificacion=options['solo_clasificacion'],
            solo_alertas=options['solo_alertas'],
        )

        elapsed = round(time.time() - inicio, 2)

        self.stdout.write('')
        for key, value in resultados.items():
            self.stdout.write(f'  {key}: {value}')
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Pipeline completado en {elapsed}s'))
