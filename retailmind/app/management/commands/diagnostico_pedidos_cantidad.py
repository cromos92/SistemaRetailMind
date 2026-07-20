"""
Diagnóstico READ-ONLY de la cantidad de artículos en pedidos de ecommerce.

Objetivo: confirmar la causa raíz del bug "cuando la cantidad de un artículo es 2,
la boleta no lo toma bien". La facturación lee `int(item.get('cantidad') or 1)`, así
que si el JSON del pedido NO trae la clave `cantidad` (o llega bajo otro nombre, o
como valor no entero) RetailMind factura 1 unidad en vez de 2 — y la "regla de oro"
de montos (forzar que las líneas sumen `pedido.total`) ENMASCARA el error inflando
el precio unitario, dejando la boleta con total correcto pero cantidad y stock mal.

Este comando NO escribe nada. Solo lee PedidoEcommerce / Dte y reporta:
  1. Qué claves aparecen realmente en los items (para detectar 'quantity'/'qty'/… en
     vez de 'cantidad').
  2. Pedidos cuya cantidad "leída como RM" difiere de la cantidad "real" detectable.
  3. Pedidos donde pedido.total != suma(precio_unitario * cantidad) → la reconciliación
     habría deformado las líneas.
  4. En pedidos ya FACTURADOS: si el DTE emitido subcontó unidades respecto al pedido.

Uso (BD de producción, es solo lectura):
    python manage.py diagnostico_pedidos_cantidad
    python manage.py diagnostico_pedidos_cantidad --dias 60
    python manage.py diagnostico_pedidos_cantidad --pedido RM-XXXXXXXX   # dump 1 pedido
    python manage.py diagnostico_pedidos_cantidad --solo-facturados --limite 40
"""
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from app.models import PedidoEcommerce, Dte, Dte_Productos

# Claves alternativas de cantidad que algún canal/AllConnected podría estar usando
# en vez de 'cantidad' (RetailMind hoy SOLO lee 'cantidad').
CLAVES_CANTIDAD_ALT = ('quantity', 'qty', 'unidades', 'units', 'cantidad_producto', 'cant')
CLAVES_PRECIO_ALT = ('precio_unitario', 'precio', 'price', 'unit_price', 'precio_unit')


def _leer_cantidad_como_rm(item):
    """Reproduce EXACTAMENTE lo que hace la facturación: int(item.get('cantidad') or 1).
    Devuelve (valor, error_str|None)."""
    try:
        return int(item.get('cantidad') or 1), None
    except (ValueError, TypeError) as e:
        return None, f'{type(e).__name__}: {item.get("cantidad")!r}'


def _cantidad_real_detectable(item):
    """Mejor estimación de la cantidad "verdadera": usa 'cantidad' si es numérica y
    > 0; si no, prueba claves alternativas. Devuelve (valor, fuente)."""
    for clave in ('cantidad',) + CLAVES_CANTIDAD_ALT:
        v = item.get(clave)
        if v in (None, ''):
            continue
        try:
            n = int(float(v))
            if n > 0:
                return n, clave
        except (ValueError, TypeError):
            continue
    return 1, None  # nada usable → 1


def _precio_detectable(item):
    for clave in CLAVES_PRECIO_ALT:
        v = item.get(clave)
        if v in (None, ''):
            continue
        try:
            return int(float(v)), clave
        except (ValueError, TypeError):
            continue
    return 0, None


