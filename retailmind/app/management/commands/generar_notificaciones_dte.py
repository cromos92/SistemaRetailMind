"""
Comando de Django para generar notificaciones de DTEs pendientes.
Crea notificaciones para DTEs existentes que están en estado EMITIDO
y tienen receptor diferente al emisor.
"""
from django.core.management.base import BaseCommand
from django.db import models
from app.models import Dte, NotificacionDTE


class Command(BaseCommand):
    help = 'Genera notificaciones para DTEs pendientes de recepcionar'

    def add_arguments(self, parser):
        parser.add_argument(
            '--receptor-id',
            type=int,
            help='ID de la empresa receptora (opcional, si no se especifica procesa todas)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular sin crear notificaciones'
        )

    def handle(self, *args, **options):
        receptor_id = options.get('receptor_id')
        dry_run = options.get('dry_run', False)
        
        # Query base: DTEs emitidos con receptor diferente al emisor
        dtes_query = Dte.objects.filter(
            estado_dte='EMITIDO'
        ).exclude(
            receptor__isnull=True
        ).exclude(
            emisor_id=models.F('receptor_id')
        ).select_related('emisor', 'receptor')
        
        if receptor_id:
            dtes_query = dtes_query.filter(receptor_id=receptor_id)
        
        total_dtes = dtes_query.count()
        self.stdout.write(f"DTEs pendientes encontrados: {total_dtes}")
        
        creadas = 0
        existentes = 0
        errores = 0
        
        for dte in dtes_query:
            # Verificar si ya existe una notificación para este DTE
            existe = NotificacionDTE.objects.filter(
                dte=dte,
                empresa_receptora=dte.receptor
            ).exists()
            
            if existe:
                existentes += 1
                continue
            
            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Crearía notificación para DTE #{dte.numero_documento} "
                    f"({dte.emisor.nombre} -> {dte.receptor.nombre})"
                )
                creadas += 1
                continue
            
            try:
                notif = NotificacionDTE.crear_notificacion_dte_emitido(dte)
                if notif:
                    creadas += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[OK] Notificacion creada para DTE #{dte.numero_documento}"
                        )
                    )
                else:
                    existentes += 1
            except Exception as e:
                errores += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"[ERROR] Error en DTE #{dte.numero_documento}: {str(e)}"
                    )
                )
        
        # Resumen
        self.stdout.write("\n" + "="*50)
        self.stdout.write(f"Total DTEs procesados: {total_dtes}")
        self.stdout.write(self.style.SUCCESS(f"Notificaciones creadas: {creadas}"))
        self.stdout.write(f"Ya existían: {existentes}")
        if errores:
            self.stdout.write(self.style.ERROR(f"Errores: {errores}"))
