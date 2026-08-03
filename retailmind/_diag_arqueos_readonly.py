"""
DIAGNOSTICO READ-ONLY del modulo Cuadratura de Caja / Revision de Arqueos.

NO escribe nada. Solo SELECT.
Uso:  python _diag_arqueos_readonly.py            (ultimos 90 dias)
      python _diag_arqueos_readonly.py 180        (ultimos 180 dias)

Contrasta las hipotesis de la auditoria contra datos reales:
  H1  El KPI "Dif. Efectivo Mes" suma CON SIGNO -> faltantes y sobrantes se anulan.
  H2  El KPI "Dif. Transbank" incluye arqueos sin cierre POS -> faltante artificial.
  H3  Arqueos con diferencia EXACTAMENTE 0 = conteo copiado del teorico, no contado.
  H4  Modo EXPRESS usado como atajo y nunca revisado.
  H5  Doble fuente de verdad: `estado` vs `resultado_revision`.
  H6  Dias con VENTAS pero SIN arqueo (el KPI de dias habiles usa el calendario).
  H7  Depositos declarados y nunca confirmados (plata en el aire).
  H8  Contradiccion entre las 4 formulas de diferencia del modelo.
  H9  Cuantos pasos/estados recorre realmente un arqueo.
  H10 Volumen real de uso por sucursal (¿la pantalla se usa?).
"""
import os
import sys
from collections import defaultdict
from datetime import timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Count, Sum, Q, F, Value, Case, When, IntegerField
from django.utils import timezone

from app.models import ArqueoCaja, DepositoBancario, Sucursal, Ticket, Dte

DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
HOY = timezone.localdate()
DESDE = HOY - timedelta(days=DIAS)

TOL = 1000  # tolerancia usada por el codigo actual


def money(v):
    v = int(v or 0)
    return f"${v:,}".replace(',', '.')


def titulo(t):
    print()
    print('=' * 78)
    print(t)
    print('=' * 78)


base = ArqueoCaja.objects.filter(fecha_arqueo__gte=DESDE, fecha_arqueo__lte=HOY)
total_arqueos = base.count()

print(f"Periodo analizado: {DESDE} -> {HOY}  ({DIAS} dias)")
print(f"Arqueos en el periodo: {total_arqueos:,}")
if total_arqueos == 0:
    print("\nNo hay arqueos en el periodo. Abortando.")
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
titulo("H10 · VOLUMEN DE USO POR SUCURSAL (¿la pantalla se usa?)")
# ─────────────────────────────────────────────────────────────────────────────
filas = (
    base.values('sucursal__alias')
    .annotate(
        n=Count('id'),
        cerrados=Count('id', filter=Q(estado='CERRADO')),
        con_dif=Count('id', filter=Q(estado='CON_DIFERENCIAS')),
        abiertos=Count('id', filter=Q(estado='ABIERTO')),
        revisados=Count('id', filter=Q(estado='REVISADO')),
        dep_decl=Count('id', filter=Q(estado='DEPOSITO_DECLARADO')),
        dep_conf=Count('id', filter=Q(estado='DEPOSITO_CONFIRMADO')),
    )
    .order_by('-n')
)
print(f"{'SUCURSAL':<14}{'ARQ':>6}{'ABIERTO':>9}{'CERRADO':>9}{'C/DIF':>8}"
      f"{'DEP_DEC':>9}{'DEP_CNF':>9}{'REVISADO':>10}")
print('-' * 78)
for f in filas:
    print(f"{(f['sucursal__alias'] or '?'):<14}{f['n']:>6}{f['abiertos']:>9}"
          f"{f['cerrados']:>9}{f['con_dif']:>8}{f['dep_decl']:>9}"
          f"{f['dep_conf']:>9}{f['revisados']:>10}")

por_estado = base.values('estado').annotate(n=Count('id')).order_by('-n')
print("\nDistribucion global por estado:")
for e in por_estado:
    pct = e['n'] / total_arqueos * 100
    print(f"  {e['estado']:<22}{e['n']:>6}  ({pct:5.1f}%)")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H1 · EL KPI 'DIF. EFECTIVO MES' SE ANULA SOLO (suma con signo)")
