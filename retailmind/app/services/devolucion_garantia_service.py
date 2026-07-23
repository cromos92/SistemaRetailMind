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

Este módulo NUNCA mueve stock: es devolución de DINERO por garantía; el
producto fallado se gestiona aparte (vía Requerimiento al proveedor). El
modo MONTO reusa el patrón "corrige montos" de `anular_factura_dte`
(línea conceptual sin talla, razón SII 3).
"""
import json
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Dte_Detalle_Pago, Empresa,
    DevolucionGarantia, DevolucionGarantiaDetalle,
    METODO_DEVOLUCION_DG_CHOICES,
)

logger = logging.getLogger('app')


class DevolucionGarantiaError(Exception):
    """Error de negocio (DTE no encontrado, producto ya devuelto, RUT inválido, etc.)."""


TIPOS_DTE_VENTA_VALIDOS = ['BOLETA ELECTRONICA', 'FACTURA ELECTRONICA', 'BOLETA PAPEL']

# Estados de DevolucionGarantia que "reservan" disponibilidad (aún vivos).
ESTADOS_RESERVA = ['PENDIENTE', 'REGISTRADA']
ESTADOS_CONSUMO = ['PENDIENTE', 'REGISTRADA', 'NC_GENERADA']


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
    monto_original = int(dte_original.monto_con_iva or 0)
    total_nc_previas = int(Dte.objects.filter(
        documento_afectado=dte_original,
        es_nota_credito=True,
        estado_dte__in=['EMITIDO', 'ACEPTADO'],
    ).aggregate(t=Sum('monto_con_iva'))['t'] or 0)

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
        giro=giro or '',
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


@transaction.atomic
def crear_solicitud_devolucion(*, dte_original, sucursal, receptor, motivo,
                               usuario, detalles, requerimiento=None):
    """
    Registra una SOLICITUD de devolución en estado PENDIENTE. NO consume folio
    de NC, NO genera documento ni TXT: eso ocurre al aprobar.

    `detalles`: lista de dicts {dte_producto_id, modo, cantidad?, monto?}.
    `requerimiento`: instancia opcional de Requerimiento (puente de UI).

    Devuelve la DevolucionGarantia creada.
    """
    lineas, monto_total = _validar_lineas(dte_original, detalles, lock=True)

    saldo = saldo_documento(dte_original)
    if monto_total > saldo['monto_restante']:
        raise DevolucionGarantiaError(
            f'El monto total solicitado (${monto_total:,}) excede el saldo disponible '
            f'de NC sobre este documento (${saldo["monto_restante"]:,}).'
        )

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
                       fecha_imputacion=None, observaciones=''):
    """
    Aprueba una solicitud PENDIENTE: genera la NC 61 + TXT Acepta con el
    impacto en caja elegido por el aprobador. Re-valida disponibilidad bajo
    lock; si cambió, lanza DevolucionGarantiaError y la solicitud queda
    PENDIENTE (el aprobador decide rechazar o esperar).

    Devuelve (devolucion, nc, contenido_txt_o_None).
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
    afecta_caja = metodo_devolucion in ('EFECTIVO_CAJA', 'TRANSFERENCIA_BANCARIA')

    dte_original = devolucion.dte_original
    sucursal = devolucion.sucursal

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

    # Razón SII dinámica: '1' solo si la NC cubre el saldo completo y no hay NC
    # previas; si no '3' (corrige montos / NC parcial). Nunca anula el original.
    razon = '1' if (monto_con_iva_nc == saldo['monto_restante'] and total_nc_previas == 0) else '3'

    numero_nc = obtener_siguiente_correlativo(sucursal, 'NOTA DE CREDITO')
    tipo_sii_original = 33 if 'FACTURA' in (dte_original.tipo_documento or '') else 39
    referencias_json = json.dumps([{
        'tipo_documento': tipo_sii_original,
        'folio': str(dte_original.numero_documento),
        'fecha': dte_original.fecha_emision.strftime('%Y-%m-%d'),
        'razon': razon,
    }])

    motivo_nc = (
        f"Devolución por Garantía {devolucion.numero_operacion}. "
        f"Motivo: {devolucion.motivo or 'Garantía aprobada'}"
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

    for linea in lineas:
        dp = linea['dte_producto']
        if linea['modo'] == 'CANTIDAD':
            cantidad = linea['cantidad']
            # Precio efectivo en la base nativa del documento (patrón
            # _precio_efectivo_dp de anular_factura_dte): boleta con IVA,
            # factura neto. No usar el monto_con_iva del detalle DG.
            mi_eff = monto_real_linea_dte(dp, cantidad)
            p_eff = int(round(mi_eff / cantidad)) if cantidad and mi_eff else int(dp.precio or 0)
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
            Dte_Productos.objects.create(
                dte=nc,
                productoTalla=None,
                descripcion=desc[:255],
                costo=0,
                sobreprecio=0,
                precio=int(linea['monto_con_iva']),
                stock=1,
                activo=True,
            )

    # `fecha_pago` = día al que la cuadratura imputa el egreso de caja.
    if metodo_devolucion == 'EFECTIVO_CAJA':
        Dte_Detalle_Pago.objects.create(
            dte=nc, metodo_pago='EFECTIVO', monto=monto_con_iva_nc, fecha_pago=fecha_imp,
        )
    elif metodo_devolucion == 'TRANSFERENCIA_BANCARIA':
        Dte_Detalle_Pago.objects.create(
            dte=nc, metodo_pago='TRANSFERENCIA', monto=monto_con_iva_nc, fecha_pago=fecha_imp,
        )
    # NO_AFECTA_CAJA: sin Dte_Detalle_Pago (NC informativa que no resta teóricos).

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

    contenido_txt = _generar_txt_nc(nc, devolucion)
    return devolucion, nc, contenido_txt


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
    es_admin = getattr(usuario, 'rol', '') in ('administrador', 'administracion')
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


def impacto_caja_preview(*, devolucion, metodo, fecha_imputacion=None):
    """
    Previsualiza (sin efectos) cómo impactará la NC en la cuadratura de caja
    según el método y la fecha de imputación. Advierte si el arqueo de esa
    (sucursal, fecha) ya no está ABIERTO (teóricos snapshoteados).
    """
    from app.models import ArqueoCaja

    monto = int(devolucion.monto_total or 0)
    sucursal = devolucion.sucursal
    afecta = metodo in ('EFECTIVO_CAJA', 'TRANSFERENCIA_BANCARIA')
    tipo_tx = 'DEVOLUCION' if afecta else 'ANULACION'

    advertencias = []
    fecha_valida = True
    fecha_str = None
    arqueo_existe = False
    arqueo_estado = None
    arqueo_abierto = True

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

        medio = 'efectivo' if metodo == 'EFECTIVO_CAJA' else 'la transferencia'
        descripcion = (
            f"Esta NC restará ${monto:,} de {medio} teórico de "
            f"{sucursal.alias} el {fecha_str}."
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
    }


def _generar_txt_nc(nc, devolucion):
    """Genera y persiste el TXT Acepta de la NC por la vía canónica
    (construir_datos_txt_desde_dte). No fatal si falla."""
    from app.views_modulo_documentos import construir_datos_txt_desde_dte, generar_txt_dte_acepta
    try:
        datos = construir_datos_txt_desde_dte(nc)
        contenido = generar_txt_dte_acepta(datos)

        import os
        ruta_nc = os.path.join('MEDIA', 'documentos_electronicos', 'nc')
        os.makedirs(ruta_nc, exist_ok=True)
        nombre = f"NC_61_{nc.numero_documento}_{nc.fecha_emision.strftime('%Y%m%d')}.txt"
        with open(os.path.join(ruta_nc, nombre), 'w', encoding='utf-8') as f:
            f.write(contenido)
        return contenido
    except Exception:
        logger.exception(
            "Error generando TXT Acepta para NC #%s (DevolucionGarantia %s)",
            nc.numero_documento, devolucion.numero_operacion,
        )
        return None
