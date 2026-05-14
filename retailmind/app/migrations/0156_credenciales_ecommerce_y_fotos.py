# Generated manually for feature "foto de portada desde ecommerce externos".

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0155_compras_es_historica_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CredencialesEcommerce',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.SlugField(help_text='Identificador interno único (ej: realsport, paola).', max_length=50, unique=True)),
                ('nombre', models.CharField(max_length=100)),
                ('tipo', models.CharField(choices=[('realsport', 'realsport.cl'), ('paola', 'paola.cl'), ('otro', 'Otro')], max_length=20)),
                ('url_api', models.URLField(help_text='URL base sin /api/v1/ (ej: https://realsport.cl).', max_length=255)),
                ('api_key', models.CharField(help_text='Valor que se manda en el header.', max_length=255)),
                ('header_name', models.CharField(default='X-AllConnected-Key', help_text='Nombre del header HTTP que lleva la API key.', max_length=50)),
                ('activo', models.BooleanField(default=True)),
                ('prioridad', models.IntegerField(default=0, help_text='En caso de conflicto entre ecommerces para un mismo articulo, gana el de mayor prioridad.')),
                ('ultima_sync_at', models.DateTimeField(blank=True, null=True)),
                ('ultima_sync_resultado', models.CharField(blank=True, default='', max_length=255)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_actualizacion', models.DateTimeField(auto_now=True)),
                ('empresa', models.ForeignKey(help_text='Empresa propietaria del ecommerce (ej: IMPORTADORA NICOL para realsport.cl).', on_delete=django.db.models.deletion.CASCADE, related_name='credenciales_ecommerce', to='app.empresa')),
            ],
            options={
                'verbose_name': 'Credencial ecommerce',
                'verbose_name_plural': 'Credenciales ecommerce',
                'ordering': ['-prioridad', 'nombre'],
            },
        ),
        migrations.AddIndex(
            model_name='credencialesecommerce',
            index=models.Index(fields=['activo'], name='app_credenc_activo_idx'),
        ),
        migrations.AddIndex(
            model_name='credencialesecommerce',
            index=models.Index(fields=['tipo'], name='app_credenc_tipo_idx'),
        ),
        migrations.CreateModel(
            name='FotoPortadaArticulo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('articulo', models.CharField(db_index=True, max_length=200)),
                ('url_foto', models.URLField(max_length=500)),
                ('es_principal', models.BooleanField(default=True, help_text='Si en el futuro guardamos galería, esta es la portada.')),
                ('sync_at', models.DateTimeField(auto_now=True)),
                ('origen', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fotos', to='app.credencialesecommerce')),
            ],
            options={
                'verbose_name': 'Foto portada de articulo',
                'verbose_name_plural': 'Fotos portada de articulos',
                'unique_together': {('articulo', 'origen')},
            },
        ),
        migrations.AddIndex(
            model_name='fotoportadaarticulo',
            index=models.Index(fields=['articulo', 'es_principal'], name='app_fotopor_art_prin_idx'),
        ),
    ]
