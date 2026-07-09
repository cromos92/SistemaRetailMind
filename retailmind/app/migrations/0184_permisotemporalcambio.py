from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0183_desafiopromo_cuentapuntos_codigo_referido_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PermisoTemporalCambio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accion', models.CharField(choices=[('CANCELAR', 'Cancelar solicitud de cambio'), ('REVERTIR', 'Revertir cambio ejecutado')], max_length=20)),
                ('motivo', models.TextField()),
                ('vigente_desde', models.DateTimeField(default=django.utils.timezone.now)),
                ('vigente_hasta', models.DateTimeField(db_index=True)),
                ('revocado_en', models.DateTimeField(blank=True, null=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('codigo_autorizacion', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='permisos_temporales_cambios', to='app.codigoautorizaciondinamico')),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='permisos_temporales_cambios', to='app.empresa')),
                ('otorgado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='permisos_temporales_cambios_otorgados', to=settings.AUTH_USER_MODEL)),
                ('revocado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='permisos_temporales_cambios_revocados', to=settings.AUTH_USER_MODEL)),
                ('sucursal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='permisos_temporales_cambios', to='app.sucursal')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='permisos_temporales_cambios', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Permiso temporal de cambios',
                'verbose_name_plural': 'Permisos temporales de cambios',
                'ordering': ['-creado_en'],
            },
        ),
        migrations.AddIndex(
            model_name='permisotemporalcambio',
            index=models.Index(fields=['usuario', 'empresa', 'sucursal', 'accion', 'vigente_hasta'], name='app_ptc_vigencia_idx'),
        ),
        migrations.AddIndex(
            model_name='permisotemporalcambio',
            index=models.Index(fields=['otorgado_por', 'creado_en'], name='app_ptc_otorgado_idx'),
        ),
    ]
