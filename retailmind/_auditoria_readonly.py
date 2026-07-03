"""
_auditoria_readonly.py — Auditoría de integridad de datos post-migración (SOLO LECTURA).

Ejecutar desde retailmind/ (donde está manage.py):

    python _auditoria_readonly.py             # secciones A-D (rápido, ~1 min)
    python _auditoria_readonly.py --detalle   # + sección E: stock vs kardex por SKU,
                                              #   estados, radiografía AJUSTE_INVENTARIO

NO modifica nada: solo consultas de conteo/agregación contra la BD que apunte
DATABASE_URL / PG_* del .env. Seguro de correr contra producción.

Complementa (no reemplaza) a:
  - python manage.py reconciliar_stock_lotes      (drift stock vs lotes, por SKU)
  - python _reconciliar_stock_vs_mov.py           (stock vs suma de kardex, por SKU)
  - python manage.py verificacion_final           (compara contra MySQL legacy)
"""
import os
import sys
from collections import Counter
from datetime import date

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Count, F, IntegerField, Q, Sum, Value  # noqa: E402
from django.db.models.functions import Coalesce  # noqa: E402

from app.models import (  # noqa: E402
    CONCEPTO_MOVIMIENTO_CHOICES,
    Dte,
    Dte_Detalle_Pago,
    LoteProducto,
    Movimientos_Producto,
    Producto,
    Producto_Talla,
    Ticket,
)

SEP = '=' * 78


def titulo(txt):
    print(f"\n{SEP}\n{txt}\n{SEP}")


# ---------------------------------------------------------------- A. VOLÚMENES
titulo('A. VOLÚMENES GENERALES')
print(f"Productos:                {Producto.objects.count():>12,}")
print(f"SKUs (Producto_Talla):    {Producto_Talla.objects.count():>12,}")
total_movs = Movimientos_Producto.objects.count()
movs_mig = Movimientos_Producto.objects.filter(referencia_externa__startswith='MIG:').count()
print(f"Movimientos kardex:       {total_movs:>12,}  (migrados 'MIG:': {movs_mig:,} | "
      f"nativos: {total_movs - movs_mig:,})")
print(f"Lotes FIFO activos:       {LoteProducto.objects.filter(activo=True).count():>12,}")
print(f"Tickets POS:              {Ticket.objects.count():>12,}")
print(f"Pagos DTE (detalle):      {Dte_Detalle_Pago.objects.count():>12,}")
print("DTEs por tipo_transaccion:")
for fila in Dte.objects.values('tipo_transaccion').annotate(n=Count('id')).order_by('-n'):
    print(f"   {fila['tipo_transaccion'] or '(nulo)':<15} {fila['n']:>10,}")
print(f"   {'(notas crédito)':<15} {Dte.objects.filter(es_nota_credito=True).count():>10,}")

# ------------------------------------------------------- B. HUELLAS MIGRACIÓN
titulo('B. HUELLAS DE LA MIGRACIÓN (14-abr-2026)')

print("Top 8 fechas de INGRESO_INICIAL (concentración = stock legacy con fecha de proceso):")
qs = (Movimientos_Producto.objects.filter(concepto='INGRESO_INICIAL')
      .values('fecha').annotate(n=Count('id')).order_by('-n')[:8])
for fila in qs:
    print(f"   {fila['fecha']}  {fila['n']:>10,}")

placeholder = Dte.objects.filter(fecha_emision=date(2026, 1, 7)).count()
print(f"\nDTEs con fecha placeholder 2026-01-07:          {placeholder:>10,}"
      "   (deberían ser ~0 tras corregir_fechas_desde_ventas)")

egresos_positivos = Movimientos_Producto.objects.filter(
    tipo_movimiento='EGRESO', cantidad__gt=0).count()
print(f"EGRESOs con cantidad POSITIVA (signo Laravel):  {egresos_positivos:>10,}"
      "   (deberían ser 0 tras corregir_signo_egresos_migrados)")

dup = (Dte.objects.values('emisor_id', 'tipo_documento', 'numero_documento')
       .annotate(n=Count('id')).filter(n__gt=1).count())
