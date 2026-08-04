"""
Módulo Fidelización (puntos) - RetailMind

Configuración del programa, cuentas de puntos por cliente, ficha con historial,
ajustes manuales y reportes. Vistas HTML + APIs JSON, function-based.
La lógica vive en `app/services/fidelizacion_service.py`.
"""
import json
import logging
import os
from datetime import datetime, time

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import F, Q, Sum, Count, Min, Max
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date

from .decorators import requiere_permiso, requiere_alguno_de_los_permisos
from .models import (
    Cliente, CuentaPuntos, MovimientoPuntos, ProgramaFidelizacion,
    Empresa, EmpresaUser, GiftCard, Ticket, TIPO_CLIENTE_CHOICES,
    validar_rut_chileno,
)
from .services import fidelizacion_service, giftcard_service
from .utils_permisos import usuario_puede_ver_todas_sucursales

logger = logging.getLogger('app')

# Tope de un ajuste manual de puntos. Los puntos se canjean por vales y el vale
# es descuento real en caja, así que un ajuste sin límite equivale a emitir
# dinero desde el back-office. Configurable por entorno.
PUNTOS_MAXIMO_AJUSTE_MANUAL = int(os.environ.get('FIDELIZACION_AJUSTE_MAX_PUNTOS', '50000'))

# Chip de tipo de cliente: color de fondo, color de texto e icono Remix.
# Se usan SOLO colores ya presentes en el módulo (paleta Velzon/NEXO), para que
# el POS y las pantallas de fidelización pinten el mismo chip sin inventar
# paletas nuevas ni duplicar el mapa en cada template.
TIPO_CLIENTE_ESTILOS = {
    'INDIVIDUAL':      {'bg': '#eef2ff', 'color': '#405189', 'icono': 'ri-user-line'},
    'EMPRESARIAL':     {'bg': '#e3f2fd', 'color': '#299cdb', 'icono': 'ri-building-line'},
    'MAYORISTA':       {'bg': '#fff8e1', 'color': '#f9a825', 'icono': 'ri-store-2-line'},
    'DISTRIBUIDOR':    {'bg': '#f3e5f5', 'color': '#8e24aa', 'icono': 'ri-truck-line'},
    'EMPLEADO':        {'bg': '#e8f5e9', 'color': '#0ab39c', 'icono': 'ri-user-star-line'},
    'CREDITO_EXTERNO': {'bg': '#ffe9e5', 'color': '#f06548', 'icono': 'ri-bank-card-line'},
}
_TIPO_CLIENTE_LABELS = dict(TIPO_CLIENTE_CHOICES)


def _empresa_ids_usuario(usuario):
    """IDs de empresas a las que el usuario tiene acceso (vía EmpresaUser)."""
    return list(
        EmpresaUser.objects
        .filter(user=usuario, status=True, empresa__isnull=False)
        .values_list('empresa_id', flat=True)
        .distinct()
    )


def _empresa_actual(request):
    """Empresa activa en sesión (para asociar clientes nuevos)."""
    eid = request.session.get('idEmpresaActual') or request.session.get('empresaActual')
    if not eid:
        return None
    return Empresa.objects.filter(id=eid).first()


def _cliente_en_alcance(usuario, cliente):
    """
    ¿El cliente pertenece al alcance multi-empresa del usuario?

    Mismo criterio que `api_listar_cuentas`: quien ve todas las sucursales ve
    todo; el resto ve los clientes de SUS empresas más los que no tienen
    empresa asignada (la mayoría del histórico migrado).
    """
    if cliente is None:
        return False
    if usuario_puede_ver_todas_sucursales(usuario):
        return True
    if cliente.empresa_id is None:
        return True
    return cliente.empresa_id in _empresa_ids_usuario(usuario)


def _info_tipo_cliente(cliente):
    """Tipo de cliente + estilo del chip (bg/color/icono), listo para pintar."""
    tipo = (getattr(cliente, 'tipo_cliente', '') or 'INDIVIDUAL').upper()
    estilo = TIPO_CLIENTE_ESTILOS.get(tipo, TIPO_CLIENTE_ESTILOS['INDIVIDUAL'])
    return {
        'tipo_cliente': tipo,
        'tipo_cliente_display': _TIPO_CLIENTE_LABELS.get(tipo, tipo),
        'tipo_cliente_bg': estilo['bg'],
        'tipo_cliente_color': estilo['color'],
        'tipo_cliente_icono': estilo['icono'],
    }


def giftcards_vigentes_cliente(cliente, limite=10):
    """
    Gift cards utilizables HOY del cliente (activas, con saldo y no vencidas).

    Las gift cards son globales en la cadena por diseño (ver
    `app/models/giftcards.py`), así que NO se filtran por empresa. El detalle de
    cada tarjeta lo arma `giftcard_service.consultar_saldo`, que es la única
    fuente de verdad del estado/validez: aquí solo se acota el universo.
    """
    if cliente is None:
        return []
    hoy = timezone.localdate()
    qs = (
        GiftCard.objects
        .filter(cliente=cliente, estado='ACTIVA', saldo_actual__gt=0)
        .filter(Q(fecha_vencimiento__isnull=True) | Q(fecha_vencimiento__gte=hoy))
        .order_by('fecha_vencimiento', '-saldo_actual')[:limite]
    )
    tarjetas = []
    for gc in qs:
        try:
            tarjetas.append(giftcard_service.consultar_saldo(gc.codigo))
        except giftcard_service.GiftCardError:
            # Carrera con una anulación/consumo: se ignora la tarjeta.
            continue
    return tarjetas


def construir_ficha_rapida(identificador, *, usuario=None):
    """
    Ficha de cliente para caja, en UNA sola consulta al backend: nombre,
    TIPO DE CLIENTE, saldo de puntos, valor en pesos, puntos por vencer y gift
    cards vigentes.

    `identificador` es normalmente el RUT; si trae '@' se resuelve por correo
    (el servicio solo acepta el correo cuando es único, ver
    `resolver_cliente_por_email`).

    Reutiliza los servicios existentes y no duplica reglas:
      - `fidelizacion_service.resolver_cliente_por_identificador` para encontrar
        al cliente, `consultar_saldo` para el saldo/nivel/por-vencer.
      - `giftcard_service.consultar_saldo` para el estado de cada tarjeta.
      - `validar_rut_chileno` (app.models.base) para el RUT.

    Alcance: el saldo de puntos se devuelve igual que antes (no se recorta un
    dato que la caja ya mostraba), pero los datos de contacto y el enlace a la
    ficha solo viajan si el cliente está en el alcance del usuario
    (`en_alcance`). Las gift cards son globales por diseño.
    """
    texto = (identificador or '').strip()
    es_email = '@' in texto
    ficha = {
        'identificador': texto,
        'rut': '' if es_email else texto,
        'rut_valido': True if es_email else bool(validar_rut_chileno(texto)),
        'cliente': None,          # nombre completo (clave histórica del POS)
        'cliente_id': None,
        'saldo_puntos': 0,
        'valor_pesos': 0,
        'puntos_por_vencer': 0,
        'nivel': None,
        'tipo_cliente': None,
        'tipo_cliente_display': '',
        'tipo_cliente_bg': '',
        'tipo_cliente_color': '',
        'tipo_cliente_icono': '',
        'giftcards': [],
        'giftcards_cantidad': 0,
        'giftcards_saldo_total': 0,
        'en_alcance': True,
        'acumula_puntos': True,
        'motivo_no_acumula': '',
    }

    if not ficha['rut_valido']:
        ficha['acumula_puntos'] = False
        ficha['motivo_no_acumula'] = 'RUT inválido'
        return ficha

    # Motivo por el que un RUT válido igualmente no fideliza (mismas reglas que
    # `venta_fideliza`, evaluadas sobre el RUT porque aún no hay ticket).
    if not es_email:
        rut_norm = fidelizacion_service.normalizar_rut(texto)
        formateado = f"{rut_norm[:-1]}-{rut_norm[-1:]}" if rut_norm else ''
        if formateado in fidelizacion_service.RUT_FICTICIOS:
            ficha['acumula_puntos'] = False
            ficha['motivo_no_acumula'] = 'RUT genérico de consumidor final'
        elif fidelizacion_service.es_rut_empresa(texto):
            ficha['acumula_puntos'] = False
            ficha['motivo_no_acumula'] = 'RUT de empresa'

    cliente = fidelizacion_service.resolver_cliente_por_identificador(texto)
    if cliente is None:
        return ficha

    info = fidelizacion_service.consultar_saldo(cliente=cliente)
    ficha.update({k: v for k, v in info.items() if k != 'cliente'})
    ficha['cliente'] = info.get('cliente') or cliente.nombre_completo
    ficha.update(_info_tipo_cliente(cliente))

    tarjetas = giftcards_vigentes_cliente(cliente)
    ficha['giftcards'] = tarjetas
    ficha['giftcards_cantidad'] = len(tarjetas)
    ficha['giftcards_saldo_total'] = sum(t.get('saldo_actual') or 0 for t in tarjetas)

    en_alcance = _cliente_en_alcance(usuario, cliente) if usuario is not None else True
    ficha['en_alcance'] = en_alcance
    if en_alcance:
        ficha['cliente_id'] = cliente.id
        ficha['rut'] = cliente.rut or ficha['rut']
        ficha['email'] = cliente.email or ''
        ficha['celular'] = cliente.celular or cliente.telefono or ''
        ficha['empresa'] = cliente.empresa.nombre if cliente.empresa else ''
        ficha['url_ficha'] = f"/app/fidelizacion/cliente/{cliente.id}/"
    return ficha


