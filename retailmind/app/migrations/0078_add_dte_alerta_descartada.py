from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0077_add_excluir_analisis_inventario'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DteAlertaDescartada',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha_descartada', models.DateTimeField(auto_now_add=True)),
                ('dte', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alertas_descartadas', to='app.dte')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alertas_dte_descartadas', to=settings.AUTH_USER_MODEL)),
                ('sucursal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='alertas_dte_descartadas', to='app.sucursal')),
            ],
            options={
                'verbose_name': 'Alerta DTE Descartada',
                'verbose_name_plural': 'Alertas DTE Descartadas',
            },
        ),
        migrations.AddIndex(
            model_name='dtealertadescartada',
            index=models.Index(fields=['dte', 'usuario'], name='dte_alerta_dte_user_idx'),
        ),
        migrations.AddIndex(
            model_name='dtealertadescartada',
            index=models.Index(fields=['usuario'], name='dte_alerta_usuario_idx'),
        ),
        migrations.AddIndex(
            model_name='dtealertadescartada',
            index=models.Index(fields=['-fecha_descartada'], name='dte_alerta_fecha_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='dtealertadescartada',
            unique_together={('dte', 'usuario')},
        ),
    ]
