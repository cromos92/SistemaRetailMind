"""Configuración → Inteligencia Artificial: claves de API (cifradas) y modelo por tarea.

- `ClaveProveedorIA`: una fila por proveedor (Anthropic, OpenAI, Gemini…), la
  clave CIFRADA con Fernet (services/mp_credenciales.py) y solo los últimos 4
  caracteres a la vista.
- `ModeloTareaIA`: el modelo (o cadena de respaldo) de cada tarea: lectura de
  facturas, verificación, enderezar, chat, búsqueda, asistente.
  Sin filas, todo sigue con las variables de entorno de siempre.
- Opción de menú `inteligencia_artificial` (módulo Configuración). Solo el
  Maestro (que pasa todo): se crean filas APAGADAS para administrador y
  administración para que `inicializar_permisos` (que enciende al
  administrador todo lo que falta) no se la regale. El Maestro puede
  habilitarla a otro rol desde Gestión Permisos.

Escrita a mano. Segura en caliente: crea 2 tablas nuevas e inserta filas.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


APAGADO = {'puede_ver': False, 'puede_crear': False, 'puede_editar': False,
           'puede_eliminar': False, 'puede_exportar': False, 'puede_aprobar': False}


def crear_opcion(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='configuracion',
        defaults={'nombre': 'Configuración', 'descripcion': 'Configuración del sistema',
                  'icono': 'ri-settings-4-line', 'orden': 8},
    )
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='inteligencia_artificial',
        defaults={
            'modulo': modulo,
            'nombre': 'Inteligencia Artificial',
            'url_name': 'inteligencia_artificial',
            'url_path': '/app/configuracion/inteligencia-artificial/',
            'icono': 'ri-robot-line',
            'orden': 90,
            'es_submenu': False,
            'activo': True,
        },
    )
    for rol in ('administrador', 'administracion'):
        PermisoRol.objects.get_or_create(rol=rol, opcion_menu=opcion, defaults=APAGADO)


def quitar_opcion(apps, schema_editor):
    apps.get_model('app', 'OpcionMenu').objects.filter(codigo='inteligencia_artificial').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0240_envio_correo_devolucion_dinero'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ClaveProveedorIA',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('proveedor', models.CharField(choices=[('anthropic', 'Anthropic (Claude)'), ('openai', 'OpenAI (ChatGPT)'), ('gemini', 'Google Gemini'), ('deepseek', 'DeepSeek'), ('openrouter', 'OpenRouter'), ('compatible', 'API compatible (Ollama, vLLM, Groq…)')], max_length=20, unique=True)),
                ('clave_cifrada', models.TextField(blank=True, help_text='Clave de API CIFRADA — usar set_clave()')),
                ('ultimos4', models.CharField(blank=True, help_text='Últimos caracteres, para reconocerla en pantalla.', max_length=8)),
                ('url_base', models.CharField(blank=True, help_text='Solo «API compatible»: URL base (p. ej. http://localhost:11434/v1 para Ollama).', max_length=255)),
                ('workspace_id', models.CharField(blank=True, help_text='Solo Anthropic: id del workspace (wrkspc_…) si la clave es de organización.', max_length=100)),
                ('activa', models.BooleanField(default=True, help_text='Apagada = se usa la variable de entorno (si hay).')),
                ('modelos', models.JSONField(blank=True, default=list, help_text='Modelos que listó el proveedor en la última prueba.')),
                ('probada_en', models.DateTimeField(blank=True, null=True)),
                ('prueba_ok', models.BooleanField(blank=True, null=True)),
                ('prueba_detalle', models.CharField(blank=True, max_length=300)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('actualizado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='claves_ia_actualizadas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Clave de proveedor de IA',
                'verbose_name_plural': 'Claves de proveedores de IA',
                'ordering': ['proveedor'],
            },
        ),
        migrations.CreateModel(
            name='ModeloTareaIA',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tarea', models.CharField(choices=[('lectura', 'Lectura de facturas PDF'), ('lectura_opciones', 'Otros lectores que se ofrecen al subir'), ('verificacion', 'Verificación de líneas dudosas'), ('rapido', 'Enderezar páginas escaneadas'), ('chat', 'Chat de la carga por factura'), ('busqueda', 'Búsqueda en internet'), ('asistente', 'Asistente')], max_length=30, unique=True)),
                ('modelo', models.CharField(help_text='«proveedor:modelo»; varios separados por coma = respaldo en orden (en «otros lectores», cada lector separado por punto y coma).', max_length=255)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('actualizado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='modelos_ia_actualizados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Modelo de IA por tarea',
                'verbose_name_plural': 'Modelos de IA por tarea',
                'ordering': ['tarea'],
            },
        ),
        migrations.RunPython(crear_opcion, quitar_opcion),
    ]
