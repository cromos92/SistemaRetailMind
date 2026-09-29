"""
Servicio de Devolución de Dinero por Garantía (function-based, estilo
`giftcard_service.py`).

Flujo en dos pasos:

  1. `crear_solicitud_devolucion` — un usuario con acceso al módulo busca el
     DTE con el problema, resuelve/crea el cliente real como Empresa, elige
     líneas (por cantidad o por monto parcial) y registra la solicitud en
     estado PENDIENTE. NO consume folio de NC ni genera documento.
  2. `aprobar_devolucion` / `rechazar_devolucion` — un administrador la
     analiza. Al aprobar decide el impacto en caja (efectivo/transferencia/no
     afecta) y ahí recién se genera la Nota de Crédito (DTE 61) + TXT Acepta.
     `anular_solicitud` permite al solicitante retirar una pendiente.

Inventario (auditoría de caminos 29-09-2026, hallazgo H4): al aprobar, el
producto que el cliente devuelve vuelve al inventario si está APTO para la
venta (default) — stock plano + lote FIFO + kardex DEVOLUCION_NC ligado a la
NC, vía `ReingresoVenta` (respeta cambios previos y lo que otras NC ya
acreditaron). Si el operador lo marca NO APTO, no entra a ningún stock y
queda un kardex documental DEVOLUCION_NO_APTA (cantidad 0, tipo AJUSTE, la
cantidad en la observación), igual que en Cambios y Devoluciones. El modo
MONTO (sin unidades) no mueve nada y reusa el patrón "corrige montos" de
`anular_factura_dte` (línea conceptual sin talla, razón SII 3).
"""
import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Dte_Detalle_Pago, Empresa,
    DevolucionGarantia, DevolucionGarantiaDetalle,
    Movimientos_Producto, Producto_Talla,
    METODO_DEVOLUCION_DG_CHOICES, TIPO_CUENTA_DG_CHOICES,
    rol_efectivo,
)

logger = logging.getLogger('app')


class DevolucionGarantiaError(Exception):
    """Error de negocio (DTE no encontrado, producto ya devuelto, RUT inválido, etc.)."""


TIPOS_DTE_VENTA_VALIDOS = ['BOLETA ELECTRONICA', 'FACTURA ELECTRONICA', 'BOLETA PAPEL']

# Estados de DevolucionGarantia que "reservan" disponibilidad (aún vivos).
ESTADOS_RESERVA = ['PENDIENTE', 'REGISTRADA']
ESTADOS_CONSUMO = ['PENDIENTE', 'REGISTRADA', 'NC_GENERADA']

# El SII exige giro del receptor en la NC (61). Para persona natural no
# corresponde usar el nombre (el giro es la actividad económica), así que se
# completa con el estándar chileno para boletas/NC a personas.
GIRO_PARTICULAR_DEFAULT = 'PARTICULAR'

# === CONDICIÓN DE PAGO DEL DOCUMENTO ORIGINAL ===
# Métodos de pago que NO son plata recibida: la venta quedó como cuenta por
# cobrar. Mismo conjunto que `METODOS_CREDITO_DTE` de views_modulo_ventas
# (el que decide forma_pago_dte = 2/Crédito al emitir el DTE del ticket).
METODOS_PAGO_CREDITO = {'CREDITO_TRABAJADOR', 'CREDITO_EXTERNO', 'CONVENIO', 'ORDEN_COMPRA'}

# Métodos de pago que entraron por la MÁQUINA Transbank (tarjeta). Mismo
# universo que suma `total_transbank` la cuadratura de views_modulo_ventas.
# Importan porque un ERROR DE COBRO con tarjeta se corrige ANULANDO en la
# misma máquina (el dinero vuelve solo a la tarjeta del cliente): una NC de
# devolución de dinero encima de esa anulación paga dos veces.
METODOS_PAGO_TRANSBANK = {
    'TBK_DEBITO_POS', 'TBK_CREDITO_POS', 'TBK_PREPAGO_POS',
    'TBK_POS_INTEGRADO', 'TBK_MANUAL', 'TARJETA_DEBITO', 'TARJETA_CREDITO',
}

# Métodos de devolución que se imputan a un día de cuadratura (llevan
# Dte_Detalle_Pago con fecha_pago). NO_AFECTA_CAJA queda fuera a propósito.
METODOS_DG_CON_IMPUTACION = ('EFECTIVO_CAJA', 'TRANSFERENCIA_BANCARIA', 'REBAJA_CREDITO', 'MERCADO_PAGO')

# Método de pago con el que se graba el Dte_Detalle_Pago de la NC. Es lo que
# lee `_calcular_cuadratura_data` para saber de qué teórico descontar.
# MERCADO_PAGO no está: copia el método MP_* del cobro original (ver
# `_pago_nc_mercadopago`), porque de él depende el sub-bucket débito/crédito.
METODO_PAGO_NC_POR_DG = {
    'EFECTIVO_CAJA': 'EFECTIVO',
    'TRANSFERENCIA_BANCARIA': 'TRANSFERENCIA',
    'REBAJA_CREDITO': 'CREDITO_EXTERNO',
}

# `Ticket.tipo_dte` del ticket que originó cada tipo de documento. El POS deja
# en 'TICKET' el ticket de una boleta electrónica (visto en prod 28-09-2026:
# ticket #12180 de PAO1 → BOL.E 413479), así que buscar solo por
# BOLETA_ELECTRONICA no encontraba nunca la venta.
TIPOS_TICKET_BOLETA = ['BOLETA_ELECTRONICA', 'BOLETA', 'TICKET']
TIPOS_TICKET_FACTURA = ['FACTURA_ELECTRONICA', 'FACTURA_EXENTA']

# === CONDICIÓN DEL PRODUCTO DEVUELTO (apto / no apto para la venta) ===
# La solicitud recuerda qué líneas vienen NO APTAS con una marca al final de
# `motivo`: `[NO_APTO:todas]` o `[NO_APTO:12,15]` (ids de Dte_Productos).
# DevolucionGarantiaDetalle no tiene columna para esto y agregarla exige
# migración (pendiente: `condicion_producto`); mientras tanto la marca vive
# acá, `motivo_limpio` la quita de todo lo que se muestra o va a la NC, y el
# aprobador puede corregirla al aprobar (checkbox por línea).
MARCA_NO_APTO_RE = re.compile(r'\s*\[NO_APTO:([^\]]*)\]\s*$')
MARCA_NO_APTO_TODAS = 'todas'


def motivo_limpio(motivo):
    """Motivo sin la marca interna de líneas no aptas (para UI, comprobante y NC)."""
    return MARCA_NO_APTO_RE.sub('', motivo or '').strip()


def _marcar_no_apto(motivo, ids_no_aptos=(), todas=False):
    """Agrega (o reemplaza) la marca de líneas no aptas al final del motivo."""
    base = motivo_limpio(motivo)
    if todas:
        return f'{base} [NO_APTO:{MARCA_NO_APTO_TODAS}]'.strip()
    ids = sorted({int(i) for i in ids_no_aptos if str(i).strip().isdigit()})
    if not ids:
        return base
    return f"{base} [NO_APTO:{','.join(str(i) for i in ids)}]".strip()


def lineas_no_aptas_de(devolucion):
    """(todas, {dte_producto_id}) marcadas como NO APTAS en la solicitud."""
    m = MARCA_NO_APTO_RE.search(devolucion.motivo or '')
    if not m:
        return False, set()
    valor = (m.group(1) or '').strip().lower()
    if valor == MARCA_NO_APTO_TODAS:
        return True, set()
    return False, {int(v) for v in valor.split(',') if v.strip().isdigit()}


def _ids_no_aptos_de_detalles(detalles, lineas_no_aptas=None):
    """Une el `no_apto` por línea del wizard con la lista explícita del body."""
    ids = set()
    for item in detalles or []:
        if isinstance(item, dict) and item.get('no_apto') in (True, 1, '1', 'true'):
            try:
                ids.add(int(item.get('dte_producto_id')))
            except (TypeError, ValueError):
                pass
    for i in lineas_no_aptas or []:
        try:
            ids.add(int(i))
        except (TypeError, ValueError):
            pass
    return ids


def _tipos_ticket_de(dte):
    return TIPOS_TICKET_FACTURA if 'FACTURA' in (dte.tipo_documento or '').upper() else TIPOS_TICKET_BOLETA


def condicion_pago_dte(dte):
    """
    ¿El documento original se vendió al CONTADO o A CRÉDITO?

    Importa porque el método de devolución debe seguir por dónde entró la
    plata: si la venta fue a crédito, el cliente nunca puso dinero en la caja
    y devolvérselo en efectivo deja el arqueo de ese día con un faltante que
    nunca existió (`total_efectivo` teórico baja sin que haya salido plata).

    Se considera a crédito si se cumple cualquiera de estas (en orden de
    confianza):
      - `estado_pago` PENDIENTE/VENCIDO  → la cuenta por cobrar sigue abierta;
      - `diasCredito > 0`                → se emitió con pago diferido;
      - hay pagos y TODOS son de crédito (CREDITO_EXTERNO, CONVENIO, ...);
      - no hay ni un `Dte_Detalle_Pago`  → no entró a ningún medio de caja.

    Devuelve un dict serializable (lo consumen la API y el preview de caja).
    """
    pagos = list(dte.dte_asociado.all())
    metodos = {(p.metodo_pago or '').upper() for p in pagos if (p.metodo_pago or '').strip()}
    monto_credito = sum(int(p.monto or 0) for p in pagos
                        if (p.metodo_pago or '').upper() in METODOS_PAGO_CREDITO)
    monto_contado = sum(int(p.monto or 0) for p in pagos
                        if (p.metodo_pago or '').upper() not in METODOS_PAGO_CREDITO)

    estado_pago = (dte.estado_pago or '').upper()
    dias_credito = int(dte.diasCredito or 0)
    # Cobro abierto: es el caso duro — el cliente aún debe la plata.
    cobro_abierto = estado_pago in ('PENDIENTE', 'VENCIDO')

    motivos = []
    if cobro_abierto:
        motivos.append(f'el documento está {estado_pago.lower()} de pago (cuenta por cobrar abierta)')
    if dias_credito > 0:
        motivos.append(f'se emitió a {dias_credito} días de crédito')
    if not pagos:
        motivos.append('no tiene ningún pago registrado (no entró a ningún medio de caja)')
    elif metodos and metodos <= METODOS_PAGO_CREDITO:
        motivos.append('todos sus pagos son de crédito (' + ', '.join(sorted(metodos)) + ')')

    return {
        'es_credito': bool(motivos),
        'cobro_abierto': cobro_abierto,
        'estado_pago': estado_pago,
        'dias_credito': dias_credito,
        'metodos_pago': sorted(metodos),
        'monto_credito': monto_credito,
        'monto_contado': monto_contado,
        'motivos': motivos,
    }


def metodo_devolucion_sugerido(dte, mp=None):
    """Método que la UI debe preseleccionar según cómo se vendió el documento:
    a crédito → rebaja de crédito; cobrado con Mercado Pago (y con saldo por
    devolver) → Mercado Pago, la plata vuelve por donde entró; si no,
    transferencia."""
    if condicion_pago_dte(dte)['es_credito']:
        return 'REBAJA_CREDITO'
    mp = mp if mp is not None else pago_mercadopago_dte(dte)
    if mp['es_mp'] and mp['disponible'] > 0:
        return 'MERCADO_PAGO'
    return 'TRANSFERENCIA_BANCARIA'


def pago_transbank_dte(dte):
    """
    ¿El documento se cobró (total o parcialmente) con tarjeta por la máquina
    Transbank, y esa venta registra una ANULACIÓN hecha en la máquina?

    Un error de cobro con tarjeta NO se corrige por este módulo: se anula en
    la misma máquina Transbank y el dinero vuelve a la tarjeta. Si además se
    aprueba aquí una devolución de dinero, el cliente cobra dos veces. La UI
    advierte siempre que hubo tarjeta, y se bloquea cuando la anulación por
    máquina ya está registrada (`TransaccionPOS` tipo ANULACION APROBADA,
    vía anular_transaccion_pos) y cubre todo lo pagado con tarjeta.

    El vínculo Dte→Ticket es por folio (`Ticket.folio_dte` no es FK ni único):
    se acota por sucursal + tipo de documento, el mismo criterio que usa el
    resto del sistema (ej. referencia ANULACION_DTE_{folio}).
    """
    from app.models import Ticket, TransaccionPOS

    monto_tarjeta = 0
    metodos = set()
    for p in dte.dte_asociado.all():
        m = (p.metodo_pago or '').upper()
        if m in METODOS_PAGO_TRANSBANK:
            metodos.add(m)
            monto_tarjeta += int(p.monto or 0)

    resultado = {
        'es_transbank': bool(metodos),
        'metodos_tarjeta': sorted(metodos),
        'monto_tarjeta': monto_tarjeta,
        'monto_anulado_pos': 0,
        'anulaciones_pos': [],
        'anulado_completo': False,
    }
    if not metodos:
        return resultado

    ticket_ids = list(Ticket.objects.filter(
        sucursal_id=dte.sucursal_id,
        folio_dte=dte.numero_documento,
        tipo_dte__in=_tipos_ticket_de(dte),
    ).values_list('id', flat=True))
    if not ticket_ids:
        return resultado

    monto_anulado = 0
    detalle = []
    anulaciones = TransaccionPOS.objects.select_related('usuario_operador').filter(
        ticket_id__in=ticket_ids, tipo_transaccion='ANULACION', estado='APROBADA',
    ).order_by('fecha_inicio')
    for a in anulaciones:
        monto_anulado += int(a.monto or 0)
        detalle.append({
            'fecha': timezone.localtime(a.fecha_inicio).strftime('%d/%m/%Y %H:%M'),
            'monto': int(a.monto or 0),
            'operador': a.usuario_operador.username if a.usuario_operador_id else '',
        })
    resultado['monto_anulado_pos'] = monto_anulado
    resultado['anulaciones_pos'] = detalle
    resultado['anulado_completo'] = 0 < monto_tarjeta <= monto_anulado
    return resultado


