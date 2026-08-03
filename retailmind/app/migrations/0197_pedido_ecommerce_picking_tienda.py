from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Trazabilidad de picking en tienda para pedidos ecommerce.

    Cuatro campos nullable (sin backfill) que completan la línea de tiempo del
    pedido junto a fecha_asignacion / fecha_facturacion ya existentes:

      - fecha_impresion_guia + guia_impresa_por: primera impresión de la guía
        de preparación (imprimirla marca el inicio del picking).
      - fecha_inicio_preparacion: transición a EN_PREPARACION.
      - fecha_listo_despacho: transición a LISTO_DESPACHO (fin del picking).

    Con esto el dashboard de asignación puede medir T1 reacción / T2 picking /
    T3 espera de factura por sucursal, y AllConnected puede ver el avance.
    """

    dependencies = [
        ('app', '0196_dte_redujo_lineas_documento'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoecommerce',
            name='fecha_impresion_guia',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Primera vez que la tienda imprimió la guía de preparación',
                verbose_name='Fecha impresión guía',
            ),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='guia_impresa_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pedidos_ecommerce_guias_impresas',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Guía impresa por',
            ),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='fecha_inicio_preparacion',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Cuándo pasó a EN_PREPARACION (imprimir la guía lo marca)',
                verbose_name='Fecha inicio preparación',
            ),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='fecha_listo_despacho',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Cuándo la tienda marcó LISTO_DESPACHO (fin del picking)',
                verbose_name='Fecha listo despacho',
            ),
        ),
    ]
