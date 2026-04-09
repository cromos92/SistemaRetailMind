from django.db import migrations


TIPOS_FOTO_SEED = [
    {
        'codigo': 'FOTO_GENERAL',
        'nombre': 'Foto general del producto',
        'descripcion_guia': 'Tome una foto del producto completo donde se vea claramente el modelo, color y estado general. La foto debe mostrar el producto desde un angulo frontal con buena iluminacion.',
        'icono': 'ri-camera-line',
        'tipos_requerimiento': ['PRODUCTO_FALLADO', 'GARANTIA', 'ERROR_DESPACHO'],
        'es_obligatorio': True,
        'orden': 1,
    },
    {
        'codigo': 'FOTO_DEFECTO',
        'nombre': 'Foto del defecto / falla (primer plano)',
        'descripcion_guia': 'Tome una foto de cerca (primer plano) del defecto o falla. Debe verse claramente el problema: despegue, rotura, mancha, costura suelta, etc. Use buena luz y enfoque directo sobre el area afectada.',
        'icono': 'ri-error-warning-line',
        'tipos_requerimiento': ['PRODUCTO_FALLADO', 'GARANTIA'],
        'es_obligatorio': True,
        'orden': 2,
    },
    {
        'codigo': 'FOTO_ETIQUETA',
        'nombre': 'Foto de la etiqueta / SKU',
        'descripcion_guia': 'Tome una foto de la etiqueta del producto donde se vea el codigo SKU, marca, talla y otra informacion del fabricante. Debe ser legible en la foto.',
        'icono': 'ri-barcode-line',
        'tipos_requerimiento': ['PRODUCTO_FALLADO', 'GARANTIA', 'ERROR_DESPACHO'],
        'es_obligatorio': True,
        'orden': 3,
    },
    {
        'codigo': 'FOTO_SUELA',
        'nombre': 'Foto de la suela / base',
        'descripcion_guia': 'Para calzado: tome una foto de la suela mostrando el desgaste o estado. Para otros productos: foto de la parte inferior o base del producto.',
        'icono': 'ri-footprint-line',
        'tipos_requerimiento': ['PRODUCTO_FALLADO', 'GARANTIA'],
        'es_obligatorio': False,
        'orden': 4,
    },
    {
        'codigo': 'FOTO_CAJA',
        'nombre': 'Foto de la caja / empaque',
        'descripcion_guia': 'Tome una foto de la caja o empaque original del producto. Esto ayuda a identificar el lote y verificar el producto correcto.',
        'icono': 'ri-inbox-line',
        'tipos_requerimiento': ['PRODUCTO_FALLADO', 'ERROR_DESPACHO'],
        'es_obligatorio': False,
        'orden': 5,
    },
    {
        'codigo': 'FOTO_PRODUCTO_RECIBIDO',
        'nombre': 'Foto del producto recibido (error)',
        'descripcion_guia': 'Tome una foto del producto que se recibio por error, mostrando claramente que no corresponde al producto solicitado (diferente modelo, talla o color).',
        'icono': 'ri-exchange-line',
        'tipos_requerimiento': ['ERROR_DESPACHO'],
        'es_obligatorio': True,
        'orden': 6,
    },
    {
        'codigo': 'FOTO_BOLETA',
        'nombre': 'Foto de la boleta / comprobante',
        'descripcion_guia': 'Tome una foto de la boleta o comprobante de compra. Debe ser legible el numero de documento, fecha y monto.',
        'icono': 'ri-file-text-line',
        'tipos_requerimiento': ['PRODUCTO_FALLADO', 'GARANTIA', 'RECLAMO'],
        'es_obligatorio': False,
        'orden': 7,
    },
    {
        'codigo': 'FOTO_ADICIONAL',
        'nombre': 'Foto adicional',
        'descripcion_guia': 'Foto adicional que complementa la informacion del requerimiento.',
        'icono': 'ri-image-add-line',
        'tipos_requerimiento': ['PRODUCTO_FALLADO', 'GARANTIA', 'ERROR_DESPACHO', 'DEVOLUCION', 'RECLAMO', 'CONSULTA', 'OTROS'],
        'es_obligatorio': False,
        'orden': 99,
    },
]


def seed_tipos_foto(apps, schema_editor):
    TipoFotoRequerimiento = apps.get_model('app', 'TipoFotoRequerimiento')
    for datos in TIPOS_FOTO_SEED:
        TipoFotoRequerimiento.objects.update_or_create(
            codigo=datos['codigo'],
            defaults=datos,
        )


def borrar_tipos_foto(apps, schema_editor):
    TipoFotoRequerimiento = apps.get_model('app', 'TipoFotoRequerimiento')
    codigos = [d['codigo'] for d in TIPOS_FOTO_SEED]
    TipoFotoRequerimiento.objects.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0118_requerimientos_v2_tipos_fotos'),
    ]

    operations = [
        migrations.RunPython(seed_tipos_foto, borrar_tipos_foto),
    ]
