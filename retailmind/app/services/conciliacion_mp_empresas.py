"""
Conciliación Mercado Pago vista POR EMPRESA.

Cada empresa (RUT) tiene UNA cuenta de Mercado Pago que junta la plata de todas
sus tiendas y cajas; los retiros al banco son de esa cuenta, no de una tienda.
Sobre lo que ya guarda `conciliacion_mp_service` (retiros con su desglose del
reporte de Liberaciones y cobros amarrados), este módulo arma lo que muestran
las pestañas «Liberaciones y banco» y «Asignación de retiros»:

- dónde está hoy la plata de cada empresa: por liberar → disponible en Mercado
  Pago → enviada al banco → abonada (vista en la cartola), y
- qué se llevó cada retiro, en partes que se entienden: ventas del POS, pagos
  «MP manual», pagos que no pasan por el POS y saldo que ya estaba en la cuenta.

Solo lectura: no escribe nada.
"""
import logging
import re
from datetime import timedelta

from django.db.models import Count, Max, Min, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from app.models import MercadoPagoConfig, RetiroMercadoPago, TransaccionMercadoPago
from app.services import conciliacion_mp_service as conc

logger = logging.getLogger('app')

# Partes de un retiro, en el orden en que se pintan. (etiqueta, explicación)
PARTES = {
    'pos': ('Ventas del POS', 'Cobros de la Point o del QR que tienen su venta en el sistema.'),
    'manual': ('MP manual', 'Pagos cobrados en la Point sin integración y anotados a mano en el POS con su N° de operación.'),
    'otros': ('Sin venta en el sistema', 'Pagos que Mercado Pago liberó y el sistema no tiene como venta: '
                                         'ventas online, links de pago o cobros que nadie registró.'),
    'anterior': ('Saldo anterior', 'Plata que ya estaba en la cuenta de Mercado Pago antes de las ventas que el '
                                   'sistema tiene registradas (por ejemplo, acumulada antes de conectar el POS).'),
    'sin_explicar': ('Sin explicar', 'El retiro es mayor que lo liberado que muestra el reporte de Mercado Pago.'),
}

_RE_QUEDAN = re.compile(r'quedaron \$([\d\.]+)')


def _neto():
    return Coalesce('monto_neto', 'monto')


def _fecha(dt):
    return timezone.localtime(dt).strftime('%d/%m/%Y') if dt else ''


def empresas_mp(sucursal_id=None):
    """Una entrada por cuenta de Mercado Pago (empresa/RUT) con sus cajas y tiendas.

    Misma regla que el cobro para saber la cuenta de una caja (`_cuenta_efectiva`:
    la elegida en la caja o la de la empresa de la sucursal). Una caja sin cuenta
    resoluble queda sola. Con `sucursal_id`, solo la empresa de esa tienda.
    """
    grupos = {}
    for cfg in (MercadoPagoConfig.objects.select_related('sucursal__empresa', 'cuenta__empresa')
                .order_by('-habilitado', 'id')):
        cuenta = conc._cuenta_efectiva(cfg)
        if cuenta is not None:
            clave, empresa = f'c{cuenta.id}', cuenta.empresa
        else:
            clave, empresa = f'cfg{cfg.id}', (cfg.sucursal.empresa if cfg.sucursal_id else None)
        g = grupos.setdefault(clave, {
            'clave': clave, 'cuenta_id': cuenta.id if cuenta else None,
            # caja cualquiera de la cuenta (la primera habilitada): la usan las APIs por cuenta
            'config_id': cfg.id,
            'nombre': getattr(empresa, 'nombre', '') or 'Cuenta Mercado Pago',
            'rut': getattr(empresa, 'rut', '') or '',
            'configs': [], 'tiendas': [], 'sucursales': [],
        })
        g['configs'].append(cfg.id)
        tienda = cfg.sucursal.alias if cfg.sucursal_id else cfg.nombre
        if tienda and tienda not in g['tiendas']:
            g['tiendas'].append(tienda)
        if cfg.sucursal_id and cfg.sucursal_id not in g['sucursales']:
            g['sucursales'].append(cfg.sucursal_id)
    salida = [g for g in grupos.values()
              if not sucursal_id or int(sucursal_id) in g['sucursales']]
    for g in salida:
        g['tiendas'].sort()
    return sorted(salida, key=lambda g: (g['nombre'] or '').lower())


