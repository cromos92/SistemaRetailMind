"""
Servicio de Fidelización por puntos (function-based, estilo `pos_service.py`).

Una sola fuente de verdad para acumular, canjear, ajustar, reversar y expirar
puntos. Usado por el hook de cobro, la API desktop, las vistas de gestión y el
command de expiración.

Garantías:
- Cada operación que mueve saldo abre `transaction.atomic()` y bloquea la
  `CuentaPuntos` con `select_for_update()`.
- Idempotencia vía `idempotency_key` única en `MovimientoPuntos` (reintentos
  del POS no duplican acumulación).
- Vencimiento FIFO: las ACUMULACION/BIENVENIDA son lotes con `fecha_expiracion`;
  el canje y la expiración consumen los lotes más antiguos primero.
"""
import logging
from datetime import timedelta

from django.apps import apps
from django.db import transaction, IntegrityError
from django.db.models import F, Q, Sum
from django.utils import timezone

from app.models import (
    Cliente,
    CuentaPuntos,
    MovimientoPuntos,
    ProgramaFidelizacion,
    ReservaPuntos,
    CanjeVale,
    calcular_fecha_expiracion,
    validar_rut_chileno,
)

logger = logging.getLogger('app')

# Formato móvil chileno: 9 dígitos comenzando con 9 (admite +56 / espacios).
import re as _re
_EMAIL_RE = _re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


class FidelizacionError(Exception):
    """Error de negocio de fidelización (saldo insuficiente, sin cuenta, etc.)."""


def normalizar_celular(celular):
    """Devuelve los 9 dígitos del móvil chileno, o '' si no es válido."""
    if not celular:
        return ''
    n = _re.sub(r'[^0-9]', '', str(celular))
    if len(n) == 11 and n.startswith('56'):
        n = n[2:]
    if len(n) == 9 and n.startswith('9'):
        return n
    return ''


def validar_email(email):
    return bool(email) and bool(_EMAIL_RE.match(email.strip()))


def normalizar_rut(rut):
    """Normaliza a 'cuerpo+DV' sin puntos/guion/espacios, en mayúsculas."""
    if not rut:
        return ''
    return str(rut).replace('.', '').replace('-', '').replace(' ', '').upper()


def es_rut_empresa(rut):
    """Personas jurídicas chilenas tienen número de RUT >= 50.000.000. No fidelizan."""
    try:
        digitos = ''.join(c for c in str(rut or '') if c.isdigit())
        if len(digitos) < 2:
            return False
        numero = int(digitos[:-1])
        return numero >= 50_000_000
    except (ValueError, IndexError):
        return False


# Documentos tributarios que, por definición, son venta a empresa (B2B).
TIPOS_DOCUMENTO_NO_FIDELIZAN = {'FACTURA_ELECTRONICA', 'FACTURA ELECTRONICA'}

# RUTs genéricos que se usan para venta sin cliente identificado.
RUT_FICTICIOS = {'66666666-6'}


def venta_fideliza(ticket, *, tipo_documento=None, cotizacion=None):
    """
    Decide si una venta participa del programa de fidelización.

    Fuente única de verdad para acumular puntos Y para aceptar vales de canje:
    ambos son "beneficio de cliente particular" y deben excluirse juntos.

    La fidelización es SOLO para clientes particulares. Se excluye si:
      - La venta viene de una cotización (las cotizaciones son a empresas).
      - El documento es Factura Electrónica (venta B2B).
      - El RUT es de persona jurídica (>= 50.000.000).
      - El RUT falta, es inválido, o es el genérico de consumidor final.

    Ojo: `es_rut_empresa` por sí solo NO alcanza — una EIRL o una persona
    natural con giro tiene RUT < 50M y antes acumulaba puntos al facturarle.

    Devuelve (fideliza: bool, motivo: str). `motivo` es '' cuando fideliza.
    """
    if cotizacion is not None:
        return False, 'venta originada en cotización (cliente empresa)'

    if tipo_documento and str(tipo_documento).upper() in TIPOS_DOCUMENTO_NO_FIDELIZAN:
        return False, 'documento de tipo factura (venta a empresa)'

    rut = (getattr(ticket, 'cliente_rut', '') or '').strip()
    if not rut:
        return False, 'venta sin RUT de cliente'
    # Comparación NORMALIZADA (sin puntos/guion/espacios): `RUT_FICTICIOS` está
    # escrito en un solo formato, pero el RUT del ticket llega como lo tecleó la
    # caja. Con comparación literal, "66.666.666-6" se colaba como cliente real
    # y el consumidor final acumulaba puntos.
    if normalizar_rut(rut) in {normalizar_rut(r) for r in RUT_FICTICIOS}:
        return False, 'RUT genérico de consumidor final'

    from app.models.base import validar_rut_chileno as _validar_rut
    if not _validar_rut(rut):
        return False, 'RUT inválido'
    if es_rut_empresa(rut):
        return False, 'RUT de empresa'

    return True, ''


def resolver_cliente_por_rut(rut):
    """
    Devuelve el Cliente del CRM cuyo RUT coincide (comparación robusta sin
    formato), o None. NO crea el cliente.
    """
    rut_norm = normalizar_rut(rut)
    if not rut_norm:
        return None
    # Los RUT en BD pueden estar con o sin formato (12.345.678-9 vs 123456789).
    # Probamos varias representaciones del cuerpo para acotar el queryset y
    # confirmamos la coincidencia exacta normalizando en Python.
    cuerpo = rut_norm[:-1]
    cuerpo_con_puntos = ''
    if cuerpo.isdigit():
        cuerpo_con_puntos = f"{int(cuerpo):,}".replace(',', '.')
    candidatos = Cliente.objects.filter(
        Q(rut__icontains=cuerpo) | Q(rut__icontains=cuerpo_con_puntos)
    )[:50] if cuerpo_con_puntos else Cliente.objects.filter(rut__icontains=cuerpo)[:50]
    for c in candidatos:
        if normalizar_rut(c.rut) == rut_norm:
            return c
    return None


def resolver_cliente_por_email(email):
    """
    Devuelve el Cliente del CRM cuyo email coincide (case-insensitive), SOLO si
    hay exactamente UNA coincidencia activa. Si hay 0 o varias, devuelve None:
    el email NO es único en el CRM, así que un correo ambiguo no permite
    identificar de forma segura. NO crea el cliente.
    """
    if not validar_email(email):
        return None
    email_norm = email.strip().lower()
    candidatos = list(
        Cliente.objects.filter(email__iexact=email_norm, activo=True)[:2]
    )
    return candidatos[0] if len(candidatos) == 1 else None


def resolver_cliente_por_identificador(identificador):
    """
    Resuelve un Cliente por correo (si el texto parece email) o por RUT.
    Devuelve None si no hay coincidencia, o si el email no es único.
    """
    if identificador and '@' in str(identificador):
        return resolver_cliente_por_email(identificador)
    return resolver_cliente_por_rut(identificador)


def _actualizar_nivel_cuenta(cuenta, cliente, programa):
    """
    Recalcula gasto 12m desde tickets y actualiza nivel de la cuenta.
    Debe llamarse dentro de una transacción con la cuenta bloqueada.
    """
    Ticket = apps.get_model('app', 'Ticket')
    hace_12m = timezone.now() - timedelta(days=365)
    gasto = int(
        Ticket.objects.filter(
            cliente=cliente, estado='PAGADO', fecha__gte=hace_12m,
        ).aggregate(total=Sum('total'))['total'] or 0
    )
    nuevo_nivel = programa.calcular_nivel(gasto)
    cuenta.gasto_12_meses = gasto
    cuenta.nivel = nuevo_nivel
    cuenta.nivel_actualizado = timezone.now()
    cuenta.save(update_fields=['gasto_12_meses', 'nivel', 'nivel_actualizado', 'updated_at'])


def get_or_create_cuenta(cliente, *, programa=None, otorgar_bienvenida=True,
                         usuario=None):
    """
    Obtiene (o crea) la CuentaPuntos del cliente. Si se crea y el programa
    define `puntos_bienvenida`, otorga el bono de bienvenida.
    Devuelve (cuenta, creada: bool).
    """
    cuenta, creada = CuentaPuntos.objects.get_or_create(cliente=cliente)
    if creada and otorgar_bienvenida:
        programa = programa or ProgramaFidelizacion.get_activo()
        if programa and programa.puntos_bienvenida > 0:
            _otorgar(
                cuenta, 'BIENVENIDA', programa.puntos_bienvenida,
                programa=programa, usuario=usuario,
                idempotency_key=f"bienvenida:{cuenta.id}",
                observaciones='Bono de bienvenida',
                rut_cliente=getattr(cliente, 'rut', None),
                canal='AUTO',
            )
    return cuenta, creada