def _validar_cobro_transbank_no_anulado(dte, tbk=None):
    """
    Bloquea la doble devolución: si el cobro con tarjeta de este documento ya
    fue ANULADO por la máquina Transbank, el dinero vuelve a la tarjeta por
    esa vía y no corresponde además una devolución de dinero por aquí. Se
    valida en el service (no solo en la UI) porque los endpoints reciben el
    folio/método por JSON.
    """
    tbk = tbk or pago_transbank_dte(dte)
    if tbk['anulado_completo']:
        ultima = tbk['anulaciones_pos'][-1]
        raise DevolucionGarantiaError(
            f'El cobro con tarjeta del documento #{dte.numero_documento} '
            f'(${tbk["monto_tarjeta"]:,}) ya fue ANULADO por la máquina Transbank '
            f'(${tbk["monto_anulado_pos"]:,} el {ultima["fecha"]}). El dinero vuelve '
            f'a la tarjeta del cliente por esa vía: no corresponde una devolución '
            f'de dinero adicional (sería pagarle dos veces).'
        )


def _validar_metodo_vs_condicion_pago(dte, metodo_devolucion):
    """
    Bloquea sacar plata de la caja por una venta que nunca la puso ahí.

    Solo es un error duro cuando la cuenta por cobrar sigue ABIERTA
    (`estado_pago` PENDIENTE/VENCIDO): ahí no hay ninguna lectura en que
    corresponda entregar efectivo del día. Para el resto de los casos a
    crédito el preview de caja advierte, pero no bloquea (una factura a
    crédito ya cobrada sí puede devolverse por transferencia).
    """
    if metodo_devolucion != 'EFECTIVO_CAJA':
        return
    cond = condicion_pago_dte(dte)
    if cond['cobro_abierto']:
        raise DevolucionGarantiaError(
            f'El documento #{dte.numero_documento} se vendió A CRÉDITO y su pago sigue '
            f'{cond["estado_pago"].lower()}: no corresponde devolver efectivo de caja '
            f'(dejaría el arqueo del día con un faltante que nunca existió). '
            f'Use "Rebaja crédito del cliente" para descontar la cuenta por cobrar.'
        )


def validar_transbank_directa(dte):
    """
    La devolución DIRECTA no procede si el cobro con tarjeta tiene una
    anulación PARCIAL por la máquina Transbank (la completa ya la bloquea
    `_validar_cobro_transbank_no_anulado`). Esa anulación no rebaja el saldo de
    NC del documento, así que devolver el total pagaría dos veces la parte ya
    reversada a la tarjeta. En el flujo con aprobación el aprobador ve el monto
    anulado en el preview; en la directa nadie lo revisa.
    """
    tbk = pago_transbank_dte(dte)
    if tbk['monto_anulado_pos'] and not tbk['anulado_completo']:
        raise DevolucionGarantiaError(
            f'Esta venta ya tiene una anulación PARCIAL por la máquina Transbank '
            f'(${int(tbk["monto_anulado_pos"]):,} de ${int(tbk["monto_tarjeta"]):,} cobrados '
            f'con tarjeta). Envíe la devolución a aprobación para que el administrador '
            f'descuente lo ya reversado.'
        )


# === COBRO CON MERCADO PAGO (Point / QR) ===
# Una venta cobrada con Mercado Pago se devuelve desde Mercado Pago (app,
# panel o la misma Point): la plata vuelve a la tarjeta/cuenta del cliente y
# nunca sale de la caja. Registrarla como transferencia o efectivo descuadra
# dos medios a la vez (caso PAO1 28-09-2026: NC 5155 como transferencia).

# Etiqueta del medio cuando el pago no trae `tipo_tarjeta` (payment_type_id).
_MEDIO_POR_METODO_MP = {'MP_POINT_DEBITO': 'DEBITO', 'MP_POINT_CREDITO': 'CREDITO'}


def _es_metodo_mp(metodo):
    """Mismo criterio que la cuadratura: todo método MP_* es Mercado Pago presencial."""
    return (metodo or '').upper().startswith('MP_')


def _medio_mp(metodo, tipo_tarjeta):
    from app.services.mercadopago_service import etiqueta_medio_mp
    if (tipo_tarjeta or '').strip():
        return etiqueta_medio_mp(tipo_tarjeta)
    return _MEDIO_POR_METODO_MP.get((metodo or '').upper(), 'MERCADO PAGO')


def _transacciones_mp_venta(dte, pagos_mp):
    """Cobros MP (`TransaccionMercadoPago` VENTA) de la venta original.

    Primero por el voucher del pago del documento, que es el id del cobro (el
    ULID de la Orders API, o el N° de MP si ya se completó): calza exacto
    aunque no se encuentre el ticket. Además, por el ticket de ese folio en la
    sucursal.
    """
    from app.models import Ticket, TransaccionMercadoPago

    vouchers = {str(p.voucher).strip() for p in pagos_mp if (p.voucher or '').strip()}
    filtro = Q()
    if vouchers:
        filtro |= Q(payment_id__in=vouchers) | Q(payment_id_mp__in=vouchers)
    ticket_ids = list(Ticket.objects.filter(
        sucursal_id=dte.sucursal_id, folio_dte=dte.numero_documento,
        tipo_dte__in=_tipos_ticket_de(dte),
    ).values_list('id', flat=True))
    if ticket_ids:
        filtro |= Q(ticket_id__in=ticket_ids)
    if not filtro:
        return []
    return list(
        TransaccionMercadoPago.objects.filter(filtro)
        .filter(sucursal_id=dte.sucursal_id, tipo='VENTA', estado__in=('APROBADA', 'DEVUELTA'))
        .select_related('config').distinct().order_by('-monto', 'id')
    )


def _monto_nc_mp_previas(dte):
    """Lo ya devuelto por Mercado Pago con NC de devolución sobre este documento."""
    return int(Dte_Detalle_Pago.objects.filter(
        dte__documento_afectado=dte,
        dte__tipo_documento='NOTA DE CREDITO',
        dte__tipo_transaccion='DEVOLUCION',
        dte__estado_dte__in=['EMITIDO', 'ACEPTADO'],
        dte__descartado=False,
        metodo_pago__startswith='MP_',
    ).aggregate(total=Sum('monto'))['total'] or 0)


def pago_mercadopago_dte(dte, consultar_api=False):
    """
    ¿El documento se cobró (total o parcialmente) con Mercado Pago presencial,
    y cuánto de eso se puede todavía devolver por Mercado Pago?

    Trae además el N° de operación de cada cobro — el que muestran la app y el
    panel de Mercado Pago y el voucher de la Point — para que quien devuelve
    encuentre el cobro y el número quede anotado en la NC. El sistema guarda
    el id de la Orders API (`PAY01…`), que MP no reconoce en su panel; el
    número real vive en `TransaccionMercadoPago.payment_id_mp`, que casi nunca
    quedó lleno. Con `consultar_api` se completa preguntándole a MP (una
    consulta por cobro, solo si falta).
    """
    from app.services.mercadopago_service import completar_numero_operacion

    resultado = {
        'es_mp': False, 'monto_mp': 0, 'metodo_pago': '', 'tipo_tarjeta': '', 'medio': '',
        'operaciones': [], 'numero_operacion': '', 'monto_devuelto': 0, 'disponible': 0,
        # Lo que se puede resolver a la tarjeta por la API (refund): saldo de
        # los cobros con pago real en MP + refunds ya hechos que quedaron sin NC.
        'devolvible_api': 0, 'refund_pendiente_nc': 0,
    }
    pagos_mp = [p for p in dte.dte_asociado.all() if _es_metodo_mp(p.metodo_pago)]
    if not pagos_mp:
        return resultado

    principal = max(pagos_mp, key=lambda p: int(p.monto or 0))
    monto_mp = sum(int(p.monto or 0) for p in pagos_mp)
    devuelto = _monto_nc_mp_previas(dte)

    operaciones = []
    devolvible_api = 0
    trxs = _transacciones_mp_venta(dte, pagos_mp)
    # Refunds por API ya hechos sobre estos cobros cuya NC no alcanzó a
    # emitirse: cuentan como "resuelto por API" (el siguiente intento los
    # reutiliza sin volver a pedirlos).
    refund_pendiente_nc = sum(int(r.monto or 0) for r in _refunds_pendientes_de_nc(
        [t for t in trxs if _trx_devolvible_api(t)]))
    for trx in trxs:
        if _trx_devolvible_api(trx):
            devolvible_api += _saldo_api_trx(trx)
        numero = trx.payment_id_mp or (completar_numero_operacion(trx) if consultar_api else '')
        operaciones.append({
            'numero': numero or '',
            'monto': int(trx.monto or 0),
            'medio': _medio_mp('', trx.metodo_pago_mp),
            'ultimos_4': trx.ultimos_4_digitos or '',
            'fecha': timezone.localtime(trx.creado_en).strftime('%d/%m/%Y %H:%M') if trx.creado_en else '',
            'estado': trx.estado,
        })
    if not operaciones:
        # Cobro «MP manual» (sin transacción): el cajero digitó el N° en el
        # voucher. Un voucher `PAY01…` es el id de Orders y no sirve para buscar.
        for p in pagos_mp:
            voucher = (p.voucher or '').strip()
            if voucher and not voucher.upper().startswith('PAY'):
                operaciones.append({
                    'numero': voucher, 'monto': int(p.monto or 0),
                    'medio': _medio_mp(p.metodo_pago, p.tipo_tarjeta),
                    'ultimos_4': '', 'fecha': '', 'estado': '',
                })

    resultado.update({
        'es_mp': True,
        'monto_mp': monto_mp,
        'metodo_pago': (principal.metodo_pago or '').upper(),
        'tipo_tarjeta': principal.tipo_tarjeta or '',
        'medio': _medio_mp(principal.metodo_pago, principal.tipo_tarjeta),
        'operaciones': operaciones,
        'numero_operacion': next((o['numero'] for o in operaciones if o['numero']), ''),
        'monto_devuelto': devuelto,
        'disponible': max(monto_mp - devuelto, 0),
        'devolvible_api': min(devolvible_api + refund_pendiente_nc, max(monto_mp - devuelto, 0)),
        'refund_pendiente_nc': refund_pendiente_nc,
    })
    return resultado


def _validar_devolucion_mercadopago(dte, monto, mp=None):
    """La devolución por Mercado Pago existe solo si la venta se cobró por ahí,
    y no puede superar lo cobrado con MP menos lo ya devuelto con NC por esa
    vía: el resto del documento se pagó con otro medio."""
    mp = mp if mp is not None else pago_mercadopago_dte(dte)
    if not mp['es_mp']:
        raise DevolucionGarantiaError(
            f'El documento #{dte.numero_documento} no se cobró con Mercado Pago: no hay '
            f'cobro que devolver por esa vía. Use efectivo o transferencia.'
        )
    if int(monto) > mp['disponible']:
        ya = (f' y ya se devolvieron ${mp["monto_devuelto"]:,} por esa vía'
              if mp['monto_devuelto'] else '')
        raise DevolucionGarantiaError(
            f'Con Mercado Pago se cobraron ${mp["monto_mp"]:,} de este documento{ya}: '
            f'por Mercado Pago se pueden devolver hasta ${mp["disponible"]:,} y la '
            f'devolución es de ${int(monto):,}. El resto va por el medio con que se pagó.'
        )
    return mp


def _numero_operacion_mp(numero, mp):
    """N° de operación MP de la devolución: el que se indicó o el del cobro."""
    numero = ''.join(str(numero or '').split())[:50] or mp['numero_operacion']
    if not numero:
        raise DevolucionGarantiaError(
            'Indique el N° de operación de Mercado Pago (aparece en la app o el panel de '
            'Mercado Pago y en el voucher de la Point): queda en la NC para ubicar la devolución.'
        )
    return numero


def _datos_pago_nc_mercadopago(mp, numero_operacion, devolucion):
    """Campos del Dte_Detalle_Pago de una NC devuelta por Mercado Pago.

    Copia el MÉTODO y el medio (`tipo_tarjeta`) del cobro original: la
    cuadratura resta la NC del bucket MP POS y, dentro, del sub-bucket
    débito/crédito/otros con el mismo criterio con que sumó el cobro. El N° de
    operación va al voucher: es lo que muestra y busca Consulta de Documentos.
    """
    return {
        'metodo_pago': mp['metodo_pago'] or 'MP_POINT',
        'tipo_tarjeta': mp['tipo_tarjeta'] or None,
        'voucher': numero_operacion[:50],
        'notas': f'Devuelto por Mercado Pago (app/panel/Point) - {devolucion.numero_operacion}',
    }


def _registrar_devolucion_en_libro_mp(*, dte, nc, monto, devolucion, usuario):
    """Anota la devolución en el libro de cobros MP (`TransaccionMercadoPago`),
    repartida sobre los cobros de la venta con saldo, el mayor primero. Si la
    venta no tiene cobro registrado (MP manual antiguo) no hay nada que anotar.
    """
    from app.services import mercadopago_service as mp_service

    pagos_mp = [p for p in dte.dte_asociado.all() if _es_metodo_mp(p.metodo_pago)]
    restante = int(monto)
    for trx in _transacciones_mp_venta(dte, pagos_mp):
        if restante <= 0:
            break
        disponible = mp_service._disponible_trx(trx)
        if disponible <= 0:
            continue
        tomar = min(restante, disponible)
        mp_service.registrar_devolucion_externa(
            trx, tomar,
            referencia=f'{trx.external_reference}-NC{nc.id}',
            detalle=f'Devuelto en MP - NC {nc.numero_documento} ({devolucion.numero_operacion})',
            usuario=usuario,
        )
        restante -= tomar