# ========== VISTAS HTML ==========

@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def modulo_fidelizacion(request):
    """Listado de clientes con puntos."""
    programa = ProgramaFidelizacion.get_activo()
    context = {
        'programa': programa,
        # Opciones del filtro por tipo de cliente + estilo del chip.
        'tipos_cliente': [
            {'valor': valor, 'label': label, **TIPO_CLIENTE_ESTILOS.get(
                valor, TIPO_CLIENTE_ESTILOS['INDIVIDUAL'])}
            for valor, label in TIPO_CLIENTE_CHOICES
        ],
    }
    return render(request, 'vistas/modulo_fidelizacion/lista.html', context)


@requiere_permiso('fidelizacion_programa', 'puede_ver')
def configurar_programa_vista(request):
    """
    Configuración del programa de puntos (solo admin).

    Además del formulario, se entrega la CARTERA REAL de saldos para que la
    pantalla pueda simular las consecuencias de cada parámetro antes de
    guardar: cuántos clientes alcanzarían el mínimo de canje, a cuánta compra
    equivale ese mínimo con cada tasa y cuánto pasivo hay hoy. Sin esto el
    programa se configura a ciegas (así se llegó a un mínimo de 6.000 pts que
    solo alcanzan 5 de 98 cuentas, 2 de ellas basura).
    """
    programa = ProgramaFidelizacion.get_activo()
    valor_pto = programa.valor_punto_en_pesos if programa else 0
    minimo = programa.minimo_canje_puntos if programa else 0
    cuentas = CuentaPuntos.objects.select_related('cliente')
    if not usuario_puede_ver_todas_sucursales(request.user):
        empresas = _empresa_ids_usuario(request.user)
        cuentas = cuentas.filter(Q(cliente__empresa__isnull=True) |
                                 Q(cliente__empresa_id__in=empresas))
    cartera = analizar_cartera_puntos(cuentas, valor_pto=valor_pto, minimo_canje=minimo)

    context = {
        'programa': programa,
        'redondeo_choices': ProgramaFidelizacion._meta.get_field('redondeo').choices,
        'acumula_choices': ProgramaFidelizacion._meta.get_field('acumula_sobre').choices,
        'cartera': cartera,
        # Escalera de saldos ELEGIBLES (orden descendente). La pantalla la usa
        # para responder en vivo "¿cuántos clientes alcanzan este mínimo?"
        # sin volver al servidor por cada tecla.
        'saldos_elegibles_json': json.dumps(cartera['saldos_elegibles']),
        'valor_punto': valor_pto,
    }
    return render(request, 'vistas/modulo_fidelizacion/configuracion.html', context)


@requiere_permiso('fidelizacion_reporte', 'puede_ver')
def reporte_fidelizacion_vista(request):
    """Reporte operativo de puntos, vencimientos, canjes y señales de abuso."""
    programa = ProgramaFidelizacion.get_activo()
    context = {'programa': programa}
    return render(request, 'vistas/modulo_fidelizacion/reporte.html', context)


@requiere_permiso('fidelizacion_cuentas', 'puede_crear')
def registrar_cliente_vista(request):
    """
    Alta manual movida a un modal dentro del listado. Esta ruta se conserva y
    redirige al listado abriendo el modal de registro.
    """
    return redirect('/app/fidelizacion/?nuevo=1')


@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def ficha_cliente_puntos_vista(request, cliente_id):
    """Ficha de cliente con saldo, tipo de cliente, gift cards e historial."""
    cliente = get_object_or_404(
        Cliente.objects.select_related('empresa', 'cuenta_puntos'), id=cliente_id,
    )
    # El id llega por URL: se valida contra el alcance multi-empresa del usuario
    # (el listado ya filtra, pero la URL es adivinable).
    if not _cliente_en_alcance(request.user, cliente):
        logger.warning(
            "Ficha de fidelización fuera de alcance: usuario=%s cliente=%s empresa=%s",
            request.user.id, cliente.id, cliente.empresa_id,
        )
        return HttpResponseForbidden('No tienes acceso a la ficha de este cliente.')

    cuenta = getattr(cliente, 'cuenta_puntos', None)
    movimientos = []
    tendencia_mensual = []
    if cuenta:
        movimientos = cuenta.movimientos.select_related('ticket').order_by('-fecha')[:200]
        programa = ProgramaFidelizacion.get_activo()
        valor_pto = programa.valor_punto_en_pesos if programa else 0
        tendencia_mensual = _construir_tendencia_mensual(cuenta=cuenta, valor_pto=valor_pto)

    giftcards = giftcards_vigentes_cliente(cliente)
    context = {
        'cliente': cliente,
        'cuenta': cuenta,
        'movimientos': movimientos,
        'tendencia_mensual': tendencia_mensual,
        'saldo_info': fidelizacion_service.consultar_saldo(cliente=cliente),
        'tipo_info': _info_tipo_cliente(cliente),
        'giftcards': giftcards,
        'giftcards_saldo_total': sum(g.get('saldo_actual') or 0 for g in giftcards),
    }
    return render(request, 'vistas/modulo_fidelizacion/ficha_cliente.html', context)


# ========== APIs JSON ==========

@require_GET
@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def api_listar_cuentas(request):
    """
    Listado paginado de TODAS las personas registradas en las empresas a las que
    el usuario tiene acceso (no solo las que ya tienen cuenta de puntos).
    Muestra su saldo de puntos (0 si aún no acumula).

    Filtros (TODOS server-side, se aplican al queryset antes de paginar, así que
    el total y las páginas son los del universo filtrado):
      - `q`            texto libre: nombre, apellido, RUT o empresa.
      - `tipo_cliente` uno de TIPO_CLIENTE_CHOICES.
      - `nivel`        PLATA / ORO / PLATINO (o varios separados por coma).
      - `puntos`       'con' (saldo > 0) | 'sin' (sin cuenta o saldo 0).
      - `saldo_min` / `saldo_max`  rango de saldo de puntos.
      - `orden`        'saldo_desc' | 'saldo_asc' | 'nombre' (default).
    """
    qs = Cliente.objects.filter(activo=True).select_related('empresa', 'cuenta_puntos')

    # Multi-empresa: el admin (o quien ve todas las sucursales) ve todo; el resto
    # ve los clientes de sus empresas + los clientes sin empresa asignada.
    if not usuario_puede_ver_todas_sucursales(request.user):
        empresas = _empresa_ids_usuario(request.user)
        qs = qs.filter(Q(empresa__isnull=True) | Q(empresa_id__in=empresas))

    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(
            Q(nombre__icontains=busqueda) |
            Q(apellido__icontains=busqueda) |
            Q(rut__icontains=busqueda) |
            Q(empresa__nombre__icontains=busqueda)
        )

    tipo_cliente = (request.GET.get('tipo_cliente') or '').strip().upper()
    if tipo_cliente in _TIPO_CLIENTE_LABELS:
        qs = qs.filter(tipo_cliente=tipo_cliente)

    # `nivel` acepta uno o varios separados por coma (ej. 'ORO,PLATINO').
    niveles = [n for n in (request.GET.get('nivel') or '').strip().upper().split(',')
               if n in ('PLATA', 'ORO', 'PLATINO')]
    if niveles:
        qs = qs.filter(cuenta_puntos__nivel__in=niveles)

    puntos = (request.GET.get('puntos') or '').strip().lower()
    if puntos == 'con':
        qs = qs.filter(cuenta_puntos__saldo_puntos__gt=0)
    elif puntos == 'sin':
        # Sin cuenta creada o con cuenta en cero: ambos son "sin puntos".
        qs = qs.filter(Q(cuenta_puntos__isnull=True) | Q(cuenta_puntos__saldo_puntos__lte=0))

    def _entero(nombre):
        valor = (request.GET.get(nombre) or '').strip()
        if valor == '':
            return None
        try:
            return int(float(valor))
        except (TypeError, ValueError):
            return None

    saldo_min = _entero('saldo_min')
    saldo_max = _entero('saldo_max')
    if saldo_min is not None:
        if saldo_min <= 0:
            # Un mínimo de 0 debe seguir incluyendo a los que no tienen cuenta.
            qs = qs.filter(Q(cuenta_puntos__isnull=True) |
                           Q(cuenta_puntos__saldo_puntos__gte=saldo_min))
        else:
            qs = qs.filter(cuenta_puntos__saldo_puntos__gte=saldo_min)
    if saldo_max is not None:
        qs = qs.filter(Q(cuenta_puntos__isnull=True) |
                       Q(cuenta_puntos__saldo_puntos__lte=saldo_max))

    # Orden explícito (la paginación sin ORDER BY estable puede repetir filas).
    orden = (request.GET.get('orden') or '').strip().lower()
    if orden == 'saldo_desc':
        qs = qs.order_by(F('cuenta_puntos__saldo_puntos').desc(nulls_last=True), 'apellido', 'nombre', 'id')
    elif orden == 'saldo_asc':
        qs = qs.order_by(F('cuenta_puntos__saldo_puntos').asc(nulls_first=True), 'apellido', 'nombre', 'id')
    else:
        qs = qs.order_by('apellido', 'nombre', 'id')

    programa = ProgramaFidelizacion.get_activo()
    valor_pto = programa.valor_punto_en_pesos if programa else 0

    try:
        per_page = max(5, min(100, int(request.GET.get('per_page', 20))))
    except (TypeError, ValueError):
        per_page = 20
    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get('page', 1))

    items = []
    for c in page:
        cuenta = getattr(c, 'cuenta_puntos', None)
        saldo = cuenta.saldo_puntos if cuenta else 0
        items.append({
            'cliente_id': c.id,
            'cliente': c.nombre_completo,
            'rut': c.rut or '',
            'empresa': c.empresa.nombre if c.empresa else '',
            'saldo_puntos': saldo,
            'valor_pesos': saldo * valor_pto,
            'tiene_cuenta': cuenta is not None,
            'nivel': cuenta.nivel if cuenta else 'PLATA',
            **_info_tipo_cliente(c),
        })

    return JsonResponse({
        'success': True,
        'items': items,
        'page': page.number,
        'num_pages': paginator.num_pages,
        'total': paginator.count,
        'per_page': per_page,
    })


