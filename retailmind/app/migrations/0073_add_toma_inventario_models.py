# Generated migration for TomaInventario models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0072_agregar_verificacion_depositos'),
    ]

    operations = [
        # Crear modelo TomaInventario
        migrations.CreateModel(
            name='TomaInventario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero_inventario', models.CharField(help_text='Identificador único del inventario', max_length=50, unique=True, verbose_name='Número de Inventario')),
                ('nombre', models.CharField(help_text='Nombre descriptivo del inventario', max_length=200, verbose_name='Nombre/Descripción')),
                ('tipo_inventario', models.CharField(choices=[
                    ('COMPLETO', 'Inventario Completo'),
                    ('POR_MARCA', 'Por Marca'),
                    ('POR_CATEGORIA', 'Por Categoría/Departamento'),
                    ('POR_ATRIBUTO', 'Por Atributo'),
                    ('SELECTIVO', 'Selectivo (Productos específicos)'),
                    ('CICLICO', 'Cíclico (ABC)'),
                    ('ALEATORIO', 'Aleatorio (Muestreo)'),
                ], default='COMPLETO', max_length=20, verbose_name='Tipo de Inventario')),
                ('filtros_aplicados', models.JSONField(blank=True, default=dict, help_text='Filtros JSON: {"marcas": [1,2,3], "categorias": [4,5], "atributos": {"color": "rojo"}}', verbose_name='Filtros Aplicados')),
                ('fecha_corte', models.DateTimeField(help_text='Momento exacto en que se congela el stock del sistema para comparación', verbose_name='Fecha de Corte')),
                ('fecha_inicio_conteo', models.DateTimeField(blank=True, null=True, verbose_name='Fecha Inicio Conteo')),
                ('fecha_fin_conteo', models.DateTimeField(blank=True, null=True, verbose_name='Fecha Fin Conteo')),
                ('estado', models.CharField(choices=[
                    ('BORRADOR', 'Borrador'),
                    ('EN_CONTEO', 'En Conteo'),
                    ('CONTEO_FINALIZADO', 'Conteo Finalizado'),
                    ('EN_REVISION', 'En Revisión'),
                    ('PENDIENTE_APROBACION', 'Pendiente de Aprobación'),
                    ('APROBADO', 'Aprobado'),
                    ('APLICANDO', 'Aplicando Ajustes'),
                    ('COMPLETADO', 'Completado'),
                    ('CANCELADO', 'Cancelado'),
                ], default='BORRADOR', max_length=25, verbose_name='Estado')),
                ('progreso_conteo', models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='Progreso del Conteo (%)')),
                ('total_productos_esperados', models.IntegerField(default=0, verbose_name='Total Productos a Contar')),
                ('total_productos_contados', models.IntegerField(default=0, verbose_name='Total Productos Contados')),
                ('total_diferencias_positivas', models.IntegerField(default=0, verbose_name='Diferencias Positivas (Sobrantes)')),
                ('total_diferencias_negativas', models.IntegerField(default=0, verbose_name='Diferencias Negativas (Faltantes)')),
                ('valor_diferencias_positivas', models.DecimalField(decimal_places=2, default=0, max_digits=15, verbose_name='Valor Sobrantes')),
                ('valor_diferencias_negativas', models.DecimalField(decimal_places=2, default=0, max_digits=15, verbose_name='Valor Faltantes')),
                ('valor_inventario_sistema', models.DecimalField(decimal_places=2, default=0, max_digits=15, verbose_name='Valor Inventario Sistema')),
                ('valor_inventario_fisico', models.DecimalField(decimal_places=2, default=0, max_digits=15, verbose_name='Valor Inventario Físico')),
                ('fecha_aprobacion', models.DateTimeField(blank=True, null=True, verbose_name='Fecha de Aprobación')),
                ('observaciones', models.TextField(blank=True, null=True, verbose_name='Observaciones')),
                ('motivo_cancelacion', models.TextField(blank=True, null=True, verbose_name='Motivo de Cancelación')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('aprobado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventarios_aprobados', to=settings.AUTH_USER_MODEL, verbose_name='Aprobado Por')),
                ('creado_por', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventarios_creados', to=settings.AUTH_USER_MODEL, verbose_name='Creado Por')),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tomas_inventario', to='app.empresa', verbose_name='Empresa')),
                ('sucursal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tomas_inventario', to='app.sucursal', verbose_name='Sucursal')),
            ],
            options={
                'verbose_name': 'Toma de Inventario',
                'verbose_name_plural': 'Tomas de Inventario',
                'ordering': ['-created_at'],
            },
        ),
        
        # Crear índices para TomaInventario
        migrations.AddIndex(
            model_name='tomainventario',
            index=models.Index(fields=['sucursal', 'estado'], name='app_tomai_sucursa_idx'),
        ),
        migrations.AddIndex(
            model_name='tomainventario',
            index=models.Index(fields=['fecha_corte'], name='app_tomai_fecha_c_idx'),
        ),
        migrations.AddIndex(
            model_name='tomainventario',
            index=models.Index(fields=['numero_inventario'], name='app_tomai_numero_idx'),
        ),
        migrations.AddIndex(
            model_name='tomainventario',
            index=models.Index(fields=['-created_at'], name='app_tomai_created_idx'),
        ),
        
        # Crear modelo TomaInventarioDetalle
        migrations.CreateModel(
            name='TomaInventarioDetalle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sku', models.CharField(max_length=100, verbose_name='SKU')),
                ('producto_nombre', models.CharField(max_length=255, verbose_name='Nombre Producto')),
                ('talla_nombre', models.CharField(blank=True, max_length=50, null=True, verbose_name='Talla')),
                ('marca_nombre', models.CharField(blank=True, max_length=100, null=True, verbose_name='Marca')),
                ('categoria_nombre', models.CharField(blank=True, max_length=100, null=True, verbose_name='Categoría')),
                ('stock_sistema', models.IntegerField(default=0, help_text='Stock según el sistema en la fecha de corte', verbose_name='Stock Sistema')),
                ('costo_unitario_sistema', models.DecimalField(decimal_places=2, default=0, help_text='Costo promedio FIFO en fecha de corte', max_digits=12, verbose_name='Costo Unitario')),
                ('precio_venta_sistema', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='Precio Venta')),
                ('stock_fisico', models.IntegerField(default=0, help_text='Cantidad contada físicamente', verbose_name='Stock Físico')),
                ('contado', models.BooleanField(default=False, verbose_name='¿Contado?')),
                ('fecha_conteo', models.DateTimeField(blank=True, null=True, verbose_name='Fecha de Conteo')),
                ('diferencia', models.IntegerField(default=0, help_text='stock_fisico - stock_sistema (positivo=sobrante, negativo=faltante)', verbose_name='Diferencia')),
                ('reconteo_requerido', models.BooleanField(default=False, verbose_name='Requiere Reconteo')),
                ('stock_reconteo', models.IntegerField(blank=True, null=True, verbose_name='Stock Reconteo')),
                ('fecha_reconteo', models.DateTimeField(blank=True, null=True, verbose_name='Fecha Reconteo')),
                ('ubicacion', models.CharField(blank=True, help_text='Estante, pasillo, zona, etc.', max_length=100, null=True, verbose_name='Ubicación')),
                ('observaciones', models.TextField(blank=True, null=True, verbose_name='Observaciones')),
                ('ajuste_aplicado', models.BooleanField(default=False, verbose_name='Ajuste Aplicado')),
                ('fecha_ajuste', models.DateTimeField(blank=True, null=True, verbose_name='Fecha de Ajuste')),
                ('producto_talla', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='inventarios_detalle', to='app.producto_talla', verbose_name='Producto/Talla')),
                ('toma_inventario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='detalles', to='app.tomainventario', verbose_name='Toma de Inventario')),
                ('usuario_conteo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='conteos_realizados', to=settings.AUTH_USER_MODEL, verbose_name='Usuario que Contó')),
                ('usuario_reconteo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reconteos_realizados', to=settings.AUTH_USER_MODEL, verbose_name='Usuario que Recontó')),
            ],
            options={
                'verbose_name': 'Detalle de Inventario',
                'verbose_name_plural': 'Detalles de Inventario',
                'ordering': ['producto_nombre', 'talla_nombre'],
            },
        ),
        
        # Restricción unique_together para TomaInventarioDetalle
        migrations.AddConstraint(
            model_name='tomainventariodetalle',
            constraint=models.UniqueConstraint(fields=['toma_inventario', 'producto_talla'], name='unique_inventario_producto'),
        ),
        
        # Índices para TomaInventarioDetalle
        migrations.AddIndex(
            model_name='tomainventariodetalle',
            index=models.Index(fields=['toma_inventario', 'contado'], name='app_tomai_det_contado_idx'),
        ),
        migrations.AddIndex(
            model_name='tomainventariodetalle',
            index=models.Index(fields=['toma_inventario', 'diferencia'], name='app_tomai_det_dif_idx'),
        ),
        migrations.AddIndex(
            model_name='tomainventariodetalle',
            index=models.Index(fields=['sku'], name='app_tomai_det_sku_idx'),
        ),
        
        # Crear modelo TomaInventarioLog
        migrations.CreateModel(
            name='TomaInventarioLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo_accion', models.CharField(choices=[
                    ('CREACION', 'Creación'),
                    ('INICIO_CONTEO', 'Inicio de Conteo'),
                    ('REGISTRO_CONTEO', 'Registro de Conteo'),
                    ('RECONTEO', 'Reconteo'),
                    ('CAMBIO_ESTADO', 'Cambio de Estado'),
                    ('ENVIO_APROBACION', 'Envío a Aprobación'),
                    ('APROBACION', 'Aprobación'),
                    ('RECHAZO', 'Rechazo'),
                    ('APLICACION_AJUSTES', 'Aplicación de Ajustes'),
                    ('CANCELACION', 'Cancelación'),
                    ('MODIFICACION', 'Modificación'),
                ], max_length=25)),
                ('descripcion', models.TextField()),
                ('datos_adicionales', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('toma_inventario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logs', to='app.tomainventario')),
                ('usuario', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Log de Inventario',
                'verbose_name_plural': 'Logs de Inventario',
                'ordering': ['-created_at'],
            },
        ),
    ]