def _parte_de_caja(etiqueta):
    """('pos'|'manual'|'otros'|'anterior'|'sin_explicar', tienda) de una
    etiqueta del desglose `por_caja` que guarda el reporte de Liberaciones."""
    et = str(etiqueta or '')
    if et == conc.CAJA_SALDO_ANTERIOR:
        return 'anterior', ''
    if et == conc.CAJA_NO_EXPLICADA:
        return 'sin_explicar', ''
    if et.startswith(('Sin caja', 'Caja MP «')):
        return 'otros', ''
    tienda, sep, caja = et.partition(' · ')
    if sep and caja == 'MP manual':
        return 'manual', tienda
    return 'pos', tienda if sep else et


def cuanto_quedo(retiro):
    """Lo que quedó disponible en Mercado Pago después del retiro: 0 = se llevó
    todo; > 0 = retiro por un monto (parcial); None = no se sabe (sin desglose)."""
    raw = retiro.raw_reporte if isinstance(retiro.raw_reporte, dict) else {}
    if raw.get('quedan') is not None:
        try:
            return int(raw['quedan'])
        except (TypeError, ValueError):
            pass
    if not raw.get('por_caja'):
        return None
    if retiro.estado == 'CON_DIFERENCIA':
        return 0   # el retiro supera lo liberado: no quedó nada
    m = _RE_QUEDAN.search(retiro.detalle_diferencia or '')
    return int(m.group(1).replace('.', '')) if m else 0


def composicion_retiro(retiro, neto_amarradas=0, con_cobro=frozenset()):
    """Qué se llevó un retiro, en pesos por parte (ver PARTES), más el reparto por
    tienda según la caja por la que entró la plata.

    `con_cobro`: N° de operación que el reporte contó «sin venta» y que después sí
    tuvieron su cobro (asociados a mano o importados): cuentan como ventas.
    """
    raw = retiro.raw_reporte if isinstance(retiro.raw_reporte, dict) else {}
    por_caja = [c for c in (raw.get('por_caja') or []) if isinstance(c, dict)]
    partes = dict.fromkeys(PARTES, 0)
    tiendas = {}
    if por_caja:
        for c in por_caja:
            parte, tienda = _parte_de_caja(c.get('caja'))
            monto = int(c.get('monto') or 0)
            if parte in ('anterior', 'sin_explicar', 'manual'):
                partes[parte] += monto
            if tienda:
                t = tiendas.setdefault(tienda, {'tienda': tienda, 'monto': 0, 'manual': 0})
                t['monto'] += monto
                if parte == 'manual':
                    t['manual'] += monto
        # «Sin venta» con la misma regla que el resto de la pantalla: incluye pagos
        # que entraron por una caja conocida pero no tienen venta en el sistema.
        sin_venta = int(conc._sin_venta_de(raw, con_cobro) or 0)
        partes['otros'] = max(0, sin_venta - partes['anterior'] - partes['sin_explicar'])
        partes['pos'] = max(0, retiro.monto - partes['anterior'] - partes['sin_explicar']
                            - partes['manual'] - partes['otros'])
    else:
        # Guardado antes del desglose (o abierto para recalcular): lo explican
        # solo sus ventas amarradas.
        partes['pos'] = min(retiro.monto, max(0, int(neto_amarradas or 0)))
        partes['sin_explicar'] = retiro.monto - partes['pos']
    identificado = partes['pos'] + partes['manual']
    return {
        'partes': partes,
        'identificado': identificado,
        'pct_identificado': round(100 * identificado / retiro.monto) if retiro.monto else None,
        'tiendas': sorted(tiendas.values(), key=lambda t: -t['monto']),
        'con_desglose': bool(por_caja),
    }


