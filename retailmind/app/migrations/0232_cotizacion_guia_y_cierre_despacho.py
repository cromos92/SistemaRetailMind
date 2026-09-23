"""Cotizaciones: guía de despacho de venta previa a la factura y cierre de
despachos pendientes con motivo.

- Cotizacion_Empresa.guia_despacho: guía (DTE 52, venta) emitida antes de
  facturar. Sus unidades ya salieron del inventario.
- Ticket_Productos.despachado_por_guia: la línea ya salió con una guía; al
  cobrar no se vuelve a descontar stock.
- Cotizacion_Empresa_Detalle: unidades cerradas sin despacho + motivo/traza.
- Historial_Cotizacion.accion: DESPACHO_CERRADO, GUIA_EMITIDA, GUIA_ANULADA
  (cambio solo de choices, sin efecto en la base).

Escrita a mano. Solo agrega columnas nullable o con default: segura en caliente.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0231_renombrar_menu_mercadopago_transbank'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacion_empresa',
            name='guia_despacho',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Guía de despacho de venta emitida antes de facturar',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='cotizaciones_guia', to='app.dte'),
        ),
        migrations.AddField(
            model_name='ticket_productos',
            name='despachado_por_guia',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa_detalle',
            name='unidades_cerradas_sin_despacho',
            field=models.IntegerField(
                default=0,
                help_text='Unidades facturadas cerradas sin salida de stock (con motivo)'),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa_detalle',
            name='motivo_cierre_despacho',
            field=models.CharField(
                blank=True, default='', max_length=30,
                choices=[
                    ('NOTA_CREDITO', 'Anulado con nota de crédito'),
                    ('CLIENTE_DESISTIO', 'Cliente desistió (con nota de crédito)'),
                    ('ENTREGADO_FUERA_SISTEMA', 'Entregado sin salida en el sistema (inventario ajustado aparte)'),
                    ('OTRO', 'Otro motivo'),
                ]),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa_detalle',
            name='detalle_cierre_despacho',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa_detalle',
            name='cierre_despacho_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='items_despacho_cerrados', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='cotizacion_empresa_detalle',
            name='fecha_cierre_despacho',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='historial_cotizacion',
            name='accion',
            field=models.CharField(
                max_length=50,
                choices=[
                    ('CREADA', 'Cotización Creada'),
                    ('MODIFICADA', 'Cotización Modificada'),
                    ('ANULADA', 'Cotización Anulada'),
                    ('FACTURADA', 'Convertida a Factura'),
                    ('ENVIADA', 'Enviada al Cliente'),
                    ('VENCIDA', 'Marcada como Vencida'),
                    ('ITEM_AGREGADO', 'Item Agregado'),
                    ('ITEM_MODIFICADO', 'Item Modificado'),
                    ('ITEM_ELIMINADO', 'Item Eliminado'),
                    ('SKU_ASIGNADO', 'SKU Asignado Post-Factura'),
                    ('DESPACHO_COMPLETADO', 'Despacho Completado'),
                    ('DESPACHO_VALIDADO', 'Despacho Validado (OK Admin)'),
                    ('DESPACHO_CERRADO', 'Pendiente cerrado sin despacho (con motivo)'),
                    ('GUIA_EMITIDA', 'Guía de despacho emitida'),
                    ('GUIA_ANULADA', 'Guía de despacho anulada'),
                ]),
        ),
    ]
