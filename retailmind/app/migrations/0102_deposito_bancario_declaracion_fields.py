from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0101_add_qztray_config_sucursal'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='depositobancario',
            name='monto_declarado',
            field=models.IntegerField(default=0, help_text='Monto que el cajero declara llevar a depositar'),
        ),
        migrations.AddField(
            model_name='depositobancario',
            name='monto_confirmado',
            field=models.IntegerField(default=0, help_text='Monto confirmado por el supervisor al recibir el comprobante bancario'),
        ),
        migrations.AddField(
            model_name='depositobancario',
            name='declarado_por',
            field=models.ForeignKey(
                blank=True,
                help_text='Cajero que declaró el envío del depósito',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='depositos_declarados',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='depositobancario',
            name='fecha_declaracion',
            field=models.DateTimeField(
                blank=True,
                help_text='Fecha y hora en que el cajero hizo la declaración',
                null=True,
            ),
        ),
        # Migrate existing records: monto_declarado = monto and monto_confirmado = monto
        migrations.RunSQL(
            sql="""
                UPDATE deposito_bancario
                SET monto_declarado = monto,
                    monto_confirmado = monto
                WHERE monto_declarado = 0;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
