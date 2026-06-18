from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0167_cuentaclienteapp_alter_dte_tipo_precio_externo_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='giftcard',
            name='tipo_tarjeta',
            field=models.CharField(
                choices=[
                    ('DIGITAL', 'Digital (vinculada a cliente)'),
                    ('FISICA', 'Física (código impreso)'),
                ],
                db_index=True,
                default='DIGITAL',
                help_text='Digital (vinculada a cliente) o Física (código impreso)',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='giftcard',
            name='codigo_fisico',
            field=models.CharField(
                blank=True,
                help_text='Código impreso de la tarjeta física (null en digitales)',
                max_length=50,
                null=True,
                unique=True,
            ),
        ),
    ]