# ---------- Devolución a la tarjeta por la API de Mercado Pago (refund) ----------
# La alternativa a «ya devuelto desde la app/panel/Point»: RetailMind le pide
# a MP el refund y la plata vuelve sola a la tarjeta/cuenta del cliente. Mueve
# plata real de la cuenta MP de la empresa, por eso la vista exige el permiso
# `devolver_mercadopago` (por política, solo el Maestro).

def _trx_devolvible_api(trx):
    """¿Este cobro del libro MP tiene un pago real en Mercado Pago que la API
    pueda reembolsar? Un «MP manual» no; una asignación (ASOC-) solo si ya
    conoce el N° de pago."""
    ref = str(trx.external_reference or '')
    if ref.startswith('MANUAL-'):
        return False
    if ref.startswith('ASOC-'):
        return bool(trx.payment_id_mp or (trx.payment_id or '').isdigit())
    return True


def _saldo_api_trx(trx):
    from app.services import mercadopago_service as mp_service
    return mp_service._disponible_trx(trx)


def _validar_devolvible_api(mp, monto):
    if int(monto) <= mp['devolvible_api']:
        return
    if not mp['devolvible_api']:
        raise DevolucionGarantiaError(
            'Este cobro no se puede devolver por la API de Mercado Pago (se registró como '
            '«MP manual» o no tiene pago en Mercado Pago). Devuélvalo desde la app o el '
            'panel de Mercado Pago y registre el N° de operación.'
        )
    raise DevolucionGarantiaError(
        f'Por la API de Mercado Pago se pueden devolver hasta ${mp["devolvible_api"]:,} de '
        f'este documento y la devolución es de ${int(monto):,}. Devuelva el resto desde la '
        f'app o el panel de Mercado Pago y regístrelo con su N° de operación.'
    )


# Marca (estado_detalle) de un refund por API que MP ya hizo pero cuya NC no
# alcanzó a emitirse (la transacción local se revirtió después del refund).
# El siguiente intento lo reutiliza en vez de pedir otro refund.
MARCA_REFUND_PENDIENTE_NC = 'Refund vía API - pendiente NC'


def _ref_refund(trx, devolucion):
    """external_reference / X-Idempotency-Key del refund: FIJA por (cobro,
    devolución). Un reintento (timeout, rollback local) recibe de MP el mismo
    refund y no crea otro."""
    sufijo = f'-REF-{devolucion.numero_operacion}'
    return f'{str(trx.external_reference or "")[:80 - len(sufijo)]}{sufijo}'


def _refunds_pendientes_de_nc(trxs):
    """Refunds por API ya hechos sobre estos cobros que quedaron sin NC."""
    from app.models import TransaccionMercadoPago
    if not trxs:
        return []
    return list(TransaccionMercadoPago.objects.filter(
        tipo='DEVOLUCION', transaccion_origen__in=trxs,
        estado_detalle__startswith=MARCA_REFUND_PENDIENTE_NC,
    ).select_related('transaccion_origen').order_by('id'))


def _devolver_por_api_mp(dte, monto, devolucion, usuario, ctx=None):
    """Pide a Mercado Pago el refund de `monto` sobre los cobros de ESTA venta
    con saldo (el mayor primero) y devuelve las filas DEVOLUCION del libro MP
    (las de este intento y las «pendiente NC» de intentos anteriores).

    Se llama ANTES de emitir la NC (refund-primero, igual que
    `anular_factura_dte`): si MP rechaza, la NC no se emite ni se quema folio.

    Contra la doble devolución si algo falla DESPUÉS del refund (correlativo,
    BD) y la transacción local se revierte:
      - la clave de idempotencia es fija por (cobro, devolución): un reintento
        recibe de MP el mismo refund;
      - cada refund hecho se anota en `ctx['refunds']` para que la vista, ya
        fuera del atomic, lo vuelva a registrar (`persistir_refunds_sin_nc`);
      - los refunds «pendiente NC» que ya existan sobre estos cobros se
        descuentan de lo que falta por devolver.
    """
    from app.services import mercadopago_service as mp_service

    pagos_mp = [p for p in dte.dte_asociado.all() if _es_metodo_mp(p.metodo_pago)]
    trxs = [t for t in _transacciones_mp_venta(dte, pagos_mp) if _trx_devolvible_api(t)]
    devoluciones = _refunds_pendientes_de_nc(trxs)
    hechos = sum(int(r.monto or 0) for r in devoluciones)
    if hechos:
        logger.warning(
            "MP: devolución %s reutiliza %s refund/s por API sin NC ($%s) sobre DTE %s",
            devolucion.numero_operacion, len(devoluciones), hechos, dte.numero_documento,
        )
    restante = int(monto) - hechos
    ya = {r.id for r in devoluciones}
    for trx in trxs:
        if restante <= 0:
            break
        saldo = _saldo_api_trx(trx)
        if saldo <= 0:
            continue
        tomar = min(restante, saldo)
        try:
            fila = mp_service.reembolsar(
                trx, monto=tomar, usuario=usuario, referencia=_ref_refund(trx, devolucion))
        except mp_service.MercadoPagoError as e:
            detalle = getattr(e, 'mensaje', str(e))
            if getattr(e, 'red', False):
                # Transporte: no se sabe si MP recibió el refund. Con la clave
                # fija, reintentar es seguro (MP devuelve el mismo refund).
                raise DevolucionGarantiaError(
                    f'Mercado Pago no respondió: no se sabe si alcanzó a devolver ({detalle}). '
                    f'Reintente en unos segundos: si ya devolvió, Mercado Pago reconocerá la '
                    f'misma solicitud y no devolverá dos veces. La NC NO fue emitida.'
                ) from e
            raise DevolucionGarantiaError(
                f'Mercado Pago no pudo devolver a la tarjeta: {detalle} La NC NO fue emitida.'
            ) from e
        if fila.id in ya:
            # Misma clave ya registrada (no se movió plata nueva).
            continue
        ya.add(fila.id)
        devoluciones.append(fila)
        if ctx is not None:
            ctx.setdefault('refunds', []).append(fila)
        restante -= tomar
    if restante > 0:
        raise DevolucionGarantiaError(
            f'Mercado Pago devolvió ${int(monto) - restante:,} y faltaron ${restante:,}: '
            f'no había saldo devolvible por API. La NC NO fue emitida.'
        )
    logger.warning(
        "MP: devolución de dinero por API de $%s sobre DTE %s (%s refund/s) autorizada por %s",
        monto, dte.numero_documento, len(devoluciones), getattr(usuario, 'username', 'sistema'),
    )
    return devoluciones


def persistir_refunds_sin_nc(ctx, causa='', numero_operacion=''):
    """Vuelve a registrar en el libro MP los refunds que MP YA hizo en un
    intento cuya transacción local se revirtió (la NC falló después del
    refund). Llamar FUERA del atomic. Devuelve los ids de refund.

    Sin esto el libro MP quedaba sin la fila, `devolvible_api` volvía a mostrar
    el total y el reintento pedía otro refund (la clave fija lo frena en MP,
    pero el libro quedaba ciego). Las filas quedan como «pendiente NC»: el
    siguiente intento las reutiliza.
    """
    from app.models import TransaccionMercadoPago
    from app.services import mercadopago_service as mp_service

    ids = []
    for fila in (ctx or {}).get('refunds') or []:
        origen = fila.transaccion_origen
        if origen is None:
            continue
        origen.refresh_from_db()
        marca = f'{MARCA_REFUND_PENDIENTE_NC} ({numero_operacion or "s/n"}): {causa}'[:120]
        row = TransaccionMercadoPago.objects.filter(external_reference=fila.external_reference).first()
        if row is None:
            row = TransaccionMercadoPago.objects.create(
                config=fila.config, sucursal_id=fila.sucursal_id, ticket=fila.ticket,
                correlativo_ticket=fila.correlativo_ticket, tipo='DEVOLUCION', canal=fila.canal,
                transaccion_origen=origen, external_reference=fila.external_reference,
                order_id=fila.order_id, payment_id=fila.payment_id, monto=fila.monto,
                estado='DEVUELTA', estado_detalle=marca, metodo_pago_mp=origen.metodo_pago_mp,
                raw_response=fila.raw_response, consumida=True, usuario=fila.usuario,
            )
        ids.append(str(row.payment_id or row.external_reference))
        total_devuelto = sum(TransaccionMercadoPago.objects.filter(
            transaccion_origen=origen, tipo='DEVOLUCION').values_list('monto', flat=True))
        if origen.estado == 'APROBADA' and total_devuelto >= origen.monto:
            mp_service._aplicar_estado(origen, 'DEVUELTA', detalle='Devolución total')
    if ids:
        logger.critical(
            "MP: refund por API HECHO pero la NC no se emitió (%s). Refunds %s quedaron "
            "registrados como pendiente NC; reintentar la aprobación. Causa: %s",
            numero_operacion or 's/n', ', '.join(ids), causa,
        )
    return ids


def _numero_cobro_tras_refund(mp, refunds):
    """N° de operación que queda en la NC tras un refund por API: el del cobro
    (lo que busca Consulta de Documentos); `reembolsar` lo completó si faltaba.
    Como último recurso, el id del refund."""
    if mp['numero_operacion']:
        return mp['numero_operacion']
    for r in refunds:
        origen = r.transaccion_origen
        if origen is not None and origen.payment_id_mp:
            return origen.payment_id_mp
    return next((str(r.payment_id) for r in refunds if r.payment_id), '')


def _marca_refund_api(nc, devolucion):
    # `numero_operacion` es único: es la llave para reconocer después qué
    # filas del libro MP son los refunds de ESTA devolución.
    return f'Refund vía API - NC {nc.numero_documento} ({devolucion.numero_operacion})'[:120]


def _marcar_refunds_api(refunds, nc, devolucion):
    marca = _marca_refund_api(nc, devolucion)
    for r in refunds:
        r.estado_detalle = marca
        r.save(update_fields=['estado_detalle', 'actualizado_en'])


def _nota_pago_refund_api(refunds, devolucion):
    ids = ', '.join(str(r.payment_id) for r in refunds if r.payment_id) or 's/n'
    return (f'Devuelto a la tarjeta por API de Mercado Pago (refund {ids}) - '
            f'{devolucion.numero_operacion}')


def refunds_api_de_devolucion(devolucion):
    """Filas DEVOLUCION del libro MP hechas por API para esta devolución."""
    from app.models import TransaccionMercadoPago
    return TransaccionMercadoPago.objects.filter(
        tipo='DEVOLUCION',
        external_reference__contains='-REF-',
        estado_detalle__endswith=f'({devolucion.numero_operacion})',
    ).select_related('transaccion_origen').order_by('id')


def mercadopago_de_devolucion(devolucion):
    """Datos de Mercado Pago que muestran el comprobante y el detalle.

    Aprobada por MP: el N° y el medio del pago de la NC (lo que quedó
    registrado) y, si la plata volvió por la API, los ids del refund.
    Pendiente pedida por MP: el N° del cobro original, para que quien la
    resuelva lo ubique. En otro caso, None.
    """
    if devolucion.metodo_devolucion == 'MERCADO_PAGO' and devolucion.nota_credito_id:
        pago = next((p for p in devolucion.nota_credito.dte_asociado.all()
                     if _es_metodo_mp(p.metodo_pago)), None)
        if pago is not None:
            refunds = [str(r.payment_id) for r in refunds_api_de_devolucion(devolucion) if r.payment_id]
            return {'numero_operacion': pago.voucher or '',
                    'medio': _medio_mp(pago.metodo_pago, pago.tipo_tarjeta),
                    'devuelto': True, 'api': bool(refunds), 'refunds': refunds}
    if devolucion.estado == 'PENDIENTE' and devolucion.metodo_solicitado == 'MERCADO_PAGO':
        mp = pago_mercadopago_dte(devolucion.dte_original)
        return {'numero_operacion': mp['numero_operacion'], 'medio': mp['medio'],
                'devuelto': False, 'api': False, 'refunds': []}
    return None


def _revertir_devolucion_en_libro_mp(nc):
    """Deshace lo que `_registrar_devolucion_en_libro_mp` anotó para esta NC
    (solo sus propias filas) y devuelve la venta a APROBADA si ya no queda
    devuelta entera."""
    from app.models import TransaccionMercadoPago

    filas = list(TransaccionMercadoPago.objects.filter(
        tipo='DEVOLUCION', external_reference__endswith=f'-NC{nc.id}',
    ).select_related('transaccion_origen'))
    for fila in filas:
        venta = fila.transaccion_origen
        fila.delete()
        if venta is None or venta.estado != 'DEVUELTA':
            continue
        resto = sum(TransaccionMercadoPago.objects.filter(
            transaccion_origen=venta, tipo='DEVOLUCION').values_list('monto', flat=True))
        if resto < venta.monto:
            TransaccionMercadoPago.objects.filter(pk=venta.pk).update(
                estado='APROBADA', actualizado_en=timezone.now(),
                estado_detalle=f'Devolución de la NC {nc.numero_documento} revertida'[:120],
            )