def _otorgar(cuenta, tipo, puntos, *, programa=None, ticket=None, sucursal=None,
             usuario=None, idempotency_key=None, observaciones='',
             rut_cliente=None, canal='AUTO'):
    """
    Crea un lote de puntos positivo (ACUMULACION/BIENVENIDA/AJUSTE+/REVERSA+) y
    actualiza el saldo. Todo crédito de puntos es un lote consumible por FIFO;
    se le asigna fecha_expiracion según el programa para que también caduque.
    Debe correr dentro de una transacción con la cuenta bloqueada.
    """
    programa = programa or ProgramaFidelizacion.get_activo()
    fecha_exp = calcular_fecha_expiracion(programa)
    nuevo_saldo = cuenta.saldo_puntos + puntos
    mov = MovimientoPuntos.objects.create(
        cuenta=cuenta,
        tipo=tipo,
        puntos=puntos,
        saldo_resultante=nuevo_saldo,
        fecha_expiracion=fecha_exp,
        ticket=ticket,
        sucursal=sucursal,
        usuario=usuario,
        idempotency_key=idempotency_key,
        observaciones=observaciones,
        rut_cliente=rut_cliente,
        canal=canal,
    )
    cuenta.saldo_puntos = nuevo_saldo
    cuenta.save(update_fields=['saldo_puntos', 'updated_at'])
    return mov


def _consumir_lotes_fifo(cuenta, puntos, tipo, *, ticket=None, sucursal=None,
                         usuario=None, idempotency_key=None, observaciones='',
                         rut_cliente=None, canal='POS'):
    """
    Consume `puntos` (cantidad positiva) de los lotes más antiguos primero,
    creando movimientos negativos enlazados al lote origen. Actualiza saldo.
    Debe correr dentro de una transacción con la cuenta bloqueada.

    Devuelve la lista de movimientos de consumo creados.
    """
    restante = puntos
    movimientos = []
    # Cualquier crédito de puntos (puntos > 0) es un lote consumible. Se ordena
    # por fecha de expiración (los que vencen antes se gastan primero); los
    # lotes sin vencimiento (NULL) quedan al final.
    lotes = (
        MovimientoPuntos.objects
        .select_for_update()
        .filter(cuenta=cuenta, puntos__gt=0)
        .order_by(F('fecha_expiracion').asc(nulls_last=True), 'fecha')
    )
    for lote in lotes:
        if restante <= 0:
            break
        disponible = lote.puntos - lote.puntos_consumidos_del_lote
        if disponible <= 0:
            continue
        tomar = min(disponible, restante)
        lote.puntos_consumidos_del_lote += tomar
        lote.save(update_fields=['puntos_consumidos_del_lote'])

        cuenta.saldo_puntos -= tomar
        mov = MovimientoPuntos.objects.create(
            cuenta=cuenta,
            tipo=tipo,
            puntos=-tomar,
            saldo_resultante=cuenta.saldo_puntos,
            lote_origen=lote,
            ticket=ticket,
            sucursal=sucursal,
            usuario=usuario,
            # idempotency_key solo en el primer consumo para no violar unique
            idempotency_key=(idempotency_key if not movimientos else None),
            observaciones=observaciones,
            rut_cliente=rut_cliente,
            canal=canal,
        )
        movimientos.append(mov)
        restante -= tomar

    cuenta.save(update_fields=['saldo_puntos', 'updated_at'])
    return movimientos, restante


def _auto_registrar_cliente_desde_historial(rut, *, ticket_actual=None, usuario=None):
    """
    Crea un registro Cliente en el CRM a partir de los datos del ticket actual
    (o del historial de tickets anteriores) cuando el RUT existe en el sistema
    pero nunca fue dado de alta manualmente.

    Devuelve (cliente, True) si creó el registro, (None, False) si no fue posible.
    """
    rut_norm = normalizar_rut(rut)
    if not rut_norm:
        return None, False

    # Fuente 1: datos del ticket que se está cobrando ahora.
    fuentes = []
    if ticket_actual and (getattr(ticket_actual, 'cliente_nombre', '') or '').strip():
        fuentes.append(ticket_actual)

    # Fuente 2: último ticket con nombre en el historial del mismo RUT.
    if not fuentes:
        try:
            Ticket = apps.get_model('app', 'Ticket')
            cuerpo = rut_norm[:-1]
            hist = (
                Ticket.objects
                .filter(cliente_rut__icontains=cuerpo)
                .exclude(cliente_nombre__isnull=True)
                .exclude(cliente_nombre__exact='')
                .order_by('-fecha')
                .first()
            )
            if hist:
                fuentes.append(hist)
        except Exception:
            logger.warning("Error buscando historial de tickets para rut=%s", rut)

    if not fuentes:
        return None, False

    fuente = fuentes[0]
    nombre_completo = (getattr(fuente, 'cliente_nombre', '') or '').strip()
    if not nombre_completo:
        return None, False

    partes = nombre_completo.split(' ', 1)
    nombre = partes[0]
    apellido = partes[1] if len(partes) > 1 else '-'

    email = (getattr(fuente, 'cliente_email', '') or '').strip()
    celular = (
        getattr(fuente, 'cliente_telefono_secundario', '') or
        getattr(fuente, 'cliente_telefono', '') or ''
    ).strip()

    try:
        cliente, _cuenta, creado = registrar_cliente_manual(
            nombre=nombre,
            apellido=apellido,
            rut=rut,
            email=email,
            celular=celular,
            usuario=usuario,
        )
        if creado:
            logger.info(
                "Cliente auto-registrado desde historial rut=%s nombre='%s %s'",
                rut, nombre, apellido,
            )
        return cliente, creado
    except FidelizacionError as exc:
        logger.warning("No se pudo auto-registrar cliente rut=%s: %s", rut, exc)
        return None, False
    except Exception:
        logger.exception("Error inesperado al auto-registrar cliente rut=%s", rut)
        return None, False


def acumular_puntos_por_venta(ticket, usuario=None, *, tipo_documento=None,
                              cotizacion=None):
    """
    Hook de cobro: acumula puntos por una venta pagada.

    - Aplica `venta_fideliza()`: solo clientes particulares (no facturas, no
      cotizaciones, no RUT de empresa).
    - Resuelve el Cliente por `ticket.cliente_rut`. Si no hay cliente
      identificado → venta anónima → no acumula (devuelve None sin error).
    - Si el RUT existe en historial de tickets pero no en el CRM, auto-registra
      al cliente (con bono de bienvenida) antes de acumular.
    - Setea `ticket.cliente` si lo encuentra.
    - Idempotente por `acum:{ticket.id}`.

    `tipo_documento` y `cotizacion` los pasa el POS al cobrar; sin ellos el
    guard sigue funcionando pero solo por RUT (comportamiento histórico).

    Devuelve dict {puntos_ganados, saldo_total, valor_punto, valor_ganado_pesos,
    saldo_pesos} o None.
    """
    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        return None

    fideliza, motivo = venta_fideliza(
        ticket, tipo_documento=tipo_documento, cotizacion=cotizacion,
    )
    if not fideliza:
        logger.debug(
            "Venta no fideliza ticket=%s motivo=%s",
            getattr(ticket, 'correlativo', '?'), motivo,
        )
        return None

    rut_ticket = getattr(ticket, 'cliente_rut', '') or ''
    cliente = resolver_cliente_por_rut(rut_ticket)
    if not cliente and rut_ticket:
        # RUT presente pero sin registro CRM → auto-registrar desde historial.
        cliente, _creado = _auto_registrar_cliente_desde_historial(
            rut_ticket, ticket_actual=ticket, usuario=usuario,
        )
    if not cliente:
        return None  # venta anónima → no acumula

    # Resultado uniforme para el POS
    def _resultado(puntos_ganados, saldo_total, nivel='PLATA'):
        vp = programa.valor_punto_en_pesos or 0
        return {
            'puntos_ganados': puntos_ganados,
            'saldo_total': saldo_total,
            'valor_punto': vp,
            'valor_ganado_pesos': puntos_ganados * vp,
            'saldo_pesos': saldo_total * vp,
            'nivel': nivel,
        }

    # Enlazar el ticket al cliente (trazabilidad), sin romper si ya estaba.
    if getattr(ticket, 'cliente_id', None) != cliente.id:
        ticket.cliente = cliente
        try:
            ticket.save(update_fields=['cliente'])
        except Exception:
            logger.exception("No se pudo setear ticket.cliente ticket=%s", ticket.id)

    # Nivel vigente ANTES de acumular (para aplicar la tasa correcta).
    cuenta_previa = getattr(cliente, 'cuenta_puntos', None)
    nivel_actual = cuenta_previa.nivel if cuenta_previa else 'PLATA'

    base = ticket.total or 0
    puntos = programa.calcular_puntos_con_nivel(base, nivel_actual)
    if puntos <= 0:
        cuenta, _ = get_or_create_cuenta(cliente, programa=programa, usuario=usuario)
        nivel = getattr(cuenta, 'nivel', 'PLATA')
        return _resultado(0, cuenta.saldo_puntos, nivel)

    idem = f"acum:{ticket.id}"
    with transaction.atomic():
        existente = MovimientoPuntos.objects.filter(idempotency_key=idem).first()
        if existente:
            cuenta = CuentaPuntos.objects.get(pk=existente.cuenta_id)
            return _resultado(existente.puntos, cuenta.saldo_puntos, getattr(cuenta, 'nivel', 'PLATA'))

        cuenta, _ = get_or_create_cuenta(cliente, programa=programa, usuario=usuario)
        cuenta = CuentaPuntos.objects.select_for_update().get(pk=cuenta.pk)
        try:
            mov = _otorgar(
                cuenta, 'ACUMULACION', puntos,
                programa=programa, ticket=ticket,
                sucursal=getattr(ticket, 'sucursal', None), usuario=usuario,
                idempotency_key=idem,
                observaciones=f'Compra ticket {ticket.correlativo} (${base:,})',
                rut_cliente=getattr(cliente, 'rut', None),
                canal='POS',
            )
        except IntegrityError:
            mov = MovimientoPuntos.objects.filter(idempotency_key=idem).first()
            if not mov:
                raise

    # Actualizar gasto 12m y nivel FUERA del bloque principal: si la migración
    # aún no se aplicó (columnas inexistentes) no cancela la acumulación.
    try:
        with transaction.atomic():
            cuenta_fresca = CuentaPuntos.objects.select_for_update().get(pk=cuenta.pk)
            _actualizar_nivel_cuenta(cuenta_fresca, cliente, programa)
            cuenta.nivel = cuenta_fresca.nivel
            cuenta.gasto_12_meses = cuenta_fresca.gasto_12_meses
    except Exception:
        logger.warning("No se pudo actualizar nivel cliente=%s (¿falta migración?)", cliente.id)

    # Bono "invita y gana": si esta es la primera compra de un referido,
    # paga los bonos a ambos (idempotente; nunca lanza).
    _pagar_referido_si_corresponde(cliente, programa, usuario=usuario)

    # Desafíos/promos: paga bonos de desafíos recién completados (idem).
    _pagar_desafios_si_corresponde(cliente, programa, usuario=usuario)

    logger.info("Puntos acumulados cliente=%s ticket=%s puntos=%s saldo=%s nivel=%s",
                cliente.id, ticket.correlativo, puntos, cuenta.saldo_puntos, cuenta.nivel)
    return _resultado(puntos, cuenta.saldo_puntos, cuenta.nivel)


