# Generated manually for tipo_sucursal and sobreprecio_traspaso
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0061_agregar_codigos_autorizacion_dinamicos'),
    ]

    operations = [
        # Nuevos campos en Sucursal
        migrations.AddField(
            model_name='sucursal',
            name='tipo_sucursal',
            field=models.CharField(
                choices=[
                    ('CENTRO_DISTRIBUCION', 'Centro de Distribución (Compradora)'),
                    ('VENDEDORA', 'Sucursal Vendedora'),
                    ('MIXTA', 'Mixta (Compra y Vende)'),
                ],
                default='VENDEDORA',
                help_text='CENTRO_DISTRIBUCION = Sucursal que compra a proveedores (ej: EDEL, GILD). VENDEDORA = Solo vende, recibe mercadería del CD.',
                max_length=20,
                verbose_name='Tipo de Sucursal',
            ),
        ),
        migrations.AddField(
            model_name='sucursal',
            name='es_centro_distribucion',
            field=models.BooleanField(
                default=False,
                help_text='Marcar si esta sucursal compra directamente a proveedores externos y despacha a otras sucursales',
                verbose_name='¿Es Centro de Distribución?',
            ),
        ),
        migrations.AddField(
            model_name='sucursal',
            name='margen_sobreprecio_default',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Porcentaje de sobreprecio que aplica al despachar a otras sucursales',
                max_digits=5,
                verbose_name='Margen Sobreprecio %',
            ),
        ),
        # Nuevos campos en Traspaso_Detalle
        migrations.AddField(
            model_name='traspaso_detalle',
            name='sobreprecio',
            field=models.IntegerField(default=0, verbose_name='Sobreprecio CD'),
        ),
        migrations.AddField(
            model_name='traspaso_detalle',
            name='costo_destino',
            field=models.IntegerField(default=0, verbose_name='Costo para Destino'),
        ),
    ]