def _con_cobro(retiros):
    """N° de operación que algún retiro contó «sin venta» y hoy sí tienen cobro."""
    claves = set()
    for r in retiros:
        raw = r.raw_reporte if isinstance(r.raw_reporte, dict) else {}
        claves.update(str(k) for k in (raw.get('sin_local') or {}))
    if not claves:
        return frozenset()
    return frozenset(TransaccionMercadoPago.objects.filter(tipo='VENTA', payment_id_mp__in=list(claves))
                     .values_list('payment_id_mp', flat=True))


def describir_retiros(retiros, alias_tienda=''):
    """Filas listas para pintar de una lista de RetiroMercadoPago: composición,
    total/parcial, rango de las ventas que se llevó, etapa en el banco."""
    retiros = list(retiros)
    if not retiros:
        return []
    ids = [r.id for r in retiros]
    ventas = {f['retiro_id']: f for f in (
        TransaccionMercadoPago.objects.filter(retiro_id__in=ids).values('retiro_id')
        .annotate(n=Count('id'), neto=Sum(_neto()), v_min=Min('creado_en'), v_max=Max('creado_en'),
                  l_min=Min('money_release_date'), l_max=Max('money_release_date')))}
    con_cobro = _con_cobro(retiros)
    hoy = timezone.localdate()
    filas = []
    for r in retiros:
        raw = r.raw_reporte if isinstance(r.raw_reporte, dict) else {}
        v = ventas.get(r.id) or {}
        comp = composicion_retiro(r, v.get('neto') or 0, con_cobro)
        quedan = cuanto_quedo(r)
        instante = conc._instante(raw.get('instante'))
        de_la_tienda = None
        if alias_tienda:
            de_la_tienda = sum(t['monto'] for t in comp['tiendas'] if t['tienda'] == alias_tienda)
        filas.append({
            'withdrawal_id': r.withdrawal_id,
            'fecha': r.fecha.strftime('%d/%m/%Y'), 'fecha_iso': str(r.fecha),
            'hora': timezone.localtime(instante).strftime('%H:%M') if instante else '',
            'monto': r.monto,
            'estado': r.estado,
            'tipo': None if quedan is None else ('PARCIAL' if quedan > 0 else 'TOTAL'),
            'quedan': quedan,
            **comp,
            'de_la_tienda': de_la_tienda,
            'ventas_pos': int(v.get('n') or 0),
            'ventas_manual': len(raw.get('netos') or {}),
            'vendidas_desde': _fecha(v.get('v_min')), 'vendidas_hasta': _fecha(v.get('v_max')),
            'liberadas_desde': _fecha(v.get('l_min')), 'liberadas_hasta': _fecha(v.get('l_max')),
            'pendiente_recalcular': bool(raw.get('recalcular')) and not raw.get('por_caja'),
            'visto_en_cartola': r.visto_en_cartola,
            **conc.etapa_bancaria(r, hoy),
        })
    return filas