# ─────────────────────────────────────────────────────────────────────────────
agg = base.aggregate(
    suma_con_signo=Sum('diferencia_efectivo'),
    faltantes=Sum('diferencia_efectivo', filter=Q(diferencia_efectivo__lt=0)),
    sobrantes=Sum('diferencia_efectivo', filter=Q(diferencia_efectivo__gt=0)),
    n_faltantes=Count('id', filter=Q(diferencia_efectivo__lt=0)),
    n_sobrantes=Count('id', filter=Q(diferencia_efectivo__gt=0)),
    n_exactos=Count('id', filter=Q(diferencia_efectivo=0)),
)
suma = agg['suma_con_signo'] or 0
falt = agg['faltantes'] or 0
sobr = agg['sobrantes'] or 0
expo = abs(falt) + abs(sobr)
print(f"  Lo que MUESTRA el KPI hoy (Sum con signo) : {money(suma)}")
print(f"  Faltantes reales ({agg['n_faltantes']:>4} arqueos)        : {money(falt)}")
print(f"  Sobrantes reales ({agg['n_sobrantes']:>4} arqueos)        : {money(sobr)}")
print(f"  EXPOSICION REAL (|falt| + |sobr|)         : {money(expo)}")
if expo > 0:
    ocultado = expo - abs(suma)
    print(f"  --> El KPI actual OCULTA               : {money(ocultado)} "
          f"({ocultado / expo * 100:.1f}% de la exposicion)")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H2 · EL KPI 'DIF. TRANSBANK' MEZCLA ARQUEOS SIN CIERRE POS")
# ─────────────────────────────────────────────────────────────────────────────
tbk = base.aggregate(
    formula_actual=Sum(F('cierre_pos_fisico') - F('total_transbank_teorico'),
                       output_field=IntegerField()),
    sin_cierre=Count('id', filter=Q(cierre_pos_fisico=0,
                                    total_transbank_teorico__gt=0)),
    con_cierre=Count('id', filter=Q(cierre_pos_fisico__gt=0)),
    teorico_sin_cierre=Sum('total_transbank_teorico',
                           filter=Q(cierre_pos_fisico=0,
                                    total_transbank_teorico__gt=0)),
)
solo_con = base.filter(cierre_pos_fisico__gt=0).aggregate(
    real=Sum(F('cierre_pos_fisico') - F('total_transbank_teorico'),
             output_field=IntegerField()))['real'] or 0
print(f"  Formula actual (TODOS los arqueos)        : {money(tbk['formula_actual'])}")
print(f"  Arqueos SIN cierre POS pero CON venta TBK : {tbk['sin_cierre']:,}")
print(f"  Teorico TBK de esos arqueos (ruido puro)  : {money(tbk['teorico_sin_cierre'])}")
print(f"  Arqueos CON cierre POS informado          : {tbk['con_cierre']:,}")
print(f"  Diferencia REAL (solo los que informaron) : {money(solo_con)}")
if total_arqueos:
    pct = tbk['sin_cierre'] / total_arqueos * 100
    print(f"  --> {pct:.1f}% de los arqueos contamina el KPI con un faltante inventado")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H3 · CONTEOS 'PERFECTOS' (diferencia exacta 0 = copiado del teorico)")
# ─────────────────────────────────────────────────────────────────────────────
exactos = agg['n_exactos']
con_venta_efvo = base.filter(total_efectivo_teorico__gt=0).count()
exactos_con_venta = base.filter(diferencia_efectivo=0,
                                total_efectivo_teorico__gt=0).count()
print(f"  Arqueos con diferencia EXACTA $0          : {exactos:,} / {total_arqueos:,} "
      f"({exactos / total_arqueos * 100:.1f}%)")
print(f"  ... de los que SI tuvieron venta efectivo : {exactos_con_venta:,} / "
      f"{con_venta_efvo:,} "
      f"({(exactos_con_venta / con_venta_efvo * 100) if con_venta_efvo else 0:.1f}%)")
print("  Referencia: un conteo manual real casi nunca da 0 exacto.")
print("  Sobre ~80% es la senal clasica de 'copie el teorico y cerre'.")

