import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models import Compras, Dte, Empresa

print('\n' + '='*60)
print('COMPRAS Y SUS PROVEEDORES')
print('='*60)
compras = Compras.objects.select_related('empresa').filter(id__in=[1, 2])
for c in compras:
    print(f'\nCompra #{c.id}: {c.nombre}')
    print(f'  Proveedor: {c.empresa.nombre}')
    print(f'  ID Proveedor: {c.empresa.id}')
    print(f'  Temporada: {c.temporada}')

print('\n' + '='*60)
print('FACTURAS/DTES DE COMPRAS CREADOS')
print('='*60)
dtes = Dte.objects.select_related('emisor', 'receptor').filter(tipo_transaccion='COMPRA').order_by('-id')[:10]
if dtes.exists():
    for d in dtes:
        print(f'\nDTE #{d.id}: Factura {d.numero_documento}')
        print(f'  Proveedor (emisor): {d.emisor.nombre if d.emisor else "N/A"}')
        print(f'  ID Proveedor: {d.emisor.id if d.emisor else "N/A"}')
        print(f'  Receptor: {d.receptor.nombre if d.receptor else "N/A"}')
        print(f'  Monto: ${d.monto_con_iva:,.0f}')
else:
    print('\nNo hay DTEs de compras creados todavía')

print('\n' + '='*60)
print('VERIFICAR FACTURA 150')
print('='*60)
dte150 = Dte.objects.filter(numero_documento='150', tipo_transaccion='COMPRA').first()
if dte150:
    print(f'\nFactura 150 EXISTE:')
    print(f'  Proveedor: {dte150.emisor.nombre if dte150.emisor else "N/A"}')
    print(f'  ID Proveedor: {dte150.emisor.id if dte150.emisor else "N/A"}')
    print(f'  Monto: ${dte150.monto_con_iva:,.0f}')
else:
    print('\nFactura 150 NO EXISTE en la base de datos')

print('\n' + '='*60)
print('DIAGNÓSTICO')
print('='*60)
if compras.exists():
    for c in compras:
        print(f'\nPara recepcionar productos de la Compra #{c.id}:')
        print(f'  Debes crear DTEs con proveedor: {c.empresa.nombre} (ID: {c.empresa.id})')
        
        # Verificar si hay DTEs de ese proveedor
        dtes_proveedor = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            emisor=c.empresa
        ).count()
        print(f'  DTEs disponibles de este proveedor: {dtes_proveedor}')
        
        if dtes_proveedor == 0:
            print(f'  ⚠️ PROBLEMA: No hay DTEs creados para este proveedor')
            print(f'  ✅ SOLUCIÓN: Crea DTEs seleccionando "{c.empresa.nombre}" como proveedor')

print('\n')