def validar_efectivo_directo(dte, sucursal, fecha=None):
    """
    Guardas extra del EFECTIVO en la devolución DIRECTA (firmada en el momento
    con el código de un Administrador/Maestro).

    En el flujo con aprobación el aprobador ve el preview de caja y elige la
    fecha; en la directa no hay preview ni fecha (es hoy) y el cajero entrega
    la plata al instante. Por eso acá se bloquea lo que allá solo se advierte:

    - Venta a crédito (cualquier motivo de `condicion_pago_dte`) o con ALGUNA
      parte pagada con un medio de crédito (boleta mixta efectivo + crédito
      trabajador, que `es_credito` no marca): el cliente no puso esa plata en
      la caja y la cuenta por cobrar seguiría viva.
    - Arqueo de hoy ya cerrado: el efectivo sale de un cajón ya contado y
      el arqueo guardado quedaría descuadrado.
    """
    from app.models import ArqueoCaja

    cond = condicion_pago_dte(dte)
    if cond['es_credito']:
        raise DevolucionGarantiaError(
            f'El documento #{dte.numero_documento} se vendió a crédito '
            f'({"; ".join(cond["motivos"])}): no corresponde entregar efectivo de la '
            f'caja. Use "Rebaja de crédito".'
        )
    if cond['monto_credito'] > 0:
        raise DevolucionGarantiaError(
            f'El documento #{dte.numero_documento} se pagó en parte a crédito '
            f'(${int(cond["monto_credito"]):,} con {", ".join(cond["metodos_pago"])}): '
            f'no corresponde entregar en efectivo lo que el cliente no pagó en la caja. '
            f'Envíe la devolución a aprobación.'
        )

    fecha = fecha or timezone.localdate()
    arqueo = ArqueoCaja.objects.filter(sucursal=sucursal, fecha_arqueo=fecha).first()
    if arqueo and arqueo.estado != 'ABIERTO':
        raise DevolucionGarantiaError(
            f"La caja de hoy en {sucursal.alias} ya está '{arqueo.get_estado_display()}': "
            f"entregar efectivo ahora descuadraría el arqueo. Devuelva por transferencia "
            f"o envíe la devolución a aprobación (el administrador elige la fecha)."
        )


def buscar_dte_para_devolucion(folio, sucursal=None):
    """
    Busca una boleta/factura de venta por folio (número de documento).
    Si `sucursal` viene, restringe a esa sucursal; si no, busca en todas
    (patrón inspirado en `buscar_ticket_por_folio` de Requerimientos).

    Devuelve el Dte (con sus dte_productos precargados) o lanza
    DevolucionGarantiaError si no existe / no es un documento de venta válido.
    """
    try:
        folio_num = int(folio)
    except (TypeError, ValueError):
        raise DevolucionGarantiaError('Folio inválido.')

    qs = Dte.objects.select_related('receptor', 'sucursal').prefetch_related(
        'dte_productos__productoTalla__producto'
    ).filter(
        numero_documento=folio_num,
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        tipo_documento__in=TIPOS_DTE_VENTA_VALIDOS,
        estado_dte__in=['EMITIDO', 'ACEPTADO'],
    )
    if sucursal is not None:
        qs = qs.filter(sucursal=sucursal)

    dte = qs.order_by('-id').first()
    if not dte:
        raise DevolucionGarantiaError(f'No se encontró boleta/factura de venta #{folio_num}.')

    return dte


def _base_es_bruto(dte):
    """True si los precios por línea del DTE están CON IVA (BRUTO).

    Usa `base_lineas_dte` (comparación Σlíneas vs cabecera) con el mismo
    fallback por nombre que `anular_factura_dte` cuando es 'DESCONOCIDO'.
    """
    from app.views_modulo_documentos import base_lineas_dte
    base = base_lineas_dte(dte)
    if base == 'DESCONOCIDO':
        base = 'BRUTO' if 'BOLETA' in (dte.tipo_documento or '').upper() else 'NETO'
    return base == 'BRUTO'


def calcular_disponible_linea(dte_producto, dte=None, excluir_devolucion_id=None):
    """
    Disponibilidad de una línea del DTE para devolver, en unidades y en monto
    CON IVA. Descuenta, sin doble conteo:
      (a) NC vivas de CUALQUIER vía por talla (gestión-DTE, regularización y
          las NC modo-CANTIDAD de este módulo — todas llevan productoTalla);
      (b) reservas DG modo CANTIDAD aún pendientes (PENDIENTE/REGISTRADA;
          las NC_GENERADA ya se cuentan en (a));
      (c) consumo DG modo MONTO en todos los estados vivos (sus líneas de NC
          son conceptuales y nunca aparecen por talla, así que se cuentan aquí).

    `excluir_devolucion_id` omite una devolución concreta de (b)/(c) — se usa
    al re-validar en la aprobación para no contar la propia reserva.

    Limitación: las NC "corrige montos" emitidas desde gestión-DTE (líneas
    conceptuales sin talla) no son atribuibles por línea; solo las cubre el
    guard de documento (`saldo_documento`), igual que en gestión-DTE.
    """
    from app.views_modulo_documentos import monto_real_linea_dte

    dp = dte_producto
    dte = dte or dp.dte
    es_bruto = _base_es_bruto(dte)
    stock = int(dp.stock or 0)

    vigente_nativo = monto_real_linea_dte(dp, stock)
    monto_linea_vigente = vigente_nativo if es_bruto else int(round(vigente_nativo * Decimal('1.19')))
    precio_unitario_con_iva = int(round(monto_linea_vigente / stock)) if stock > 0 else 0

    # (a) NC vivas por talla (cualquier vía)
    nc_cantidad = 0
    if dp.productoTalla_id:
        nc_cantidad = Dte_Productos.objects.filter(
            dte__documento_afectado_id=dte.id,
            dte__es_nota_credito=True,
            dte__estado_dte__in=['EMITIDO', 'ACEPTADO'],
            productoTalla_id=dp.productoTalla_id,
        ).aggregate(t=Sum('stock'))['t'] or 0
    nc_cantidad = int(nc_cantidad)

    # (b) reservas DG modo CANTIDAD pendientes
    resv_qs = DevolucionGarantiaDetalle.objects.filter(
        dte_producto=dp, modo='CANTIDAD',
        devolucion__estado__in=ESTADOS_RESERVA,
    )
    if excluir_devolucion_id:
        resv_qs = resv_qs.exclude(devolucion_id=excluir_devolucion_id)
    resv_cantidad = int(resv_qs.aggregate(t=Sum('cantidad'))['t'] or 0)

    # (c) consumo DG modo MONTO vivo
    consumo_qs = DevolucionGarantiaDetalle.objects.filter(
        dte_producto=dp, modo='MONTO',
        devolucion__estado__in=ESTADOS_CONSUMO,
    )
    if excluir_devolucion_id:
        consumo_qs = consumo_qs.exclude(devolucion_id=excluir_devolucion_id)
    consumo_monto = int(consumo_qs.aggregate(t=Sum('monto'))['t'] or 0)

    cantidad_disponible = max(0, stock - nc_cantidad - resv_cantidad)
    monto_disponible = max(0, (
        monto_linea_vigente
        - nc_cantidad * precio_unitario_con_iva
        - resv_cantidad * precio_unitario_con_iva
        - consumo_monto
    ))

    return {
        'cantidad_disponible': cantidad_disponible,
        'monto_disponible': monto_disponible,
        'monto_linea_vigente': monto_linea_vigente,
        'precio_unitario_con_iva': precio_unitario_con_iva,
        'cantidad_vendida': stock,
    }


def saldo_documento(dte_original, excluir_devolucion_id=None):
    """
    Saldo de NC disponible sobre el documento completo (CON IVA):
      monto_restante = monto_original - NC previas vivas - reservas DG pendientes.

    `total_nc_previas` sigue el criterio exacto de `anular_factura_dte`. Las
    reservas pendientes (sin NC aún) se restan aparte para que dos solicitudes
    concurrentes no puedan sumar más que el documento. `excluir_devolucion_id`
    omite la propia reserva al re-validar en la aprobación.
    """
    # Redondeo half-up en vez de int() (que trunca): `monto_con_iva` puede
    # traer decimales (DTEs guardados como neto*1.19 sin redondear, ej.
    # 28.740 → 34.200,60) y truncar dejaba el saldo 1 peso bajo el total de
    # una devolución por el 100% del documento.
    monto_original = int(Decimal(dte_original.monto_con_iva or 0).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))
    total_nc_previas = int(Decimal(Dte.objects.filter(
        documento_afectado=dte_original,
        es_nota_credito=True,
        estado_dte__in=['EMITIDO', 'ACEPTADO'],
    ).aggregate(t=Sum('monto_con_iva'))['t'] or 0).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))

    resv_qs = DevolucionGarantia.objects.filter(
        dte_original=dte_original, estado__in=ESTADOS_RESERVA,
    )
    if excluir_devolucion_id:
        resv_qs = resv_qs.exclude(id=excluir_devolucion_id)
    reservado_pendiente = int(resv_qs.aggregate(t=Sum('monto_total'))['t'] or 0)

    monto_restante = monto_original - total_nc_previas - reservado_pendiente
    return {
        'monto_original': monto_original,
        'total_nc_previas': total_nc_previas,
        'reservado_pendiente': reservado_pendiente,
        'monto_restante': monto_restante,
    }


def _validar_lineas(dte_original, detalles, lock=False, excluir_devolucion_id=None):
    """
    Valida `detalles` = [{dte_producto_id, modo, cantidad?, monto?}] contra la
    disponibilidad por línea. Devuelve (lineas_validadas, monto_total_con_iva).

    Cada línea validada: {dte_producto, modo, cantidad, monto_con_iva,
    precio_unitario_con_iva}. Con `lock=True` bloquea cada Dte_Productos
    (debe llamarse dentro de una transacción).
    """
    if not detalles:
        raise DevolucionGarantiaError('Debe indicar al menos un producto a devolver.')

    lineas = []
    monto_total = 0
    vistos = set()
    for item in detalles:
        try:
            pid = int(item.get('dte_producto_id'))
        except (TypeError, ValueError):
            raise DevolucionGarantiaError('Producto inválido en el detalle.')
        if pid in vistos:
            raise DevolucionGarantiaError('Un producto aparece duplicado en el detalle.')
        vistos.add(pid)

        qs = Dte_Productos.objects.select_related('productoTalla__producto').filter(
            id=pid, dte=dte_original,
        )
        if lock:
            # of=('self',) → FOR UPDATE OF <dte_productos> : lockea solo la
            # fila base y no el LEFT OUTER JOIN de productoTalla (FK nullable),
            # que PostgreSQL rechaza ("FOR UPDATE cannot be applied to the
            # nullable side of an outer join").
            qs = qs.select_for_update(of=('self',))
        dp = qs.first()
        if dp is None:
            raise DevolucionGarantiaError('Uno de los productos no pertenece a la boleta indicada.')

        modo = (item.get('modo') or 'CANTIDAD').upper()
        disp = calcular_disponible_linea(dp, dte=dte_original, excluir_devolucion_id=excluir_devolucion_id)
        sku = dp.productoTalla.sku if dp.productoTalla else f'#{pid}'

        if modo == 'CANTIDAD':
            cantidad = int(item.get('cantidad') or 0)
            if cantidad <= 0:
                raise DevolucionGarantiaError(f'Línea {sku}: la cantidad a devolver debe ser mayor a 0.')
            if cantidad > disp['cantidad_disponible']:
                raise DevolucionGarantiaError(
                    f'Línea {sku}: solicitas {cantidad} pero solo hay '
                    f'{disp["cantidad_disponible"]} unidades disponibles.'
                )
            monto_ci = cantidad * disp['precio_unitario_con_iva']
            # Una devolución MONTO previa reduce monto_disponible pero NO
            # cantidad_disponible (sus NC son conceptuales, sin talla): sin
            # este guard, CANTIDAD podía sobre-acreditar la línea.
            if monto_ci > disp['monto_disponible']:
                raise DevolucionGarantiaError(
                    f'Línea {sku}: {cantidad} unidad(es) equivalen a ${monto_ci:,} '
                    f'pero solo quedan ${disp["monto_disponible"]:,} disponibles en esa '
                    f'línea (ya tiene devoluciones por monto).'
                )
            lineas.append({
                'dte_producto': dp, 'modo': 'CANTIDAD', 'cantidad': cantidad,
                'monto_con_iva': monto_ci,
                'precio_unitario_con_iva': disp['precio_unitario_con_iva'],
            })
            monto_total += monto_ci

        elif modo == 'MONTO':
            try:
                monto_ci = int(round(float(item.get('monto') or 0)))
            except (TypeError, ValueError):
                raise DevolucionGarantiaError(f'Línea {sku}: monto inválido.')
            if monto_ci <= 0:
                raise DevolucionGarantiaError(f'Línea {sku}: el monto a devolver debe ser mayor a 0.')
            if monto_ci > disp['monto_disponible']:
                raise DevolucionGarantiaError(
                    f'Línea {sku}: el monto (${monto_ci:,}) supera lo disponible '
                    f'para esa línea (${disp["monto_disponible"]:,}).'
                )
            lineas.append({
                'dte_producto': dp, 'modo': 'MONTO', 'cantidad': 0,
                'monto_con_iva': monto_ci, 'precio_unitario_con_iva': 0,
            })
            monto_total += monto_ci
        else:
            raise DevolucionGarantiaError(f'Modo de devolución inválido: {modo}.')

    return lineas, monto_total


