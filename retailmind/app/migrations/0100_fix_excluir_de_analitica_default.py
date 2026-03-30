from django.db import migrations


def resetear_excluir_de_analitica(apps, schema_editor):
    """
    Asegura que todos los Producto tengan excluir_de_analitica=False por defecto.
    La migración 0099 agregó el campo con default=False, pero en algunos entornos
    PostgreSQL puede quedarse en True por el comportamiento de fast-column-add.
    Esta migración lo corrige explícitamente.
    """
    Producto = apps.get_model('app', 'Producto')
    count = Producto.objects.filter(excluir_de_analitica=True).count()
    if count > 0:
        # Si MÁS del 90% están en True, es un problema de default — resetear todos
        total = Producto.objects.count()
        if total > 0 and (count / total) > 0.9:
            Producto.objects.filter(excluir_de_analitica=True).update(excluir_de_analitica=False)


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0099_add_excluir_de_analitica'),
    ]

    operations = [
        migrations.RunPython(
            resetear_excluir_de_analitica,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
