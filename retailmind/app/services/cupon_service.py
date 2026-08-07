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
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from app.models import (
    CampanaCupon,
    Cliente,
    CuponCliente,
    calcular_descuento_cupon,
    normalizar_codigo_publico,
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


def _generar_codigo_cupon(longitud=8, prefijo='DC'):
    """Código de cupón ininteligible y legible: 'DC-XXXXXXXX' (secrets, no random)."""
    cuerpo = ''.join(secrets.choice(_CUPON_ALFABETO) for _ in range(longitud))
    return f'{prefijo}-{cuerpo}'


def _codigo_unico(generador, intentos=5):
    """Reintenta ante la rarísima colisión del unique de BD."""
    codigo = generador()
    for _ in range(intentos):
        if not CuponCliente.objects.filter(codigo=codigo).exists():
            return codigo
        codigo = generador()
    return codigo


# ========== RESOLUCIÓN DEL CÓDIGO TECLEADO EN CAJA ==========

def resolver_codigo(codigo):
    """
    Traduce lo que el cajero tecleó a `(cupon, campana_publica)`.

    Hay DOS clases de código conviviendo y el cajero no distingue entre ellas —
    escribe lo que el cliente le muestra y punto:

      - Nominativo (`DC-XXXXXXXX`): existe como fila `CuponCliente`, se emitió a
        una persona concreta. Devuelve `(cupon, None)`.
      - Público (`GISE2438`): vive en `CampanaCupon.codigo_publico`, no hay fila
        previa; se materializa recién al canjear. Devuelve `(None, campana)`.

    Devuelve `(None, None)` si no es ninguno de los dos.

    El orden importa: primero el nominativo. Si alguien creara una campaña cuyo
    código público colisiona con un cupón ya emitido, gana el cupón individual
    (es el que tiene dueño, y romperlo sería peor).
    """
    codigo = (codigo or '').strip().upper()
    if not codigo:
        return None, None

    cupon = CuponCliente.objects.select_related('cliente', 'campana').filter(
        codigo=codigo).first()
    if cupon is not None:
        return cupon, None

    normalizado = normalizar_codigo_publico(codigo)
    if not normalizado:
        return None, None

    campana = CampanaCupon.objects.select_related('empresa').filter(
        codigo_publico=normalizado).first()
    return None, campana


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
    if campana.es_codigo_publico:
        raise CuponError(
            f'La campaña "{campana.nombre}" usa el código público '
            f'{campana.codigo_publico}: no se emiten cupones uno por uno. '
            f'Basta con que el cliente lo presente en caja.'
        )
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

        # Serializa las emisiones de ESTA campaña. El constraint parcial de BD
        # sólo cubre "dos vivos a la vez"; el límite UNICO mira también los ya
        # canjeados, y eso no se puede expresar como constraint (depende de la
        # configuración de la campaña). El lock da la garantía que falta.
        # Es backoffice, no el hot path del POS: serializar acá no cuesta nada.
        campana = CampanaCupon.objects.select_for_update().get(pk=campana.pk)

        # Expira lo vencido ANTES de mirar si hay uno vivo: si no, un cupón que
        # caducó hace un mes bloquearía la emisión de uno nuevo para siempre.
        _expirar_cupones_de_cliente(campana, cliente)

        if campana.limite_por_cliente == 'UNICO':
            # "Una vez en la vida": cuenta cualquier cupón previo, usado o no.
            # Sólo los ANULADOS no cuentan (fueron un error administrativo).
            previo = CuponCliente.objects.filter(
                campana=campana, cliente=cliente,
            ).exclude(estado='ANULADO').first()
            if previo:
                detalle = {
                    'CANJEADO': f'ya lo usó el {timezone.localtime(previo.canjeado_en):%d-%m-%Y}'
                                if previo.canjeado_en else 'ya lo usó',
                    'EXPIRADO': 'se le venció sin usarlo',
                    'PENDIENTE': f'lo tiene sin usar, vence '
                                 f'{timezone.localtime(previo.expira_en):%d-%m-%Y}',
                }.get(previo.estado, previo.estado)
                raise CuponError(
                    f'Esta campaña entrega un solo cupón por cliente y este cliente '
                    f'ya recibió el suyo ({previo.codigo}: {detalle}).'
                )
        elif campana.limite_por_cliente == 'VIVO':
            vivo = CuponCliente.objects.filter(
                campana=campana, cliente=cliente, estado='PENDIENTE',
            ).first()
            if vivo:
                raise CuponError(
                    f'El cliente ya tiene un cupón sin usar de esta campaña '
                    f'({vivo.codigo}, vence {timezone.localtime(vivo.expira_en):%d-%m-%Y}).'
                )

        # Código único: reintenta ante la rarísima colisión (unique en BD).
        codigo = _codigo_unico(_generar_codigo_cupon)

        cupon = CuponCliente.objects.create(
            campana=campana,
            cliente=cliente,
            rut_cliente=normalizar_rut(cliente.rut),
            codigo=codigo,
            origen='EMITIDO',
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
    cupon, campana_publica = resolver_codigo(codigo)

    if cupon is None and campana_publica is not None:
        return _validar_codigo_publico(
            campana_publica,
            monto=monto,
            rut_cliente=rut_cliente,
            sucursal=sucursal,
            tiene_dcto_manual=tiene_dcto_manual,
            tiene_vale_puntos=tiene_vale_puntos,
        )

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


def resolver_cliente_para_codigo_publico(rut):
    """
    Cliente ACTIVO del CRM dueño de ese RUT, o None.

    El código público exige ficha porque es la decisión de negocio (07-ago): sin
    Cliente no hay a quién atribuirle el canje, ni forma de contar cuántas veces
    lo usó ese RUT, ni de medir la campaña.

    Se reusa `resolver_cliente_por_rut` (fidelizacion_service), que ya acota el
    queryset por cuerpo del RUT y confirma normalizando en Python — `Cliente.rut`
    no tiene formato garantizado (hay fichas con puntos, sin puntos y con 'K'
    minúscula), así que un filtro exacto en SQL se pierde la mitad de los casos.

    OJO: se exige que la ficha EXISTA y esté activa, NO que esté completa. Pedir
    email/celular acá bloquearía la venta en el mostrador; ese requisito es de la
    EMISIÓN nominativa, donde hay que entregarle el código a alguien. Acá el
    cliente ya llegó con el código en la mano.
    """
    from app.services.fidelizacion_service import resolver_cliente_por_rut

    cliente = resolver_cliente_por_rut(rut)
    if cliente is None or not getattr(cliente, 'activo', False):
        return None
    return cliente


def _validar_codigo_publico(campana, *, monto=0, rut_cliente=None, sucursal=None,
                            tiene_dcto_manual=False, tiene_vale_puntos=False):
    """
    Valida un CÓDIGO PÚBLICO para una venta concreta, sin escribir nada.

    Mismas reglas que el nominativo salvo la que no aplica: no hay "dueño" del
    código, así que en vez de comparar el RUT contra el cupón se comprueba que el
    RUT sea de una persona con ficha y que ese RUT no haya agotado su cuota
    (`limite_por_cliente`). Esa cuota es toda la protección de un código
    compartido: sin ella, el mismo código circulando por WhatsApp se usa infinitas
    veces.
    """
    base = {
        'codigo': campana.codigo_publico,
        'cliente_nombre': '',
        'campana': campana.nombre,
        'tipo_valor': campana.tipo_valor,
        'valor': float(campana.valor),
        'es_codigo_publico': True,
    }

    # --- Exclusión con otros beneficios (regla dura, igual que el nominativo) ---
    if tiene_dcto_manual:
        return _rechazo(
            'DCTO_MANUAL',
            'No se puede combinar con un descuento manual. '
            'Quite el descuento o el código.', **base)
    if tiene_vale_puntos:
        return _rechazo(
            'VALE_ACTIVO',
            'Esta venta ya tiene un vale de puntos. Es uno u otro: '
            'quite el vale para usar el código.', **base)

    # --- Vigencia de la campaña ---
    if not campana.activo:
        return _rechazo('INACTIVA', 'Esta promoción está desactivada.', **base)
    hoy = timezone.localdate()
    if hoy < campana.fecha_inicio:
        return _rechazo(
            'NO_EMPEZO',
            f'Esta promoción parte el {campana.fecha_inicio:%d-%m-%Y}.', **base)
    if hoy > campana.fecha_fin:
        return _rechazo(
            'EXPIRADO',
            f'Esta promoción terminó el {campana.fecha_fin:%d-%m-%Y}.', **base)

    # --- Alcance por empresa ---
    if sucursal is not None:
        empresa_venta = getattr(sucursal, 'empresa_id', None)
        if empresa_venta and empresa_venta != campana.empresa_id:
            return _rechazo(
                'OTRA_EMPRESA',
                'Esta promoción es de otra cadena y no aplica en esta sucursal.',
                **base)

    # --- El RUT: sin él no hay forma de limitar el uso ---
    rut_venta = normalizar_rut(rut_cliente)
    if not rut_venta:
        return _rechazo(
            'SIN_CLIENTE',
            'Identifique al cliente con su RUT para usar este código.', **base)
    if rut_venta in {normalizar_rut(r) for r in RUT_FICTICIOS}:
        return _rechazo(
            'SIN_CLIENTE',
            'Este código no aplica a consumidor final: identifique al cliente '
            'con su RUT.', **base)
    if not validar_rut_chileno(rut_venta):
        return _rechazo('RUT_INVALIDO', 'El RUT de la venta no es válido.', **base)
    if es_rut_empresa(rut_venta):
        return _rechazo(
            'RUT_EMPRESA',
            'Este código es para clientes particulares, no para empresas.', **base)

    cliente = resolver_cliente_para_codigo_publico(rut_venta)
    if cliente is None:
        return _rechazo(
            'SIN_FICHA',
            'Este RUT no tiene ficha de cliente. Créela desde el POS y vuelva a '
            'ingresar el código.', **base)
    base['cliente_nombre'] = cliente.nombre_completo

    # --- Cuota por cliente: LA protección del código compartido ---
    motivo_cuota = _motivo_cuota_agotada(campana, cliente)
    if motivo_cuota:
        return _rechazo('YA_USADO', motivo_cuota, **base)

    # --- Condiciones económicas ---
    monto = int(monto or 0)
    if monto <= 0:
        return _rechazo('SIN_MONTO', 'La venta no tiene monto para descontar.', **base)
    if campana.monto_minimo and monto < campana.monto_minimo:
        falta = f"${campana.monto_minimo:,}".replace(',', '.')
        return _rechazo(
            'MONTO_MINIMO',
            f'Este código requiere una compra mínima de {falta}.', **base)

    descuento = campana.calcular_descuento(monto)
    if descuento <= 0:
        return _rechazo('SIN_DESCUENTO',
                        'El código no genera descuento sobre este monto.', **base)

    return {'valido': True, 'motivo': '', 'motivo_codigo': '',
            'descuento_pesos': descuento, **base}


def _motivo_cuota_agotada(campana, cliente):
    """
    ¿Este cliente ya agotó su cuota en esta campaña? Devuelve el motivo en
    lenguaje de mostrador, o '' si todavía le queda.

    Se apoya en las MISMAS filas `CuponCliente` que usa el flujo nominativo: cada
    canje de código público deja una, así que el histórico es uno solo y el
    límite `UNICO` funciona igual para ambos modos.
    """
    if campana.limite_por_cliente == 'SIN_LIMITE':
        return ''

    previos = CuponCliente.objects.filter(
        campana=campana, cliente=cliente).exclude(estado='ANULADO')

    if campana.limite_por_cliente == 'UNICO':
        previo = previos.first()
        if previo is None:
            return ''
        if previo.estado == 'CANJEADO' and previo.canjeado_en:
            cuando = timezone.localtime(previo.canjeado_en).strftime('%d-%m-%Y')
            return f'Este cliente ya usó esta promoción el {cuando}.'
        return 'Este cliente ya usó esta promoción.'

    # VIVO: sólo bloquea si tiene uno sin usar (no aplica en la práctica al
    # código público —sus filas nacen CANJEADAS— pero se respeta la semántica.
    if previos.filter(estado='PENDIENTE').exists():
        return 'Este cliente ya tiene un cupón sin usar de esta promoción.'
    return ''


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

    # Código público: no hay fila que lockear todavía — el canje la CREA.
    _cupon_previo, campana_publica = resolver_codigo(codigo)
    if _cupon_previo is None and campana_publica is not None:
        return _canjear_codigo_publico(
            campana_publica, ticket=ticket, sucursal=sucursal, usuario=usuario,
            monto_descuento=monto_descuento,
        )

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


def _canjear_codigo_publico(campana, *, ticket, sucursal=None, usuario=None,
                            monto_descuento=None):
    """
    Materializa el canje de un código público: crea la fila `CuponCliente` que ES
    el registro de ese uso.

    Se crea recién acá (no al validar) por dos razones: validar no debe escribir
    nada —el POS la llama en cada tecla— y así la fila sólo existe si la venta
    efectivamente se cobró.

    Serializa por campaña con `select_for_update`: el chequeo de cuota y la
    creación tienen que ser atómicos o dos cajas cobrando al mismo cliente en el
    mismo segundo le dejan usar dos veces un código de límite UNICO. Es
    backoffice de un canje puntual, no el hot path del POS.
    """
    if ticket is None:
        raise CuponError('Falta el ticket para canjear el código.')

    monto = int(monto_descuento or 0)
    if monto <= 0:
        raise CuponError('El descuento del cupón debe ser mayor a 0.')

    resultado = None
    error = None
    with transaction.atomic():
        campana = CampanaCupon.objects.select_for_update().get(pk=campana.pk)

        # Idempotencia: el reintento del POS sobre el MISMO ticket no crea una
        # segunda fila (y el constraint `uniq_cupon_canjeado_por_ticket` lo
        # atajaría igual, pero con un 500 en vez de una respuesta correcta).
        ya = CuponCliente.objects.filter(
            campana=campana, ticket=ticket, estado='CANJEADO').first()
        if ya is not None:
            return ya

        cliente = resolver_cliente_para_codigo_publico(ticket.cliente_rut)
        if cliente is None:
            error = ('Este RUT no tiene ficha de cliente: no se puede registrar '
                     'el uso del código.')
        else:
            motivo_cuota = _motivo_cuota_agotada(campana, cliente)
            if motivo_cuota:
                error = motivo_cuota
            else:
                # `expira_en` es NOT NULL y la fila nace ya canjeada, así que no
                # gobierna ninguna decisión: se fija al cierre de la campaña
                # porque es la fecha honesta (hasta cuándo ese código valía).
                expira = timezone.make_aware(
                    datetime.combine(campana.fecha_fin, time.max),
                    timezone.get_current_timezone(),
                )

                resultado = CuponCliente.objects.create(
                    campana=campana,
                    cliente=cliente,
                    rut_cliente=normalizar_rut(cliente.rut),
                    codigo=_codigo_unico(
                        lambda: _generar_codigo_cupon(
                            longitud=4, prefijo=campana.codigo_publico)),
                    origen='PUBLICO',
                    empresa=campana.empresa,
                    tipo_valor=campana.tipo_valor,
                    valor=campana.valor,
                    tope_descuento=campana.tope_descuento,
                    monto_minimo=campana.monto_minimo,
                    estado='CANJEADO',
                    expira_en=expira,
                    ticket=ticket,
                    monto_descuento=monto,
                    sucursal_canje=sucursal,
                    usuario_canje=usuario,
                    canjeado_en=timezone.now(),
                    created_by=usuario,
                )

    if error:
        raise CuponError(error)

    logger.info(
        'Código público canjeado codigo=%s campana=%s cliente=%s ticket=%s '
        'descuento=%s sucursal=%s',
        resultado.codigo, campana.id, resultado.cliente_id,
        getattr(ticket, 'correlativo', None), monto, getattr(sucursal, 'id', None),
    )
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
                if c.origen == 'PUBLICO':
                    # Un canje de código público NO puede volver a PENDIENTE: su
                    # `codigo` es interno ("GISE2438-XK7M"), el cliente nunca lo
                    # vio y no podría presentarlo. Lo que hay que devolverle es su
                    # CUOTA sobre el código publicado, y eso se logra sacando la
                    # fila del conteo — ANULADO es el único estado que
                    # `_motivo_cuota_agotada` excluye. Vuelve a poder usar
                    # "GISE2438" mientras la campaña siga viva.
                    c.estado = 'ANULADO'
                else:
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
