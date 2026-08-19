from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Separa la decisión interna de la del proveedor.

    Hasta acá ambas aprobaciones caían en el mismo par APROBADO/RECHAZADO y
    no se podía distinguir "la empresa decidió no reclamarlo" de "el
    proveedor lo rechazó". Se agregan los estados VALIDADO y
    RECHAZADO_INTERNO más los campos que registran quién tomó esa decisión.

    No toca datos existentes: los estados viejos siguen siendo válidos.
    """

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0211_foto_requerimiento_storage_spaces'),
    ]

    operations = [
        migrations.AddField(
            model_name='requerimiento',
            name='decision_interna',
            field=models.CharField(
                blank=True,
                choices=[
                    ('APROBADO', 'Procede: se reclama al proveedor'),
                    ('RECHAZADO', 'No procede: no se reclama'),
                ],
                help_text='Decisión de la empresa antes de escalar al proveedor',
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='motivo_decision_interna',
            field=models.TextField(
                blank=True,
                help_text='Por qué se validó o se rechazó internamente',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='fecha_decision_interna',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='usuario_decision_interna',
            field=models.ForeignKey(
                blank=True,
                help_text='Quién tomó la decisión interna',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='requerimientos_decididos',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='requerimiento',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de revision'),
                    ('EN_REVISION', 'En revision'),
                    ('VALIDADO', 'Validado (listo para el proveedor)'),
                    ('RECHAZADO_INTERNO', 'Rechazado internamente'),
                    ('ESPERANDO_RESPUESTA', 'Esperando respuesta del proveedor'),
                    ('APROBADO', 'Aprobado por el proveedor'),
                    ('RECHAZADO', 'Rechazado por el proveedor'),
                    ('EN_PROCESO', 'En proceso de resolucion'),
                    ('COMPLETADO', 'Completado'),
                    ('CANCELADO', 'Cancelado'),
                ],
                default='PENDIENTE',
                max_length=30,
            ),
        ),
    ]
