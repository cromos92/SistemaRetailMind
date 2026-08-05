"""
Servicio de cupones de descuento nominativos (function-based, estilo
`fidelizacion_service.py`).

Un cupón es un código que la empresa REGALA a un cliente concreto. A diferencia
de un vale de puntos, no está respaldado por saldo del cliente: es margen. Por
eso la validación es más estricta que la de un vale.

Reglas de negocio (decisión 2026-08-05):

1. **Nominativo.** Sólo lo usa su dueño: en caja se exige que el RUT de la venta
   sea el del cliente al que se emitió. Ese chequeo, por sí solo, ya excluye el
   cliente genérico, las empresas y las ventas sin RUT.
2. **Por empresa.** El cupón nace atado a la cadena que lo emite y sólo se canjea
   en sus sucursales.
3. **Requiere ficha de cliente.** Se emite desde la ficha del CRM, y al emitir se
   exige una ficha usable (RUT válido + un canal de contacto real).
4. **Un solo beneficio por venta.** El cupón NO se combina con el descuento
   manual del cajero ni con un vale de puntos. Es regla dura de este módulo, no
   un flag configurable, a propósito.

Garantías:
- Emisión y canje corren en `transaction.atomic()` con `select_for_update()`.
- Idempotencia por `idempotency_key` (emisión) y por ticket (canje): un reintento
  del POS no descuenta dos veces ni quema dos cupones.
- El snapshot de valores se congela al emitir: editar la campaña no altera los
  cupones ya entregados.
"""
import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from app.models import (
    CampanaCupon,
    CuponCliente,
    calcular_descuento_cupon,
    validar_rut_chileno,
)
from app.services.fidelizacion_service import (
    es_rut_empresa,
    normalizar_celular,
    normalizar_rut,
    validar_email,
    RUT_FICTICIOS,
)

logger = logging.getLogger('app')

# Alfabeto sin caracteres ambiguos (sin 0/O, 1/I/L): el cajero lo teclea a mano
# y el cliente a veces lo dicta por teléfono. Mismo criterio que los vales.
_CUPON_ALFABETO = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'

# Valores dummy que carga el botón "Cliente Genérico" del POS. Una ficha que los
# tenga no es una ficha: es el genérico con otro nombre.
_CONTACTOS_DUMMY = {'generico@test.cl', '999999999', '99999999'}


class CuponError(Exception):
    """Error de negocio de cupones (ficha incompleta, campaña vencida, etc.)."""


def _generar_codigo_cupon(longitud=8):
    """Código de cupón ininteligible y legible: 'DC-XXXXXXXX' (secrets, no random)."""
    cuerpo = ''.join(secrets.choice(_CUPON_ALFABETO) for _ in range(longitud))
    return f'DC-{cuerpo}'


# ========== FICHA DEL CLIENTE ==========

def ficha_completa(cliente):
    """
    ¿La ficha del cliente permite emitirle un cupón?

    Devuelve `(ok: bool, faltantes: list[str])`. Se valida al EMITIR, no al
    canjear: en caja lo que manda es que el RUT coincida. Acá el objetivo es no
    emitir cupones que después no se pueden ni entregar.
    """
    faltantes = []

    if not cliente or not getattr(cliente, 'activo', False):
        faltantes.append('cliente inactivo')

    if not (cliente.nombre or '').strip():
        faltantes.append('nombre')
    if not (cliente.apellido or '').strip():
        faltantes.append('apellido')

    rut = (cliente.rut or '').strip()
    rut_norm = normalizar_rut(rut)
    if not rut:
        faltantes.append('RUT')
    elif rut_norm in {normalizar_rut(r) for r in RUT_FICTICIOS}:
        faltantes.append('RUT genérico (consumidor final)')
    elif not validar_rut_chileno(rut):
        faltantes.append('RUT inválido')
    elif es_rut_empresa(rut):
        faltantes.append('RUT de empresa (el cupón es para clientes particulares)')

    # Al menos un canal de contacto REAL: si no, el cupón no se puede entregar.
    email = (cliente.email or '').strip().lower()
    celular = normalizar_celular(cliente.celular) or normalizar_celular(cliente.telefono)
    email_ok = validar_email(email) and email not in _CONTACTOS_DUMMY
    celular_ok = bool(celular) and celular not in _CONTACTOS_DUMMY
    if not (email_ok or celular_ok):
        faltantes.append('email o celular de contacto')

    return (not faltantes), faltantes


