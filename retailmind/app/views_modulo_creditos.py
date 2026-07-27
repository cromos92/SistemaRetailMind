"""
Módulo de Créditos a Trabajadores - RetailMind
Contiene todas las vistas relacionadas con créditos, pagos y firmas
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Count, Q, Avg
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal

logger = logging.getLogger('app')

from .models import (
    CreditoTrabajador, PagoCreditoTrabajador, FirmaCreditoTrabajador,
    Cliente, Empresa, Sucursal, EmpresaUser,
    ESTADO_CREDITO_CHOICES, TIPO_CREDITO_CHOICES, TIPO_BENEFICIARIO_CHOICES,
    METODO_PAGO_TICKET_CHOICES,
)
from .models.permisos import PermisoUsuario


def _serializar_beneficiario(credito):
    """Serializa el beneficiario (Cliente) de un crédito."""
    if credito.beneficiario:
        b = credito.beneficiario
        return {
            'id': b.id,
            'nombre': b.nombre_completo,
            'rut': b.rut or '',
            'codigo_vendedor': '',
            'empresa': b.empresa.nombre if b.empresa else '',
            'tipo': credito.tipo_beneficiario,
        }
    return {'id': None, 'nombre': 'Sin asignar', 'rut': '', 'codigo_vendedor': '', 'empresa': '', 'tipo': ''}


def _usuario_puede_ver_creditos_todas_sucursales(user):
    return (
        user.is_superuser or
        getattr(user, 'rol', '') == 'administrador' or
        PermisoUsuario.usuario_ve_todas_sucursales(user)
    )


def _alcance_creditos_usuario(request, alcance='actual'):
    """Retorna empresas/sucursales visibles para créditos según sesión/permisos."""
    empresa_actual_id = request.session.get('idEmpresaActual')
    sucursal_actual_id = request.session.get('idSucursalActual')
    puede_todas = _usuario_puede_ver_creditos_todas_sucursales(request.user)

    if puede_todas and alcance == 'todas':
        # Administrador ve todo el universo de créditos. Usuarios con override
        # ven todas las empresas/sucursales que tengan asignadas por EmpresaUser.
        if request.user.is_superuser or getattr(request.user, 'rol', '') == 'administrador':
            empresa_ids = list(Empresa.objects.values_list('id', flat=True))
            sucursal_ids = list(Sucursal.objects.filter(activa=True).values_list('id', flat=True))
        else:
            asignaciones = EmpresaUser.objects.filter(
                user=request.user,
                status=True,
            )
            empresa_ids = list(asignaciones.values_list('empresa_id', flat=True).distinct())
            sucursal_ids = list(
                asignaciones
                .exclude(sucursal_id__isnull=True)
                .values_list('sucursal_id', flat=True)
                .distinct()
            )
            if not empresa_ids and empresa_actual_id:
                empresa_ids = [empresa_actual_id]
            if not sucursal_ids and empresa_ids:
                sucursal_ids = list(
                    Sucursal.objects
                    .filter(empresa_id__in=empresa_ids, activa=True)
                    .values_list('id', flat=True)
                )
        return {
            'empresa_ids': empresa_ids,
            'sucursal_ids': sucursal_ids,
            'alcance': 'todas',
            'puede_todas': True,
        }

    return {
        'empresa_ids': [empresa_actual_id] if empresa_actual_id else [],
        'sucursal_ids': [sucursal_actual_id] if sucursal_actual_id else [],
        'alcance': 'actual',
        'puede_todas': puede_todas,
    }


def _usuario_puede_acceder_credito(request, credito):
    alcance = _alcance_creditos_usuario(request, 'todas' if _usuario_puede_ver_creditos_todas_sucursales(request.user) else 'actual')
    if credito.empresa_origen_id not in alcance['empresa_ids']:
        return False
    if alcance['alcance'] == 'todas':
        if not alcance['sucursal_ids']:
            return True
        return credito.sucursal_id in alcance['sucursal_ids']
    if _usuario_puede_ver_creditos_todas_sucursales(request.user):
        return True
    sucursal_actual_id = request.session.get('idSucursalActual')
    return str(credito.sucursal_id) == str(sucursal_actual_id)


# ========== CARTERA POR COBRAR ==========
#
# Un `CreditoTrabajador` puede significar dos cosas OPUESTAS según su origen:
#
#  1) Créditos importados del sistema Laravel (`numero_credito` CP-INT-* /
#     CP-EXT-*, motivo "Importado desde creditos_personal"): el monto es
#     mercadería YA retirada, es decir DEUDA. Sus `pagos` legacy venían en 0.
#  2) Créditos nativos del ERP (CR-AAAA-NNNN): el monto es un CUPO. La deuda
#     nace recién cuando el beneficiario consume ese cupo en el POS, y ese
#     consumo se guarda -confusamente- como `PagoCreditoTrabajador` con
#     metodo_pago CREDITO_TRABAJADOR / CREDITO_EXTERNO.
#
# Por eso `saldo_pendiente` (= otorgado - monto_pagado) NO es la deuda:
# en los nativos es el cupo que todavía NO se ha usado. La cartera se calcula
# aquí de forma explícita para no sumar peras con manzanas.
METODOS_CONSUMO_CREDITO = ('CREDITO_TRABAJADOR', 'CREDITO_EXTERNO')


# ========== CADUCIDAD DEL CUPO SIN USAR ==========
#
# Regla de negocio pedida por gerencia: "si queda un cupo sin usar y pasan 10
# días no debería poder usarlo".
#
# DOMINIO (verificado contra los datos de producción):
#
#  * "USAR" el cupo = cerrar una venta en el POS cobrada con el método
#    CREDITO_TRABAJADOR / CREDITO_EXTERNO. Eso hace que el POS llame a
#    `usar_credito_en_venta`, que deja un `PagoCreditoTrabajador` con ese
#    método, la sucursal de la venta (Sucursal Uso) y la boleta en
#    `referencia_pago` (Número Boleta). Ese registro ES el uso; no hay otro.
#
#  * EL RELOJ ARRANCA cuando el cupo queda DISPONIBLE, no cuando se solicita:
#    `fecha_aprobacion`. En este ERP `crear_credito_trabajador` deja el crédito
#    ACTIVO y auto-aprobado, así que aprobación y solicitud coinciden; pero si
#    mañana se reactiva el flujo de aprobación manual, contar desde la solicitud
#    castigaría al beneficiario por la demora de quien aprueba. Si por datos
#    migrados no hay `fecha_aprobacion`, se cae a `fecha_solicitud`.
#
#  * CRÉDITO PARCIALMENTE USADO: cada consumo REINICIA el plazo (el cupo está
#    demostradamente vivo y suele gastarse en varias compras). Caduca solamente
#    el REMANENTE; la deuda ya generada por lo consumido NO se toca y el crédito
#    NO cambia de estado, porque `_calcular_cartera_creditos` sólo mira
#    ACTIVO/APROBADO/PAGADO y cancelarlo borraría plata por cobrar de la cartera.
#
#  * TOPE DURO: nunca se puede usar el cupo después de `fecha_vencimiento`.
#    `validar_codigo_credito` ya lo validaba, pero `usar_credito_en_venta` NO:
#    el POS podía consumir un crédito vencido saltándose la validación previa.
#
#  * CRÉDITOS IMPORTADOS (CP-*): su monto NO es cupo, es mercadería ya retirada
#    (deuda). Sin embargo `saldo_pendiente` los muestra con "saldo disponible",
#    o sea que el POS aceptaría gastarlos: 993 créditos por $84,7 MM. Se bloquea
#    siempre su uso en el POS, sin importar fechas ni configuración.
CREDITOS_DIAS_VIGENCIA_CUPO_DEFAULT = 10


def _dias_vigencia_cupo():
    """Días que un cupo sigue disponible. Configurable por entorno."""
    import os
    from django.conf import settings as _settings

    valor = os.environ.get('CREDITOS_DIAS_VIGENCIA_CUPO')
    if valor in (None, ''):
        valor = getattr(_settings, 'CREDITOS_DIAS_VIGENCIA_CUPO', CREDITOS_DIAS_VIGENCIA_CUPO_DEFAULT)
    try:
        dias = int(valor)
    except (TypeError, ValueError):
        logger.warning('CREDITOS_DIAS_VIGENCIA_CUPO invalido (%r), se usa %s',
                       valor, CREDITOS_DIAS_VIGENCIA_CUPO_DEFAULT)
        return CREDITOS_DIAS_VIGENCIA_CUPO_DEFAULT
    return dias if dias > 0 else CREDITOS_DIAS_VIGENCIA_CUPO_DEFAULT


def _caducidad_renueva_con_uso():
    """True si cada consumo reinicia el plazo del cupo remanente."""
    import os
    from django.conf import settings as _settings

    valor = os.environ.get('CREDITOS_CADUCIDAD_RENUEVA_CON_USO')
    if valor in (None, ''):
        valor = getattr(_settings, 'CREDITOS_CADUCIDAD_RENUEVA_CON_USO', True)
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() not in ('0', 'false', 'no', 'off')


def _caducidad_aplica_desde():
    """Fecha desde la cual rige la caducidad (None = rige para todos).

    Permite decidir si la regla se aplica retroactivamente o sólo a los créditos
    nuevos, sin tocar código: `CREDITOS_CADUCIDAD_DESDE=2026-08-01`.
    """
    import os
    from django.conf import settings as _settings

    valor = os.environ.get('CREDITOS_CADUCIDAD_DESDE')
    if valor in (None, ''):
        valor = getattr(_settings, 'CREDITOS_CADUCIDAD_DESDE', None)
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        partes = [int(p) for p in str(valor).strip().split('-')]
        return date(partes[0], partes[1], partes[2])
    except (ValueError, IndexError):
        logger.warning('CREDITOS_CADUCIDAD_DESDE invalido (%r), se ignora', valor)
        return None


def _consumo_de_credito(credito):
    """(total consumido en POS, fecha del último consumo) de un crédito.

    Usa `credito.pagos` ya precargado si existe (prefetch_related) para no
    disparar una query por crédito dentro del listado.
    """
    total = Decimal('0')
    ultimo = None
    for pago in credito.pagos.all():
        if pago.metodo_pago not in METODOS_CONSUMO_CREDITO:
            continue
        total += Decimal(str(pago.monto_pago or 0))
        if pago.fecha_pago and (ultimo is None or pago.fecha_pago > ultimo):
            ultimo = pago.fecha_pago
    return total, ultimo


def _estado_caducidad_cupo(credito, consumido=None, ultimo_consumo=None, dias=None):
    """Evalúa la caducidad del cupo remanente de un crédito.

    Devuelve un dict con la decisión y su fundamento. Es una regla CALCULADA
    (no un campo en la BD): así rige aunque nadie haya corrido el comando de
    caducidad y sin necesitar una migración.
    """
    hoy = timezone.localdate()
    dias = _dias_vigencia_cupo() if dias is None else dias

    if consumido is None:
        consumido, ultimo_consumo = _consumo_de_credito(credito)
    consumido = Decimal(str(consumido or 0))

    otorgado = Decimal(str(credito.monto_aprobado or credito.monto_solicitado or 0))
    remanente = otorgado - consumido

    base_disponible = None
    if credito.fecha_aprobacion:
        base_disponible = timezone.localtime(credito.fecha_aprobacion).date()
    elif credito.fecha_solicitud:
        base_disponible = timezone.localtime(credito.fecha_solicitud).date()

    base = base_disponible
    if _caducidad_renueva_con_uso() and ultimo_consumo and base:
        base = max(base, ultimo_consumo)

    fecha_limite = (base + timedelta(days=dias)) if base else None

    tope_vencimiento = credito.fecha_vencimiento
    if fecha_limite and tope_vencimiento and tope_vencimiento < fecha_limite:
        fecha_limite = tope_vencimiento
        motivo_tope = 'VENCIMIENTO'
    else:
        motivo_tope = 'PLAZO'

    resultado = {
        'caducado': False,
        'motivo': None,
        'mensaje': '',
        'fecha_limite': fecha_limite,
        'fecha_base': base,
        'dias_vigencia': dias,
        'consumido': float(consumido),
        'remanente': float(remanente),
        'dias_para_caducar': (fecha_limite - hoy).days if fecha_limite else None,
    }

    # Los importados de Laravel no son cupo: su monto ya se retiró en mercadería.
    if _es_credito_legacy(credito.numero_credito):
        resultado.update({
            'caducado': True,
            'motivo': 'LEGACY',
            'mensaje': ('Crédito importado del sistema anterior: su monto es deuda '
                        'ya retirada, no un cupo disponible para comprar.'),
        })
        return resultado

    if remanente <= 0:
        return resultado

    desde = _caducidad_aplica_desde()
    if desde and base_disponible and base_disponible < desde:
        resultado['mensaje'] = f'Caducidad no retroactiva (rige desde {desde.strftime("%d/%m/%Y")}).'
        return resultado

    if fecha_limite and fecha_limite < hoy:
        dias_pasados = (hoy - fecha_limite).days
        resultado.update({
            'caducado': True,
            'motivo': motivo_tope,
            'mensaje': (
                f'El cupo caducó el {fecha_limite.strftime("%d/%m/%Y")} '
                f'(hace {dias_pasados} día{"s" if dias_pasados != 1 else ""}). '
                + ('Superó la fecha de vencimiento del crédito.' if motivo_tope == 'VENCIMIENTO'
                   else f'Los cupos sin usar caducan a los {dias} días.')
            ),
        })
    return resultado


BUCKETS_ANTIGUEDAD = (
    ('por_vencer', 'Por vencer'),
    ('d1_30', '1 a 30 días'),
    ('d31_60', '31 a 60 días'),
    ('d61_90', '61 a 90 días'),
    ('d91_180', '91 a 180 días'),
    ('d181_365', '181 a 365 días'),
    ('d365_mas', 'Más de 1 año'),
)


def _bucket_antiguedad(dias_vencido):
    if dias_vencido <= 0:
        return 'por_vencer'
    if dias_vencido <= 30:
        return 'd1_30'
    if dias_vencido <= 60:
        return 'd31_60'
    if dias_vencido <= 90:
        return 'd61_90'
    if dias_vencido <= 180:
        return 'd91_180'
    if dias_vencido <= 365:
        return 'd181_365'
    return 'd365_mas'


def _es_credito_legacy(numero_credito):
    return bool(numero_credito) and numero_credito.upper().startswith('CP-')


def _calcular_cartera_creditos(alcance_info):
    """Fotografía de la cartera por cobrar AL DÍA DE HOY.

    No aplica el filtro de fechas de la pantalla: una deuda de 2022 sigue
    siendo deuda aunque el usuario esté mirando el mes en curso.
    """
    hoy = timezone.localdate()

    creditos = CreditoTrabajador.objects.filter(
        empresa_origen_id__in=alcance_info['empresa_ids'],
        estado__in=['ACTIVO', 'APROBADO', 'PAGADO'],
    )
    if alcance_info['sucursal_ids']:
        creditos = creditos.filter(sucursal_id__in=alcance_info['sucursal_ids'])

    filas = list(creditos.values(
        'id', 'numero_credito', 'estado', 'monto_aprobado', 'monto_solicitado',
        'monto_pagado', 'fecha_vencimiento', 'fecha_solicitud',
        'beneficiario_id', 'beneficiario__nombre', 'beneficiario__apellido',
        'beneficiario__rut', 'sucursal__alias', 'empresa_origen__nombre',
    ))
    ids = [f['id'] for f in filas]

    consumos = {}
    abonos = {}
    if ids:
        for cid, metodo, total in (
            PagoCreditoTrabajador.objects
            .filter(credito_id__in=ids)
            .values_list('credito_id', 'metodo_pago')
            .annotate(total=Sum('monto_pago'))
        ):
            destino = consumos if metodo in METODOS_CONSUMO_CREDITO else abonos
            destino[cid] = destino.get(cid, 0.0) + float(total or 0)

    buckets = {clave: {'label': label, 'n': 0, 'monto': 0.0} for clave, label in BUCKETS_ANTIGUEDAD}
    deudores = {}
    total_deuda = 0.0
    total_vencida = 0.0
    total_por_vencer = 0.0
    total_otorgado = 0.0
    total_consumido = 0.0
    total_abonado = 0.0
    cupo_disponible = 0.0
    deuda_legacy = 0.0
    deuda_nativa = 0.0
    n_legacy_con_consumo = 0
    detalle_vencidos = []

    for f in filas:
        otorgado = float(f['monto_aprobado'] or f['monto_solicitado'] or 0)
        consumo = consumos.get(f['id'], 0.0)
        abono = abonos.get(f['id'], 0.0)
        legacy = _es_credito_legacy(f['numero_credito'])

        total_otorgado += otorgado
        total_consumido += consumo
        total_abonado += abono

        if legacy:
            # El monto importado ya es mercadería retirada.
            deuda = otorgado - abono
            if consumo > 0:
                n_legacy_con_consumo += 1
        else:
            # Solo se debe lo efectivamente consumido del cupo.
            deuda = consumo - abono
            if f['estado'] in ('ACTIVO', 'APROBADO'):
                cupo_disponible += max(otorgado - consumo, 0.0)

        deuda = round(deuda, 2)
        if deuda <= 0:
            continue

        dias_vencido = (hoy - f['fecha_vencimiento']).days if f['fecha_vencimiento'] else 0
        clave = _bucket_antiguedad(dias_vencido)
        buckets[clave]['n'] += 1
        buckets[clave]['monto'] += deuda

        total_deuda += deuda
        if dias_vencido > 0:
            total_vencida += deuda
        else:
            total_por_vencer += deuda

        if legacy:
            deuda_legacy += deuda
        else:
            deuda_nativa += deuda

        nombre = f"{f['beneficiario__nombre'] or ''} {f['beneficiario__apellido'] or ''}".strip() or 'Sin asignar'
        clave_deudor = f['beneficiario_id'] or f"s/{nombre}"
        d = deudores.setdefault(clave_deudor, {
            'beneficiario_id': f['beneficiario_id'],
            'nombre': nombre,
            'rut': f['beneficiario__rut'] or '',
            'documentos': 0,
            'deuda': 0.0,
            'deuda_vencida': 0.0,
            'dias_mora_max': 0,
        })
        d['documentos'] += 1
        d['deuda'] += deuda
        if dias_vencido > 0:
            d['deuda_vencida'] += deuda
            d['dias_mora_max'] = max(d['dias_mora_max'], dias_vencido)

        if dias_vencido > 0:
            detalle_vencidos.append({
                'numero_credito': f['numero_credito'],
                'beneficiario': nombre,
                'rut': f['beneficiario__rut'] or '',
                'sucursal': f['sucursal__alias'] or '',
                'deuda': deuda,
                'dias_vencido': dias_vencido,
                'fecha_vencimiento': f['fecha_vencimiento'].strftime('%d/%m/%Y') if f['fecha_vencimiento'] else '',
                'origen': 'Importado' if legacy else 'ERP',
            })

    top_deudores = sorted(deudores.values(), key=lambda x: x['deuda'], reverse=True)[:15]
    detalle_vencidos.sort(key=lambda x: x['deuda'], reverse=True)

    return {
        'fecha_corte': hoy.strftime('%d/%m/%Y'),
        'deuda_total': round(total_deuda, 2),
        'deuda_vencida': round(total_vencida, 2),
        'deuda_por_vencer': round(total_por_vencer, 2),
        'porcentaje_vencido': round((total_vencida / total_deuda * 100), 1) if total_deuda else 0,
        'deudores': len(deudores),
        'documentos_con_deuda': sum(b['n'] for b in buckets.values()),
        'deuda_importada': round(deuda_legacy, 2),
        'deuda_erp': round(deuda_nativa, 2),
        'cupo_otorgado': round(total_otorgado, 2),
        'consumido_pos': round(total_consumido, 2),
        'abonos_registrados': round(total_abonado, 2),
        'cupo_disponible_sin_usar': round(cupo_disponible, 2),
        'legacy_con_consumo_pos': n_legacy_con_consumo,
        'antiguedad': [
            {'clave': clave, 'label': buckets[clave]['label'],
             'n': buckets[clave]['n'], 'monto': round(buckets[clave]['monto'], 2)}
            for clave, _ in BUCKETS_ANTIGUEDAD
        ],
        'top_deudores': [
            {**d, 'deuda': round(d['deuda'], 2), 'deuda_vencida': round(d['deuda_vencida'], 2)}
            for d in top_deudores
        ],
        'vencidos': detalle_vencidos[:200],
        'total_vencidos': len(detalle_vencidos),
    }


def _ventas_credito_sin_respaldo(alcance_info, dias=730):
    """Ventas del POS cobradas con crédito que NO dejaron registro en el módulo.

    El POS marca el pago del ticket como CREDITO_TRABAJADOR/CREDITO_EXTERNO y
    recién DESPUÉS de cerrar la venta llama a `usar_credito_en_venta`. Si esa
    segunda llamada falla (o el pago es un crédito externo escrito a mano, sin
    `credito_id`), la venta queda cobrada contra un crédito que nunca se debitó
    y la deuda no aparece en ninguna parte.
    """
    from .models import TicketDetallePago

    from datetime import timedelta

    desde = timezone.now() - timedelta(days=dias)
    pagos_pos = (
        TicketDetallePago.objects
        .filter(metodo_pago__in=METODOS_CONSUMO_CREDITO, ticket__created_at__gte=desde)
        .select_related('ticket', 'ticket__sucursal')
    )
    if alcance_info['sucursal_ids']:
        pagos_pos = pagos_pos.filter(ticket__sucursal_id__in=alcance_info['sucursal_ids'])

    registrados = set()
    for numero, monto in (
        PagoCreditoTrabajador.objects
        .filter(created_at__gte=desde)
        .values_list('credito__numero_credito', 'monto_pago')
    ):
        registrados.add((numero, float(monto)))

    huerfanos = []
    total = 0.0
    cantidad = 0
    for pago in pagos_pos.order_by('-ticket__created_at'):
        notas = pago.notas or ''
        match = re.search(r'(C[RP]-[A-Za-z0-9\-]+)', notas)
        if match and (match.group(1), float(pago.monto)) in registrados:
            continue
        monto = float(pago.monto or 0)
        total += monto
        cantidad += 1
        if len(huerfanos) < 50:
            nombre = ''
            if notas.strip().startswith('{'):
                try:
                    nombre = (json.loads(notas) or {}).get('nombre', '')
                except (ValueError, TypeError):
                    nombre = ''
            huerfanos.append({
                'fecha': pago.ticket.created_at.strftime('%d/%m/%Y'),
                'metodo': pago.metodo_pago,
                'monto': monto,
                'ticket': pago.ticket_id,
                'correlativo': pago.ticket.correlativo,
                'sucursal': pago.ticket.sucursal.alias if pago.ticket.sucursal_id else '',
                'cliente': nombre or (match.group(1) if match else ''),
            })

    return {'cantidad': cantidad, 'monto': round(total, 2), 'detalle': huerfanos}


# ========== GESTIÓN DE CRÉDITOS ==========

@login_required
def gestion_creditos(request):
    """Vista principal para gestión de créditos a trabajadores"""
    sucursal_id = request.session.get('idSucursalActual')
    puede_ver_todas = (
        request.user.is_superuser or
        getattr(request.user, 'rol', '') == 'administrador' or
        PermisoUsuario.usuario_ve_todas_sucursales(request.user)
    )
    sucursal_actual = None
    if sucursal_id:
        try:
            from .models import Sucursal
            sucursal_actual = Sucursal.objects.get(id=sucursal_id)
        except Exception:
            pass
    context = {
        'qz_config': {
            'habilitado': getattr(sucursal_actual, 'usar_qz_tray', False) if sucursal_actual else False,
            'nombre_impresora': (
                getattr(sucursal_actual, 'nombre_impresora_termica', 'EPSON TM-T20II') or 'EPSON TM-T20II'
            ) if sucursal_actual else 'EPSON TM-T20II',
        },
        'puede_ver_todas_sucursales': puede_ver_todas,
    }
    return render(request, 'vistas/modulo_administracion/gestion_creditos.html', context)


@login_required
@require_POST
def crear_credito_trabajador(request):
    """Crear una nueva solicitud de crédito para trabajador"""
    try:
        data = json.loads(request.body)
        
        # Validar datos requeridos
        trabajador_id = data.get('trabajador_id')
        monto_solicitado = data.get('monto_solicitado')
        fecha_vencimiento = data.get('fecha_vencimiento')
        motivo_solicitud = data.get('motivo_solicitud')
        tipo_credito = data.get('tipo_credito', 'PRESTAMO_EMPRESA')
        
        if not all([trabajador_id, monto_solicitado, fecha_vencimiento, motivo_solicitud]):
            return JsonResponse({
                'success': False,
                'error': 'Trabajador, monto, fecha de vencimiento y motivo son requeridos'
            }, status=400)
        
        # Obtener empresa y sucursal actual
        empresa_actual_id = request.session.get('idEmpresaActual')
        sucursal_actual_id = request.session.get('idSucursalActual')
        
        if not empresa_actual_id or not sucursal_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa o sucursal activa en la sesión'
            }, status=400)
        
        # Validar beneficiario (Cliente)
        beneficiario = get_object_or_404(Cliente, id=trabajador_id)
        empresa = get_object_or_404(Empresa, id=empresa_actual_id)
        sucursal = get_object_or_404(Sucursal, id=sucursal_actual_id)
        
        tipo_beneficiario = data.get('tipo_beneficiario', 'EMPLEADO')

        # Validar monto
        try:
            monto_solicitado = Decimal(str(monto_solicitado))
            if monto_solicitado <= 0:
                raise ValueError("El monto debe ser mayor a 0")
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Monto inválido'
            }, status=400)
        
        # Crear crédito directamente ACTIVO (sin aprobación)
        credito = CreditoTrabajador.objects.create(
            beneficiario=beneficiario,
            tipo_beneficiario=tipo_beneficiario,
            empresa_origen=empresa,
            sucursal=sucursal,
            tipo_credito=tipo_credito,
            monto_solicitado=monto_solicitado,
            monto_aprobado=monto_solicitado,  # Auto-aprobado por el mismo monto
            fecha_vencimiento=fecha_vencimiento,
            motivo_solicitud=motivo_solicitud,
            observaciones_solicitud=data.get('observaciones_solicitud', ''),
            tasa_interes=Decimal(str(data.get('tasa_interes', 0))),
            numero_cuotas=int(data.get('numero_cuotas', 1)),
            requiere_aval=data.get('requiere_aval', False),
            aval_nombre=data.get('aval_nombre', ''),
            aval_rut=data.get('aval_rut', ''),
            aval_telefono=data.get('aval_telefono', ''),
            solicitado_por=request.user,
            autorizado_por=request.user,  # Auto-autorizado
            fecha_primer_pago=data.get('fecha_primer_pago'),
            estado='ACTIVO',  # Directamente ACTIVO
            fecha_aprobacion=timezone.now()  # Fecha de aprobación inmediata
        )
        
        # Crear registro de firma
        FirmaCreditoTrabajador.objects.create(credito=credito)
        
        return JsonResponse({
            'success': True,
            'message': 'Crédito creado y activado exitosamente',
            'credito_id': credito.id,
            'numero_credito': credito.numero_credito,
            'monto_aprobado': float(credito.monto_aprobado),
            'trabajador': credito.nombre_beneficiario,
            'imprimir_url': f'/app/api/creditos/imprimir-voucher/{credito.id}/'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear crédito: {str(e)}'
        }, status=500)


def _normalizar_fecha_filtro(fecha_str):
    """Normaliza DD/MM/AAAA o DD-MM-AAAA a AAAA-MM-DD."""
    if not fecha_str:
        return None
    fecha_str = str(fecha_str).strip()
    if '/' in fecha_str:
        partes = fecha_str.split('/')
    elif '-' in fecha_str:
        partes = fecha_str.split('-')
    else:
        return fecha_str
    if len(partes) != 3:
        return fecha_str
    if len(partes[0]) == 2:
        return f"{partes[2]}-{partes[1]}-{partes[0]}"
    return fecha_str


# Filtros que acepta el listado. El PDF usa EXACTAMENTE los mismos (por GET),
# para que el papel sea lo que el usuario está viendo en pantalla.
FILTROS_CREDITOS = (
    'fecha_inicio', 'fecha_fin', 'estado', 'trabajador_id', 'tipo_credito',
    'numero_credito', 'trabajador_texto', 'sucursal_texto', 'saldo_min',
    'saldo_max', 'alcance',
)


def _queryset_creditos_filtrado(request, data):
    """Arma el queryset de créditos aplicando alcance + filtros de pantalla.

    Devuelve (queryset, alcance_info, error). `error` es un string listo para
    devolver al cliente; si viene distinto de None, queryset es None.
    """
    alcance = data.get('alcance') or 'actual'
    alcance_info = _alcance_creditos_usuario(request, alcance)
    if not alcance_info['empresa_ids']:
        return None, alcance_info, 'No hay empresas disponibles para consultar créditos'

    queryset = CreditoTrabajador.objects.filter(
        empresa_origen_id__in=alcance_info['empresa_ids']
    ).select_related(
        'beneficiario', 'beneficiario__empresa', 'empresa_origen', 'sucursal',
        'autorizado_por', 'solicitado_por',
    ).prefetch_related('pagos__sucursal_cobro', 'pagos__registrado_por')

    if alcance_info['sucursal_ids']:
        queryset = queryset.filter(sucursal_id__in=alcance_info['sucursal_ids'])
    elif alcance_info['alcance'] != 'todas':
        return None, alcance_info, 'No hay sucursal activa en la sesión'

    fecha_inicio = _normalizar_fecha_filtro(data.get('fecha_inicio'))
    fecha_fin = _normalizar_fecha_filtro(data.get('fecha_fin'))
    if fecha_inicio:
        queryset = queryset.filter(fecha_solicitud__date__gte=fecha_inicio)
    if fecha_fin:
        queryset = queryset.filter(fecha_solicitud__date__lte=fecha_fin)

    estado = data.get('estado')
    if estado == 'VENCIDO':
        # Ningún proceso escribe estado='VENCIDO' en la BD (0 registros en
        # producción), así que filtrar por el literal devolvía siempre vacío.
        # "Vencido" es una condición calculada: activo/aprobado y con la
        # fecha de vencimiento pasada.
        queryset = queryset.filter(
            estado__in=['ACTIVO', 'APROBADO'],
            fecha_vencimiento__lt=timezone.localdate(),
        )
    elif estado:
        queryset = queryset.filter(estado=estado)

    if data.get('trabajador_id'):
        queryset = queryset.filter(beneficiario_id=data['trabajador_id'])

    if data.get('tipo_credito'):
        queryset = queryset.filter(tipo_credito=data['tipo_credito'])

    if data.get('numero_credito'):
        queryset = queryset.filter(numero_credito__icontains=data['numero_credito'])

    if data.get('trabajador_texto'):
        texto = str(data['trabajador_texto']).strip()
        queryset = queryset.filter(
            Q(beneficiario__nombre__icontains=texto) |
            Q(beneficiario__apellido__icontains=texto) |
            Q(beneficiario__rut__icontains=texto)
        )

    if data.get('sucursal_texto'):
        texto = str(data['sucursal_texto']).strip()
        queryset = queryset.filter(
            Q(sucursal__alias__icontains=texto) |
            Q(sucursal__direccion__icontains=texto)
        )

    saldo_min = data.get('saldo_min')
    saldo_max = data.get('saldo_max')
    if saldo_min or saldo_max:
        saldo_expr = ExpressionWrapper(
            Coalesce('monto_aprobado', 'monto_solicitado') - F('monto_pagado'),
            output_field=DecimalField(max_digits=12, decimal_places=2)
        )
        queryset = queryset.annotate(saldo_calc=saldo_expr)
        try:
            if saldo_min is not None and saldo_min != '':
                queryset = queryset.filter(saldo_calc__gte=float(saldo_min))
        except (ValueError, TypeError):
            pass
        try:
            if saldo_max is not None and saldo_max != '':
                queryset = queryset.filter(saldo_calc__lte=float(saldo_max))
        except (ValueError, TypeError):
            pass

    return queryset.order_by('-fecha_solicitud'), alcance_info, None


@login_required
@require_POST
def cargar_creditos_trabajadores(request):
    """Cargar créditos con filtros y paginación"""
    try:
        data = json.loads(request.body)

        # Parámetros de paginación
        page = int(data.get('page', 1))
        per_page = int(data.get('per_page', 20))

        queryset, alcance_info, error = _queryset_creditos_filtrado(request, data)
        if error:
            return JsonResponse({'success': False, 'error': error}, status=400)

        # Paginación
        paginator = Paginator(queryset, per_page)
        creditos_page = paginator.get_page(page)
        
        # Serializar datos
        creditos_data = []
        for credito in creditos_page:
            # Obtener TODOS los usos/pagos del crédito.
            # Un crédito puede usarse en varias compras (varias boletas / varias
            # sucursales), por eso se devuelven todos los usos ordenados
            # cronológicamente; el reporte luego los agrega.
            usos = []
            consumo_pos = 0.0
            abonos_reales = 0.0
            ultimo_consumo = None
            # Se usa la lista precargada (prefetch_related) en vez de volver a
            # consultar por cada crédito: antes eran N queries por página.
            for pago in sorted(credito.pagos.all(), key=lambda p: (p.fecha_pago, p.created_at)):
                if pago.metodo_pago in METODOS_CONSUMO_CREDITO:
                    consumo_pos += float(pago.monto_pago or 0)
                    if pago.fecha_pago and (ultimo_consumo is None or pago.fecha_pago > ultimo_consumo):
                        ultimo_consumo = pago.fecha_pago
                else:
                    abonos_reales += float(pago.monto_pago or 0)
                # Intentar extraer número de boleta de la referencia o de las observaciones
                numero_boleta = ''
                if pago.referencia_pago:
                    numero_boleta = pago.referencia_pago.strip()
                elif pago.observaciones:
                    # Buscar patrón de ticket en observaciones
                    match = re.search(r'Ticket\s*#?(\d+)', pago.observaciones)
                    if match:
                        numero_boleta = match.group(1)

                usos.append({
                    'sucursal_cobro': pago.sucursal_cobro.alias if pago.sucursal_cobro else '',
                    'sucursal_direccion': (pago.sucursal_cobro.direccion or '') if pago.sucursal_cobro else '',
                    'numero_boleta': numero_boleta,
                    'fecha_pago': pago.fecha_pago.strftime('%d/%m/%Y'),
                    'monto_pago': float(pago.monto_pago),
                    'metodo_pago': pago.get_metodo_pago_display(),
                    'registrado_por': pago.registrado_por.username if pago.registrado_por else ''
                })

            # Compatibilidad: último uso/pago registrado
            pago_info = usos[-1] if usos else None

            # Deuda real (ver comentario del bloque "CARTERA POR COBRAR"):
            #  - importados de Laravel: el monto ya es mercadería retirada
            #  - nativos del ERP: solo se debe lo consumido en el POS
            es_legacy = _es_credito_legacy(credito.numero_credito)
            otorgado = float(credito.monto_aprobado or credito.monto_solicitado or 0)
            if es_legacy:
                deuda = otorgado - abonos_reales
            else:
                deuda = consumo_pos - abonos_reales
            if credito.estado in ('PENDIENTE', 'RECHAZADO'):
                deuda = 0.0
            deuda = round(max(deuda, 0.0), 2)
            dias_vencido = (timezone.localdate() - credito.fecha_vencimiento).days if credito.fecha_vencimiento else 0

            # Caducidad del cupo remanente (regla calculada, ver bloque de arriba).
            # Se manda al frontend para que pueda marcar la fila; el bloqueo real
            # ocurre en `validar_codigo_credito` / `usar_credito_en_venta`.
            caducidad = _estado_caducidad_cupo(
                credito, consumido=consumo_pos, ultimo_consumo=ultimo_consumo,
            )

            creditos_data.append({
                'cupo_caducado': caducidad['caducado'],
                'motivo_caducidad': caducidad['motivo'],
                'mensaje_caducidad': caducidad['mensaje'],
                'cupo_remanente': round(caducidad['remanente'], 2),
                'fecha_limite_uso': caducidad['fecha_limite'].strftime('%d/%m/%Y') if caducidad['fecha_limite'] else None,
                'dias_para_caducar': caducidad['dias_para_caducar'],
                'dias_vigencia_cupo': caducidad['dias_vigencia'],
                'es_legacy': es_legacy,
                'origen_registro': 'Importado' if es_legacy else 'ERP',
                'consumo_pos': round(consumo_pos, 2),
                'abonos_reales': round(abonos_reales, 2),
                'deuda': deuda,
                'dias_vencido': dias_vencido if dias_vencido > 0 else 0,
                'vencido_real': bool(deuda > 0 and dias_vencido > 0),
                'id': credito.id,
                'numero_credito': credito.numero_credito,
                'trabajador': _serializar_beneficiario(credito),
                'empresa': {
                    'id': credito.empresa_origen.id,
                    'nombre': credito.empresa_origen.nombre,
                    'rut': credito.empresa_origen.rut
                },
                'tipo_credito': credito.tipo_credito,
                'tipo_credito_display': credito.get_tipo_credito_display(),
                'monto_solicitado': float(credito.monto_solicitado),
                'monto_aprobado': float(credito.monto_aprobado) if credito.monto_aprobado else None,
                'monto_pagado': float(credito.monto_pagado),
                'saldo_pendiente': credito.saldo_pendiente,
                'porcentaje_pagado': credito.porcentaje_pagado,
                'estado': credito.estado,
                'estado_display': credito.get_estado_display(),
                'fecha_solicitud': credito.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
                'fecha_vencimiento': credito.fecha_vencimiento.strftime('%d/%m/%Y'),
                'fecha_aprobacion': credito.fecha_aprobacion.strftime('%d/%m/%Y %H:%M') if credito.fecha_aprobacion else None,
                'autorizado_por': credito.autorizado_por.username if credito.autorizado_por else None,
                'solicitado_por': credito.solicitado_por.username,
                'sucursal': credito.sucursal.alias,
                'sucursal_direccion': credito.sucursal.direccion if credito.sucursal.direccion else '',
                'esta_vencido': credito.esta_vencido,
                'dias_para_vencimiento': credito.dias_para_vencimiento,
                'numero_cuotas': credito.numero_cuotas,
                'valor_cuota': float(credito.valor_cuota) if credito.valor_cuota else None,
                'tasa_interes': float(credito.tasa_interes),
                'requiere_aval': credito.requiere_aval,
                'ultimo_pago': pago_info,
                'usos': usos,
            })
        
        return JsonResponse({
            'success': True,
            'creditos': creditos_data,
            'alcance': alcance_info['alcance'],
            'puede_ver_todas_sucursales': alcance_info['puede_todas'],
            'pagination': {
                'current_page': creditos_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': creditos_page.has_next(),
                'has_previous': creditos_page.has_previous(),
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al cargar créditos: {str(e)}'
        }, status=500)


@login_required
@require_GET
def detalle_credito_trabajador(request, credito_id):
    """Obtener detalles completos de un crédito"""
    try:
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver este crédito'
            }, status=403)
        puede_ver_todas = (
            request.user.is_superuser or
            getattr(request.user, 'rol', '') == 'administrador' or
            PermisoUsuario.usuario_ve_todas_sucursales(request.user)
        )
        sucursal_actual_id = request.session.get('idSucursalActual')
        if not puede_ver_todas and str(credito.sucursal_id) != str(sucursal_actual_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver créditos de otra sucursal'
            }, status=403)
        
        # Obtener pagos del crédito
        pagos = []
        consumo_pos = 0.0
        abonos_reales = 0.0
        for pago in credito.pagos.select_related('sucursal_cobro', 'registrado_por').all():
            if pago.metodo_pago in METODOS_CONSUMO_CREDITO:
                consumo_pos += float(pago.monto_pago or 0)
            else:
                abonos_reales += float(pago.monto_pago or 0)
            pagos.append({
                'id': pago.id,
                'numero_pago': pago.numero_pago,
                'monto_pago': float(pago.monto_pago),
                'fecha_pago': pago.fecha_pago.strftime('%d/%m/%Y'),
                'metodo_pago': pago.metodo_pago,
                'metodo_pago_display': pago.get_metodo_pago_display(),
                'numero_cuota': pago.numero_cuota,
                'es_pago_total': pago.es_pago_total,
                'referencia_pago': pago.referencia_pago or '',
                'registrado_por': pago.registrado_por.username,
                'sucursal_cobro': pago.sucursal_cobro.alias if pago.sucursal_cobro else None,
                'sucursal_cobro_direccion': pago.sucursal_cobro.direccion if pago.sucursal_cobro else None,
                'observaciones': pago.observaciones or '',
                'fecha_registro': pago.created_at.strftime('%d/%m/%Y %H:%M')
            })
        
        # Obtener datos de firma
        firma_data = None
        if hasattr(credito, 'firma'):
            firma = credito.firma
            firma_data = {
                'firmado_por_trabajador': firma.firmado_por_trabajador,
                'fecha_firma_trabajador': firma.fecha_firma_trabajador.strftime('%d/%m/%Y %H:%M') if firma.fecha_firma_trabajador else None,
                'firmado_por_autorizador': firma.firmado_por_autorizador,
                'fecha_firma_autorizador': firma.fecha_firma_autorizador.strftime('%d/%m/%Y %H:%M') if firma.fecha_firma_autorizador else None,
                'firmado_por_aval': firma.firmado_por_aval,
                'fecha_firma_aval': firma.fecha_firma_aval.strftime('%d/%m/%Y %H:%M') if firma.fecha_firma_aval else None,
                'esta_completamente_firmado': firma.esta_completamente_firmado
            }
        
        es_legacy = _es_credito_legacy(credito.numero_credito)
        otorgado = float(credito.monto_aprobado or credito.monto_solicitado or 0)
        deuda = (otorgado - abonos_reales) if es_legacy else (consumo_pos - abonos_reales)
        if credito.estado in ('PENDIENTE', 'RECHAZADO'):
            deuda = 0.0

        caducidad = _estado_caducidad_cupo(credito)

        credito_data = {
            'cupo_caducado': caducidad['caducado'],
            'motivo_caducidad': caducidad['motivo'],
            'mensaje_caducidad': caducidad['mensaje'],
            'cupo_remanente': round(caducidad['remanente'], 2),
            'fecha_limite_uso': caducidad['fecha_limite'].strftime('%d/%m/%Y') if caducidad['fecha_limite'] else None,
            'dias_para_caducar': caducidad['dias_para_caducar'],
            'dias_vigencia_cupo': caducidad['dias_vigencia'],
            'id': credito.id,
            'numero_credito': credito.numero_credito,
            'es_legacy': es_legacy,
            'origen_registro': 'Importado' if es_legacy else 'ERP',
            'consumo_pos': round(consumo_pos, 2),
            'abonos_reales': round(abonos_reales, 2),
            'deuda': round(max(deuda, 0.0), 2),
            'trabajador': _serializar_beneficiario(credito),
            'empresa_origen': {
                'id': credito.empresa_origen.id,
                'nombre': credito.empresa_origen.nombre,
                'rut': credito.empresa_origen.rut
            },
            'sucursal': {
                'id': credito.sucursal.id,
                'alias': credito.sucursal.alias,
                'direccion': credito.sucursal.direccion
            },
            'tipo_credito': credito.tipo_credito,
            'tipo_credito_display': credito.get_tipo_credito_display(),
            'monto_solicitado': float(credito.monto_solicitado),
            'monto_aprobado': float(credito.monto_aprobado) if credito.monto_aprobado else None,
            'monto_pagado': float(credito.monto_pagado),
            'saldo_pendiente': credito.saldo_pendiente,
            'porcentaje_pagado': credito.porcentaje_pagado,
            'estado': credito.estado,
            'estado_display': credito.get_estado_display(),
            'fecha_solicitud': credito.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
            'fecha_aprobacion': credito.fecha_aprobacion.strftime('%d/%m/%Y %H:%M') if credito.fecha_aprobacion else None,
            'fecha_vencimiento': credito.fecha_vencimiento.strftime('%d/%m/%Y'),
            'fecha_primer_pago': credito.fecha_primer_pago.strftime('%d/%m/%Y') if credito.fecha_primer_pago else None,
            'autorizado_por': credito.autorizado_por.username if credito.autorizado_por else None,
            'solicitado_por': credito.solicitado_por.username,
            'tasa_interes': float(credito.tasa_interes),
            'numero_cuotas': credito.numero_cuotas,
            'valor_cuota': float(credito.valor_cuota) if credito.valor_cuota else None,
            'motivo_solicitud': credito.motivo_solicitud,
            'observaciones_solicitud': credito.observaciones_solicitud or '',
            'observaciones_aprobacion': credito.observaciones_aprobacion or '',
            'observaciones_rechazo': credito.observaciones_rechazo or '',
            'requiere_aval': credito.requiere_aval,
            'aval_nombre': credito.aval_nombre or '',
            'aval_rut': credito.aval_rut or '',
            'aval_telefono': credito.aval_telefono or '',
            'esta_vencido': credito.esta_vencido,
            'dias_para_vencimiento': credito.dias_para_vencimiento,
            'pagos': pagos,
            'firma': firma_data
        }
        
        return JsonResponse({
            'success': True,
            'credito': credito_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener crédito: {str(e)}'
        }, status=500)


@login_required
@require_POST
def aprobar_credito_trabajador(request):
    """Aprobar un crédito de trabajador"""
    try:
        data = json.loads(request.body)
        
        credito_id = data.get('credito_id')
        monto_aprobado = data.get('monto_aprobado')
        observaciones = data.get('observaciones', '')
        
        if not credito_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de crédito requerido'
            }, status=400)
        
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        # Verificar permisos
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para aprobar este crédito'
            }, status=403)
        
        # Verificar estado
        if credito.estado != 'PENDIENTE':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden aprobar créditos pendientes'
            }, status=400)
        
        # Validar monto aprobado
        if monto_aprobado:
            try:
                monto_aprobado = Decimal(str(monto_aprobado))
                if monto_aprobado <= 0:
                    raise ValueError("El monto debe ser mayor a 0")
            except (ValueError, TypeError):
                return JsonResponse({
                    'success': False,
                    'error': 'Monto aprobado inválido'
                }, status=400)
        
        # Aprobar crédito
        credito.aprobar_credito(
            usuario_autorizador=request.user,
            monto_aprobado=monto_aprobado,
            observaciones=observaciones
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Crédito aprobado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al aprobar crédito: {str(e)}'
        }, status=500)


@login_required
@require_POST
def rechazar_credito_trabajador(request):
    """Rechazar un crédito de trabajador"""
    try:
        data = json.loads(request.body)
        
        credito_id = data.get('credito_id')
        motivo_rechazo = data.get('motivo_rechazo')
        
        if not all([credito_id, motivo_rechazo]):
            return JsonResponse({
                'success': False,
                'error': 'ID de crédito y motivo de rechazo son requeridos'
            }, status=400)
        
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        # Verificar permisos
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para rechazar este crédito'
            }, status=403)
        
        # Verificar estado
        if credito.estado != 'PENDIENTE':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden rechazar créditos pendientes'
            }, status=400)
        
        # Rechazar crédito
        credito.rechazar_credito(
            usuario_autorizador=request.user,
            motivo_rechazo=motivo_rechazo
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Crédito rechazado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al rechazar crédito: {str(e)}'
        }, status=500)


@login_required
@require_POST
def activar_credito_trabajador(request):
    """Activar un crédito aprobado (cuando se entrega el dinero)"""
    try:
        data = json.loads(request.body)
        
        credito_id = data.get('credito_id')
        
        if not credito_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de crédito requerido'
            }, status=400)
        
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        # Verificar permisos
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para activar este crédito'
            }, status=403)
        
        # Verificar estado
        if credito.estado != 'APROBADO':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden activar créditos aprobados'
            }, status=400)
        
        # Activar crédito
        credito.activar_credito()
        
        return JsonResponse({
            'success': True,
            'message': 'Crédito activado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al activar crédito: {str(e)}'
        }, status=500)


@login_required
@require_POST
def ajustar_monto_credito(request):
    """Ajustar el monto de un crédito (solo si no tiene pagos)"""
    try:
        data = json.loads(request.body)
        
        credito_id = data.get('credito_id')
        nuevo_monto = data.get('nuevo_monto')
        motivo = data.get('motivo')
        
        if not all([credito_id, nuevo_monto, motivo]):
            return JsonResponse({
                'success': False,
                'error': 'Crédito, nuevo monto y motivo son requeridos'
            }, status=400)
        
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        # Verificar permisos
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para modificar este crédito'
            }, status=403)
        
        # Verificar que no tenga pagos
        if credito.monto_pagado > 0:
            return JsonResponse({
                'success': False,
                'error': 'No se puede ajustar el monto de un crédito que ya tiene pagos registrados'
            }, status=400)
        
        # Verificar estado del crédito
        if credito.estado not in ['PENDIENTE', 'APROBADO', 'ACTIVO']:
            return JsonResponse({
                'success': False,
                'error': 'Solo se puede ajustar el monto de créditos pendientes, aprobados o activos'
            }, status=400)
        
        # Validar nuevo monto
        try:
            nuevo_monto = Decimal(str(nuevo_monto))
            if nuevo_monto <= 0:
                raise ValueError("El monto debe ser mayor a 0")
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Monto inválido'
            }, status=400)
        
        # Guardar monto anterior para el historial
        monto_anterior = credito.monto_aprobado or credito.monto_solicitado
        
        # Actualizar montos
        credito.monto_solicitado = nuevo_monto
        credito.monto_aprobado = nuevo_monto
        
        # Agregar al historial de observaciones
        observacion_ajuste = f"\n[AJUSTE DE MONTO - {timezone.now().strftime('%d/%m/%Y %H:%M')}] "
        observacion_ajuste += f"De ${monto_anterior:,.0f} a ${nuevo_monto:,.0f}. "
        observacion_ajuste += f"Motivo: {motivo}. Usuario: {request.user.username}"
        
        if credito.observaciones_solicitud:
            credito.observaciones_solicitud += observacion_ajuste
        else:
            credito.observaciones_solicitud = observacion_ajuste.strip()
        
        credito.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Monto ajustado exitosamente',
            'monto_anterior': float(monto_anterior),
            'nuevo_monto': float(nuevo_monto)
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al ajustar monto: {str(e)}'
        }, status=500)


@login_required
@require_POST
def registrar_pago_credito(request):
    """Registrar un pago/abono a un crédito"""
    try:
        data = json.loads(request.body)
        
        credito_id = data.get('credito_id')
        monto_pago = data.get('monto_pago')
        fecha_pago = data.get('fecha_pago')
        metodo_pago = data.get('metodo_pago', 'EFECTIVO')
        
        if not all([credito_id, monto_pago, fecha_pago]):
            return JsonResponse({
                'success': False,
                'error': 'Crédito, monto y fecha de pago son requeridos'
            }, status=400)
        
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        # Verificar permisos
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para registrar pagos en este crédito'
            }, status=403)
        
        # Verificar estado del crédito
        if credito.estado in ('PENDIENTE', 'RECHAZADO'):
            return JsonResponse({
                'success': False,
                'error': 'No se pueden registrar cobros en créditos pendientes o rechazados'
            }, status=400)

        # Deuda real del crédito (ver bloque "CARTERA POR COBRAR").
        # ANTES se validaba contra `saldo_pendiente`, que en los créditos
        # nativos es el CUPO SIN USAR: por eso era imposible registrar la
        # cobranza de un crédito ya consumido (estado PAGADO, saldo 0).
        consumo_pos = Decimal('0')
        abonos_reales = Decimal('0')
        for metodo, total in (
            credito.pagos.values_list('metodo_pago').annotate(t=Sum('monto_pago'))
        ):
            if metodo in METODOS_CONSUMO_CREDITO:
                consumo_pos += Decimal(str(total or 0))
            else:
                abonos_reales += Decimal(str(total or 0))

        otorgado = Decimal(str(credito.monto_aprobado or credito.monto_solicitado or 0))
        if _es_credito_legacy(credito.numero_credito):
            deuda = otorgado - abonos_reales
        else:
            deuda = consumo_pos - abonos_reales

        if deuda <= 0:
            return JsonResponse({
                'success': False,
                'error': 'Este crédito no tiene deuda por cobrar (nada consumido o ya abonado por completo)'
            }, status=400)

        # Validar monto
        try:
            monto_pago = Decimal(str(monto_pago))
            if monto_pago <= 0:
                raise ValueError("El monto debe ser mayor a 0")

            if monto_pago > deuda:
                return JsonResponse({
                    'success': False,
                    'error': f'El monto no puede exceder la deuda del crédito (${deuda:,.0f})'
                }, status=400)

        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Monto inválido'
            }, status=400)

        if metodo_pago in METODOS_CONSUMO_CREDITO:
            return JsonResponse({
                'success': False,
                'error': 'Este formulario registra cobros; el consumo del crédito lo registra el POS'
            }, status=400)

        # Obtener sucursal actual para el cobro
        sucursal_cobro_id = request.session.get('idSucursalActual')
        sucursal_cobro = None
        if sucursal_cobro_id:
            from .models import Sucursal
            try:
                sucursal_cobro = Sucursal.objects.get(id=sucursal_cobro_id)
            except Sucursal.DoesNotExist:
                pass
        
        # Crear pago
        pago = PagoCreditoTrabajador.objects.create(
            credito=credito,
            monto_pago=monto_pago,
            fecha_pago=fecha_pago,
            metodo_pago=metodo_pago,
            numero_cuota=data.get('numero_cuota'),
            es_pago_total=data.get('es_pago_total', False),
            referencia_pago=data.get('referencia_pago', ''),
            observaciones=data.get('observaciones', ''),
            registrado_por=request.user,
            sucursal_cobro=sucursal_cobro
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Cobro registrado exitosamente',
            'pago_id': pago.id,
            'numero_pago': pago.numero_pago,
            'nuevo_saldo': credito.saldo_pendiente,
            'nueva_deuda': float(deuda - monto_pago),
            'estado_credito': credito.estado
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al registrar pago: {str(e)}'
        }, status=500)


@login_required
@require_POST
def registrar_firma_credito(request):
    """Registrar firma digital en un crédito"""
    try:
        data = json.loads(request.body)
        
        credito_id = data.get('credito_id')
        tipo_firma = data.get('tipo_firma')  # 'trabajador', 'autorizador', 'aval'
        firma_data = data.get('firma_data')
        
        if not all([credito_id, tipo_firma, firma_data]):
            return JsonResponse({
                'success': False,
                'error': 'Crédito, tipo de firma y datos de firma son requeridos'
            }, status=400)
        
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        # Verificar permisos
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para firmar este crédito'
            }, status=403)
        
        # Obtener o crear registro de firma
        firma, created = FirmaCreditoTrabajador.objects.get_or_create(credito=credito)
        
        # Obtener IP del cliente
        ip_address = request.META.get('HTTP_X_FORWARDED_FOR')
        if ip_address:
            ip_address = ip_address.split(',')[0]
        else:
            ip_address = request.META.get('REMOTE_ADDR')
        
        # Registrar firma según el tipo
        if tipo_firma == 'trabajador':
            firma.registrar_firma_trabajador(firma_data, ip_address)
        elif tipo_firma == 'autorizador':
            firma.registrar_firma_autorizador(firma_data, ip_address)
        elif tipo_firma == 'aval':
            if not credito.requiere_aval:
                return JsonResponse({
                    'success': False,
                    'error': 'Este crédito no requiere firma de aval'
                }, status=400)
            firma.registrar_firma_aval(firma_data, ip_address)
        else:
            return JsonResponse({
                'success': False,
                'error': 'Tipo de firma inválido'
            }, status=400)
        
        return JsonResponse({
            'success': True,
            'message': f'Firma de {tipo_firma} registrada exitosamente',
            'completamente_firmado': firma.esta_completamente_firmado
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al registrar firma: {str(e)}'
        }, status=500)


@login_required
@require_GET
def obtener_trabajadores_credito(request):
    """Obtener lista de clientes disponibles para créditos"""
    try:
        clientes = Cliente.objects.filter(
            nombre__isnull=False, activo=True,
        ).exclude(nombre='').select_related('empresa').order_by('apellido', 'nombre')
        
        clientes_data = []
        for cli in clientes:
            creditos_activos = CreditoTrabajador.objects.filter(
                beneficiario=cli,
                estado__in=['ACTIVO', 'APROBADO']
            ).count()
            
            clientes_data.append({
                'id': cli.id,
                'nombre': cli.nombre_completo,
                'rut': cli.rut or '',
                'codigo_vendedor': '',
                'correo': cli.email or '',
                'creditos_activos': creditos_activos,
                'empresa': cli.empresa.nombre if cli.empresa else '',
                'empresa_id': cli.empresa.id if cli.empresa else None,
                'tipo_cliente': cli.tipo_cliente,
                'correo': cli.email or '',
                'fecha_nacimiento': cli.fecha_nacimiento.strftime('%d-%m-%Y') if cli.fecha_nacimiento else '',
            })
        
        return JsonResponse({
            'success': True,
            'trabajadores': clientes_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener trabajadores: {str(e)}'
        })


@login_required
@require_POST
def crear_trabajador_credito(request):
    """Crear un nuevo cliente/beneficiario para créditos"""
    try:
        data = json.loads(request.body)
        
        nombre = data.get('nombre', '').strip()
        rut = data.get('rut', '').strip()
        correo = data.get('correo', '').strip()
        fecha_nacimiento = data.get('fecha_nacimiento')
        empresa_id = data.get('empresa_id')
        tipo_cliente = data.get('tipo_cliente', 'EMPLEADO')
        
        if not nombre:
            return JsonResponse({
                'success': False,
                'error': 'El nombre es requerido'
            }, status=400)
        
        if rut:
            existente = Cliente.objects.filter(rut__iexact=rut).first()
            if existente:
                return JsonResponse({
                    'success': False,
                    'error': f'Ya existe un cliente con RUT "{rut}": {existente.nombre_completo}'
                }, status=400)
        
        fecha_nacimiento_parsed = None
        if fecha_nacimiento:
            try:
                if '-' in fecha_nacimiento and len(fecha_nacimiento.split('-')[0]) == 2:
                    partes = fecha_nacimiento.split('-')
                    fecha_nacimiento_parsed = f"{partes[2]}-{partes[1]}-{partes[0]}"
                else:
                    fecha_nacimiento_parsed = fecha_nacimiento
            except Exception:
                pass
        
        empresa = None
        if empresa_id:
            empresa = Empresa.objects.filter(id=empresa_id).first()
        if not empresa:
            empresa_actual_id = request.session.get('idEmpresaActual')
            if empresa_actual_id:
                empresa = Empresa.objects.filter(id=empresa_actual_id).first()
        
        partes_nombre = nombre.split(None, 1)
        primer_nombre = partes_nombre[0]
        apellido = partes_nombre[1] if len(partes_nombre) > 1 else ''

        cliente = Cliente.objects.create(
            nombre=primer_nombre,
            apellido=apellido,
            rut=rut or None,
            email=correo or None,
            fecha_nacimiento=fecha_nacimiento_parsed,
            empresa=empresa,
            tipo_cliente=tipo_cliente,
            activo=True,
            created_by=request.user,
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Beneficiario creado exitosamente',
            'trabajador': {
                'id': cliente.id,
                'nombre': cliente.nombre_completo,
                'rut': cliente.rut or '',
                'codigo_vendedor': '',
                'correo': cliente.email or '',
                'sucursales': [],
                'empresa': cliente.empresa.nombre if cliente.empresa else None,
                'empresa_id': cliente.empresa.id if cliente.empresa else None,
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear beneficiario: {str(e)}'
        }, status=500)


@login_required
@require_POST
def actualizar_trabajador_credito(request):
    """Editar los datos de un beneficiario (Cliente) ya existente.

    Permite corregir nombre, RUT, correo, fecha de nacimiento y la empresa a la
    que pertenece el beneficiario. La empresa solo se modifica si se envía un
    `empresa_id` válido; si llega vacío se conserva la empresa actual.
    """
    try:
        data = json.loads(request.body)

        trabajador_id = data.get('trabajador_id')
        if not trabajador_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de beneficiario requerido'
            }, status=400)

        cliente = Cliente.objects.filter(id=trabajador_id).first()
        if not cliente:
            return JsonResponse({
                'success': False,
                'error': 'Beneficiario no encontrado'
            }, status=404)

        nombre = data.get('nombre', '').strip()
        rut = data.get('rut', '').strip()
        correo = data.get('correo', '').strip()
        fecha_nacimiento = data.get('fecha_nacimiento')
        empresa_id = data.get('empresa_id')

        if not nombre:
            return JsonResponse({
                'success': False,
                'error': 'El nombre es requerido'
            }, status=400)

        # Validar RUT único (excluyendo al propio beneficiario)
        if rut:
            existente = Cliente.objects.filter(rut__iexact=rut).exclude(id=cliente.id).first()
            if existente:
                return JsonResponse({
                    'success': False,
                    'error': f'Ya existe otro cliente con RUT "{rut}": {existente.nombre_completo}'
                }, status=400)

        # Parsear fecha de nacimiento (acepta DD-MM-YYYY)
        fecha_nacimiento_parsed = None
        if fecha_nacimiento:
            try:
                if '-' in fecha_nacimiento and len(fecha_nacimiento.split('-')[0]) == 2:
                    partes = fecha_nacimiento.split('-')
                    fecha_nacimiento_parsed = f"{partes[2]}-{partes[1]}-{partes[0]}"
                else:
                    fecha_nacimiento_parsed = fecha_nacimiento
            except Exception:
                pass

        # Empresa: solo se reemplaza si llega un id válido
        if empresa_id:
            empresa_nueva = Empresa.objects.filter(id=empresa_id).first()
            if empresa_nueva:
                cliente.empresa = empresa_nueva

        # Separar nombre completo en primer nombre + apellido (mismo criterio que al crear)
        partes_nombre = nombre.split(None, 1)
        primer_nombre = partes_nombre[0]
        apellido = partes_nombre[1] if len(partes_nombre) > 1 else ''

        cliente.nombre = primer_nombre
        cliente.apellido = apellido
        cliente.rut = rut or None
        cliente.email = correo or None
        if fecha_nacimiento_parsed:
            cliente.fecha_nacimiento = fecha_nacimiento_parsed
        cliente.save()

        return JsonResponse({
            'success': True,
            'message': 'Beneficiario actualizado exitosamente',
            'trabajador': {
                'id': cliente.id,
                'nombre': cliente.nombre_completo,
                'rut': cliente.rut or '',
                'correo': cliente.email or '',
                'empresa': cliente.empresa.nombre if cliente.empresa else None,
                'empresa_id': cliente.empresa.id if cliente.empresa else None,
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al actualizar beneficiario: {str(e)}'
        }, status=500)


@login_required
@require_GET
def validar_codigo_trabajador(request):
    """Verificar si un RUT de cliente ya existe"""
    codigo = request.GET.get('codigo', '').strip()
    
    if not codigo:
        return JsonResponse({'exists': False})
    
    exists = Cliente.objects.filter(rut__iexact=codigo).exists()
    existente = None
    if exists:
        c = Cliente.objects.filter(rut__iexact=codigo).first()
        existente = {'nombre': c.nombre_completo, 'codigo': c.rut}
    
    return JsonResponse({
        'exists': exists,
        'existente': existente
    })


@login_required
@require_GET
def obtener_empresas_disponibles(request):
    """Obtener las empresas disponibles para el usuario"""
    try:
        empresas = Empresa.objects.all().order_by('nombre')
        
        empresas_data = [{
            'id': e.id,
            'nombre': e.nombre,
            'rut': getattr(e, 'rut', '') or ''
        } for e in empresas]
        
        return JsonResponse({
            'success': True,
            'empresas': empresas_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener empresas: {str(e)}'
        })


@login_required
@require_GET
def obtener_sucursales_empresa(request):
    """Obtener las sucursales de una empresa.

    Acepta un parámetro opcional `empresa_id` por query string.
    - Si se pasa `empresa_id`, devuelve las sucursales de esa empresa.
    - Si no se pasa, usa la empresa activa en la sesión (`idEmpresaActual`).
    Solo retorna sucursales activas.
    """
    try:
        empresa_id_param = request.GET.get('empresa_id')
        empresa_id = None

        if empresa_id_param:
            try:
                empresa_id = int(empresa_id_param)
            except (TypeError, ValueError):
                return JsonResponse({
                    'success': False,
                    'error': 'empresa_id inválido'
                }, status=400)
        else:
            empresa_id = request.session.get('idEmpresaActual')

        if not empresa_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa en la sesión'
            }, status=400)

        empresa = Empresa.objects.filter(id=empresa_id).first()
        if not empresa:
            return JsonResponse({
                'success': False,
                'error': 'La empresa indicada no existe'
            }, status=404)

        sucursales = Sucursal.objects.filter(
            empresa_id=empresa_id,
            activa=True,
        ).order_by('alias')

        sucursales_data = [{
            'id': s.id,
            'alias': s.alias,
            'nombre': s.nombre or '',
            'direccion': s.direccion or '',
            'comuna': s.comuna or '',
            'ciudad': s.ciudad or '',
        } for s in sucursales]

        return JsonResponse({
            'success': True,
            'empresa': {
                'id': empresa.id,
                'nombre': empresa.nombre,
            },
            'sucursales': sucursales_data,
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_GET
def reporte_creditos_trabajadores(request):
    """Generar reporte de créditos de trabajadores"""
    try:
        def normalize_fecha(fecha_str):
            if not fecha_str:
                return None
            fecha_str = fecha_str.strip()
            if '/' in fecha_str:
                partes = fecha_str.split('/')
            elif '-' in fecha_str:
                partes = fecha_str.split('-')
            else:
                return fecha_str
            if len(partes) != 3:
                return fecha_str
            if len(partes[0]) == 2:
                return f"{partes[2]}-{partes[1]}-{partes[0]}"
            return fecha_str
        alcance = request.GET.get('alcance', 'actual')
        alcance_info = _alcance_creditos_usuario(request, alcance)
        if not alcance_info['empresa_ids']:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresas disponibles para consultar créditos'
            }, status=400)
        
        fecha_inicio = normalize_fecha(request.GET.get('fecha_inicio'))
        fecha_fin = normalize_fecha(request.GET.get('fecha_fin'))

        # Estadísticas generales
        creditos = CreditoTrabajador.objects.filter(empresa_origen_id__in=alcance_info['empresa_ids'])
        if alcance_info['sucursal_ids']:
            creditos = creditos.filter(sucursal_id__in=alcance_info['sucursal_ids'])
        elif alcance_info['alcance'] != 'todas':
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa en la sesión'
            }, status=400)

        if fecha_inicio:
            creditos = creditos.filter(fecha_solicitud__date__gte=fecha_inicio)
        if fecha_fin:
            creditos = creditos.filter(fecha_solicitud__date__lte=fecha_fin)
        
        total_creditos = creditos.count()
        total_monto_solicitado = creditos.aggregate(
            total=Sum('monto_solicitado')
        )['total'] or 0
        
        total_monto_aprobado = creditos.filter(
            monto_aprobado__isnull=False
        ).aggregate(
            total=Sum('monto_aprobado')
        )['total'] or 0
        
        total_monto_pagado = creditos.aggregate(
            total=Sum('monto_pagado')
        )['total'] or 0
        
        # Estadísticas por estado — una sola consulta agrupada en vez de una
        # consulta COUNT por cada estado del choices.
        conteo_estados = {
            row['estado']: row['n']
            for row in creditos.values('estado').annotate(n=Count('id'))
        }
        stats_por_estado = {
            estado: {'count': conteo_estados.get(estado, 0), 'display': display}
            for estado, display in ESTADO_CREDITO_CHOICES
        }

        # Créditos vencidos.
        # Antes se iteraba el queryset completo instanciando modelos y se leía
        # `credito.nombre_beneficiario`, que dispara una consulta por crédito
        # (1.006 consultas contra la BD remota => ~4 minutos de respuesta).
        # Ahora se filtra el vencimiento en SQL y se traen solo columnas planas.
        hoy_local = timezone.localdate()
        creditos_vencidos = []
        for f in creditos.filter(
            estado__in=['ACTIVO', 'APROBADO'],
            fecha_vencimiento__lt=hoy_local,
        ).values(
            'id', 'numero_credito', 'monto_aprobado', 'monto_solicitado',
            'monto_pagado', 'fecha_vencimiento',
            'beneficiario__nombre', 'beneficiario__apellido',
        ):
            saldo = float(f['monto_aprobado'] or f['monto_solicitado'] or 0) - float(f['monto_pagado'] or 0)
            if saldo <= 0:
                continue
            nombre = f"{f['beneficiario__nombre'] or ''} {f['beneficiario__apellido'] or ''}".strip()
            creditos_vencidos.append({
                'id': f['id'],
                'numero_credito': f['numero_credito'],
                'trabajador': nombre or 'Sin asignar',
                'monto_pendiente': saldo,
                'dias_vencido': (hoy_local - f['fecha_vencimiento']).days,
                'fecha_vencimiento': f['fecha_vencimiento'].strftime('%d/%m/%Y'),
            })

        # `Count` ya viene importado a nivel de módulo; el import local hacía
        # que Python tratara el nombre como variable local en toda la función.
        top_beneficiarios = creditos.values(
            'beneficiario__nombre', 'beneficiario__apellido', 'beneficiario__id'
        ).annotate(
            total_creditos=Count('id'),
            total_monto=Sum('monto_aprobado')
        ).order_by('-total_creditos')[:10]
        
        reporte_data = {
            'resumen': {
                'total_creditos': total_creditos,
                'total_monto_solicitado': float(total_monto_solicitado),
                'total_monto_aprobado': float(total_monto_aprobado),
                'total_monto_pagado': float(total_monto_pagado),
                'saldo_pendiente_total': float(total_monto_aprobado - total_monto_pagado)
            },
            # Cartera "al día de hoy": ignora el filtro de fechas de la pantalla
            # a propósito (una deuda de 2022 sigue siendo deuda en julio 2026).
            'cartera': _calcular_cartera_creditos(alcance_info),
            'sin_respaldo': _ventas_credito_sin_respaldo(alcance_info),
            'estadisticas_por_estado': stats_por_estado,
            'creditos_vencidos': creditos_vencidos,
            'top_trabajadores': [
                {
                    'trabajador_id': item['beneficiario__id'],
                    'trabajador': f"{item['beneficiario__nombre'] or ''} {item['beneficiario__apellido'] or ''}".strip(),
                    'total_creditos': item['total_creditos'],
                    'total_monto': float(item['total_monto'] or 0)
                }
                for item in top_beneficiarios
            ]
        }
        
        return JsonResponse({
            'success': True,
            'reporte': reporte_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar reporte: {str(e)}'
        }, status=500)


@login_required
@require_GET
def imprimir_voucher_credito(request, credito_id):
    """Generar voucher térmico de crédito — 80 mm, una sola página, diseño compacto."""
    try:
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        if not _usuario_puede_acceder_credito(request, credito):
            return JsonResponse({'success': False, 'error': 'No tiene permisos para imprimir este crédito'}, status=403)

        rut_benef     = (credito.beneficiario.rut if credito.beneficiario else '') or 'N/A'
        nombre_auth   = credito.autorizado_por.get_full_name() or credito.autorizado_por.username
        valor_cuota   = f"${credito.valor_cuota:,.0f}" if credito.valor_cuota else '-'
        interes_row   = (
            f'<div class="cell"><div class="lbl">Interés</div>'
            f'<div class="val">{float(credito.tasa_interes):.1f}%</div></div>'
            if credito.tasa_interes > 0 else ''
        )
        sucursal_dir  = credito.sucursal.direccion or ''
        aval_bloque   = ''
        if credito.requiere_aval:
            aval_bloque = f'''
<div class="firma-bloque">
    <div class="ftipo">Aval Garante</div>
    <div class="fnombre">{credito.aval_nombre}<br><span class="fpeq">RUT: {credito.aval_rut}</span></div>
    <div class="flinea">Firma Aval</div>
</div>'''

        motivo_texto  = credito.motivo_solicitud[:200]
        fecha_imp     = timezone.now().strftime('%d/%m/%Y %H:%M')

        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Voucher {credito.numero_credito}</title>
<style>
@page {{
    size: 80mm auto;
    margin: 0;
}}
*, *::before, *::after {{
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
    margin: 0; padding: 0;
}}
body {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 9.5px;
    line-height: 1.35;
    color: #000;
    background: #fff;
    width: 80mm;
    padding: 3mm 3mm 2mm;
}}

/* ── separadores ── */
.sep  {{ border-top: 1.5px solid #000; margin: 3px 0; }}
.sep2 {{ border-top: 2px solid #000;   margin: 3px 0; }}
.dash {{ border-top: 1px dashed #555;  margin: 2px 0; }}

/* ── cabecera ── */
.hdr-empresa  {{ font-size: 13px; font-weight: 900; text-align: center; letter-spacing: .3px; }}
.hdr-sucursal {{ font-size: 9px;  font-weight: 600; text-align: center; color: #333; }}
.hdr-titulo   {{ font-size: 11px; font-weight: 900; text-align: center; letter-spacing: .6px; padding: 2px 0; }}

/* ── código de barras ── */
.bc-wrap {{ text-align: center; padding: 2px 0; }}
.bc-num  {{ font-size: 10.5px; font-weight: 900; letter-spacing: 1.5px; margin-top: 1px; }}

/* ── monto destacado ── */
.monto-box {{
    text-align: center;
    padding: 5px 3px 4px;
    background: #111;
    color: #fff;
    margin: 3px 0;
}}
.monto-lbl {{ font-size: 8px; font-weight: 700; letter-spacing: .5px; text-transform: uppercase; }}
.monto-val {{ font-size: 24px; font-weight: 900; line-height: 1.15; }}
.monto-saldo {{
    display: flex; justify-content: space-between;
    font-size: 8.5px; padding: 2px 3px;
    background: #eee; margin: 0 0 3px;
}}
.monto-saldo .pen {{ font-weight: 900; }}

/* ── sección título ── */
.stit {{
    font-size: 8px; font-weight: 900; text-transform: uppercase;
    letter-spacing: .5px; background: #ddd;
    padding: 1px 3px; margin: 3px 0 2px;
}}

/* ── grilla de datos 2 columnas ── */
.grid2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 3px 8px;
    margin: 3px 0;
}}
.cell .lbl {{ font-size: 8px; color: #555; text-transform: uppercase; font-weight: 600; }}
.cell .val {{ font-size: 11px; font-weight: 700; }}
.cell .val.em {{ font-size: 12px; font-weight: 900; }}

/* ── motivo ── */
.motivo {{ font-size: 10px; line-height: 1.4; margin: 2px 0 3px; }}

/* ── firmas: una por fila ── */
.firma-bloque {{
    border: 1px solid #000;
    padding: 4px 5px;
    margin: 3px 0;
}}
.ftipo   {{ font-size: 8px; font-weight: 900; text-transform: uppercase; margin-bottom: 2px; }}
.fnombre {{ font-size: 10px; font-weight: 700; margin-bottom: 22px; }}
.fpeq    {{ font-size: 8.5px; font-weight: 400; }}
.flinea  {{
    border-top: 1px solid #000;
    padding-top: 2px;
    font-size: 8px; font-weight: 700;
    text-align: center; text-transform: uppercase;
}}

/* ── footer ── */
.footer {{
    text-align: center; font-size: 8px; color: #333;
    margin-top: 3px; line-height: 1.5;
}}

@media print {{ .no-print {{ display: none; }} }}
</style>
<script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.11.5/dist/JsBarcode.all.min.js"></script>
</head>
<body>

<!-- ══ CABECERA ══ -->
<div class="hdr-empresa">{credito.empresa_origen.nombre.upper()}</div>
<div class="hdr-sucursal">{credito.sucursal.alias}{(' — ' + sucursal_dir) if sucursal_dir else ''}</div>
<div class="sep2"></div>
<div class="hdr-titulo">◆ VOUCHER CRÉDITO TRABAJADOR ◆</div>
<div class="sep2"></div>

<!-- ══ CÓDIGO DE BARRAS ══ -->
<div class="bc-wrap">
    <svg id="barcode"></svg>
    <div class="bc-num">{credito.numero_credito}</div>
</div>
<div class="sep"></div>

<!-- ══ MONTO ══ -->
<div class="monto-box">
    <div class="monto-lbl">Monto Aprobado</div>
    <div class="monto-val">${credito.monto_aprobado:,.0f}</div>
</div>
<div class="monto-saldo">
    <span>Pagado: <strong>${credito.monto_pagado:,.0f}</strong></span>
    <span class="pen">Saldo: ${credito.saldo_pendiente:,.0f}</span>
</div>

<!-- ══ BENEFICIARIO ══ -->
<div class="stit">Beneficiario</div>
<div class="grid2">
    <div class="cell"><div class="lbl">Nombre</div><div class="val">{credito.nombre_beneficiario}</div></div>
    <div class="cell"><div class="lbl">RUT</div><div class="val">{rut_benef}</div></div>
    <div class="cell"><div class="lbl">Tipo</div><div class="val">{credito.get_tipo_beneficiario_display()}</div></div>
    <div class="cell"><div class="lbl">Estado</div><div class="val em">{credito.get_estado_display()}</div></div>
</div>

<!-- ══ DETALLE CRÉDITO ══ -->
<div class="dash"></div>
<div class="stit">Detalle Crédito</div>
<div class="grid2">
    <div class="cell"><div class="lbl">Tipo</div><div class="val">{credito.get_tipo_credito_display()}</div></div>
    <div class="cell"><div class="lbl">Emisión</div><div class="val">{credito.fecha_solicitud.strftime('%d/%m/%Y')}</div></div>
    <div class="cell"><div class="lbl">Vencimiento</div><div class="val em">{credito.fecha_vencimiento.strftime('%d/%m/%Y')}</div></div>
    <div class="cell"><div class="lbl">Cuotas</div><div class="val">{credito.numero_cuotas}</div></div>
    {interes_row}
    <div class="cell"><div class="lbl">Valor cuota</div><div class="val">{valor_cuota}</div></div>
    <div class="cell"><div class="lbl">Autorizado por</div><div class="val">{nombre_auth}</div></div>
</div>

<!-- ══ MOTIVO ══ -->
<div class="dash"></div>
<div class="stit">Motivo</div>
<div class="motivo">{motivo_texto}</div>

<!-- ══ FIRMAS ══ -->
<div class="sep"></div>
<div class="stit">Firmas</div>
<div class="firma-bloque">
    <div class="ftipo">Autorizado por</div>
    <div class="fnombre">{nombre_auth}</div>
    <div class="flinea">Firma y Timbre</div>
</div>
<div class="firma-bloque">
    <div class="ftipo">Recibí conforme</div>
    <div class="fnombre">{credito.nombre_beneficiario}<br><span class="fpeq">RUT: {rut_benef}</span></div>
    <div class="flinea">Firma Beneficiario</div>
</div>
{aval_bloque}

<!-- ══ FOOTER ══ -->
<div class="dash"></div>
<div class="footer">
    Emitido: <strong>{fecha_imp}</strong> &nbsp;·&nbsp; Usuario: <strong>{request.user.username}</strong><br>
    Doc: <strong>{credito.numero_credito}</strong> &nbsp;·&nbsp; Sistema RetailMind
</div>

<script>
    JsBarcode("#barcode", "{credito.numero_credito}", {{
        format: "CODE128",
        width: 2,
        height: 48,
        displayValue: false,
        margin: 2
    }});
    window.onload = function() {{
        setTimeout(function() {{ window.print(); }}, 400);
    }};
</script>
</body>
</html>"""

        return HttpResponse(html, content_type='text/html; charset=utf-8')

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar voucher: {str(e)}'
        }, status=500)


