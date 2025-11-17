"""
Comando para corregir DTEs que no tienen métodos de pago registrados
pero tienen un ticket asociado con pagos

Uso: python manage.py corregir_pagos_dtes
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from app.models import Dte, Dte_Detalle_Pago, Ticket
import re


class Command(BaseCommand):
    help = 'Corrige DTEs sin métodos de pago copiándolos desde sus tickets asociados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar qué se haría sin hacer cambios',
        )
        parser.add_argument(
            '--sucursal',
            type=int,
            help='Filtrar solo por una sucursal específica',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        sucursal_id = options.get('sucursal')
        
        self.stdout.write(self.style.WARNING('=' * 80))
        self.stdout.write(self.style.WARNING('CORRECCIÓN DE PAGOS EN DTEs'))
        self.stdout.write(self.style.WARNING('=' * 80))
        
        if dry_run:
            self.stdout.write(self.style.NOTICE('MODO DRY-RUN: No se realizarán cambios en la BD'))
        
        # Buscar DTEs sin métodos de pago que tengan referencia a un ticket
        # Usamos annotate para contar los pagos asociados
        from django.db.models import Count
        dtes_query = Dte.objects.annotate(
            num_pagos=Count('dte_asociado')
        ).filter(
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            estado_pago='PAGADO',
            referencias__icontains='TICKET-',
            num_pagos=0  # Solo DTEs sin pagos registrados
        ).select_related('sucursal', 'vendedor')
        
        if sucursal_id:
            dtes_query = dtes_query.filter(sucursal_id=sucursal_id)
        
        dtes_sin_pagos = list(dtes_query)
        
        self.stdout.write(f"\nEncontrados {len(dtes_sin_pagos)} DTEs sin métodos de pago registrados")
        
        if not dtes_sin_pagos:
            self.stdout.write(self.style.SUCCESS('\n[OK] No hay DTEs que corregir'))
            return
        
        # Procesar cada DTE
        dtes_corregidos = 0
        dtes_sin_ticket = 0
        dtes_ticket_sin_pagos = 0
        errores = 0
        
        for dte in dtes_sin_pagos:
            # Extraer correlativo del ticket desde el campo referencias
            match = re.search(r'TICKET-(\d+)', dte.referencias or '')
            if not match:
                self.stdout.write(
                    self.style.WARNING(
                        f"[!] DTE #{dte.numero_documento} ({dte.tipo_documento}) - "
                        f"No se pudo extraer correlativo del ticket"
                    )
                )
                dtes_sin_ticket += 1
                continue
            
            ticket_correlativo = int(match.group(1))
            
            # Buscar el ticket
            try:
                ticket = Ticket.objects.prefetch_related('pagos').get(
                    correlativo=ticket_correlativo,
                    sucursal=dte.sucursal
                )
            except Ticket.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"[!] DTE #{dte.numero_documento} ({dte.tipo_documento}) - "
                        f"Ticket #{ticket_correlativo} no encontrado"
                    )
                )
                dtes_sin_ticket += 1
                continue
            
            # Verificar si el ticket tiene pagos
            pagos_ticket = list(ticket.pagos.all())
            if not pagos_ticket:
                self.stdout.write(
                    self.style.NOTICE(
                        f"[i] DTE #{dte.numero_documento} ({dte.tipo_documento}) - "
                        f"Ticket #{ticket_correlativo} no tiene pagos registrados"
                    )
                )
                dtes_ticket_sin_pagos += 1
                continue
            
            # Copiar pagos del ticket al DTE
            self.stdout.write(
                f"\n> DTE #{dte.numero_documento} ({dte.tipo_documento}) - "
                f"Copiando {len(pagos_ticket)} pago(s) desde Ticket #{ticket_correlativo}"
            )
            
            try:
                if not dry_run:
                    with transaction.atomic():
                        for pago in pagos_ticket:
                            Dte_Detalle_Pago.objects.create(
                                dte=dte,
                                metodo_pago=pago.metodo_pago,
                                monto=pago.monto,
                                tipo_tarjeta=pago.tipo_tarjeta or '',
                                voucher=pago.voucher or '',
                                notas=pago.notas or ''
                            )
                            self.stdout.write(
                                f"  OK {pago.metodo_pago}: ${pago.monto:,}"
                            )
                else:
                    for pago in pagos_ticket:
                        self.stdout.write(
                            f"  [DRY-RUN] {pago.metodo_pago}: ${pago.monto:,}"
                        )
                
                dtes_corregidos += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f"[X] Error al procesar DTE #{dte.numero_documento}: {str(e)}"
                    )
                )
                errores += 1
        
        # Resumen final
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.WARNING('RESUMEN'))
        self.stdout.write('=' * 80)
        self.stdout.write(f"DTEs procesados: {len(dtes_sin_pagos)}")
        
        if dry_run:
            self.stdout.write(self.style.NOTICE(f"DTEs que se corregirían: {dtes_corregidos}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"DTEs corregidos: {dtes_corregidos}"))
        
        self.stdout.write(self.style.WARNING(f"DTEs sin ticket encontrado: {dtes_sin_ticket}"))
        self.stdout.write(self.style.NOTICE(f"DTEs con ticket sin pagos: {dtes_ticket_sin_pagos}"))
        
        if errores > 0:
            self.stdout.write(self.style.ERROR(f"Errores: {errores}"))
        
        self.stdout.write('\n')
        
        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    'Para aplicar los cambios, ejecute el comando sin --dry-run'
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS('[OK] Proceso completado'))

