"""
Comando Django para probar integración Transbank POS SDK
Ejecutar: python manage.py test_transbank_pos
"""

from django.core.management.base import BaseCommand
from app.services.transbank_pos_sdk_service import POSService
from transbank.error.transbank_exception import TransbankException
import sys


class Command(BaseCommand):
    help = 'Prueba la integración con Transbank POS SDK'

    def add_arguments(self, parser):
        parser.add_argument(
            '--puerto',
            type=str,
            help='Puerto serial a usar (ej: COM3, /dev/ttyUSB0)',
        )
        parser.add_argument(
            '--venta',
            action='store_true',
            help='Realizar una venta de prueba',
        )
        parser.add_argument(
            '--monto',
            type=int,
            default=1000,
            help='Monto para venta de prueba (default: 1000)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(
            '\n' + '='*60 + '\n'
            '   PRUEBA TRANSBANK POS SDK\n'
            '   Sistema RetailMind\n'
            '='*60 + '\n'
        ))

        pos_service = POSService()

        # 1. Listar puertos
        self.stdout.write('\n📍 Listando puertos disponibles...')
        try:
            puertos = pos_service.listar_puertos()
            if puertos:
                self.stdout.write(self.style.SUCCESS(f'✅ Puertos encontrados: {puertos}'))
                
                # Seleccionar puerto
                puerto = options.get('puerto')
                if not puerto:
                    if puertos:
                        puerto = puertos[0]
                        self.stdout.write(f'   Usando puerto por defecto: {puerto}')
                    else:
                        self.stdout.write(self.style.ERROR('❌ No hay puertos disponibles'))
                        return
            else:
                self.stdout.write(self.style.ERROR('❌ No se encontraron puertos'))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error listando puertos: {e}'))
            return

        # 2. Conectar
        self.stdout.write(f'\n🔌 Conectando a {puerto}...')
        try:
            resultado = pos_service.conectar(puerto, 115200)
            if resultado:
                self.stdout.write(self.style.SUCCESS(f'✅ Conectado exitosamente a {puerto}'))
            else:
                self.stdout.write(self.style.ERROR('❌ No se pudo conectar'))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error conectando: {e}'))
            return

        # 3. Verificar conexión
        self.stdout.write('\n✅ Verificando conexión (POLL)...')
        try:
            if pos_service.verificar_conexion():
                self.stdout.write(self.style.SUCCESS('✅ POS responde correctamente'))
            else:
                self.stdout.write(self.style.WARNING('⚠️  POS no responde'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠️  Error en POLL: {e}'))

        # 4. Cargar llaves (opcional)
        self.stdout.write('\n🔑 Cargar llaves en el POS?')
        self.stdout.write('   (Requerido 1 vez al día o tras conectar)')
        
        # En modo no interactivo, no cargar llaves automáticamente
        if sys.stdin.isatty():
            respuesta = input('   Cargar ahora? (s/N): ').strip().lower()
            if respuesta == 's':
                try:
                    resultado = pos_service.cargar_llaves()
                    if resultado.get('response_code') == 0:
                        self.stdout.write(self.style.SUCCESS('✅ Llaves cargadas exitosamente'))
                        self.stdout.write(f"   Commerce Code: {resultado.get('commerce_code')}")
                        self.stdout.write(f"   Terminal ID: {resultado.get('terminal_id')}")
                    else:
                        self.stdout.write(self.style.ERROR(
                            f"❌ Error cargando llaves: código {resultado.get('response_code')}"
                        ))
                except TransbankException as e:
                    self.stdout.write(self.style.ERROR(f'❌ Error: {e}'))

        # 5. Venta de prueba
        if options.get('venta'):
            import random
            monto = options.get('monto')
            ticket = f'TEST-{random.randint(1000, 9999)}'
            
            self.stdout.write(f'\n💳 Procesando venta de prueba...')
            self.stdout.write(f'   Monto: ${monto:,}')
            self.stdout.write(f'   Ticket: {ticket}')
            self.stdout.write(self.style.WARNING(
                '   ⚠️  IMPORTANTE: Pase una tarjeta en el POS cuando se solicite'
            ))
            
            try:
                resultado = pos_service.venta(monto, ticket)
                
                if resultado.get('response_code') == 0:
                    self.stdout.write(self.style.SUCCESS('\n✅ VENTA APROBADA'))
                    self.stdout.write(f"   Código Autorización: {resultado.get('authorization_code')}")
                    self.stdout.write(f"   Operation Number: {resultado.get('operation_number')}")
                    self.stdout.write(f"   Tipo Tarjeta: {resultado.get('card_type')}")
                    self.stdout.write(f"   Últimos 4 dígitos: {resultado.get('card_number', '')[-4:]}")
                    
                    # Guardar operation_number para posible anulación
                    self.stdout.write(self.style.WARNING(
                        f'\n   ⚠️  Guardar Operation Number para anulaciones: {resultado.get("operation_number")}'
                    ))
                else:
                    self.stdout.write(self.style.ERROR(
                        f'\n❌ VENTA RECHAZADA: {resultado.get("response_message", "Error desconocido")}'
                    ))
                    self.stdout.write(f'   Código: {resultado.get("response_code")}')
                    
            except TransbankException as e:
                self.stdout.write(self.style.ERROR(f'❌ Error en venta: {e}'))

        # 6. Totales
        self.stdout.write('\n📊 Consultando totales del día...')
        try:
            resultado = pos_service.totales()
            if resultado.get('response_code') == 0:
                self.stdout.write(self.style.SUCCESS('✅ Totales obtenidos'))
                self.stdout.write(f"   Cantidad transacciones: {resultado.get('tx_count', 0)}")
                self.stdout.write(f"   Total: ${resultado.get('tx_total', 0):,}")
            else:
                self.stdout.write(self.style.WARNING('⚠️  No se pudieron obtener totales'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠️  Error obteniendo totales: {e}'))

        # 7. Desconectar
        self.stdout.write('\n🔌 Desconectando del POS...')
        try:
            if pos_service.desconectar():
                self.stdout.write(self.style.SUCCESS('✅ Desconectado exitosamente'))
            else:
                self.stdout.write(self.style.WARNING('⚠️  Error al desconectar'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠️  Error: {e}'))

        # Resumen final
        self.stdout.write(self.style.SUCCESS(
            '\n' + '='*60 + '\n'
            '   ✅ PRUEBA COMPLETADA\n'
            '='*60 + '\n'
        ))

        self.stdout.write('\n📚 Próximos pasos:')
        self.stdout.write('   1. Revisar GUIA_TRANSBANK_POS_SDK.md')
        self.stdout.write('   2. Probar endpoints con: python test_transbank_sdk.py')
        self.stdout.write('   3. Integrar en tu aplicación\n')