# ========== EMISIÓN ==========

def emitir_cupon(campana, cliente, *, usuario=None, idempotency_key=None):
    """
    Emite un cupón nominativo del cliente para esa campaña.

    Congela el snapshot de valores y genera un código de un solo uso. Lanza
    `CuponError` si la campaña no está vigente, la ficha no sirve, o el cliente
    ya tiene un cupón vivo de la misma campaña.
    """
    if campana is None or cliente is None:
        raise CuponError('Falta la campaña o el cliente.')
    if not campana.vigente:
        raise CuponError(
            f'La campaña "{campana.nombre}" no está vigente '
            f'({campana.fecha_inicio} → {campana.fecha_fin}).'
        )

    ok, faltantes = ficha_completa(cliente)
    if not ok:
        raise CuponError(
            'La ficha del cliente no permite emitir un cupón. '
            f'Falta o está mal: {", ".join(faltantes)}.'
        )

    with transaction.atomic():
        if idempotency_key:
            existente = CuponCliente.objects.filter(
                idempotency_key=idempotency_key).first()
            if existente:
                return existente

        # Expira lo vencido ANTES de mirar si hay uno vivo: si no, un cupón que
        # caducó hace un mes bloquearía la emisión de uno nuevo para siempre.
        _expirar_cupones_de_cliente(campana, cliente)

        if campana.uno_vivo_por_cliente:
            vivo = CuponCliente.objects.filter(
                campana=campana, cliente=cliente, estado='PENDIENTE',
            ).first()
            if vivo:
                raise CuponError(
                    f'El cliente ya tiene un cupón sin usar de esta campaña '
                    f'({vivo.codigo}, vence {timezone.localtime(vivo.expira_en):%d-%m-%Y}).'
                )

        # Código único: reintenta ante la rarísima colisión (unique en BD).
        codigo = _generar_codigo_cupon()
        for _ in range(5):
            if not CuponCliente.objects.filter(codigo=codigo).exists():
                break
            codigo = _generar_codigo_cupon()

        cupon = CuponCliente.objects.create(
            campana=campana,
            cliente=cliente,
            rut_cliente=normalizar_rut(cliente.rut),
            codigo=codigo,
            empresa=campana.empresa,
            tipo_valor=campana.tipo_valor,
            valor=campana.valor,
            tope_descuento=campana.tope_descuento,
            monto_minimo=campana.monto_minimo,
            estado='PENDIENTE',
            expira_en=timezone.now() + timedelta(days=campana.vigencia_dias),
            idempotency_key=idempotency_key,
            created_by=usuario,
        )

    logger.info(
        'Cupón emitido id=%s codigo=%s campana=%s cliente=%s empresa=%s',
        cupon.id, cupon.codigo, campana.id, cliente.id, campana.empresa_id,
    )
    return cupon


def emitir_lote(campana, clientes, *, usuario=None):
    """
    Emisión masiva. NUNCA falla en bloque: un cliente con ficha incompleta se
    omite con su motivo y el resto sigue.

    Devuelve `{'emitidos': [CuponCliente], 'omitidos': [(cliente, motivo)]}`.
    """
    emitidos, omitidos = [], []
    for cliente in clientes:
        try:
            emitidos.append(emitir_cupon(campana, cliente, usuario=usuario))
        except CuponError as e:
            omitidos.append((cliente, str(e)))
        except Exception:
            logger.exception('Error inesperado emitiendo cupón cliente=%s',
                             getattr(cliente, 'id', None))
            omitidos.append((cliente, 'Error inesperado (ver log).'))
    logger.info('Emisión masiva campana=%s emitidos=%s omitidos=%s',
                campana.id, len(emitidos), len(omitidos))
    return {'emitidos': emitidos, 'omitidos': omitidos}


# ========== VALIDACIÓN EN CAJA ==========