def resolver_o_crear_receptor(*, cliente_id=None, rut='', nombre='', giro='',
                               direccion='', comuna='', ciudad='',
                               email='', telefono=''):
    """
    Busca una Empresa (el único modelo válido como receptor de un DTE en
    este sistema) por `cliente_id` o `rut`; si no existe la crea con los
    datos dados. Lógica inspirada en `guardar_cliente_pos`
    (views_modulo_ventas.py), extraída/adaptada aquí para no importar una
    vista desde un service.

    A diferencia de `guardar_cliente_pos` (que solo formatea el RUT), esta
    función SÍ valida el dígito verificador antes de crear una Empresa
    nueva — es el requisito central de este módulo: la NC debe quedar con
    un RUT real y válido, no uno mal tipeado. No revalida el RUT de una
    Empresa ya existente en BD (podría tener datos legacy imperfectos que
    no corresponde romper aquí).

    Requiere como mínimo rut + nombre para crear. Lanza
    DevolucionGarantiaError si no hay datos suficientes o el RUT es inválido.
    """
    from .fidelizacion_service import es_rut_empresa

    def _formatear_rut(valor):
        limpio = ''.join(c for c in (valor or '') if c.isdigit() or c.lower() == 'k')
        if len(limpio) < 2:
            return valor
        return f"{limpio[:-1]}-{limpio[-1]}"

    nombre = (nombre or '').strip()
    rut_raw = (rut or '').strip()
    rut_norm = _formatear_rut(rut_raw) if rut_raw else ''

    empresa = None
    if cliente_id:
        empresa = Empresa.objects.filter(id=cliente_id).first()

    if empresa is None and rut_norm:
        empresa = Empresa.objects.filter(
            Q(rut__iexact=rut_norm) | Q(rut__iexact=rut_raw)
        ).order_by('-id').first()

    if empresa is not None:
        # El propósito central de este módulo es que la NC quede con un
        # cliente real, no con el receptor genérico de consumidor final —
        # aunque exista una Empresa así en la BD (heredada de boletas
        # antiguas), no se acepta como receptor de una devolución nueva.
        rut_empresa_limpio = (empresa.rut or '').replace('.', '')
        if rut_empresa_limpio in ('66666666-6', '666666666'):
            raise DevolucionGarantiaError(
                'El RUT ingresado corresponde a "Consumidor Final" (genérico). '
                'Ingrese el RUT real del cliente que recibe la devolución.'
            )
        # Actualizar solo si llegan valores nuevos (no pisar datos buenos con vacío).
        if nombre:
            empresa.nombre = nombre
            empresa.razon_social = nombre
        if giro:
            empresa.giro = giro
        elif not (empresa.giro or '').strip() and not es_rut_empresa(empresa.rut or rut_norm):
            # Persona natural sin giro cargado: completarlo para que la NC no
            # salga con el campo vacío. No pisa un giro real ya existente.
            empresa.giro = GIRO_PARTICULAR_DEFAULT
        if direccion:
            empresa.direccion = direccion
        if comuna:
            empresa.comuna = comuna
        if ciudad:
            empresa.ciudad = ciudad
        if email:
            empresa.correoVendedor = email
        if telefono:
            empresa.contacto1 = telefono
        empresa.save()
        return empresa

    if not rut_norm or not nombre:
        raise DevolucionGarantiaError(
            'Debe indicar RUT y nombre/razón social del cliente para registrar el receptor.'
        )

    from app.models.base import validar_rut_chileno
    if not validar_rut_chileno(rut_norm):
        raise DevolucionGarantiaError(f'RUT inválido: {rut_norm}. Verifique el dígito verificador.')

    es_juridica = es_rut_empresa(rut_norm)
    if es_juridica and not (giro and direccion and comuna and ciudad):
        raise DevolucionGarantiaError(
            'Para un cliente con RUT de empresa, giro/dirección/comuna/ciudad son obligatorios.'
        )

    empresa = Empresa.objects.create(
        nombre=nombre,
        rut=rut_norm,
        nombre_fantasia=nombre,
        razon_social=nombre,
        # Persona natural sin giro → 'PARTICULAR' (el SII lo exige en la NC).
        giro=giro or ('' if es_juridica else GIRO_PARTICULAR_DEFAULT),
        direccion=direccion or '',
        comuna=comuna or '',
        ciudad=ciudad or '',
        esProveedor=False,
        correoVendedor=email or '',
        correoIntercambio='',
        correoAdministrador='',
        contacto1=telefono or '',
        contacto2='',
    )
    return empresa


def _registrar_historial_requerimiento(devolucion, accion, comentario, usuario, desvincular=False):
    """Registra una acción en el historial del Requerimiento vinculado (si lo
    hay) y opcionalmente lo desvincula para permitir re-solicitar."""
    from app.models import HistorialRequerimiento
    req = devolucion.requerimiento_origen.first()
    if not req:
        return
    HistorialRequerimiento.objects.create(
        requerimiento=req,
        accion=accion,
        estado_anterior=req.estado,
        estado_nuevo=req.estado,
        comentario=comentario,
        usuario=usuario,
    )
    if desvincular:
        req.devolucion_garantia = None
        req.save(update_fields=['devolucion_garantia'])


def _validar_datos_transferencia(metodo_solicitado, banco, tipo_cuenta,
                                 numero_cuenta, cuenta_titular_rut):
    """Normaliza y valida el método pedido por el cliente. Para transferencia
    exige banco/tipo/número/titular; devuelve la tupla normalizada."""
    # str() defensivo: por API el número de cuenta puede llegar numérico.
    def _txt(v):
        return str(v if v is not None else '').strip()

    metodo = _txt(metodo_solicitado).upper()
    if metodo and metodo not in dict(METODO_DEVOLUCION_DG_CHOICES):
        raise DevolucionGarantiaError('Método de devolución inválido.')

    banco = _txt(banco)
    tipo_cuenta = _txt(tipo_cuenta).upper()
    numero_cuenta = _txt(numero_cuenta)
    cuenta_titular_rut = _txt(cuenta_titular_rut)

    if metodo == 'TRANSFERENCIA_BANCARIA':
        faltan = []
        if not banco:
            faltan.append('banco')
        if not tipo_cuenta:
            faltan.append('tipo de cuenta')
        if not numero_cuenta:
            faltan.append('número de cuenta')
        if not cuenta_titular_rut:
            faltan.append('RUT del titular')
        if faltan:
            raise DevolucionGarantiaError(
                'Para transferencia debe indicar: ' + ', '.join(faltan) + '.'
            )
        from app.models.base import validar_rut_chileno
        if not validar_rut_chileno(cuenta_titular_rut):
            raise DevolucionGarantiaError(
                f'RUT del titular inválido: {cuenta_titular_rut}. Verifique el dígito verificador.'
            )
        if tipo_cuenta not in dict(TIPO_CUENTA_DG_CHOICES):
            raise DevolucionGarantiaError('Tipo de cuenta inválido.')
    else:
        # Efectivo (o sin especificar): no se guardan datos bancarios.
        banco = tipo_cuenta = numero_cuenta = cuenta_titular_rut = ''

    return metodo, banco, tipo_cuenta, numero_cuenta, cuenta_titular_rut


@transaction.atomic
# ===================== INVENTARIO =====================

def _mover_inventario_devolucion(devolucion, nc, lineas, aprobador, todas_no_aptas, ids_no_aptos):
    """Reingresa (o registra como no apto) el producto de cada línea CANTIDAD.

    Devuelve un dict serializable:
      reingresos    [{producto_talla_id, sku, talla, cantidad, cambio}]  entraron a stock
      no_aptas      [{producto_talla_id, sku, talla, cantidad, cambio}]  kardex documental
      sin_reingreso [{producto_talla_id, cantidad, cambio}]  ya devueltas en un cambio sin reemplazo
      avisos        [str]  textos de ReingresoVenta para el operador

    Las aptas van primero: `ReingresoVenta` lleva la cuenta de lo acreditado
    por talla y las no aptas se reparten (`repartir`) desde donde quedó, para
    que el kardex documental apunte al SKU que el cliente trae de verdad si
    hubo un cambio previo. La cantidad de un mismo SKU en dos líneas no aptas
    de la MISMA solicitud se reparte desde el mismo punto (caso raro: el
    documento tendría dos líneas del mismo SKU); es solo documental.
    """
    from app.services.reingreso_devolucion import ReingresoVenta, repartir

    dte = devolucion.dte_original
    responsable = aprobador.username
    doc = f'{dte.get_tipo_documento_display()} #{dte.numero_documento}'
    reingreso = ReingresoVenta(dte, excluir_nc_id=nc.id)
    inventario = {'reingresos': [], 'no_aptas': [], 'sin_reingreso': [], 'avisos': []}

    aptas, no_aptas = [], []
    for linea in lineas:
        dp = linea['dte_producto']
        if linea['modo'] != 'CANTIDAD' or not dp.productoTalla_id:
            continue  # MONTO (sin unidades) y líneas sin talla no mueven nada
        destino = no_aptas if (todas_no_aptas or dp.id in ids_no_aptos) else aptas
        destino.append((dp, int(linea['cantidad']), int(linea.get('precio_nc') or dp.precio or 0)))

    for dp, cantidad, precio in aptas:
        pt = dp.productoTalla
        # Costo del kardex/lote: el de la línea vendida y, si venía en 0, el
        # del producto (regla: costo = producto.costo o costo del lote).
        costo = int(dp.costo or 0) or int(getattr(pt.producto, 'costo', 0) or 0)
        reingreso.reingresar(
            pt.id, cantidad, 'DEVOLUCION_NC', responsable,
            dte_movimiento=nc,
            # Receptor del INGRESO = sucursal dueña del SKU (regla del kardex).
            sucursal_destino=pt.producto.sucursal or devolucion.sucursal,
            costo_unitario=costo,
            sobreprecio_unitario=int(dp.sobreprecio or 0),
            precio_unitario=precio,
            observaciones=(
                f'Devolución de dinero {devolucion.numero_operacion}: {cantidad} u. '
                f'vuelven al inventario aptas para la venta (NC #{nc.numero_documento} sobre {doc}).'
            ),
            referencia_externa=devolucion.numero_operacion,
        )

    for dp, cantidad, precio in no_aptas:
        partes, _ambiguo = repartir(
            dp.productoTalla_id, reingreso.ya_acreditadas(dp.productoTalla_id),
            cantidad, reingreso.tickets,
        )
        for pt_id, unidades, numero_cambio in partes:
            if pt_id is None:
                inventario['sin_reingreso'].append({
                    'producto_talla_id': dp.productoTalla_id, 'cantidad': unidades,
                    'cambio': numero_cambio,
                })
                continue
            pt = Producto_Talla.objects.select_related('producto__sucursal').get(id=pt_id)
            obs = (
                f'Devolución de dinero {devolucion.numero_operacion} NO APTA: {unidades} u. '
                f'del SKU {pt.sku} NO entran a stock (producto fallado). '
                f'NC #{nc.numero_documento} sobre {doc}.'
            )
            if pt_id != dp.productoTalla_id:
                obs += (f' El SKU {dp.productoTalla.sku} vendido se había cambiado en '
                        f'{numero_cambio}: lo devuelto es lo entregado en su lugar.')
            # Registro documental, mismo formato que Cambios (cantidad 0, tipo
            # AJUSTE): no toca stock ni lotes. Pendiente de política: si la
            # empresa tuviera sucursal de fallados, acá iría un ingresar() al
            # clon del SKU en esa sucursal (ver constants_kardex.CONCEPTOS_SIN_STOCK).
            Movimientos_Producto.objects.create(
                ProductoTalla=pt,
                dte=nc,
                tipo_movimiento='AJUSTE',
                concepto='DEVOLUCION_NO_APTA',
                cantidad=0,
                costo=int(dp.costo or 0) or int(getattr(pt.producto, 'costo', 0) or 0),
                precio=precio,
                responsable=responsable,
                sucursal_destino=pt.producto.sucursal or devolucion.sucursal,
                estado='COMPLETADO',
                referencia_externa=devolucion.numero_operacion,
                observaciones=obs[:500],
            )
            inventario['no_aptas'].append({
                'producto_talla_id': pt_id, 'sku': pt.sku, 'talla': pt.talla or '',
                'cantidad': unidades,
                'cambio': numero_cambio if pt_id != dp.productoTalla_id else None,
            })

    tallas = dict(Producto_Talla.objects.filter(
        id__in=[r['producto_talla_id'] for r in reingreso.resumen['reingresos']]
    ).values_list('id', 'talla'))
    for r in reingreso.resumen['reingresos']:
        inventario['reingresos'].append({
            'producto_talla_id': r['producto_talla_id'], 'sku': r['sku'],
            'talla': tallas.get(r['producto_talla_id']) or '',
            'cantidad': r['cantidad'], 'cambio': r['cambio'],
        })
    inventario['sin_reingreso'].extend(reingreso.resumen['sin_reingreso'])
    inventario['avisos'] = reingreso.avisos()
    return inventario


def texto_inventario_devolucion(inventario):
    """Resumen en una línea de lo que pasó con el producto (va a
    `observaciones_aprobacion` y a los avisos de la UI). '' si no hubo nada."""
    if not inventario:
        return ''
    partes = []
    if inventario.get('reingresos'):
        partes.append('Reingresó a inventario: ' + ', '.join(
            f"{r['cantidad']} u. SKU {r['sku']}" + (f" (talla {r['talla']})" if r.get('talla') else '')
            for r in inventario['reingresos']) + '.')
    if inventario.get('no_aptas'):
        partes.append('NO APTO, sin ingreso a stock: ' + ', '.join(
            f"{r['cantidad']} u. SKU {r['sku']}" + (f" (talla {r['talla']})" if r.get('talla') else '')
            for r in inventario['no_aptas']) + '.')
    for aviso in inventario.get('avisos') or []:
        partes.append(aviso)
    return ('Inventario — ' + ' '.join(partes)) if partes else ''