print("\n  Por sucursal (solo dias con venta en efectivo):")
por_suc_ex = (
    base.filter(total_efectivo_teorico__gt=0)
    .values('sucursal__alias')
    .annotate(n=Count('id'), ex=Count('id', filter=Q(diferencia_efectivo=0)))
    .order_by('-n')
)
print(f"  {'SUCURSAL':<14}{'DIAS':>7}{'EXACTOS':>9}{'%':>8}   SENAL")
print('  ' + '-' * 60)
for r in por_suc_ex:
    p = r['ex'] / r['n'] * 100 if r['n'] else 0
    senal = 'SOSPECHOSO' if p >= 80 else ('revisar' if p >= 60 else '')
    print(f"  {(r['sucursal__alias'] or '?'):<14}{r['n']:>7}{r['ex']:>9}{p:>7.1f}%   {senal}")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H4 · MODO EXPRESS (atajo de conteo)")
# ─────────────────────────────────────────────────────────────────────────────
exp = base.aggregate(
    express=Count('id', filter=Q(modo_conteo='EXPRESS')),
    express_sin_rev=Count('id', filter=Q(modo_conteo='EXPRESS') & ~Q(estado='REVISADO')),
    flag_pendiente=Count('id', filter=Q(requiere_revision_express=True)),
)
print(f"  Arqueos en modo EXPRESS                   : {exp['express']:,} "
      f"({exp['express'] / total_arqueos * 100:.1f}%)")
print(f"  EXPRESS que NUNCA se revisaron            : {exp['express_sin_rev']:,}")
print(f"  Con flag requiere_revision_express=True   : {exp['flag_pendiente']:,}")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H5 · DOBLE FUENTE DE VERDAD: estado vs resultado_revision")
# ─────────────────────────────────────────────────────────────────────────────
cruce = defaultdict(int)
for r in base.values('estado', 'resultado_revision').annotate(n=Count('id')):
    cruce[(r['estado'], r['resultado_revision'])] = r['n']
estados = sorted({k[0] for k in cruce})
resultados = sorted({k[1] for k in cruce})
print(f"  {'estado \\ resultado':<24}" + ''.join(f"{r[:13]:>15}" for r in resultados))
print('  ' + '-' * (24 + 15 * len(resultados)))
for e in estados:
    print(f"  {e:<24}" + ''.join(f"{cruce.get((e, r), 0):>15}" for r in resultados))

incoh_a = base.filter(estado='REVISADO', resultado_revision='PENDIENTE').count()
incoh_b = base.exclude(estado='REVISADO').exclude(resultado_revision='PENDIENTE').count()
print(f"\n  INCOHERENTES estado=REVISADO pero resultado=PENDIENTE : {incoh_a:,}")
print(f"  INCOHERENTES resultado decidido pero estado != REVISADO: {incoh_b:,}")
print("  (el filtro 'Revisados' de la UI usa `estado`; los badges usan `resultado_revision`)")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H6 · DIAS CON VENTA PERO SIN ARQUEO (el KPI usa el calendario, no la venta)")
# ─────────────────────────────────────────────────────────────────────────────
print("  Calculando dias con venta real por sucursal... (puede tardar)")
ventas_por_suc = defaultdict(set)
for r in (Ticket.objects.filter(fecha__gte=DESDE, fecha__lte=HOY, estado='PAGADO')
          .values('sucursal_id', 'fecha').distinct()):
    ventas_por_suc[r['sucursal_id']].add(r['fecha'])

arqueos_por_suc = defaultdict(set)
for r in base.values('sucursal_id', 'fecha_arqueo'):
    arqueos_por_suc[r['sucursal_id']].add(r['fecha_arqueo'])

alias = {s.id: s.alias for s in Sucursal.objects.all()}
print(f"\n  {'SUCURSAL':<14}{'D/VENTA':>9}{'D/ARQUEO':>10}{'SIN ARQUEO':>12}{'DOM. C/VENTA':>14}")
print('  ' + '-' * 62)
tot_huecos = 0
for sid, dias_v in sorted(ventas_por_suc.items(),
                          key=lambda x: -len(x[1])):
    dias_a = arqueos_por_suc.get(sid, set())
    huecos = dias_v - dias_a
    domingos = {d for d in dias_v if d.weekday() == 6}
    tot_huecos += len(huecos)
    print(f"  {alias.get(sid, f'id={sid}'):<14}{len(dias_v):>9}{len(dias_a):>10}"
          f"{len(huecos):>12}{len(domingos):>14}")