class Command(BaseCommand):
    help = 'Diagnóstico READ-ONLY de cantidades en pedidos de ecommerce (no escribe nada).'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=90,
                            help='Ventana hacia atrás por fecha_recepcion (default 90).')
        parser.add_argument('--pedido', type=str, default=None,
                            help='numero_ticket_rm o id: vuelca el JSON crudo de ese pedido.')
        parser.add_argument('--solo-facturados', action='store_true',
                            help='Analiza solo pedidos en estado FACTURADO.')
        parser.add_argument('--limite', type=int, default=60,
                            help='Máximo de pedidos problemáticos a listar (default 60).')

    # ------------------------------------------------------------------ #
    def handle(self, *args, **opts):
        if opts['pedido']:
            return self._dump_pedido(opts['pedido'])

        desde = timezone.now() - timedelta(days=opts['dias'])
        qs = PedidoEcommerce.objects.filter(fecha_recepcion__gte=desde)
        if opts['solo_facturados']:
            qs = qs.filter(estado='FACTURADO')
        qs = qs.order_by('fecha_recepcion')

        total_pedidos = qs.count()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== Diagnóstico cantidades ecommerce — últimos {opts["dias"]} días '
            f'({total_pedidos} pedidos) ===\n'))

        claves_item = Counter()          # frecuencia de cada clave en los items
        canal_keyset = {}                # canal -> set de "firmas de claves"
        con_qty_mayor_1 = 0              # pedidos con alguna línea de cantidad real > 1
        sin_clave_cantidad = 0          # pedidos con alguna línea SIN 'cantidad' usable
        rm_subcontaria = []             # pedidos donde RM leería MENOS que la cantidad real
        total_desalineado = []          # pedido.total != suma(precio*cantidad_real)+envio
        cast_errores = []               # 'cantidad' presente pero no int() -> crashearía
        dte_subconto = []               # FACTURADO: DTE.unidades header < cantidad real
        dte_monto_mismatch = []         # FACTURADO: DTE.monto_con_iva != pedido.total (>tol)
        fechas_subconto = []            # fecha_emision de los DTE con header subcontado

        for p in qs.iterator():
            items = p.items or []
            if not isinstance(items, list):
                continue

            suma_real = 0
            qty_real_pedido = 0
            qty_rm_pedido = 0
            pedido_tiene_qty2 = False
            pedido_sin_clave = False
            pedido_subcuenta = False

            firma_claves = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                for k in it.keys():
                    claves_item[k] += 1
                firma_claves.update(it.keys())

                qty_real, fuente = _cantidad_real_detectable(it)
                qty_rm, err = _leer_cantidad_como_rm(it)
                precio, _ = _precio_detectable(it)

                qty_real_pedido += qty_real
                if qty_rm is not None:
                    qty_rm_pedido += qty_rm
                suma_real += precio * qty_real

                if qty_real > 1:
                    pedido_tiene_qty2 = True
                if 'cantidad' not in it or it.get('cantidad') in (None, ''):
                    pedido_sin_clave = True
                if err:
                    cast_errores.append((p.numero_ticket_rm, err))
                if qty_rm is not None and qty_rm < qty_real:
                    pedido_subcuenta = True

            if pedido_tiene_qty2:
                con_qty_mayor_1 += 1
            if pedido_sin_clave:
                sin_clave_cantidad += 1
            if pedido_subcuenta:
                rm_subcontaria.append(
                    (p.numero_ticket_rm, p.canal_origen, qty_rm_pedido, qty_real_pedido, p.estado))

            canal_keyset.setdefault(p.canal_origen, set()).add(
                tuple(sorted(firma_claves)))

            # El código real SÍ agrega el costo_envio como línea DESPACHO, así que la
            # comparación de montos debe incluirlo (si no, todo pedido con envío se
            # marca como desalineado — falso positivo).
            total_ped = int(round(float(p.total or 0)))
            envio = int(round(float(p.costo_envio or 0)))
            suma_con_envio = suma_real + envio
            if total_ped > 0 and suma_real > 0 and total_ped != suma_con_envio:
                total_desalineado.append(
                    (p.numero_ticket_rm, p.canal_origen, total_ped, suma_con_envio,
                     total_ped - suma_con_envio, p.estado))

            # DTE ya emitido: ¿subcontó unidades (header) y cuadra el monto?
            if p.estado == 'FACTURADO' and p.dte_id:
                try:
                    dte = Dte.objects.only('unidades_productos', 'monto_con_iva',
                                           'numero_documento', 'tipo_documento',
                                           'fecha_emision').get(id=p.dte_id)
                    # Unidades a nivel LÍNEA (lo que realmente sale en la boleta) vs
                    # el header unidades_productos (que salió en 0 en varios casos).
                    uds_lineas = (Dte_Productos.objects.filter(dte=dte)
                                  .aggregate(s=Sum('stock'))['s']) or 0
                    if dte.unidades_productos is not None and qty_real_pedido > 0 \
                            and dte.unidades_productos < qty_real_pedido:
                        fe = dte.fecha_emision
                        dte_subconto.append(
                            (p.numero_ticket_rm, dte.tipo_documento, dte.numero_documento,
                             dte.unidades_productos, uds_lineas, qty_real_pedido,
                             fe.isoformat()[:10] if fe else '?'))
                        if fe:
                            fechas_subconto.append(fe)
                    monto_dte = int(round(float(dte.monto_con_iva or 0)))
                    if total_ped > 0 and abs(monto_dte - total_ped) > 2:
                        dte_monto_mismatch.append(
                            (p.numero_ticket_rm, dte.numero_documento, monto_dte,
                             total_ped, monto_dte - total_ped))
                except Dte.DoesNotExist:
                    pass

        # ---------------- Reporte ---------------- #
        self.stdout.write(self.style.HTTP_INFO('— Claves presentes en los items (frecuencia) —'))
        for k, n in claves_item.most_common():
            marca = ''
            if k in CLAVES_CANTIDAD_ALT:
                marca = '  <-- ¿CANTIDAD bajo otro nombre? RM NO la lee'
            self.stdout.write(f'   {k:<24} {n}{marca}')
        tiene_cantidad = claves_item.get('cantidad', 0)
        if not tiene_cantidad:
            self.stdout.write(self.style.ERROR(
                "   ⚠️  NINGÚN item trae la clave 'cantidad' → RM factura SIEMPRE 1 unidad."))

        self.stdout.write(self.style.HTTP_INFO('\n— Firma de claves por canal —'))
        for canal, firmas in canal_keyset.items():
            self.stdout.write(f'   {canal}:')
            for f in sorted(firmas):
                tiene = 'cantidad' in f
                flag = '' if tiene else '   <-- SIN cantidad'
                self.stdout.write(f'      {list(f)}{flag}')

        self.stdout.write(self.style.HTTP_INFO('\n— Resumen —'))
        self.stdout.write(f'   Pedidos analizados .................. {total_pedidos}')
        self.stdout.write(f'   Con alguna línea cantidad real > 1 .. {con_qty_mayor_1}')
        self.stdout.write(f'   Con alguna línea SIN cantidad usable  {sin_clave_cantidad}')
        self.stdout.write(f'   RM subcontaría unidades ............. {len(rm_subcontaria)}')
        self.stdout.write(f'   total != suma(precio*cant)+envio .... {len(total_desalineado)}')
        self.stdout.write(f'   cantidad no-int (crashearía int()) .. {len(cast_errores)}')
        self.stdout.write(f'   FACTURADOS con header uds subcontado  {len(dte_subconto)}')
        self.stdout.write(f'   FACTURADOS con MONTO DTE != total .... {len(dte_monto_mismatch)}')

        lim = opts['limite']
        if rm_subcontaria:
            self.stdout.write(self.style.WARNING(
                '\n— RM leería MENOS unidades que las reales (causa raíz directa) —'))
            self.stdout.write('   ticket_rm         canal      qty_RM  qty_real  estado')
            for row in rm_subcontaria[:lim]:
                self.stdout.write('   {:<16} {:<10} {:>6} {:>9}  {}'.format(*row))

        if dte_subconto:
            self.stdout.write(self.style.ERROR(
                '\n— Boletas emitidas: header unidades_productos < unidades del pedido —'))
            self.stdout.write('   (uds_hdr = header DTE · uds_lin = suma de líneas Dte_Productos · ped_uds = pedido)')
            self.stdout.write('   ticket_rm         tipo_dte             folio    uds_hdr  uds_lin  ped_uds  fecha')
            for row in dte_subconto[:lim]:
                self.stdout.write('   {:<16} {:<20} {:<8} {:>7} {:>8} {:>8}  {}'.format(*row))
            if fechas_subconto:
                fmin, fmax = min(fechas_subconto), max(fechas_subconto)
                horizonte = timezone.now() - timedelta(days=30)
                recientes = sum(1 for f in fechas_subconto if f >= horizonte)
                self.stdout.write(self.style.WARNING(
                    f'   → rango de emisión: {fmin.isoformat()[:10]} … {fmax.isoformat()[:10]}  '
                    f'| en los últimos 30 días: {recientes}'))
                if recientes == 0:
                    self.stdout.write(self.style.SUCCESS(
                        '   → 0 recientes ⇒ parece BUG HISTÓRICO (fix-forward ya no lo reproduce); remediar el backlog.'))
                else:
                    self.stdout.write(self.style.ERROR(
                        '   → HAY recientes ⇒ el bug SIGUE VIVO; hay que encontrar el path que aún emite en 0.'))

        if dte_monto_mismatch:
            self.stdout.write(self.style.ERROR(
                '\n— Boletas emitidas cuyo MONTO no coincide con pedido.total (montos) —'))
            self.stdout.write('   ticket_rm         folio     monto_dte    total_ped    diff')
            for row in dte_monto_mismatch[:lim]:
                self.stdout.write('   {:<16} {:<8} {:>10} {:>12} {:>8}'.format(*row))

        if total_desalineado:
            self.stdout.write(self.style.WARNING(
                '\n— total ≠ suma(precio×cantidad)+envío: la reconciliación deformó líneas —'))
            self.stdout.write('   ticket_rm         canal      total_ped  suma+envio  diff   estado')
            for row in total_desalineado[:lim]:
                self.stdout.write('   {:<16} {:<10} {:>9} {:>11} {:>7}  {}'.format(*row))

        if cast_errores:
            self.stdout.write(self.style.ERROR(
                "\n— 'cantidad' presente pero NO int()-eable (int() lanzaría al facturar) —"))
            for tk, err in cast_errores[:lim]:
                self.stdout.write(f'   {tk}: {err}')

        self.stdout.write(self.style.SUCCESS('\nDiagnóstico completado (solo lectura).\n'))

    # ------------------------------------------------------------------ #
    def _dump_pedido(self, ref):
        """Vuelca el JSON crudo de un pedido y su lectura RM vs real."""
        import json
        p = (PedidoEcommerce.objects.filter(numero_ticket_rm=ref).first()
             or (PedidoEcommerce.objects.filter(id=ref).first() if ref.isdigit() else None))
        if not p:
            self.stdout.write(self.style.ERROR(f'No se encontró pedido {ref!r}.'))
            return
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== Pedido {p.numero_ticket_rm} — {p.canal_origen} #{p.numero_pedido_canal} ==='))
        self.stdout.write(f'estado={p.estado}/{p.sub_estado}  total={p.total}  '
                          f'subtotal={p.subtotal}  descuento={p.descuento}  envio={p.costo_envio}')
        self.stdout.write('\nitems (JSON crudo):')
        self.stdout.write(json.dumps(p.items, ensure_ascii=False, indent=2))
        self.stdout.write('\nLectura línea a línea:')
        for i, it in enumerate(p.items or []):
            if not isinstance(it, dict):
                self.stdout.write(f'  [{i}] (no es objeto): {it!r}')
                continue
            qty_rm, err = _leer_cantidad_como_rm(it)
            qty_real, fuente = _cantidad_real_detectable(it)
            precio, kprecio = _precio_detectable(it)
            flag = '' if (qty_rm == qty_real and not err) else '   <-- DISCREPANCIA'
            self.stdout.write(
                f'  [{i}] sku={it.get("sku")!r}  RM_lee={qty_rm} (err={err})  '
                f'real={qty_real} (via {fuente})  precio={precio} (via {kprecio}){flag}')
        # Líneas del ticket asociado (lo que _crear_ticket_desde_pedido generó)
        if p.ticket_id:
            tps = list(p.ticket.ticket_productos.all()) if p.ticket else []
            self.stdout.write(f'\nTicket #{getattr(p.ticket, "correlativo", "?")} — '
                              f'líneas ({len(tps)}):')
            suma_tp = 0
            for tp in tps:
                suma_tp += int(tp.precio) * int(tp.stock)
                desc = (tp.descripcion_linea or (tp.ProductoTalla.sku if tp.ProductoTalla else '')) or '-'
                self.stdout.write(
                    f'   stock(cant)={tp.stock:<3} precio={int(tp.precio):<8} '
                    f'subtotal={int(tp.subtotal):<9} {desc}')
            self.stdout.write(f'   suma líneas ticket = {suma_tp}')

        # Líneas reales del DTE emitido (lo que sale en la boleta)
        if p.dte_id:
            dte = Dte.objects.filter(id=p.dte_id).first()
            if dte:
                dps = list(Dte_Productos.objects.filter(dte=dte))
                uds_lin = sum(int(d.stock or 0) for d in dps)
                suma_lin = sum(int(d.monto_item or 0) for d in dps)
                self.stdout.write(
                    f'\nDTE emitido: {dte.tipo_documento} #{dte.numero_documento}  '
                    f'estado_dte={dte.estado_dte}  header.unidades={dte.unidades_productos}  '
                    f'monto_con_iva={dte.monto_con_iva}')
                self.stdout.write(f'   líneas DTE ({len(dps)}): uds_lineas={uds_lin}  '
                                  f'suma_monto_item={suma_lin}')
                for d in dps:
                    self.stdout.write(
                        f'   stock={d.stock:<3} precio={int(d.precio or 0):<8} '
                        f'monto_item={int(d.monto_item or 0):<9} {(d.descripcion or "")[:40]}')
                if (dte.unidades_productos or 0) == 0 and uds_lin > 0:
                    self.stdout.write(self.style.WARNING(
                        '   ⚠️  header unidades_productos=0 pero las líneas SÍ tienen unidades '
                        '→ boleta correcta en líneas, header mal.'))
        self.stdout.write('')