print(f"Folios duplicados (emisor+tipo+numero repetido):{dup:>10,}"
      "   (colisiones PAO1-4/PA00 mismo RUT)")

# --------------------------------------------------- C. INTEGRIDAD DEL KARDEX
titulo('C. INTEGRIDAD DEL KARDEX (Movimientos_Producto)')

mal_tipados = Movimientos_Producto.objects.filter(
    tipo_movimiento='INGRESO', cantidad__lt=0).count()
print(f"tipo_movimiento='INGRESO' con cantidad NEGATIVA: {mal_tipados:>9,}"
      "   (evidencia del save() autoclasificador muerto)")

conceptos_validos = {c[0] for c in CONCEPTO_MOVIMIENTO_CHOICES}
en_uso = Counter(dict(
    Movimientos_Producto.objects.values_list('concepto')
    .annotate(n=Count('id')).values_list('concepto', 'n')
))
fuera = {c: n for c, n in en_uso.items() if c not in conceptos_validos}
print(f"\nConceptos EN USO fuera de CONCEPTO_MOVIMIENTO_CHOICES ({len(fuera)}):")
for c, n in sorted(fuera.items(), key=lambda x: -x[1]):
    print(f"   {c or '(vacío)':<35} {n:>10,}")

print("\nMovimientos de venta por concepto (los reportes de sucursal solo cuentan"
      " VENTA_PUBLICO/VENTA_MAYORISTA):")
for c in ('VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA', 'VENTA_TICKET'):
    print(f"   {c:<20} {en_uso.get(c, 0):>10,}")

# --------------------------------------------------------------- D. STOCK
titulo('D. COHERENCIA DE STOCK')

negativos = Producto_Talla.objects.filter(stock__lt=0).count()
print(f"SKUs con stock NEGATIVO:                    {negativos:>10,}")

stock_plano = Producto_Talla.objects.aggregate(s=Sum('stock'))['s'] or 0
stock_lotes = (LoteProducto.objects.filter(activo=True)
               .aggregate(s=Sum('cantidad_disponible'))['s'] or 0)
print(f"Σ stock plano (Producto_Talla.stock):       {stock_plano:>10,}")
print(f"Σ lotes FIFO activos (cantidad_disponible): {stock_lotes:>10,}")
print(f"Drift global plano vs lotes:                {stock_plano - stock_lotes:>10,}"
      "   (detalle por SKU: manage.py reconciliar_stock_lotes)")

suma_kardex = (Movimientos_Producto.objects.filter(estado='COMPLETADO')
               .aggregate(s=Sum('cantidad'))['s'] or 0)
print(f"Σ kardex (cantidad firmada, COMPLETADO):    {suma_kardex:>10,}")
print(f"Drift global plano vs kardex:               {stock_plano - suma_kardex:>10,}"
      "   (detalle por SKU: python _reconciliar_stock_vs_mov.py)")

sin_lote = (Producto_Talla.objects.filter(stock__gt=0)
            .exclude(id__in=LoteProducto.objects.filter(activo=True)
                     .values('producto_talla_id')).count())
print(f"SKUs con stock > 0 y SIN ningún lote activo:{sin_lote:>10,}")

