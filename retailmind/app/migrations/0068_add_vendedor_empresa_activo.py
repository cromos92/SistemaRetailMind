# Generated migration for adding empresa and activo fields to Vendedor

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0067_add_cotizacion_detalle_sku'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendedor',
            name='empresa',
            field=models.ForeignKey(
                blank=True,
                help_text='Empresa a la que pertenece el vendedor',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='vendedores',
                to='app.empresa',
                verbose_name='Empresa'
            ),
        ),
        migrations.AddField(
            model_name='vendedor',
            name='activo',
            field=models.BooleanField(default=True, verbose_name='Activo'),
        ),
    ]
