import app.models.inventario
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0130_nc_devolucion_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='movimientos_producto',
            name='fecha',
            field=models.DateField(blank=True, default=app.models.inventario.django_date_today),
        ),
        migrations.AlterField(
            model_name='movimientos_producto',
            name='hora',
            field=models.TimeField(blank=True, default=app.models.inventario.django_time_now),
        ),
    ]
