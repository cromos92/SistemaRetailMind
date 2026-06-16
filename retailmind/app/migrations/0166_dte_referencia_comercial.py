# Generated for Dte referencia comercial fields (OC / Nota de Pedido)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0165_permisos_modulo_fidelizacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte',
            name='referencia_tipo',
            field=models.CharField(blank=True, help_text='Código SII del documento referenciado: 801=Orden de Compra, 802=Nota de Pedido', max_length=10, null=True),
        ),
        migrations.AddField(
            model_name='dte',
            name='referencia_folio',
            field=models.CharField(blank=True, help_text='Folio/Número del documento referenciado (ej. N° de orden de compra)', max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='dte',
            name='referencia_fecha',
            field=models.DateField(blank=True, help_text='Fecha del documento referenciado', null=True),
        ),
    ]
