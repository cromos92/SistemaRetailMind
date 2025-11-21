"""
Comando de Django para verificar la instalación del módulo de Gestión de Sucursales
"""

from django.core.management.base import BaseCommand
from django.urls import reverse, NoReverseMatch
from django.db import connection
from app.models import Sucursal


class Command(BaseCommand):
    help = 'Verifica la instalación del módulo de Gestión de Sucursales'

    def handle(self, *args, **options):
        self.stdout.write('=' * 70)
        self.stdout.write(self.style.SUCCESS('🏢 VERIFICACIÓN DE GESTIÓN DE SUCURSALES'))
        self.stdout.write('=' * 70)
        self.stdout.write('')

        # 1. Verificar modelo
        self.stdout.write(self.style.HTTP_INFO('1️⃣ Verificando modelo Sucursal...'))
        try:
            # Verificar campos del modelo
            campos_esperados = [
                'alias', 'nombre', 'direccion', 'comuna', 'ciudad',
                'telefono', 'email', 'activa', 'created_at', 'updated_at'
            ]
            
            campos_modelo = [field.name for field in Sucursal._meta.get_fields()]
            
            campos_faltantes = []
            for campo in campos_esperados:
                if campo in campos_modelo:
                    self.stdout.write(self.style.SUCCESS(f'   ✅ Campo "{campo}" encontrado'))
                else:
                    campos_faltantes.append(campo)
                    self.stdout.write(self.style.ERROR(f'   ❌ Campo "{campo}" NO encontrado'))
            
            if not campos_faltantes:
                self.stdout.write(self.style.SUCCESS('   ✅ Todos los campos esperados están presentes'))
            else:
                self.stdout.write(self.style.ERROR(f'   ⚠️ Campos faltantes: {", ".join(campos_faltantes)}'))
                self.stdout.write(self.style.WARNING('   💡 Ejecuta: python manage.py migrate'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ❌ Error al verificar modelo: {str(e)}'))
        
        self.stdout.write('')

        # 2. Verificar tabla en base de datos
        self.stdout.write(self.style.HTTP_INFO('2️⃣ Verificando tabla en base de datos...'))
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM information_schema.columns 
                    WHERE table_name = 'app_sucursal'
                """)
                num_columnas = cursor.fetchone()[0]
                
                if num_columnas > 0:
                    self.stdout.write(self.style.SUCCESS(f'   ✅ Tabla app_sucursal existe con {num_columnas} columnas'))
                    
                    # Contar sucursales
                    total_sucursales = Sucursal.objects.count()
                    activas = Sucursal.objects.filter(activa=True).count()
                    inactivas = Sucursal.objects.filter(activa=False).count()
                    
                    self.stdout.write(self.style.SUCCESS(f'   📊 Total sucursales: {total_sucursales}'))
                    self.stdout.write(self.style.SUCCESS(f'   ✅ Activas: {activas}'))
                    self.stdout.write(self.style.WARNING(f'   ⏸️ Inactivas: {inactivas}'))
                else:
                    self.stdout.write(self.style.ERROR('   ❌ Tabla no encontrada'))
                    self.stdout.write(self.style.WARNING('   💡 Ejecuta: python manage.py migrate'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   ❌ Error al verificar tabla: {str(e)}'))
        
        self.stdout.write('')

        # 3. Verificar URLs
        self.stdout.write(self.style.HTTP_INFO('3️⃣ Verificando URLs...'))
        urls_to_check = [
            ('gestion_sucursales', 'Vista principal'),
            ('listar_sucursales_tabla', 'Listar sucursales'),
            ('crear_sucursal', 'Crear sucursal'),
        ]
        
        for url_name, descripcion in urls_to_check:
            try:
                url = reverse(url_name)
                self.stdout.write(self.style.SUCCESS(f'   ✅ {descripcion}: {url}'))
            except NoReverseMatch:
                self.stdout.write(self.style.ERROR(f'   ❌ {descripcion} (URL name: {url_name}) NO encontrada'))
        
        self.stdout.write('')

        # 4. Verificar archivos
        self.stdout.write(self.style.HTTP_INFO('4️⃣ Verificando archivos...'))
        
        import os
        from django.conf import settings
        
        archivos_esperados = [
            ('app/views_gestion_sucursales.py', 'Vistas de gestión'),
            ('app/templates/vistas/modulo_configuracion/gestion_sucursales.html', 'Template'),
            ('app/migrations/0059_extend_sucursal_model.py', 'Migración'),
        ]
        
        base_path = settings.BASE_DIR / 'app'
        
        for ruta, descripcion in archivos_esperados:
            archivo_path = settings.BASE_DIR / ruta.replace('/', os.sep)
            if archivo_path.exists():
                self.stdout.write(self.style.SUCCESS(f'   ✅ {descripcion}: {ruta}'))
            else:
                self.stdout.write(self.style.ERROR(f'   ❌ {descripcion}: {ruta} NO encontrado'))
        
        self.stdout.write('')

        # 5. Resumen final
        self.stdout.write('=' * 70)
        self.stdout.write(self.style.SUCCESS('📝 RESUMEN'))
        self.stdout.write('=' * 70)
        self.stdout.write('')
        self.stdout.write('Para acceder al módulo:')
        self.stdout.write(self.style.HTTP_INFO('   🌐 URL: http://localhost:8000/app/gestion-sucursales/'))
        self.stdout.write(self.style.HTTP_INFO('   📱 Menú: Configuración > Gestión Sucursales'))
        self.stdout.write('')
        self.stdout.write('Comandos útiles:')
        self.stdout.write(self.style.WARNING('   python manage.py migrate                    # Aplicar migraciones'))
        self.stdout.write(self.style.WARNING('   python manage.py runserver                  # Iniciar servidor'))
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('✅ Verificación completada'))
        self.stdout.write('=' * 70)

