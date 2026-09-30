"""Requerimientos: correo fijo del módulo y correo recordado por proveedor.

- `ConfiguracionRequerimientos` (fila única): el correo que recibe la copia de
  cada envío y las respuestas de los proveedores. Antes caía en el correo
  personal de quien enviaba.
- `CorreoProveedorRequerimiento`: el destino de los requerimientos a cada
  proveedor. Se guarda aparte de la ficha `Empresa` porque sus campos de
  correo los usan también Compras y el intercambio de DTE.

Escrita a mano. Segura en caliente: solo crea 2 tablas nuevas y no toca datos.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0241_configuracion_inteligencia_artificial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfiguracionRequerimientos',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('correo_modulo', models.EmailField(blank=True, default='', help_text='Recibe la copia de cada envío y las respuestas de los proveedores', max_length=254)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('actualizado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Configuración de Requerimientos',
                'verbose_name_plural': 'Configuración de Requerimientos',
            },
        ),
        migrations.CreateModel(
            name='CorreoProveedorRequerimiento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('correo', models.EmailField(help_text='Destino de los requerimientos a este proveedor', max_length=254)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('actualizado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('proveedor', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='correo_requerimientos', to='app.empresa')),
            ],
            options={
                'verbose_name': 'Correo de proveedor (requerimientos)',
                'verbose_name_plural': 'Correos de proveedores (requerimientos)',
            },
        ),
    ]