@require_GET
@requiere_permiso('fidelizacion_cuentas', 'puede_ver')
def api_detalle_cuenta(request, cliente_id):
    """Saldo + tipo de cliente + gift cards vigentes + historial de la cuenta."""
    cliente = get_object_or_404(Cliente.objects.select_related('empresa'), id=cliente_id)
    if not _cliente_en_alcance(request.user, cliente):
        return JsonResponse(
            {'success': False, 'error': 'El cliente no pertenece a tus empresas.'},
            status=403,
        )
    info = fidelizacion_service.consultar_saldo(cliente=cliente)
    cuenta = getattr(cliente, 'cuenta_puntos', None)
    movimientos = []
    if cuenta:
        movimientos = [{
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'puntos': m.puntos,
            'saldo_resultante': m.saldo_resultante,
            'fecha': m.fecha.strftime('%Y-%m-%d %H:%M'),
            'fecha_expiracion': m.fecha_expiracion.isoformat() if m.fecha_expiracion else None,
            'ticket': m.ticket.correlativo if m.ticket else None,
            'observaciones': m.observaciones or '',
        } for m in cuenta.movimientos.all().order_by('-fecha')[:200]]

    tendencia_mensual = []
    if cuenta:
        programa = ProgramaFidelizacion.get_activo()
        valor_pto = programa.valor_punto_en_pesos if programa else 0
        tendencia_mensual = _construir_tendencia_mensual(cuenta=cuenta, valor_pto=valor_pto)

    giftcards = giftcards_vigentes_cliente(cliente)
    return JsonResponse({
        'success': True, 'saldo': info, 'movimientos': movimientos,
        'tendencia_mensual': tendencia_mensual,
        'giftcards': giftcards,
        'giftcards_saldo_total': sum(g.get('saldo_actual') or 0 for g in giftcards),
        **_info_tipo_cliente(cliente),
    })


@require_GET
@requiere_alguno_de_los_permisos('fidelizacion_cuentas', 'ticket_venta')
def api_consultar_saldo_puntos(request):
    """
    FICHA RÁPIDA DE CLIENTE POR RUT — endpoint único que usa la caja.

    Permiso: basta con 'fidelizacion_cuentas' O 'ticket_venta'. Antes exigía
    sólo el primero, y como el rol `vendedor` lo tiene en False (verificado en
    producción: 6 vendedores activos), el panel de fidelización de la caja les
    respondía 403 aunque sí pueden cobrar. Consultar por RUT al cliente que uno
    está atendiendo es una capacidad más chica que navegar el maestro completo
    de clientes, así que se ata al permiso de la caja y no al del módulo.

    Devuelve en un solo llamado: nombre, tipo de cliente (con el color del
    chip), saldo de puntos, valor en pesos, puntos por vencer y gift cards
    vigentes. Antes el cajero habría necesitado tres llamadas distintas.

    Compatibilidad: conserva TODAS las claves que ya devolvía
    (`cliente`, `saldo_puntos`, `valor_pesos`, `puntos_por_vencer`, `nivel`,
    `gasto_12_meses`, `membresia`), así que el POS actual sigue funcionando sin
    tocar nada; lo nuevo se suma.

    Parámetros: `rut` (o `q`, que además acepta un correo si es único en el
    CRM). Un RUT inválido no es un error de servidor: responde 200 con
    `rut_valido: false` y `cliente: null`, igual que un RUT que no existe.
    """
    identificador = (request.GET.get('rut') or request.GET.get('q') or '').strip()
    if not identificador:
        return JsonResponse({'success': False, 'error': 'RUT requerido.'}, status=400)
    ficha = construir_ficha_rapida(identificador, usuario=request.user)
    return JsonResponse({'success': True, **ficha})


def _parse_fecha_reporte(valor, default):
    fecha = parse_date(valor or '')
    return fecha or default


def _rango_reporte(request):
    hoy = timezone.localdate()
    fecha_inicio = _parse_fecha_reporte(request.GET.get('desde'), hoy - timezone.timedelta(days=29))
    fecha_fin = _parse_fecha_reporte(request.GET.get('hasta'), hoy)
    if fecha_fin < fecha_inicio:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio
    tz = timezone.get_current_timezone()
    inicio_dt = timezone.make_aware(datetime.combine(fecha_inicio, time.min), tz)
    fin_dt = timezone.make_aware(datetime.combine(fecha_fin, time.max), tz)
    return fecha_inicio, fecha_fin, inicio_dt, fin_dt


def _movimientos_visibles_para_usuario(qs, usuario):
    """Aplica el mismo alcance multi-empresa que el listado de cuentas."""
    if usuario_puede_ver_todas_sucursales(usuario):
        return qs
    empresas = _empresa_ids_usuario(usuario)
    return qs.filter(Q(cuenta__cliente__empresa__isnull=True) |
                     Q(cuenta__cliente__empresa_id__in=empresas))


def _cliente_nombre(cliente):
    if not cliente:
        return ''
    return cliente.nombre_completo


def _sumar_meses(fecha, delta_meses):
    """Suma (o resta) meses a una fecha, devolviendo siempre el día 1 de mes."""
    mes_total = fecha.month - 1 + delta_meses
    anio = fecha.year + mes_total // 12
    mes = mes_total % 12 + 1
    return fecha.replace(year=anio, month=mes, day=1)


def _construir_tendencia_mensual(meses=12, usuario=None, cuenta=None, valor_pto=0):
    """
    Puntos ganados/canjeados/expirados por mes, de los últimos `meses` meses
    (ventana fija, independiente del filtro desde/hasta del reporte). Si se
    pasa `cuenta`, se limita al historial de ese cliente.
    """
    hoy = timezone.localdate()
    inicio_mes = _sumar_meses(hoy.replace(day=1), -(meses - 1))
    tz = timezone.get_current_timezone()
    inicio_dt = timezone.make_aware(datetime.combine(inicio_mes, time.min), tz)

    qs = MovimientoPuntos.objects.filter(fecha__gte=inicio_dt)
    if cuenta is not None:
        qs = qs.filter(cuenta=cuenta)
    elif usuario is not None:
        qs = _movimientos_visibles_para_usuario(qs, usuario)

    filas = (
        qs.annotate(mes=TruncMonth('fecha'))
          .values('mes')
          .annotate(
              ganados=Sum('puntos', filter=Q(tipo__in=['ACUMULACION', 'BIENVENIDA', 'CUMPLEANOS'], puntos__gt=0)),
              canjeados=Sum('puntos', filter=Q(tipo='CANJE', puntos__lt=0)),
              expirados=Sum('puntos', filter=Q(tipo='EXPIRACION', puntos__lt=0)),
          )
    )
    por_mes = {f['mes'].strftime('%Y-%m'): f for f in filas if f['mes']}

    resultado = []
    cursor = inicio_mes
    for _ in range(meses):
        clave = cursor.strftime('%Y-%m')
        fila = por_mes.get(clave, {})
        ganados = fila.get('ganados') or 0
        canjeados = abs(fila.get('canjeados') or 0)
        expirados = abs(fila.get('expirados') or 0)
        resultado.append({
            'mes': clave,
            'puntos_ganados': ganados,
            'valor_ganado': ganados * valor_pto,
            'puntos_canjeados': canjeados,
            'valor_canjeado': canjeados * valor_pto,
            'puntos_expirados': expirados,
            'valor_expirado': expirados * valor_pto,
        })
        cursor = _sumar_meses(cursor, 1)
    return resultado


def _construir_ranking_mes(mes_str=None, usuario=None, valor_pto=0, top=10):
    """Top clientes por puntos ganados en un mes puntual (default: mes actual)."""
    hoy = timezone.localdate()
    anio, mes = hoy.year, hoy.month
    if mes_str:
        try:
            partes = mes_str.split('-')
            anio, mes = int(partes[0]), int(partes[1])
        except (ValueError, IndexError, TypeError):
            anio, mes = hoy.year, hoy.month

    qs = MovimientoPuntos.objects.filter(
        tipo__in=['ACUMULACION', 'BIENVENIDA', 'CUMPLEANOS'],
        puntos__gt=0,
        fecha__year=anio,
        fecha__month=mes,
    )
    if usuario is not None:
        qs = _movimientos_visibles_para_usuario(qs, usuario)

    filas = (
        qs.values('cuenta_id', 'cuenta__cliente_id', 'cuenta__cliente__nombre',
                   'cuenta__cliente__apellido', 'cuenta__cliente__rut')
          .annotate(puntos_ganados=Sum('puntos'))
          .order_by('-puntos_ganados')[:top]
    )
    return [{
        'cliente_id': f['cuenta__cliente_id'],
        'cliente': (
            f"{f.get('cuenta__cliente__nombre') or ''} {f.get('cuenta__cliente__apellido') or ''}".strip()
            or 'Cliente'
        ),
        'rut': f.get('cuenta__cliente__rut') or '',
        'puntos_ganados': f['puntos_ganados'] or 0,
        'valor_pesos': (f['puntos_ganados'] or 0) * valor_pto,
    } for f in filas]