def validar_cupon(codigo, *, monto=0, rut_cliente=None, sucursal=None,
                  tiene_dcto_manual=False, tiene_vale_puntos=False):
    """
    Valida (SIN escribir nada) un cupón para una venta concreta.

    Lo usan el POS —para mostrarle al cajero el descuento antes de cobrar— y el
    propio cobro, que lo vuelve a llamar con los datos definitivos. Nunca lanza:
    devuelve un dict con `valido` y un `motivo` en lenguaje de mostrador.
    """
    codigo = (codigo or '').strip().upper()
    cupon = CuponCliente.objects.select_related('cliente', 'campana').filter(
        codigo=codigo).first()

    if cupon is None:
        return _rechazo('NO_EXISTE', 'Ese código de descuento no existe.')

    base = {
        'codigo': cupon.codigo,
        'cliente_nombre': cupon.cliente.nombre_completo,
        'campana': cupon.campana.nombre,
        'tipo_valor': cupon.tipo_valor,
        'valor': float(cupon.valor),
        'expira_en': cupon.expira_en.isoformat(),
    }

    # --- Exclusión con otros beneficios (regla dura) ---
    # Va primero porque es el rechazo más accionable para el cajero: no depende
    # del cupón, sino de algo que él acaba de hacer y puede deshacer.
    if tiene_dcto_manual:
        return _rechazo(
            'DCTO_MANUAL',
            'No se puede combinar con un descuento manual. '
            'Quite el descuento o el cupón.', **base)
    if tiene_vale_puntos:
        return _rechazo(
            'VALE_ACTIVO',
            'Esta venta ya tiene un vale de puntos. Es uno u otro: '
            'quite el vale para usar el cupón.', **base)

    # --- Estado del cupón ---
    if cupon.estado == 'CANJEADO':
        return _rechazo('CANJEADO', 'Este cupón ya fue utilizado.', **base)
    if cupon.estado == 'ANULADO':
        return _rechazo('ANULADO', 'Este cupón fue anulado.', **base)
    if cupon.estado == 'EXPIRADO' or cupon.expira_en <= timezone.now():
        return _rechazo('EXPIRADO', 'Este cupón está vencido.', **base)

    # --- Nominativo: el RUT de la venta debe ser el del dueño ---
    rut_venta = normalizar_rut(rut_cliente)
    if not rut_venta:
        return _rechazo(
            'SIN_CLIENTE',
            'Identifique al cliente con su RUT para usar este cupón.', **base)
    if rut_venta != cupon.rut_cliente:
        return _rechazo(
            'OTRO_CLIENTE',
            'Este cupón pertenece a otro cliente. Verifique el RUT de la venta.',
            **base)

    # --- Alcance por empresa ---
    if sucursal is not None:
        empresa_venta = getattr(sucursal, 'empresa_id', None)
        if empresa_venta and empresa_venta != cupon.empresa_id:
            return _rechazo(
                'OTRA_EMPRESA',
                'Este cupón es de otra cadena y no aplica en esta sucursal.',
                **base)

    # --- Condiciones económicas ---
    monto = int(monto or 0)
    if monto <= 0:
        return _rechazo('SIN_MONTO', 'La venta no tiene monto para descontar.', **base)
    if cupon.monto_minimo and monto < cupon.monto_minimo:
        falta = f"${cupon.monto_minimo:,}".replace(',', '.')
        return _rechazo(
            'MONTO_MINIMO',
            f'Este cupón requiere una compra mínima de {falta}.', **base)

    descuento = cupon.calcular_descuento(monto)
    if descuento <= 0:
        return _rechazo('SIN_DESCUENTO',
                        'El cupón no genera descuento sobre este monto.', **base)

    return {'valido': True, 'motivo': '', 'motivo_codigo': '',
            'descuento_pesos': descuento, **base}


def _rechazo(motivo_codigo, motivo, **extra):
    return {'valido': False, 'motivo_codigo': motivo_codigo, 'motivo': motivo,
            'descuento_pesos': 0, **extra}


# ========== CANJE ==========

