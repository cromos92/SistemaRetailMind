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

from django.db import transaction, IntegrityError
from django.db.models import F, Q
from django.utils import timezone

from app.models import (
    Cliente,
    CuentaPuntos,
    MovimientoPuntos,
    ProgramaFidelizacion,
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
            )
    return cuenta, creada


def _otorgar(cuenta, tipo, puntos, *, programa=None, ticket=None, sucursal=None,
             usuario=None, idempotency_key=None, observaciones=''):
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
    )
    cuenta.saldo_puntos = nuevo_saldo
    cuenta.save(update_fields=['saldo_puntos', 'updated_at'])
    return mov


def _consumir_lotes_fifo(cuenta, puntos, tipo, *, ticket=None, sucursal=None,
                         usuario=None, idempotency_key=None, observaciones=''):
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
        )
        movimientos.append(mov)
        restante -= tomar

    cuenta.save(update_fields=['saldo_puntos', 'updated_at'])
    return movimientos, restante


def acumular_puntos_por_venta(ticket, usuario=None):
    """
    Hook de cobro: acumula puntos por una venta pagada.

    - Resuelve el Cliente por `ticket.cliente_rut`. Si no hay cliente
      identificado → venta anónima → no acumula (devuelve None sin error).
    - Setea `ticket.cliente` si lo encuentra.
    - Idempotente por `acum:{ticket.id}`.

    Devuelve dict {puntos_ganados, saldo_total} o None.
    """
    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        return None

    cliente = resolver_cliente_por_rut(getattr(ticket, 'cliente_rut', ''))
    if not cliente:
        return None  # venta anónima → no acumula

    # Enlazar el ticket al cliente (trazabilidad), sin romper si ya estaba.
    if getattr(ticket, 'cliente_id', None) != cliente.id:
        ticket.cliente = cliente
        try:
            ticket.save(update_fields=['cliente'])
        except Exception:
            logger.exception("No se pudo setear ticket.cliente ticket=%s", ticket.id)

    base = ticket.total or 0
    puntos = programa.calcular_puntos(base)
    if puntos <= 0:
        # Igual aseguramos cuenta + bienvenida para el cliente nuevo.
        get_or_create_cuenta(cliente, programa=programa, usuario=usuario)
        return {'puntos_ganados': 0, 'saldo_total': cliente.cuenta_puntos.saldo_puntos}

    idem = f"acum:{ticket.id}"
    with transaction.atomic():
        existente = MovimientoPuntos.objects.filter(idempotency_key=idem).first()
        if existente:
            cuenta = CuentaPuntos.objects.get(pk=existente.cuenta_id)
            return {'puntos_ganados': existente.puntos, 'saldo_total': cuenta.saldo_puntos}

        cuenta, _ = get_or_create_cuenta(cliente, programa=programa, usuario=usuario)
        cuenta = CuentaPuntos.objects.select_for_update().get(pk=cuenta.pk)
        try:
            mov = _otorgar(
                cuenta, 'ACUMULACION', puntos,
                programa=programa, ticket=ticket,
                sucursal=getattr(ticket, 'sucursal', None), usuario=usuario,
                idempotency_key=idem,
                observaciones=f'Compra ticket {ticket.correlativo} (${base:,})',
            )
        except IntegrityError:
            mov = MovimientoPuntos.objects.filter(idempotency_key=idem).first()
            if not mov:
                raise

    logger.info("Puntos acumulados cliente=%s ticket=%s puntos=%s saldo=%s",
                cliente.id, ticket.correlativo, puntos, cuenta.saldo_puntos)
    return {'puntos_ganados': puntos, 'saldo_total': cuenta.saldo_puntos}


def canjear_puntos(cliente, puntos, *, ticket=None, sucursal=None, usuario=None,
                   idempotency_key=None):
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
        )
    valor = puntos * programa.valor_punto_en_pesos
    return {'puntos_canjeados': puntos, 'valor_pesos': valor,
            'saldo_total': cuenta.saldo_puntos}


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
        if puntos > 0:
            _otorgar(cuenta, 'AJUSTE', puntos, usuario=usuario,
                     observaciones=observaciones or 'Ajuste manual (+)')
        else:
            if cuenta.saldo_puntos < abs(puntos):
                raise FidelizacionError('Saldo insuficiente para el ajuste negativo.')
            _consumir_lotes_fifo(cuenta, abs(puntos), 'AJUSTE', usuario=usuario,
                                 observaciones=observaciones or 'Ajuste manual (-)')
    return cuenta.saldo_puntos


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
    lotes = MovimientoPuntos.objects.filter(
        cuenta=cuenta, tipo__in=('ACUMULACION', 'BIENVENIDA'),
        fecha_expiracion__lte=limite, fecha_expiracion__gte=timezone.localdate(),
    )
    for lote in lotes:
        por_vencer += max(0, lote.puntos - lote.puntos_consumidos_del_lote)

    valor = cuenta.saldo_puntos * (programa.valor_punto_en_pesos if programa else 0)
    return {
        'cliente': cliente.nombre_completo,
        'saldo_puntos': cuenta.saldo_puntos,
        'valor_pesos': valor,
        'puntos_por_vencer': por_vencer,
    }


def registrar_cliente_manual(*, nombre, apellido='', rut, email='', celular='',
                             fecha_nacimiento=None, genero='', usuario=None):
    """
    Alta manual de un cliente para fidelización (sin esperar a que compre).
    Valida los campos, crea/actualiza el Cliente del CRM y su CuentaPuntos
    (con bono de bienvenida si el programa lo define).

    Devuelve (cliente, cuenta, creado_cliente: bool).
    Lanza FidelizacionError con un mensaje claro ante datos inválidos.
    """
    nombre = (nombre or '').strip()
    apellido = (apellido or '').strip()
    rut = (rut or '').strip()
    email = (email or '').strip()
    celular = (celular or '').strip()

    # Validaciones (server-side; el front valida en paralelo).
    if not nombre:
        raise FidelizacionError('El nombre es obligatorio.')
    if not validar_rut_chileno(rut):
        raise FidelizacionError('El RUT no es válido.')
    cel_norm = normalizar_celular(celular)
    if not cel_norm:
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
                tipo_cliente='INDIVIDUAL', activo=True,
                created_by=usuario,
                observaciones='Alta manual desde Fidelización',
            )
            creado = True
        cuenta, _ = get_or_create_cuenta(cliente, usuario=usuario)
    return cliente, cuenta, creado