@login_required
@require_POST
def validar_codigo_credito(request):
    """Validar código de crédito desde el POS"""
    try:
        data = json.loads(request.body)
        codigo_credito = data.get('codigo_credito', '').strip().upper()
        
        if not codigo_credito:
            return JsonResponse({
                'success': False,
                'error': 'Código de crédito requerido'
            }, status=400)
        
        # Buscar crédito por código
        try:
            credito = CreditoTrabajador.objects.select_related(
                'beneficiario', 'empresa_origen', 'sucursal'
            ).get(numero_credito=codigo_credito)
        except CreditoTrabajador.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Código de crédito no encontrado',
                'codigo_invalido': True
            }, status=404)
        
        # Validar que el crédito esté activo
        if credito.estado != 'ACTIVO':
            return JsonResponse({
                'success': False,
                'error': f'Crédito en estado: {credito.get_estado_display()}. Debe estar ACTIVO',
                'estado_invalido': True
            }, status=400)
        
        # Validar que tenga saldo disponible
        if credito.saldo_pendiente <= 0:
            return JsonResponse({
                'success': False,
                'error': 'El crédito no tiene saldo disponible',
                'sin_saldo': True
            }, status=400)
        
        # Validar que no esté vencido
        if credito.esta_vencido:
            return JsonResponse({
                'success': False,
                'error': f'Crédito vencido desde el {credito.fecha_vencimiento.strftime("%d/%m/%Y")}',
                'vencido': True
            }, status=400)

        # Validar que el cupo no haya caducado (ver bloque "CADUCIDAD DEL CUPO").
        caducidad = _estado_caducidad_cupo(credito)
        if caducidad['caducado']:
            logger.info(
                'validar_codigo_credito: cupo caducado credito=%s motivo=%s limite=%s',
                credito.numero_credito, caducidad['motivo'], caducidad['fecha_limite'],
            )
            return JsonResponse({
                'success': False,
                'error': caducidad['mensaje'],
                'caducado': True,
                'motivo_caducidad': caducidad['motivo'],
                'fecha_limite_uso': caducidad['fecha_limite'].strftime('%d/%m/%Y') if caducidad['fecha_limite'] else None,
            }, status=400)

        # Retornar datos del crédito
        return JsonResponse({
            'success': True,
            'message': 'Crédito válido',
            'credito': {
                'id': credito.id,
                'numero_credito': credito.numero_credito,
                'trabajador': _serializar_beneficiario(credito),
                'monto_aprobado': float(credito.monto_aprobado),
                'monto_usado': float(credito.monto_pagado),
                'saldo_disponible': float(credito.saldo_pendiente),
                'fecha_vencimiento': credito.fecha_vencimiento.strftime('%d/%m/%Y'),
                'dias_para_vencimiento': credito.dias_para_vencimiento,
                'numero_cuotas': credito.numero_cuotas,
                'tipo_credito': credito.get_tipo_credito_display(),
                'fecha_limite_uso': caducidad['fecha_limite'].strftime('%d/%m/%Y') if caducidad['fecha_limite'] else None,
                'dias_para_caducar': caducidad['dias_para_caducar'],
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al validar crédito: {str(e)}'
        }, status=500)


@login_required
@require_POST
def usar_credito_en_venta(request):
    """Registrar uso de crédito en una venta del POS"""
    try:
        data = json.loads(request.body)
        
        credito_id = data.get('credito_id')
        monto_usado = data.get('monto_usado')
        ticket_id = data.get('ticket_id')  # Opcional al crear
        numero_boleta = data.get('numero_boleta', '')  # Número de boleta/documento
        folio_documento = data.get('folio_documento', '')  # Folio del documento
        
        if not all([credito_id, monto_usado]):
            return JsonResponse({
                'success': False,
                'error': 'ID de crédito y monto son requeridos'
            }, status=400)
        
        # Validar monto
        try:
            monto_usado = Decimal(str(monto_usado))
            if monto_usado <= 0:
                raise ValueError("El monto debe ser mayor a 0")
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Monto inválido'
            }, status=400)
        
        # Obtener crédito
        credito = get_object_or_404(CreditoTrabajador, id=credito_id)
        
        # Validar estado
        if credito.estado != 'ACTIVO':
            return JsonResponse({
                'success': False,
                'error': f'El crédito está en estado: {credito.get_estado_display()}'
            }, status=400)
        
        # Validar saldo disponible
        if monto_usado > credito.saldo_pendiente:
            return JsonResponse({
                'success': False,
                'error': f'Monto excede el saldo disponible (${credito.saldo_pendiente:,.0f})',
                'saldo_disponible': float(credito.saldo_pendiente)
            }, status=400)
        
        # Obtener sucursal actual para el cobro
        sucursal_cobro_id = request.session.get('idSucursalActual')
        sucursal_cobro = None
        if sucursal_cobro_id:
            try:
                sucursal_cobro = Sucursal.objects.get(id=sucursal_cobro_id)
            except Sucursal.DoesNotExist:
                pass
        
        # Construir referencia de pago (número de boleta o ticket)
        referencia = numero_boleta or folio_documento or (f'TKT-{ticket_id}' if ticket_id else '')

        # Idempotencia: el POS llama a este endpoint DESPUÉS de cerrar la venta.
        # Un reintento (doble click, reenvío del navegador, retry de red) volvía
        # a descontar el mismo consumo dos veces. Si ya existe un uso idéntico
        # (mismo crédito, misma referencia y mismo monto) se responde OK sin
        # duplicar el débito.
        if referencia:
            existente = PagoCreditoTrabajador.objects.filter(
                credito_id=credito.id,
                referencia_pago=referencia,
                monto_pago=monto_usado,
            ).first()
            if existente:
                credito.refresh_from_db()
                logger.warning(
                    'usar_credito_en_venta: uso duplicado ignorado credito=%s ref=%s monto=%s',
                    credito.numero_credito, referencia, monto_usado,
                )
                return JsonResponse({
                    'success': True,
                    'message': 'El uso del crédito ya estaba registrado',
                    'nuevo_saldo': float(credito.saldo_pendiente),
                    'estado_credito': credito.estado,
                    'estado_display': credito.get_estado_display(),
                    'pago_id': existente.id,
                    'credito_pagado_completo': credito.estado == 'PAGADO',
                    'duplicado': True,
                })

        # Registrar el uso del crédito
        with transaction.atomic():
            # Se bloquea la fila para que dos ventas simultáneas contra el mismo
            # crédito no lean el mismo saldo y lo sobregiren.
            credito = CreditoTrabajador.objects.select_for_update().get(id=credito.id)

            # CADUCIDAD: se evalúa DENTRO del lock y justo antes de debitar, que
            # es el único punto donde el bloqueo es real. La validación previa
            # (`validar_codigo_credito`) es sólo la advertencia al cajero: el POS
            # llama a este endpoint después de cerrar la venta, así que si el
            # cupo caducó entre medio el débito igual no debe ocurrir.
            caducidad = _estado_caducidad_cupo(credito)
            if caducidad['caducado']:
                logger.warning(
                    'usar_credito_en_venta RECHAZADO por cupo caducado: credito=%s motivo=%s '
                    'limite=%s monto=%s ticket=%s boleta=%s usuario=%s',
                    credito.numero_credito, caducidad['motivo'], caducidad['fecha_limite'],
                    monto_usado, ticket_id, numero_boleta, request.user.username,
                )
                return JsonResponse({
                    'success': False,
                    'error': caducidad['mensaje'],
                    'caducado': True,
                    'motivo_caducidad': caducidad['motivo'],
                    'fecha_limite_uso': caducidad['fecha_limite'].strftime('%d/%m/%Y') if caducidad['fecha_limite'] else None,
                }, status=400)

            if monto_usado > credito.saldo_pendiente:
                return JsonResponse({
                    'success': False,
                    'error': f'Monto excede el saldo disponible (${credito.saldo_pendiente:,.0f})',
                    'saldo_disponible': float(credito.saldo_pendiente)
                }, status=400)

            # Actualizar monto pagado (usado)
            credito.monto_pagado += monto_usado

            # Si se pagó todo, cambiar estado a PAGADO
            if credito.saldo_pendiente <= 0:
                credito.estado = 'PAGADO'
            
            credito.save()
            
            # Registrar como pago
            pago = PagoCreditoTrabajador.objects.create(
                credito=credito,
                monto_pago=monto_usado,
                fecha_pago=timezone.localdate(),
                metodo_pago='CREDITO_TRABAJADOR',
                referencia_pago=referencia,
                sucursal_cobro=sucursal_cobro,
                observaciones=f'Compra en POS{f" - Ticket #{ticket_id}" if ticket_id else ""}{f" - Boleta: {numero_boleta}" if numero_boleta else ""}',
                registrado_por=request.user
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Crédito usado exitosamente',
            'nuevo_saldo': float(credito.saldo_pendiente),
            'estado_credito': credito.estado,
            'estado_display': credito.get_estado_display(),
            'pago_id': pago.id,
            'credito_pagado_completo': credito.estado == 'PAGADO'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al usar crédito: {str(e)}'
        }, status=500)


