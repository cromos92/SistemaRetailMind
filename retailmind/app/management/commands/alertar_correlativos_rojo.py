"""
Alerta por correo cuando un correlativo queda en rojo (agotado o crítico).

Por defecto SIMULA (igual que enviar_recordatorios_requerimientos.py): sólo
muestra qué correo mandaría y a quién. Para mandarlo de verdad hay que pasar
--enviar.

Uso manual:
    python manage.py alertar_correlativos_rojo             # dry-run
    python manage.py alertar_correlativos_rojo --enviar     # manda de verdad

En producción corre solo, una vez al día, desde run_scheduler.py — este
comando existe aparte para poder probarlo a mano o dispararlo desde un cron
externo sin depender del worker de loop infinito.
"""
from django.core.management.base import BaseCommand

from app.services.correlativos_service import alertar_correlativos_en_rojo


class Command(BaseCommand):
    help = ('Manda un correo con los correlativos agotados o críticos. '
            'Por defecto solo simula; usa --enviar para mandarlo de verdad.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--enviar', action='store_true',
            help='Manda el correo de verdad (sin esto solo muestra qué haría)')

    def handle(self, *args, **options):
        resultado = alertar_correlativos_en_rojo(enviar=options['enviar'])

        total = resultado['total_en_rojo']
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Sin correlativos en rojo. No hay nada que avisar.'))
            return

        self.stdout.write(f'{total} correlativo(s) en rojo:')
        for c in resultado['correlativos']:
            etiqueta = 'AGOTADO' if c['nivel_alerta'] == 'agotado' else 'CRÍTICO'
            self.stdout.write(
                f"  - {c['sucursal_alias']} / {c['tipo_dte']}: "
                f"{c['disponibles']} disponibles ({etiqueta})"
            )

        motivo = resultado['motivo']
        if motivo == 'dry_run':
            self.stdout.write(self.style.WARNING(
                '\nDry-run: no se mandó ningún correo. Agregá --enviar para mandarlo de verdad.'
            ))
        elif motivo == 'sin_destinatarios_configurados':
            self.stdout.write(self.style.ERROR(
                '\nNo se mandó nada: CORRELATIVOS_ALERTA_EMAILS está vacío en settings/env.'
            ))
        elif motivo == 'enviado':
            self.stdout.write(self.style.SUCCESS('\nCorreo enviado.'))
        elif motivo == 'enviado_con_errores':
            self.stdout.write(self.style.WARNING(
                f"\nCorreo enviado con errores: {resultado.get('errores')}"
            ))