def _plata_sin_retiro(configs, desde=None, hasta=None):
    """Neto de las ventas aprobadas que ningún retiro se llevó, separado en lo que
    Mercado Pago todavía no libera y lo que ya está disponible en la cuenta.
    Con `desde`/`hasta`, solo las cobradas en ese rango."""
    ahora = timezone.now()
    base = (TransaccionMercadoPago.objects
            .filter(tipo='VENTA', estado='APROBADA', retiro__isnull=True, config_id__in=list(configs))
            .exclude(correlativo_ticket__startswith='PRUEBA-'))
    if desde:
        base = base.filter(creado_en__date__gte=desde)
    if hasta:
        base = base.filter(creado_en__date__lte=hasta)
    por_liberar = base.filter(money_release_date__gt=ahora).aggregate(
        n=Count('id'), neto=Sum(_neto()), proxima=Min('money_release_date'), ultima=Max('money_release_date'))
    disponible = base.exclude(money_release_date__gt=ahora).aggregate(
        n=Count('id'), neto=Sum(_neto()), desde=Min('creado_en'), hasta=Max('creado_en'),
        sin_fecha=Count('id', filter=Q(money_release_date__isnull=True)))
    # Devoluciones parciales (la venta sigue APROBADA): esa plata ya no está.
    devoluciones = (TransaccionMercadoPago.objects
                    .filter(tipo='DEVOLUCION', estado='DEVUELTA', transaccion_origen__in=base)
                    .values_list('transaccion_origen__money_release_date', 'monto'))
    dev_por_liberar = sum(int(m or 0) for f, m in devoluciones if f and f > ahora)
    dev_disponible = sum(int(m or 0) for f, m in devoluciones if not (f and f > ahora))
    return {
        'por_liberar': max(0, int(por_liberar['neto'] or 0) - dev_por_liberar),
        'por_liberar_n': por_liberar['n'],
        'proxima_liberacion': _fecha(por_liberar['proxima']),
        'ultima_liberacion': _fecha(por_liberar['ultima']),
        'disponible': max(0, int(disponible['neto'] or 0) - dev_disponible),
        'disponible_n': disponible['n'],
        'disponible_desde': _fecha(disponible['desde']),
        'disponible_hasta': _fecha(disponible['hasta']),
        'sin_fecha_liberacion': disponible['sin_fecha'],
    }


def liberaciones_por_empresa(desde, hasta, sucursal_id=None):
    """Pestaña «Liberaciones y banco»: por empresa, dónde está hoy la plata y los
    retiros del período con lo que se llevó cada uno."""
    d, h = conc.rango_fechas(desde, hasta)
    hoy = timezone.localdate()
    alias_tienda = ''
    if sucursal_id:
        alias_tienda = (MercadoPagoConfig.objects.filter(sucursal_id=int(sucursal_id))
                        .values_list('sucursal__alias', flat=True).first() or '')
    salida = []
    for emp in empresas_mp(sucursal_id):
        configs = emp['configs']
        qs = (RetiroMercadoPago.objects.filter(config_id__in=configs)
              .select_related('config').order_by('-fecha', '-id'))
        del_periodo = list(qs.filter(fecha__gte=d, fecha__lte=h))
        vigentes = [r for r in del_periodo if conc.etapa_bancaria(r, hoy)['etapa'] != 'REVERTIDO']
        abonados = [r for r in vigentes if r.visto_en_cartola]
        en_transito = [r for r in vigentes if not r.visto_en_cartola]
        # De antes del período, todavía sin abono confirmado (4 meses hacia atrás).
        arrastre = [r for r in qs.filter(fecha__lt=d, fecha__gte=d - timedelta(days=120), visto_en_cartola=False)
                    if conc.etapa_bancaria(r, hoy)['etapa'] == 'EN_TRANSITO']
        ultimo = qs.first()
        cuadre = conc.cuadre_por_sucursal(str(d), str(h), sucursal_id=sucursal_id, configs=configs)['total']
        salida.append({
            **{k: emp[k] for k in ('clave', 'cuenta_id', 'config_id', 'nombre', 'rut', 'tiendas')},
            'hoy': _plata_sin_retiro(configs),
            'periodo': {
                'cobrado_bruto': cuadre['vendido'], 'cobrado_neto': cuadre['neto'], 'cobros': cuadre['cobros'],
                'comision': cuadre['comision'], 'devuelto': cuadre['devuelto'],
                'retirado': sum(r.monto for r in vigentes), 'retiros_n': len(vigentes),
                'abonado': sum(r.monto for r in abonados), 'abonado_n': len(abonados),
                'en_transito': sum(r.monto for r in en_transito), 'en_transito_n': len(en_transito),
                'alerta_transito': sum(1 for r in en_transito if conc.etapa_bancaria(r, hoy)['alerta_transito']),
                'revertidos': len(del_periodo) - len(vigentes),
            },
            'arrastre_sin_abonar': sum(r.monto for r in arrastre), 'arrastre_sin_abonar_n': len(arrastre),
            'retiros': describir_retiros(del_periodo, alias_tienda),
            'ultimo_retiro': ({'fecha': ultimo.fecha.strftime('%d/%m/%Y'), 'monto': ultimo.monto}
                              if ultimo is not None else None),
        })
    return {'desde': str(d), 'hasta': str(h), 'empresas': salida, 'partes': PARTES}


