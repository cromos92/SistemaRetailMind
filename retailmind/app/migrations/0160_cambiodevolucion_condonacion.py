from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0159_pedidoecommerce_correlativo'),
    ]

    operations = [
        migrations.AddField(
            model_name='cambiodevolucion',
            name='diferencia_condonada',
            field=models.BooleanField(default=False, help_text='Si la diferencia de cobro fue condonada (perdonada) por un administrador'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='motivo_condonacion',
            field=models.TextField(blank=True, help_text='Justificación obligatoria de la condonación de la diferencia', null=True),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='condonada_por',
            field=models.ForeignKey(blank=True, help_text='Administrador que condonó la diferencia', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='condonaciones_cambio', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='fecha_condonacion',
            field=models.DateTimeField(blank=True, help_text='Fecha en que se condonó la diferencia', null=True),
        ),
        migrations.AlterField(
            model_name='historialcambiodevolucion',
            name='accion',
            field=models.CharField(choices=[('CREADO', 'Creado'), ('APROBADO', 'Aprobado'), ('APROBADO_Y_EJECUTADO', 'Aprobado y Ejecutado'), ('RECHAZADO', 'Rechazado'), ('EJECUTADO', 'Ejecutado'), ('EJECUTADO_COBRO_PENDIENTE', 'Ejecutado - Cobro Pendiente'), ('EJECUTADO_DEVOL_PENDIENTE', 'Ejecutado - Devolución Pendiente'), ('COMPLETADO', 'Completado'), ('COMPLETADO_AUTO', 'Completado Automáticamente'), ('CANCELADO', 'Cancelado'), ('MODIFICADO', 'Modificado'), ('PAGO_PROCESADO', 'Pago Procesado'), ('PRODUCTO_EVALUADO', 'Producto Evaluado'), ('REVERTIDO', 'Revertido'), ('COBRO_DIFERENCIA', 'Cobro de Diferencia'), ('CONDONACION_DIFERENCIA', 'Condonación de Diferencia'), ('DEVOLUCION_PROCESADA', 'Devolución Procesada'), ('NC_GENERADA', 'Nota de Crédito Generada'), ('NC_ANULADA', 'Nota de Crédito Anulada')], max_length=50),
        ),
    ]
