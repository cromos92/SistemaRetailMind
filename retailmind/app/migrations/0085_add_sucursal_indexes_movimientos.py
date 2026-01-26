# Generated manually for performance optimization
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0084_add_permiso_sucursal'),
    ]

    operations = [
        # ✅ OPTIMIZACIÓN: Índices para búsquedas por sucursal (carga inicial rápida)
        migrations.AddIndex(
            model_name='movimientos_producto',
            index=models.Index(fields=['sucursal_origen', 'fecha'], name='app_movimie_sucursa_7a8b9c_idx'),
        ),
        migrations.AddIndex(
            model_name='movimientos_producto',
            index=models.Index(fields=['sucursal_destino', 'fecha'], name='app_movimie_sucursa_1d2e3f_idx'),
        ),
        migrations.AddIndex(
            model_name='movimientos_producto',
            index=models.Index(fields=['-fecha', '-hora'], name='app_movimie_fecha_d_4g5h6i_idx'),
        ),
    ]
