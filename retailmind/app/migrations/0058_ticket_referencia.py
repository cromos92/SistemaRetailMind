# Generated manually
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0057_modulosistema_configuracionpermisoglobal_opcionmenu_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='TicketReferencia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo_documento', models.CharField(help_text='801=OC, 52=Guía, 803=Contrato, HES=Hoja Entrada Servicio', max_length=10, verbose_name='Tipo Documento')),
                ('folio', models.CharField(max_length=100, verbose_name='Folio/Número')),
                ('fecha', models.DateField(verbose_name='Fecha Documento')),
                ('observaciones', models.TextField(blank=True, null=True, verbose_name='Observaciones')),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referencias', to='app.ticket')),
            ],
            options={
                'verbose_name': 'Referencia de Ticket',
                'verbose_name_plural': 'Referencias de Tickets',
                'ordering': ['creado_en'],
            },
        ),
    ]