def detectar_senales_rut_sospechoso(fecha_inicio_dt, fecha_fin_dt, usuario=None):
    """
    Señales de posible abuso/fraude sobre RUTs en el programa de puntos,
    calculadas en vivo sobre el período del reporte (mismo criterio que
    `alertas_concentracion`, que se sigue calculando aparte y se muestra
    junto a estas señales en el reporte).

    - MULTI_SUCURSAL: misma cuenta acumulando en 2+ sucursales el mismo día.
    - RUT_GENERICO: ventas pagadas con el RUT ficticio o un RUT inválido,
      agrupadas por vendedor (no genera puntos, así que se mide sobre Ticket).
    - CANJE_INMEDIATO: canje realizado dentro de 48h de la acumulación previa.
    """
    senales = []

    mov_qs = MovimientoPuntos.objects.select_related('cuenta__cliente', 'sucursal').filter(
        fecha__gte=fecha_inicio_dt, fecha__lte=fecha_fin_dt,
    )
    if usuario is not None:
        mov_qs = _movimientos_visibles_para_usuario(mov_qs, usuario)

    # 1) Multi-sucursal el mismo día
    por_cuenta_dia = {}
    for mov in mov_qs.filter(tipo='ACUMULACION', puntos__gt=0, sucursal__isnull=False):
        clave = (mov.cuenta_id, timezone.localtime(mov.fecha).date())
        entrada = por_cuenta_dia.setdefault(clave, {'sucursales': set(), 'cliente': mov.cuenta.cliente})
        entrada['sucursales'].add(mov.sucursal_id)

    por_cuenta_ocurrencias = {}
    for (cuenta_id, dia), entrada in por_cuenta_dia.items():
        if len(entrada['sucursales']) >= 2:
            por_cuenta_ocurrencias.setdefault(cuenta_id, []).append(
                (dia, len(entrada['sucursales']), entrada['cliente'])
            )

    for cuenta_id, ocurrencias in por_cuenta_ocurrencias.items():
        cliente = ocurrencias[0][2]
        max_sucursales = max(o[1] for o in ocurrencias)
        severidad = 'ALTA' if max_sucursales >= 3 or len(ocurrencias) >= 2 else 'MEDIA'
        senales.append({
            'tipo': 'MULTI_SUCURSAL',
            'severidad': severidad,
            'cliente_id': cliente.id,
            'cliente': _cliente_nombre(cliente),
            'rut': cliente.rut or '',
            'detalle': f"{len(ocurrencias)} día(s) con acumulación en hasta {max_sucursales} sucursales distintas",
        })

    # 3) RUT genérico/inválido reutilizado (a nivel de venta, no de puntos:
    # el RUT ficticio nunca acumula puntos, así que no aparece en MovimientoPuntos)
    tickets_qs = Ticket.objects.select_related('vendedor').filter(
        created_at__gte=fecha_inicio_dt, created_at__lte=fecha_fin_dt,
        estado='PAGADO',
    ).exclude(cliente_rut__isnull=True).exclude(cliente_rut='')
    if usuario is not None and not usuario_puede_ver_todas_sucursales(usuario):
        empresas = _empresa_ids_usuario(usuario)
        tickets_qs = tickets_qs.filter(
            Q(vendedor__empresa__isnull=True) | Q(vendedor__empresa_id__in=empresas)
        )

    conteo_por_vendedor = {}
    for t in tickets_qs.only('id', 'cliente_rut', 'vendedor_id', 'vendedor__nombre'):
        rut_norm = (t.cliente_rut or '').strip().upper()
        es_generico = rut_norm in fidelizacion_service.RUT_FICTICIOS or not validar_rut_chileno(rut_norm)
        if not es_generico:
            continue
        entrada = conteo_por_vendedor.setdefault(t.vendedor_id, {
            'count': 0,
            'vendedor': t.vendedor.nombre if t.vendedor else 'Sin vendedor',
        })
        entrada['count'] += 1

    for info in conteo_por_vendedor.values():
        if info['count'] < 15:
            continue
        severidad = 'ALTA' if info['count'] >= 30 else 'MEDIA'
        senales.append({
            'tipo': 'RUT_GENERICO',
            'severidad': severidad,
            'cliente_id': None,
            'cliente': info['vendedor'],
            'rut': '',
            'detalle': f"{info['count']} venta(s) pagadas con RUT genérico/inválido en el período",
        })

    # 4) Canje inmediato tras acumular (mismo RUT/cuenta)
    por_cuenta_eventos = {}
    for mov in mov_qs.filter(tipo__in=['ACUMULACION', 'CANJE']).order_by('cuenta_id', 'fecha'):
        por_cuenta_eventos.setdefault(mov.cuenta_id, []).append(mov)

    for eventos in por_cuenta_eventos.values():
        ultimo_acum = None
        ocurrencias = 0
        cliente = None
        for mov in eventos:
            if mov.tipo == 'ACUMULACION':
                ultimo_acum = mov.fecha
            elif mov.tipo == 'CANJE' and ultimo_acum is not None:
                if (mov.fecha - ultimo_acum).total_seconds() < 48 * 3600:
                    ocurrencias += 1
                    cliente = mov.cuenta.cliente
        if ocurrencias >= 1 and cliente is not None:
            severidad = 'ALTA' if ocurrencias >= 2 else 'MEDIA'
            senales.append({
                'tipo': 'CANJE_INMEDIATO',
                'severidad': severidad,
                'cliente_id': cliente.id,
                'cliente': _cliente_nombre(cliente),
                'rut': cliente.rut or '',
                'detalle': f"{ocurrencias} canje(s) realizados dentro de 48h de acumular",
            })

    return senales


# ========== ELEGIBILIDAD DE UNA CUENTA (pasivo real vs pasivo inflado) ==========
#
# El programa arrastra saldos que se acumularon ANTES de que
# `fidelizacion_service.venta_fideliza` bloqueara el RUT genérico y los RUT de
# empresa (junio 2026). Esos saldos siguen vivos en el ledger e inflan el
# pasivo: en producción son el 60% del total. Aquí NO se inventan reglas
# nuevas: se aplican exactamente los mismos criterios de RUT que el motor usa
# hoy para decidir si una venta acumula. Si una compra de ese RUT hoy NO
# fideliza, su saldo histórico se reporta aparte.

MOTIVOS_CUENTA_NO_ELEGIBLE = {
    'SIN_RUT': 'Cuenta sin RUT',
    'RUT_GENERICO': 'RUT genérico de consumidor final',
    'RUT_INVALIDO': 'RUT inválido',
    'RUT_EMPRESA': 'RUT de empresa (persona jurídica)',
}

# Tipos de cliente que NO son consumidor particular. No bloquean la
# acumulación (el motor solo mira el RUT), pero se marcan en el reporte para
# que el usuario sepa qué parte del pasivo es B2B.
TIPOS_CLIENTE_NO_PARTICULAR = ('EMPRESARIAL', 'MAYORISTA', 'DISTRIBUIDOR')


def motivo_cuenta_no_elegible(cliente):
    """
    Código del motivo por el que el saldo de este cliente NO debería contarse
    como pasivo del programa, o '' si es un cliente elegible.

    Mismos criterios de RUT que `fidelizacion_service.venta_fideliza`.
    """
    if cliente is None:
        return 'SIN_RUT'
    rut = (getattr(cliente, 'rut', '') or '').strip()
    if not rut:
        return 'SIN_RUT'
    ficticios = {fidelizacion_service.normalizar_rut(r)
                 for r in fidelizacion_service.RUT_FICTICIOS}
    if fidelizacion_service.normalizar_rut(rut) in ficticios:
        return 'RUT_GENERICO'
    if not validar_rut_chileno(rut):
        return 'RUT_INVALIDO'
    if fidelizacion_service.es_rut_empresa(rut):
        return 'RUT_EMPRESA'
    return ''


