from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0128_dte_tipo_precio_externo'),
    ]

    operations = [
        migrations.AddField(
            model_name='arqueocaja',
            name='resultado_revision',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de revisión'),
                    ('OK', 'Aprobado sin observaciones'),
                    ('OK_CON_OBS', 'Aprobado con observaciones'),
                    ('REQUIERE_ACCION', 'Requiere acción correctiva'),
                ],
                default='PENDIENTE',
                help_text='Resultado de la revisión del supervisor',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='ObservacionArqueo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('CAJERA', 'Observación de Cajera'), ('SUPERVISOR', 'Observación de Supervisor'), ('SISTEMA', 'Nota del Sistema')], max_length=20)),
                ('texto', models.TextField()),
                ('fecha', models.DateTimeField(auto_now_add=True)),
                ('visible_para_cajera', models.BooleanField(default=True)),
                ('arqueo', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bitacora', to='app.arqueocaja')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='observaciones_arqueo', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Observación de Arqueo',
                'verbose_name_plural': 'Observaciones de Arqueo',
                'ordering': ['-fecha'],
            },
        ),
    ]
