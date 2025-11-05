# Generated manually on 2025-11-05
# Agregar campo notas a Dte_Detalle_Pago para guardar información de convenios externos

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0040_reorganizar_metodos_pago_arqueocaja'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte_detalle_pago',
            name='notas',
            field=models.TextField(blank=True, null=True),
        ),
    ]

