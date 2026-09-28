"""
Agrega «Devolución de dinero» a los módulos de la bitácora de correo
(`EnvioCorreo.modulo`): el comprobante de una devolución que se le manda al
cliente queda registrado y trazable como los correos de requerimientos.

Solo cambia `choices` (validación a nivel de Django): NO altera la columna en
PostgreSQL, no reescribe ni borra datos. Segura en caliente.

Escrita a mano (28-09-2026).
"""
from django.db import migrations, models


MODULOS = [
    ('REQUERIMIENTO', 'Requerimiento a proveedor'),
    ('GIFTCARD', 'Gift card'),
    ('COTIZACION', 'Cotización'),
    ('OTP', 'Código de verificación'),
    ('PASSWORD', 'Recuperación de contraseña'),
    ('DEVOLUCION_DINERO', 'Devolución de dinero'),
    ('OTRO', 'Otro'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0239_devolucion_dinero_mercadopago'),
    ]

    operations = [
        migrations.AlterField(
            model_name='enviocorreo',
            name='modulo',
            field=models.CharField(
                choices=MODULOS, db_index=True, default='OTRO',
                help_text='Módulo que originó el correo', max_length=20,
            ),
        ),
    ]
