# -*- coding: utf-8 -*-
# AUDITORIA PERMISOS REPORTES - SOLO LECTURA (SELECTs via ORM). No escribe nada.
from collections import defaultdict
from app.models import OpcionMenu, PermisoRol

codes = [
    # foco del encargo
    'reporte_diferencias_recepcion', 'reporte_mercaderia_transito',
    'reporte_existencias_sucursal',           # quiebre-talla reusa este
    'reporte_ventas_internet', 'reporte_productos_vendidos',
    'reporte_comisiones_vendedor',
    'plan_liquidacion', 'campanas_liquidacion',
    # resto del mapa de reportes
    'reporte_ventas_sucursal', 'reporte_ventas_comparativo',
    'reporte_documentos_emitidos', 'reporte_existencias',
    'reporte_existencias_marca', 'resumen_existencias',
    'reporte_movimientos_sucursal', 'reporte_despachos_proveedor',
    'reporte_compras', 'reporte_rendimiento_proveedor',
    # codigos que quiza NO existen (reportes sin permiso propio)
    'reporte_productos_origen', 'reporte_ventas_global', 'inteligencia_compra',
    'reporte_recepciones_detallado', 'reporte_despachos_detallado',
]

existentes = {o.codigo: o for o in OpcionMenu.objects.filter(codigo__in=codes)}
print('=== OpcionMenu ===')
for c in codes:
    o = existentes.get(c)
    if o:
        print('%s: EXISTE id=%s activo=%s nombre=%r' % (c, o.id, o.activo, o.nombre))
    else:
        print('%s: NO EXISTE' % c)

print('')
print('=== PermisoRol por codigo (rol: flags) ===')
m = defaultdict(list)
for p in PermisoRol.objects.filter(opcion_menu__codigo__in=codes).select_related('opcion_menu'):
    flags = []
    if p.puede_ver:
        flags.append('ver')
    if getattr(p, 'puede_exportar', False):
        flags.append('exportar')
    m[p.opcion_menu.codigo].append('%s(%s)' % (p.rol, '+'.join(flags) if flags else 'ninguno'))
for c in codes:
    filas = sorted(m.get(c, []))
    print('%s: %s' % (c, filas if filas else 'SIN FILAS PermisoRol'))

print('')
print('=== Barrido amplio: OpcionMenu con codigos afines ===')
for patron in ('report', 'liquidacion', 'existencias', 'inteligencia', 'kardex', 'fifo'):
    qs = OpcionMenu.objects.filter(codigo__icontains=patron).order_by('codigo').values_list('codigo', 'activo')
    for cod, act in qs:
        print('[%s] %s activo=%s' % (patron, cod, act))