def inventario_de_devolucion(devolucion):
    """Qué pasó con el producto al aprobar, leído del kardex ligado a la NC
    (persistente: sirve para el detalle aunque la aprobación fuera en otra
    sesión). None si la devolución no tiene NC."""
    if not devolucion.nota_credito_id:
        return None
    movs = Movimientos_Producto.objects.filter(
        dte_id=devolucion.nota_credito_id,
        concepto__in=('DEVOLUCION_NC', 'DEVOLUCION_NO_APTA'),
    ).select_related('ProductoTalla', 'sucursal_destino').order_by('id')
    inventario = {'reingresos': [], 'no_aptas': [], 'sin_reingreso': [], 'avisos': []}
    for m in movs:
        pt = m.ProductoTalla
        fila = {
            'producto_talla_id': pt.id, 'sku': pt.sku, 'talla': pt.talla or '',
            'sucursal': m.sucursal_destino.alias if m.sucursal_destino else '',
            'observaciones': m.observaciones or '',
        }
        if m.concepto == 'DEVOLUCION_NC':
            fila['cantidad'] = int(m.cantidad or 0)
            fila['cambio'] = 'se había cambiado' in (m.observaciones or '')
            inventario['reingresos'].append(fila)
        else:
            # La cantidad física de un registro documental va en la observación.
            n = re.search(r'NO APTA: (\d+) u\.', m.observaciones or '')
            fila['cantidad'] = int(n.group(1)) if n else 0
            inventario['no_aptas'].append(fila)
    return inventario


def crear_solicitud_devolucion(*, dte_original, sucursal, receptor, motivo,
                               usuario, detalles, requerimiento=None,
                               metodo_solicitado='', banco='', tipo_cuenta='',
                               numero_cuenta='', cuenta_titular_rut='',
                               lineas_no_aptas=None, no_apto=False):
    """
    Registra una SOLICITUD de devolución en estado PENDIENTE. NO consume folio
    de NC, NO genera documento ni TXT: eso ocurre al aprobar.

    `detalles`: lista de dicts {dte_producto_id, modo, cantidad?, monto?,
        no_apto?}. `no_apto` por línea (o `lineas_no_aptas` = ids, o `no_apto`
        global) marca que el producto devuelto NO está apto para la venta: al
        aprobar no vuelve a stock (queda kardex documental). Se persiste como
        marca al final de `motivo` (ver MARCA_NO_APTO_RE).
    `requerimiento`: instancia opcional de Requerimiento (puente de UI).
    `metodo_solicitado` + datos bancarios: cómo quiere el cliente recibir la
        devolución (efectivo o transferencia con banco/cuenta/titular).

    Devuelve la DevolucionGarantia creada.
    """
    (metodo_solicitado, banco, tipo_cuenta, numero_cuenta,
     cuenta_titular_rut) = _validar_datos_transferencia(
        metodo_solicitado, banco, tipo_cuenta, numero_cuenta, cuenta_titular_rut,
    )

    # Cobro con tarjeta ya anulado por la máquina Transbank → doble devolución.
    _validar_cobro_transbank_no_anulado(dte_original)

    lineas, monto_total = _validar_lineas(dte_original, detalles, lock=True)

    # Marca de líneas no aptas: solo ids que de verdad están en la solicitud.
    ids_solicitud = {l['dte_producto'].id for l in lineas}
    ids_no_aptos = _ids_no_aptos_de_detalles(detalles, lineas_no_aptas) & ids_solicitud
    motivo = _marcar_no_apto(motivo or 'Garantía aprobada', ids_no_aptos, todas=bool(no_apto))

    saldo = saldo_documento(dte_original)
    if monto_total > saldo['monto_restante']:
        raise DevolucionGarantiaError(
            f'El monto total solicitado (${monto_total:,}) excede el saldo disponible '
            f'de NC sobre este documento (${saldo["monto_restante"]:,}).'
        )

    # Pedir la devolución por Mercado Pago solo tiene sentido si la venta se
    # cobró por ahí (y por hasta lo cobrado): se avisa ya, no al aprobar.
    if metodo_solicitado == 'MERCADO_PAGO':
        _validar_devolucion_mercadopago(dte_original, monto_total)

    # === Número de operación ===
    # select_for_update() sobre el rango del prefijo: dos solicitudes
    # concurrentes de la misma sucursal/mes se serializan aquí para no calcular
    # el mismo correlativo y chocar con el unique=True de numero_operacion.
    fecha = timezone.now()
    prefijo = f"DG-{sucursal.id}-{fecha.strftime('%Y%m')}"
    ultimo = DevolucionGarantia.objects.select_for_update().filter(
        numero_operacion__startswith=prefijo
    ).order_by('-numero_operacion').first()
    correlativo_local = 1
    if ultimo:
        try:
            correlativo_local = int(ultimo.numero_operacion.split('-')[-1]) + 1
        except ValueError:
            correlativo_local = 1
    numero_operacion = f"{prefijo}-{correlativo_local:04d}"

    devolucion = DevolucionGarantia.objects.create(
        dte_original=dte_original,
        sucursal=sucursal,
        receptor=receptor,
        motivo=motivo or 'Garantía aprobada',
        solicitado_por=usuario,
        autorizado_por=None,
        estado='PENDIENTE',
        monto_total=monto_total,
        numero_operacion=numero_operacion,
        metodo_solicitado=metodo_solicitado,
        banco=banco,
        tipo_cuenta=tipo_cuenta,
        numero_cuenta=numero_cuenta,
        cuenta_titular_rut=cuenta_titular_rut,
    )

    for linea in lineas:
        if linea['modo'] == 'CANTIDAD':
            DevolucionGarantiaDetalle.objects.create(
                devolucion=devolucion,
                dte_producto=linea['dte_producto'],
                modo='CANTIDAD',
                cantidad=linea['cantidad'],
                precio_unitario=linea['precio_unitario_con_iva'],
                monto=None,
                subtotal=linea['monto_con_iva'],
            )
        else:
            DevolucionGarantiaDetalle.objects.create(
                devolucion=devolucion,
                dte_producto=linea['dte_producto'],
                modo='MONTO',
                cantidad=0,
                precio_unitario=0,
                monto=linea['monto_con_iva'],
                subtotal=linea['monto_con_iva'],
            )

    if requerimiento is not None:
        from app.models import HistorialRequerimiento
        requerimiento.devolucion_garantia = devolucion
        requerimiento.save(update_fields=['devolucion_garantia'])
        HistorialRequerimiento.objects.create(
            requerimiento=requerimiento,
            accion='DEVOLUCION_GARANTIA_SOLICITADA',
            estado_anterior=requerimiento.estado,
            estado_nuevo=requerimiento.estado,
            comentario=(
                f"Solicitud de devolución {numero_operacion} creada por "
                f"{usuario.username} (${monto_total:,.0f}), pendiente de aprobación."
            ),
            usuario=usuario,
        )

    return devolucion


