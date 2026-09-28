# -*- coding: utf-8 -*-
"""
Revisa las recepciones de compra PENDIENTES de crear (Productos_Recepcionados
con compra_producto_talla y sin producto_talla) —el "stock fantasma" que ofrece
la lista de Crear Productos— y las clasifica:

  a) COMPRA_ANULADA  la compra está ELIMINADA o CANCELADA.
  b) YA_INGRESADA    el mismo artículo+talla ya tiene INGRESO en el kardex con la
                     misma factura (DTE): crearla duplicaría el stock.
  c) SIN_DATOS       no tiene factura ni sucursal destino.
  d) PENDIENTE_REAL  pendiente legítima.

Además (--auditar-ingresados) lista, solo como reporte:
  - compras ELIMINADAS cuyas recepciones YA se convirtieron en stock;
  - facturas de compra recepcionadas en más de una compra y con más unidades
    recepcionadas que las que declara el documento (posible doble ingreso).

Por defecto es DRY-RUN (solo reporta). Con --apply BORRA solo las recepciones
pendientes de las categorías indicadas en --categorias (default: anulada).
Borrar 'ya_ingresada' exige nombrarla explícitamente. NUNCA toca stock, kardex
ni recepciones ya creadas: los ajustes de inventario se deciden a mano.

Uso:
    python manage.py compras_revisar_recepciones_pendientes
    python manage.py compras_revisar_recepciones_pendientes --auditar-ingresados
    python manage.py compras_revisar_recepciones_pendientes --apply --categorias anulada
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum, Q

from app.models import Movimientos_Producto, Productos_Recepcionados

CONCEPTOS_INGRESO = ('INGRESO_INICIAL', 'INGRESO_MANUAL')
CATEGORIAS = ('anulada', 'ya_ingresada', 'sin_datos', 'pendiente_real')


class Command(BaseCommand):
    help = (
        'Clasifica las recepciones de compra pendientes de crear (stock fantasma). '
        'DRY-RUN por defecto; --apply borra solo las categorías pedidas.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--compra', type=int, default=None, help='Solo esta compra (id).')
        parser.add_argument('--categorias', default='anulada',
                            help='Con --apply: categorías a borrar, separadas por coma '
                                 f'({", ".join(CATEGORIAS[:2])}). Default: anulada.')
        parser.add_argument('--apply', action='store_true',
                            help='Borra las recepciones pendientes de las categorías pedidas.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Explícito: solo reporta (comportamiento por defecto).')
        parser.add_argument('--auditar-ingresados', action='store_true',
                            help='Agrega el reporte de compras eliminadas con stock ya ingresado '
                                 'y de facturas recepcionadas por sobre lo declarado.')
        parser.add_argument('--detalle', type=int, default=15,
                            help='Filas de ejemplo por categoría (default 15).')

    def handle(self, *args, **opts):
        aplicar = bool(opts['apply']) and not opts['dry_run']
        cats_borrar = {c.strip().lower() for c in (opts['categorias'] or '').split(',') if c.strip()}
        invalidas = cats_borrar - {'anulada', 'ya_ingresada'}
        if invalidas:
            raise CommandError(f'Categorías no borrables: {", ".join(sorted(invalidas))} '
                               '(solo anulada, ya_ingresada).')

        pendientes = (
            Productos_Recepcionados.objects
            .filter(compra_producto_talla__isnull=False, producto_talla__isnull=True)
            .select_related('compra_producto_talla__compra_producto__compras', 'dte')
            .order_by('id')
        )
        if opts['compra']:
            pendientes = pendientes.filter(compra_producto_talla__compra_producto__compras_id=opts['compra'])
        pendientes = list(pendientes)

        # Ingresos en kardex por (dte, artículo, talla) para detectar duplicados.
        dtes = {p.dte_id for p in pendientes if p.dte_id}
        ingresados = set()
        if dtes:
            for dte_id, articulo, talla in (
                Movimientos_Producto.objects
                .filter(dte_id__in=dtes, concepto__in=CONCEPTOS_INGRESO, cantidad__gt=0)
                .values_list('dte_id', 'ProductoTalla__producto__articulo', 'ProductoTalla__talla')
                .distinct()
            ):
                ingresados.add((dte_id, (articulo or '').strip().upper(), (talla or '').strip().upper()))

        clasif = defaultdict(list)
        for p in pendientes:
            cpt = p.compra_producto_talla
            cp = cpt.compra_producto
            compra = cp.compras
            if compra.estado in ('ELIMINADA', 'CANCELADA'):
                cat = 'anulada'
            elif p.dte_id and (p.dte_id, (cp.nombre or '').strip().upper(),
                               (cpt.talla or '').strip().upper()) in ingresados:
                cat = 'ya_ingresada'
            elif not p.dte_id and not p.sucursal_destino_id:
                cat = 'sin_datos'
            else:
                cat = 'pendiente_real'
            clasif[cat].append(p)

        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no se borra nada)'
        self.stdout.write(self.style.WARNING(f'== compras_revisar_recepciones_pendientes: {modo}'))
        self.stdout.write(f'Recepciones de compra pendientes de crear: {len(pendientes)} '
                          f'({sum(int(p.stockArribado or 0) for p in pendientes)} uds)')
        for cat in CATEGORIAS:
            filas = clasif.get(cat, [])
            uds = sum(int(p.stockArribado or 0) for p in filas)
            costo = sum(int(p.stockArribado or 0) * int(p.compra_producto_talla.compra_producto.costo or 0)
                        for p in filas)
            por_compra = Counter(p.compra_producto_talla.compra_producto.compras_id for p in filas)
            self.stdout.write(
                f'  {cat:<15} {len(filas):>5} filas · {uds:>6} uds · ${costo:,.0f} a costo · compras '
                + ', '.join(f'#{c}({n})' for c, n in sorted(por_compra.items())))
            for p in filas[:opts['detalle']]:
                cpt = p.compra_producto_talla
                self.stdout.write(
                    f'      rec {p.id} compra #{cpt.compra_producto.compras_id} '
                    f'({cpt.compra_producto.compras.estado}) {cpt.compra_producto.nombre} T{cpt.talla} '
                    f'{p.stockArribado} u · DTE {p.dte.numero_documento if p.dte else "-"} · '
                    f'suc {p.sucursal_destino_id or "-"} · fecha {p.fecha}')

        if opts['auditar_ingresados']:
            self._auditar_ingresados()

        if not aplicar:
            self.stdout.write(self.style.SUCCESS(
                'Dry-run terminado. Para borrar: --apply --categorias anulada[,ya_ingresada] '
                '(requiere OK explícito del usuario; no toca stock ni kardex).'))
            return

        a_borrar = [p.id for cat in cats_borrar for p in clasif.get(cat, [])]
        with transaction.atomic():
            borradas, _ = (
                Productos_Recepcionados.objects
                .filter(id__in=a_borrar, producto_talla__isnull=True)
                .delete()
            )
        self.stdout.write(self.style.SUCCESS(
            f'Borradas {borradas} recepciones pendientes ({", ".join(sorted(cats_borrar))}).'))

    def _auditar_ingresados(self):
        self.stdout.write(self.style.WARNING('== Compras ELIMINADAS con stock ya ingresado desde sus recepciones'))
        filas = (
            Productos_Recepcionados.objects
            .filter(compra_producto_talla__compra_producto__compras__estado__in=['ELIMINADA', 'CANCELADA'])
            .values('compra_producto_talla__compra_producto__compras_id',
                    'compra_producto_talla__compra_producto__compras__nombre',
                    'compra_producto_talla__compra_producto__compras__fecha_eliminacion')
            .annotate(
                creadas=Count('id', filter=Q(producto_talla__isnull=False)),
                uds_creadas=Sum('stockArribado', filter=Q(producto_talla__isnull=False)),
                pendientes=Count('id', filter=Q(producto_talla__isnull=True)),
                uds_pend=Sum('stockArribado', filter=Q(producto_talla__isnull=True)),
            )
            .order_by('compra_producto_talla__compra_producto__compras_id')
        )
        for f in filas:
            self.stdout.write(
                f'  #{f["compra_producto_talla__compra_producto__compras_id"]} '
                f'{str(f["compra_producto_talla__compra_producto__compras__nombre"])[:35]:<35} '
                f'eliminada {f["compra_producto_talla__compra_producto__compras__fecha_eliminacion"] or "-"} · '
                f'creadas {f["creadas"]} ({f["uds_creadas"] or 0} u) · pendientes {f["pendientes"]} '
                f'({f["uds_pend"] or 0} u)')

        self.stdout.write(self.style.WARNING(
            '== Facturas recepcionadas en más de una compra y por sobre lo declarado'))
        por_dte = (
            Productos_Recepcionados.objects
            .filter(compra_producto_talla__isnull=False, dte__isnull=False,
                    dte__tipo_transaccion='COMPRA')
            .values('dte_id', 'dte__numero_documento', 'dte__tipo_documento',
                    'dte__unidades_productos', 'dte__emisor__nombre')
            .annotate(
                uds=Sum('stockArribado'),
                n_compras=Count('compra_producto_talla__compra_producto__compras', distinct=True),
            )
            .filter(n_compras__gt=1)
            .order_by('dte__numero_documento')
        )
        for f in por_dte:
            declaradas = int(f['dte__unidades_productos'] or 0)
            marca = 'EXCESO' if declaradas and f['uds'] > declaradas else ('sin dato' if not declaradas else 'ok')
            compras = sorted(set(
                Productos_Recepcionados.objects.filter(dte_id=f['dte_id'], compra_producto_talla__isnull=False)
                .values_list('compra_producto_talla__compra_producto__compras_id', flat=True)))
            kardex = (
                Movimientos_Producto.objects
                .filter(dte_id=f['dte_id'], concepto__in=CONCEPTOS_INGRESO)
                .aggregate(s=Sum('cantidad'))['s'] or 0
            )
            self.stdout.write(
                f'  {marca:<8} DTE {f["dte__numero_documento"]} {f["dte__tipo_documento"]} '
                f'({str(f["dte__emisor__nombre"])[:25]}): declara {declaradas or "-"} u · '
                f'recepcionadas {f["uds"]} u en compras {", ".join("#" + str(c) for c in compras)} · '
                f'ingreso kardex {kardex} u')
