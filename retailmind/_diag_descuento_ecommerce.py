"""
Diagnóstico SOLO LECTURA: ¿cómo viene el descuento en pedidos de
realsport.cl (REALSPORT) y calzadospaola.cl (PAOLA)?

Determina, para cada pedido con descuento > 0, si el `precio_unitario` de los
ítems viene BRUTO (sin descuento) o YA DESCONTADO, comparando:

    suma_lineas = Σ (precio_unitario × cantidad)
    contra pedido.subtotal, pedido.descuento y pedido.total

NO modifica nada. NO toca stock, ni DTE, ni tickets. Solo imprime.

Correr (desde retailmind/, donde está manage.py):

    python manage.py shell < _diag_descuento_ecommerce.py
"""
from decimal import Decimal
from app.models import PedidoEcommerce

CANALES_PROPIOS = ['REALSPORT', 'PAOLA']

qs = (
    PedidoEcommerce.objects
    .filter(canal_origen__in=CANALES_PROPIOS, descuento__gt=0)
    .order_by('-fecha_recepcion', '-id')
)

total = qs.count()
print("=" * 78)
print(f"Pedidos de {CANALES_PROPIOS} con descuento > 0: {total}")
print("=" * 78)

if total == 0:
    print("\nNo hay pedidos con descuento en esos canales todavía.")
    print("Probando SIN filtro de descuento (primeros 5 con items) para ver el shape:\n")
    qs = (
        PedidoEcommerce.objects
        .filter(canal_origen__in=CANALES_PROPIOS)
        .order_by('-id')[:5]
    )

# Contadores de veredicto
n_bruto = 0       # líneas suman ≈ subtotal (precio bruto, descuento aparte) -> CASO B (bug grave)
n_descontado = 0  # líneas suman ≈ total (ya descontado)                     -> CASO A
n_otro = 0

for p in qs[:25]:
    items = p.items or []
    suma_lineas = sum(
        Decimal(str(it.get('precio_unitario') or 0)) * Decimal(str(it.get('cantidad') or 1))
        for it in items
    )
    subtotal = Decimal(str(p.subtotal or 0))
    descuento = Decimal(str(p.descuento or 0))
    envio = Decimal(str(p.costo_envio or 0))
    tot = Decimal(str(p.total or 0))

    # Veredicto
    cerca_subtotal = abs(suma_lineas - subtotal) <= 2 if subtotal else False
    cerca_total = abs(suma_lineas - (tot - envio)) <= 2 if tot else False

    if cerca_subtotal and not cerca_total:
        veredicto = "BRUTO (descuento NO aplicado en líneas)  <-- CASO B / sobrefactura"
        n_bruto += 1
    elif cerca_total and not cerca_subtotal:
        veredicto = "YA DESCONTADO (líneas == total)          <-- CASO A"
        n_descontado += 1
    else:
        veredicto = "AMBIGUO (revisar manualmente)"
        n_otro += 1

    # ¿Los items traen algún campo de descuento por línea?
    claves_item = sorted({k for it in items for k in it.keys()})
    claves_dcto = [k for k in claves_item if 'desc' in k.lower() or 'cup' in k.lower()]

    print(f"\n--- Pedido RM {p.numero_ticket_rm} | canal={p.canal_origen} | "
          f"#{p.numero_pedido_canal} | estado={p.estado}")
    print(f"    subtotal={subtotal}  descuento={descuento}  envio={envio}  total={tot}")
    print(f"    Σ(precio_unitario × cant) en líneas = {suma_lineas}")
    print(f"    claves por ítem: {claves_item}")
    if claves_dcto:
        print(f"    *** ítems CON campos de descuento por línea: {claves_dcto}")
    print(f"    VEREDICTO: {veredicto}")

print("\n" + "=" * 78)
print(f"RESUMEN  ->  bruto/CASO-B: {n_bruto}   ya_descontado/CASO-A: {n_descontado}   "
      f"ambiguo: {n_otro}")
print("=" * 78)
print("\nCASO B = el precio de las líneas NO trae el descuento -> el DTE/TXT sobre-factura.")
print("CASO A = las líneas ya suman el total -> monto OK pero el DTE no muestra el descuento.")
