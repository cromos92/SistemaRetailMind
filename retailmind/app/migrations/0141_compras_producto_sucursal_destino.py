from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0140_permisos_edicion_dte'),
    ]

    operations = [
        migrations.AddField(
            model_name='compras_producto',
            name='sucursal_destino',
            field=models.ForeignKey(
                blank=True,
                help_text='Sucursal destino sugerida para este producto. Viene del formato de importación (opcional) y se usa como default en la Recepción de Productos. Puede sobrescribirse al recepcionar.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='compras_productos_destino',
                to='app.sucursal',
                verbose_name='Sucursal destino sugerida',
            ),
        ),
    ]
