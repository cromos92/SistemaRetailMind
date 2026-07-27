"""
Verificacion READ-ONLY del fix de totales inflados en la grilla de Gestion de
Compras (endpoint obtener_compras_por_anio, app/views.py).

No escribe nada: solo SELECT. Compara, para 5 compras reales (incluida la #14,
caso testigo de la auditoria):

  - VIEJO  : el annotate() combinado (dos Sum sobre relaciones multivaluadas
             distintas en una sola query)  -> inflado
  - NUEVO  : las subconsultas correlacionadas separadas
  - REAL   : suma en Python sobre las filas crudas, sin ORM aggregate

Uso:
    cd retailmind
    python ..\\<ruta>\\_verificar_compras_grid.py
"""
import os
import sys

import django

sys.path.insert(0, os.getcwd())  # se ejecuta desde retailmind/ (donde esta manage.py)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value  # noqa: E402
from django.db.models.functions import Coalesce  # noqa: E402

from app.models import Compras, Compras_Producto_Talla, Productos_Recepcionados  # noqa: E402


# ---------------------------------------------------------------- VIEJO
def annotate_viejo(qs):
    return qs.annotate(
        unidades_totales=Sum('compras_producto__compras_producto_talla__stock'),
        costo_total=Sum(
            F('compras_producto__compras_producto_talla__stock')
            * F('compras_producto__costo')
        ),
        total_recepcionado=Sum(
            'compras_producto__compras_producto_talla__productos_recepcionados__stockArribado'
        ),
        pendientes_crear=Count(
            'compras_producto__compras_producto_talla__productos_recepcionados',
            filter=Q(
                compras_producto__compras_producto_talla__productos_recepcionados__producto_talla__isnull=True
            ),
        ),
    )


# ---------------------------------------------------------------- NUEVO
def annotate_nuevo(qs):
    unidades_sq = (
        Compras_Producto_Talla.objects
        .filter(compra_producto__compras=OuterRef('pk'))
        .order_by().values('compra_producto__compras')
        .annotate(total=Sum('stock')).values('total')[:1]
    )
    costo_sq = (
        Compras_Producto_Talla.objects
        .filter(compra_producto__compras=OuterRef('pk'))
        .order_by().values('compra_producto__compras')
        .annotate(total=Sum(F('stock') * F('compra_producto__costo'))).values('total')[:1]
    )
    recepcionado_sq = (
        Productos_Recepcionados.objects
        .filter(compra_producto_talla__compra_producto__compras=OuterRef('pk'))
        .order_by().values('compra_producto_talla__compra_producto__compras')
        .annotate(total=Sum('stockArribado')).values('total')[:1]
    )
    pendientes_sq = (
        Productos_Recepcionados.objects
        .filter(
            compra_producto_talla__compra_producto__compras=OuterRef('pk'),
            producto_talla__isnull=True,
        )
        .order_by().values('compra_producto_talla__compra_producto__compras')
        .annotate(total=Count('id')).values('total')[:1]
    )
    return qs.annotate(
        unidades_totales=Coalesce(Subquery(unidades_sq, output_field=IntegerField()), Value(0)),
        costo_total=Coalesce(Subquery(costo_sq, output_field=IntegerField()), Value(0)),
        total_recepcionado=Coalesce(Subquery(recepcionado_sq, output_field=IntegerField()), Value(0)),
        pendientes_crear=Coalesce(Subquery(pendientes_sq, output_field=IntegerField()), Value(0)),
    )


# ---------------------------------------------------------------- REAL (Python)
def valores_reales(compra_id):
    filas = list(
        Compras_Producto_Talla.objects
        .filter(compra_producto__compras_id=compra_id)
        .values_list('id', 'stock', 'compra_producto__costo')
    )
    unidades = sum(int(f[1] or 0) for f in filas)
    costo = sum(int(f[1] or 0) * int(f[2] or 0) for f in filas)

    recep = list(
        Productos_Recepcionados.objects
        .filter(compra_producto_talla__compra_producto__compras_id=compra_id)
        .values_list('id', 'stockArribado', 'producto_talla_id')
    )
    recepcionado = sum(int(r[1] or 0) for r in recep)
    pendientes = sum(1 for r in recep if r[2] is None)
    return {
        'tallas': len(filas),
        'recepciones': len(recep),
        'unidades': unidades,
        'costo': costo,
        'recepcionado': recepcionado,
        'pendientes_crear': pendientes,
    }


