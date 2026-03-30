from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0098_increase_decimal_precision'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='excluir_de_analitica',
            field=models.BooleanField(
                default=False,
                verbose_name='Excluir de analitica',
                help_text='No aparece en dashboards, predicciones ni KPIs. Usar para exhibición, consignación o catálogo histórico.'
            ),
        ),
    ]
