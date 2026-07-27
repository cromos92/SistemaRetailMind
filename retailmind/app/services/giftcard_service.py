"""
Servicio de Gift Cards (function-based, estilo `pos_service.py`).

Encapsula TODA la lógica de saldos para que el hook de cobro, las vistas de
gestión, la API desktop y los flujos de reversa compartan una sola fuente de
verdad.

Garantías:
- Cada operación que mueve saldo abre su propia `transaction.atomic()` y bloquea
  la giftcard con `select_for_update()` (anti-carrera entre cobros simultáneos).
- Idempotencia: los movimientos llevan `idempotency_key` única; reintentos del
  POS no vuelven a descontar/cargar.

NO se confía en un rollback del request porque `registrar_pagos_ticket` no es
atómico de forma global.
"""
import logging

from django.db import transaction, IntegrityError
from django.db.models import Q
from django.utils import timezone

from app.models import GiftCard, MovimientoGiftCard

logger = logging.getLogger('app')


class GiftCardError(Exception):
    """Error de negocio de gift cards (saldo insuficiente, vencida, etc.)."""


def _normalizar_codigo(codigo):
    return (codigo or '').strip().upper()


def _validar_motivo(motivo):
    from app.models import MOTIVO_GIFTCARD_CHOICES
    validos = {c[0] for c in MOTIVO_GIFTCARD_CHOICES}
    if motivo not in validos:
        raise GiftCardError('Motivo de gift card inválido.')


def _resolver_giftcard(codigo, *, lock=False):
    """
    Busca una gift card por su código de sistema (GC-XXXX...) o por el código
    impreso de la tarjeta física. Devuelve la GiftCard o None.

    `lock=True` aplica select_for_update (usar solo dentro de una transacción).
    """
    codigo = _normalizar_codigo(codigo)
    if not codigo:
        return None
    qs = GiftCard.objects.select_for_update() if lock else GiftCard.objects
    return qs.filter(Q(codigo=codigo) | Q(codigo_fisico=codigo)).first()


def _registrar_movimiento(giftcard, tipo, monto, *, ticket=None, pago_ticket=None,
                          sucursal=None, usuario=None, idempotency_key=None,
                          observaciones=''):
    """
    Crea un MovimientoGiftCard y actualiza el saldo denormalizado de la
    giftcard. Debe llamarse DENTRO de una transacción con la giftcard bloqueada.
    """
    nuevo_saldo = giftcard.saldo_actual + monto
    mov = MovimientoGiftCard.objects.create(
        giftcard=giftcard,
        tipo=tipo,
        monto=monto,
        saldo_resultante=nuevo_saldo,
        ticket=ticket,
        pago_ticket=pago_ticket,
        sucursal=sucursal,
        usuario=usuario,
        idempotency_key=idempotency_key,
        observaciones=observaciones,
    )
    giftcard.saldo_actual = nuevo_saldo
    # Recalcular estado por saldo (sin pisar estados terminales ANULADA/BLOQUEADA)
    if giftcard.estado in ('ACTIVA', 'AGOTADA'):
        giftcard.estado = 'AGOTADA' if nuevo_saldo <= 0 else 'ACTIVA'
    giftcard.save(update_fields=['saldo_actual', 'estado', 'updated_at'])
    return mov