def canjear_puntos(cliente, puntos, *, ticket=None, sucursal=None, usuario=None,
                   idempotency_key=None, canal='POS'):
    """
    Canjea `puntos` (positivo) del cliente, consumiendo lotes FIFO.
    Devuelve dict {puntos_canjeados, valor_pesos, saldo_total}.
    Lanza FidelizacionError si no alcanza o no cumple el mínimo.
    """
    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        raise FidelizacionError('No hay un programa de fidelización activo.')
    puntos = int(puntos)
    if puntos <= 0:
        raise FidelizacionError('La cantidad a canjear debe ser mayor a 0.')
    if puntos < programa.minimo_canje_puntos:
        raise FidelizacionError(
            f'El canje mínimo es {programa.minimo_canje_puntos} puntos.'
        )
    if programa.incremento_canje > 0 and puntos % programa.incremento_canje != 0:
        raise FidelizacionError(
            f'El canje debe ser múltiplo de {programa.incremento_canje} puntos.'
        )

    with transaction.atomic():
        try:
            cuenta = CuentaPuntos.objects.select_for_update().get(cliente=cliente)
        except CuentaPuntos.DoesNotExist:
            raise FidelizacionError('El cliente no tiene cuenta de puntos.')
        if cuenta.saldo_puntos < puntos:
            raise FidelizacionError(
                f'Saldo insuficiente (disponible {cuenta.saldo_puntos}, requerido {puntos}).'
            )
        _consumir_lotes_fifo(
            cuenta, puntos, 'CANJE',
            ticket=ticket, sucursal=sucursal, usuario=usuario,
            idempotency_key=idempotency_key,
            observaciones=f'Canje de {puntos} puntos',
            rut_cliente=getattr(cliente, 'rut', None),
            canal=canal,
        )
    valor = puntos * programa.valor_punto_en_pesos
    return {'puntos_canjeados': puntos, 'valor_pesos': valor,
            'saldo_total': cuenta.saldo_puntos}


# ========== RESERVA DE PUNTOS (compra híbrida desde la app) ==========

def _puntos_comprometidos(cuenta):
    """
    Puntos comprometidos pero aún NO debitados del ledger: la suma de las
    reservas de compra app (RESERVADA) y los vales de canje con código
    (PENDIENTE). Ambos bloquean saldo sin moverlo hasta que se confirman
    (pago / canje en POS) o se liberan (expiración / anulación). Contar AMBOS
    evita que el cliente comprometa los mismos puntos en una compra y en un vale.

    Cuenta TODO compromiso vivo aunque su TTL ya pasó: la liberación efectiva del
    saldo ocurre solo al confirmar/cancelar/expirar (que sí cambian el estado),
    no en el instante en que cruza `expira_en`. Liberar antes permitiría
    doble-compromiso de puntos sin débito.
    """
    if cuenta is None:
        return 0
    reservas = (
        ReservaPuntos.objects
        .filter(cuenta=cuenta, estado='RESERVADA')
        .aggregate(total=Sum('puntos_reservados'))['total'] or 0
    )
    vales = (
        CanjeVale.objects
        .filter(cuenta=cuenta, estado='PENDIENTE')
        .aggregate(total=Sum('puntos'))['total'] or 0
    )
    return reservas + vales


def saldo_disponible_para_reserva(cuenta):
    """
    Puntos que el cliente puede comprometer AHORA (reservar para una compra o
    canjear por código): el saldo menos lo ya comprometido (ver
    `_puntos_comprometidos`).
    """
    if cuenta is None:
        return 0
    return max(0, cuenta.saldo_puntos - _puntos_comprometidos(cuenta))


def cotizar_canje(cliente, puntos):
    """
    Datos para que la app muestre cuántos puntos puede aplicar y su valor en $.
    No reserva ni lanza por saldo: devuelve los límites para la UI.

    Si NO hay programa de fidelización activo, NO bloquea: devuelve los límites en
    0 (`puntos_disponibles=False`) para que la app pueda seguir y cobrar a precio
    normal. Solo `reservar_puntos`/`generar_vale_canje` (que mueven puntos) exigen
    programa activo, porque sin tasa no hay con qué calcular el descuento.
    """
    puntos = max(0, int(puntos or 0))
    cuenta = getattr(cliente, 'cuenta_puntos', None)
    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        return {
            'puntos': puntos,
            'valor_pesos': 0,
            'valor_punto': 0,
            'saldo_total': cuenta.saldo_puntos if cuenta else 0,
            'saldo_disponible': 0,
            'minimo_canje': 0,
            'puntos_disponibles': False,
        }
    # Libera compromisos vencidos (reservas y vales) sin depender del cron global.
    _expirar_reservas_de_cuenta(cuenta)
    _expirar_vales_de_cuenta(cuenta)
    saldo = cuenta.saldo_puntos if cuenta else 0
    disponible = saldo_disponible_para_reserva(cuenta)
    return {
        'puntos': puntos,
        'valor_pesos': puntos * programa.valor_punto_en_pesos,
        'valor_punto': programa.valor_punto_en_pesos,
        'saldo_total': saldo,
        'saldo_disponible': disponible,
        'minimo_canje': programa.minimo_canje_puntos,
        'incremento_canje': programa.incremento_canje,
        'puntos_disponibles': True,
    }