def canjear_cupon(codigo, *, ticket, sucursal=None, usuario=None,
                  monto_descuento=None):
    """
    Marca el cupón como CANJEADO y lo deja atado al ticket que lo consumió.

    Se llama DESPUÉS de confirmar el pago (mismo patrón que `canjear_vale` y las
    gift cards). Idempotente: si el mismo ticket ya lo canjeó, devuelve el cupón
    sin volver a escribir. Lanza `CuponError` si el código no existe, ya se usó
    en otra venta, o expiró.
    """
    codigo = (codigo or '').strip().upper()
    if not codigo:
        raise CuponError('Falta el código del cupón.')

    # Igual que en `canjear_vale`: el error se acumula y se lanza FUERA del
    # bloque atómico, para que el rollback no descarte el marcado EXPIRADO.
    resultado = None
    error = None
    with transaction.atomic():
        cupon = CuponCliente.objects.select_for_update().filter(codigo=codigo).first()
        if cupon is None:
            error = 'El código no existe.'
        elif cupon.estado == 'CANJEADO':
            if ticket is not None and cupon.ticket_id == ticket.id:
                resultado = cupon           # idempotente: mismo ticket, no reescribe
            else:
                error = 'Este cupón ya fue utilizado en otra venta.'
        elif cupon.estado == 'ANULADO':
            error = 'El cupón fue anulado.'
        elif cupon.estado == 'EXPIRADO' or cupon.expira_en <= timezone.now():
            if cupon.estado == 'PENDIENTE':
                cupon.estado = 'EXPIRADO'
                cupon.save(update_fields=['estado', 'updated_at'])
            error = 'El cupón expiró.'
        else:
            monto = int(monto_descuento or 0)
            if monto <= 0:
                error = 'El descuento del cupón debe ser mayor a 0.'
            else:
                cupon.estado = 'CANJEADO'
                cupon.ticket = ticket
                cupon.monto_descuento = monto
                cupon.sucursal_canje = sucursal
                cupon.usuario_canje = usuario
                cupon.canjeado_en = timezone.now()
                cupon.save(update_fields=[
                    'estado', 'ticket', 'monto_descuento', 'sucursal_canje',
                    'usuario_canje', 'canjeado_en', 'updated_at',
                ])
                logger.info(
                    'Cupón canjeado codigo=%s ticket=%s descuento=%s sucursal=%s usuario=%s',
                    cupon.codigo, getattr(ticket, 'correlativo', None), monto,
                    getattr(sucursal, 'id', None), getattr(usuario, 'id', None),
                )
                resultado = cupon

    if error:
        raise CuponError(error)
    return resultado


def liberar_cupon(ticket, *, motivo=''):
    """
    Devuelve al cliente los cupones que consumió un ticket que se anuló o se
    devolvió. Un cupón vencido en el intertanto queda EXPIRADO (no se resucita).

    Devuelve cuántos cupones volvieron a estar disponibles. Best-effort: nunca
    lanza, porque corre dentro del flujo de anulación y no debe tumbarlo.
    """
    if ticket is None:
        return 0
    liberados = 0
    for cupon in CuponCliente.objects.filter(ticket=ticket, estado='CANJEADO'):
        try:
            with transaction.atomic():
                c = CuponCliente.objects.select_for_update().get(pk=cupon.pk)
                if c.estado != 'CANJEADO':
                    continue
                vencido = c.expira_en <= timezone.now()
                c.estado = 'EXPIRADO' if vencido else 'PENDIENTE'
                c.ticket = None
                c.monto_descuento = 0
                c.canjeado_en = None
                c.sucursal_canje = None
                c.usuario_canje = None
                c.save(update_fields=[
                    'estado', 'ticket', 'monto_descuento', 'canjeado_en',
                    'sucursal_canje', 'usuario_canje', 'updated_at',
                ])
                if not vencido:
                    liberados += 1
                logger.info(
                    'Cupón liberado codigo=%s ticket=%s estado=%s motivo=%s',
                    c.codigo, getattr(ticket, 'correlativo', None), c.estado, motivo,
                )
        except Exception:
            logger.exception('No se pudo liberar el cupón %s', cupon.codigo)
    return liberados


# ========== EXPIRACIÓN ==========

def _expirar_cupones_de_cliente(campana, cliente):
    """
    Expira (lazy) los cupones PENDIENTE vencidos de un cliente en una campaña.
    Sin esto, un cupón caducado bloquearía para siempre la emisión de uno nuevo
    por la regla "uno vivo por cliente". Best-effort: nunca lanza.
    """
    try:
        CuponCliente.objects.filter(
            campana=campana, cliente=cliente, estado='PENDIENTE',
            expira_en__lte=timezone.now(),
        ).update(estado='EXPIRADO', updated_at=timezone.now())
    except Exception:
        logger.exception('Expiración lazy de cupones falló campana=%s cliente=%s',
                         getattr(campana, 'id', None), getattr(cliente, 'id', None))


def expirar_vencidos():
    """
    Pasa a EXPIRADO todos los cupones PENDIENTE ya vencidos. Para el command
    programado. Devuelve cuántos expiró. Sin esto la tasa de redención miente:
    los no usados quedarían contados como "todavía pueden usarse".
    """
    n = CuponCliente.objects.filter(
        estado='PENDIENTE', expira_en__lte=timezone.now(),
    ).update(estado='EXPIRADO', updated_at=timezone.now())
    if n:
        logger.info('Cupones expirados: %s', n)
    return n
