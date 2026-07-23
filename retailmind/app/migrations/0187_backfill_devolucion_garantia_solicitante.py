from django.db import migrations, models


def backfill_solicitante(apps, schema_editor):
    """Las devoluciones del flujo antiguo (un solo actor) tienen
    autorizado_por seteado pero solicitado_por nulo. Se copia el aprobador
    como solicitante para no perder la trazabilidad de quién la originó."""
    DevolucionGarantia = apps.get_model('app', 'DevolucionGarantia')
    DevolucionGarantia.objects.filter(solicitado_por__isnull=True).update(
        solicitado_por_id=models.F('autorizado_por_id')
    )


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0186_devolucion_garantia_flujo_aprobacion'),
    ]

    operations = [
        migrations.RunPython(backfill_solicitante, migrations.RunPython.noop),
    ]