def reservar_puntos(cliente, puntos, *, tienda, empresa=None, idempotency_key=None,
                    ttl_minutos=60):
    """
    Reserva (bloqueo lógico) `puntos` del cliente para una compra en `tienda`.
    NO debita el ledger; solo crea una ReservaPuntos RESERVADA con TTL. El
    `codigo_cupon` (PTS-<id>) se materializa como cupón en el ecommerce aparte.

    Devuelve la ReservaPuntos. Lanza FidelizacionError si no alcanza el saldo
    disponible o no cumple el mínimo. Idempotente por `idempotency_key`.
    """
    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        raise FidelizacionError('No hay un programa de fidelización activo.')
    puntos = int(puntos)
    if puntos <= 0:
        raise FidelizacionError('La cantidad a reservar debe ser mayor a 0.')
    if puntos < programa.minimo_canje_puntos:
        raise FidelizacionError(
            f'El mínimo para usar puntos es {programa.minimo_canje_puntos}.'
        )

    # Liberar compromisos vencidos de este cliente antes de calcular el disponible
    # (no dependemos del cron global para que su saldo sea correcto).
    _cuenta_prev = getattr(cliente, 'cuenta_puntos', None)
    _expirar_reservas_de_cuenta(_cuenta_prev)
    _expirar_vales_de_cuenta(_cuenta_prev)

    with transaction.atomic():
        if idempotency_key:
            existente = ReservaPuntos.objects.filter(
                idempotency_key=idempotency_key).first()
            if existente:
                return existente
        try:
            # El lock de la cuenta serializa las reservas concurrentes del mismo
            # cliente: el segundo espera y recalcula el disponible ya restado.
            cuenta = CuentaPuntos.objects.select_for_update().get(cliente=cliente)
        except CuentaPuntos.DoesNotExist:
            raise FidelizacionError('El cliente no tiene cuenta de puntos.')

        disponible = saldo_disponible_para_reserva(cuenta)
        if disponible < puntos:
            raise FidelizacionError(
                f'Saldo disponible insuficiente (disponible {disponible}, requerido {puntos}).'
            )
        reserva = ReservaPuntos.objects.create(
            cuenta=cuenta,
            cliente=cliente,
            puntos_reservados=puntos,
            valor_pesos=puntos * programa.valor_punto_en_pesos,
            tienda=tienda,
            empresa=empresa,
            estado='RESERVADA',
            expira_en=timezone.now() + timedelta(minutes=ttl_minutos),
            idempotency_key=idempotency_key,
        )
        reserva.codigo_cupon = f'PTS-{reserva.id}'
        reserva.save(update_fields=['codigo_cupon', 'updated_at'])
    return reserva


def confirmar_reserva(reserva, *, puntos_reales, order_number=None, ticket=None,
                      sucursal=None, usuario=None):
    """
    Confirma una reserva tras un pago real: debita del ledger (FIFO) los
    `puntos_reales` efectivamente aplicados (el descuento del pedido pagado),
    nunca más que lo reservado ni que el saldo. El sobrante reservado se libera
    de facto (no se debita). Idempotente: una reserva ya CONFIRMADA no vuelve a
    debitar.

    Devuelve dict {puntos_consumidos, saldo_total}.
    """
    puntos_reales = max(0, int(puntos_reales))
    with transaction.atomic():
        reserva = ReservaPuntos.objects.select_for_update().get(pk=reserva.pk)
        cuenta = CuentaPuntos.objects.select_for_update().get(pk=reserva.cuenta_id)
        if reserva.estado == 'CONFIRMADA':
            return {'puntos_consumidos': reserva.puntos_consumidos,
                    'saldo_total': cuenta.saldo_puntos}

        a_consumir = min(puntos_reales, reserva.puntos_reservados, cuenta.saldo_puntos)
        consumido = 0
        if a_consumir > 0:
            _movs, restante = _consumir_lotes_fifo(
                cuenta, a_consumir, 'CANJE',
                ticket=ticket, sucursal=sucursal, usuario=usuario,
                idempotency_key=f'canje_pts:{reserva.id}',
                observaciones=f'Canje por compra app (pedido {order_number or reserva.codigo_cupon})',
                rut_cliente=getattr(reserva.cliente, 'rut', None),
                canal='APP',
            )
            consumido = a_consumir - restante

        reserva.estado = 'CONFIRMADA'
        reserva.puntos_consumidos = consumido
        if order_number:
            reserva.order_number = order_number
        reserva.save(update_fields=['estado', 'puntos_consumidos', 'order_number',
                                    'updated_at'])
    logger.info('Reserva confirmada id=%s puntos=%s pedido=%s',
                reserva.id, consumido, order_number)
    return {'puntos_consumidos': consumido, 'saldo_total': cuenta.saldo_puntos}


def liberar_reserva(reserva, motivo=''):
    """
    Libera una reserva sin tocar el ledger (no había débito). Idempotente: solo
    actúa si está RESERVADA. Devuelve la reserva.
    """
    with transaction.atomic():
        reserva = ReservaPuntos.objects.select_for_update().get(pk=reserva.pk)
        if reserva.estado != 'RESERVADA':
            return reserva
        reserva.estado = 'LIBERADA'
        reserva.save(update_fields=['estado', 'updated_at'])
    if motivo:
        logger.info('Reserva %s liberada (%s)', reserva.id, motivo)
    return reserva


def cancelar_reserva(reserva):
    """
    Cancela una reserva por abandono / fallo de pago: la libera (sin tocar el
    ledger) y DESACTIVA su cupón en el ecommerce para que no quede vivo. Solo
    actúa si está RESERVADA (no cancela una ya CONFIRMADA = pagada). Idempotente.
    Devuelve la reserva.
    """
    from app.services import ecommerce_cupon_service
    reserva = liberar_reserva(reserva, motivo='cancelada')
    if reserva.estado == 'LIBERADA' and reserva.codigo_cupon:
        ecommerce_cupon_service.desactivar_cupon(reserva.tienda, reserva.codigo_cupon)
    return reserva


def _expirar_reservas_de_cuenta(cuenta):
    """
    Expira (lazy) las reservas vencidas de UNA cuenta y desactiva sus cupones.

    Permite que el saldo del cliente se libere aunque el cron global no haya
    corrido: cada vez que el cliente cotiza/reserva de nuevo, sus propias reservas
    vencidas se limpian. Así la compra app no queda "rota" si falta el scheduler.
    No toca el ledger (una reserva nunca debitó). Best-effort: nunca lanza.
    """
    if cuenta is None:
        return
    from app.services import ecommerce_cupon_service
    vencidas = list(ReservaPuntos.objects.filter(
        cuenta=cuenta, estado='RESERVADA', expira_en__lt=timezone.now()))
    for reserva in vencidas:
        codigo = tienda = None
        try:
            with transaction.atomic():
                r = ReservaPuntos.objects.select_for_update().get(pk=reserva.pk)
                if r.estado != 'RESERVADA':
                    continue
                r.estado = 'EXPIRADA'
                r.save(update_fields=['estado', 'updated_at'])
                codigo, tienda = r.codigo_cupon, r.tienda
            if codigo:
                ecommerce_cupon_service.desactivar_cupon(tienda, codigo)
        except Exception:
            logger.exception('Expiración lazy de reserva %s falló', reserva.pk)


def expirar_reservas_vencidas():
    """
    Marca EXPIRADA las reservas RESERVADA cuyo TTL venció y DESACTIVA su cupón en
    el ecommerce. No toca el ledger (nunca se debitó). Devuelve la cantidad de
    reservas expiradas.

    Usado por el command `expirar_reservas_puntos`.
    """
    from app.services import ecommerce_cupon_service

    vencidas = ReservaPuntos.objects.filter(
        estado='RESERVADA', expira_en__lt=timezone.now(),
    )
    total = 0
    for reserva in vencidas.iterator():
        with transaction.atomic():
            r = ReservaPuntos.objects.select_for_update().get(pk=reserva.pk)
            if r.estado != 'RESERVADA':
                continue
            r.estado = 'EXPIRADA'
            r.save(update_fields=['estado', 'updated_at'])
            total += 1
            codigo, tienda = r.codigo_cupon, r.tienda
        # Desactivar el cupón fuera de la transacción (I/O remoto, nunca lanza).
        if codigo:
            ecommerce_cupon_service.desactivar_cupon(tienda, codigo)
    return total


