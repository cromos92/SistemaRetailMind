"""
READ-ONLY. Diagnóstico: por qué aparece una FACTURA EXENTA en el Resumen de
Caja (cuadratura) del 06-07-2026. No modifica nada.

Uso (desde retailmind/):
    python _check_fact_exenta_cuadratura.py
"""
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from datetime import date
from app.models import Dte, Dte_Detalle_Pago, Sucursal

FECHA = date(2026, 7, 6)

# Mismo universo que la cuadratura de caja (_calcular_cuadratura_data)
qs_dia = Dte.objects.filter(
    fecha_emision=FECHA,
    estado_dte__in=['EMITIDO', 'ACEPTADO'],
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
    descartado=False,
).select_related('sucursal', 'receptor')

print(f'=== DTEs del {FECHA} que entran a la cuadratura (todas las sucursales) ===')
print(f'{"SUC":10s} {"TIPO_DOC":22s} {"TIPO_TRANS":14s} {"FOLIO":>10s} {"MONTO":>14s}  RECEPTOR')
for d in qs_dia.order_by('sucursal__alias', 'tipo_documento', 'numero_documento'):
    suc = d.sucursal.alias if d.sucursal else '—'
    rec = (d.receptor.razon_social if d.receptor else '(sin receptor)')[:35]
    print(f'{suc:10s} {d.tipo_documento:22s} {d.tipo_transaccion:14s} '
          f'{str(d.numero_documento):>10s} {int(d.monto_con_iva or 0):>14,}  {rec}')

print(f'\n=== FOCO: FACTURA EXENTA del {FECHA} ===')
exentas = qs_dia.filter(tipo_documento='FACTURA EXENTA')
if not exentas.exists():
    print('  (ninguna)')
for d in exentas:
    suc = d.sucursal.alias if d.sucursal else '—'
    print(f'\n  Folio {d.numero_documento} | Suc {suc} | tipo_transaccion={d.tipo_transaccion} '
          f'| monto={int(d.monto_con_iva or 0):,}')
    print(f'    receptor: {d.receptor.razon_social if d.receptor else "(sin receptor)"}')
    print(f'    responsable: {d.responsable} | referencias: {str(d.referencias)[:80]}')
    pagos = Dte_Detalle_Pago.objects.filter(dte=d)
    if pagos.exists():
        for p in pagos:
            print(f'    pago: metodo={p.metodo_pago} monto={int(p.monto or 0):,} '
                  f'fecha_pago={p.fecha_pago}')
    else:
        print('    pago: (sin Dte_Detalle_Pago) -> no aporta a teoricos, pero SI a venta_total')

print('\n=== Interpretacion ===')
print('  tipo_transaccion=VENTA_PUBLICO  -> venta al publico por POS/modulo ventas (SI debe estar)')
print('  tipo_transaccion=VENTA          -> emitida por concepto/despacho/compensacion')
print('                                     (documentos, NO caja) -> NO deberia estar')
