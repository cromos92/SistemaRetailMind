from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0184_permisotemporalcambio'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte',
            name='es_por_concepto',
            field=models.BooleanField(default=False, help_text='Compra registrada por concepto: solo cabecera financiera, sin productos ni stock'),
        ),
    ]