def conciliar_reserva_por_pedido(pedido, codigo_cupon, descuento_pesos):
    """
    Cierra el loop de puntos de una compra de la app: confirma la reserva asociada
    al `codigo_cupon` (PTS-<id>) debitando los puntos por el descuento REALMENTE
    aplicado en el pedido pagado (no por lo reservado: el cupón TYPE_FIXED aplica
    min(monto, subtotal), así que el descuento real puede ser menor).

    Idempotente: una reserva ya CONFIRMADA no vuelve a debitar. No-op si no hay
    reserva para ese cupón. Funciona aunque la reserva ya haya EXPIRADO por TTL
    (el pedido se pagó de verdad, hay que debitar).
    """
    codigo = (codigo_cupon or '').strip()
    if not codigo:
        return None
    reserva = ReservaPuntos.objects.filter(codigo_cupon=codigo).first()
    if reserva is None or reserva.estado == 'CONFIRMADA':
        return reserva

    # Tasa del SNAPSHOT de la reserva: `valor_pesos` se fijó al reservar como
    # `puntos_reservados * valor_punto_en_pesos`. Usarla evita dos bugs: que el
    # programa cambie su tasa entre reserva y conciliación, y el fallback erróneo
    # a 1 (que con la tasa real de $10 inflaba 10x los puntos a debitar).
    if reserva.puntos_reservados > 0 and reserva.valor_pesos > 0:
        valor_punto = reserva.valor_pesos / reserva.puntos_reservados
    else:
        programa = ProgramaFidelizacion.get_activo()
        valor_punto = programa.valor_punto_en_pesos if programa else 0
    if not valor_punto or valor_punto <= 0:
        logger.warning('conciliar_reserva_por_pedido: sin valor_punto para reserva '
                       '%s; no se debita', reserva.id)
        return reserva

    try:
        descuento = int(float(descuento_pesos or 0))
    except (TypeError, ValueError):
        descuento = 0
    puntos_reales = int(descuento // valor_punto)
    order_number = getattr(pedido, 'numero_pedido_canal', '') or ''
    confirmar_reserva(reserva, puntos_reales=puntos_reales, order_number=order_number)
    return reserva


# ========== CANJE CON CÓDIGO (vale para tienda física) ==========

import secrets as _secrets

# Alfabeto sin caracteres ambiguos (sin 0/O, 1/I/L) para dictar/teclear el código.
_VALE_ALFABETO = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


def _generar_codigo_vale(longitud=8):
    """Código de vale ininteligible y legible: 'RM-XXXXXXXX' (secrets, no random)."""
    cuerpo = ''.join(_secrets.choice(_VALE_ALFABETO) for _ in range(longitud))
    return f'RM-{cuerpo}'


def _expirar_vales_de_cuenta(cuenta):
    """
    Expira (lazy) los vales PENDIENTE vencidos de UNA cuenta. No toca el ledger
    (un vale nunca debitó hasta canjearse). Devuelve el saldo disponible correcto
    sin depender del cron global. Best-effort: nunca lanza.
    """
    if cuenta is None:
        return
    vencidos = list(CanjeVale.objects.filter(
        cuenta=cuenta, estado='PENDIENTE', expira_en__lt=timezone.now()))
    for vale in vencidos:
        try:
            with transaction.atomic():
                v = CanjeVale.objects.select_for_update().get(pk=vale.pk)
                if v.estado != 'PENDIENTE':
                    continue
                v.estado = 'EXPIRADO'
                v.save(update_fields=['estado', 'updated_at'])
        except Exception:
            logger.exception('Expiración lazy de vale %s falló', vale.pk)


def generar_vale_canje(cliente, puntos, *, empresa=None, idempotency_key=None,
                       ttl_horas=72):
    """
    Genera un vale de canje "con código": compromete `puntos` del cliente y
    devuelve un `CanjeVale` PENDIENTE con un código de un solo uso para presentar
    en tienda. NO debita el ledger (el débito ocurre al canjear en el POS).

    Valida programa activo, mínimo de canje y saldo DISPONIBLE (descontando otros
    compromisos vivos). Idempotente por `idempotency_key`. Lanza FidelizacionError.
    """
    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        raise FidelizacionError('No hay un programa de fidelización activo.')
    puntos = int(puntos)
    if puntos <= 0:
        raise FidelizacionError('La cantidad a canjear debe ser mayor a 0.')
    if puntos < programa.minimo_canje_puntos:
        raise FidelizacionError(
            f'El canje mínimo es {programa.minimo_canje_puntos} puntos.'
        )

    # Liberar compromisos vencidos antes de calcular el disponible (no dependemos
    # del cron global para que su saldo sea correcto).
    cuenta_prev = getattr(cliente, 'cuenta_puntos', None)
    _expirar_reservas_de_cuenta(cuenta_prev)
    _expirar_vales_de_cuenta(cuenta_prev)

    with transaction.atomic():
        if idempotency_key:
            existente = CanjeVale.objects.filter(
                idempotency_key=idempotency_key).first()
            if existente:
                return existente
        try:
            cuenta = CuentaPuntos.objects.select_for_update().get(cliente=cliente)
        except CuentaPuntos.DoesNotExist:
            raise FidelizacionError('El cliente no tiene cuenta de puntos.')

        disponible = saldo_disponible_para_reserva(cuenta)
        if disponible < puntos:
            raise FidelizacionError(
                f'Saldo disponible insuficiente (disponible {disponible}, requerido {puntos}).'
            )

        # Código único: reintenta ante la rarísima colisión (unique en BD).
        codigo = _generar_codigo_vale()
        for _ in range(5):
            if not CanjeVale.objects.filter(codigo=codigo).exists():
                break
            codigo = _generar_codigo_vale()

        vale = CanjeVale.objects.create(
            cuenta=cuenta,
            cliente=cliente,
            puntos=puntos,
            valor_pesos=puntos * programa.valor_punto_en_pesos,
            codigo=codigo,
            estado='PENDIENTE',
            empresa=empresa,
            expira_en=timezone.now() + timedelta(hours=ttl_horas),
            idempotency_key=idempotency_key,
        )
    logger.info('Vale de canje generado id=%s codigo=%s puntos=%s cliente=%s',
                vale.id, vale.codigo, puntos, cliente.id)
    return vale


def validar_vale(codigo):
    """
    Consulta (sin debitar) el estado de un vale por código, para que el POS lo
    muestre antes de canjear. Devuelve dict con su estado/valor. No lanza por
    "no encontrado": devuelve estado='NO_EXISTE'.
    """
    codigo = (codigo or '').strip().upper()
    vale = CanjeVale.objects.filter(codigo=codigo).first()
    if vale is None:
        return {'existe': False, 'estado': 'NO_EXISTE'}
    expirado = vale.estado == 'PENDIENTE' and vale.expira_en <= timezone.now()
    return {
        'existe': True,
        'codigo': vale.codigo,
        'estado': 'EXPIRADO' if expirado else vale.estado,
        'puntos': vale.puntos,
        'valor_pesos': vale.valor_pesos,
        'canjeable': vale.estado == 'PENDIENTE' and not expirado,
        'cliente_nombre': vale.cliente.nombre_completo,
        'cliente_rut': vale.cliente.rut or '',
        'expira_en': vale.expira_en.isoformat(),
    }


def canjear_vale(codigo, *, sucursal=None, usuario=None, ticket=None):
    """
    Canjea un vale en el POS: debita del ledger (FIFO) los puntos del vale y lo
    marca CANJEADO. Devuelve dict {valor_pesos, puntos, codigo} para que el POS
    aplique el descuento. Idempotente (un vale ya CANJEADO no vuelve a debitar).
    Lanza FidelizacionError si el código no existe, ya se usó o expiró.
    """
    codigo = (codigo or '').strip().upper()
    if not codigo:
        raise FidelizacionError('Falta el código del vale.')

    # Acumulamos el error y lo lanzamos FUERA del bloque atómico: si lo
    # lanzáramos dentro, el rollback descartaría también el marcado EXPIRADO.
    resultado = None
    error = None
    with transaction.atomic():
        vale = (CanjeVale.objects.select_for_update()
                .filter(codigo=codigo).first())
        if vale is None:
            error = 'El código no existe.'
        elif vale.estado == 'CANJEADO':
            # Idempotente: mismo resultado, no vuelve a debitar.
            resultado = {'valor_pesos': vale.valor_pesos, 'puntos': vale.puntos,
                         'codigo': vale.codigo, 'ya_canjeado': True}
        elif vale.estado == 'ANULADO':
            error = 'El vale fue anulado.'
        elif vale.estado == 'EXPIRADO' or vale.expira_en <= timezone.now():
            if vale.estado == 'PENDIENTE':
                vale.estado = 'EXPIRADO'
                vale.save(update_fields=['estado', 'updated_at'])
            error = 'El vale expiró.'
        else:
            cuenta = CuentaPuntos.objects.select_for_update().get(pk=vale.cuenta_id)
            if cuenta.saldo_puntos < vale.puntos:
                # El saldo cambió desde la emisión (otra operación lo consumió).
                error = (f'Saldo insuficiente para canjear el vale '
                         f'(disponible {cuenta.saldo_puntos}, vale {vale.puntos}).')
            else:
                _consumir_lotes_fifo(
                    cuenta, vale.puntos, 'CANJE',
                    ticket=ticket, sucursal=sucursal, usuario=usuario,
                    idempotency_key=f'vale:{vale.id}',
                    observaciones=f'Canje vale {vale.codigo}',
                    rut_cliente=getattr(vale.cliente, 'rut', None),
                    canal='POS',
                )
                vale.estado = 'CANJEADO'
                vale.canjeado_en = timezone.now()
                vale.sucursal_canje = sucursal
                vale.usuario_canje = usuario
                vale.ticket = ticket
                vale.save(update_fields=['estado', 'canjeado_en', 'sucursal_canje',
                                         'usuario_canje', 'ticket', 'updated_at'])
                logger.info('Vale canjeado codigo=%s puntos=%s sucursal=%s usuario=%s',
                            vale.codigo, vale.puntos, getattr(sucursal, 'id', None),
                            getattr(usuario, 'id', None))
                resultado = {'valor_pesos': vale.valor_pesos, 'puntos': vale.puntos,
                             'codigo': vale.codigo, 'ya_canjeado': False}

    if error:
        raise FidelizacionError(error)
    return resultado


def anular_vale(vale, motivo=''):
    """
    Anula un vale PENDIENTE (lo cancela el cliente o un admin): vuelve a dejar sus
    puntos disponibles sin tocar el ledger. Idempotente: solo actúa si está
    PENDIENTE. Devuelve el vale.
    """
    with transaction.atomic():
        vale = CanjeVale.objects.select_for_update().get(pk=vale.pk)
        if vale.estado != 'PENDIENTE':
            return vale
        vale.estado = 'ANULADO'
        vale.save(update_fields=['estado', 'updated_at'])
    if motivo:
        logger.info('Vale %s anulado (%s)', vale.codigo, motivo)
    return vale


def expirar_vales_vencidos():
    """
    Marca EXPIRADO los vales PENDIENTE cuyo TTL venció (no toca el ledger: nunca
    se debitó). Devuelve la cantidad expirada. Usado por el scheduler / command.
    """
    vencidos = CanjeVale.objects.filter(
        estado='PENDIENTE', expira_en__lt=timezone.now(),
    )
    total = 0
    for vale in vencidos.iterator():
        with transaction.atomic():
            v = CanjeVale.objects.select_for_update().get(pk=vale.pk)
            if v.estado != 'PENDIENTE':
                continue
            v.estado = 'EXPIRADO'
            v.save(update_fields=['estado', 'updated_at'])
            total += 1
    return total


def ajuste_manual(cliente, puntos, *, usuario=None, observaciones=''):
    """
    Ajuste manual de puntos (positivo suma como lote nuevo; negativo consume
    FIFO). Solo para roles autorizados (admin). Devuelve el saldo resultante.
    """
    puntos = int(puntos)
    if puntos == 0:
        raise FidelizacionError('El ajuste no puede ser 0.')
    with transaction.atomic():
        cuenta, _ = CuentaPuntos.objects.get_or_create(cliente=cliente)
        cuenta = CuentaPuntos.objects.select_for_update().get(pk=cuenta.pk)
        rut = getattr(cliente, 'rut', None)
        if puntos > 0:
            _otorgar(cuenta, 'AJUSTE', puntos, usuario=usuario,
                     observaciones=observaciones or 'Ajuste manual (+)',
                     rut_cliente=rut, canal='MANUAL')
        else:
            if cuenta.saldo_puntos < abs(puntos):
                raise FidelizacionError('Saldo insuficiente para el ajuste negativo.')
            _consumir_lotes_fifo(cuenta, abs(puntos), 'AJUSTE', usuario=usuario,
                                 observaciones=observaciones or 'Ajuste manual (-)',
                                 rut_cliente=rut, canal='MANUAL')
    return cuenta.saldo_puntos


def otorgar_bono_cumpleanos(cliente, usuario=None):
    """
    Otorga el bono de cumpleaños si:
    - El programa está activo y tiene puntos_cumpleanos > 0
    - El cliente tiene fecha_nacimiento y coincide con mes/día de hoy
    - No se otorgó ya este año (chequeado por anio_ultimo_bono_cumpleanos)
    Devuelve dict {puntos, saldo_total} o None si no corresponde.
    Idempotente: segunda llamada en el mismo año devuelve None.
    """
    programa = ProgramaFidelizacion.get_activo()
    if not programa or programa.puntos_cumpleanos <= 0:
        return None

    fecha_nac = getattr(cliente, 'fecha_nacimiento', None)
    if not fecha_nac:
        return None

    hoy = timezone.localdate()
    if fecha_nac.month != hoy.month or fecha_nac.day != hoy.day:
        return None  # no es cumpleaños hoy

    anio_actual = hoy.year
    with transaction.atomic():
        cuenta, _ = get_or_create_cuenta(cliente, programa=programa,
                                         otorgar_bienvenida=False, usuario=usuario)
        cuenta = CuentaPuntos.objects.select_for_update().get(pk=cuenta.pk)
        if cuenta.anio_ultimo_bono_cumpleanos == anio_actual:
            return None  # ya se otorgó este año

        idem = f"cumpleanos:{cuenta.id}:{anio_actual}"
        if MovimientoPuntos.objects.filter(idempotency_key=idem).exists():
            cuenta.anio_ultimo_bono_cumpleanos = anio_actual
            cuenta.save(update_fields=['anio_ultimo_bono_cumpleanos', 'updated_at'])
            return None

        _otorgar(
            cuenta, 'CUMPLEANOS', programa.puntos_cumpleanos,
            programa=programa, usuario=usuario,
            idempotency_key=idem,
            observaciones=f'Bono de cumpleaños {anio_actual}',
            rut_cliente=getattr(cliente, 'rut', None),
            canal='AUTO',
        )
        cuenta.anio_ultimo_bono_cumpleanos = anio_actual
        cuenta.save(update_fields=['anio_ultimo_bono_cumpleanos', 'updated_at'])

    logger.info('Bono cumpleaños cliente=%s puntos=%s año=%s',
                cliente.id, programa.puntos_cumpleanos, anio_actual)
    return {'puntos': programa.puntos_cumpleanos, 'saldo_total': cuenta.saldo_puntos}


def reversar_venta(ticket, *, usuario=None):
    """
    Reversa los puntos acumulados por una venta que se anuló/devolvió.
    Descuenta del cliente los puntos que esa venta otorgó. Idempotente.
    Devuelve los puntos reversados (0 si no había acumulación).
    """
    acum = MovimientoPuntos.objects.filter(
        ticket=ticket, tipo='ACUMULACION'
    ).first()
    if not acum:
        return 0
    idem = f"reversa_pts:{ticket.id}"
    with transaction.atomic():
        if MovimientoPuntos.objects.filter(idempotency_key=idem).exists():
            return 0
        cuenta = CuentaPuntos.objects.select_for_update().get(pk=acum.cuenta_id)
        a_revertir = min(acum.puntos, cuenta.saldo_puntos)
        if a_revertir <= 0:
            # Registrar el intento para idempotencia aunque no haya saldo.
            return 0
        cuenta.saldo_puntos -= a_revertir
        MovimientoPuntos.objects.create(
            cuenta=cuenta,
            tipo='REVERSA',
            puntos=-a_revertir,
            saldo_resultante=cuenta.saldo_puntos,
            ticket=ticket,
            usuario=usuario,
            idempotency_key=idem,
            observaciones=f'Reversa por anulación/devolución de venta {ticket.correlativo}',
            rut_cliente=acum.rut_cliente,
            canal='POS',
        )
        cuenta.save(update_fields=['saldo_puntos', 'updated_at'])
    logger.info("Puntos reversados ticket=%s puntos=%s", ticket.correlativo, a_revertir)
    return a_revertir


def expirar_lotes_vencidos(usuario=None):
    """
    Crea movimientos EXPIRACION para los lotes vencidos con saldo > 0.
    Devuelve el total de puntos expirados. Usado por el command `expirar_puntos`.
    """
    hoy = timezone.localdate()
    total_expirado = 0
    # Lotes positivos vencidos cuyo consumo aún no cubre todo el lote.
    lotes = MovimientoPuntos.objects.filter(
        puntos__gt=0,
        fecha_expiracion__lt=hoy,
        puntos_consumidos_del_lote__lt=F('puntos'),
    )
    for lote in lotes.iterator():
        disponible = lote.puntos - lote.puntos_consumidos_del_lote
        if disponible <= 0:
            continue
        with transaction.atomic():
            lote_locked = MovimientoPuntos.objects.select_for_update().get(pk=lote.pk)
            disponible = lote_locked.puntos - lote_locked.puntos_consumidos_del_lote
            if disponible <= 0:
                continue
            cuenta = CuentaPuntos.objects.select_for_update().get(pk=lote_locked.cuenta_id)
            lote_locked.puntos_consumidos_del_lote += disponible
            lote_locked.save(update_fields=['puntos_consumidos_del_lote'])
            cuenta.saldo_puntos = max(0, cuenta.saldo_puntos - disponible)
            MovimientoPuntos.objects.create(
                cuenta=cuenta,
                tipo='EXPIRACION',
                puntos=-disponible,
                saldo_resultante=cuenta.saldo_puntos,
                lote_origen=lote_locked,
                usuario=usuario,
                observaciones=f'Expiración de lote del {lote_locked.fecha.date()}',
                rut_cliente=lote_locked.rut_cliente,
                canal='AUTO',
            )
            cuenta.save(update_fields=['saldo_puntos', 'updated_at'])
            total_expirado += disponible
    return total_expirado


def consultar_saldo(cliente=None, rut=None):
    """
    Devuelve dict con saldo, valor en pesos y puntos por vencer (próximos 30
    días) del cliente. Si no hay cuenta, saldo 0.
    """
    if cliente is None and rut:
        cliente = resolver_cliente_por_rut(rut)
    if not cliente:
        return {'cliente': None, 'saldo_puntos': 0, 'valor_pesos': 0,
                'puntos_por_vencer': 0}

    programa = ProgramaFidelizacion.get_activo()
    cuenta = getattr(cliente, 'cuenta_puntos', None)
    if not cuenta:
        return {'cliente': cliente.nombre_completo, 'saldo_puntos': 0,
                'valor_pesos': 0, 'puntos_por_vencer': 0}

    limite = timezone.localdate() + timezone.timedelta(days=30)
    por_vencer = 0
    # TIPOS_LOTE: incluye también CUMPLEANOS/REFERIDO/DESAFIO — todos los
    # créditos expiran y deben aparecer en "por vencer".
    from app.models import TIPOS_LOTE as _TIPOS_LOTE
    lotes = MovimientoPuntos.objects.filter(
        cuenta=cuenta, tipo__in=_TIPOS_LOTE,
        fecha_expiracion__lte=limite, fecha_expiracion__gte=timezone.localdate(),
    )
    for lote in lotes:
        por_vencer += max(0, lote.puntos - lote.puntos_consumidos_del_lote)

    valor = cuenta.saldo_puntos * (programa.valor_punto_en_pesos if programa else 0)
    # Bloque de membresía para la app: umbrales y tasas del programa activo,
    # para pintar la barra de progreso de nivel sin hardcodear valores.
    membresia = None
    if programa:
        membresia = {
            'umbral_oro': int(programa.umbral_oro),
            'umbral_platino': int(programa.umbral_platino),
            'tasas': {
                'PLATA': float(programa.tasa_plata),
                'ORO': float(programa.tasa_oro),
                'PLATINO': float(programa.tasa_platino),
            },
        }
    return {
        'cliente': cliente.nombre_completo,
        'saldo_puntos': cuenta.saldo_puntos,
        'valor_pesos': valor,
        'puntos_por_vencer': por_vencer,
        'nivel': cuenta.nivel,
        'gasto_12_meses': cuenta.gasto_12_meses,
        'membresia': membresia,
    }


def registrar_cliente_manual(*, nombre, apellido='', rut, email='', celular='',
                             fecha_nacimiento=None, genero='', usuario=None,
                             empresa=None):
    """
    Alta manual de un cliente para fidelización (sin esperar a que compre).
    Valida los campos, crea/actualiza el Cliente del CRM y su CuentaPuntos
    (con bono de bienvenida si el programa lo define).

    `empresa` (opcional): empresa a la que se asocia el cliente nuevo. Si el
    cliente ya existe sin empresa, se le asigna; nunca se pisa una empresa ya
    existente.

    Devuelve (cliente, cuenta, creado_cliente: bool).
    Lanza FidelizacionError con un mensaje claro ante datos inválidos.
    """
    nombre = (nombre or '').strip()
    apellido = (apellido or '').strip()
    rut = (rut or '').strip()
    email = (email or '').strip()
    celular = (celular or '').strip()

    # Validaciones (server-side; el front valida en paralelo).
    # Mínimo histórico: solo nombre + RUT. Celular/email/fecha son opcionales
    # (muchos clientes antiguos se cargaron solo con RUT y nombre); si vienen,
    # se validan; si no, se guardan vacíos.
    if not nombre:
        raise FidelizacionError('El nombre es obligatorio.')
    if not validar_rut_chileno(rut):
        raise FidelizacionError('El RUT no es válido.')
    cel_norm = normalizar_celular(celular)
    if celular and not cel_norm:
        raise FidelizacionError('El celular no es válido (ej: +56 9 1234 5678).')
    if email and not validar_email(email):
        raise FidelizacionError('El email no tiene un formato válido.')

    rut_norm = normalizar_rut(rut)
    with transaction.atomic():
        cliente = resolver_cliente_por_rut(rut_norm)
        if cliente:
            # Actualizar con los valores nuevos que vengan; nunca pisar con vacío.
            if email:
                cliente.email = email
            if cel_norm:
                cliente.celular = cel_norm
            if fecha_nacimiento:
                cliente.fecha_nacimiento = fecha_nacimiento
            if nombre:
                cliente.nombre = nombre
            if apellido:
                cliente.apellido = apellido
            if empresa and not cliente.empresa_id:
                cliente.empresa = empresa
            if usuario:
                cliente.updated_by = usuario
            cliente.save()
            creado = False
        else:
            cliente = Cliente.objects.create(
                nombre=nombre, apellido=apellido or '-', rut=rut,
                email=email, celular=cel_norm,
                fecha_nacimiento=fecha_nacimiento or None,
                genero=(genero or None),
                empresa=empresa,
                tipo_cliente='INDIVIDUAL', activo=True,
                created_by=usuario,
                observaciones='Alta manual desde Fidelización',
            )
            creado = True
        cuenta, _ = get_or_create_cuenta(cliente, usuario=usuario)
    return cliente, cuenta, creado


# ========== REFERIDOS ("invita y gana") ==========

# Alfabeto sin caracteres ambiguos (0/O, 1/I) para dictar el código en voz.
_ALFABETO_REFERIDO = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def obtener_codigo_referido(cliente, *, usuario=None):
    """
    Devuelve el código "invita y gana" del cliente (se genera al 1er uso).
    Corre en transacción con la cuenta bloqueada: así la eventual bienvenida
    de una cuenta nueva es atómica y dos requests concurrentes del mismo
    cliente reciben EL MISMO código (sin last-write-wins).
    """
    import secrets

    with transaction.atomic():
        cuenta, _ = get_or_create_cuenta(cliente, usuario=usuario)
        cuenta = CuentaPuntos.objects.select_for_update().get(pk=cuenta.pk)
        if cuenta.codigo_referido:
            return cuenta.codigo_referido
        for _intento in range(20):
            codigo = 'MP' + ''.join(
                secrets.choice(_ALFABETO_REFERIDO) for _ in range(6)
            )
            if CuentaPuntos.objects.filter(codigo_referido=codigo).exists():
                continue
            cuenta.codigo_referido = codigo
            cuenta.save(update_fields=['codigo_referido', 'updated_at'])
            return codigo
    raise FidelizacionError('No se pudo generar tu código. Intenta de nuevo.')


def resumen_referidos(cliente, *, usuario=None):
    """Resumen para la app: mi código, invitados, ganado, y si puedo ingresar uno."""
    from app.models import Referido

    programa = ProgramaFidelizacion.get_activo()
    codigo = obtener_codigo_referido(cliente, usuario=usuario)
    refs = Referido.objects.filter(padrino=cliente)
    pagados = refs.filter(estado='PAGADO')
    ya_referido = Referido.objects.filter(ahijado=cliente).exists()
    ya_compro = MovimientoPuntos.objects.filter(
        cuenta__cliente=cliente, tipo='ACUMULACION',
    ).exists()
    return {
        'codigo': codigo,
        'total_invitados': refs.exclude(estado='ANULADO').count(),
        'invitados_pagados': pagados.count(),
        'puntos_ganados': pagados.aggregate(t=Sum('puntos_padrino'))['t'] or 0,
        'puede_ingresar_codigo': (not ya_referido) and (not ya_compro),
        'bono_padrino': int(programa.bono_referido_padrino) if programa else 0,
        'bono_ahijado': int(programa.bono_referido_ahijado) if programa else 0,
        'activo': bool(programa and programa.bono_referido_padrino > 0),
    }


def aplicar_codigo_referido(cliente, codigo, *, usuario=None):
    """
    El cliente (ahijado) ingresa el código de quien lo invitó. NO paga bonos
    todavía: el vínculo queda REGISTRADO y los bonos se pagan automáticamente
    con su PRIMERA compra (ver `_pagar_referido_si_corresponde`).
    """
    from app.models import Referido

    programa = ProgramaFidelizacion.get_activo()
    if not programa or programa.bono_referido_padrino <= 0:
        raise FidelizacionError('El programa de referidos no está activo.')
    codigo = (codigo or '').strip().upper()
    if not codigo:
        raise FidelizacionError('Ingresa un código.')

    cuenta_padrino = (
        CuentaPuntos.objects.select_related('cliente')
        .filter(codigo_referido=codigo)
        .first()
    )
    if not cuenta_padrino:
        raise FidelizacionError('Ese código no existe. Revísalo e intenta de nuevo.')
    padrino = cuenta_padrino.cliente
    if padrino.id == cliente.id:
        raise FidelizacionError('No puedes usar tu propio código.')
    if Referido.objects.filter(ahijado=cliente).exists():
        raise FidelizacionError('Ya ingresaste un código de invitación.')
    if MovimientoPuntos.objects.filter(
        cuenta__cliente=cliente, tipo='ACUMULACION',
    ).exists():
        raise FidelizacionError(
            'Los códigos de invitación son solo para clientes '
            'que aún no hacen su primera compra.'
        )
    # Anti-farmeo: clientes con compras HISTÓRICAS (previas al programa de
    # puntos, sin movimientos en el ledger) tampoco cuentan como "nuevos".
    from app.models import Ticket as _Ticket
    if _Ticket.objects.filter(cliente=cliente).exists():
        raise FidelizacionError(
            'Los códigos de invitación son solo para clientes nuevos.'
        )

    try:
        Referido.objects.create(ahijado=cliente, padrino=padrino)
    except IntegrityError:
        # Carrera: dos requests simultáneos del mismo cliente (OneToOne).
        raise FidelizacionError('Ya ingresaste un código de invitación.')
    logger.info("Referido registrado padrino=%s ahijado=%s", padrino.id, cliente.id)
    return {
        'padrino': padrino.nombre_completo,
        'bono_ahijado': int(programa.bono_referido_ahijado),
        'mensaje': (
            f'¡Listo! Con tu primera compra recibirás '
            f'{int(programa.bono_referido_ahijado)} puntos de regalo.'
        ),
    }


def _pagar_referido_si_corresponde(cliente, programa, *, usuario=None):
    """
    Si `cliente` es un ahijado con Referido REGISTRADO, paga ambos bonos
    (idempotente por referido, tipo REFERIDO en el ledger). Se invoca tras una
    acumulación exitosa y NUNCA lanza: si falla, queda REGISTRADO y se paga
    con la siguiente compra.
    """
    from app.models import Referido

    try:
        ref = (
            Referido.objects.select_related('padrino')
            .filter(ahijado=cliente, estado='REGISTRADO')
            .first()
        )
        if not ref or not programa:
            return
        bono_p = int(programa.bono_referido_padrino or 0)
        bono_a = int(programa.bono_referido_ahijado or 0)
        with transaction.atomic():
            ref_lock = Referido.objects.select_for_update().get(pk=ref.pk)
            if ref_lock.estado != 'REGISTRADO':
                return
            cuenta_a, _ = get_or_create_cuenta(
                cliente, programa=programa, usuario=usuario,
            )
            cuenta_p, _ = get_or_create_cuenta(
                ref_lock.padrino, programa=programa, usuario=usuario,
            )
            # Bloquear AMBAS cuentas en orden de pk: evita deadlock AB-BA si
            # dos referidos mutuos pagan simultáneamente en dos cajas.
            bloqueadas = {
                c.pk: c
                for c in CuentaPuntos.objects.select_for_update().filter(
                    pk__in=[cuenta_a.pk, cuenta_p.pk],
                ).order_by('pk')
            }
            cuenta_a = bloqueadas[cuenta_a.pk]
            cuenta_p = bloqueadas[cuenta_p.pk]
            if bono_a > 0:
                _otorgar(
                    cuenta_a, 'REFERIDO', bono_a,
                    programa=programa, usuario=usuario,
                    idempotency_key=f'ref-ahijado:{ref_lock.pk}',
                    observaciones=(
                        f'Bono de invitación (te invitó '
                        f'{ref_lock.padrino.nombre_completo})'
                    ),
                    rut_cliente=getattr(cliente, 'rut', None),
                    canal='AUTO',
                )
            if bono_p > 0:
                _otorgar(
                    cuenta_p, 'REFERIDO', bono_p,
                    programa=programa, usuario=usuario,
                    idempotency_key=f'ref-padrino:{ref_lock.pk}',
                    observaciones=f'Bono por invitar a {cliente.nombre_completo}',
                    rut_cliente=getattr(ref_lock.padrino, 'rut', None),
                    canal='AUTO',
                )
            ref_lock.estado = 'PAGADO'
            ref_lock.puntos_padrino = bono_p
            ref_lock.puntos_ahijado = bono_a
            ref_lock.pagado_at = timezone.now()
            ref_lock.save(update_fields=[
                'estado', 'puntos_padrino', 'puntos_ahijado', 'pagado_at',
            ])
        logger.info(
            "Bonos de referido pagados ref=%s padrino=%s ahijado=%s",
            ref_lock.pk, ref_lock.padrino_id, cliente.id,
        )
    except Exception:
        logger.exception("No se pudo pagar bono de referido cliente=%s", cliente.id)


# ========== DESAFÍOS / PROMOCIONES ==========

def _progreso_desafio(desafio, cliente):
    """Progreso del cliente en un desafío (nº de compras o $ acumulado)."""
    # Tickets con REVERSA (venta anulada/devuelta) no cuentan para la meta.
    tickets_reversados = MovimientoPuntos.objects.filter(
        cuenta__cliente=cliente, tipo='REVERSA', ticket__isnull=False,
    ).values('ticket_id')
    movs = MovimientoPuntos.objects.filter(
        cuenta__cliente=cliente,
        tipo='ACUMULACION',
        fecha__date__gte=desafio.fecha_inicio,
        fecha__date__lte=desafio.fecha_fin,
    ).exclude(ticket_id__in=tickets_reversados)
    if desafio.tipo == 'COMPRAS_N':
        return movs.count()
    # MONTO_ACUMULADO: suma de los tickets asociados a esas acumulaciones.
    return int(movs.aggregate(t=Sum('ticket__total'))['t'] or 0)


def _desafios_vigentes():
    from app.models import DesafioPromo

    hoy = timezone.localdate()
    return DesafioPromo.objects.filter(
        activo=True, fecha_inicio__lte=hoy, fecha_fin__gte=hoy,
    )


def desafios_cliente(cliente):
    """
    Desafíos vigentes aplicables al cliente, con su progreso y estado,
    para la app (GET desafios/).
    """
    cuenta = getattr(cliente, 'cuenta_puntos', None)
    nivel = cuenta.nivel if cuenta else 'PLATA'
    resultados = []
    for d in _desafios_vigentes():
        if d.nivel_objetivo and d.nivel_objetivo != nivel:
            continue
        valor = _progreso_desafio(d, cliente)
        pagado = MovimientoPuntos.objects.filter(
            idempotency_key=f'desafio:{d.id}:{cliente.id}',
        ).exists()
        resultados.append({
            'id': d.id,
            'nombre': d.nombre,
            'descripcion': d.descripcion,
            'tipo': d.tipo,
            'meta': d.meta_valor,
            'valor_actual': min(valor, d.meta_valor),
            'bono_puntos': d.bono_puntos,
            'fecha_fin': d.fecha_fin.isoformat(),
            'completado': valor >= d.meta_valor,
            'pagado': pagado,
        })
    return resultados


def _pagar_desafios_si_corresponde(cliente, programa, *, usuario=None):
    """
    Tras una acumulación: paga los bonos de desafíos que el cliente acaba de
    completar (idempotente por `desafio:{id}:{cliente_id}`). Nunca lanza.
    """
    try:
        cuenta_ref = getattr(cliente, 'cuenta_puntos', None)
        nivel = cuenta_ref.nivel if cuenta_ref else 'PLATA'
        for d in _desafios_vigentes().filter(bono_puntos__gt=0):
            if d.nivel_objetivo and d.nivel_objetivo != nivel:
                continue
            idem = f'desafio:{d.id}:{cliente.id}'
            if MovimientoPuntos.objects.filter(idempotency_key=idem).exists():
                continue
            if _progreso_desafio(d, cliente) < d.meta_valor:
                continue
            with transaction.atomic():
                cuenta, _ = get_or_create_cuenta(
                    cliente, programa=programa, usuario=usuario,
                )
                cuenta = CuentaPuntos.objects.select_for_update().get(pk=cuenta.pk)
                try:
                    _otorgar(
                        cuenta, 'DESAFIO', d.bono_puntos,
                        programa=programa, usuario=usuario,
                        idempotency_key=idem,
                        observaciones=f'Desafío completado: {d.nombre}',
                        rut_cliente=getattr(cliente, 'rut', None),
                        canal='AUTO',
                    )
                except IntegrityError:
                    continue  # carrera: otro proceso ya lo pagó
            logger.info(
                "Bono de desafío pagado desafio=%s cliente=%s puntos=%s",
                d.id, cliente.id, d.bono_puntos,
            )
    except Exception:
        logger.exception("No se pudo evaluar desafíos cliente=%s", cliente.id)
