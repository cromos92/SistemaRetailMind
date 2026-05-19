from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0007_alter_usuario_foto_perfil'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='pin_autorizacion',
            field=models.CharField(
                blank=True,
                help_text='PIN de 6 dígitos para autorizar operaciones especiales. Solo administradores/jefes.',
                max_length=128,
                null=True,
                verbose_name='PIN de Autorización (hasheado)',
            ),
        ),
        migrations.AddField(
            model_name='usuario',
            name='pin_autorizacion_actualizado',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Última Actualización del PIN',
            ),
        ),
    ]
