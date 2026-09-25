"""Tabla `CargaFacturaPdf`: expediente de cada factura PDF que se le pasa al
agente de Gestión de Productos (archivo, lectura con Claude, correcciones de la
vista previa, conversación y resultado de la carga).

Escrita a mano (25-09-2026). Segura en caliente: solo crea una tabla nueva.
El `storage` del archivo es el mismo callable de la evidencia de
requerimientos (Spaces si está configurado, disco si no), ver la 0211.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import app.models.compras
import app.storage_backends


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0235_asociar_pagos_mp_admin'),
    ]

    operations = [
        migrations.CreateModel(
            name='CargaFacturaPdf',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('marca', models.CharField(blank=True, help_text='Marca indicada al subir (si no, la que lea en la factura).', max_length=100)),
                ('archivo', models.FileField(storage=app.storage_backends.storage_evidencias, upload_to=app.models.compras._ruta_carga_factura)),
                ('nombre_archivo', models.CharField(max_length=255)),
                ('lecturas', models.PositiveSmallIntegerField(default=2, help_text='Lecturas independientes que se comparan.')),
                ('modelo', models.CharField(blank=True, help_text='Modelo de Claude usado.', max_length=60)),
                ('estado', models.CharField(choices=[('LEYENDO', 'Leyendo el PDF'), ('LEIDA', 'Leída: en vista previa'), ('ERROR', 'Error de lectura'), ('CARGANDO', 'Cargando productos'), ('CERRADA', 'Cerrada')], default='LEYENDO', max_length=12)),
                ('progreso', models.CharField(blank=True, help_text='Último paso en curso (lectura o carga).', max_length=255)),
                ('error', models.TextField(blank=True)),
                ('facturas', models.JSONField(blank=True, default=list, help_text='Una entrada por factura del PDF, formato de compras/facturas/*.json, con las correcciones de la vista previa y el resultado de la carga.')),
                ('mensajes', models.JSONField(blank=True, default=list, help_text='Conversación: [{quien, texto, fecha, tipo, factura}].')),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('leida_en', models.DateTimeField(blank=True, null=True)),
                ('creado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cargas_factura_pdf', to=settings.AUTH_USER_MODEL)),
                ('sucursal', models.ForeignKey(help_text='Bodega donde entra la mercadería.', on_delete=django.db.models.deletion.PROTECT, related_name='cargas_factura_pdf', to='app.sucursal')),
            ],
            options={
                'verbose_name': 'Carga de productos desde factura PDF',
                'verbose_name_plural': 'Cargas de productos desde factura PDF',
                'ordering': ['-id'],
            },
        ),
    ]
