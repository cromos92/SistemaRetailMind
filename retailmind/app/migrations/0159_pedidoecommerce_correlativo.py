from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0158_rename_app_credenc_activo_idx_app_credenc_activo_626f55_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoecommerce',
            name='correlativo',
            field=models.CharField(blank=True, db_index=True, default='', help_text='Folio de despacho de AllConnected impreso en la etiqueta (ej. PA3000198). Vacío hasta que se imprime.', max_length=50, verbose_name='Folio despacho'),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='correlativo_numero',
            field=models.IntegerField(blank=True, db_index=True, help_text='Número crudo del correlativo (sin prefijo ni padding), para ordenar/buscar.', null=True, verbose_name='Folio N°'),
        ),
    ]