def emitir(monto, *, sucursal=None, cliente=None, vendedor=None,
           ticket=None, vencimiento=None, pin=None, usuario=None,
           observaciones='', tipo_tarjeta='DIGITAL', codigo_fisico=None,
           motivo='OTRO', descripcion=''):
    """
    Emite una nueva gift card con `monto` de saldo inicial.
    Devuelve la GiftCard creada. El código de sistema se genera de forma segura
    y única.

    - tipo_tarjeta='DIGITAL': vinculada a un cliente (se recomienda pasar cliente).
    - tipo_tarjeta='FISICA':  requiere `codigo_fisico` (el impreso en la tarjeta);
      el cliente es opcional y puede vincularse al canjear.
    - motivo: propósito de la tarjeta (ver MOTIVO_GIFTCARD_CHOICES).
    - descripcion: etiqueta/nombre libre para el seguimiento.
    """
    monto = int(monto)
    if monto <= 0:
        raise GiftCardError('El monto de la gift card debe ser mayor a 0.')

    tipo_tarjeta = (tipo_tarjeta or 'DIGITAL').upper()
    if tipo_tarjeta not in ('DIGITAL', 'FISICA'):
        raise GiftCardError('Tipo de tarjeta inválido.')

    motivo = (motivo or 'OTRO').upper()
    _validar_motivo(motivo)

    codigo_fisico = _normalizar_codigo(codigo_fisico) or None
    if tipo_tarjeta == 'FISICA':
        if not codigo_fisico:
            raise GiftCardError('Ingresa el código impreso de la tarjeta física.')
        if GiftCard.objects.filter(codigo_fisico=codigo_fisico).exists():
            raise GiftCardError('Ya existe una gift card con ese código físico.')
        # El código físico no debe chocar con un código de sistema existente.
        if GiftCard.objects.filter(codigo=codigo_fisico).exists():
            raise GiftCardError('Ese código físico ya está en uso.')
    else:
        codigo_fisico = None  # las digitales no llevan código impreso

    if vencimiento is None:
        from app.models import GIFTCARD_VIGENCIA_MESES_DEFAULT
        # Vigencia por defecto: hoy + N meses (aprox. 30 días/mes).
        vencimiento = timezone.localdate() + timezone.timedelta(
            days=30 * GIFTCARD_VIGENCIA_MESES_DEFAULT
        )

    with transaction.atomic():
        giftcard = GiftCard(
            saldo_inicial=monto,
            saldo_actual=0,  # se sube con el movimiento EMISION para dejar ledger
            estado='ACTIVA',
            tipo_tarjeta=tipo_tarjeta,
            codigo_fisico=codigo_fisico,
            fecha_vencimiento=vencimiento,
            sucursal_emision=sucursal,
            cliente=cliente,
            vendedor=vendedor,
            ticket_emision=ticket,
            pin=pin,
            motivo=motivo,
            descripcion=(descripcion or '').strip() or None,
            observaciones=observaciones,
            created_by=usuario,
            updated_by=usuario,
        )
        giftcard.save()  # genera código único (con reintento)
        _registrar_movimiento(
            giftcard, 'EMISION', monto,
            ticket=ticket, sucursal=sucursal, usuario=usuario,
            idempotency_key=f"emision:{giftcard.id}",
            observaciones='Emisión de gift card',
        )
    logger.info("GiftCard emitida codigo=%s monto=%s", giftcard.codigo, monto)
    return giftcard


def consultar_saldo(codigo):
    """
    Devuelve un dict con el estado y saldo de la gift card (sin descontar).
    Lanza GiftCardError si no existe.
    """
    gc = _resolver_giftcard(codigo)
    if gc is None:
        raise GiftCardError('Gift card no encontrada.')
    return {
        'codigo': gc.codigo,
        'codigo_fisico': gc.codigo_fisico,
        'tipo_tarjeta': gc.tipo_tarjeta,
        'motivo': gc.motivo,
        'motivo_display': gc.get_motivo_display(),
        'descripcion': gc.descripcion or '',
        'estado': gc.estado,
        'estado_display': gc.get_estado_display(),
        'saldo_actual': gc.saldo_actual,
        'fecha_vencimiento': gc.fecha_vencimiento.isoformat() if gc.fecha_vencimiento else None,
        'vencida': gc.esta_vencida,
        'valida': gc.estado == 'ACTIVA' and not gc.esta_vencida and gc.saldo_actual > 0,
    }


def validar(codigo, monto, *, pin=None):
    """
    Pre-validación SIN descontar (usada antes de cobrar).
    Devuelve dict con `valida`, `saldo_suficiente`, `saldo_actual` y `motivo`.
    Nunca lanza por saldo/estado; solo informa.
    """
    monto = int(monto or 0)
    gc = _resolver_giftcard(codigo)
    if not gc:
        return {'valida': False, 'motivo': 'No encontrada', 'saldo_actual': 0,
                'saldo_suficiente': False}
    if gc.estado != 'ACTIVA':
        return {'valida': False, 'motivo': f'Estado {gc.get_estado_display()}',
                'saldo_actual': gc.saldo_actual, 'saldo_suficiente': False}
    if gc.esta_vencida:
        return {'valida': False, 'motivo': 'Vencida', 'saldo_actual': gc.saldo_actual,
                'saldo_suficiente': False}
    if gc.pin and pin is not None and str(pin) != str(gc.pin):
        return {'valida': False, 'motivo': 'PIN incorrecto', 'saldo_actual': gc.saldo_actual,
                'saldo_suficiente': False}
    suficiente = gc.saldo_actual >= monto
    return {
        'valida': suficiente,
        'motivo': '' if suficiente else 'Saldo insuficiente',
        'saldo_actual': gc.saldo_actual,
        'saldo_suficiente': suficiente,
    }


