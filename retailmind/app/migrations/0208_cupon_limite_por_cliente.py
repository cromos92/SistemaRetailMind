"""`CampanaCupon.uno_vivo_por_cliente` (bool) -> `limite_por_cliente` (choices).

Por qué existe esta migración en vez de haber editado la 0205: la 0205 ya se
aplicó en producción con el campo booleano. Reescribirla habría dejado el
esquema divergente — prod con la columna vieja y cualquier BD nueva con la
nueva, sin que Django pueda notar la diferencia (para él la 0205 ya corrió).

El booleano no alcanzaba para la regla pedida: `True` significaba "un cupón sin
usar a la vez", que permite reemitir después de canjearlo. Lo que se necesita es
"uno por cliente para siempre" (UNICO), y queda VIVO para campañas recurrentes.

Migración de datos incluida por si alguna campaña ya existiera:
    uno_vivo_por_cliente=True  -> 'VIVO'   (conserva el comportamiento exacto)
    uno_vivo_por_cliente=False -> 'SIN_LIMITE'
Ninguna se convierte a UNICO automáticamente: UNICO es MÁS restrictivo que lo
que esas campañas prometieron, y endurecer una promesa ya hecha a un cliente
por efecto de una migración sería un cambio silencioso de negocio.
"""
from django.db import migrations, models


def bool_a_choice(apps, schema_editor):
    CampanaCupon = apps.get_model('app', 'CampanaCupon')
    CampanaCupon.objects.filter(uno_vivo_por_cliente=True).update(limite_por_cliente='VIVO')
    CampanaCupon.objects.filter(uno_vivo_por_cliente=False).update(limite_por_cliente='SIN_LIMITE')


def choice_a_bool(apps, schema_editor):
    CampanaCupon = apps.get_model('app', 'CampanaCupon')
    CampanaCupon.objects.filter(limite_por_cliente='SIN_LIMITE').update(uno_vivo_por_cliente=False)
    CampanaCupon.objects.exclude(limite_por_cliente='SIN_LIMITE').update(uno_vivo_por_cliente=True)


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0207_cliente_pasaporte'),
    ]

    operations = [
        # 1. Agregar el campo nuevo (con default, así las filas existentes quedan válidas)
        migrations.AddField(
            model_name='campanacupon',
            name='limite_por_cliente',
            field=models.CharField(
                choices=[
                    ('UNICO', 'Uno por cliente, para siempre'),
                    ('VIVO', 'Uno sin usar a la vez (permite reemitir tras usarlo)'),
                    ('SIN_LIMITE', 'Sin límite'),
                ],
                default='UNICO',
                help_text='UNICO: un solo cupón por cliente en toda la campaña, aunque ya '
                          'lo haya usado. VIVO: sólo impide acumular varios sin usar.',
                max_length=12,
            ),
        ),
        # 2. Traducir los datos que hubiera
        migrations.RunPython(bool_a_choice, choice_a_bool),
        # 3. Recién ahora se va el viejo
        migrations.RemoveField(
            model_name='campanacupon',
            name='uno_vivo_por_cliente',
        ),
    ]
