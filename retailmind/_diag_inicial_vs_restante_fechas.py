"""
_diag_inicial_vs_restante_fechas.py  --  SOLO LECTURA

Demuestra el efecto del filtro de fechas del reporte "Inicial vs Restante"
(/app/reportes/movimientos-sucursal/): `fecha_desde`/`fecha_hasta` recortan los
MOVIMIENTOS (columna Recib.) pero NO recortan el stock (columna Rest., que
siempre es Producto_Talla.stock de HOY). Resultado: todo lo que llego antes de
`fecha_desde` desaparece del "inicial" y la fila queda con Recib. < Rest.

Uso:
    python _diag_inicial_vs_restante_fechas.py
    python _diag_inicial_vs_restante_fechas.py "SKECHERS"
"""
import os
import sys
from collections import defaultdict
from datetime import date

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django  # noqa: E402
django.setup()

from django.db.models import Sum  # noqa: E402
from app.models import Producto, Producto_Talla, Movimientos_Producto, Sucursal  # noqa: E402
from app.constants_kardex import (  # noqa: E402
    CONCEPTOS_ABASTECIMIENTO, CONCEPTOS_TRASPASO_ENTRADA, CONCEPTOS_TRASPASO_LEGACY,
)

MARCA = sys.argv[1] if len(sys.argv) > 1 else 'PANAMA JACK'
EXTRA = ('CAMBIO_PRODUCTO_ENTRADA', 'AJUSTE_POSITIVO', 'AJUSTE_INVENTARIO_ENTRADA',
         'SOBRANTE_INGRESO', 'DONACION_RECIBIDA')
CONCEPTOS_B = list(CONCEPTOS_ABASTECIMIENTO + EXTRA + CONCEPTOS_TRASPASO_ENTRADA
                   + CONCEPTOS_TRASPASO_LEGACY)

productos = Producto.objects.filter(
    atributo1__valor__icontains=MARCA, excluir_de_analitica=False)
prod_ids = list(productos.values_list('id', flat=True))
prod_suc = {p.id: p.sucursal_id for p in productos}
sucursales = {s.id: (s.alias or s.nombre or str(s.id)) for s in Sucursal.objects.all()}

stock_prod_suc = defaultdict(int)
for r in (Producto_Talla.objects.filter(producto_id__in=prod_ids)
          .values('producto_id', 'producto__sucursal_id').annotate(s=Sum('stock'))):
    stock_prod_suc[(r['producto_id'], r['producto__sucursal_id'])] += r['s'] or 0
stock_total = sum(stock_prod_suc.values())

print(f'MARCA "{MARCA}": {len(prod_ids)} productos, stock actual total = {stock_total}\n')
print('Simulacion del filtro de fechas del reporte (Recib. se recorta, Rest. NO):\n')
print(f'{"fecha_desde":<14}{"Inicial(Recib)":>16}{"Restante":>11}'
      f'{"filas Recib<Rest":>19}{"und fantasma":>14}')

escenarios = [None, date(2020, 1, 1), date(2023, 1, 1), date(2025, 1, 1),
              date(2026, 1, 1), date(2026, 7, 1)]

for desde in escenarios:
    qs = Movimientos_Producto.objects.filter(
        ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO',
        concepto__in=CONCEPTOS_B, cantidad__gt=0)
    if desde:
        qs = qs.filter(fecha__gte=desde)

    ini = defaultdict(int)
    for r in (qs.filter(sucursal_destino_id__isnull=False)
              .values('ProductoTalla__producto_id', 'sucursal_destino_id')
              .annotate(t=Sum('cantidad'))):
        ini[(r['ProductoTalla__producto_id'], r['sucursal_destino_id'])] += abs(r['t'] or 0)
    for r in (qs.filter(sucursal_destino_id__isnull=True)
              .values('ProductoTalla__producto_id', 'sucursal_origen_id')
              .annotate(t=Sum('cantidad'))):
        ini[(r['ProductoTalla__producto_id'], r['sucursal_origen_id'])] += abs(r['t'] or 0)

    malas = 0
    fantasma = 0
    for clave, st in stock_prod_suc.items():
        if st <= 0:
            continue
        i = ini.get(clave, 0)
        if i < st:
            malas += 1
            fantasma += st - i
    etiqueta = str(desde) if desde else '(sin filtro)'
    print(f'{etiqueta:<14}{sum(ini.values()):>16}{stock_total:>11}{malas:>19}{fantasma:>14}')

print('\n"und fantasma" = unidades que el reporte muestra como Restante pero que'
      '\nno tienen ningun Recib. dentro de la ventana de fechas: llegaron antes.')

# ---- ejemplo concreto ------------------------------------------------------
print('\n' + '-' * 78)
print('EJEMPLO CONCRETO: productos con stock hoy cuya ULTIMA entrada es antigua')
print('-' * 78)
ult = {}
for r in (Movimientos_Producto.objects.filter(
            ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO',
            concepto__in=CONCEPTOS_B, cantidad__gt=0)
          .values('ProductoTalla__producto_id')
          .annotate(f=__import__('django.db.models', fromlist=['Max']).Max('fecha'))):
    ult[r['ProductoTalla__producto_id']] = r['f']

filas = []
for (pid, sid), st in stock_prod_suc.items():
    if st > 0 and pid in ult:
        filas.append((ult[pid], pid, sucursales.get(sid, '?'), st))
filas.sort()
print(f'{"ult.entrada":<13}{"producto":<10}{"articulo":<24}{"suc":<10}{"stock hoy":>10}')
art = {p.id: p.articulo for p in productos}
for f, pid, suc, st in filas[:15]:
    print(f'{str(f):<13}#{pid:<9}{art[pid][:23]:<24}{suc:<10}{st:>10}')
print('\nCada una de estas filas muestra Recib.=0 si el usuario filtra desde una'
      '\nfecha posterior a su ultima entrada, aunque el stock siga ahi.')
print('\nFIN (solo lectura).')
