# -*- coding: utf-8 -*-
"""READ-ONLY: en que traspasos el TOTAL que muestra la pantalla INCLUYE las
unidades que ya tienen NC.

La pantalla /app/recepcion-dte/ muestra:
    total_unidades = SUM(Dte_Productos.stock) de las lineas activo=True
y al lado un badge "N NC (-X uds)".

Segun por donde se emitio la NC, esas X unidades pueden estar YA restadas del
total (NC pre-recepcion: las lineas se reducen) o SEGUIR DENTRO del total
(NC post-recepcion: las lineas quedan intactas a proposito).

Este script clasifica cada caso con evidencia cruda.

NO ESCRIBE NADA.
"""
from datetime import timedelta
from django.db.models import Sum
from django.utils import timezone
from app.models import Dte, Dte_Productos, Movimientos_Producto

DIAS = 120
desde = timezone.localdate() - timedelta(days=DIAS)

# Traspasos con al menos una NC/ajuste hijo vivo
hijos = list(
    Dte.objects.filter(
        documento_afectado__isnull=False,
        documento_afectado__tipo_transaccion='TRASPASO',
        estado_dte__in=['EMITIDO', 'ACEPTADO'],
        documento_afectado__fecha_emision__gte=desde,
    )
    .select_related('documento_afectado')
    .order_by('documento_afectado_id', 'id')
)

por_padre = {}
for h in hijos:
    por_padre.setdefault(h.documento_afectado_id, []).append(h)

print(f"Traspasos con NC/ajuste vivo emitidos desde {desde}: {len(por_padre)}")
print()

inflados = []
ok = []
dudosos = []

for padre_id, ncs in por_padre.items():
    padre = ncs[0].documento_afectado

    activas = int(
        Dte_Productos.objects.filter(dte_id=padre_id, activo=True)
        .aggregate(s=Sum('stock'))['s'] or 0
    )
    uds_nc = 0
    detalle_ncs = []
    for h in ncs:
        u = int(
            Dte_Productos.objects.filter(dte_id=h.id).aggregate(s=Sum('stock'))['s'] or 0
        ) or int(h.unidades_productos or 0)
        uds_nc += u
        conceptos = sorted({
            m.concepto for m in Movimientos_Producto.objects.filter(dte_id=h.id)
        })
        detalle_ncs.append((h, u, conceptos))

    ref = (padre.referencias or '')
    marca_pre = '[AJUSTE EMISOR]' in ref
    marca_post = '[NC POST-RECEPCIÓN]' in ref or '[NC POST-RECEPCION]' in ref

    salida = abs(int(
        Movimientos_Producto.objects
        .filter(dte_id=padre_id, concepto='TRASPASO_SALIDA')
        .aggregate(s=Sum('cantidad'))['s'] or 0
    ))
    entrada = int(
        Movimientos_Producto.objects
        .filter(dte_id=padre_id, concepto='TRASPASO_ENTRADA')
        .aggregate(s=Sum('cantidad'))['s'] or 0
    )

    # Clasificacion: las unidades con NC siguen contadas dentro de `activas`?
    if marca_post:
        veredicto = 'INFLADO (NC post-recepcion: las lineas NO se reducen)'
    elif marca_pre and not marca_post:
        veredicto = 'OK (NC pre-recepcion: lineas ya reducidas)'
    else:
        # sin marca textual -> deducir por movimientos de la NC
        conceptos_todos = {c for _, _, cs in detalle_ncs for c in cs}
        if conceptos_todos & {'DEVOLUCION_NC_PENDIENTE_DESPACHO',
                              'DEVOLUCION_NC_POST_RECEPCION',
                              'SOBRANTE_ABSORBIDO_ORIGEN'}:
            veredicto = 'INFLADO (NC post-recepcion por movimientos)'
        else:
            veredicto = 'DUDOSO (sin marca; revisar a mano)'

    fila = {
        'padre': padre, 'activas': activas, 'uds_nc': uds_nc,
        'salida': salida, 'entrada': entrada, 'veredicto': veredicto,
        'ncs': detalle_ncs,
    }
    if veredicto.startswith('INFLADO'):
        inflados.append(fila)
    elif veredicto.startswith('OK'):
        ok.append(fila)
    else:
        dudosos.append(fila)


def _dump(titulo, filas):
    print("=" * 118)
    print(f"{titulo}  ({len(filas)} DTEs)")
    print("=" * 118)
    for f in filas:
        p = f['padre']
        print(f"\n  DTE #{p.numero_documento} (id={p.id}) | {p.tipo_documento} | estado={p.estado_dte} | "
              f"f_emision={p.fecha_emision} | f_recepcion={p.fecha_recepcion} | "
              f"suc={p.sucursal.alias if p.sucursal else '-'}")
        print(f"     total que MUESTRA la pantalla (lineas activas) = {f['activas']}   "
              f"| header unidades_productos = {p.unidades_productos}")
        print(f"     uds con NC = {f['uds_nc']}   -> badge diria '(-{f['uds_nc']} uds)'")
        print(f"     kardex: TRASPASO_SALIDA={f['salida']}  TRASPASO_ENTRADA={f['entrada']}")
        if f['veredicto'].startswith('INFLADO'):
            print(f"     >>> el total {f['activas']} INCLUYE las {f['uds_nc']} de la NC. "
                  f"Neto real = {f['activas'] - f['uds_nc']}")
        for h, u, conceptos in f['ncs']:
            print(f"     NC/ajuste #{h.numero_documento} (id={h.id}) | {h.tipo_documento} | "
                  f"{u} uds | movs={conceptos or 'NINGUNO'} | motivo={(h.motivo_nc or '')[:45]}")
    print()


_dump("A) TOTAL INFLADO — las unidades con NC siguen contadas", inflados)
_dump("B) DUDOSO — revisar a mano", dudosos)
_dump("C) OK — la NC ya esta descontada del total", ok)

print("=" * 118)
print("RESUMEN")
print("=" * 118)
print(f"  INFLADOS : {len(inflados):3}  (uds mal contadas: "
      f"{sum(f['uds_nc'] for f in inflados)})")
print(f"  DUDOSOS  : {len(dudosos):3}")
print(f"  OK       : {len(ok):3}")
print()
print("FIN (read-only)")