def analizar_cartera_puntos(cuentas_qs, valor_pto=0, minimo_canje=0, top=15):
    """
    Recorre las cuentas y separa pasivo elegible / no elegible, cuenta cuántos
    clientes alcanzan el mínimo de canje y devuelve la escalera de saldos.

    Devuelve un dict listo para serializar. Una sola pasada sobre las cuentas
    (hoy 98 filas en producción); el saldo sale del cache `saldo_puntos`, que
    se contrasta contra el ledger en `construir_reporte_fidelizacion`.
    """
    total_pts = total_cuentas = 0
    eleg_pts = eleg_cuentas = 0
    noeleg_pts = noeleg_cuentas = 0
    alcanzan_minimo = alcanzan_minimo_elegibles = 0
    por_motivo = {}
    detalle = []
    saldos_elegibles = []
    b2b_pts = b2b_cuentas = 0

    for cuenta in cuentas_qs.only(
        'id', 'saldo_puntos', 'nivel',
        'cliente__id', 'cliente__rut', 'cliente__nombre',
        'cliente__apellido', 'cliente__tipo_cliente',
    ):
        cliente = cuenta.cliente
        saldo = cuenta.saldo_puntos or 0
        total_pts += saldo
        total_cuentas += 1
        if minimo_canje and saldo >= minimo_canje:
            alcanzan_minimo += 1

        motivo = motivo_cuenta_no_elegible(cliente)
        tipo = (getattr(cliente, 'tipo_cliente', '') or 'INDIVIDUAL').upper()
        if tipo in TIPOS_CLIENTE_NO_PARTICULAR:
            b2b_pts += saldo
            b2b_cuentas += 1

        if motivo:
            noeleg_pts += saldo
            noeleg_cuentas += 1
            agregado = por_motivo.setdefault(motivo, {
                'motivo': motivo,
                'label': MOTIVOS_CUENTA_NO_ELEGIBLE.get(motivo, motivo),
                'cuentas': 0, 'puntos': 0, 'valor_pesos': 0,
            })
            agregado['cuentas'] += 1
            agregado['puntos'] += saldo
            agregado['valor_pesos'] = agregado['puntos'] * valor_pto
            detalle.append({
                'cliente_id': cliente.id,
                'cliente': _cliente_nombre(cliente),
                'rut': cliente.rut or '',
                'motivo': motivo,
                'motivo_label': MOTIVOS_CUENTA_NO_ELEGIBLE.get(motivo, motivo),
                'tipo_cliente': tipo,
                'puntos': saldo,
                'valor_pesos': saldo * valor_pto,
            })
        else:
            eleg_pts += saldo
            eleg_cuentas += 1
            saldos_elegibles.append(saldo)
            if minimo_canje and saldo >= minimo_canje:
                alcanzan_minimo_elegibles += 1

    detalle.sort(key=lambda x: -x['puntos'])
    saldos_elegibles.sort(reverse=True)
    # `detalle` trae TODAS las cuentas no elegibles (el recorte a `top` se hace
    # al devolver), así que sirve de índice para marcar filas en otras tablas.
    motivos_por_cliente = {item['cliente_id']: item['motivo_label'] for item in detalle}
    return {
        'motivos_por_cliente': motivos_por_cliente,
        'total_cuentas': total_cuentas,
        'total_puntos': total_pts,
        'total_valor_pesos': total_pts * valor_pto,
        'elegibles_cuentas': eleg_cuentas,
        'elegibles_puntos': eleg_pts,
        'elegibles_valor_pesos': eleg_pts * valor_pto,
        'no_elegibles_cuentas': noeleg_cuentas,
        'no_elegibles_puntos': noeleg_pts,
        'no_elegibles_valor_pesos': noeleg_pts * valor_pto,
        'no_elegibles_pct': round(noeleg_pts / total_pts * 100, 1) if total_pts else 0,
        'b2b_cuentas': b2b_cuentas,
        'b2b_puntos': b2b_pts,
        'b2b_valor_pesos': b2b_pts * valor_pto,
        'por_motivo': sorted(por_motivo.values(), key=lambda x: -x['puntos']),
        'detalle_no_elegibles': detalle[:top],
        'alcanzan_minimo': alcanzan_minimo,
        'alcanzan_minimo_elegibles': alcanzan_minimo_elegibles,
        'saldos_elegibles': saldos_elegibles,
    }


