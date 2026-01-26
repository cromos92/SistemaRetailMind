from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0085_add_sucursal_indexes_movimientos'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cambiopreciopendiente',
            name='descartado',
            field=models.BooleanField(default=False, help_text='Si el usuario descartó/archivó este registro'),
        ),
        migrations.AddField(
            model_name='cambiopreciopendiente',
            name='fecha_descarte',
            field=models.DateTimeField(blank=True, null=True, help_text='Fecha en que se descartó'),
        ),
        migrations.AddField(
            model_name='cambiopreciopendiente',
            name='descartado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='cambios_precio_descartados', to=settings.AUTH_USER_MODEL, help_text='Usuario que descartó el registro'),
        ),
    ]
