# Generated manually
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0058_ticket_referencia'),
    ]

    operations = [
        migrations.AddField(
            model_name='permisorol',
            name='limite_descuento_porcentaje',
            field=models.DecimalField(decimal_places=2, default=0, help_text='Límite máximo de descuento en porcentaje (0-100) que puede aplicar este rol', max_digits=5),
        ),
    ]

