"""
Campañas de cupón con CÓDIGO PÚBLICO compartido.

Hasta ahora una campaña sólo podía emitir cupones nominativos uno por uno
(DC-XXXXXXXX por cliente). Esta migración habilita el segundo modo: escribir UN
código en la campaña (ej. "GISE2438"), publicarlo, y que cualquier cliente lo
presente en caja — con el límite `limite_por_cliente` decidiendo cuántas veces
lo puede usar cada RUT.

Sólo agrega columnas nullable / con default y amplía un `max_length`. NO toca
ninguna fila existente: las campañas actuales quedan con `codigo_publico=NULL`
(o sea, nominativas, exactamente como hoy) y los cupones ya emitidos con
`origen='EMITIDO'`.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0208_cupon_limite_por_cliente'),
    ]

    operations = [
        migrations.AddField(
            model_name='campanacupon',
            name='codigo_publico',
            field=models.CharField(
                blank=True, db_index=True, max_length=20, null=True, unique=True,
                help_text='Código único que se publica y cualquier cliente presenta en '
                          'caja (ej. GISE2438). Vacío = campaña nominativa (un código '
                          'distinto por cliente).',
            ),
        ),
        migrations.AddField(
            model_name='cuponcliente',
            name='origen',
            field=models.CharField(
                choices=[('EMITIDO', 'Emitido al cliente'),
                         ('PUBLICO', 'Canje de código público')],
                db_index=True, default='EMITIDO', max_length=10,
            ),
        ),
        # El canje de un código público guarda "GISE2438-XK7M": el código
        # publicado más un sufijo que mantiene el unique. 24 caracteres se
        # quedaban cortos con un código público largo.
        migrations.AlterField(
            model_name='cuponcliente',
            name='codigo',
            field=models.CharField(
                db_index=True, max_length=32, unique=True,
                help_text='Código de un solo uso que el cliente presenta en caja',
            ),
        ),
    ]
