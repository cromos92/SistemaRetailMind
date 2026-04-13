"""
Data migration: populate sub_estado and sucursal_original for existing PedidoEcommerce records.
"""
from django.db import migrations


def populate_substates(apps, schema_editor):
    PedidoEcommerce = apps.get_model('app', 'PedidoEcommerce')

    # PENDIENTE -> sub_estado='RECIBIDO' (ya es el default, pero por claridad)
    PedidoEcommerce.objects.filter(estado='PENDIENTE', sub_estado='RECIBIDO').update(sub_estado='RECIBIDO')

    # FACTURADO -> sub_estado='FACTURADO_OK'
    PedidoEcommerce.objects.filter(estado='FACTURADO').update(sub_estado='FACTURADO_OK')

    # CANCELADO -> sub_estado='CANCELADO_CLIENTE' (asumimos por defecto)
    PedidoEcommerce.objects.filter(estado='CANCELADO').update(sub_estado='CANCELADO_CLIENTE')

    # ERROR -> sub_estado='ERROR_DTE' (asumimos por defecto)
    PedidoEcommerce.objects.filter(estado='ERROR').update(sub_estado='ERROR_DTE')

    # Copiar sucursal -> sucursal_original donde no esté seteado
    for pedido in PedidoEcommerce.objects.filter(sucursal_original__isnull=True).select_related('sucursal'):
        pedido.sucursal_original = pedido.sucursal
        pedido.save(update_fields=['sucursal_original'])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0124_ecommerce_substates_historial_metricas'),
    ]

    operations = [
        migrations.RunPython(populate_substates, reverse_noop),
    ]