@transaction.atomic
def aprobar_devolucion(*, devolucion_id, aprobador, metodo_devolucion,
                       fecha_imputacion=None, observaciones='', numero_operacion_mp='',
                       devolver_mp_api=False, refund_ctx=None,
                       lineas_no_aptas=None, no_apto=None):
    """
    Aprueba una solicitud PENDIENTE: genera la NC 61 + TXT Acepta con el
    impacto en caja elegido por el aprobador. Re-valida disponibilidad bajo
    lock; si cambió, lanza DevolucionGarantiaError y la solicitud queda
    PENDIENTE (el aprobador decide rechazar o esperar).

    `numero_operacion_mp`: solo para MERCADO_PAGO — N° de operación de Mercado
    Pago de la devolución; si viene vacío se usa el del cobro original y, si el
    sistema no lo conoce, se exige.

    Inventario: las líneas CANTIDAD con talla reingresan al stock (lote FIFO +
    kardex DEVOLUCION_NC ligado a la NC) salvo las NO APTAS, que dejan solo un
    kardex documental DEVOLUCION_NO_APTA. Qué es no apto lo decide, en este
    orden: `no_apto=True` (todas), `lineas_no_aptas` (ids de Dte_Productos que
    manda el aprobador; una lista vacía = todas aptas) o, si ninguno viene, la
    marca que dejó el solicitante en `motivo`. El resultado queda en
    `devolucion.inventario` (dict, ver `_mover_inventario_devolucion`) y
    resumido en `observaciones_aprobacion`.

    Devuelve (devolucion, nc, contenido_txt_o_None, txt_warnings).
    """
    from app.views import obtener_siguiente_correlativo
    from app.views_modulo_documentos import calcular_montos_nc, monto_real_linea_dte

    devolucion = DevolucionGarantia.objects.select_for_update().select_related(
        'dte_original', 'receptor', 'sucursal',
    ).get(id=devolucion_id)
    if devolucion.estado != 'PENDIENTE':
        raise DevolucionGarantiaError(
            f'La solicitud ya fue resuelta (estado {devolucion.get_estado_display()}).'
        )

    if metodo_devolucion not in dict(METODO_DEVOLUCION_DG_CHOICES):
        raise DevolucionGarantiaError('Método de devolución inválido.')
    # "Afecta caja" = se imputa a un día de cuadratura y lleva Dte_Detalle_Pago.
    # REBAJA_CREDITO también entra: no toca efectivo, pero sí baja la cuenta
    # por cobrar y debe verse en la cuadratura del día elegido.
    afecta_caja = metodo_devolucion in METODOS_DG_CON_IMPUTACION

    dte_original = devolucion.dte_original
    sucursal = devolucion.sucursal

    # Una venta a crédito no puede devolverse con plata de la caja: el cliente
    # nunca la puso ahí. Se valida acá (no solo en la UI) porque el endpoint
    # acepta el método por JSON.
    _validar_metodo_vs_condicion_pago(dte_original, metodo_devolucion)

    # Re-chequeo al aprobar: la anulación por la máquina Transbank pudo
    # registrarse DESPUÉS de creada la solicitud.
    _validar_cobro_transbank_no_anulado(dte_original)

    fecha_imp = None
    if afecta_caja:
        fecha_imp = fecha_imputacion or timezone.localdate()
        if fecha_imp > timezone.localdate():
            raise DevolucionGarantiaError('La fecha de imputación en caja no puede ser futura.')
        if dte_original.fecha_emision and fecha_imp < dte_original.fecha_emision:
            raise DevolucionGarantiaError(
                'La fecha de imputación no puede ser anterior a la emisión del documento original.'
            )

    # Re-validar bajo lock, excluyendo la propia reserva.
    detalles = []
    for det in devolucion.detalles.all():
        if det.modo == 'MONTO':
            detalles.append({
                'dte_producto_id': det.dte_producto_id, 'modo': 'MONTO',
                'monto': int(det.monto or det.subtotal or 0),
            })
        else:
            detalles.append({
                'dte_producto_id': det.dte_producto_id, 'modo': 'CANTIDAD',
                'cantidad': det.cantidad,
            })
    lineas, _monto_est = _validar_lineas(
        dte_original, detalles, lock=True, excluir_devolucion_id=devolucion.id,
    )

    saldo = saldo_documento(dte_original, excluir_devolucion_id=devolucion.id)
    total_nc_previas = saldo['total_nc_previas']

    # Montos NC mixtos (sin doble IVA).
    lineas_cantidad = [(l['dte_producto'], l['cantidad']) for l in lineas if l['modo'] == 'CANTIDAD']
    lineas_monto = [l for l in lineas if l['modo'] == 'MONTO']

    neto_cant = bruto_cant = 0
    if lineas_cantidad:
        neto_cant, bruto_cant = calcular_montos_nc(dte_original, lineas_cantidad)
    bruto_monto = sum(l['monto_con_iva'] for l in lineas_monto)
    neto_monto = int(round(bruto_monto / Decimal('1.19'))) if bruto_monto else 0

    monto_con_iva_nc = int(bruto_cant) + int(bruto_monto)
    monto_neto_nc = int(neto_cant) + int(neto_monto)

    if monto_con_iva_nc <= 0:
        raise DevolucionGarantiaError('El monto de la NC resultó en 0. Revise las líneas de la solicitud.')
    if monto_con_iva_nc > saldo['monto_restante']:
        raise DevolucionGarantiaError(
            f'El monto de la NC (${monto_con_iva_nc:,}) excede el saldo disponible '
            f'sobre este documento (${saldo["monto_restante"]:,}).'
        )

    # Mercado Pago: la venta tiene que haberse cobrado por ahí (y por hasta lo
    # cobrado), y el N° de operación queda en la NC para ubicar la devolución.
    # `devolver_mp_api`: en vez de registrar una devolución hecha a mano en la
    # app/panel, RetailMind pide el refund a MP (la vista ya validó el permiso
    # `devolver_mercadopago` de quien firma).
    mp = numero_mp = None
    refunds_api = []
    if metodo_devolucion == 'MERCADO_PAGO':
        mp = _validar_devolucion_mercadopago(dte_original, monto_con_iva_nc)
        if devolver_mp_api:
            _validar_devolvible_api(mp, monto_con_iva_nc)
        else:
            numero_mp = _numero_operacion_mp(numero_operacion_mp, mp)

    # Razón SII dinámica: '1' solo si la NC cubre el saldo REAL del documento
    # (monto_original - NC previas vivas) y no hay NC previas; si no '3'.
    # OJO: no comparar contra monto_restante, que además descuenta reservas de
    # OTRAS solicitudes pendientes — una NC parcial saldría declarada como
    # "anula documento" si otra solicitud reservaba el resto.
    saldo_real_documento = saldo['monto_original'] - saldo['total_nc_previas']
    razon = '1' if (monto_con_iva_nc == saldo_real_documento and total_nc_previas == 0) else '3'

    # Refund por API ANTES de emitir la NC: si MP rechaza, no se quema folio.
    if devolver_mp_api and mp is not None:
        refunds_api = _devolver_por_api_mp(
            dte_original, monto_con_iva_nc, devolucion, aprobador, ctx=refund_ctx)
        numero_mp = _numero_cobro_tras_refund(mp, refunds_api)

    numero_nc = obtener_siguiente_correlativo(sucursal, 'NOTA DE CREDITO')
    # Referencia SII: 33 factura electrónica, 39 boleta electrónica, 35 boleta
    # papel (referenciarla como 39 declararía un folio electrónico inexistente).
    tipo_doc_orig = (dte_original.tipo_documento or '')
    if 'FACTURA' in tipo_doc_orig:
        tipo_sii_original = 33
    elif tipo_doc_orig == 'BOLETA PAPEL':
        tipo_sii_original = 35
    else:
        tipo_sii_original = 39
    referencias_json = json.dumps([{
        'tipo_documento': tipo_sii_original,
        'folio': str(dte_original.numero_documento),
        'fecha': dte_original.fecha_emision.strftime('%Y-%m-%d'),
        'razon': razon,
    }])

    # La marca interna [NO_APTO:...] del motivo no va al SII ni al TXT.
    motivo_nc = (
        f"Devolución de dinero {devolucion.numero_operacion}. "
        f"Motivo: {motivo_limpio(devolucion.motivo) or 'Garantía aprobada'}"
    )
    if lineas_monto and razon == '3':
        motivo_nc += " [Corrige montos]"

    tipo_transaccion_nc = 'DEVOLUCION' if afecta_caja else 'ANULACION'

    nc = Dte.objects.create(
        emisor=dte_original.emisor,
        receptor=devolucion.receptor,
        tipo_documento='NOTA DE CREDITO',
        numero_documento=numero_nc,
        monto_neto=monto_neto_nc,
        monto_con_iva=monto_con_iva_nc,
        descuento=0,
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=0,
        unidades_productos=sum(l['cantidad'] for l in lineas),
        estado_dte='EMITIDO',
        estado_pago='PAGADO',
        tipo_transaccion=tipo_transaccion_nc,
        responsable=aprobador.username,
        sucursal=sucursal,
        hora=timezone.localtime().time(),
        es_nota_credito=True,
        documento_afectado=dte_original,
        motivo_nc=motivo_nc,
        referencias=referencias_json,
    )

    # Todas las líneas de la NC deben quedar en la MISMA base que el documento
    # original (boleta: CON IVA, factura: NETO): si las CANTIDAD van en base
    # nativa y las MONTO siempre con IVA, una NC mixta sobre factura queda con
    # dos bases mezcladas y normalizar_detalle_para_tipo no puede cuadrar el TXT.
    es_bruto_nc = _base_es_bruto(dte_original)

    for linea in lineas:
        dp = linea['dte_producto']
        if linea['modo'] == 'CANTIDAD':
            cantidad = linea['cantidad']
            # Precio efectivo en la base nativa del documento (patrón
            # _precio_efectivo_dp de anular_factura_dte): boleta con IVA,
            # factura neto. No usar el monto_con_iva del detalle DG.
            mi_eff = monto_real_linea_dte(dp, cantidad)
            p_eff = int(round(mi_eff / cantidad)) if cantidad and mi_eff else int(dp.precio or 0)
            linea['precio_nc'] = p_eff  # lo lee el reingreso a inventario
            Dte_Productos.objects.create(
                dte=nc,
                productoTalla=dp.productoTalla,
                descripcion=f"[DEV -{cantidad}] {dp.descripcion}",
                costo=dp.costo,
                sobreprecio=dp.sobreprecio,
                precio=p_eff,
                precio_unitario=p_eff,
                monto_item=mi_eff,
                stock=cantidad,
                activo=True,
            )
        else:
            # Línea conceptual (sin talla): no interfiere con la contabilidad
            # por talla ni con el stock. El SKU viaja en la descripción.
            sku_ref = dp.productoTalla.sku if dp.productoTalla else ''
            desc = f"[CORRIGE MONTO] {dp.descripcion}"
            if sku_ref:
                desc += f" (SKU {sku_ref})"
            monto_nativo = (
                int(linea['monto_con_iva']) if es_bruto_nc
                else int(round(linea['monto_con_iva'] / Decimal('1.19')))
            )
            Dte_Productos.objects.create(
                dte=nc,
                productoTalla=None,
                descripcion=desc[:255],
                costo=0,
                sobreprecio=0,
                precio=monto_nativo,
                precio_unitario=monto_nativo,
                monto_item=monto_nativo,
                stock=1,
                activo=True,
            )

    # === INVENTARIO (mismo atomic que la NC) ===
    # Va DESPUÉS de las líneas de la NC (ReingresoVenta la excluye por id para
    # no contar como ya acreditado lo que esta misma NC devuelve) y ANTES del
    # pago. Es un ingreso: no puede fallar por stock. Si el refund MP por API
    # ya salió y algo revienta acá, el atomic revierte la NC y la vista deja
    # los refunds registrados (`persistir_refunds_sin_nc`), como hasta ahora.
    if no_apto is True:
        todas_no_aptas, ids_no_aptos = True, set()
    elif lineas_no_aptas is not None:
        todas_no_aptas, ids_no_aptos = False, _ids_no_aptos_de_detalles(None, lineas_no_aptas)
    else:
        todas_no_aptas, ids_no_aptos = lineas_no_aptas_de(devolucion)
    inventario = _mover_inventario_devolucion(
        devolucion, nc, lineas, aprobador, todas_no_aptas, ids_no_aptos,
    )
    devolucion.inventario = inventario
    texto_inventario = texto_inventario_devolucion(inventario)
    if texto_inventario:
        observaciones = f'{observaciones} {texto_inventario}'.strip() if observaciones else texto_inventario

    # `fecha_pago` = día al que la cuadratura imputa el egreso. El
    # `metodo_pago` decide de qué teórico se descuenta:
    #   EFECTIVO       -> total_efectivo
    #   TRANSFERENCIA  -> total_transferencia
    #   CREDITO_EXTERNO-> total_credito_externo (rebaja de cuenta por cobrar)
    #   MP_*           -> total_mercadopago_pos (mismo método del cobro)
    # NO_AFECTA_CAJA: sin Dte_Detalle_Pago (NC informativa que no resta teóricos).
    metodo_pago_nc = METODO_PAGO_NC_POR_DG.get(metodo_devolucion)
    if metodo_devolucion == 'MERCADO_PAGO':
        datos_pago = _datos_pago_nc_mercadopago(mp, numero_mp, devolucion)
        if refunds_api:
            datos_pago['notas'] = _nota_pago_refund_api(refunds_api, devolucion)
        Dte_Detalle_Pago.objects.create(
            dte=nc, monto=monto_con_iva_nc, fecha_pago=fecha_imp, **datos_pago,
        )
        if refunds_api:
            # `reembolsar` ya dejó las filas DEVOLUCION del libro MP; solo se
            # marcan como de esta NC.
            _marcar_refunds_api(refunds_api, nc, devolucion)
        else:
            _registrar_devolucion_en_libro_mp(
                dte=dte_original, nc=nc, monto=monto_con_iva_nc,
                devolucion=devolucion, usuario=aprobador,
            )
    elif metodo_pago_nc:
        Dte_Detalle_Pago.objects.create(
            dte=nc, metodo_pago=metodo_pago_nc, monto=monto_con_iva_nc, fecha_pago=fecha_imp,
        )

    devolucion.nota_credito = nc
    devolucion.estado = 'NC_GENERADA'
    devolucion.autorizado_por = aprobador
    devolucion.fecha_aprobacion = timezone.now()
    devolucion.observaciones_aprobacion = observaciones or ''
    devolucion.metodo_devolucion = metodo_devolucion
    devolucion.fecha_imputacion_caja = fecha_imp
    devolucion.monto_total = monto_con_iva_nc
    devolucion.save(update_fields=[
        'nota_credito', 'estado', 'autorizado_por', 'fecha_aprobacion',
        'observaciones_aprobacion', 'metodo_devolucion', 'fecha_imputacion_caja',
        'monto_total', 'updated_at',
    ])

    _registrar_historial_requerimiento(
        devolucion, 'DEVOLUCION_GARANTIA_GENERADA',
        (
            f"Devolución de dinero {devolucion.numero_operacion} aprobada "
            f"(NC #{numero_nc}, ${monto_con_iva_nc:,.0f})."
        ),
        aprobador, desvincular=False,
    )

    contenido_txt, txt_warnings = _generar_txt_nc(nc, devolucion)
    return devolucion, nc, contenido_txt, txt_warnings


@transaction.atomic
def rechazar_devolucion(*, devolucion_id, aprobador, motivo_rechazo):
    """Rechaza una solicitud PENDIENTE (motivo obligatorio). Devuelve la devolución."""
    devolucion = DevolucionGarantia.objects.select_for_update().get(id=devolucion_id)
    if devolucion.estado != 'PENDIENTE':
        raise DevolucionGarantiaError(
            f'La solicitud ya fue resuelta (estado {devolucion.get_estado_display()}).'
        )
    motivo_rechazo = (motivo_rechazo or '').strip()
    if not motivo_rechazo:
        raise DevolucionGarantiaError('Debe indicar el motivo del rechazo.')

    devolucion.estado = 'RECHAZADA'
    devolucion.autorizado_por = aprobador
    devolucion.fecha_rechazo = timezone.now()
    devolucion.motivo_rechazo = motivo_rechazo
    devolucion.save(update_fields=[
        'estado', 'autorizado_por', 'fecha_rechazo', 'motivo_rechazo', 'updated_at',
    ])

    _registrar_historial_requerimiento(
        devolucion, 'DEVOLUCION_GARANTIA_RECHAZADA',
        f"Solicitud {devolucion.numero_operacion} rechazada: {motivo_rechazo}",
        aprobador, desvincular=True,
    )
    return devolucion


@transaction.atomic
def anular_solicitud(*, devolucion_id, usuario):
    """Anula una solicitud PENDIENTE (solo el solicitante o un administrador)."""
    devolucion = DevolucionGarantia.objects.select_for_update().get(id=devolucion_id)
    if devolucion.estado != 'PENDIENTE':
        raise DevolucionGarantiaError(
            f'Solo se pueden anular solicitudes pendientes '
            f'(estado actual {devolucion.get_estado_display()}).'
        )
    es_admin = rol_efectivo(usuario) in ('administrador', 'administracion')  # Maestro incluido
    if devolucion.solicitado_por_id != usuario.id and not es_admin:
        raise DevolucionGarantiaError(
            'Solo el solicitante o un administrador pueden anular esta solicitud.'
        )

    devolucion.estado = 'ANULADA'
    devolucion.anulada_por = usuario
    devolucion.fecha_anulacion = timezone.now()
    devolucion.save(update_fields=['estado', 'anulada_por', 'fecha_anulacion', 'updated_at'])

    _registrar_historial_requerimiento(
        devolucion, 'DEVOLUCION_GARANTIA_ANULADA',
        f"Solicitud {devolucion.numero_operacion} anulada por {usuario.username}.",
        usuario, desvincular=True,
    )
    return devolucion


# Métodos entre los que se puede corregir una devolución ya aprobada: los que
# sacan plata de un medio (llevan un único pago en la NC). REBAJA_CREDITO y
# NO_AFECTA_CAJA cambian la naturaleza de la NC, no solo el medio.
METODOS_CORREGIBLES = ('EFECTIVO_CAJA', 'TRANSFERENCIA_BANCARIA', 'MERCADO_PAGO')