print(f"\n  TOTAL dias-sucursal vendidos sin arqueo: {tot_huecos:,}")
print("  Los domingos con venta HOY NO se cuentan como dia habil en el KPI")
print("  (`dia_actual.weekday() < 6`) -> nunca aparecen como faltantes.")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H7 · DEPOSITOS DECLARADOS Y NUNCA CONFIRMADOS (plata en el aire)")
# ─────────────────────────────────────────────────────────────────────────────
dep = DepositoBancario.objects.filter(arqueo__fecha_arqueo__gte=DESDE)
d_agg = dep.aggregate(
    total=Count('id'),
    declarados=Count('id', filter=Q(monto_declarado__gt=0)),
    pendientes=Count('id', filter=Q(verificado=False, monto_declarado__gt=0)),
    monto_pend=Sum('monto_declarado', filter=Q(verificado=False, monto_declarado__gt=0)),
    verificados=Count('id', filter=Q(verificado=True)),
    con_dif=Count('id', filter=Q(verificado=True, monto_confirmado__gt=0)
                  & ~Q(monto_confirmado=F('monto_declarado'))
                  & Q(monto_declarado__gt=0)),
    sin_comprobante=Count('id', filter=Q(verificado=True, numero_comprobante='')),
)
print(f"  Depositos en el periodo                   : {d_agg['total']:,}")
print(f"  Declarados por cajero                     : {d_agg['declarados']:,}")
print(f"  PENDIENTES de confirmar                   : {d_agg['pendientes']:,}")
print(f"  Monto pendiente de confirmar              : {money(d_agg['monto_pend'])}")
print(f"  Verificados                               : {d_agg['verificados']:,}")
print(f"  Verificados con monto != declarado        : {d_agg['con_dif']:,}")
print(f"  Verificados SIN numero de comprobante     : {d_agg['sin_comprobante']:,}")

viejos = dep.filter(verificado=False, monto_declarado__gt=0).order_by('fecha_declaracion')[:10]
if viejos:
    print("\n  Los 10 mas antiguos sin confirmar:")
    for d in viejos:
        edad = (HOY - d.arqueo.fecha_arqueo).days
        print(f"    #{d.id:<7} {d.arqueo.sucursal.alias:<12} arqueo {d.arqueo.fecha_arqueo} "
              f"({edad:>3}d)  declarado {money(d.monto_declarado)}")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H8 · LAS 4 FORMULAS DE DIFERENCIA SE CONTRADICEN")
# ─────────────────────────────────────────────────────────────────────────────
print("  El modelo expone 4 nociones distintas de 'diferencia':")
print("    A) diferencia_efectivo          = fisico - (teorico + fondo_fijo)")
print("    B) diferencia_efectivo_real     = (fisico - depositos - fondo) - teorico")
print("    C) diferencia_deposito_vs_teorico = dep_efectivo_verificado - teorico")
print("    D) diferencia_total_real        = B + diferencia_transbank")
print()
muestra = list(base.filter(cache_total_depositos__gt=0)
               .select_related('sucursal')
               .order_by('-fecha_arqueo')[:15])
if not muestra:
    print("  (no hay arqueos con depositos en el periodo para comparar)")
else:
    print(f"  {'FECHA':<12}{'SUC':<10}{'A':>12}{'B':>12}{'C':>12}  VEREDICTOS")
    print('  ' + '-' * 74)
    contradicen = 0
    for a in muestra:
        A = a.diferencia_efectivo
        B = a.diferencia_efectivo_real
        C = a.diferencia_deposito_vs_teorico
        vs = []
        for nombre, v in (('A', A), ('B', B), ('C', C)):
            vs.append('OK' if abs(v) <= TOL else ('FALT' if v < 0 else 'SOBR'))
        if len(set(vs)) > 1:
            contradicen += 1
        print(f"  {str(a.fecha_arqueo):<12}{a.sucursal.alias[:9]:<10}"
              f"{money(A):>12}{money(B):>12}{money(C):>12}  {'/'.join(vs)}")
    print(f"\n  {contradicen} de {len(muestra)} arqueos de la muestra reciben "
          f"VEREDICTOS DISTINTOS segun la formula.")