# ------------------------------------------------- E. DETALLE (con --detalle)
if '--detalle' in sys.argv:
    titulo('E1. STOCK PLANO vs KARDEX (por SKU, agregado en la BD)')
    calc = Coalesce(
        Sum('movimientos_productos_talla__cantidad',
            filter=Q(movimientos_productos_talla__estado='COMPLETADO')),
        Value(0), output_field=IntegerField(),
    )
    qs = Producto_Talla.objects.annotate(calc=calc)
    total = Producto_Talla.objects.count()
    cuadran = qs.filter(stock=F('calc')).count()
    sobra = qs.filter(stock__gt=F('calc')).count()
    falta = qs.filter(stock__lt=F('calc')).count()
    print(f"SKUs totales:               {total:>10,}")
    print(f"SKUs donde stock == kardex: {cuadran:>10,}  ({100 * cuadran / total:.1f}%)")
    print(f"SKUs con stock > kardex:    {sobra:>10,}   (stock escrito sin movimiento)")
    print(f"SKUs con stock < kardex:    {falta:>10,}   (movimientos sin efecto en stock)")

    print("\nTop 12 stock MUY sobre kardex:")
    for r in (qs.annotate(diff=F('stock') - F('calc')).filter(diff__gt=0).order_by('-diff')
              .values('sku', 'talla', 'stock', 'calc', 'diff',
                      'producto__articulo', 'producto__sucursal__alias')[:12]):
        print(f"   sku={r['sku']} t={r['talla']} {str(r['producto__sucursal__alias']):<8}"
              f" stock={r['stock']:>6} kardex={r['calc']:>7} diff={r['diff']:>6}"
              f"  {str(r['producto__articulo'])[:30]}")
    print("\nTop 12 kardex MUY sobre stock:")
    for r in (qs.annotate(diff=F('stock') - F('calc')).filter(diff__lt=0).order_by('diff')
              .values('sku', 'talla', 'stock', 'calc', 'diff',
                      'producto__articulo', 'producto__sucursal__alias')[:12]):
        print(f"   sku={r['sku']} t={r['talla']} {str(r['producto__sucursal__alias']):<8}"
              f" stock={r['stock']:>6} kardex={r['calc']:>7} diff={r['diff']:>6}"
              f"  {str(r['producto__articulo'])[:30]}")

    titulo('E2. DISTRIBUCIÓN DE estado EN Movimientos_Producto')
    for fila in (Movimientos_Producto.objects.values('estado')
                 .annotate(n=Count('id'), suma=Sum('cantidad')).order_by('-n')):
        print(f"   {str(fila['estado']):<20} n={fila['n']:>10,}   "
              f"Σcantidad={fila['suma'] or 0:>12,}")

    titulo('E3. RADIOGRAFÍA DE AJUSTE_INVENTARIO (concepto no declarado)')
    ai = Movimientos_Producto.objects.filter(concepto='AJUSTE_INVENTARIO')
    mig_ai = ai.filter(referencia_externa__startswith='MIG:').count()
    print(f"Total: {ai.count():,}   (migrados MIG: {mig_ai:,})")
    for fila in (ai.values('tipo_movimiento')
                 .annotate(n=Count('id'), suma=Sum('cantidad')).order_by('-n')):
        print(f"   tipo={str(fila['tipo_movimiento']):<12} n={fila['n']:>10,}   "
              f"Σcantidad={fila['suma'] or 0:>12,}")
    pos = ai.filter(cantidad__gt=0).aggregate(n=Count('id'), s=Sum('cantidad'))
    neg = ai.filter(cantidad__lt=0).aggregate(n=Count('id'), s=Sum('cantidad'))
    print(f"   cantidad>0: n={pos['n']:,} Σ={pos['s'] or 0:,}   |   "
          f"cantidad<0: n={neg['n']:,} Σ={neg['s'] or 0:,}   |   "
          f"cantidad=0: {ai.filter(cantidad=0).count():,}")

    titulo('E4. SIGNOS/TIPOS DE LOS DEMÁS CONCEPTOS NO DECLARADOS')
    for c in ('VENTA_DIRECTA', 'VENTA', 'DEVOLUCION', 'ANULACION_TICKET',
              'REVERSION_CAMBIO'):
        q = Movimientos_Producto.objects.filter(concepto=c)
        agg = q.aggregate(n=Count('id'), s=Sum('cantidad'),
                          pos=Count('id', filter=Q(cantidad__gt=0)),
                          neg=Count('id', filter=Q(cantidad__lt=0)))
        tipos = list(q.values_list('tipo_movimiento', flat=True).distinct())
        print(f"   {c:<18} n={agg['n']:>7,}  Σ={agg['s'] or 0:>9,}  "
              f"(+:{agg['pos']:,} / -:{agg['neg']:,})  tipos={tipos}")

titulo('FIN — auditoría de solo lectura completada')
