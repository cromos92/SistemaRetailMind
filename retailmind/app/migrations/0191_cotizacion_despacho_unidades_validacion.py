# Despacho diferido de cotizaciones: cuadratura por unidades + OK del Administrador.
#
# - `Cotizacion_Empresa_Detalle_SKU.asignado_post_factura` distingue los SKUs
#   despachados DESPUÉS de facturar de los asociados al crear la cotización
#   (sin la marca, la cuadratura facturado-vs-despachado es imposible).
# - `Cotizacion_Empresa.despacho_validado(_por)/fecha_validacion_despacho`
#   registran el OK final de un usuario con permiso
#   `gestion_cotizaciones.puede_aprobar`.
# - Nuevo choice DESPACHO_VALIDADO en el historial.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def marcar_despachos_historicos(apps, schema_editor):
    """Marca como post-factura los SKUs despachados con el flujo viejo.

    Antes del campo, `asignar_sku_pendiente` creaba las filas sin marca. Un
    ítem que nació pendiente (sku_asignado_post_factura=True al cerrarse) no
    tenía SKUs asociados al crear la cotización, así que TODAS sus filas
    provienen de despachos post-factura. Sin este backfill, el histórico
    aparecería como "pendiente" falso en la cuadratura por unidades."""
    DetalleSKU = apps.get_model('app', 'Cotizacion_Empresa_Detalle_SKU')
    DetalleSKU.objects.filter(
        detalle__sku_asignado_post_factura=True,
    ).update(asignado_post_factura=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0190_cotizacion_dte_y_detalle_en_dte_productos'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacion_empresa',
            name='despacho_validado',
            field=models.BooleanField(default=False, help_text='True cuando un administrador dio el OK final al despacho (cuadratura facturado vs despachado)'),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa',
            name='despacho_validado_por',
            field=models.ForeignKey(blank=True, help_text='Usuario que validó el despacho completado', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='despachos_cotizacion_validados', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa',
            name='fecha_validacion_despacho',
            field=models.DateTimeField(blank=True, help_text='Fecha/hora en que se validó el despacho', null=True),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa_detalle_sku',
            name='asignado_post_factura',
            field=models.BooleanField(default=False, help_text='True cuando este SKU se asignó DESPUÉS de facturar (despacho diferido con salida de stock). Los SKUs asociados al crear la cotización quedan en False: sin esta marca la cuadratura facturado-vs-despachado sería imposible.'),
        ),
        migrations.AlterField(
            model_name='historial_cotizacion',
            name='accion',
            field=models.CharField(choices=[('CREADA', 'Cotización Creada'), ('MODIFICADA', 'Cotización Modificada'), ('ANULADA', 'Cotización Anulada'), ('FACTURADA', 'Convertida a Factura'), ('ENVIADA', 'Enviada al Cliente'), ('VENCIDA', 'Marcada como Vencida'), ('ITEM_AGREGADO', 'Item Agregado'), ('ITEM_MODIFICADO', 'Item Modificado'), ('ITEM_ELIMINADO', 'Item Eliminado'), ('SKU_ASIGNADO', 'SKU Asignado Post-Factura'), ('DESPACHO_COMPLETADO', 'Despacho Completado'), ('DESPACHO_VALIDADO', 'Despacho Validado (OK Admin)')], max_length=50),
        ),
        migrations.RunPython(marcar_despachos_historicos, migrations.RunPython.noop),
    ]
