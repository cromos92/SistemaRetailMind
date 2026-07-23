from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0185_dte_es_por_concepto'),
    ]

    operations = [
        # --- DevolucionGarantia: flujo de aprobación en dos pasos ---
        migrations.AlterField(
            model_name='devoluciongarantia',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de Aprobación'),
                    ('REGISTRADA', 'Registrada'),
                    ('NC_GENERADA', 'NC Generada'),
                    ('RECHAZADA', 'Rechazada'),
                    ('ANULADA', 'Anulada'),
                ],
                default='PENDIENTE', max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='devoluciongarantia',
            name='autorizado_por',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Administrador que aprobó o rechazó la solicitud',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='devoluciones_garantia_autorizadas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='solicitado_por',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Usuario que creó la solicitud de devolución',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='devoluciones_garantia_solicitadas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='anulada_por',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Usuario que anuló la solicitud pendiente',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='devoluciones_garantia_anuladas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='fecha_aprobacion',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='fecha_rechazo',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='fecha_anulacion',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='observaciones_aprobacion',
            field=models.TextField(
                blank=True, default='',
                help_text='Notas del administrador al aprobar',
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='motivo_rechazo',
            field=models.TextField(
                blank=True, default='',
                help_text='Motivo obligatorio al rechazar',
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='metodo_devolucion',
            field=models.CharField(
                blank=True, default='', max_length=30,
                choices=[
                    ('EFECTIVO_CAJA', 'Efectivo de caja'),
                    ('TRANSFERENCIA_BANCARIA', 'Transferencia bancaria'),
                    ('NO_AFECTA_CAJA', 'No afecta caja'),
                ],
                help_text='Cómo impacta la NC en la cuadratura de caja',
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantia',
            name='fecha_imputacion_caja',
            field=models.DateField(
                blank=True, null=True,
                help_text='Fecha a la que se imputa el egreso en la cuadratura (fecha_pago de la NC); null si no afecta caja',
            ),
        ),
        # --- DevolucionGarantiaDetalle: modo cantidad/monto ---
        migrations.AddField(
            model_name='devoluciongarantiadetalle',
            name='modo',
            field=models.CharField(
                default='CANTIDAD', max_length=10,
                choices=[('CANTIDAD', 'Por cantidad'), ('MONTO', 'Por monto parcial')],
            ),
        ),
        migrations.AddField(
            model_name='devoluciongarantiadetalle',
            name='monto',
            field=models.DecimalField(
                blank=True, null=True, decimal_places=2, max_digits=12,
                help_text='Monto CON IVA a acreditar (solo modo MONTO)',
            ),
        ),
    ]
