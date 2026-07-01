from django.db import migrations, models


def backfill_rango_inicial(apps, schema_editor):
    """
    Rellena rango_inicial para los correlativos existentes.

    Se usa rango_inicial = 1 para PRESERVAR el comportamiento histórico: antes de
    este campo el sistema asumía que todo rango empezaba en 1 (calculaba
    consumidos = inicio - 1). Con rango_inicial = 1 los "consumidos" y "% de
    consumo" que ya se veían en pantalla NO cambian para los datos migrados.

    Alternativa (dejar consumidos en 0 para los existentes): setear
    rango_inicial = inicio. NO se usa por defecto porque borraría el histórico de
    consumo visible. Si prefieres esa variante, reemplaza el update de abajo por:
        Correlativo.objects.filter(rango_inicial__isnull=True).update(
            rango_inicial=F('inicio')
        )
    (recuerda: from django.db.models import F)
    """
    Correlativo = apps.get_model('app', 'Correlativo')
    Correlativo.objects.filter(rango_inicial__isnull=True).update(rango_inicial=1)


def noop_reverse(apps, schema_editor):
    # Reversa sin pérdida real: el campo se elimina en el reverse de AddField.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0177_dte_detalle_pago_documento_compensacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='correlativo',
            name='rango_inicial',
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text='Número desde el que arrancó el correlativo. Fijo, no se muta al emitir.',
            ),
        ),
        migrations.RunPython(backfill_rango_inicial, noop_reverse),
    ]
