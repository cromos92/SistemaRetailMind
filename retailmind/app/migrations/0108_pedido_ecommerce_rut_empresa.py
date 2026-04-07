from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0107_rename_app_pe_estado_fecha_idx_app_pedido__estado_b9861a_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoecommerce',
            name='rut_empresa',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='RUT de la empresa del canal de origen (sin puntos, con guión)',
                max_length=20,
                verbose_name='RUT empresa canal',
            ),
        ),
    ]