def main():
    # #14 es el caso testigo de la auditoria. Se completan hasta 5 compras con
    # las que mas recepciones tienen (que son las que sufren el inflado).
    ids = [14]
    candidatas = (
        Compras.objects.exclude(estado='ELIMINADA')
        .annotate(
            n_recep=Count('compras_producto__compras_producto_talla__productos_recepcionados')
        )
        .order_by('-n_recep')
        .values_list('id', flat=True)[:12]
    )
    for cid in candidatas:
        if cid not in ids and len(ids) < 5:
            ids.append(cid)

    viejo = {
        r['id']: r for r in annotate_viejo(Compras.objects.filter(id__in=ids)).values(
            'id', 'unidades_totales', 'costo_total', 'total_recepcionado', 'pendientes_crear'
        )
    }
    nuevo = {
        r['id']: r for r in annotate_nuevo(Compras.objects.filter(id__in=ids)).values(
            'id', 'unidades_totales', 'costo_total', 'total_recepcionado', 'pendientes_crear'
        )
    }

    fallos = 0
    print('=' * 108)
    print('COMPARATIVA POR COMPRA  (REAL = suma en Python sobre filas crudas)')
    print('=' * 108)
    for cid in ids:
        real = valores_reales(cid)
        v = viejo.get(cid, {})
        n = nuevo.get(cid, {})
        c = Compras.objects.filter(id=cid).values('nombre', 'estado', 'fecha').first()
        print('')
        print(f"COMPRA #{cid}  {c['nombre'][:45] if c else '?'}  "
              f"({c['estado'] if c else '?'}, {c['fecha'] if c else '?'})")
        print(f"  tallas={real['tallas']}  recepciones={real['recepciones']}")
        print(f"  {'metrica':<18}{'REAL':>14}{'VIEJO':>16}{'NUEVO':>16}   veredicto")
        for etiqueta, key_real, key_ann in (
            ('unidades', 'unidades', 'unidades_totales'),
            ('costo', 'costo', 'costo_total'),
            ('recepcionado', 'recepcionado', 'total_recepcionado'),
            ('pendientes_crear', 'pendientes_crear', 'pendientes_crear'),
        ):
            r = real[key_real]
            vv = int(v.get(key_ann) or 0)
            nn = int(n.get(key_ann) or 0)
            ok_nuevo = (nn == r)
            if not ok_nuevo:
                fallos += 1
            marca = 'NUEVO OK' if ok_nuevo else 'NUEVO FALLA'
            if vv != r:
                marca += f'  | VIEJO inflado en {vv - r:+,}'
            print(f"  {etiqueta:<18}{r:>14,}{vv:>16,}{nn:>16,}   {marca}")

    # --------------------------------------------------- paginacion determinista
    print('')
    print('=' * 108)
    print('PAGINACION: ¿el orden es estable y sin solapes?')
    print('=' * 108)
    anio = Compras.objects.exclude(estado='ELIMINADA').order_by('-fecha').values_list(
        'fecha__year', flat=True).first()
    base = Compras.objects.filter(fecha__year=anio).exclude(estado='ELIMINADA')
    sin_orden_1 = list(base.values_list('id', flat=True)[0:20])
    sin_orden_2 = list(base.values_list('id', flat=True)[20:40])
    con_orden = base.order_by('-fecha', '-id')
    ord_1 = list(con_orden.values_list('id', flat=True)[0:20])
    ord_2 = list(con_orden.values_list('id', flat=True)[20:40])
    solape_sin = set(sin_orden_1) & set(sin_orden_2)
    solape_con = set(ord_1) & set(ord_2)
    print(f"  anio de prueba: {anio}   total compras: {base.count()}")
    print(f"  SIN order_by  -> solape pagina1/pagina2: {len(solape_sin)} id(s) {sorted(solape_sin)[:5]}")
    print(f"  CON order_by  -> solape pagina1/pagina2: {len(solape_con)}")
    # repetibilidad
    ord_1_bis = list(con_orden.values_list('id', flat=True)[0:20])
    print(f"  CON order_by  -> pagina 1 repetible: {ord_1 == ord_1_bis}")

    print('')
    print('=' * 108)
    print(f"RESULTADO: {'TODO OK' if fallos == 0 else str(fallos) + ' METRICAS DEL NUEVO NO CUADRAN'}")
    print('=' * 108)


if __name__ == '__main__':
    main()
