"""Aprendizaje del agente de carga por factura (fase 4) y contador de tokens.

- `PerfilCargaMarca`: lo fijado en cargas anteriores por marca (tipo de talla,
  guías, regla de precio, notas para el lector); pisa al perfil de código.
- `ProductoAprendido`: clasificación confirmada por marca + código, último
  precio y lo encontrado en internet (qué es, color primario).
- `CargaFacturaPdf.uso`: tokens y búsquedas consumidos por sesión.
- `CargaFacturaPdf.estado`: nuevo valor BUSCANDO (búsqueda en internet en curso).

Escrita a mano (25-09-2026). Segura en caliente: dos tablas nuevas, una
columna JSON con default y un cambio de choices (solo Django).
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0237_rol_jefe_y_devolucion_mercadopago'),
    ]

    operations = [
        migrations.AddField(
            model_name='cargafacturapdf',
            name='uso',
            field=models.JSONField(blank=True, default=dict, help_text='Tokens y búsquedas consumidos: totales (llamadas, entrada, salida, cache_leida, cache_escrita, busquedas) y "pasos" con el detalle por lectura / chat / búsqueda.'),
        ),
        migrations.AlterField(
            model_name='cargafacturapdf',
            name='estado',
            field=models.CharField(choices=[('LEYENDO', 'Leyendo el PDF'), ('LEIDA', 'Leída: en vista previa'), ('BUSCANDO', 'Buscando en internet'), ('ERROR', 'Error de lectura'), ('CARGANDO', 'Cargando productos'), ('CERRADA', 'Cerrada')], default='LEYENDO', max_length=12),
        ),
        migrations.CreateModel(
            name='PerfilCargaMarca',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('marca', models.CharField(help_text='Clave canónica de la marca (mayúsculas, sin puntuación; ver perfiles.clave_marca).', max_length=100, unique=True)),
                ('nombre', models.CharField(blank=True, help_text='Marca tal como la escriben.', max_length=100)),
                ('tipo_talla', models.CharField(blank=True, help_text='CL / US / EU / UK / BR / CM.', max_length=5)),
                ('guias_talla', models.JSONField(blank=True, default=dict, help_text='{HOMBRE: guía, MUJER: guía, INFANTIL: guía, DEFAULT: guía}.')),
                ('identidad_color', models.BooleanField(blank=True, help_text='True si el código no lleva el color y cada color es otra ficha (Chalada); False si el código ya lo trae (Nike). Vacío = lo que diga el perfil de código.', null=True)),
                ('color_defecto', models.CharField(blank=True, max_length=100)),
                ('umbral_costo', models.IntegerField(blank=True, null=True)),
                ('factor_bajo', models.DecimalField(blank=True, decimal_places=3, max_digits=6, null=True)),
                ('factor_alto', models.DecimalField(blank=True, decimal_places=3, max_digits=6, null=True)),
                ('margen_sobreprecio', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ('pistas_lectura', models.TextField(blank=True, help_text='Notas para el lector, una por línea («el color va en la descripción»).')),
                ('veces_usado', models.PositiveIntegerField(default=0)),
                ('ultima_factura', models.CharField(blank=True, max_length=60)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('actualizado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='perfiles_carga_marca', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Perfil aprendido de carga por marca',
                'verbose_name_plural': 'Perfiles aprendidos de carga por marca',
                'ordering': ['marca'],
            },
        ),
        migrations.CreateModel(
            name='ProductoAprendido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('marca', models.CharField(help_text='Clave canónica de la marca.', max_length=100)),
                ('articulo', models.CharField(help_text='Código normalizado (utils_producto_match).', max_length=100)),
                ('descripcion', models.CharField(blank=True, max_length=255)),
                ('color', models.CharField(blank=True, help_text='Solo para marcas cuyo código lleva el color (un código = un color).', max_length=100)),
                ('genero', models.CharField(blank=True, max_length=50)),
                ('categoria', models.CharField(blank=True, help_text='Ruta v1.2: "Padre > Hija".', max_length=150)),
                ('especialidades', models.JSONField(blank=True, default=list)),
                ('precioventa', models.IntegerField(blank=True, help_text='Última venta con que se cargó.', null=True)),
                ('costo', models.IntegerField(blank=True, help_text='Último costo neto con que se cargó.', null=True)),
                ('nombre_internet', models.CharField(blank=True, max_length=255)),
                ('que_es', models.TextField(blank=True, help_text='Qué es el producto según internet.')),
                ('color_internet', models.CharField(blank=True, help_text='Color predominante según internet.', max_length=100)),
                ('fuente_url', models.URLField(blank=True, max_length=500)),
                ('fuente', models.CharField(choices=[('carga', 'Carga confirmada'), ('chat', 'Chat'), ('internet', 'Internet')], default='carga', max_length=20)),
                ('veces_usado', models.PositiveIntegerField(default=0)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Producto aprendido por el agente',
                'verbose_name_plural': 'Productos aprendidos por el agente',
                'ordering': ['marca', 'articulo'],
                'unique_together': {('marca', 'articulo')},
            },
        ),
    ]
