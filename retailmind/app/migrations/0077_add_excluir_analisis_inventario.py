from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0076_add_post_corte_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='excluir_de_analisis',
            field=models.BooleanField(
                default=False,
                help_text='No considerar este producto en métricas y análisis',
                verbose_name='Excluir de análisis'
            ),
        ),
    ]