def construir_reporte_fidelizacion(*, fecha_inicio_dt, fecha_fin_dt,
                                   dias_vencimiento=30, usuario=None, mes=None):
    """
    Calcula el reporte de fidelización. Separado para testear la lógica sin
    depender de la vista ni del JavaScript.
    """
    # Import local: `TruncDate` solo se usa aquí y así no se toca el bloque de
    # imports del módulo, compartido con el resto de pantallas de fidelización.
    from django.db.models.functions import TruncDate

    programa = ProgramaFidelizacion.get_activo()
    valor_pto = programa.valor_punto_en_pesos if programa else 0
    hoy = timezone.localdate()
    vence_hasta = hoy + timezone.timedelta(days=max(1, int(dias_vencimiento or 30)))

    cuentas = CuentaPuntos.objects.select_related('cliente')
    mov_base = MovimientoPuntos.objects.select_related(
        'cuenta__cliente', 'ticket', 'usuario', 'sucursal',
    )
    if usuario is not None:
        mov_base = _movimientos_visibles_para_usuario(mov_base, usuario)
        if not usuario_puede_ver_todas_sucursales(usuario):
            empresas = _empresa_ids_usuario(usuario)
            cuentas = cuentas.filter(Q(cliente__empresa__isnull=True) |
                                     Q(cliente__empresa_id__in=empresas))

    periodo_qs = mov_base.filter(fecha__gte=fecha_inicio_dt, fecha__lte=fecha_fin_dt)
    acumulaciones_qs = periodo_qs.filter(tipo='ACUMULACION', puntos__gt=0)
    bienvenida_qs = periodo_qs.filter(tipo='BIENVENIDA', puntos__gt=0)
    canjes_qs = periodo_qs.filter(tipo='CANJE', puntos__lt=0)
    expiraciones_qs = periodo_qs.filter(tipo='EXPIRACION', puntos__lt=0)

    total_puntos = cuentas.aggregate(s=Sum('saldo_puntos'))['s'] or 0
    puntos_acumulados = acumulaciones_qs.aggregate(s=Sum('puntos'))['s'] or 0
    puntos_bienvenida = bienvenida_qs.aggregate(s=Sum('puntos'))['s'] or 0
    puntos_canjeados = abs(canjes_qs.aggregate(s=Sum('puntos'))['s'] or 0)
    puntos_expirados = abs(expiraciones_qs.aggregate(s=Sum('puntos'))['s'] or 0)

    # --- Pasivo real vs pasivo inflado por cuentas no elegibles ---
    minimo_canje = programa.minimo_canje_puntos if programa else 0
    cartera = analizar_cartera_puntos(cuentas, valor_pto=valor_pto, minimo_canje=minimo_canje)

    # --- Cuadratura: saldo cacheado de las cuentas vs suma del ledger ---
    # `saldo_puntos` es un cache denormalizado; si se desalinea del ledger todo
    # el pasivo que muestra esta pantalla es falso. Se compara siempre.
    saldo_ledger = (
        MovimientoPuntos.objects.filter(cuenta__in=cuentas)
        .aggregate(s=Sum('puntos'))['s'] or 0
    )
    descuadre_ledger = total_puntos - saldo_ledger
    if cartera['total_puntos'] != total_puntos:
        # El SUM de SQL y el recorrido en Python deben dar lo mismo; si no,
        # algo cambió a mitad del reporte (o el filtro de alcance difiere).
        logger.warning(
            "Reporte fidelización: SUM(saldo_puntos)=%s != recorrido de cuentas=%s",
            total_puntos, cartera['total_puntos'],
        )
    if descuadre_ledger:
        logger.warning(
            "Reporte fidelización: descuadre cache vs ledger = %s pts "
            "(cache=%s ledger=%s)", descuadre_ledger, total_puntos, saldo_ledger,
        )

    # --- Histórico completo (sin filtro de fechas): tasa de canje real ---
    # Un programa de puntos sin canjes es un pasivo que crece sin fidelizar a
    # nadie; la tasa de canje es EL indicador de salud del programa.
    hist = mov_base.aggregate(
        emitidos=Sum('puntos', filter=Q(puntos__gt=0)),
        canjeados=Sum('puntos', filter=Q(tipo='CANJE')),
        expirados=Sum('puntos', filter=Q(tipo='EXPIRACION')),
    )
    emitidos_hist = hist['emitidos'] or 0
    canjeados_hist = abs(hist['canjeados'] or 0)
    expirados_hist = abs(hist['expirados'] or 0)
    tasa_canje_pct = round(canjeados_hist / emitidos_hist * 100, 2) if emitidos_hist else 0.0

    # --- Horizonte real de vencimiento (no solo la ventana elegida) ---
    lotes_vivos = mov_base.filter(puntos__gt=0, fecha_expiracion__isnull=False)
    proximo_vencimiento = lotes_vivos.filter(
        fecha_expiracion__gte=hoy).aggregate(f=Min('fecha_expiracion'))['f']
    puntos_vencidos_sin_expirar = 0
    for lote in lotes_vivos.filter(fecha_expiracion__lt=hoy):
        puntos_vencidos_sin_expirar += max(0, lote.saldo_lote)

    por_vencer_map = {}
    lotes_por_vencer = mov_base.filter(
        puntos__gt=0,
        fecha_expiracion__gte=hoy,
        fecha_expiracion__lte=vence_hasta,
    ).order_by('fecha_expiracion', 'fecha')
    for lote in lotes_por_vencer:
        disponible = lote.saldo_lote
        if disponible <= 0:
            continue
        cliente = lote.cuenta.cliente
        item = por_vencer_map.setdefault(lote.cuenta_id, {
            'cliente_id': cliente.id,
            'cliente': _cliente_nombre(cliente),
            'rut': cliente.rut or '',
            'puntos': 0,
            'valor_pesos': 0,
            'proximo_vencimiento': lote.fecha_expiracion,
        })
        item['puntos'] += disponible
        item['valor_pesos'] = item['puntos'] * valor_pto
        if lote.fecha_expiracion < item['proximo_vencimiento']:
            item['proximo_vencimiento'] = lote.fecha_expiracion

    puntos_por_vencer = sorted(
        por_vencer_map.values(),
        key=lambda x: (x['proximo_vencimiento'], -x['puntos']),
    )
    total_por_vencer = sum(i['puntos'] for i in puntos_por_vencer)

    canjes_recientes = []
    for mov in canjes_qs.order_by('-fecha')[:20]:
        cliente = mov.cuenta.cliente
        puntos = abs(mov.puntos)
        canjes_recientes.append({
            'fecha': timezone.localtime(mov.fecha).strftime('%Y-%m-%d %H:%M'),
            'cliente_id': cliente.id,
            'cliente': _cliente_nombre(cliente),
            'rut': cliente.rut or '',
            'puntos': puntos,
            'valor_pesos': puntos * valor_pto,
            'usuario': mov.usuario.get_full_name() if mov.usuario else '',
            'ticket': mov.ticket.correlativo if mov.ticket else None,
            'observaciones': mov.observaciones or '',
        })

    total_tickets_por_usuario = {
        row['usuario_id']: row['tickets_usuario']
        for row in acumulaciones_qs.filter(usuario__isnull=False, ticket__isnull=False)
        .values('usuario_id')
        .annotate(tickets_usuario=Count('ticket', distinct=True))
    }

    alertas_concentracion = []
    concentracion_rows = (
        acumulaciones_qs
        .filter(usuario__isnull=False, ticket__isnull=False)
        .values(
            'usuario_id', 'usuario__username', 'usuario__first_name', 'usuario__last_name',
            'cuenta_id', 'cuenta__cliente_id', 'cuenta__cliente__nombre',
            'cuenta__cliente__apellido', 'cuenta__cliente__rut',
        )
        .annotate(
            tickets=Count('ticket', distinct=True),
            # OJO: antes esto contaba `ticket__fecha` distintos. `Ticket.fecha`
            # es un DateField con auto_now: se reescribe con la fecha de HOY
            # cada vez que el ticket se vuelve a guardar, así que los días
            # colapsaban y la señal "N tickets en 1 día" era inventada. Se
            # cuentan los días reales del movimiento de puntos.
            dias=Count(TruncDate('fecha'), distinct=True),
            puntos=Sum('puntos'),
            venta_total=Sum('ticket__total'),
            primer_mov=Min('fecha'),
            ultimo_mov=Max('fecha'),
        )
        .filter(tickets__gte=5)
        .order_by('-tickets', '-puntos')[:30]
    )
    for row in concentracion_rows:
        tickets_usuario = total_tickets_por_usuario.get(row['usuario_id'], 0) or 0
        participacion = round((row['tickets'] / tickets_usuario * 100), 1) if tickets_usuario else 0
        severidad = 'ALTA' if row['tickets'] >= 10 or participacion >= 50 else 'MEDIA'
        nombre_usuario = (
            f"{row.get('usuario__first_name') or ''} {row.get('usuario__last_name') or ''}".strip()
            or row.get('usuario__username') or 'Usuario'
        )
        nombre_cliente = (
            f"{row.get('cuenta__cliente__nombre') or ''} {row.get('cuenta__cliente__apellido') or ''}".strip()
            or 'Cliente'
        )
        # La mayoría de las "concentraciones" históricas son contra la cuenta
        # del RUT genérico: marcarlas evita perseguir a una cajera por algo que
        # es basura de datos.
        motivo_cuenta = cartera['motivos_por_cliente'].get(row['cuenta__cliente_id'], '')
        alertas_concentracion.append({
            'no_elegible': bool(motivo_cuenta),
            'motivo_no_elegible': motivo_cuenta,
            'severidad': severidad,
            'usuario_id': row['usuario_id'],
            'usuario': nombre_usuario,
            'cliente_id': row['cuenta__cliente_id'],
            'cliente': nombre_cliente,
            'rut': row.get('cuenta__cliente__rut') or '',
            'tickets': row['tickets'],
            'tickets_usuario': tickets_usuario,
            'participacion': participacion,
            'dias': row['dias'],
            'puntos': row['puntos'] or 0,
            'valor_pesos': (row['puntos'] or 0) * valor_pto,
            'venta_total': row['venta_total'] or 0,
            'primer_mov': timezone.localtime(row['primer_mov']).strftime('%Y-%m-%d') if row['primer_mov'] else '',
            'ultimo_mov': timezone.localtime(row['ultimo_mov']).strftime('%Y-%m-%d') if row['ultimo_mov'] else '',
            'motivo': (
                'Alta concentración de tickets de una cajera en una misma cuenta. '
                'Revisar si corresponde al cliente real o a acumulación indebida.'
            ),
        })

    # Desglose por nivel
    niveles_qs = cuentas.values('nivel').annotate(
        cantidad=Count('id'),
        puntos_total=Sum('saldo_puntos'),
    )
    desglose_niveles = {row['nivel']: {
        'cantidad': row['cantidad'],
        'puntos': row['puntos_total'] or 0,
        'valor_pesos': (row['puntos_total'] or 0) * valor_pto,
    } for row in niveles_qs}

    # Bonos cumpleaños en el período
    cumpleanos_qs = periodo_qs.filter(tipo='CUMPLEANOS', puntos__gt=0)
    puntos_cumpleanos = cumpleanos_qs.aggregate(s=Sum('puntos'))['s'] or 0

    # "Emitidos" = TODO punto positivo del período, no solo los 3 tipos que se
    # listan aparte. Sumando solo ACUMULACION+BIENVENIDA+CUMPLEANOS, los bonos
    # de referido, desafío y los ajustes manuales al alza quedaban fuera del
    # KPI y del valor emitido: puntos regalados que no aparecían en ningún lado.
    puntos_emitidos_periodo = periodo_qs.filter(puntos__gt=0).aggregate(
        s=Sum('puntos'))['s'] or 0
    puntos_otros_emitidos = (
        puntos_emitidos_periodo - puntos_acumulados - puntos_bienvenida - puntos_cumpleanos
    )

    tendencia_mensual = _construir_tendencia_mensual(usuario=usuario, valor_pto=valor_pto)
    ranking_mes = _construir_ranking_mes(mes, usuario=usuario, valor_pto=valor_pto)
    for fila in ranking_mes:
        motivo_fila = cartera['motivos_por_cliente'].get(fila['cliente_id'], '')
        fila['no_elegible'] = bool(motivo_fila)
        fila['motivo_no_elegible'] = motivo_fila
    senales_rut_sospechoso = detectar_senales_rut_sospechoso(fecha_inicio_dt, fecha_fin_dt, usuario=usuario)

    return {
        'programa': {
            'nombre': programa.nombre if programa else '',
            'valor_punto': valor_pto,
            'tasa_descuento_efectiva': programa.tasa_descuento_efectiva if programa else 0,
            'tasa_plata': float(programa.tasa_plata) if programa else 3.0,
            'tasa_oro': float(programa.tasa_oro) if programa else 4.0,
            'tasa_platino': float(programa.tasa_platino) if programa else 5.0,
            'umbral_oro': programa.umbral_oro if programa else 300000,
            'umbral_platino': programa.umbral_platino if programa else 800000,
            'minimo_canje_puntos': minimo_canje,
            'incremento_canje': programa.incremento_canje if programa else 0,
            'vigencia_dias': programa.vigencia_dias if programa else 0,
        },
        'resumen': {
            'total_clientes': cartera['total_cuentas'],
            'puntos_circulantes': total_puntos,
            'pasivo_estimado_pesos': total_puntos * valor_pto,
            # Pasivo depurado: sin RUT genérico / empresa / inválido.
            'puntos_circulantes_elegibles': cartera['elegibles_puntos'],
            'pasivo_elegible_pesos': cartera['elegibles_valor_pesos'],
            'clientes_elegibles': cartera['elegibles_cuentas'],
            'puntos_no_elegibles': cartera['no_elegibles_puntos'],
            'pasivo_no_elegible_pesos': cartera['no_elegibles_valor_pesos'],
            'cuentas_no_elegibles': cartera['no_elegibles_cuentas'],
            'pct_no_elegible': cartera['no_elegibles_pct'],
            'puntos_b2b': cartera['b2b_puntos'],
            'cuentas_b2b': cartera['b2b_cuentas'],
            # Salud del programa (histórico completo, sin filtro de fechas)
            'puntos_emitidos_historico': emitidos_hist,
            'puntos_canjeados_historico': canjeados_hist,
            'puntos_expirados_historico': expirados_hist,
            'tasa_canje_pct': tasa_canje_pct,
            'valor_canjeado_historico': canjeados_hist * valor_pto,
            # Cuadratura cache vs ledger
            'saldo_ledger': saldo_ledger,
            'descuadre_ledger': descuadre_ledger,
            'cuadra_ledger': descuadre_ledger == 0,
            # Vencimientos
            'proximo_vencimiento': proximo_vencimiento.isoformat() if proximo_vencimiento else None,
            'puntos_vencidos_sin_expirar': puntos_vencidos_sin_expirar,
            # Alcance del mínimo de canje configurado
            'minimo_canje_puntos': minimo_canje,
            'clientes_alcanzan_minimo': cartera['alcanzan_minimo'],
            'clientes_alcanzan_minimo_elegibles': cartera['alcanzan_minimo_elegibles'],
            'puntos_emitidos_periodo': puntos_emitidos_periodo,
            'puntos_acumulados_periodo': puntos_acumulados,
            'puntos_bienvenida_periodo': puntos_bienvenida,
            'puntos_cumpleanos_periodo': puntos_cumpleanos,
            'puntos_otros_emitidos_periodo': puntos_otros_emitidos,
            'valor_emitido_periodo': puntos_emitidos_periodo * valor_pto,
            'puntos_canjeados_periodo': puntos_canjeados,
            'valor_canjeado_periodo': puntos_canjeados * valor_pto,
            'puntos_expirados_periodo': puntos_expirados,
            'valor_expirado_periodo': puntos_expirados * valor_pto,
            'puntos_por_vencer': total_por_vencer,
            'valor_por_vencer': total_por_vencer * valor_pto,
            'alertas_concentracion': len(alertas_concentracion),
            'acumulaciones_30d': mov_base.filter(
                tipo='ACUMULACION',
                fecha__gte=timezone.now() - timezone.timedelta(days=30),
            ).count(),
        },
        'desglose_niveles': desglose_niveles,
        'cartera_no_elegible': {
            'por_motivo': cartera['por_motivo'],
            'detalle': cartera['detalle_no_elegibles'],
        },
        'puntos_por_vencer': [{
            **item,
            'proximo_vencimiento': item['proximo_vencimiento'].isoformat(),
        } for item in puntos_por_vencer[:20]],
        'canjes_recientes': canjes_recientes,
        'alertas_concentracion': alertas_concentracion,
        'tendencia_mensual': tendencia_mensual,
        'ranking_mes': ranking_mes,
        'senales_rut_sospechoso': senales_rut_sospechoso,
    }


