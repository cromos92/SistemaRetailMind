"""
Comando de Django para limpiar créditos duplicados

Uso:
    python manage.py limpiar_creditos_duplicados
    
    # Con confirmación automática (sin preguntar)
    python manage.py limpiar_creditos_duplicados --auto
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from app.models import CreditoTrabajador


class Command(BaseCommand):
    help = 'Limpia créditos con números duplicados renumerándolos automáticamente'

    def add_arguments(self, parser):
        parser.add_argument(
            '--auto',
            action='store_true',
            help='Ejecutar sin pedir confirmación',
        )

    def handle(self, *args, **options):
        auto = options['auto']
        
        self.stdout.write(self.style.MIGRATE_HEADING('=' * 70))
        self.stdout.write(self.style.MIGRATE_HEADING('LIMPIEZA DE CRÉDITOS DUPLICADOS'))
        self.stdout.write(self.style.MIGRATE_HEADING('=' * 70))
        
        # Buscar duplicados
        duplicados = (
            CreditoTrabajador.objects
            .values('numero_credito')
            .annotate(count=Count('id'))
            .filter(count__gt=1)
            .order_by('numero_credito')
        )
        
        if not duplicados:
            self.stdout.write(self.style.SUCCESS('\n✅ No se encontraron créditos duplicados'))
            self.stdout.write('   El sistema está correcto.')
            return
        
        # Mostrar duplicados encontrados
        self.stdout.write(f'\n⚠️  Se encontraron {len(duplicados)} número(s) de crédito duplicado(s):')
        self.stdout.write('')
        
        total_afectados = 0
        for dup in duplicados:
            numero = dup['numero_credito']
            cantidad = dup['count']
            total_afectados += cantidad
            
            self.stdout.write(f'   • {numero}: {cantidad} veces')
            
            # Mostrar detalles
            creditos = CreditoTrabajador.objects.filter(numero_credito=numero).order_by('id')
            for i, c in enumerate(creditos, 1):
                self.stdout.write(
                    f'     [{i}] ID: {c.id} | {c.trabajador.nombre[:30]:30} | '
                    f'${c.monto_solicitado:>10,.0f} | {c.get_estado_display()}'
                )
        
        self.stdout.write('')
        self.stdout.write(f'Total de créditos afectados: {total_afectados}')
        
        # Pedir confirmación (si no es auto)
        if not auto:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('ESTRATEGIA:'))
            self.stdout.write('   • Se mantendrá el crédito más antiguo (menor ID)')
            self.stdout.write('   • Los créditos duplicados serán renumerados automáticamente')
            self.stdout.write('')
            
            confirmacion = input('¿Desea continuar? (s/n): ')
            if confirmacion.lower() != 's':
                self.stdout.write(self.style.ERROR('\n❌ Operación cancelada'))
                return
        
        # Procesar renumeración
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_LABEL('Procesando...'))
        self.stdout.write('-' * 70)
        
        try:
            with transaction.atomic():
                creditos_renumerados = 0
                
                for dup in duplicados:
                    numero = dup['numero_credito']
                    creditos = CreditoTrabajador.objects.filter(numero_credito=numero).order_by('id')
                    
                    # Mantener el primero
                    primero = creditos.first()
                    self.stdout.write(f'\n✓ Manteniendo: {numero} (ID: {primero.id})')
                    
                    # Renumerar los demás
                    for credito in creditos[1:]:
                        # Extraer año
                        try:
                            año = int(numero.split('-')[1])
                        except:
                            año = credito.fecha_solicitud.year
                        
                        # Buscar siguiente número disponible
                        contador = 1
                        while True:
                            nuevo_numero = f"CR-{año}-{contador:04d}"
                            
                            # Verificar que no exista
                            existe = CreditoTrabajador.objects.filter(
                                numero_credito=nuevo_numero
                            ).exclude(id=credito.id).exists()
                            
                            if not existe:
                                break
                            
                            contador += 1
                            
                            if contador > 9999:
                                raise Exception(f"No se pudo encontrar número disponible para año {año}")
                        
                        # Renumerar
                        antiguo = credito.numero_credito
                        credito.numero_credito = nuevo_numero
                        credito.save()
                        
                        self.stdout.write(
                            f'  ✏️  ID {credito.id}: {antiguo} → {nuevo_numero}'
                        )
                        creditos_renumerados += 1
                
                # Verificación final
                self.stdout.write('')
                self.stdout.write('-' * 70)
                self.stdout.write(self.style.SUCCESS(f'\n✅ Se renumeraron {creditos_renumerados} crédito(s)'))
                
                # Verificar que no queden duplicados
                total = CreditoTrabajador.objects.count()
                unicos = CreditoTrabajador.objects.values('numero_credito').distinct().count()
                
                self.stdout.write('')
                self.stdout.write('VERIFICACIÓN FINAL:')
                self.stdout.write(f'   Total de créditos: {total}')
                self.stdout.write(f'   Números únicos: {unicos}')
                
                if total == unicos:
                    self.stdout.write(self.style.SUCCESS('   ✅ Todos los números son únicos'))
                else:
                    self.stdout.write(
                        self.style.ERROR(f'   ⚠️  Aún hay {total - unicos} duplicado(s)')
                    )
                
                self.stdout.write('')
                self.stdout.write(self.style.MIGRATE_HEADING('=' * 70))
                self.stdout.write(self.style.SUCCESS('OPERACIÓN COMPLETADA EXITOSAMENTE'))
                self.stdout.write(self.style.MIGRATE_HEADING('=' * 70))
                
        except Exception as e:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('=' * 70))
            self.stdout.write(self.style.ERROR('❌ ERROR DURANTE LA RENUMERACIÓN:'))
            self.stdout.write(self.style.ERROR('=' * 70))
            self.stdout.write(self.style.ERROR(f'   {str(e)}'))
            self.stdout.write('')
            self.stdout.write('   Los cambios NO se guardaron (rollback automático)')
            self.stdout.write('   La base de datos permanece sin cambios')
            self.stdout.write(self.style.ERROR('=' * 70))
            raise