# ========== EXPORTACIÓN A PDF ==========
#
# Reemplaza el "Excel" que armaba el navegador (en realidad un CSV con `;`).
# El PDF se genera en el servidor para que:
#   - respete el alcance por empresa/sucursal del usuario (el navegador pedía
#     `per_page: 9999` y exportaba lo que le devolvieran),
#   - y salga siempre igual, sin depender de Excel ni de la configuración
#     regional del PC del usuario.
#
# GRANULARIDAD: UNA FILA POR USO, no por crédito.
# Cuatro de las nueve columnas pedidas (Monto Usado, Sucursal Uso, Número Boleta
# y Fecha Boleta) son atributos de la COMPRA, no del crédito. Al agrupar por
# crédito habría que apilar "BE-1234, BE-1288 | Nickolas, Paola" dentro de una
# celda, y quien recibe el papel (administración / cobranza) no podría cruzar
# cada boleta con su monto ni con su sucursal. Con una fila por uso, cada línea
# es un documento verificable. Para que el crédito se siga leyendo como una
# unidad: sus usos van juntos y consecutivos, el número de crédito se repite en
# cada fila (una hoja suelta sigue siendo legible) y, cuando un crédito tiene más
# de un uso, se agrega una línea de subtotal. Los créditos sin uso aparecen igual
# con "Sin uso registrado": un cupo entregado y no usado también es información.