@require_POST
@requiere_permiso('fidelizacion_programa', 'puede_editar')
def api_guardar_programa(request):
    """Crea/actualiza el programa de fidelización activo."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST

    programa = ProgramaFidelizacion.get_activo()
    if not programa:
        programa = ProgramaFidelizacion()

    campos_int = [
        'puntos_por_monto', 'monto_base_acumulacion', 'valor_punto_en_pesos',
        'minimo_canje_puntos', 'vigencia_dias', 'puntos_bienvenida',
        'puntos_cumpleanos', 'incremento_canje', 'umbral_oro', 'umbral_platino',
        # Bonos de referido: existían en el modelo pero no eran editables desde
        # ninguna pantalla, así que quedaban clavados en su default de 2.000.
        'bono_referido_padrino', 'bono_referido_ahijado',
    ]
    for campo in campos_int:
        if data.get(campo) not in (None, ''):
            setattr(programa, campo, int(float(data[campo])))
    campos_decimal = ['tasa_plata', 'tasa_oro', 'tasa_platino']
    for campo in campos_decimal:
        if data.get(campo) not in (None, ''):
            setattr(programa, campo, float(data[campo]))
    if data.get('nombre'):
        programa.nombre = data['nombre']
    if data.get('redondeo'):
        programa.redondeo = data['redondeo']
    if data.get('acumula_sobre'):
        programa.acumula_sobre = data['acumula_sobre']

    # Guardrail de costo.
    #
    # OJO: `tasa_descuento_efectiva` sale de puntos_por_monto/monto_base, que es
    # la fórmula PLANA LEGACY. El motor real (`calcular_puntos_con_nivel`, único
    # usado por `fidelizacion_service.acumular_puntos_por_venta`) aplica el % del
    # nivel del cliente. Con la config de producción la propiedad legacy decía
    # 1% cuando el costo real era 0,5%/1%/1,5% según nivel: el guardrail vigilaba
    # un número que no manda nada. Ahora se evalúa el costo REAL:
    #   pesos devueltos por venta = monto × tasa_nivel% × valor_punto_en_pesos
    TASA_AVISO = 5.0
    TASA_TOPE = 10.0
    valor_pto = programa.valor_punto_en_pesos or 0
    tasa_max_nivel = max(
        float(programa.tasa_plata), float(programa.tasa_oro), float(programa.tasa_platino),
    )
    tasa_real = round(tasa_max_nivel * valor_pto, 2)
    tasa_legacy = programa.tasa_descuento_efectiva
    confirmado = str(data.get('confirmar_tasa_alta', '')).lower() in ('true', '1', 'on', 'yes')
    if tasa_real > TASA_TOPE and not confirmado:
        return JsonResponse({
            'success': False,
            'requiere_confirmacion': True,
            'tasa_real_maxima': tasa_real,
            'tasa_descuento_efectiva': tasa_legacy,
            'warning': (f'El nivel más alto quedaría devolviendo {tasa_real}% de cada '
                        f'venta en puntos (sobre el tope de {TASA_TOPE:.0f}%). '
                        '¿Confirmas que es correcto?'),
        })

    programa.activo = True
    programa.updated_by = request.user
    programa.save()

    # Cuántos clientes alcanzan el NUEVO mínimo de canje: la consecuencia más
    # cara de esta pantalla, confirmada con el dato real tras guardar.
    cuentas = CuentaPuntos.objects.select_related('cliente')
    if not usuario_puede_ver_todas_sucursales(request.user):
        empresas = _empresa_ids_usuario(request.user)
        cuentas = cuentas.filter(Q(cliente__empresa__isnull=True) |
                                 Q(cliente__empresa_id__in=empresas))
    cartera = analizar_cartera_puntos(
        cuentas, valor_pto=valor_pto, minimo_canje=programa.minimo_canje_puntos,
    )

    resp = {
        'success': True,
        'programa_id': programa.id,
        'tasa_real_maxima': tasa_real,
        'tasa_descuento_efectiva': tasa_legacy,
        'clientes_alcanzan_minimo': cartera['alcanzan_minimo_elegibles'],
        'clientes_elegibles': cartera['elegibles_cuentas'],
        'minimo_canje_puntos': programa.minimo_canje_puntos,
    }
    avisos = []
    if tasa_real > TASA_AVISO:
        avisos.append(f'El nivel más alto devuelve {tasa_real}% de cada venta en puntos, '
                      f'por encima del {TASA_AVISO:.0f}% recomendado.')
    if cartera['elegibles_cuentas'] and cartera['alcanzan_minimo_elegibles'] == 0:
        avisos.append(f'NINGUNO de los {cartera["elegibles_cuentas"]} clientes con cuenta '
                      f'alcanza el mínimo de {programa.minimo_canje_puntos} puntos: '
                      'nadie podrá canjear.')
    if avisos:
        resp['warning'] = ' '.join(avisos)
    logger.info(
        "Programa de fidelización guardado por %s: minimo_canje=%s tasas=%s/%s/%s "
        "valor_punto=%s tasa_real_max=%s alcanzan_minimo=%s/%s",
        request.user.id, programa.minimo_canje_puntos, programa.tasa_plata,
        programa.tasa_oro, programa.tasa_platino, valor_pto, tasa_real,
        cartera['alcanzan_minimo_elegibles'], cartera['elegibles_cuentas'],
    )
    return JsonResponse(resp)


@require_POST
@requiere_permiso('fidelizacion_cuentas', 'puede_editar')
def api_ajuste_manual_puntos(request):
    """Ajuste manual de puntos (suma o resta). Solo roles con puede_editar."""
    try:
        data = json.loads(request.body or '{}')
        cliente = get_object_or_404(Cliente, id=data.get('cliente_id'))
        if not _cliente_en_alcance(request.user, cliente):
            return JsonResponse(
                {'success': False, 'error': 'El cliente no pertenece a tus empresas.'},
                status=403,
            )
        puntos = int(data.get('puntos') or 0)
        if puntos == 0:
            return JsonResponse(
                {'success': False, 'error': 'El ajuste debe ser distinto de 0.'},
                status=400,
            )

        # Los puntos se convierten en vale y el vale en descuento de caja, así que
        # un ajuste manual es dinero. Se acota y se exige justificación, igual que
        # ya se hace al anular una gift card.
        if abs(puntos) > PUNTOS_MAXIMO_AJUSTE_MANUAL:
            return JsonResponse({
                'success': False,
                'error': (
                    f'El ajuste ({puntos:+,} pts) supera el máximo permitido de '
                    f'{PUNTOS_MAXIMO_AJUSTE_MANUAL:,} pts por operación.'
                ).replace(',', '.'),
            }, status=400)

        observaciones = (data.get('observaciones') or '').strip()
        if len(observaciones) < 5:
            return JsonResponse({
                'success': False,
                'error': 'Indica el motivo del ajuste (mínimo 5 caracteres).',
            }, status=400)

        saldo = fidelizacion_service.ajuste_manual(
            cliente, puntos, usuario=request.user,
            observaciones=observaciones,
        )
        return JsonResponse({'success': True, 'saldo_total': saldo})
    except fidelizacion_service.FidelizacionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error en ajuste manual de puntos")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@requiere_permiso('fidelizacion_cuentas', 'puede_crear')
def api_registrar_cliente(request):
    """Alta manual de un cliente para fidelización."""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST
    try:
        cliente, cuenta, creado = fidelizacion_service.registrar_cliente_manual(
            nombre=data.get('nombre', ''),
            apellido=data.get('apellido', ''),
            rut=data.get('rut', ''),
            email=data.get('email', ''),
            celular=data.get('celular', ''),
            fecha_nacimiento=(data.get('fecha_nacimiento') or None),
            genero=data.get('genero', ''),
            usuario=request.user,
            empresa=_empresa_actual(request),
        )
        return JsonResponse({
            'success': True,
            'creado': creado,
            'cliente_id': cliente.id,
            'cliente': cliente.nombre_completo,
            'saldo_puntos': cuenta.saldo_puntos,
        })
    except fidelizacion_service.FidelizacionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error en alta manual de cliente fidelización")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@requiere_permiso('fidelizacion_cuentas', 'puede_editar')
def api_bono_cumpleanos(request):
    """Otorga el bono de cumpleaños al cliente (si hoy es su cumpleaños y no se otorgó aún este año)."""
    try:
        data = json.loads(request.body or '{}')
        cliente = get_object_or_404(Cliente, id=data.get('cliente_id'))
        if not _cliente_en_alcance(request.user, cliente):
            return JsonResponse(
                {'success': False, 'error': 'El cliente no pertenece a tus empresas.'},
                status=403,
            )
        resultado = fidelizacion_service.otorgar_bono_cumpleanos(
            cliente, usuario=request.user
        )
        if resultado is None:
            return JsonResponse({
                'success': False,
                'error': 'No corresponde bono: hoy no es el cumpleaños del cliente, ya se otorgó este año, o el cliente no tiene fecha de nacimiento registrada.',
            }, status=400)
        return JsonResponse({'success': True, **resultado})
    except fidelizacion_service.FidelizacionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error al otorgar bono de cumpleaños")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _kpis_rapidos_fidelizacion(usuario):
    """
    Solo las cifras de cabecera del listado de cuentas: total de clientes,
    puntos circulantes, pasivo estimado y el desglose por nivel.

    Existe para no pagar el reporte completo (24 consultas, ~5 s, incluye
    `detectar_senales_rut_sospechoso`, que recorre en Python todos los tickets
    pagados del rango) solo para pintar cuatro números en la pantalla de
    cuentas. Respeta el mismo alcance por empresa que el reporte grande.
    """
    programa = ProgramaFidelizacion.get_activo()
    valor_pto = programa.valor_punto_en_pesos if programa else 0

    cuentas = CuentaPuntos.objects.all()
    if usuario is not None and not usuario_puede_ver_todas_sucursales(usuario):
        empresas = _empresa_ids_usuario(usuario)
        cuentas = cuentas.filter(Q(cliente__empresa__isnull=True) |
                                 Q(cliente__empresa_id__in=empresas))

    agregado = cuentas.aggregate(total=Count('id'), puntos=Sum('saldo_puntos'))
    total_puntos = agregado['puntos'] or 0

    desglose_niveles = {
        row['nivel']: {
            'cantidad': row['cantidad'],
            'puntos': row['puntos_total'] or 0,
            'valor_pesos': (row['puntos_total'] or 0) * valor_pto,
        }
        for row in cuentas.values('nivel').annotate(
            cantidad=Count('id'), puntos_total=Sum('saldo_puntos'))
    }

    return {
        'total_clientes': agregado['total'] or 0,
        'puntos_circulantes': total_puntos,
        'pasivo_estimado_pesos': total_puntos * valor_pto,
        'desglose_niveles': desglose_niveles,
    }


@require_GET
@requiere_permiso('fidelizacion_reporte', 'puede_ver')
def api_reporte_fidelizacion(request):
    """KPIs y detalle operativo del programa de puntos."""
    _, _, inicio_dt, fin_dt = _rango_reporte(request)
    try:
        dias_vencimiento = int(request.GET.get('vencen_en') or 30)
    except (TypeError, ValueError):
        dias_vencimiento = 30

    # Atajo para los consumidores que solo pintan las tarjetas de cabecera.
    # Es aditivo: sin `?solo_kpis=1` la respuesta es exactamente la de antes.
    if request.GET.get('solo_kpis'):
        return JsonResponse({'success': True, **_kpis_rapidos_fidelizacion(request.user)})

    reporte = construir_reporte_fidelizacion(
        fecha_inicio_dt=inicio_dt,
        fecha_fin_dt=fin_dt,
        dias_vencimiento=dias_vencimiento,
        usuario=request.user,
        mes=request.GET.get('mes'),
    )
    # Compatibilidad con el listado actual: conserva los KPIs en la raíz.
    return JsonResponse({'success': True, **reporte['resumen'], **reporte})


# ========== CANJE EN CAJA (vale de puntos, sesión web) ==========
#
# Por qué existen estos dos endpoints
# -----------------------------------
# El canje estaba implementado de punta a punta EXCEPTO por dos huecos que, en
# conjunto, hacían imposible canjear un solo punto:
#
#   1) El único emisor de vales era la app móvil del cliente
#      (`POST /api/v1/cliente/canje/generar/`). En producción no hay un solo
#      dispositivo con la app instalada, así que nunca existió un código que
#      teclear en caja.
#   2) El POS web validaba el código contra `/api/v1/desktop/canje/<codigo>/`,
#      que exige el permiso `IsAuthorizedDevice` (header `X-Device-ID` + un
#      `DispositivoAutorizado` registrado). Un navegador no manda ese header,
#      así que la caja recibía siempre HTTP 403.
#
# Estos endpoints cierran ambos huecos usando la sesión web normal y los
# permisos por rol del proyecto, sin duplicar ni una regla de negocio: toda la
# lógica (mínimo de canje, incremento, saldo disponible, FIFO, idempotencia,
# `select_for_update`) sigue viviendo en `fidelizacion_service`.

# Un vale emitido en caja es para usarse en la venta que se está cobrando en
# ese momento, no para llevárselo a casa. Con TTL corto, si el cliente se
# arrepiente, sus puntos vuelven solos al saldo disponible en una hora en vez
# de quedar comprometidos tres días.
TTL_HORAS_VALE_CAJA = 1


@require_GET
@requiere_alguno_de_los_permisos('fidelizacion_cuentas', 'ticket_venta')
def api_validar_vale_canje(request, codigo):
    """
    Valida (SIN debitar) un vale de canje por código, para que la caja muestre
    su valor antes de aplicarlo al cobro.

    Permiso: mismo criterio que `api_consultar_saldo_puntos` — basta con
    'fidelizacion_cuentas' O 'ticket_venta'. Leer un código que el cliente
    acaba de mostrar es una capacidad de caja, no de administración del
    maestro de clientes, y el rol `vendedor` tiene 'fidelizacion_cuentas' en
    False.

    Devuelve las MISMAS claves que `/api/v1/desktop/canje/<codigo>/`
    (`estado`, `canjeable`, `puntos`, `valor_pesos`, `cliente_nombre`, …) para
    que el POS pueda apuntar aquí cambiando sólo la URL.
    """
    info = fidelizacion_service.validar_vale(codigo)
    if not info.get('existe'):
        return JsonResponse(
            {'success': False, 'error': 'El código no existe.', 'estado': 'NO_EXISTE'},
            status=404,
        )
    return JsonResponse({'success': True, **info})


@require_POST
@requiere_alguno_de_los_permisos(
    ('ticket_venta', 'puede_crear'), ('fidelizacion_cuentas', 'puede_editar'),
)
def api_generar_vale_canje(request):
    """
    Emite en caja un vale de canje con los puntos del cliente.

    Body JSON: {"cliente_id": 382, "puntos": 6000}

    NO debita el ledger: sólo compromete los puntos (los descuenta del saldo
    disponible) y devuelve un código de un solo uso. El débito real ocurre
    cuando el cobro canjea el vale. Si el cliente se arrepiente, el vale expira
    en `TTL_HORAS_VALE_CAJA` y los puntos vuelven a estar disponibles sin que
    nadie tenga que revertir nada.

    Permiso: quien puede levantar una venta ('ticket_venta.puede_crear' —
    cajero, vendedor, jefe local) o quien administra las cuentas de puntos
    ('fidelizacion_cuentas.puede_editar'). Ayudar al cliente a usar SUS puntos
    es una operación de caja.
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = request.POST

    cliente = get_object_or_404(Cliente, id=data.get('cliente_id'))
    if not _cliente_en_alcance(request.user, cliente):
        return JsonResponse(
            {'success': False, 'error': 'El cliente no pertenece a tus empresas.'},
            status=403,
        )
    try:
        puntos = int(data.get('puntos') or 0)
    except (TypeError, ValueError):
        return JsonResponse(
            {'success': False, 'error': 'Cantidad de puntos inválida.'}, status=400,
        )

    # Idempotencia: si el POS reintenta el mismo vale (doble clic, timeout),
    # `generar_vale_canje` devuelve el vale ya emitido en vez de comprometer
    # los puntos dos veces.
    idem = (request.headers.get('Idempotency-Key')
            or (data.get('idempotency_key') or '').strip() or None)

    try:
        vale = fidelizacion_service.generar_vale_canje(
            cliente, puntos,
            empresa=_empresa_actual(request),
            idempotency_key=idem,
            ttl_horas=TTL_HORAS_VALE_CAJA,
        )
    except fidelizacion_service.FidelizacionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error al emitir vale de canje en caja cliente=%s", cliente.id)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    logger.info(
        "Vale de canje emitido en caja codigo=%s puntos=%s cliente=%s usuario=%s",
        vale.codigo, vale.puntos, cliente.id, request.user.id,
    )
    return JsonResponse({
        'success': True,
        'codigo': vale.codigo,
        'puntos': vale.puntos,
        'valor_pesos': vale.valor_pesos,
        'estado': vale.estado,
        'expira_en': vale.expira_en.isoformat(),
        'cliente': cliente.nombre_completo,
    })


# ========== LANDING PÚBLICA DE DESCARGA DE LA APP ==========

def descargar_app_puntos(request):
    """
    Landing PÚBLICA (sin login) a la que apunta el QR impreso en los tickets
    del POS. La escanea el cliente final con su teléfono.

    Los botones de descarga se configuran por entorno (según decisión
    stores vs APK directo):
      - APP_PUNTOS_URL_ANDROID  (Play Store)
      - APP_PUNTOS_URL_IOS      (App Store)
      - APP_PUNTOS_URL_APK      (APK directo)
    Sin ninguna configurada, muestra "muy pronto" e igual educa sobre
    acumular con el RUT en caja.
    """
    import os
    contexto = {
        'url_android': os.environ.get('APP_PUNTOS_URL_ANDROID', ''),
        'url_ios': os.environ.get('APP_PUNTOS_URL_IOS', ''),
        'url_apk': os.environ.get('APP_PUNTOS_URL_APK', ''),
    }
    return render(
        request, 'vistas/modulo_fidelizacion/descargar_app.html', contexto,
    )