def consumir(codigo, monto, *, ticket=None, pago_ticket=None, sucursal=None,
             usuario=None, pin=None, idempotency_key=None, cliente=None):
    """
    Descuenta `monto` de la gift card (pago de una venta).

    Bloquea la giftcard con select_for_update. Idempotente: si ya existe un
    movimiento con la misma `idempotency_key`, devuelve sin volver a descontar.

    Acepta código de sistema o código físico impreso. Si la tarjeta no tiene
    titular y se entrega `cliente`, lo vincula en este primer canje (caso de la
    tarjeta física que se activó sin RUT).

    Lanza GiftCardError si la giftcard no es válida o no tiene saldo.
    """
    monto = int(monto)
    if monto <= 0:
        raise GiftCardError('El monto a consumir debe ser mayor a 0.')

    # idempotencia preferente: por pago concreto
    if not idempotency_key and pago_ticket is not None:
        idempotency_key = f"consumo:{pago_ticket.id}"
    # Respaldo por ticket+tarjeta cuando el llamador no pasa ni clave ni pago.
    # Sin esto, dos llamadas seguidas descuentan dos veces (verificado: $10.000
    # con dos consumos de $1.000 quedaba en $8.000 por separado, no idempotente).
    # Es el mismo criterio que ya usa `reversar()` más abajo.
    if not idempotency_key and ticket is not None:
        idempotency_key = f"consumo_gc:{ticket.id}:{_normalizar_codigo(codigo)}"

    with transaction.atomic():
        # Si ya se procesó este consumo, no repetir.
        if idempotency_key:
            existente = MovimientoGiftCard.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existente:
                logger.info("Consumo giftcard idempotente codigo=%s key=%s (ya aplicado)",
                            codigo, idempotency_key)
                return existente

        gc = _resolver_giftcard(codigo, lock=True)
        if gc is None:
            raise GiftCardError('Gift card no encontrada.')

        if gc.estado != 'ACTIVA':
            raise GiftCardError(f'Gift card no disponible (estado: {gc.get_estado_display()}).')
        if gc.esta_vencida:
            raise GiftCardError('Gift card vencida.')
        if gc.pin and pin is not None and str(pin) != str(gc.pin):
            raise GiftCardError('PIN incorrecto.')
        if gc.saldo_actual < monto:
            raise GiftCardError(
                f'Saldo insuficiente (disponible ${gc.saldo_actual:,}, requerido ${monto:,}).'
            )

        # Vincular titular en el primer canje si la tarjeta circulaba sin RUT.
        if cliente is not None and gc.cliente_id is None:
            gc.cliente = cliente
            gc.save(update_fields=['cliente', 'updated_at'])

        try:
            # El INSERT va en un atomic() ANIDADO (savepoint) a propósito: si
            # choca contra el índice único de idempotency_key, Postgres aborta
            # la transacción y Django marca needs_rollback, de modo que el
            # `filter()` del except reventaba con TransactionManagementError en
            # vez de recuperar el movimiento. Con el savepoint, el error queda
            # contenido y la transacción exterior (que tiene el lock de la
            # tarjeta) sigue utilizable.
            with transaction.atomic():
                mov = _registrar_movimiento(
                    gc, 'CONSUMO', -monto,
                    ticket=ticket, pago_ticket=pago_ticket, sucursal=sucursal,
                    usuario=usuario, idempotency_key=idempotency_key,
                    observaciones=f'Pago de venta (ticket {getattr(ticket, "correlativo", "")})',
                )
        except IntegrityError:
            # Carrera: otro proceso insertó el mismo idempotency_key. Recuperar.
            # Sin clave no se puede identificar el movimiento del otro proceso
            # (filtrar por NULL devolvería cualquier fila sin clave), así que
            # el error se propaga tal cual.
            if not idempotency_key:
                raise
            mov = MovimientoGiftCard.objects.filter(idempotency_key=idempotency_key).first()
            if not mov:
                raise
            # El savepoint revirtió el saldo que _registrar_movimiento pudo
            # haber dejado en memoria: releer para no devolver datos rancios.
            gc.refresh_from_db()
            logger.info("Consumo giftcard resuelto por carrera codigo=%s key=%s",
                        codigo, idempotency_key)
    logger.info("GiftCard consumida codigo=%s monto=%s saldo=%s", gc.codigo, monto, gc.saldo_actual)
    return mov