MESES_ES = (
    'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
)

# Tope de seguridad: un PDF más grande que esto no lo lee nadie y castiga la
# memoria del worker. Si se trunca, se avisa en el encabezado del reporte.
MAX_FILAS_PDF_CREDITOS = 8000


def _fmt_clp(valor):
    """Formato chileno de moneda: $1.234.567 (sin decimales)."""
    try:
        numero = float(valor or 0)
    except (TypeError, ValueError):
        return '$0'
    return '$' + f'{numero:,.0f}'.replace(',', '.')


def _mes_solicitud_label(fecha):
    if not fecha:
        return ''
    local = timezone.localtime(fecha) if timezone.is_aware(fecha) else fecha
    return f'{MESES_ES[local.month - 1]} {local.year}'


def _formatear_boleta(referencia, observaciones=''):
    """Número de boleta como 'BE-<dígitos>' (mismo criterio que usaba el Excel)."""
    crudo = (referencia or '').strip()
    if not crudo and observaciones:
        match = re.search(r'Ticket\s*#?(\d+)', observaciones)
        if match:
            crudo = match.group(1)
    digitos = re.sub(r'\D', '', crudo)
    if digitos:
        return f'BE-{digitos}'
    return crudo


def _filas_pdf_creditos(queryset):
    """Aplana los créditos a filas de uso, listas para la tabla del PDF."""
    creditos = sorted(
        queryset,
        key=lambda c: (
            (c.nombre_beneficiario or '').upper(),
            c.numero_credito or '',
        ),
    )

    filas = []
    total_monto = Decimal('0')
    total_usos = 0
    total_creditos = 0
    truncado = False

    for credito in creditos:
        if len(filas) >= MAX_FILAS_PDF_CREDITOS:
            truncado = True
            break
        total_creditos += 1

        beneficiario = credito.beneficiario
        empresa_beneficiaria = ''
        if beneficiario and beneficiario.empresa_id:
            empresa_beneficiaria = beneficiario.empresa.nombre or ''
        if not empresa_beneficiaria and credito.empresa_origen_id:
            # Sin ficha de empresa en el cliente se cae a la empresa que otorgó
            # el crédito, que es quien termina cobrándolo.
            empresa_beneficiaria = credito.empresa_origen.nombre or ''

        base = {
            'numero_credito': credito.numero_credito or '',
            'beneficiario': credito.nombre_beneficiario,
            'rut': (beneficiario.rut if beneficiario else '') or '',
            'empresa': empresa_beneficiaria,
            'mes_solicitud': _mes_solicitud_label(credito.fecha_solicitud),
        }

        usos = [p for p in credito.pagos.all() if p.metodo_pago in METODOS_CONSUMO_CREDITO]
        usos.sort(key=lambda p: (p.fecha_pago, p.created_at))

        if not usos:
            filas.append(dict(base, tipo='sin_uso', monto=Decimal('0'),
                              sucursal='Sin uso registrado', boleta='', fecha_boleta=''))
            continue

        subtotal = Decimal('0')
        for pago in usos:
            monto = Decimal(str(pago.monto_pago or 0))
            subtotal += monto
            total_monto += monto
            total_usos += 1
            filas.append(dict(
                base, tipo='uso', monto=monto,
                sucursal=(pago.sucursal_cobro.alias if pago.sucursal_cobro_id else ''),
                boleta=_formatear_boleta(pago.referencia_pago, pago.observaciones or ''),
                fecha_boleta=pago.fecha_pago.strftime('%d/%m/%Y') if pago.fecha_pago else '',
            ))

        if len(usos) > 1:
            # En el subtotal sólo se repite el número de crédito: los datos del
            # beneficiario en blanco hacen que la línea se lea como cierre del
            # bloque y no como un uso más.
            filas.append({
                'numero_credito': base['numero_credito'], 'beneficiario': '',
                'rut': '', 'empresa': '', 'mes_solicitud': '',
                'tipo': 'subtotal', 'monto': subtotal,
                'sucursal': f'Subtotal {len(usos)} usos', 'boleta': '', 'fecha_boleta': '',
            })

    return {
        'filas': filas,
        'total_monto': total_monto,
        'total_usos': total_usos,
        'total_creditos': total_creditos,
        'truncado': truncado,
    }


