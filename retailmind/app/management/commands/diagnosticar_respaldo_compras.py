"""
Mide qué tan rastreable es la FACTURA DE COMPRA de un producto.

Es la pregunta del módulo de Requerimientos: el proveedor exige la factura
con la que se le compró el producto, y la tienda no la sabe. El buscador la
persigue por cuatro caminos; este comando cuenta cuánto rinde cada uno y, en
particular, cuánto se perdió en la migración desde Laravel.

SOLO LEE. No escribe ni una fila.

    python manage.py diagnosticar_respaldo_compras
    python manage.py diagnosticar_respaldo_compras --sku 418284
    python manage.py diagnosticar_respaldo_compras --muestra 500
"""

from django.core.management.base import BaseCommand
from django.db.models import Q, Count

from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, LoteProducto,
    Requerimiento, Producto_Talla, DocumentoCompraLegacy,
)

CONCEPTOS_INGRESO_COMPRA = (
    'RECEPCION_COMPRA', 'INGRESO_INICIAL', 'INGRESO_MANUAL', 'REPOSICION_STOCK',
)


class Command(BaseCommand):
    help = 'Diagnostica (solo lectura) qué tan rastreable es la factura de compra por SKU'

    def add_arguments(self, parser):
        parser.add_argument('--sku', type=str, help='Analiza un SKU puntual en detalle')
        parser.add_argument('--muestra', type=int, default=200,
                            help='Cuántos SKU de requerimientos evaluar (default 200)')

    # ── utilidades de salida ────────────────────────────────────────────
    def titulo(self, texto):
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(texto))
        self.stdout.write('─' * 78)

    def fila(self, etiqueta, valor, total=None, alertar_si_alto=False):
        pct = f'{valor / total * 100:5.1f}%' if total else '       '
        linea = f'  {etiqueta:<52} {valor:>9,}  {pct}'
        if total and alertar_si_alto and valor / total > 0.5:
            self.stdout.write(self.style.WARNING(linea))
        else:
            self.stdout.write(linea)

    # ── comando ─────────────────────────────────────────────────────────
    def handle(self, *args, **opciones):
        self.stdout.write(self.style.SUCCESS(
            '\n═══ RASTREABILIDAD DE LA FACTURA DE COMPRA (solo lectura) ═══'))

        if opciones['sku']:
            self.detalle_sku(opciones['sku'])
            return

        self.estado_dte_compra()
        self.estado_kardex()
        self.estado_requerimientos()
        self.cobertura_por_fuente(opciones['muestra'])
        self.conclusiones()

    # ── 1. ¿los DTE de compra traen líneas? ─────────────────────────────
    def estado_dte_compra(self):
        self.titulo('1. DTE de COMPRA: ¿tienen detalle de productos?')
        compras = Dte.objects.filter(tipo_transaccion='COMPRA')
        total = compras.count()
        if not total:
            self.stdout.write('  No hay DTE de compra en esta base.')
            return

        con_lineas = compras.annotate(n=Count('dte_productos')).filter(n__gt=0).count()
        sin_lineas = total - con_lineas

        self.fila('DTE de compra en total', total, total)
        self.fila('  con líneas de producto (buscables por SKU)', con_lineas, total)
        self.fila('  SOLO cabecera (invisibles al buscar por SKU)', sin_lineas, total,
                  alertar_si_alto=True)

        por_anio = (compras.values_list('fecha_emision__year', flat=True))
        anios = {}
        for a in por_anio:
            anios[a] = anios.get(a, 0) + 1
        if anios:
            self.stdout.write('\n  Compras por año:')
            for anio in sorted(x for x in anios if x):
                con = (compras.filter(fecha_emision__year=anio)
                       .annotate(n=Count('dte_productos')).filter(n__gt=0).count())
                self.stdout.write(
                    f'    {anio}: {anios[anio]:>6,} DTE — {con:>6,} con detalle '
                    f'({con / anios[anio] * 100:.0f}%)')

    # ── 2. el kardex y lo que la migración dejó afuera ──────────────────
    def estado_kardex(self):
        self.titulo('2. Kardex: ingresos de compra y su documento')
        ingresos = Movimientos_Producto.objects.filter(
            concepto__in=CONCEPTOS_INGRESO_COMPRA, cantidad__gt=0)
        total = ingresos.count()
        if not total:
            self.stdout.write('  No hay movimientos de ingreso por compra.')
            return

        con_dte = ingresos.filter(dte__isnull=False).count()
        migrados = ingresos.filter(referencia_externa__startswith='MIG:').count()
        migrados_con_dte = ingresos.filter(
            referencia_externa__startswith='MIG:', dte__isnull=False).count()

        self.fila('Movimientos de ingreso por compra', total, total)
        self.fila('  con FK al DTE (rastreables)', con_dte, total)
        self.fila('  SIN documento asociado', total - con_dte, total, alertar_si_alto=True)
        self.stdout.write('')
        self.fila('Migrados desde Laravel (referencia MIG:*)', migrados, total)
        self.fila('  de esos, con FK al DTE', migrados_con_dte, migrados or None)

        # El rescate NO vive en el kardex: está en su propia tabla, para no
        # mover ni un número de los reportes que hoy cuadran.
        rescatados = DocumentoCompraLegacy.objects.count()
        self.stdout.write('')
        self.fila('N° de factura rescatados (tabla aparte)', rescatados, migrados or None)
        if rescatados:
            con_cabecera = DocumentoCompraLegacy.objects.filter(dte__isnull=False).count()
            self.fila('  con la cabecera del DTE identificada', con_cabecera, rescatados)
            self.fila('  solo el número (sirve igual al proveedor)',
                      rescatados - con_cabecera, rescatados)

        if migrados and not rescatados:
            self.stdout.write(self.style.ERROR(
                '\n  ⚠ NINGÚN movimiento migrado conserva el número de documento.\n'
                '    La migración (migrate_from_laravel.py) LEE la columna N_documento\n'
                '    de MySQL en su SELECT, pero nunca la escribe en el modelo: el campo\n'
                '    referencia_externa se ocupa con "MIG:<id>" y el FK dte queda nulo.\n'
                '    Para recuperarlo, SIN tocar el kardex ni los reportes:\n'
                '        manage.py backfill_documento_movimientos_laravel'))

    # ── 3. los requerimientos ───────────────────────────────────────────
    def estado_requerimientos(self):
        self.titulo('3. Requerimientos: ¿tienen el respaldo que el proveedor exige?')
        reqs = Requerimiento.objects.all()
        total = reqs.count()
        if not total:
            self.stdout.write('  No hay requerimientos cargados.')
            return

        sin_proveedor = reqs.filter(proveedor__isnull=True).count()
        sin_factura = reqs.filter(
            Q(numero_factura_compra__isnull=True) | Q(numero_factura_compra=''),
            dte_compra__isnull=True).count()

        self.fila('Requerimientos en total', total, total)
        self.fila('  sin proveedor asignado', sin_proveedor, total, alertar_si_alto=True)
        self.fila('  sin factura de compra', sin_factura, total, alertar_si_alto=True)

    # ── 4. cuánto resuelve cada fuente ──────────────────────────────────
    def cobertura_por_fuente(self, muestra):
        self.titulo(f'4. Cobertura por fuente (muestra: SKU de requerimientos, máx {muestra})')

        # `.distinct()` sobre el modelo con su ordering por defecto NO
        # deduplica (el ORDER BY entra al SELECT): la muestra terminaba siendo
        # de requerimientos y no de SKU distintos.
        skus = list(Requerimiento.objects.exclude(sku='').order_by()
                    .values_list('sku', flat=True).distinct()[:muestra])
        if not skus:
            self.stdout.write('  No hay SKU de requerimientos que evaluar.')
            return

        marcador = {'LINEA_DTE': 0, 'LOTE': 0, 'LEGACY': 0, 'KARDEX_DTE': 0,
                    'KARDEX_SIN_DOC': 0, 'NADA': 0}

        for sku in skus:
            # `Requerimiento.sku` es CharField y admite texto libre, mientras
            # que `Producto_Talla.sku` es numérico: sin este guardo el comando
            # moría con ValueError en el primer SKU alfanumérico.
            if not str(sku).strip().isdigit():
                marcador['NADA'] += 1
                continue
            tallas = list(Producto_Talla.objects.filter(sku=str(sku).strip())
                          .values_list('id', flat=True)[:50])
            if not tallas:
                marcador['NADA'] += 1
                continue

            if Dte_Productos.objects.filter(
                    productoTalla_id__in=tallas, dte__tipo_transaccion='COMPRA').exists():
                marcador['LINEA_DTE'] += 1
            elif DocumentoCompraLegacy.objects.filter(
                    producto_talla_id__in=tallas).exists():
                marcador['LEGACY'] += 1
            elif LoteProducto.objects.filter(
                    producto_talla_id__in=tallas, dte__isnull=False,
                    dte__tipo_transaccion='COMPRA').exists():
                marcador['LOTE'] += 1
            elif Movimientos_Producto.objects.filter(
                    ProductoTalla_id__in=tallas, dte__isnull=False,
                    dte__tipo_transaccion='COMPRA').exists():
                marcador['KARDEX_DTE'] += 1
            elif Movimientos_Producto.objects.filter(
                    ProductoTalla_id__in=tallas,
                    concepto__in=CONCEPTOS_INGRESO_COMPRA, cantidad__gt=0).exists():
                marcador['KARDEX_SIN_DOC'] += 1
            else:
                marcador['NADA'] += 1

        total = len(skus)
        etiquetas = [
            ('LINEA_DTE', 'La factura detalla el SKU (confianza ALTA)'),
            ('LEGACY', 'N° rescatado del sistema anterior (ALTA)'),
            ('LOTE', 'Lote FIFO ligado al documento (ALTA)'),
            ('KARDEX_DTE', 'Movimiento ligado al documento (MEDIA)'),
            ('KARDEX_SIN_DOC', 'Hay ingreso pero sin documento (BAJA)'),
            ('NADA', 'Ningún rastro de compra'),
        ]
        for clave, texto in etiquetas:
            self.fila(texto, marcador[clave], total)

        rastreables = (marcador['LINEA_DTE'] + marcador['LEGACY']
                       + marcador['LOTE'] + marcador['KARDEX_DTE'])
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'  → {rastreables}/{total} SKU ({rastreables / total * 100:.0f}%) llegan a un '
            f'N° de documento concreto.'))
        antes = marcador['LINEA_DTE']
        if rastreables > antes:
            self.stdout.write(self.style.SUCCESS(
                f'  → El buscador anterior, que solo miraba las líneas de los DTE, '
                f'resolvía {antes} ({antes / total * 100:.0f}%).'))

    # ── detalle de un SKU ───────────────────────────────────────────────
    def detalle_sku(self, sku):
        self.titulo(f'Detalle del SKU {sku}')
        tallas = Producto_Talla.objects.filter(sku=sku).select_related('producto')
        if not tallas.exists():
            self.stdout.write(self.style.ERROR('  Ese SKU no existe en el catálogo.'))
            return

        for t in tallas[:5]:
            self.stdout.write(f'  Ficha: {t.producto.articulo} · talla {t.talla} '
                              f'· sucursal {t.producto.sucursal.alias if t.producto.sucursal_id else "—"}')
        ids = list(tallas.values_list('id', flat=True))

        self.stdout.write('\n  Líneas de DTE de compra:')
        lineas = (Dte_Productos.objects
                  .filter(productoTalla_id__in=ids, dte__tipo_transaccion='COMPRA')
                  .select_related('dte', 'dte__emisor').order_by('-dte__fecha_emision')[:10])
        for l in lineas:
            self.stdout.write(f'    {l.dte.fecha_emision} · {l.dte.get_tipo_documento_display()} '
                              f'N°{l.dte.numero_documento} · {l.dte.emisor.nombre if l.dte.emisor_id else "—"} '
                              f'· {l.stock} und · costo {l.costo}')
        if not lineas:
            self.stdout.write('    (ninguna)')

        self.stdout.write('\n  Lotes FIFO con documento:')
        lotes = (LoteProducto.objects
                 .filter(producto_talla_id__in=ids, dte__isnull=False)
                 .select_related('dte', 'dte__emisor').order_by('-fecha_ingreso')[:10])
        for lo in lotes:
            self.stdout.write(f'    {lo.dte.fecha_emision} · N°{lo.dte.numero_documento} '
                              f'· {lo.dte.emisor.nombre if lo.dte.emisor_id else "—"} '
                              f'· {lo.cantidad_inicial} und')
        if not lotes:
            self.stdout.write('    (ninguno)')

        self.stdout.write('\n  Kardex — ingresos por compra:')
        movs = (Movimientos_Producto.objects
                .filter(ProductoTalla_id__in=ids, concepto__in=CONCEPTOS_INGRESO_COMPRA,
                        cantidad__gt=0)
                .select_related('dte', 'sucursal_destino', 'sucursal_origen')
                .order_by('-fecha')[:15])
        for m in movs:
            suc = m.sucursal_destino or m.sucursal_origen
            doc = (f'DTE N°{m.dte.numero_documento}' if m.dte_id
                   else (m.observaciones or m.referencia_externa or 'sin documento'))
            self.stdout.write(f'    {m.fecha} · {m.get_concepto_display():<22} '
                              f'· {m.cantidad:>4} und · costo {m.costo:>7} '
                              f'· {suc.alias if suc else "—":<10} · {doc}')
        if not movs:
            self.stdout.write('    (ninguno)')

    def conclusiones(self):
        self.titulo('Qué hacer con esto')
        self.stdout.write(
            '  • El buscador del módulo ya cruza las cuatro fuentes y marca la\n'
            '    confianza de cada candidato, así que la parte rescatable ya se usa.\n'
            '  • Lo que NO se puede recuperar desde PostgreSQL es el N° de documento\n'
            '    de los ingresos migrados: ese dato quedó solo en el MySQL legacy.\n'
            '    Si esa base sigue accesible, correr en seco:\n'
            '        python manage.py backfill_documento_movimientos_laravel\n'
            '  • Para los DTE de compra que son solo cabecera, el detalle nunca\n'
            '    existió en el sistema: ahí el buscador ofrece las compras del\n'
            '    proveedor en fechas cercanas, que es lo máximo que puede afirmar.\n')