@transaction.atomic
def cambiar_metodo_devolucion(*, devolucion_id, usuario, metodo_nuevo,
                              numero_operacion_mp='', motivo=''):
    """
    Corrige el medio por el que se DEVOLVIÓ la plata de una devolución ya
    aprobada, cuando se registró uno y se pagó por otro (PAO1 28-09-2026:
    aprobada como transferencia, devuelta por Mercado Pago). Con el mismo
    método MERCADO_PAGO sirve para corregir solo el N° de operación.

    No toca la NC tributaria (folio, montos, líneas, TXT: el medio de pago no
    va al SII). Cambia el pago de la NC — que es lo que lee la cuadratura para
    saber de qué medio restar —, el método guardado en la devolución, y deja la
    corrección anotada en las observaciones. Conserva la fecha de imputación.
    Si el arqueo de ese día ya se cerró, sus teóricos quedan desactualizados:
    hay que recalcularlos (el comando `corregir_metodo_devolucion_dg` lo hace
    con --recalcular-arqueo).

    Devuelve un dict con el antes/después para informar.
    """
    # of=('self',): `nota_credito` es FK nullable (LEFT JOIN) y Postgres no
    # admite FOR UPDATE sobre el lado nullable de un outer join. Sqlite lo
    # ignora, por eso los tests no lo ven (mismo caso que `_validar_lineas`).
    devolucion = DevolucionGarantia.objects.select_for_update(of=('self',)).select_related(
        'dte_original', 'nota_credito', 'sucursal',
    ).get(id=devolucion_id)
    if devolucion.estado != 'NC_GENERADA' or not devolucion.nota_credito_id:
        raise DevolucionGarantiaError(
            'Solo se corrige el método de una devolución aprobada (con NC emitida).'
        )
    metodo_nuevo = str(metodo_nuevo or '').strip().upper()
    if metodo_nuevo not in METODOS_CORREGIBLES:
        raise DevolucionGarantiaError(
            'El método nuevo debe ser Efectivo, Transferencia o Mercado Pago.'
        )
    anterior = devolucion.metodo_devolucion
    if anterior == 'MERCADO_PAGO':
        refunds = [str(r.payment_id) for r in refunds_api_de_devolucion(devolucion) if r.payment_id]
        if refunds:
            raise DevolucionGarantiaError(
                f'La plata ya volvió a la tarjeta por la API de Mercado Pago '
                f'(refund {", ".join(refunds)}): no se puede cambiar el método ni el N°.'
            )
    if anterior not in METODOS_CORREGIBLES:
        raise DevolucionGarantiaError(
            f'La devolución quedó como «{devolucion.get_metodo_devolucion_display() or "sin método"}»: '
            f'solo se corrige entre efectivo, transferencia y Mercado Pago.'
        )
    solo_numero = anterior == metodo_nuevo == 'MERCADO_PAGO'
    if anterior == metodo_nuevo and not solo_numero:
        raise DevolucionGarantiaError('La devolución ya está registrada con ese método.')

    nc = devolucion.nota_credito
    pagos = list(Dte_Detalle_Pago.objects.select_for_update().filter(dte=nc))
    if len(pagos) != 1:
        raise DevolucionGarantiaError(
            f'La NC #{nc.numero_documento} tiene {len(pagos)} pagos registrados y se esperaba 1: '
            f'corríjala a mano en Consulta de Documentos.'
        )
    pago = pagos[0]
    monto = int(pago.monto or 0)
    dte = devolucion.dte_original
    antes = {'metodo_pago': pago.metodo_pago, 'tipo_tarjeta': pago.tipo_tarjeta or '',
             'voucher': pago.voucher or ''}

    if metodo_nuevo == 'MERCADO_PAGO':
        mp = pago_mercadopago_dte(dte)
        if not solo_numero:
            _validar_devolucion_mercadopago(dte, monto, mp)
        numero = _numero_operacion_mp(numero_operacion_mp, mp)
        for campo, valor in _datos_pago_nc_mercadopago(mp, numero, devolucion).items():
            setattr(pago, campo, valor)
    else:
        if metodo_nuevo == 'EFECTIVO_CAJA':
            _validar_metodo_vs_condicion_pago(dte, 'EFECTIVO_CAJA')
        numero = ''
        pago.metodo_pago = METODO_PAGO_NC_POR_DG[metodo_nuevo]
        pago.tipo_tarjeta = None
        pago.voucher = None
        pago.notas = None
    pago.save(update_fields=['metodo_pago', 'tipo_tarjeta', 'voucher', 'notas'])

    # Libro de cobros MP: la devolución entra o sale según el método.
    if anterior == 'MERCADO_PAGO' and metodo_nuevo != 'MERCADO_PAGO':
        _revertir_devolucion_en_libro_mp(nc)
    elif metodo_nuevo == 'MERCADO_PAGO' and not solo_numero:
        _registrar_devolucion_en_libro_mp(
            dte=dte, nc=nc, monto=monto, devolucion=devolucion, usuario=usuario,
        )

    etiquetas = dict(METODO_DEVOLUCION_DG_CHOICES)
    marca = (
        f"[CORREGIDO {timezone.localtime():%d-%m-%Y %H:%M} por {usuario.username}: "
        f"{etiquetas.get(anterior, anterior)} -> {etiquetas[metodo_nuevo]}"
        + (f", N° operación MP {numero}" if numero else '')
        + (f". {motivo.strip()}" if (motivo or '').strip() else '')
        + ']'
    )
    devolucion.observaciones_aprobacion = f'{devolucion.observaciones_aprobacion or ""} {marca}'.strip()
    devolucion.metodo_devolucion = metodo_nuevo
    # Lo que "pidió el cliente" también: el comprobante y el detalle lo muestran.
    devolucion.metodo_solicitado = metodo_nuevo
    devolucion.save(update_fields=[
        'observaciones_aprobacion', 'metodo_devolucion', 'metodo_solicitado', 'updated_at',
    ])
    logger.warning(
        "Devolución %s (NC #%s): método corregido %s -> %s por %s",
        devolucion.numero_operacion, nc.numero_documento, anterior, metodo_nuevo, usuario.username,
    )
    return {
        'devolucion': devolucion,
        'nc': nc,
        'monto': monto,
        'anterior': anterior,
        'nuevo': metodo_nuevo,
        'numero_operacion_mp': numero,
        'pago_antes': antes,
        'pago_despues': {'metodo_pago': pago.metodo_pago, 'tipo_tarjeta': pago.tipo_tarjeta or '',
                         'voucher': pago.voucher or ''},
        'fecha_imputacion': devolucion.fecha_imputacion_caja or pago.fecha_pago or nc.fecha_emision,
    }


def impacto_caja_preview(*, devolucion, metodo, fecha_imputacion=None):
    """
    Previsualiza (sin efectos) cómo impactará la NC en la cuadratura de caja
    según el método y la fecha de imputación. Advierte si el arqueo de esa
    (sucursal, fecha) ya no está ABIERTO (teóricos snapshoteados).
    """
    from app.models import ArqueoCaja

    monto = int(devolucion.monto_total or 0)
    sucursal = devolucion.sucursal
    afecta = metodo in METODOS_DG_CON_IMPUTACION
    tipo_tx = 'DEVOLUCION' if afecta else 'ANULACION'

    advertencias = []
    fecha_valida = True
    fecha_str = None
    arqueo_existe = False
    arqueo_estado = None
    arqueo_abierto = True

    # === CONDICIÓN DE PAGO DEL DOCUMENTO ORIGINAL ===
    # Si la venta fue a crédito, sacar plata de un medio de caja (efectivo o
    # transferencia) descuadra el arqueo: nunca entró por ahí.
    cond = condicion_pago_dte(devolucion.dte_original)
    bloqueado = False
    if cond['es_credito'] and metodo in ('EFECTIVO_CAJA', 'TRANSFERENCIA_BANCARIA'):
        detalle = '; '.join(cond['motivos'])
        if metodo == 'EFECTIVO_CAJA' and cond['cobro_abierto']:
            bloqueado = True
            advertencias.append(
                f'BLOQUEADO: el documento se vendió a crédito ({detalle}). Devolver '
                f'efectivo dejaría el arqueo con un faltante que nunca existió. '
                f'Use "Rebaja crédito del cliente".'
            )
        else:
            advertencias.append(
                f'El documento se vendió a crédito ({detalle}): el cliente no puso esta '
                f'plata en la caja. Salvo que el cobro ya se haya recibido por este medio, '
                f'corresponde "Rebaja crédito del cliente".'
            )

    # === COBRO POR MÁQUINA TRANSBANK ===
    # Un error de cobro con tarjeta se corrige anulando en la MISMA máquina
    # (el dinero vuelve a la tarjeta); esta NC encima sería pagar dos veces.
    tbk = pago_transbank_dte(devolucion.dte_original)
    if tbk['anulado_completo']:
        bloqueado = True
        ultima = tbk['anulaciones_pos'][-1]
        advertencias.append(
            f'BLOQUEADO: el cobro con tarjeta (${tbk["monto_tarjeta"]:,}) ya fue '
            f'ANULADO por la máquina Transbank (${tbk["monto_anulado_pos"]:,} el '
            f'{ultima["fecha"]}). El dinero vuelve a la tarjeta por esa vía: aprobar '
            f'esta devolución pagaría dos veces. Rechace la solicitud.'
        )
    elif tbk['es_transbank']:
        if tbk['monto_anulado_pos']:
            advertencias.append(
                f'Esta venta ya registra una anulación PARCIAL por la máquina Transbank '
                f'(${tbk["monto_anulado_pos"]:,} de ${tbk["monto_tarjeta"]:,} cobrados con '
                f'tarjeta). Verifique que la devolución no duplique ese monto.'
            )
        advertencias.append(
            f'El documento se cobró con TARJETA por la máquina Transbank '
            f'(${tbk["monto_tarjeta"]:,}). Si el motivo real es un ERROR DE COBRO, la '
            f'corrección es anular en la misma máquina (el dinero vuelve a la tarjeta), '
            f'no esta NC. Apruebe solo si se trata de una garantía.'
        )

    # === COBRO CON MERCADO PAGO ===
    # Solo se devuelve por MP lo que se cobró por MP (menos lo ya devuelto).
    mp = pago_mercadopago_dte(devolucion.dte_original)
    if metodo == 'MERCADO_PAGO':
        if not mp['es_mp']:
            bloqueado = True
            advertencias.append(
                'BLOQUEADO: la venta original no se cobró con Mercado Pago: no hay cobro que '
                'devolver por esa vía. Use efectivo o transferencia.'
            )
        elif monto > mp['disponible']:
            bloqueado = True
            advertencias.append(
                f'BLOQUEADO: con Mercado Pago se cobraron ${mp["monto_mp"]:,}'
                + (f' y ya se devolvieron ${mp["monto_devuelto"]:,}' if mp['monto_devuelto'] else '')
                + f': por esa vía se pueden devolver hasta ${mp["disponible"]:,}.'
            )

    if afecta:
        fecha = fecha_imputacion or timezone.localdate()
        fecha_str = fecha.strftime('%Y-%m-%d')
        if fecha > timezone.localdate():
            advertencias.append('La fecha de imputación no puede ser futura.')
            fecha_valida = False
        if devolucion.dte_original.fecha_emision and fecha < devolucion.dte_original.fecha_emision:
            advertencias.append('La fecha de imputación es anterior a la emisión del documento original.')
            fecha_valida = False

        arqueo = ArqueoCaja.objects.filter(sucursal=sucursal, fecha_arqueo=fecha).first()
        if arqueo:
            arqueo_existe = True
            arqueo_estado = arqueo.estado
            arqueo_abierto = (arqueo.estado == 'ABIERTO')
            if not arqueo_abierto:
                advertencias.append(
                    f"El arqueo del {fecha_str} en {sucursal.alias} está en estado "
                    f"'{arqueo.get_estado_display()}': la NC descuadrará los teóricos ya "
                    f"guardados. Sugerencia: imputar a hoy, o recalcular los teóricos del "
                    f"arqueo tras aprobar."
                )

        if metodo == 'EFECTIVO_CAJA':
            descripcion = (
                f"Esta NC restará ${monto:,} del efectivo teórico de "
                f"{sucursal.alias} el {fecha_str}."
            )
        elif metodo == 'TRANSFERENCIA_BANCARIA':
            descripcion = (
                f"Esta NC restará ${monto:,} de la transferencia teórica de "
                f"{sucursal.alias} el {fecha_str}."
            )
        elif metodo == 'MERCADO_PAGO':
            numero = mp['numero_operacion']
            descripcion = (
                f"Esta NC restará ${monto:,} de Mercado Pago POS"
                + (f" ({mp['medio']})" if mp['medio'] else '')
                + f" de {sucursal.alias} el {fecha_str}; no toca el efectivo ni las "
                f"transferencias. La plata se devuelve desde la app o el panel de Mercado "
                f"Pago" + (f" (cobro N° {numero})" if numero else '')
                + ": este sistema solo la registra."
            )
        else:  # REBAJA_CREDITO
            descripcion = (
                f"Esta NC rebajará ${monto:,} de la cuenta por cobrar del cliente. "
                f"Aparece en la cuadratura de {sucursal.alias} del {fecha_str} bajo "
                f"CRÉD. EXTERNO y baja el VENTA TOTAL del día; NO toca el efectivo "
                f"ni las transferencias teóricas."
            )
    else:
        descripcion = (
            "NC informativa: cuenta como documento del día pero NO resta de los "
            "teóricos de caja."
        )

    return {
        'monto': monto,
        'metodo': metodo,
        'tipo_transaccion_nc': tipo_tx,
        'afecta_caja': afecta,
        'fecha_imputacion': fecha_str,
        'sucursal': sucursal.alias,
        'arqueo_existe': arqueo_existe,
        'arqueo_estado': arqueo_estado,
        'arqueo_abierto': arqueo_abierto,
        'advertencias': advertencias,
        'descripcion': descripcion,
        'fecha_valida': fecha_valida,
        # `bloqueado` es distinto de `fecha_valida`: la combinación método +
        # condición de pago del documento es inválida, no la fecha. El front
        # deshabilita "Aprobar" con cualquiera de las dos.
        'bloqueado': bloqueado,
        'condicion_pago': cond,
        'pago_transbank': tbk,
        'pago_mercadopago': mp,
        'metodo_sugerido': metodo_devolucion_sugerido(devolucion.dte_original, mp),
    }


def _generar_txt_nc(nc, devolucion):
    """Genera y persiste el TXT Acepta de la NC por la vía canónica
    (construir_datos_txt_desde_dte). No fatal si falla.

    Devuelve (contenido_o_None, warnings). Los warnings vienen de
    `validar_dte_antes_de_guardar` (ej. falta giro/dirección del receptor):
    no bloquean, pero hay que mostrarlos porque el SII sí los exige y Acepta
    puede rechazar el TXT.
    """
    from app.views_modulo_documentos import (
        construir_datos_txt_desde_dte, generar_txt_dte_acepta,
        validar_dte_antes_de_guardar,
    )
    warnings = []
    try:
        datos = construir_datos_txt_desde_dte(nc)

        try:
            chequeo = validar_dte_antes_de_guardar(datos) or {}
            warnings = list(chequeo.get('errores') or []) + list(chequeo.get('warnings') or [])
        except Exception:
            logger.exception("Error validando datos del TXT NC #%s", nc.numero_documento)

        contenido = generar_txt_dte_acepta(datos)

        import os
        from django.conf import settings
        # MEDIA_ROOT absoluto (misma ruta que views.py): 'MEDIA' relativo al
        # CWD escribía en un directorio distinto según cómo corra el proceso.
        ruta_nc = os.path.join(settings.MEDIA_ROOT, 'documentos_electronicos', 'nc')
        os.makedirs(ruta_nc, exist_ok=True)
        nombre = f"NC_61_{nc.numero_documento}_{nc.fecha_emision.strftime('%Y%m%d')}.txt"
        with open(os.path.join(ruta_nc, nombre), 'w', encoding='utf-8') as f:
            f.write(contenido)
        return contenido, warnings
    except Exception as e:
        logger.exception(
            "Error generando TXT Acepta para NC #%s (DevolucionGarantia %s)",
            nc.numero_documento, devolucion.numero_operacion,
        )
        warnings.append(f'No se pudo generar el TXT Acepta: {e}')
        return None, warnings
