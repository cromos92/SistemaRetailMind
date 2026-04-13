from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0129_observacion_arqueo_resultado_revision'),
    ]

    operations = [
        migrations.AddField(
            model_name='cambiodevolucion',
            name='nota_credito',
            field=models.ForeignKey(
                blank=True,
                help_text='Nota de Crédito generada por esta devolución',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='cambio_devolucion_nc',
                to='app.dte',
            ),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='metodo_devolucion',
            field=models.CharField(
                choices=[
                    ('EFECTIVO_CAJA', 'Efectivo en Caja'),
                    ('TRANSFERENCIA_BANCARIA', 'Transferencia Bancaria'),
                    ('SIN_NC', 'Sin Nota de Crédito'),
                ],
                default='SIN_NC',
                help_text='Método de devolución del dinero al cliente',
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='nc_generada',
            field=models.BooleanField(
                default=False,
                help_text='Si se generó Nota de Crédito para esta devolución',
            ),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='fecha_nc',
            field=models.DateTimeField(
                blank=True,
                help_text='Fecha de generación de la Nota de Crédito',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='pagocambiodevolucion',
            name='tipo_pago',
            field=models.CharField(
                choices=[
                    ('PAGO_DIFERENCIA', 'Pago de Diferencia'),
                    ('DEVOLUCION_EFECTIVO', 'Devolución en Efectivo'),
                    ('DEVOLUCION_TARJETA', 'Devolución a Tarjeta'),
                    ('DEVOLUCION_TRANSFERENCIA', 'Devolución por Transferencia'),
                    ('CREDITO_TIENDA', 'Crédito en Tienda'),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='historialcambiodevolucion',
            name='accion',
            field=models.CharField(
                choices=[
                    ('CREADO', 'Creado'),
                    ('APROBADO', 'Aprobado'),
                    ('APROBADO_Y_EJECUTADO', 'Aprobado y Ejecutado'),
                    ('RECHAZADO', 'Rechazado'),
                    ('EJECUTADO', 'Ejecutado'),
                    ('EJECUTADO_COBRO_PENDIENTE', 'Ejecutado - Cobro Pendiente'),
                    ('EJECUTADO_DEVOL_PENDIENTE', 'Ejecutado - Devolución Pendiente'),
                    ('COMPLETADO', 'Completado'),
                    ('COMPLETADO_AUTO', 'Completado Automáticamente'),
                    ('CANCELADO', 'Cancelado'),
                    ('MODIFICADO', 'Modificado'),
                    ('PAGO_PROCESADO', 'Pago Procesado'),
                    ('PRODUCTO_EVALUADO', 'Producto Evaluado'),
                    ('REVERTIDO', 'Revertido'),
                    ('COBRO_DIFERENCIA', 'Cobro de Diferencia'),
                    ('DEVOLUCION_PROCESADA', 'Devolución Procesada'),
                    ('NC_GENERADA', 'Nota de Crédito Generada'),
                    ('NC_ANULADA', 'Nota de Crédito Anulada'),
                ],
                max_length=50,
            ),
        ),
    ]