def recargar(codigo, monto, *, sucursal=None, usuario=None, observaciones=''):
    """Agrega saldo a una gift card existente."""
    monto = int(monto)
    if monto <= 0:
        raise GiftCardError('El monto a recargar debe ser mayor a 0.')
    with transaction.atomic():
        gc = _resolver_giftcard(codigo, lock=True)
        if gc is None:
            raise GiftCardError('Gift card no encontrada.')
        if gc.estado in ('ANULADA', 'BLOQUEADA', 'VENCIDA'):
            raise GiftCardError(f'No se puede recargar (estado: {gc.get_estado_display()}).')
        # `consumir()` rechaza las tarjetas vencidas, así que recargar una cuya
        # fecha ya pasó inyecta plata que nadie puede canjear (pasivo muerto).
        # Se mira `esta_vencida` además del estado porque VENCIDA solo se
        # persiste cuando corre `marcar_vencidas()`: una tarjeta puede estar
        # ACTIVA en la columna y vencida en la fecha.
        # La vista api_recargar_giftcard ya lo bloquea con un mensaje propio;
        # esto es el respaldo para cualquier otro llamador (API desktop, etc.).
        if gc.esta_vencida:
            raise GiftCardError(
                f'No se puede recargar: la gift card venció el {gc.fecha_vencimiento}. '
                'Extiende primero el vencimiento.'
            )
        mov = _registrar_movimiento(
            gc, 'CARGA', monto, sucursal=sucursal, usuario=usuario,
            observaciones=observaciones or 'Recarga manual',
        )
    return mov


def anular(codigo, *, usuario=None, observaciones=''):
    """
    Anula la gift card: lleva el saldo a 0 (movimiento ANULACION) y marca el
    estado ANULADA. Irreversible.
    """
    with transaction.atomic():
        gc = _resolver_giftcard(codigo, lock=True)
        if gc is None:
            raise GiftCardError('Gift card no encontrada.')
        if gc.estado == 'ANULADA':
            return gc
        if gc.saldo_actual > 0:
            MovimientoGiftCard.objects.create(
                giftcard=gc,
                tipo='ANULACION',
                monto=-gc.saldo_actual,
                saldo_resultante=0,
                usuario=usuario,
                observaciones=observaciones or 'Anulación de gift card',
            )
        gc.saldo_actual = 0
        gc.estado = 'ANULADA'
        gc.updated_by = usuario
        gc.save(update_fields=['saldo_actual', 'estado', 'updated_by', 'updated_at'])
    logger.info("GiftCard anulada codigo=%s", gc.codigo)
    return gc


def reversar(codigo, monto, *, ticket=None, usuario=None, idempotency_key=None,
             observaciones=''):
    """
    Recarga la gift card por una devolución/anulación de venta que la consumió.
    Idempotente por `idempotency_key`.
    """
    codigo = _normalizar_codigo(codigo)
    monto = int(monto)
    if monto <= 0:
        return None
    if not idempotency_key and ticket is not None:
        idempotency_key = f"reversa_gc:{ticket.id}:{codigo}"
    with transaction.atomic():
        if idempotency_key:
            existente = MovimientoGiftCard.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existente:
                return existente
        gc = _resolver_giftcard(codigo, lock=True)
        if gc is None:
            raise GiftCardError('Gift card no encontrada.')
        # Reactivar si estaba AGOTADA; respetar ANULADA/BLOQUEADA.
        if gc.estado == 'AGOTADA':
            gc.estado = 'ACTIVA'
        mov = _registrar_movimiento(
            gc, 'REVERSA', monto, ticket=ticket, usuario=usuario,
            idempotency_key=idempotency_key,
            observaciones=observaciones or 'Reversa por devolución/anulación de venta',
        )
    return mov