def asignacion_empresa(mes=None, clave=None, sucursal_id=None):
    """Pestaña «Asignación de retiros» de UNA empresa: de lo cobrado en el mes,
    dónde está la plata; los retiros del mes en orden, con lo que se llevó cada
    uno; lo que todavía no se retira, y cada cobro con su retiro.

    `clave`: la de `empresas_mp` (sin clave, la primera; con tienda, la suya).
    """
    empresas = empresas_mp(sucursal_id)
    if not empresas:
        return {'empresas': [], 'empresa': None}
    emp = next((e for e in empresas if e['clave'] == clave), empresas[0])
    base = conc.asignaciones_mp(mes, configs=emp['configs'], sucursal_id=sucursal_id)
    alias_tienda = ''
    if sucursal_id:
        alias_tienda = (MercadoPagoConfig.objects.filter(sucursal_id=int(sucursal_id))
                        .values_list('sucursal__alias', flat=True).first() or '')
    # Retiros del mes en orden (el más antiguo primero): así se lee cómo la cuenta
    # se va llenando con ventas liberadas y cada retiro se lleva lo acumulado.
    retiros = (RetiroMercadoPago.objects.filter(config_id__in=emp['configs'],
                                                fecha__gte=base['desde'], fecha__lte=base['hasta'])
               .select_related('config').order_by('fecha', 'id'))
    filas = describir_retiros(retiros, alias_tienda)
    filas.sort(key=lambda f: (f['fecha_iso'], f['hora'] or '99:99'))
    primer_cobro = (TransaccionMercadoPago.objects.filter(config_id__in=emp['configs'], tipo='VENTA')
                    .aggregate(m=Min('creado_en'))['m'])
    return {
        'empresas': [{k: e[k] for k in ('clave', 'nombre', 'rut', 'tiendas')} for e in empresas],
        'empresa': {k: emp[k] for k in ('clave', 'cuenta_id', 'config_id', 'nombre', 'rut', 'tiendas')},
        'mes': base['mes'], 'desde': base['desde'], 'hasta': base['hasta'],
        'resumen': base['resumen'], 'serie': base['serie'],
        'retiros': filas,
        # de lo cobrado EN EL MES que ningún retiro se llevó todavía
        'queda': _plata_sin_retiro(emp['configs'], base['desde'], base['hasta']),
        # y de toda la cuenta, hoy (incluye meses anteriores)
        'queda_cuenta': _plata_sin_retiro(emp['configs']),
        'primer_cobro': _fecha(primer_cobro),
        'retirado_mes': base['retirado_mes'], 'sin_abonar': base['sin_abonar'],
        'sin_abonar_n': base['sin_abonar_n'], 'sin_abonar_alerta': base['sin_abonar_alerta'],
        'revertidos': base['revertidos'],
        'arrastre_sin_abonar': base['arrastre_sin_abonar'], 'arrastre_sin_abonar_n': base['arrastre_sin_abonar_n'],
        'cobros': base['cobros'], 'conteo': base['conteo'], 'estados': base['estados'],
        'total_cobros': base['total_cobros'], 'recortado': base['recortado'],
        'partes': PARTES,
    }
