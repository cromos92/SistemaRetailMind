from django.db import migrations, models


class Migration(migrations.Migration):
    """Campos de método de devolución pedido por el cliente + datos bancarios
    para transferencia. Van en migración propia porque la 0186 ya estaba
    aplicada en producción cuando se agregaron al modelo (editar una migración
    aplicada deja la BD sin las columnas, como pasó el 2026-07-24)."""

    dependencies = [
        ('app', '0191_cotizacion_despacho_unidades_validacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='devoluciongarantia',
            name='metodo_solicitado',
            field=models.CharField(
                blank=True, default='', max_length=30,
                choices=[
                    ('EFECTIVO_CAJA', 'Efectivo de caja'),
                    ('TRANSFERENCIA_BANCARIA', 'Transferencia bancaria'),
                    ('NO_AFECTA_CAJA', 'No afecta caja'),
                ],
                help_text='Método de devolución pedido por el cliente (efectivo/transferencia)',
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='banco',
            field=models.CharField(blank=True, default='', max_length=60),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='tipo_cuenta',
            field=models.CharField(
                blank=True, default='', max_length=20,
                choices=[
                    ('CORRIENTE', 'Cuenta Corriente'),
                    ('VISTA', 'Cuenta Vista / RUT'),
                    ('AHORRO', 'Cuenta de Ahorro'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='numero_cuenta',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='cuenta_titular_rut',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text='RUT del titular de la cuenta (igual o distinto al del cliente)',
            ),
        ),
    ]
