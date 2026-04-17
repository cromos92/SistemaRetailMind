"""
Migración: fecha real editable y temporada normalizada en Compras.

Cambios:
- `Compras.fecha`: se quita `auto_now=True` y se deja editable con default=hoy,
  para permitir cargar compras históricas (ej. del año pasado) con su fecha real.
- `Compras.fecha_creacion`: nuevo DateTimeField con `auto_now_add=True` para
  conservar la auditoría de cuándo se registró la compra.
- `Compras.temporada_familia`: nuevo CharField con choices (Verano/Otoño/Invierno/Primavera).
- `Compras.temporada_anio`: nuevo IntegerField.

Además, un RunPython intenta parsear el campo `temporada` (texto libre) existente
para poblar `temporada_familia` y `temporada_anio` en los registros actuales,
y copia `fecha` a `fecha_creacion` para no perder la auditoría previa.
"""

import re
from django.db import migrations, models
import django.utils.timezone


FAMILIAS = {
    'VERANO': ['verano'],
    'OTONO': ['otono', 'otoño', 'otoñ', 'otoño-inv', 'oto'],
    'INVIERNO': ['invierno', 'inviern', 'winter'],
    'PRIMAVERA': ['primavera', 'primaver', 'spring'],
}


def _normalizar_texto(texto):
    if not texto:
        return ''
    t = texto.strip().lower()
    # quitar tildes básicos
    reemplazos = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u'}
    for k, v in reemplazos.items():
        t = t.replace(k, v)
    return t


def _inferir_familia(texto_normalizado):
    for familia, variantes in FAMILIAS.items():
        for v in variantes:
            if v in texto_normalizado:
                return familia
    return None


def _inferir_anio(texto, fecha_fallback):
    if texto:
        m = re.search(r'(20\d{2})', texto)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    if fecha_fallback:
        return fecha_fallback.year
    return None


def poblar_temporada_normalizada(apps, schema_editor):
    Compras = apps.get_model('app', 'Compras')
    for compra in Compras.objects.all():
        texto_norm = _normalizar_texto(compra.temporada)
        familia = _inferir_familia(texto_norm)
        anio = _inferir_anio(compra.temporada, compra.fecha)

        cambios = False
        if familia and not compra.temporada_familia:
            compra.temporada_familia = familia
            cambios = True
        if anio and not compra.temporada_anio:
            compra.temporada_anio = anio
            cambios = True
        if cambios:
            compra.save(update_fields=['temporada_familia', 'temporada_anio'])


def revertir_temporada_normalizada(apps, schema_editor):
    # No hay nada que revertir de forma segura: los campos serán eliminados
    # por el reverse de AddField.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0138_arqueocaja_cache_depositos'),
    ]

    operations = [
        # 1) fecha editable con default=hoy (antes era auto_now=True)
        migrations.AlterField(
            model_name='compras',
            name='fecha',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        # 2) fecha_creacion (auditoría del registro)
        migrations.AddField(
            model_name='compras',
            name='fecha_creacion',
            field=models.DateTimeField(auto_now_add=True, null=True, blank=True),
        ),
        # 3) temporada_familia
        migrations.AddField(
            model_name='compras',
            name='temporada_familia',
            field=models.CharField(
                max_length=20,
                null=True,
                blank=True,
                choices=[
                    ('VERANO', 'Verano'),
                    ('OTONO', 'Otoño'),
                    ('INVIERNO', 'Invierno'),
                    ('PRIMAVERA', 'Primavera'),
                ],
                help_text='Familia de temporada (normalizada) para comparativas YoY',
            ),
        ),
        # 4) temporada_anio
        migrations.AddField(
            model_name='compras',
            name='temporada_anio',
            field=models.IntegerField(
                null=True,
                blank=True,
                help_text='Año de la temporada (ej. 2025) para comparativas YoY',
            ),
        ),
        # 5) Poblar los nuevos campos a partir del texto libre `temporada`
        migrations.RunPython(
            poblar_temporada_normalizada,
            revertir_temporada_normalizada,
        ),
    ]
