"""
Django management command para actualizar estados de DTEs migrados desde Laravel.

Los DTEs migrados quedaron con estado 'EMITIDO' pero en realidad ya fueron
procesados en el sistema antiguo. Este comando los marca como 'RECEPCIONADO_COMPLETO'
para que no aparezcan como facturas pendientes en el modal de Recepción de Productos.

Uso:
    python manage.py actualizar_dtes_migrados
    python manage.py actualizar_dtes_migrados --dry-run
    python manage.py actualizar_dtes_migrados --fecha-corte 2025-01-01
    python manage.py actualizar_dtes_migrados --solo-contar
"""

from datetime import date
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.functions import ExtractYear

from app.models import Dte


class Command(BaseCommand):
    help = 'Actualiza estados de DTEs migrados desde Laravel a RECEPCIONADO_COMPLETO'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular sin guardar cambios'
        )
        parser.add_argument(
            '--fecha-corte',
            type=str,
            default='2025-01-01',
            help='Fecha de corte (YYYY-MM-DD). DTEs anteriores a esta fecha se marcarán como recepcionados. Default: 2025-01-01'
        )
        parser.add_argument(
            '--solo-contar',
            action='store_true',
            help='Solo mostrar conteo de DTEs por estado, sin actualizar'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        fecha_corte_str = options.get('fecha_corte', '2025-01-01')
        solo_contar = options.get('solo_contar', False)
        
        # Parsear fecha de corte
        try:
            partes = fecha_corte_str.split('-')
            fecha_corte = date(int(partes[0]), int(partes[1]), int(partes[2]))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Fecha invalida: {fecha_corte_str}. Use formato YYYY-MM-DD'))
            return
        
        self.stdout.write('=' * 70)
        self.stdout.write(self.style.SUCCESS('[ACTUALIZACION DE DTEs MIGRADOS]'))
        self.stdout.write('=' * 70)
        
        # Mostrar estado actual
        self.stdout.write('\n[ESTADO ACTUAL] DTEs de COMPRA:')
        conteo_estado = (
            Dte.objects.filter(tipo_transaccion='COMPRA')
            .values('estado_dte')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        for c in conteo_estado:
            self.stdout.write(f'   {c["estado_dte"]:25} : {c["total"]:>6}')
        
        # Conteo por año
        self.stdout.write('\n[POR AÑO] DTEs de COMPRA:')
        conteo_anio = (
            Dte.objects.filter(tipo_transaccion='COMPRA')
            .annotate(anio=ExtractYear('fecha_emision'))
            .values('anio')
            .annotate(total=Count('id'))
            .order_by('-anio')
        )
        for c in conteo_anio:
            marca = ' <-- Se actualizaran' if c['anio'] and c['anio'] < fecha_corte.year else ''
            self.stdout.write(f'   Anio {c["anio"]}: {c["total"]:>5} DTEs{marca}')
        
        if solo_contar:
            self.stdout.write('\n[OK] Modo solo-contar: no se realizaron cambios.')
            return
        
        # Obtener DTEs a actualizar
        # Criterios:
        # - tipo_transaccion = COMPRA
        # - estado_dte = EMITIDO (los que parecen "pendientes")
        # - fecha_emision < fecha_corte
        dtes_a_actualizar = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            estado_dte='EMITIDO',
            fecha_emision__lt=fecha_corte
        )
        
        total_a_actualizar = dtes_a_actualizar.count()
        
        self.stdout.write(f'\n[FECHA CORTE] {fecha_corte}')
        self.stdout.write(f'[DTEs A ACTUALIZAR] {total_a_actualizar:,}')
        
        if total_a_actualizar == 0:
            self.stdout.write(self.style.SUCCESS('\n[OK] No hay DTEs que actualizar.'))
            return
        
        # Mostrar muestra de DTEs a actualizar
        self.stdout.write('\n[MUESTRA] DTEs que se actualizaran:')
        muestra = dtes_a_actualizar.order_by('fecha_emision')[:5]
        for d in muestra:
            emisor_nombre = d.emisor.nombre if d.emisor else 'N/A'
            self.stdout.write(f'   ID:{d.id} Num:{d.numero_documento} Fecha:{d.fecha_emision} Emisor:{emisor_nombre[:30]}')
        
        if total_a_actualizar > 5:
            self.stdout.write(f'   ... y {total_a_actualizar - 5:,} mas')
        
        if dry_run:
            self.stdout.write(self.style.WARNING(f'\n[DRY-RUN] Se actualizarian {total_a_actualizar:,} DTEs a RECEPCIONADO_COMPLETO'))
            self.stdout.write('   Para ejecutar realmente, quita la opcion --dry-run')
            return
        
        # Confirmar antes de actualizar
        self.stdout.write(self.style.WARNING(f'\n[AVISO] Se van a actualizar {total_a_actualizar:,} DTEs a estado RECEPCIONADO_COMPLETO'))
        
        # Ejecutar actualización
        self.stdout.write('\n[PROCESANDO] Actualizando DTEs...')
        
        actualizados = dtes_a_actualizar.update(estado_dte='RECEPCIONADO_COMPLETO')
        
        self.stdout.write(self.style.SUCCESS(f'\n[OK] {actualizados:,} DTEs actualizados a RECEPCIONADO_COMPLETO'))
        
        # Mostrar estado final
        self.stdout.write('\n[ESTADO FINAL] DTEs de COMPRA:')
        conteo_estado_final = (
            Dte.objects.filter(tipo_transaccion='COMPRA')
            .values('estado_dte')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        for c in conteo_estado_final:
            self.stdout.write(f'   {c["estado_dte"]:25} : {c["total"]:>6}')
        
        self.stdout.write('\n' + '=' * 70)
        self.stdout.write(self.style.SUCCESS('[OK] Proceso completado'))
        self.stdout.write('=' * 70)