def bloquear(codigo, *, usuario=None, observaciones=''):
    """
    Bloquea temporalmente la gift card (estado BLOQUEADA). Reversible con
    `desbloquear`. NO toca el saldo: deja una fila de auditoría monto=0 en el
    ledger. Solo aplica desde ACTIVA/AGOTADA.
    """
    with transaction.atomic():
        gc = _resolver_giftcard(codigo, lock=True)
        if gc is None:
            raise GiftCardError('Gift card no encontrada.')
        if gc.estado == 'BLOQUEADA':
            return gc
        if gc.estado not in ('ACTIVA', 'AGOTADA'):
            raise GiftCardError(
                f'No se puede bloquear (estado: {gc.get_estado_display()}).'
            )
        MovimientoGiftCard.objects.create(
            giftcard=gc,
            tipo='BLOQUEO',
            monto=0,
            saldo_resultante=gc.saldo_actual,
            usuario=usuario,
            observaciones=observaciones or 'Bloqueo de gift card',
        )
        gc.estado = 'BLOQUEADA'
        gc.updated_by = usuario
        gc.save(update_fields=['estado', 'updated_by', 'updated_at'])
    logger.info("GiftCard bloqueada codigo=%s", gc.codigo)
    return gc


def desbloquear(codigo, *, usuario=None, observaciones=''):
    """
    Levanta el bloqueo de una gift card. Recalcula el estado por saldo
    (ACTIVA/AGOTADA) o VENCIDA si ya pasó su fecha. Solo aplica desde BLOQUEADA.
    """
    with transaction.atomic():
        gc = _resolver_giftcard(codigo, lock=True)
        if gc is None:
            raise GiftCardError('Gift card no encontrada.')
        if gc.estado != 'BLOQUEADA':
            raise GiftCardError('La gift card no está bloqueada.')
        if gc.esta_vencida:
            nuevo_estado = 'VENCIDA'
        else:
            nuevo_estado = 'AGOTADA' if gc.saldo_actual <= 0 else 'ACTIVA'
        MovimientoGiftCard.objects.create(
            giftcard=gc,
            tipo='DESBLOQUEO',
            monto=0,
            saldo_resultante=gc.saldo_actual,
            usuario=usuario,
            observaciones=observaciones or 'Desbloqueo de gift card',
        )
        gc.estado = nuevo_estado
        gc.updated_by = usuario
        gc.save(update_fields=['estado', 'updated_by', 'updated_at'])
    logger.info("GiftCard desbloqueada codigo=%s estado=%s", gc.codigo, gc.estado)
    return gc


def editar(codigo, *, descripcion=None, motivo=None, observaciones=None, usuario=None):
    """
    Edita la metadata de una gift card ya emitida (descripción, motivo,
    observaciones). No mueve saldo. Deja una fila de auditoría monto=0 (AJUSTE)
    con el detalle del cambio para que aparezca en la trazabilidad.
    """
    with transaction.atomic():
        gc = _resolver_giftcard(codigo, lock=True)
        if gc is None:
            raise GiftCardError('Gift card no encontrada.')

        cambios = []
        campos = ['updated_by', 'updated_at']
        if descripcion is not None:
            nueva = (descripcion or '').strip() or None
            if nueva != gc.descripcion:
                cambios.append('descripción')
                gc.descripcion = nueva
                campos.append('descripcion')
        if motivo is not None:
            motivo = (motivo or 'OTRO').upper()
            _validar_motivo(motivo)
            if motivo != gc.motivo:
                cambios.append(f'motivo→{motivo}')
                gc.motivo = motivo
                campos.append('motivo')
        if observaciones is not None and (observaciones or '') != (gc.observaciones or ''):
            cambios.append('observaciones')
            gc.observaciones = observaciones or None
            campos.append('observaciones')

        if not cambios:
            return gc

        gc.updated_by = usuario
        gc.save(update_fields=campos)
        MovimientoGiftCard.objects.create(
            giftcard=gc,
            tipo='AJUSTE',
            monto=0,
            saldo_resultante=gc.saldo_actual,
            usuario=usuario,
            observaciones='Edición: ' + ', '.join(cambios),
        )
    logger.info("GiftCard editada codigo=%s cambios=%s", gc.codigo, cambios)
    return gc


def marcar_vencidas():
    """
    Marca en estado VENCIDA las gift cards ACTIVA/AGOTADA cuya fecha de
    vencimiento ya pasó. Devuelve el número de tarjetas afectadas.

    El consumo ya rechaza vencidas de forma dinámica (`esta_vencida`); esto
    persiste el estado para filtros, KPIs y reportes. Pensado para cron diario.
    """
    hoy = timezone.localdate()
    return GiftCard.objects.filter(
        estado__in=['ACTIVA', 'AGOTADA'],
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lt=hoy,
    ).update(estado='VENCIDA')