# Cuantificacion global del choque A vs B
glob = base.filter(cache_total_depositos__gt=0)
n_glob = glob.count()
if n_glob:
    a_ok_b_mal = sum(
        1 for a in glob.only(
            'total_efectivo_fisico', 'total_efectivo_teorico', 'fondo_fijo_snapshot',
            'diferencia_efectivo', 'cache_total_depositos', 'cache_depositos_actualizado')
        if abs(a.diferencia_efectivo) <= TOL and abs(a.diferencia_efectivo_real) > TOL
    )
    print(f"\n  Arqueos CON deposito donde A dice OK y B dice descuadre: "
          f"{a_ok_b_mal:,} / {n_glob:,}")
    print("  Causa: B resta los depositos a un efectivo fisico que ya fue contado")
    print("  DESPUES de depositar -> descuenta la plata dos veces.")


# ─────────────────────────────────────────────────────────────────────────────
titulo("H9 · CUANTOS PASOS RECORRE UN ARQUEO (reaperturas y bitacora)")
# ─────────────────────────────────────────────────────────────────────────────
pasos = base.aggregate(
    con_reapertura=Count('id', filter=Q(historial_reaperturas__isnull=False),
                         distinct=True),
    con_bitacora=Count('id', filter=Q(bitacora__isnull=False), distinct=True),
    con_obs_dif=Count('id', filter=~Q(observaciones_diferencia='') &
                      Q(observaciones_diferencia__isnull=False)),
    con_categoria=Count('id', filter=~Q(categoria_diferencia='') &
                        Q(categoria_diferencia__isnull=False)),
)
print(f"  Arqueos reabiertos al menos una vez       : {pasos['con_reapertura']:,}")
print(f"  Arqueos con observaciones en bitacora     : {pasos['con_bitacora']:,}")
print(f"  Arqueos con observacion de diferencia     : {pasos['con_obs_dif']:,}")
print(f"  Arqueos con categoria de diferencia       : {pasos['con_categoria']:,}")

sin_cat = base.filter(Q(categoria_diferencia='') | Q(categoria_diferencia__isnull=True))
sin_cat_grande = sin_cat.filter(
    Q(diferencia_efectivo__gt=5000) | Q(diferencia_efectivo__lt=-5000)).count()
print(f"\n  Diferencias > $5.000 SIN categoria asignada: {sin_cat_grande:,}")
print("  (el modelo dice 'obligatorio si >$5.000' pero nada lo obliga)")

try:
    from app.models import LogAccionCaja
    print("\n  Acciones registradas en el periodo (LogAccionCaja):")
    for r in (LogAccionCaja.objects.filter(timestamp__date__gte=DESDE)
              .values('accion').annotate(n=Count('id')).order_by('-n')):
        print(f"    {r['accion']:<24}{r['n']:>8,}")
except Exception as e:
    print(f"  (no se pudo leer LogAccionCaja: {e})")


# ─────────────────────────────────────────────────────────────────────────────
titulo("RESUMEN EJECUTIVO")
# ─────────────────────────────────────────────────────────────────────────────
print(f"  Arqueos analizados                        : {total_arqueos:,}")
print(f"  Exposicion real en efectivo (|f|+|s|)     : {money(expo)}")
print(f"  ... que el KPI actual reporta como        : {money(suma)}")
print(f"  Conteos exactos (posible copia teorico)   : "
      f"{exactos / total_arqueos * 100:.1f}%")
print(f"  Dias-sucursal vendidos sin arqueo         : {tot_huecos:,}")
print(f"  Depositos declarados sin confirmar        : {d_agg['pendientes']:,} "
      f"por {money(d_agg['monto_pend'])}")
print(f"  Diferencias >$5k sin categorizar          : {sin_cat_grande:,}")
print()
print("  (script read-only: no modifico ningun dato)")