def _descripcion_filtros_creditos(data, alcance_info):
    """Texto legible con los filtros aplicados, para el encabezado del PDF."""
    etiquetas = (
        ('fecha_inicio', 'Desde'), ('fecha_fin', 'Hasta'), ('estado', 'Estado'),
        ('tipo_credito', 'Tipo'), ('numero_credito', 'N. Crédito'),
        ('trabajador_texto', 'Beneficiario'), ('sucursal_texto', 'Sucursal'),
        ('saldo_min', 'Saldo min.'), ('saldo_max', 'Saldo max.'),
    )
    partes = []
    for clave, etiqueta in etiquetas:
        valor = data.get(clave)
        if valor not in (None, ''):
            partes.append(f'{etiqueta}: {valor}')
    if data.get('trabajador_id'):
        nombre = Cliente.objects.filter(id=data['trabajador_id']).values_list('nombre', 'apellido').first()
        if nombre:
            partes.append('Beneficiario: ' + ' '.join(x for x in nombre if x))
    partes.append('Alcance: ' + ('todas las sucursales visibles' if alcance_info['alcance'] == 'todas'
                                 else 'sucursal actual'))
    return ' | '.join(partes)


@login_required
@require_GET
def exportar_creditos_pdf(request):
    """Exporta el listado de créditos a PDF (una fila por uso de cupo).

    Acepta por GET los mismos filtros que `cargar_creditos_trabajadores`, de modo
    que el PDF sea exactamente lo que el usuario tiene en pantalla. El alcance
    por empresa/sucursal es el del usuario: nadie exporta lo que no puede ver.
    """
    try:
        from io import BytesIO
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

        data = {clave: request.GET.get(clave) for clave in FILTROS_CREDITOS}

        queryset, alcance_info, error = _queryset_creditos_filtrado(request, data)
        if error:
            return JsonResponse({'success': False, 'error': error}, status=400)

        resultado = _filas_pdf_creditos(queryset)
        filas = resultado['filas']
        if not filas:
            return JsonResponse({
                'success': False,
                'error': 'No hay créditos para exportar con los filtros aplicados',
            }, status=404)

        # Paleta NEXO (nexo-design-system.css): no se inventan colores nuevos.
        azul = colors.HexColor('#0066FF')
        azul_oscuro = colors.HexColor('#1A1A2E')
        gris_claro = colors.HexColor('#F5F7FA')
        gris_borde = colors.HexColor('#DEE2E6')
        verde = colors.HexColor('#00D4AA')

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
            topMargin=1.4 * cm, bottomMargin=1.6 * cm,
            title='Reporte de Creditos a Trabajadores',
            author='RetailMind',
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('TituloCred', parent=styles['Title'], fontSize=15,
                                     textColor=azul_oscuro, spaceAfter=2, alignment=0)
        sub_style = ParagraphStyle('SubCred', parent=styles['Normal'], fontSize=8,
                                   textColor=colors.HexColor('#555555'), spaceAfter=2, leading=11)
        cell_style = ParagraphStyle('CeldaCred', parent=styles['Normal'], fontSize=7, leading=8.5)

        generado = timezone.localtime(timezone.now())
        total_creditos_txt = f"{resultado['total_creditos']:,}".replace(',', '.')
        elementos = [
            Paragraph('Créditos a Trabajadores - Uso de Cupos', title_style),
            Paragraph(_descripcion_filtros_creditos(data, alcance_info), sub_style),
            Paragraph(
                f"Generado: {generado.strftime('%d/%m/%Y %H:%M')} hrs&nbsp; |&nbsp; "
                f"Usuario: {request.user.get_full_name() or request.user.username}&nbsp; |&nbsp; "
                f"Créditos: {total_creditos_txt}&nbsp; |&nbsp; "
                f"Usos registrados: {resultado['total_usos']}",
                sub_style,
            ),
        ]
        if resultado['truncado']:
            elementos.append(Paragraph(
                f'ATENCION: el reporte se truncó en {MAX_FILAS_PDF_CREDITOS} filas. '
                f'Acote los filtros para exportarlo completo.', sub_style))
        elementos.append(Spacer(1, 0.25 * cm))

        encabezados = ['N° Crédito', 'Beneficiario', 'RUT Beneficiario', 'Empresa Beneficiaria',
                       'Monto Usado', 'Mes Solicitud', 'Sucursal Uso', 'N° Boleta', 'Fecha Boleta']
        anchos = [2.7 * cm, 5.2 * cm, 2.4 * cm, 4.4 * cm, 2.4 * cm, 2.6 * cm, 3.4 * cm, 2.2 * cm, 2.0 * cm]

        tabla_datos = [encabezados]
        filas_subtotal = []
        filas_sin_uso = []
        for indice, fila in enumerate(filas, start=1):
            if fila['tipo'] == 'subtotal':
                filas_subtotal.append(indice)
            elif fila['tipo'] == 'sin_uso':
                filas_sin_uso.append(indice)
            tabla_datos.append([
                fila['numero_credito'],
                Paragraph(fila['beneficiario'] or '', cell_style),
                fila['rut'],
                Paragraph(fila['empresa'] or '', cell_style),
                _fmt_clp(fila['monto']) if fila['tipo'] != 'sin_uso' else '-',
                fila['mes_solicitud'],
                Paragraph(fila['sucursal'] or '', cell_style),
                fila['boleta'],
                fila['fecha_boleta'],
            ])

        # Fila de totales
        tabla_datos.append([
            'TOTAL',
            f"{resultado['total_creditos']} créditos",
            '',
            f"{resultado['total_usos']} usos",
            _fmt_clp(resultado['total_monto']),
            '', '', '', '',
        ])
        fila_total = len(tabla_datos) - 1

        # repeatRows=1 -> la cabecera se repite en TODAS las páginas.
        tabla = Table(tabla_datos, colWidths=anchos, repeatRows=1)
        estilos_tabla = [
            ('BACKGROUND', (0, 0), (-1, 0), azul_oscuro),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 7.5),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), 0.4, gris_borde),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, gris_claro]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (4, 1), (4, -1), 'RIGHT'),
            ('ALIGN', (8, 1), (8, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 2.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
            ('BACKGROUND', (0, fila_total), (-1, fila_total), azul),
            ('TEXTCOLOR', (0, fila_total), (-1, fila_total), colors.white),
            ('FONTNAME', (0, fila_total), (-1, fila_total), 'Helvetica-Bold'),
            ('FONTSIZE', (0, fila_total), (-1, fila_total), 8),
        ]
        for indice in filas_subtotal:
            estilos_tabla.append(('BACKGROUND', (0, indice), (-1, indice), colors.HexColor('#E8F5F2')))
            estilos_tabla.append(('FONTNAME', (0, indice), (-1, indice), 'Helvetica-Bold'))
            estilos_tabla.append(('TEXTCOLOR', (6, indice), (6, indice), verde))
        for indice in filas_sin_uso:
            estilos_tabla.append(('TEXTCOLOR', (6, indice), (6, indice), colors.HexColor('#9A9A9A')))
        tabla.setStyle(TableStyle(estilos_tabla))
        elementos.append(tabla)

        ancho_pagina = landscape(A4)[0]

        class _LienzoNumerado(rl_canvas.Canvas):
            """Canvas de dos pasadas, necesario para 'Página X de Y'.

            En la pasada normal ReportLab todavía no sabe cuántas páginas habrá,
            así que se guardan los estados de página y el pie se dibuja al final,
            cuando el total ya se conoce.
            """

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._paginas = []

            def showPage(self):
                self._paginas.append(dict(self.__dict__))
                self._startPage()

            def save(self):
                total = len(self._paginas)
                for estado in self._paginas:
                    self.__dict__.update(estado)
                    self._dibujar_pie(total)
                    super().showPage()
                super().save()

            def _dibujar_pie(self, total):
                self.saveState()
                self.setFont('Helvetica', 7)
                self.setFillColor(colors.grey)
                self.drawString(
                    1.2 * cm, 0.9 * cm,
                    'RetailMind - Créditos a Trabajadores - '
                    + generado.strftime('%d/%m/%Y %H:%M')
                )
                self.drawRightString(
                    ancho_pagina - 1.2 * cm, 0.9 * cm,
                    f'Página {self._pageNumber} de {total}'
                )
                self.restoreState()

        doc.build(elementos, canvasmaker=_LienzoNumerado)
        buffer.seek(0)

        logger.info(
            'exportar_creditos_pdf: usuario=%s creditos=%s usos=%s monto=%s alcance=%s',
            request.user.username, resultado['total_creditos'], resultado['total_usos'],
            resultado['total_monto'], alcance_info['alcance'],
        )

        response = HttpResponse(buffer.read(), content_type='application/pdf')
        nombre = f"creditos_trabajadores_{generado.strftime('%Y%m%d_%H%M')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{nombre}"'
        return response

    except Exception as e:
        logger.exception('Error al exportar créditos a PDF')
        return JsonResponse({'success': False, 'error': f'Error al generar PDF: {str(e)}'}, status=500)
