"""
Módulo de Compras - RetailMind

Vistas de compras que NO viven en app/views.py: dashboard de compras,
compensaciones factura-contra-documento, importación/exportación CSV/Excel de
proveedores y DTE, KPI de pendientes de pago y documentos vinculados.
La pantalla de Gestión Compras, la de Documentos de Compra (alta, pagos, NC,
eliminar) y la recepción viven en app/views.py (son las que rutea urls.py).
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Count, Q, Avg
from django.db.models.functions import Abs, Coalesce, ExtractMonth, ExtractYear
from django.db import models
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.core.exceptions import ValidationError
from django.db import transaction
from collections import Counter
from datetime import date, datetime, time, timedelta
from functools import cached_property
import json
import re
import csv
import logging
import unicodedata
from decimal import Decimal

from .decorators import requiere_permiso, requiere_alguno_de_los_permisos

from .models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Dte, Dte_Detalle_Pago,
    Dte_Productos, Empresa, Producto, Producto_Talla, Productos_Recepcionados,
    Sucursal, EmpresaUser, Movimientos_Producto, LoteProducto, Dte_Incidencia,
    Ticket, Ticket_Productos, Traspaso, Traspaso_Detalle
)

# Método de pago usado para registrar una compensación factura-contra-factura
# (neteo de tesorería / cuentas por pagar, NO una relación tributaria SII: ambas
# facturas siguen siendo documentos tributarios válidos). Se guarda en el libro de
# pagos Dte_Detalle_Pago igual que una Nota de Crédito. Ver asociar_factura_compensacion.
# IMPORTANTE: este string debe coincidir EXACTO en todos los lugares que lo filtran
# (en app/views.py: cargarDteCompra, pagosDTE, obtener_asociaciones_dte y el
# comprobante de pago; aquí: obtener_resumen_pendientes_anio).
METODO_COMPENSACION = 'Compensación con Factura'

# Variante del neteo cuando el instrumento es una factura EMITIDA por nosotros/EDEL al
# proveedor (tipo_transaccion='VENTA', receptor = ese proveedor), usada cuando ya no se
# puede cargar/emitir una NC del proveedor. Mismo libro de pagos (Dte_Detalle_Pago) pero
# con FK documento_compensacion al DTE emitido. Ver asociar_documento_emitido_compensacion.
METODO_COMPENSACION_EMITIDA = 'Compensación con Factura Emitida'

# Todos los métodos que representan un neteo factura-contra-documento (NO efectivo). Úsese
# en TODO filtro de saldo/listado de compensaciones para que ambas variantes se sumen igual.
METODOS_COMPENSACION = (METODO_COMPENSACION, METODO_COMPENSACION_EMITIDA)

logger = logging.getLogger('app')

# Compras_Producto.precioSugerido es precio público CON IVA, mientras `costo` es
# neto. Para comparar ambos en el mismo plano hay que descontar el impuesto.
IVA_FACTOR_COMPRAS = 1.19


# ========== GESTIÓN DE COMPRAS ==========
# Las vistas de Gestión Compras y de DTE de compra (pantalla, alta, pagos, NC,
# eliminar) viven en app/views.py: son las que rutea urls.py. Las copias rotas
# que había aquí (sin ruta, con campos inexistentes) se borraron el 2026-09-26
# (auditoría B1-15 / B3-13 / B16-05); ver app/tests/test_fase_d_deadcode.py.


# ========== GESTIÓN DE DTEs DE COMPRAS ==========

@requiere_alguno_de_los_permisos(
    'gestion_dte_compras', 'gestion_compras', 'dashboard_compras_estrategico',
    'reporte_compras', 'reporte_rendimiento_proveedor',
)
def empresas_proveedoras(request):
    """Obtener lista de empresas proveedoras (id, nombre, RUT).

    Sin mapa en URL_PERMISO_MAP porque la usan varias pantallas con permisos
    distintos: Gestión Documentos Compras (POST, gestionDteCompras.html), el
    dashboard de compras, el Reporte de Compras y el de rendimiento por
    proveedor (GET). Antes bastaba estar logueado; ahora hace falta ver
    alguna de esas pantallas (el Maestro siempre pasa).
    """
    try:
        empresas = Empresa.objects.filter(
            esProveedor=True
        ).order_by('nombre')
        
        empresas_data = []
        for empresa in empresas:
            empresas_data.append({
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut
            })
        
        return JsonResponse(empresas_data, safe=False)

    except Exception:
        logger.exception('empresas_proveedoras: error')
        return JsonResponse({
            'success': False,
            'error': 'No se pudo cargar la lista de proveedores.'
        })


@login_required
@require_GET
def verificar_dte_duplicado(request):
    """Chequeo previo (read-only) de folio repetido para el modal 'Nuevo DTE Compra'.

    `crearDteCompras` / `actualizarDteCompras` rechazan el duplicado recién al
    guardar, con el formulario entero ya tipeado. Esta vista adelanta ese aviso.

    Criterio (B14-10 / B3-11), el MISMO del guardado (views._dte_compra_duplicado):
    la identidad SII de un DTE es (RUT emisor normalizado, tipo_documento, folio),
    sin fecha y sin contar los descartados; la COTIZACION (no es folio SII)
    conserva proveedor + folio + fecha.
    - Con `tipo_documento`: `bloqueante` usa ese criterio (dice exactamente lo
      que hará el guardado).
    - Sin `tipo_documento` (llamador que aún no lo envía): no se puede saber si
      el guardado chocará, así que se mantiene el criterio anterior (mismo folio
      y misma fecha), ya sin los descartados, que el guardado ignora.
    `coincidencias` lista el mismo folio en cualquier ficha con el RUT del
    emisor (todos los tipos, descartados incluidos) para que el usuario decida.

    Parámetros GET: emisor_id, numero_documento, tipo_documento (opcional),
    fecha_emision (opcional), dte_id (opcional, para excluirse a sí mismo en
    modo edición).
    """
    from django.utils.dateparse import parse_date
    from app.models import TIPO_DOCUMENTO_CHOICES
    from app.utils_folio_dte import empresas_con_mismo_rut

    emisor_id = request.GET.get('emisor_id')
    numero_documento = request.GET.get('numero_documento')

    try:
        emisor_id = int(emisor_id)
        numero_documento = int(numero_documento)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Parámetros inválidos.'}, status=400)
    # numero_documento es IntegerField: un folio mayor reventaba la consulta (500).
    if not (0 < numero_documento <= _LIMITE_ENTERO):
        return JsonResponse({'success': False, 'error': 'Parámetros inválidos.'}, status=400)

    fecha_emision = parse_date(request.GET.get('fecha_emision') or '')

    # Tipo desconocido o ausente -> criterio anterior (no rompe al llamador).
    tipo_documento = (request.GET.get('tipo_documento') or '').strip().upper()
    if tipo_documento not in {codigo for codigo, _ in TIPO_DOCUMENTO_CHOICES}:
        tipo_documento = ''

    try:
        excluir_id = int(request.GET.get('dte_id'))
    except (TypeError, ValueError):
        excluir_id = None

    empresa_session_id = request.session.get('idEmpresaActual')

    emisor = Empresa.objects.filter(id=emisor_id).only('id', 'rut').first()
    if emisor is None:
        return JsonResponse({
            'success': True, 'existe': False, 'bloqueante': False,
            'criterio': 'rut_tipo_folio' if tipo_documento else 'folio_fecha',
            'coincidencias': [],
        })
    ids_mismo_rut = empresas_con_mismo_rut(emisor) or [emisor.id]

    qs = Dte.objects.filter(
        tipo_transaccion='COMPRA',
        emisor_id__in=ids_mismo_rut,
        numero_documento=numero_documento,
    ).select_related('receptor').order_by('-fecha_emision', '-id')

    if excluir_id:
        qs = qs.exclude(id=excluir_id)

    # `bloqueante` se mide sobre TODO el universo, no sobre las 5 filas que se
    # muestran: es la condición que hará fallar el guardado.
    if tipo_documento:
        from app.views import _dte_compra_duplicado
        bloqueante = _dte_compra_duplicado(
            emisor, tipo_documento, numero_documento, fecha_emision, excluir_id=excluir_id,
        ) is not None
    else:
        bloqueante = bool(fecha_emision) and qs.filter(
            fecha_emision=fecha_emision, descartado=False,
        ).exists()

    coincidencias = []

    for d in qs[:5]:
        mismo_dia = bool(fecha_emision and d.fecha_emision == fecha_emision)
        coincidencias.append({
            'id': d.id,
            'numero_documento': d.numero_documento,
            'tipo_documento': d.tipo_documento,
            'fecha_emision': d.fecha_emision.strftime('%d-%m-%Y') if d.fecha_emision else '',
            'monto_con_iva': float(d.monto_con_iva or 0),
            'estado_pago': d.estado_pago,
            'descartado': d.descartado,
            'mismo_dia': mismo_dia,
            # Solo con tipo_documento: la fila tiene el mismo tipo que se carga.
            'mismo_tipo': bool(tipo_documento) and d.tipo_documento == tipo_documento,
            'receptor': (d.receptor.nombre or '') if d.receptor else '',
            # El listado del módulo sólo muestra DTEs de la empresa en sesión; si
            # el duplicado es de otra receptora el usuario no podrá abrirlo.
            'visible_en_listado': bool(
                empresa_session_id and d.receptor_id and str(d.receptor_id) == str(empresa_session_id)
            ),
        })

    return JsonResponse({
        'success': True,
        'existe': bool(coincidencias),
        'bloqueante': bloqueante,
        # 'rut_tipo_folio' (criterio del guardado) o 'folio_fecha' (sin tipo).
        'criterio': 'rut_tipo_folio' if tipo_documento else 'folio_fecha',
        'coincidencias': coincidencias,
    })


# ========== DASHBOARDS DE COMPRAS ==========
# dashboard_compras_estrategico (la API del dashboard antiguo) se borró el
# 2026-09-26 (B16-05): su URL es un RedirectView a verDashboardComprasMejorado
# y el JSON de error devolvía el traceback al cliente.

@require_GET
@login_required
def exportar_dashboard_compras(request):
    """Exporta a Excel el dashboard de compras MEJORADO (antes consumía la API
    del dashboard antiguo y el Excel no coincidía con la pantalla). Pasa los
    mismos filtros GET (anio/periodo/temporada/proveedor) al API nuevo."""
    try:
        import openpyxl

        dashboard_response = dashboard_compras_mejorado_api(request)
        dashboard_data = json.loads(dashboard_response.content)
        if dashboard_data.get('success') is False:
            return JsonResponse({
                'success': False,
                'error': dashboard_data.get('error', 'Error al generar dashboard')
            })

        wb = openpyxl.Workbook()

        # Hoja 1: métricas principales
        ws = wb.active
        ws.title = "Métricas"
        m = dashboard_data.get('metricas', {})
        ws.append(['Métrica', 'Valor'])
        ws.append(['Total Compras', m.get('total_compras', 0)])
        ws.append(['Inversión Total', m.get('inversion_total', 0)])
        ws.append(['Unidades Esperadas', m.get('unidades_esperadas', 0)])
        ws.append(['Unidades Recepcionadas', m.get('unidades_recepcionadas', 0)])
        ws.append(['Cumplimiento %', m.get('cumplimiento_general', 0)])
        ws.append(['ROI Promedio % (markup lista, teórico)', m.get('roi_promedio', 0)])
        # Qué mide cada cifra: los totales suman OC reales + ingresos sin OC
        # ("Compra Manual"), que se registran ya recibidos. No es lo facturado
        # ni la deuda (eso está en el Reporte de Compras).
        origen = m.get('origen') or {}
        oc, manual = origen.get('oc') or {}, origen.get('manual') or {}
        ws.append(['  de ellas: órdenes de compra (OC)', oc.get('compras', 0)])
        ws.append(['  de ellas: ingresos sin OC (Compra Manual)', manual.get('compras', 0)])
        ws.append(['  Inversión OC', oc.get('inversion', 0)])
        ws.append(['  Inversión ingresos sin OC', manual.get('inversion', 0)])
        ws.append(['  Cumplimiento solo OC %',
                   oc['cumplimiento'] if oc.get('cumplimiento') is not None else 'sin OC en el período'])
        f = dashboard_data.get('filtros_aplicados', {})
        ws.append([])
        ws.append(['Filtros', f'anio={f.get("anio")} periodo={f.get("periodo")} temporada={f.get("temporada") or "-"} proveedor={f.get("proveedor_id") or "-"}'])
        ws.append(['Rango de fechas (OC)', f'{f.get("fecha_desde", "")} al {f.get("fecha_hasta", "")} — {f.get("etiqueta", "")}'])
        if f.get('fecha_desde_anterior') and f.get('fecha_hasta_anterior'):
            ws.append(['Comparado con', f'{f["fecha_desde_anterior"]} al {f["fecha_hasta_anterior"]}'])
        else:
            ws.append(['Comparado con', 'Sin comparación (rango mayor a 1 año)'])

        # Hojas 2-6: los nombres (proveedor, categoría, marca, producto, compra,
        # temporada) son texto de usuario o de CSV importado: se escriben con
        # _xlsx_append_seguro para que un '=...' no quede como fórmula.

        # Hoja 2: top proveedores
        ws_p = wb.create_sheet("Proveedores")
        ws_p.append(['Proveedor', 'Compras', 'Inversión', 'Cumplimiento %'])
        for p in dashboard_data.get('top_proveedores', []):
            _xlsx_append_seguro(ws_p, [p.get('proveedor', '-'), p.get('total_compras', ''),
                                       p.get('inversion', 0), p.get('cumplimiento', '')])

        # Hoja 3: inversión por categoría v1.2
        cm = dashboard_data.get('categoria_marca', {})
        ws_c = wb.create_sheet("Por Categoría")
        ws_c.append(['Categoría', 'Padre', 'Unidades', 'Inversión', '% del total'])
        for c in cm.get('categorias', []):
            _xlsx_append_seguro(ws_c, [c.get('categoria'), c.get('padre'), c.get('unidades'),
                                       c.get('inversion'), c.get('participacion')])
        ws_c.append([])
        ws_c.append(['Inversión enlazada a catálogo', cm.get('inversion_enlazada', 0)])
        ws_c.append(['Inversión sin enlace (no clasificable)', cm.get('inversion_sin_enlace', 0)])

        # Hoja 4: inversión por marca vs rotación
        ws_m = wb.create_sheet("Por Marca")
        ws_m.append(['Marca', 'Inversión', 'Unidades compradas', 'Stock hoy', 'Venta 90d', 'Sell-through 90d %'])
        for mk in cm.get('marcas', []):
            _xlsx_append_seguro(ws_m, [mk.get('marca'), mk.get('inversion'), mk.get('unidades'),
                                       mk.get('stock_actual'), mk.get('venta_90d'),
                                       mk.get('sell_through_90d')])

        # Hoja 5: top productos
        ws_tp = wb.create_sheet("Top Productos")
        ws_tp.append(['Producto', 'Marca (texto OC)', 'Unidades', 'Inversión'])
        for tp in dashboard_data.get('top_productos', []):
            _xlsx_append_seguro(ws_tp, [tp.get('nombre', '-'), tp.get('marca', ''),
                                        tp.get('unidades', 0), tp.get('inversion', 0)])

        # Hoja 6: rendimiento por compra (la tabla de la pantalla; su botón
        # "Exportar" antes solo mostraba un aviso)
        ws_r = wb.create_sheet("Rendimiento")
        ws_r.append(['Compra', 'Proveedor', 'Temporada', 'Inversión', 'Cumplimiento %',
                     'Markup lista % (teórico)', 'Unidades pedidas', 'Unidades recibidas', 'Estado'])
        for rd in dashboard_data.get('rendimiento_detallado', []):
            _xlsx_append_seguro(ws_r, [rd.get('nombre', '-'), rd.get('proveedor', '-'),
                                       rd.get('temporada', ''), rd.get('inversion', 0),
                                       rd.get('cumplimiento', 0), rd.get('roi', 0),
                                       rd.get('unidades_esperadas', 0), rd.get('unidades_recibidas', 0),
                                       rd.get('estado', '')])

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="dashboard_compras_{f.get("anio", "")}.xlsx"'
        wb.save(response)
        return response

    except Exception:
        logger.exception('Error exportando dashboard compras')
        return JsonResponse({
            'success': False,
            'error': 'Error al exportar el dashboard'
        })


@login_required
def verDashboardCompras(request):
    """Vista principal del dashboard de compras - Redirige al dashboard mejorado"""
    return render(request, 'vistas/modulo_dashboards/dashboard_compras_mejorado.html')


# Desde cuándo cuenta la deuda con proveedores en los KPI de Gestión DTE
# (B15-09). Fijo y no "año en curso" para que las facturas impagas de
# diciembre no desaparezcan de 'Vencidos' cada 1 de enero; deja fuera el
# legacy 2018-2019 migrado en 'PENDIENTE' que nadie va a pagar.
FECHA_CORTE_PENDIENTES = date(2025, 1, 1)

# Documentos de compra que NO son deuda a pagar: la NC rebaja una factura y
# la cotización no es un documento tributario (B3-05 / B15-09). Las GUIAS
# siguen contando hasta que se decida (quedan como pendiente de negocio).
TIPOS_EXCLUIDOS_DEUDA_PROVEEDOR = ('NOTA DE CREDITO', 'COTIZACION')


@login_required
@require_GET
def obtener_resumen_pendientes_anio(request):
    """KPIs de deuda con proveedores (Pendientes / Vencidos / Por vencer / Al día).

    Universo (B3-05, B12-07, B15-09):
    - DTE de COMPRA de la empresa en sesión (o sin receptor, como la grilla),
      NO descartados, emitidos desde FECHA_CORTE_PENDIENTES. Antes era solo el
      año en curso: cada 1-ene salían de 'Vencidos' las facturas impagas de
      diciembre. El corte fijo deja fuera el legacy 2018-2019.
    - Sin notas de crédito (rebajan una factura, no son deuda) ni cotizaciones,
      ni documentos RECHAZADOS / ANULADOS / CANCELADOS (sin distinguir
      mayúsculas).
    - estado_pago con saldo, sin distinguir mayúsculas: PENDIENTE / Pendiente,
      PARCIAL / Parcial y 'Abonado' (lo que dejaba registrarPagoDTE).
    Saldo = monto - TODOS sus pagos (efectivo, NC, compensaciones). La suma va
    en un Subquery correlacionado por DTE (antes: un aggregate por documento,
    ~311 consultas por recarga): una sola consulta, y sin GROUP BY sobre un
    JOIN que se multiplicaría si alguien agrega otra relación al queryset. Los
    de saldo <= $1 no cuentan (redondeo de montos enteros).
    """
    from app.utils_estado_pago import q_estado_pago_pendiente

    try:
        ahora = timezone.localtime()
        anio_actual = ahora.year
        hoy = timezone.localdate()
        empresa_actual_id = request.session.get('idEmpresaActual')

        queryset = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            descartado=False,
            fecha_emision__gte=FECHA_CORTE_PENDIENTES,
        ).filter(
            Q(receptor_id=empresa_actual_id) | Q(receptor__isnull=True)
        ).exclude(
            tipo_documento__in=TIPOS_EXCLUIDOS_DEUDA_PROVEEDOR,
        ).exclude(
            es_nota_credito=True,
        ).exclude(
            Q(estado_dte__iexact='RECHAZADO')
            | Q(estado_dte__iexact='ANULADO')
            | Q(estado_dte__iexact='CANCELADO')
        )

        pagos_por_dte = (
            Dte_Detalle_Pago.objects
            .filter(dte_id=models.OuterRef('pk'))
            .order_by()
            .values('dte_id')
            .annotate(total=Sum('monto'))
            .values('total')
        )
        pendientes = queryset.filter(
            q_estado_pago_pendiente()
        ).annotate(
            pagado=Coalesce(
                models.Subquery(pagos_por_dte, output_field=models.IntegerField()), 0,
            ),
        ).values('id', 'monto_con_iva', 'fecha_vencimiento', 'pagado')

        cantidad_pendientes = 0

        # Calcular monto total pendiente y clasificar por vencimiento
        monto_total_pendiente = 0
        vencidos = 0  # Ya pasó la fecha de vencimiento
        por_vencer_pronto = 0  # Vencen en los próximos 7 días
        al_dia = 0  # Más de 7 días para vencer
        
        monto_vencidos = 0
        monto_por_vencer = 0
        monto_al_dia = 0
        
        for dte in pendientes:
            saldo_pendiente = float((dte['monto_con_iva'] or 0) - (dte['pagado'] or 0))
            if saldo_pendiente <= 1:
                # Pagado de hecho (estado_pago sin actualizar o redondeo).
                continue
            cantidad_pendientes += 1
            monto_total_pendiente += saldo_pendiente

            # Clasificar por vencimiento. OJO: fecha_vencimiento es nullable;
            # un solo DTE sin vencimiento hacía crashear TODO el panel de KPIs
            # con TypeError. Sin vencimiento → se cuenta como "al día" (no urgente).
            if dte['fecha_vencimiento'] is None:
                al_dia += 1
                monto_al_dia += saldo_pendiente
                continue
            dias_hasta_vencimiento = (dte['fecha_vencimiento'] - hoy).days

            if dias_hasta_vencimiento < 0:
                # Ya venció
                vencidos += 1
                monto_vencidos += saldo_pendiente
            elif dias_hasta_vencimiento <= 7:
                # Por vencer en 7 días o menos
                por_vencer_pronto += 1
                monto_por_vencer += saldo_pendiente
            else:
                # Al día (más de 7 días)
                al_dia += 1
                monto_al_dia += saldo_pendiente
        
        # Estadísticas adicionales (mismo universo, una sola consulta)
        totales = queryset.aggregate(
            total=Count('id'),
            pagados=Count('id', filter=Q(estado_pago__iexact='pagado')),
        )
        total_dtes = totales['total'] or 0
        pagados = totales['pagados'] or 0

        return JsonResponse({
            'success': True,
            'anio': anio_actual,
            # Inicio del universo del KPI (fecha de emisión). La grilla debe
            # usar este mismo 'desde' al filtrar Pendientes/Vencidos/Por vencer.
            'desde': FECHA_CORTE_PENDIENTES.isoformat(),
            'cantidad_pendientes': cantidad_pendientes,
            'monto_pendiente': monto_total_pendiente,
            'total_dtes': total_dtes,
            'pagados': pagados,
            # KPIs de vencimiento
            'vencidos': vencidos,
            'monto_vencidos': monto_vencidos,
            'por_vencer_pronto': por_vencer_pronto,
            'monto_por_vencer': monto_por_vencer,
            'al_dia': al_dia,
            'monto_al_dia': monto_al_dia
        })
        
    except Exception:
        logger.exception("Error al obtener resumen de pendientes del año")
        return JsonResponse({
            'success': False,
            'error': 'No se pudo cargar el resumen de vencimientos. Reintentá; si persiste, contactá a soporte.'
        })


# ========== EXPORTACIÓN DE COMPRAS ACTUALES ==========

# Prefijos con los que Excel / LibreOffice / Sheets interpretan una celda como
# fórmula (CSV / XLSX injection, CC-16): un nombre de producto o de proveedor
# '=HYPERLINK(...)' se ejecutaría al abrir la planilla descargada.
_PREFIJOS_FORMULA = ('=', '+', '-', '@', '\t', '\r')


def _celda_csv_segura(valor):
    """Texto que Excel tomaría como fórmula -> se antepone una comilla simple.
    Solo toca str: números (también negativos) y fechas salen como están."""
    if isinstance(valor, str) and valor.startswith(_PREFIJOS_FORMULA):
        return "'" + valor
    return valor


def _fila_csv_segura(valores):
    return [_celda_csv_segura(v) for v in valores]


def _xlsx_celda(ws, fila, columna, valor):
    """ws.cell(...) que nunca deja una fórmula. openpyxl convierte en fórmula
    todo str que empieza con '='; esos textos (y los que empiezan con + - @)
    se guardan como TEXTO con quotePrefix, la comilla propia de Excel: se ven
    tal cual y no se evalúan ni al abrir ni al editar la celda."""
    celda = ws.cell(row=fila, column=columna, value=valor)
    if isinstance(valor, str) and valor.startswith(_PREFIJOS_FORMULA):
        celda.data_type = 's'
        celda.quotePrefix = True
    return celda


def _anio_exportacion(request):
    """Año pedido en ?anio= (2000-2100); por defecto el año en curso. None si
    no es un año válido."""
    valor = request.GET.get('anio') or timezone.localdate().year
    try:
        anio = int(valor)
    except (TypeError, ValueError):
        return None
    return anio if 2000 <= anio <= 2100 else None


def _datos_exportacion_compras(anio):
    """Compras del año para exportar, con productos y tallas precargados, y
    lo recepcionado por talla.

    B1-04: se excluyen las compras ELIMINADA, igual que la grilla (antes
    entraban y en 2026 eran el 77 % del costo exportado).
    B1-05 / B12-02: antes había una consulta por compra y otra por talla
    (~10.900 consultas y 11-19 s para 2025) y el detalle tomaba solo la
    PRIMERA recepción de cada talla, así que 'Recepcionado' del Detalle no
    cuadraba con el del Resumen cuando hubo entregas parciales. Ahora son 4
    consultas y se suman todas las recepciones, con todos sus folios.

    Devuelve (compras, recepcion_por_talla) con
    recepcion_por_talla[talla_id] = (unidades_recepcionadas, 'folio1, folio2').
    """
    from django.db.models import Prefetch

    compras = list(
        Compras.objects.filter(fecha__year=anio)
        .exclude(estado='ELIMINADA')
        .select_related('empresa')
        .prefetch_related(
            Prefetch('compras_producto_set',
                     queryset=Compras_Producto.objects.order_by('id')),
            Prefetch('compras_producto_set__compras_producto_talla_set',
                     queryset=Compras_Producto_Talla.objects.order_by('id')),
        )
        .order_by('-fecha', '-id')
    )

    acumulado = {}
    if compras:
        filas = (
            Productos_Recepcionados.objects
            .filter(compra_producto_talla__compra_producto__compras_id__in=[c.id for c in compras])
            .order_by('id')
            .values_list('compra_producto_talla_id', 'stockArribado', 'dte__numero_documento')
        )
        for talla_id, cantidad, folio in filas:
            registro = acumulado.setdefault(talla_id, [0, []])
            registro[0] += cantidad or 0
            if folio is not None and str(folio) not in registro[1]:
                registro[1].append(str(folio))
    recepcion_por_talla = {k: (v[0], ', '.join(v[1])) for k, v in acumulado.items()}
    return compras, recepcion_por_talla


def _error_exportacion(contexto):
    logger.exception('Error al exportar %s', contexto)
    return JsonResponse({
        'success': False,
        'error': 'No se pudo generar la exportación. Reintenta; si persiste, avisa a soporte.',
    }, status=500)


@require_GET
@requiere_permiso('gestion_compras', 'puede_ver')
def exportar_compras_excel(request):
    """Exporta las compras del año a Excel: hoja 'Resumen Compras' (una fila
    por compra) y 'Detalle Productos' (una fila por talla)."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        anio = _anio_exportacion(request)
        if anio is None:
            return JsonResponse({'success': False, 'error': 'Año inválido (2000-2100)'}, status=400)

        compras, recepcion_por_talla = _datos_exportacion_compras(anio)

        if not compras:
            return JsonResponse({
                'success': False,
                'error': f'No hay compras para el año {anio}'
            })

        # Crear workbook
        wb = openpyxl.Workbook()
        
        # Hoja 1: Resumen de Compras
        ws_resumen = wb.active
        ws_resumen.title = "Resumen Compras"
        
        # Estilos
        header_fill = PatternFill(start_color="0066CC", end_color="0066CC", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, size=11)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Encabezados
        headers_resumen = [
            'ID', 'Proveedor', 'RUT Proveedor', 'Nombre Compra', 'Temporada',
            'Fecha Inicio', 'Fecha Término', 'Fecha Registro', 'Responsable',
            'Total Productos', 'Total Unidades', 'Costo Total', 'Venta Esperada', 'Recepcionado'
        ]
        
        for col, header in enumerate(headers_resumen, start=1):
            cell = ws_resumen.cell(row=1, column=col)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
        
        # Datos
        for row_idx, compra in enumerate(compras, start=2):
            # Totales en memoria con lo ya precargado (antes: 5 consultas por
            # compra). Mismo resultado que los aggregate: el JOIN interno de
            # costo/venta ignoraba productos sin tallas, igual que este recorrido.
            productos = list(compra.compras_producto_set.all())
            total_productos = len(productos)
            total_unidades = 0
            costo_total = 0
            venta_esperada = 0
            recepcionado = 0
            for producto in productos:
                for talla in producto.compras_producto_talla_set.all():
                    total_unidades += talla.stock
                    costo_total += producto.costo * talla.stock
                    venta_esperada += producto.precioSugerido * talla.stock
                    recepcionado += recepcion_por_talla.get(talla.id, (0, ''))[0]

            # Escribir datos
            rut_proveedor = ''
            if compra.empresa and compra.empresa.rut:
                rut_proveedor = compra.empresa.rut.replace('.', '')

            _xlsx_celda(ws_resumen, row_idx, 1, compra.id)
            _xlsx_celda(ws_resumen, row_idx, 2, compra.empresa.nombre if compra.empresa else '')
            _xlsx_celda(ws_resumen, row_idx, 3, rut_proveedor)
            _xlsx_celda(ws_resumen, row_idx, 4, compra.nombre)
            _xlsx_celda(ws_resumen, row_idx, 5, compra.temporada)
            _xlsx_celda(ws_resumen, row_idx, 6, compra.fechaInicioTemporada)
            _xlsx_celda(ws_resumen, row_idx, 7, compra.fechaTerminoTemporada)
            _xlsx_celda(ws_resumen, row_idx, 8, compra.fecha)
            _xlsx_celda(ws_resumen, row_idx, 9, compra.responsable)
            _xlsx_celda(ws_resumen, row_idx, 10, total_productos)
            _xlsx_celda(ws_resumen, row_idx, 11, total_unidades)
            _xlsx_celda(ws_resumen, row_idx, 12, float(costo_total) if costo_total else 0)
            _xlsx_celda(ws_resumen, row_idx, 13, float(venta_esperada) if venta_esperada else 0)
            _xlsx_celda(ws_resumen, row_idx, 14, recepcionado)
            
            # Aplicar bordes
            for col in range(1, 15):
                ws_resumen.cell(row=row_idx, column=col).border = border
        
        # Ajustar ancho de columnas
        for col in ws_resumen.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws_resumen.column_dimensions[column].width = adjusted_width
        
        # Hoja 2: Detalle de Productos
        ws_detalle = wb.create_sheet("Detalle Productos")
        
        headers_detalle = [
            'ID Compra', 'Nombre Compra', 'Proveedor', 'Nombre Producto',
            'Descripción', 'Marca', 'Color', 'Género', 'Costo', 'Precio Sugerido',
            'Talla', 'Stock', 'Recepcionado', 'Factura DTE'
        ]
        
        for col, header in enumerate(headers_detalle, start=1):
            cell = ws_detalle.cell(row=1, column=col)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
        
        # Datos de productos
        row_idx = 2
        for compra in compras:
            for producto in compra.compras_producto_set.all():
                for talla in producto.compras_producto_talla_set.all():
                    # Todas las recepciones de la talla (antes solo la primera)
                    recepcionado, factura = recepcion_por_talla.get(talla.id, (0, ''))

                    _xlsx_celda(ws_detalle, row_idx, 1, compra.id)
                    _xlsx_celda(ws_detalle, row_idx, 2, compra.nombre)
                    _xlsx_celda(ws_detalle, row_idx, 3, compra.empresa.nombre if compra.empresa else '')
                    _xlsx_celda(ws_detalle, row_idx, 4, producto.nombre)
                    _xlsx_celda(ws_detalle, row_idx, 5, producto.descripcion or '')
                    _xlsx_celda(ws_detalle, row_idx, 6, producto.atributo1)
                    _xlsx_celda(ws_detalle, row_idx, 7, producto.atributo2)
                    _xlsx_celda(ws_detalle, row_idx, 8, producto.atributo3)
                    _xlsx_celda(ws_detalle, row_idx, 9, producto.costo)
                    _xlsx_celda(ws_detalle, row_idx, 10, producto.precioSugerido)
                    _xlsx_celda(ws_detalle, row_idx, 11, talla.talla)
                    _xlsx_celda(ws_detalle, row_idx, 12, talla.stock)
                    _xlsx_celda(ws_detalle, row_idx, 13, recepcionado)
                    _xlsx_celda(ws_detalle, row_idx, 14, factura)
                    
                    # Aplicar bordes
                    for col in range(1, 15):
                        ws_detalle.cell(row=row_idx, column=col).border = border
                    
                    row_idx += 1
        
        # Ajustar ancho de columnas en detalle
        for col in ws_detalle.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws_detalle.column_dimensions[column].width = adjusted_width
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="compras_{anio}.xlsx"'

        wb.save(response)
        return response

    except Exception:
        return _error_exportacion('compras (Excel)')


@require_GET
@requiere_permiso('gestion_compras', 'puede_ver')
def exportar_compras_csv(request):
    """Exporta las compras del año a CSV (una fila por talla)."""
    try:
        anio = _anio_exportacion(request)
        if anio is None:
            return JsonResponse({'success': False, 'error': 'Año inválido (2000-2100)'}, status=400)

        compras, recepcion_por_talla = _datos_exportacion_compras(anio)

        if not compras:
            return JsonResponse({
                'success': False,
                'error': f'No hay compras para el año {anio}'
            })
        
        # Crear respuesta CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="compras_{anio}.csv"'
        response.write('\ufeff')  # BOM para UTF-8
        
        writer = csv.writer(response)
        
        # Encabezados
        writer.writerow([
            'ID Compra', 'Proveedor', 'RUT Proveedor', 'Nombre Compra', 'Temporada',
            'Fecha Inicio', 'Fecha Término', 'Fecha Registro', 'Responsable',
            'Nombre Producto', 'Descripción', 'Marca', 'Color', 'Género',
            'Costo', 'Precio Sugerido', 'Talla', 'Stock', 'Recepcionado', 'Factura DTE'
        ])
        
        # Datos
        for compra in compras:
            # Limpiar RUT (sin puntos)
            rut_proveedor = ''
            if compra.empresa and compra.empresa.rut:
                rut_proveedor = compra.empresa.rut.replace('.', '')

            for producto in compra.compras_producto_set.all():
                for talla in producto.compras_producto_talla_set.all():
                    # Todas las recepciones de la talla (antes solo la primera)
                    recepcionado, factura = recepcion_por_talla.get(talla.id, (0, ''))

                    writer.writerow(_fila_csv_segura([
                        compra.id,
                        compra.empresa.nombre if compra.empresa else '',
                        rut_proveedor,
                        compra.nombre,
                        compra.temporada,
                        compra.fechaInicioTemporada.strftime('%Y-%m-%d') if compra.fechaInicioTemporada else '',
                        compra.fechaTerminoTemporada.strftime('%Y-%m-%d') if compra.fechaTerminoTemporada else '',
                        compra.fecha.strftime('%Y-%m-%d') if compra.fecha else '',
                        compra.responsable,
                        producto.nombre,
                        producto.descripcion or '',
                        producto.atributo1,
                        producto.atributo2,
                        producto.atributo3,
                        producto.costo,
                        producto.precioSugerido,
                        talla.talla,
                        talla.stock,
                        recepcionado,
                        factura
                    ]))

        return response

    except Exception:
        return _error_exportacion('compras (CSV)')


# ========== GESTIÓN DE PROVEEDORES - IMPORTACIÓN ==========

# ---- Utilidades comunes de las importaciones CSV/XLSX (proveedores y DTE) ----

class _ErrorArchivoImportacion(Exception):
    """Problema del archivo completo (formato, lectura): el mensaje es para el usuario."""


def _texto_sin_tildes(texto):
    t = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(ch for ch in t if not unicodedata.combining(ch))


def _normalizar_encabezado(texto):
    """'Razón Social' / '\\ufeffrut' / ' Nº Documento ' -> 'razon_social' /
    'rut' / 'no_documento'. Así calzan los encabezados de nuestras propias
    exportaciones Excel ('Dirección', 'Teléfono'...) con los que lee el
    importador (B11-04) y el BOM de las plantillas deja de romper 'rut'
    (B11-13)."""
    t = _texto_sin_tildes(str(texto or '').replace('﻿', '')).strip().lower()
    return re.sub(r'[^a-z0-9]+', '_', t).strip('_')


def _valor_celda_importacion(valor):
    """Valor de una celda como texto limpio. Fechas de Excel -> AAAA-MM-DD;
    enteros guardados como float (12345.0) -> '12345'."""
    if valor is None:
        return ''
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    texto = str(valor).strip()
    # Deshace la neutralización de fórmulas de nuestras exportaciones CSV
    # ("'+56912345678") para que exportar -> reimportar no agregue comillas.
    if len(texto) > 1 and texto[0] == "'" and texto[1] in _PREFIJOS_FORMULA:
        texto = texto[1:]
    return texto


def _leer_filas_importacion(archivo, alias=None):
    """Filas de un CSV o XLSX como [(n_fila, {encabezado_normalizado: texto})].

    - CSV: UTF-8 con o sin BOM (nuestras plantillas lo escriben) y, si no
      decodifica, cp1252 (lo que guarda Excel en es-CL). Separador ';' si el
      encabezado trae más ';' que ',' (Excel es-CL), si no ','. No se usa
      csv.Sniffer: se equivoca con comas dentro de las direcciones.
    - XLSX: valores calculados (no fórmulas).
    - .xls y otros: _ErrorArchivoImportacion con instrucciones.
    `alias` traduce encabezados normalizados a la clave que usa el importador.
    Las filas vacías se saltan y n_fila es el número real de la fila.
    """
    import io

    nombre = (getattr(archivo, 'name', '') or '').lower()
    if nombre.endswith('.xls'):
        raise _ErrorArchivoImportacion(
            'El formato .xls (Excel 97-2003) no se puede leer. Abre el archivo en Excel y '
            'guárdalo como "Libro de Excel (.xlsx)" o como CSV.'
        )
    if nombre.endswith('.csv'):
        crudo = archivo.read()
        try:
            texto = crudo.decode('utf-8-sig')
        except UnicodeDecodeError:
            texto = crudo.decode('cp1252', errors='replace')
        primera = next((linea for linea in texto.splitlines() if linea.strip()), '')
        separador = ';' if primera.count(';') > primera.count(',') else ','
        filas = list(csv.reader(io.StringIO(texto, newline=''), delimiter=separador))
    elif nombre.endswith('.xlsx'):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(archivo, read_only=True, data_only=True)
        except Exception:
            logger.warning('Importación: no se pudo abrir el Excel %s', nombre, exc_info=True)
            raise _ErrorArchivoImportacion('No se pudo leer el Excel. Verifica que sea un archivo .xlsx válido.')
        try:
            hoja = wb.active
            # En read_only openpyxl corta filas/columnas según el <dimension>
            # declarado; hay exportadores (Apache POI streaming, algunos ERP)
            # que escriben ref="A1" y la hoja se leería como solo el encabezado.
            if hasattr(hoja, 'reset_dimensions'):
                hoja.reset_dimensions()
            filas = [list(f) for f in hoja.iter_rows(values_only=True)]
        finally:
            wb.close()
    else:
        raise _ErrorArchivoImportacion('Formato de archivo no válido. Usa CSV (.csv) o Excel (.xlsx).')

    alias = alias or {}
    encabezados = None
    resultado = []
    for n_fila, fila in enumerate(filas, start=1):
        valores = [_valor_celda_importacion(v) for v in fila]
        if not any(valores):
            continue
        if encabezados is None:
            encabezados = []
            for v in valores:
                clave = _normalizar_encabezado(v)
                encabezados.append(alias.get(clave, clave))
            continue
        registro = {}
        for i, clave in enumerate(encabezados):
            if clave and i < len(valores) and not registro.get(clave):
                registro[clave] = valores[i]
        resultado.append((n_fila, registro))
    return resultado


def _rut_canonico(rut):
    """'76.123.456-0' / '76123456-0' / '761234560' -> '76123456-0' (DV en mayúscula)."""
    limpio = re.sub(r'[.\s-]', '', str(rut or '')).upper()
    if len(limpio) < 2:
        return limpio
    return f'{limpio[:-1]}-{limpio[-1]}'


def _rut_valido(rut):
    """RUT con 7-8 dígitos y dígito verificador correcto (validar_rut_chileno)."""
    from app.models import validar_rut_chileno
    canon = _rut_canonico(rut)
    return bool(re.fullmatch(r'\d{7,8}-[\dK]', canon)) and validar_rut_chileno(canon)


def _empresas_por_rut(rut):
    """Empresas cuyo RUT coincide sin mirar puntos, espacios ni guion: en la
    BD conviven '76.123.456-0', '76123456-0' y '761234560'."""
    from django.db.models import Value
    from django.db.models.functions import Replace

    clave = re.sub(r'[.\s-]', '', str(rut or '')).upper()
    if not clave:
        return Empresa.objects.none()
    return (
        Empresa.objects
        .annotate(_rut_m1=Replace(Replace(Replace(
            'rut', Value('.'), Value('')), Value(' '), Value('')), Value('-'), Value('')))
        .filter(_rut_m1__iexact=clave)
    )


def _permiso_importacion(request, tipo_permiso):
    """True si el usuario tiene `tipo_permiso` sobre Gestión Documentos Compras
    en su sucursal activa (el Maestro siempre)."""
    from app.models import PermisoRol
    return PermisoRol.tiene_permiso(
        request.user, 'gestion_dte_compras', tipo_permiso,
        sucursal_id=request.session.get('idSucursalActual'),
    )


def _denegar_importacion(mensaje):
    return JsonResponse({'success': False, 'error': mensaje, 'mensaje': mensaje}, status=403)


def _motivo_error_fila(exc):
    """Mensaje de error de una fila para el usuario, sin detalles internos."""
    from django.db import DataError, IntegrityError
    if isinstance(exc, DataError):
        return 'algún valor es demasiado largo o tiene un formato inválido para la base de datos'
    if isinstance(exc, IntegrityError):
        return 'el registro choca con otro existente'
    return 'error inesperado al guardar (quedó registrado para soporte)'


# Encabezados alternativos -> clave del importador de proveedores. 'telefono'
# es la columna donde las exportaciones escriben `contacto1`.
_ALIAS_ENCABEZADOS_PROVEEDORES = {
    'telefono': 'contacto1',
    'fono': 'contacto1',
    'correo': 'email',
    'e_mail': 'email',
    'mail': 'email',
}

# Columna del archivo -> (campo de Empresa, largo máximo)
_CAMPOS_PROVEEDOR_IMPORTABLES = {
    'nombre': ('nombre', 100),
    'nombre_fantasia': ('nombre_fantasia', 255),
    'razon_social': ('razon_social', 255),
    'giro': ('giro', 255),
    'direccion': ('direccion', 255),
    'comuna': ('comuna', 100),
    'ciudad': ('ciudad', 100),
    'acteco': ('acteco', 20),
    'contacto1': ('contacto1', 100),
    'contacto2': ('contacto2', 100),
    'correo_vendedor': ('correoVendedor', 100),
    'correo_intercambio': ('correoIntercambio', 100),
    'correo_administrador': ('correoAdministrador', 100),
}
_CORREOS_PROVEEDOR = ('correoVendedor', 'correoIntercambio', 'correoAdministrador')
_CORREO_PLACEHOLDER = 'sin@correo.com'
_TEXTO_PLACEHOLDER = 'Sin especificar'


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def ver_importacion_proveedores(request):
    """Vista para importar proveedores desde CSV/Excel"""
    return render(request, 'vistas/modulo_compras/importacion_proveedores.html')


@require_POST
@requiere_permiso('gestion_dte_compras', 'puede_crear')
def importar_proveedores_csv(request):
    """Importa proveedores desde CSV/XLSX.

    B11-04 / B13-04 / B11-05 / B11-13:
    - Exige crear en Gestión Documentos Compras; actualizar fichas existentes
      (modos crear_y_actualizar / solo_actualizar) exige además editar.
    - La ficha se busca por RUT normalizado prefiriendo la de proveedor (hay
      RUT con ficha cliente y ficha proveedor); la columna 'id' de nuestras
      exportaciones manda si su RUT coincide.
    - Las empresas del grupo (con sucursales) no se tocan desde aquí.
    - Al actualizar solo se escriben las columnas que vienen con valor; los
      placeholders ('Sin especificar', 'sin@correo.com') son solo para crear, y
      'email' solo se usa si la ficha no tiene ningún correo: exportar y
      reimportar sin cambios ya no borra direcciones, teléfonos, actecos ni
      correos de intercambio.
    - Cada fila en su propio savepoint: una fila mala no revierte en silencio
      las demás, y los contadores reflejan lo que realmente quedó guardado.
    """
    try:
        archivo = request.FILES.get('archivo_proveedores')
        if not archivo:
            return JsonResponse({
                'success': False,
                'error': 'No se proporcionó ningún archivo'
            })

        modo_actualizacion = request.POST.get('modo_actualizacion', 'crear_y_actualizar')
        if modo_actualizacion not in ('crear_y_actualizar', 'solo_crear', 'solo_actualizar'):
            modo_actualizacion = 'solo_crear'
        if modo_actualizacion != 'solo_crear' and not _permiso_importacion(request, 'puede_editar'):
            return _denegar_importacion(
                'No tienes permiso para modificar proveedores existentes. Usa el modo "Solo crear" '
                'o pide el permiso de edición de Gestión Documentos Compras.'
            )

        try:
            filas = _leer_filas_importacion(archivo, alias=_ALIAS_ENCABEZADOS_PROVEEDORES)
        except _ErrorArchivoImportacion as exc:
            return JsonResponse({'success': False, 'error': str(exc)})

        if filas:
            logger.debug("Importacion proveedores: encabezados=%s", list(filas[0][1].keys()))

        # Empresas del grupo: tienen sucursales y se editan desde Administración.
        empresas_propias = set(Sucursal.objects.values_list('empresa_id', flat=True))

        proveedores_creados = 0
        proveedores_actualizados = 0
        proveedores_sin_cambios = 0
        proveedores_omitidos = 0
        errores = []

        with transaction.atomic():
            for idx, fila in filas:
                try:
                    rut = (fila.get('rut') or '').strip()
                    nombre = (fila.get('nombre') or '').strip()
                    if not rut or not nombre:
                        errores.append(f'Fila {idx}: RUT y Nombre son requeridos (RUT="{rut}", Nombre="{nombre}")')
                        continue
                    if not _rut_valido(rut):
                        errores.append(f'Fila {idx}: RUT "{rut}" no válido (revisa el dígito verificador)')
                        continue
                    rut_canon = _rut_canonico(rut)

                    # Valores que trae la fila (solo columnas con dato)
                    presentes = {}
                    demasiado_largo = None
                    for columna, (campo, largo) in _CAMPOS_PROVEEDOR_IMPORTABLES.items():
                        valor = (fila.get(columna) or '').strip()
                        if not valor:
                            continue
                        if len(valor) > largo:
                            demasiado_largo = (columna, largo)
                            break
                        presentes[campo] = valor
                    if demasiado_largo:
                        errores.append(
                            f'Fila {idx}: "{demasiado_largo[0]}" supera los {demasiado_largo[1]} caracteres permitidos'
                        )
                        continue
                    email = (fila.get('email') or '').strip()
                    if len(email) > 100:
                        errores.append(f'Fila {idx}: "email" supera los 100 caracteres permitidos')
                        continue

                    # Ficha existente: la de la columna 'id' si su RUT coincide;
                    # si no, la de proveedor (hay RUT con ficha cliente gemela).
                    candidatos = list(_empresas_por_rut(rut_canon).order_by('-esProveedor', 'id'))
                    proveedor_existente = None
                    id_archivo = (fila.get('id') or '').strip()
                    if id_archivo.isdigit():
                        proveedor_existente = next((e for e in candidatos if e.id == int(id_archivo)), None)
                    if proveedor_existente is not None and not proveedor_existente.esProveedor:
                        proveedor_existente = next((e for e in candidatos if e.esProveedor), proveedor_existente)
                    if proveedor_existente is None and candidatos:
                        proveedor_existente = candidatos[0]

                    if any(e.id in empresas_propias for e in candidatos):
                        errores.append(
                            f'Fila {idx}: el RUT {rut_canon} es de una empresa del grupo; '
                            'se edita desde Administración de empresas, no desde la importación'
                        )
                        continue

                    with transaction.atomic():
                        if proveedor_existente:
                            if modo_actualizacion == 'solo_crear':
                                proveedores_omitidos += 1
                                continue
                            cambios = {}
                            for campo, valor in presentes.items():
                                if str(getattr(proveedor_existente, campo) or '').strip() != valor:
                                    cambios[campo] = valor
                            # 'email' (columna genérica de la exportación) solo se usa
                            # si la ficha no tiene NINGÚN correo real: nunca pisa ni
                            # completa el correo de intercambio DTE de un proveedor
                            # que ya tiene otro correo (exportar -> reimportar = sin cambios).
                            correos_actuales = [
                                (getattr(proveedor_existente, c) or '').strip() for c in _CORREOS_PROVEEDOR
                            ]
                            sin_correo_real = all(not c or c == _CORREO_PLACEHOLDER for c in correos_actuales)
                            # El placeholder no es un correo: nunca se escribe al
                            # actualizar, y solo cuenta como cambio lo que difiere
                            # de lo guardado (reimportar la exportación = sin cambios).
                            if email and email.lower() != _CORREO_PLACEHOLDER and sin_correo_real:
                                for campo in _CORREOS_PROVEEDOR:
                                    if campo in presentes:
                                        continue
                                    if (getattr(proveedor_existente, campo) or '').strip() != email:
                                        cambios[campo] = email
                            if not proveedor_existente.esProveedor:
                                cambios['esProveedor'] = True
                            if not cambios:
                                proveedores_sin_cambios += 1
                                continue
                            antes = {campo: getattr(proveedor_existente, campo) for campo in cambios}
                            for campo, valor in cambios.items():
                                setattr(proveedor_existente, campo, valor)
                            proveedor_existente.save(update_fields=list(cambios) + ['updated_at'])
                            proveedores_actualizados += 1
                            logger.warning(
                                'Proveedor actualizado por importación: usuario=%s empresa_id=%s cambios=%s',
                                request.user.username, proveedor_existente.id,
                                {c: (antes[c], cambios[c]) for c in cambios},
                            )
                        elif modo_actualizacion == 'solo_actualizar':
                            proveedores_omitidos += 1
                        else:
                            correo_default = email or _CORREO_PLACEHOLDER
                            nuevo = Empresa.objects.create(
                                rut=rut_canon,
                                nombre=presentes['nombre'],
                                nombre_fantasia=presentes.get('nombre_fantasia', presentes['nombre']),
                                razon_social=presentes.get('razon_social', presentes['nombre']),
                                giro=presentes.get('giro', _TEXTO_PLACEHOLDER),
                                direccion=presentes.get('direccion', _TEXTO_PLACEHOLDER),
                                comuna=presentes.get('comuna', _TEXTO_PLACEHOLDER),
                                ciudad=presentes.get('ciudad', _TEXTO_PLACEHOLDER),
                                esProveedor=True,
                                correoVendedor=presentes.get('correoVendedor', correo_default),
                                correoIntercambio=presentes.get('correoIntercambio', correo_default),
                                correoAdministrador=presentes.get('correoAdministrador', correo_default),
                                acteco=presentes.get('acteco'),
                                contacto1=presentes.get('contacto1'),
                                contacto2=presentes.get('contacto2'),
                            )
                            proveedores_creados += 1
                            logger.info(
                                'Proveedor creado por importación: usuario=%s empresa_id=%s rut=%s',
                                request.user.username, nuevo.id, rut_canon,
                            )
                except Exception as exc:
                    logger.exception('Importación proveedores: error en fila %s', idx)
                    errores.append(f'Fila {idx}: no se guardó: {_motivo_error_fila(exc)}')
                    continue
        mensaje = []
        if proveedores_creados > 0:
            mensaje.append(f'{proveedores_creados} proveedores creados')
        if proveedores_actualizados > 0:
            mensaje.append(f'{proveedores_actualizados} proveedores actualizados')
        if proveedores_sin_cambios > 0:
            mensaje.append(f'{proveedores_sin_cambios} sin cambios')
        if proveedores_omitidos > 0:
            mensaje.append(f'{proveedores_omitidos} proveedores omitidos')
        if errores:
            mensaje.append(f'{len(errores)} filas con errores')

        return JsonResponse({
            'success': True,
            'message': ', '.join(mensaje) if mensaje else 'No se procesaron proveedores',
            'proveedores_creados': proveedores_creados,
            'proveedores_actualizados': proveedores_actualizados,
            'proveedores_sin_cambios': proveedores_sin_cambios,
            'proveedores_omitidos': proveedores_omitidos,
            'errores': errores
        })

    except Exception:
        logger.exception('Error al importar proveedores')
        return JsonResponse({
            'success': False,
            'error': 'No se pudo importar el archivo de proveedores. Reintenta; si persiste, avisa a soporte.'
        })


# validar_rut_basico se borró el 2026-09-26 (pedido M1): sin uso; los
# importadores validan con _rut_valido → app.models.base.validar_rut_chileno.


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def descargar_formato_proveedores(request):
    """Descargar formato CSV de ejemplo para importar proveedores"""
    try:
        # Crear respuesta CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="formato_proveedores.csv"'
        response.write('\ufeff')  # BOM para UTF-8
        
        writer = csv.writer(response)
        
        # Encabezados
        writer.writerow([
            'rut', 'nombre', 'nombre_fantasia', 'razon_social', 
            'giro', 'direccion', 'comuna', 'ciudad',
            'email', 'telefono', 'acteco'
        ])
        
        # Filas de ejemplo (RUT sin puntos). B11-13: RUT con dígito
        # verificador válido; los anteriores (-7 / -8) no pasaban la validación.
        writer.writerow([
            '76123456-0',
            'Empresa Ejemplo SPA', 
            'Ejemplo', 
            'Empresa Ejemplo Sociedad por Acciones',
            'Comercio al por mayor',
            'Av. Principal 123',
            'Santiago',
            'Santiago',
            'contacto@ejemplo.cl',
            '+56912345678',
            '471010'
        ])
        writer.writerow([
            '77234567-4',
            'Distribuidora ABC Ltda',
            'ABC Distribuidora', 
            'Distribuidora ABC Limitada',
            'Distribución de productos',
            'Calle Comercio 456',
            'Providencia',
            'Santiago',
            'ventas@abc.cl',
            '+56987654321',
            '471020'
        ])
        
        return response

    except Exception:
        return _error_exportacion('formato de proveedores')


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def exportar_proveedores_actuales(request):
    """Exportar todos los proveedores actuales a CSV"""
    try:
        # Obtener proveedores
        proveedores = Empresa.objects.filter(esProveedor=True).order_by('nombre')
        
        # Crear respuesta CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="proveedores_actuales.csv"'
        response.write('\ufeff')  # BOM para UTF-8
        
        writer = csv.writer(response)
        
        # Encabezados
        writer.writerow([
            'id', 'rut', 'nombre', 'nombre_fantasia', 'razon_social', 
            'giro', 'direccion', 'comuna', 'ciudad',
            'email', 'telefono', 'acteco'
        ])
        
        # Datos
        for proveedor in proveedores:
            # Limpiar RUT (sin puntos)
            rut_limpio = proveedor.rut.replace('.', '') if proveedor.rut else ''
            
            writer.writerow(_fila_csv_segura([
                proveedor.id,
                rut_limpio,
                proveedor.nombre,
                proveedor.nombre_fantasia,
                proveedor.razon_social,
                proveedor.giro,
                proveedor.direccion,
                proveedor.comuna,
                proveedor.ciudad,
                proveedor.correoVendedor or proveedor.correoIntercambio or proveedor.correoAdministrador,
                proveedor.contacto1 or '',
                proveedor.acteco or ''
            ]))

        return response

    except Exception:
        return _error_exportacion('proveedores (CSV)')


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def exportar_proveedores_excel(request):
    """Exportar proveedores actuales a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        
        # Obtener proveedores
        proveedores = Empresa.objects.filter(esProveedor=True).order_by('nombre')
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Proveedores"
        
        # Estilos
        header_fill = PatternFill(start_color="0066CC", end_color="0066CC", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        
        # Encabezados
        headers = [
            'ID', 'RUT', 'Nombre', 'Nombre Fantasía', 'Razón Social',
            'Giro', 'Dirección', 'Comuna', 'Ciudad',
            'Email', 'Teléfono', 'Acteco'
        ]
        
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
        
        # Datos
        for row_idx, proveedor in enumerate(proveedores, start=2):
            # Limpiar RUT (sin puntos)
            rut_limpio = proveedor.rut.replace('.', '') if proveedor.rut else ''
            
            _xlsx_celda(ws, row_idx, 1, proveedor.id)
            _xlsx_celda(ws, row_idx, 2, rut_limpio)
            _xlsx_celda(ws, row_idx, 3, proveedor.nombre)
            _xlsx_celda(ws, row_idx, 4, proveedor.nombre_fantasia)
            _xlsx_celda(ws, row_idx, 5, proveedor.razon_social)
            _xlsx_celda(ws, row_idx, 6, proveedor.giro)
            _xlsx_celda(ws, row_idx, 7, proveedor.direccion)
            _xlsx_celda(ws, row_idx, 8, proveedor.comuna)
            _xlsx_celda(ws, row_idx, 9, proveedor.ciudad)
            _xlsx_celda(ws, row_idx, 10, proveedor.correoVendedor or proveedor.correoIntercambio or proveedor.correoAdministrador)
            _xlsx_celda(ws, row_idx, 11, proveedor.contacto1 or '')
            _xlsx_celda(ws, row_idx, 12, proveedor.acteco or '')
        
        # Ajustar ancho de columnas
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column].width = adjusted_width
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="proveedores_actuales.xlsx"'

        wb.save(response)
        return response

    except Exception:
        return _error_exportacion('proveedores (Excel)')


# ========== GESTIÓN DE DTEs - IMPORTACIÓN ==========

# Encabezados alternativos -> clave del importador de DTE (incluye los de las
# exportaciones Excel: 'Nº Documento', 'Tipo', 'Total', ...).
_ALIAS_ENCABEZADOS_DTE = {
    'rut_emisor': 'rut_proveedor',
    'rut': 'rut_proveedor',
    'proveedor_id': 'id_proveedor',
    'numero_dte': 'numero_documento',
    'no_documento': 'numero_documento',
    'n_documento': 'numero_documento',
    'nro_documento': 'numero_documento',
    'folio': 'numero_documento',
    'tipo': 'tipo_documento',
    'tipo_dte': 'tipo_documento',
    'subtotal': 'monto_neto',
    'neto': 'monto_neto',
    'total': 'monto_con_iva',
    'monto_total': 'monto_con_iva',
    'unidades_productos': 'unidades',
}

# Tipos que se pueden cargar como documento de COMPRA (los del modal
# 'Nuevo DTE Compra' más la factura exenta).
_TIPOS_DTE_IMPORTABLES = (
    'FACTURA ELECTRONICA', 'FACTURA EXENTA', 'GUIA',
    'NOTA DE CREDITO', 'NOTA DE DEBITO', 'COTIZACION',
)
_ALIAS_TIPO_DTE = {
    'FACTURA': 'FACTURA ELECTRONICA',
    'FACTURA AFECTA': 'FACTURA ELECTRONICA',
    'FACTURA EXENTA ELECTRONICA': 'FACTURA EXENTA',
    'FACTURA NO AFECTA O EXENTA ELECTRONICA': 'FACTURA EXENTA',
    'GUIA DE DESPACHO': 'GUIA',
    'GUIA DE DESPACHO ELECTRONICA': 'GUIA',
    'GUIA DESPACHO': 'GUIA',
    'NOTA DE CREDITO ELECTRONICA': 'NOTA DE CREDITO',
    'NC': 'NOTA DE CREDITO',
    'NOTA DE DEBITO ELECTRONICA': 'NOTA DE DEBITO',
    'ND': 'NOTA DE DEBITO',
}
# Campos que NO se tocan desde la importación si el DTE ya tiene pagos,
# líneas o recepciones (B11-01): cambiarlos descuadra pagos y cuadraturas.
_CAMPOS_DTE_PROTEGIDOS = ('monto_neto', 'monto_con_iva', 'descuento', 'fecha_emision')
_LIMITE_MONTO_DTE = Decimal('10000000000')  # max_digits=12, decimal_places=2
_LIMITE_ENTERO = 2147483647
_METODO_PAGO_NC_COMPRA = 'Nota de Crédito'


def _pagos_como_instrumento(dte, ids_emisor=None):
    """Pagos de OTRAS facturas en que `dte` es el instrumento (no pagos del
    propio documento): una NC de compra aplicada ('Nota de Crédito', voucher =
    folio de la NC), una factura usada en 'Compensación con Factura' (voucher
    = folio) o una factura emitida (FK documento_compensacion). Cambiar sus
    montos o descartarlo deja esos pagos con un valor que ya no existe
    (B11-01 / B3-10).

    El voucher solo identifica al documento dentro del mismo proveedor (por
    RUT: `ids_emisor`, por defecto su propia ficha), del mismo tipo (una NC y
    una factura pueden compartir folio) y de la misma empresa receptora (o
    factura sin receptor), como _pagos_de_nc en views.py.
    """
    q = Q(documento_compensacion_id=dte.id)
    metodo = None
    if dte.es_nota_credito or dte.tipo_documento == 'NOTA DE CREDITO':
        metodo = _METODO_PAGO_NC_COMPRA
    elif dte.tipo_documento == 'FACTURA ELECTRONICA':
        metodo = METODO_COMPENSACION
    if dte.tipo_transaccion == 'COMPRA' and metodo and dte.emisor_id and dte.numero_documento:
        receptor = Q(dte__receptor__isnull=True)
        if dte.receptor_id:
            receptor |= Q(dte__receptor_id=dte.receptor_id)
        q |= Q(
            metodo_pago__iexact=metodo,
            voucher=str(dte.numero_documento),
            dte__tipo_transaccion='COMPRA',
            dte__emisor_id__in=list(ids_emisor or [dte.emisor_id]),
        ) & receptor
    return Dte_Detalle_Pago.objects.filter(q).exclude(dte_id=dte.id)


def _tipo_documento_importado(valor):
    """(tipo del sistema, None) o (None, error). Acepta el código SII (33, 34,
    52, 56, 61...) o el nombre sin distinguir mayúsculas ni tildes; vacío =
    FACTURA ELECTRONICA. Antes el '33' del formato se guardaba literal y ese
    documento quedaba fuera de todo filtro por tipo (B11-06)."""
    texto = re.sub(r'\s+', ' ', _texto_sin_tildes(valor).strip().upper())
    if not texto:
        return 'FACTURA ELECTRONICA', None
    if texto.isdigit():
        from app.services.dte_xml_parser import TIPO_DTE_SII
        tipo = TIPO_DTE_SII.get(int(texto))
    else:
        tipo = _ALIAS_TIPO_DTE.get(texto, texto)
    if tipo not in _TIPOS_DTE_IMPORTABLES:
        return None, (
            f'tipo de documento "{valor}" no reconocido: usa FACTURA ELECTRONICA, FACTURA EXENTA, '
            'GUIA, NOTA DE CREDITO, NOTA DE DEBITO o COTIZACION, o el código SII (33, 34, 52, 56, 61)'
        )
    return tipo, None


def _tipos_equivalentes(tipo):
    """El tipo y los códigos SII con que el importador anterior lo guardaba
    literal ('33'): así también se detectan esos duplicados."""
    from app.services.dte_xml_parser import TIPO_DTE_SII
    return [tipo] + [str(codigo) for codigo, nombre in TIPO_DTE_SII.items() if nombre == tipo]


def _decimal_importado(texto):
    """'119000' / '119.000' / '119000,5' / '$ 1.190.000' / '632447.0' ->
    Decimal. None si viene vacío; ValueError si no es un número. Antes
    '119.000' (miles es-CL) se leía como 119."""
    t = re.sub(r'[\s$]', '', str(texto or ''))
    if not t:
        return None
    if ',' in t and '.' in t:
        if t.rfind(',') > t.rfind('.'):
            t = t.replace('.', '').replace(',', '.')
        else:
            t = t.replace(',', '')
    elif ',' in t:
        t = t.replace(',', '') if re.fullmatch(r'-?\d{1,3}(,\d{3})+', t) else t.replace(',', '.')
    elif re.fullmatch(r'-?\d{1,3}(\.\d{3})+', t):
        t = t.replace('.', '')
    try:
        valor = Decimal(t)
    except Exception:
        raise ValueError(texto)
    if not valor.is_finite():
        raise ValueError(texto)
    return valor


def _entero_importado(texto, campo):
    """Entero >= 0 o None si viene vacío; ValueError(campo) si no es entero."""
    t = str(texto or '').strip()
    if not t:
        return None
    if re.fullmatch(r'\d{1,3}(\.\d{3})+', t):
        t = t.replace('.', '')
    if t.endswith('.0'):
        t = t[:-2]
    if not t.isdigit() or int(t) > _LIMITE_ENTERO:
        raise ValueError(campo)
    return int(t)


_FORMATOS_FECHA_IMPORTACION = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y', '%Y/%m/%d')


def _fecha_importada(texto):
    """Fecha del archivo o None si viene vacía; ValueError si no se entiende.
    Antes una fecha ilegible o ausente se reemplazaba por HOY en silencio."""
    t = str(texto or '').strip()
    if not t or t.lower() == 'none':
        return None
    t = t.split(' ')[0].split('T')[0]
    for formato in _FORMATOS_FECHA_IMPORTACION:
        try:
            return datetime.strptime(t, formato).date()
        except ValueError:
            continue
    raise ValueError(texto)


def _montos_importados(tipo, neto, total):
    """(neto, total) a guardar. Si viene uno solo, el otro se calcula con IVA
    19 % redondeado half-up a peso; la factura exenta no lleva IVA (antes se
    le inventaba). Si vienen ambos se respetan (exportar -> reimportar no
    recalcula montos)."""
    from decimal import ROUND_HALF_UP
    peso = Decimal('1')
    exenta = tipo == 'FACTURA EXENTA'
    if neto is None:
        neto = total if exenta else (total / Decimal('1.19')).quantize(peso, ROUND_HALF_UP)
    elif total is None:
        total = neto if exenta else neto + (neto * Decimal('0.19')).quantize(peso, ROUND_HALF_UP)
    centavo = Decimal('0.01')
    return neto.quantize(centavo, ROUND_HALF_UP), total.quantize(centavo, ROUND_HALF_UP)


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def ver_importacion_dtes(request):
    """Vista para importar DTEs desde CSV/Excel"""
    # Obtener proveedores para el selector
    proveedores = Empresa.objects.filter(esProveedor=True).order_by('nombre')
    return render(request, 'vistas/modulo_compras/importacion_dtes.html', {
        'proveedores': proveedores
    })


@require_POST
@requiere_permiso('gestion_dte_compras', 'puede_crear')
def importar_dtes_csv(request):
    """Importa DTE de COMPRA desde CSV/XLSX para la empresa en sesión.

    B11-01 / B13-04 / B11-05 / B11-06 / B11-13:
    - Exige crear en Gestión Documentos Compras; el modo 'crear_y_actualizar'
      exige además editar.
    - El receptor es SIEMPRE la empresa en sesión (se ignora 'receptor_id').
    - Duplicado = mismo RUT emisor (cualquier ficha) + tipo + folio, sin
      importar tipo_transaccion. Solo se actualiza una COMPRA viva de la
      empresa en sesión; si el folio existe como VENTA, TRASPASO, compra de
      otra empresa o descartado, la fila se informa y no se toca nada.
    - Al actualizar solo se escriben las columnas que vienen con valor, y si
      el DTE ya tiene pagos, líneas o recepciones no se cambian montos ni
      fecha de emisión. Cada cambio queda en el log con valor anterior.
    - Tipo SII numérico se traduce ('33' -> FACTURA ELECTRONICA); fecha
      ilegible o ausente es error de fila (ya no 'hoy'); exentas sin IVA.
    - Cada fila en su propio savepoint.
    """
    try:
        archivo = request.FILES.get('archivo_dtes')
        if not archivo:
            return JsonResponse({
                'success': False,
                'error': 'No se proporcionó ningún archivo'
            })

        tipo_busqueda = 'id' if request.POST.get('tipo_busqueda') == 'id' else 'rut'
        modo_actualizacion = request.POST.get('modo_actualizacion', 'solo_crear')
        if modo_actualizacion != 'crear_y_actualizar':
            modo_actualizacion = 'solo_crear'
        if modo_actualizacion == 'crear_y_actualizar' and not _permiso_importacion(request, 'puede_editar'):
            return _denegar_importacion(
                'No tienes permiso para modificar documentos existentes. Usa el modo "Solo crear" '
                'o pide el permiso de edición de Gestión Documentos Compras.'
            )

        try:
            empresa_id = int(request.session.get('idEmpresaActual'))
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Empresa no identificada en sesión'}, status=400)

        try:
            filas = _leer_filas_importacion(archivo, alias=_ALIAS_ENCABEZADOS_DTE)
        except _ErrorArchivoImportacion as exc:
            return JsonResponse({'success': False, 'error': str(exc)})

        if filas:
            logger.debug("Importacion DTE compras: encabezados=%s", list(filas[0][1].keys()))

        dtes_creados = 0
        dtes_actualizados = 0
        dtes_sin_cambios = 0
        dtes_omitidos = 0
        errores = []
        emisores = {}          # ('rut'|'id', clave) -> Empresa | None
        ids_mismo_rut = {}     # emisor.id -> [ids de fichas con su RUT]

        with transaction.atomic():
            for idx, fila in filas:
                try:
                    # --- Proveedor (emisor)
                    if tipo_busqueda == 'rut':
                        rut_proveedor = (fila.get('rut_proveedor') or '').strip()
                        if not rut_proveedor:
                            errores.append(f'Fila {idx}: RUT de proveedor requerido')
                            continue
                        clave = ('rut', _rut_canonico(rut_proveedor))
                        if clave not in emisores:
                            emisores[clave] = (
                                _empresas_por_rut(rut_proveedor)
                                .filter(esProveedor=True).order_by('-activo', 'id').first()
                            )
                        emisor = emisores[clave]
                        if not emisor:
                            errores.append(f'Fila {idx}: Proveedor con RUT "{rut_proveedor}" no encontrado')
                            continue
                    else:
                        id_proveedor = (fila.get('id_proveedor') or '').strip()
                        if not id_proveedor:
                            errores.append(f'Fila {idx}: ID de proveedor requerido')
                            continue
                        if not id_proveedor.isdigit():
                            errores.append(f'Fila {idx}: ID de proveedor "{id_proveedor}" no válido')
                            continue
                        clave = ('id', int(id_proveedor))
                        if clave not in emisores:
                            emisores[clave] = Empresa.objects.filter(id=int(id_proveedor), esProveedor=True).first()
                        emisor = emisores[clave]
                        if not emisor:
                            errores.append(f'Fila {idx}: Proveedor con ID "{id_proveedor}" no encontrado')
                            continue

                    # --- Folio y tipo
                    numero_txt = (fila.get('numero_documento') or '').strip()
                    if not numero_txt:
                        errores.append(f'Fila {idx}: Número de documento requerido')
                        continue
                    try:
                        folio = _entero_importado(numero_txt, 'numero_documento')
                    except ValueError:
                        folio = None
                    if not folio:
                        errores.append(f'Fila {idx}: número de documento "{numero_txt}" no válido')
                        continue
                    tipo, error_tipo = _tipo_documento_importado(fila.get('tipo_documento'))
                    if error_tipo:
                        errores.append(f'Fila {idx}: {error_tipo}')
                        continue

                    # --- Valores de la fila (None = columna ausente o vacía)
                    try:
                        neto = _decimal_importado(fila.get('monto_neto')) or None
                        total = _decimal_importado(fila.get('monto_con_iva')) or None
                        descuento = _decimal_importado(fila.get('descuento'))
                    except ValueError as exc:
                        errores.append(f'Fila {idx}: el monto "{exc}" no es un número')
                        continue
                    if any(v is not None and (v < 0 or v >= _LIMITE_MONTO_DTE) for v in (neto, total)):
                        errores.append(f'Fila {idx}: monto fuera de rango')
                        continue
                    if descuento is not None and abs(descuento) >= Decimal('100000000'):
                        errores.append(f'Fila {idx}: descuento fuera de rango')
                        continue
                    try:
                        fecha_emision = _fecha_importada(fila.get('fecha_emision'))
                    except ValueError:
                        errores.append(
                            f'Fila {idx}: fecha de emisión "{fila.get("fecha_emision")}" no válida '
                            '(usa AAAA-MM-DD o DD/MM/AAAA)'
                        )
                        continue
                    try:
                        dias_credito = _entero_importado(fila.get('dias_credito'), 'dias_credito')
                        bultos = _entero_importado(fila.get('bultos'), 'bultos')
                        unidades = _entero_importado(fila.get('unidades'), 'unidades')
                    except ValueError as exc:
                        errores.append(f'Fila {idx}: "{exc}" debe ser un número entero')
                        continue
                    if dias_credito is not None and dias_credito > 3650:
                        errores.append(f'Fila {idx}: dias_credito fuera de rango')
                        continue
                    referencias = (fila.get('referencias') or '').strip() or None

                    if emisor.id not in ids_mismo_rut:
                        ids_mismo_rut[emisor.id] = list(
                            set(_empresas_por_rut(emisor.rut).values_list('id', flat=True)) | {emisor.id}
                        )
                    # Emisor = la propia empresa receptora (mismo RUT): no es una
                    # compra y sumaría como deuda consigo misma. Las compras a
                    # OTRA empresa del grupo sí son facturas reales y se aceptan.
                    if empresa_id in ids_mismo_rut[emisor.id]:
                        errores.append(
                            f'Fila {idx}: el emisor ({emisor.rut}) es la misma empresa que recibe el documento: '
                            'no es una compra (los movimientos internos se registran como traspaso)'
                        )
                        continue

                    with transaction.atomic():
                        # --- ¿Ya existe? Mismo RUT emisor + tipo + folio, de
                        # cualquier tipo_transaccion (no crear una COMPRA gemela
                        # de un traspaso o una venta).
                        existentes = list(
                            Dte.objects.filter(
                                emisor_id__in=ids_mismo_rut[emisor.id],
                                numero_documento=folio,
                                tipo_documento__in=_tipos_equivalentes(tipo),
                            ).order_by('id')
                        )
                        propios = [
                            d for d in existentes
                            if d.tipo_transaccion == 'COMPRA' and d.receptor_id == empresa_id and not d.descartado
                        ]

                        if propios:
                            if modo_actualizacion == 'solo_crear':
                                dtes_omitidos += 1
                                continue
                            if len(propios) > 1:
                                errores.append(
                                    f'Fila {idx}: hay {len(propios)} documentos con el folio {folio} ({tipo}) '
                                    'de ese proveedor; corrígelo desde Gestión DTE'
                                )
                                continue
                            dte = Dte.objects.select_for_update(of=('self',)).get(pk=propios[0].pk)
                            bloqueado = (
                                Dte_Detalle_Pago.objects.filter(dte=dte).exists()
                                or Dte_Productos.objects.filter(dte=dte).exists()
                                or Productos_Recepcionados.objects.filter(dte=dte).exists()
                                # NC / factura ya aplicada como pago de OTRA factura:
                                # ese pago quedó con el monto actual.
                                or _pagos_como_instrumento(dte, ids_mismo_rut[emisor.id]).exists()
                            )
                            cambios = {}
                            if neto is not None or total is not None:
                                nuevo_neto, nuevo_total = _montos_importados(tipo, neto, total)
                                # Si la fila trae solo uno de los montos y ese no
                                # cambió, el otro se deja como está: recalcularlo
                                # (IVA redondeado a peso) reescribiría montos
                                # históricos con decimales sin que nada cambiara.
                                sin_cambio_real = (
                                    (neto is None and nuevo_total == dte.monto_con_iva)
                                    or (total is None and nuevo_neto == dte.monto_neto)
                                )
                                if not sin_cambio_real:
                                    if nuevo_neto != dte.monto_neto:
                                        cambios['monto_neto'] = nuevo_neto
                                    if nuevo_total != dte.monto_con_iva:
                                        cambios['monto_con_iva'] = nuevo_total
                            if descuento is not None and descuento != dte.descuento:
                                cambios['descuento'] = descuento
                            if fecha_emision is not None and fecha_emision != dte.fecha_emision:
                                cambios['fecha_emision'] = fecha_emision
                            if bloqueado:
                                protegidos = [c for c in _CAMPOS_DTE_PROTEGIDOS if c in cambios]
                                for campo in protegidos:
                                    cambios.pop(campo)
                                if protegidos:
                                    errores.append(
                                        f'Fila {idx}: el DTE {folio} (id {dte.id}) ya tiene pagos, líneas o '
                                        'recepciones, o está aplicado como pago de otra factura: '
                                        'no se cambiaron montos ni fecha de emisión'
                                    )
                            if dias_credito is not None and dias_credito != dte.diasCredito:
                                cambios['diasCredito'] = dias_credito
                            if 'fecha_emision' in cambios or 'diasCredito' in cambios:
                                vencimiento = (
                                    cambios.get('fecha_emision', dte.fecha_emision)
                                    + timedelta(days=cambios.get('diasCredito', dte.diasCredito) or 0)
                                )
                                if vencimiento != dte.fecha_vencimiento:
                                    cambios['fecha_vencimiento'] = vencimiento
                            if bultos is not None and bultos != dte.bultos:
                                cambios['bultos'] = bultos
                            if unidades is not None and unidades != dte.unidades_productos:
                                cambios['unidades_productos'] = unidades
                            if referencias is not None and referencias != (dte.referencias or ''):
                                cambios['referencias'] = referencias
                            if not cambios:
                                dtes_sin_cambios += 1
                                continue
                            antes = {campo: getattr(dte, campo) for campo in cambios}
                            for campo, valor in cambios.items():
                                setattr(dte, campo, valor)
                            dte.save(update_fields=list(cambios))
                            dtes_actualizados += 1
                            logger.warning(
                                'DTE compra actualizado por importación: usuario=%s dte_id=%s cambios=%s',
                                request.user.username, dte.id,
                                {c: (str(antes[c]), str(cambios[c])) for c in cambios},
                            )
                            continue

                        if existentes:
                            otro = existentes[0]
                            if otro.tipo_transaccion != 'COMPRA':
                                como = f'{otro.tipo_transaccion} (id {otro.id})'
                            elif otro.receptor_id != empresa_id:
                                como = f'compra de otra empresa receptora (id {otro.id})'
                            else:
                                como = f'documento descartado (id {otro.id})'
                            errores.append(
                                f'Fila {idx}: el folio {folio} ({tipo}) de {emisor.nombre} ya existe como {como}; '
                                'no se modificó ni se creó otro'
                            )
                            continue

                        # --- Crear
                        if neto is None and total is None:
                            errores.append(f'Fila {idx}: Debe proporcionar monto_neto o monto_con_iva')
                            continue
                        if fecha_emision is None:
                            errores.append(f'Fila {idx}: fecha_emision requerida (AAAA-MM-DD o DD/MM/AAAA)')
                            continue
                        nuevo_neto, nuevo_total = _montos_importados(tipo, neto, total)
                        dias = dias_credito if dias_credito is not None else 30
                        dte = Dte.objects.create(
                            emisor=emisor,
                            receptor_id=empresa_id,
                            numero_documento=folio,
                            tipo_documento=tipo,
                            monto_neto=nuevo_neto,
                            monto_con_iva=nuevo_total,
                            estado_pago='PENDIENTE',  # el usuario registra el pago después
                            estado_dte='EMITIDO',
                            responsable=request.user.username[:100],
                            fecha_emision=fecha_emision,
                            fecha_vencimiento=fecha_emision + timedelta(days=dias),
                            diasCredito=dias,
                            bultos=bultos or 0,
                            unidades_productos=unidades or 0,
                            descuento=descuento or Decimal('0'),
                            tipo_transaccion='COMPRA',
                            referencias=referencias or '',
                            es_nota_credito=(tipo == 'NOTA DE CREDITO'),
                        )
                        dtes_creados += 1
                        logger.info(
                            "DTE compra creado por importacion: usuario=%s dte_id=%s numero=%s emisor_id=%s receptor_id=%s",
                            request.user.username, dte.id, dte.numero_documento, emisor.id, empresa_id,
                        )
                except Exception as exc:
                    logger.exception('Importación DTE compras: error en fila %s', idx)
                    errores.append(f'Fila {idx}: no se guardó: {_motivo_error_fila(exc)}')
                    continue

        logger.info(
            "Resumen importacion DTE compras: usuario=%s creados=%s actualizados=%s sin_cambios=%s omitidos=%s errores=%s",
            request.user.username, dtes_creados, dtes_actualizados, dtes_sin_cambios, dtes_omitidos, len(errores),
        )

        mensaje = []
        if dtes_creados > 0:
            mensaje.append(f'{dtes_creados} DTEs creados')
        if dtes_actualizados > 0:
            mensaje.append(f'{dtes_actualizados} DTEs actualizados')
        if dtes_sin_cambios > 0:
            mensaje.append(f'{dtes_sin_cambios} sin cambios')
        if dtes_omitidos > 0:
            mensaje.append(f'{dtes_omitidos} DTEs omitidos (duplicados)')
        if errores:
            mensaje.append(f'{len(errores)} filas con observaciones')

        return JsonResponse({
            'success': True,
            'message': ', '.join(mensaje) if mensaje else 'No se procesaron DTEs',
            'dtes_creados': dtes_creados,
            'dtes_actualizados': dtes_actualizados,
            'dtes_sin_cambios': dtes_sin_cambios,
            'dtes_omitidos': dtes_omitidos,
            'errores': errores
        })

    except Exception:
        logger.exception('Error al importar DTEs')
        return JsonResponse({
            'success': False,
            'error': 'No se pudo importar el archivo de DTEs. Reintenta; si persiste, avisa a soporte.'
        })


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def descargar_formato_dtes(request):
    """Descargar formato CSV de ejemplo para importar DTEs"""
    try:
        tipo_busqueda = request.GET.get('tipo', 'rut')  # 'rut' o 'id'
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="formato_dtes.csv"'
        response.write('\ufeff')  # BOM para UTF-8
        
        writer = csv.writer(response)
        
        # Encabezados según tipo de búsqueda (sin responsable)
        # IMPORTANTE: Los nombres deben coincidir EXACTAMENTE con lo que busca el código
        if tipo_busqueda == 'rut':
            writer.writerow([
                'rut_proveedor', 
                'numero_documento', 
                'tipo_documento', 
                'fecha_emision', 
                'monto_con_iva', 
                'dias_credito', 
                'bultos', 
                'unidades', 
                'referencias'
            ])
            # Ejemplo 1 (RUT sin puntos, monto con IVA). B11-06 / B11-13: RUT
            # con dígito verificador válido y tipo con el nombre del sistema
            # (también se acepta el código SII: 33, 34, 52, 56, 61).
            writer.writerow([
                '76123456-0', '12345', 'FACTURA ELECTRONICA',
                '2024-12-11', '119000', '30',
                '2', '50', 'Orden de Compra 001'
            ])
            # Ejemplo 2
            writer.writerow([
                '77234567-4', '12346', 'FACTURA ELECTRONICA',
                '2024-12-10', '297500', '45',
                '5', '100', 'Orden de Compra 002'
            ])
        else:  # id
            writer.writerow([
                'id_proveedor', 
                'numero_documento', 
                'tipo_documento', 
                'fecha_emision', 
                'monto_con_iva', 
                'dias_credito',
                'bultos', 
                'unidades', 
                'referencias'
            ])
            writer.writerow([
                '1', '12345', 'FACTURA ELECTRONICA',
                '2024-12-11', '119000', '30',
                '2', '50', 'Orden de Compra 001'
            ])
            writer.writerow([
                '2', '12346', 'FACTURA ELECTRONICA',
                '2024-12-10', '297500', '45',
                '5', '100', 'Orden de Compra 002'
            ])

        return response

    except Exception:
        return _error_exportacion('formato de DTE')


def _dtes_compra_exportables(request):
    """DTE de compra de la empresa en sesión (o sin receptor), no descartados:
    lo mismo que muestra Gestión DTE. Antes se exportaban los de TODAS las
    empresas a cualquier usuario logueado (B11-01)."""
    empresa_id = request.session.get('idEmpresaActual')
    return (
        Dte.objects.filter(tipo_transaccion='COMPRA', descartado=False)
        .filter(Q(receptor_id=empresa_id) | Q(receptor__isnull=True))
        .select_related('emisor')
        .order_by('-fecha_emision', '-id')
    )


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def exportar_dtes_actuales(request):
    """Exportar DTEs de compras actuales a CSV"""
    try:
        tipo_exportacion = request.GET.get('tipo', 'rut')  # 'rut' o 'id'

        dtes = _dtes_compra_exportables(request)
        
        # Crear respuesta CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="dtes_compras_actuales.csv"'
        response.write('\ufeff')  # BOM para UTF-8
        
        writer = csv.writer(response)
        
        # Encabezados según tipo
        if tipo_exportacion == 'rut':
            writer.writerow([
                'id_dte', 'rut_proveedor', 'nombre_proveedor', 'numero_documento', 'tipo_documento',
                'fecha_emision', 'monto_neto', 'monto_iva', 'total', 'dias_credito',
                'bultos', 'unidades', 'referencias', 'estado_dte', 'estado_pago'
            ])
            
            for dte in dtes:
                # Limpiar RUT (sin puntos)
                rut_limpio = dte.emisor.rut.replace('.', '') if dte.emisor.rut else ''

                writer.writerow(_fila_csv_segura([
                    dte.id,
                    rut_limpio,
                    dte.emisor.nombre,
                    dte.numero_documento,
                    dte.tipo_documento,
                    dte.fecha_emision.strftime('%Y-%m-%d'),
                    float(dte.monto_neto),
                    float(dte.monto_con_iva - dte.monto_neto),
                    float(dte.monto_con_iva),
                    dte.diasCredito,
                    dte.bultos,
                    dte.unidades_productos,
                    dte.referencias or '',
                    dte.estado_dte,
                    dte.estado_pago
                ]))
        else:  # id
            writer.writerow([
                'id_dte', 'id_proveedor', 'nombre_proveedor', 'numero_documento', 'tipo_documento',
                'fecha_emision', 'monto_neto', 'monto_iva', 'total', 'dias_credito',
                'bultos', 'unidades', 'referencias', 'estado_dte', 'estado_pago'
            ])
            
            for dte in dtes:
                writer.writerow(_fila_csv_segura([
                    dte.id,
                    dte.emisor.id,
                    dte.emisor.nombre,
                    dte.numero_documento,
                    dte.tipo_documento,
                    dte.fecha_emision.strftime('%Y-%m-%d'),
                    float(dte.monto_neto),
                    float(dte.monto_con_iva - dte.monto_neto),
                    float(dte.monto_con_iva),
                    dte.diasCredito,
                    dte.bultos,
                    dte.unidades_productos,
                    dte.referencias or '',
                    dte.estado_dte,
                    dte.estado_pago
                ]))

        return response

    except Exception:
        return _error_exportacion('DTE de compra (CSV)')


@require_GET
@requiere_permiso('gestion_dte_compras', 'puede_ver')
def exportar_dtes_excel(request):
    """Exportar DTEs de compras actuales a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment

        tipo_exportacion = request.GET.get('tipo', 'rut')

        dtes = _dtes_compra_exportables(request)
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "DTEs Compras"
        
        # Estilos
        header_fill = PatternFill(start_color="0066CC", end_color="0066CC", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        
        # Encabezados
        if tipo_exportacion == 'rut':
            headers = [
                'ID DTE', 'RUT Proveedor', 'Nombre Proveedor', 'Nº Documento', 'Tipo',
                'Fecha Emisión', 'Monto Neto', 'IVA', 'Total', 'Días Crédito',
                'Bultos', 'Unidades', 'Referencias', 'Estado DTE', 'Estado Pago'
            ]
        else:
            headers = [
                'ID DTE', 'ID Proveedor', 'Nombre Proveedor', 'Nº Documento', 'Tipo',
                'Fecha Emisión', 'Monto Neto', 'IVA', 'Total', 'Días Crédito',
                'Bultos', 'Unidades', 'Referencias', 'Estado DTE', 'Estado Pago'
            ]
        
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
        
        # Datos
        for row_idx, dte in enumerate(dtes, start=2):
            if tipo_exportacion == 'rut':
                # Limpiar RUT (sin puntos)
                rut_limpio = dte.emisor.rut.replace('.', '') if dte.emisor.rut else ''
                _xlsx_celda(ws, row_idx, 1, dte.id)
                _xlsx_celda(ws, row_idx, 2, rut_limpio)
            else:
                _xlsx_celda(ws, row_idx, 1, dte.id)
                _xlsx_celda(ws, row_idx, 2, dte.emisor.id)

            _xlsx_celda(ws, row_idx, 3, dte.emisor.nombre)
            _xlsx_celda(ws, row_idx, 4, dte.numero_documento)
            _xlsx_celda(ws, row_idx, 5, dte.tipo_documento)
            _xlsx_celda(ws, row_idx, 6, dte.fecha_emision)
            _xlsx_celda(ws, row_idx, 7, float(dte.monto_neto))
            _xlsx_celda(ws, row_idx, 8, float(dte.monto_con_iva - dte.monto_neto))
            _xlsx_celda(ws, row_idx, 9, float(dte.monto_con_iva))
            _xlsx_celda(ws, row_idx, 10, dte.diasCredito)
            _xlsx_celda(ws, row_idx, 11, dte.bultos)
            _xlsx_celda(ws, row_idx, 12, dte.unidades_productos)
            _xlsx_celda(ws, row_idx, 13, dte.referencias or '')
            _xlsx_celda(ws, row_idx, 14, dte.estado_dte)
            _xlsx_celda(ws, row_idx, 15, dte.estado_pago)
        
        # Ajustar ancho de columnas
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column].width = adjusted_width
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="dtes_compras_actuales.xlsx"'

        wb.save(response)
        return response

    except Exception:
        return _error_exportacion('DTE de compra (Excel)')


# ========== DASHBOARD COMPRAS MEJORADO ==========
#
# Todas las secciones se alimentan de un mismo juego de datos compartidos
# (_DashboardComprasDatos): cada consulta se hace UNA vez, agrupada fino en SQL
# (por compra / por proveedor / por mes / por sucursal) y se re-suma en Python.
# Como todos los campos que se suman son enteros, la re-suma es exacta y los
# números son los mismos que antes, cuando cada sección repetía sus propias
# consultas (60 por petición, varias literalmente duplicadas; hoy ~28).
#
# El filtro "Período" define UN rango de fechas [desde, hasta] que se aplica a
# todas las secciones (compras, kardex de traspasos, ventas, lotes, facturas por
# concepto); "año anterior" es ese mismo rango corrido un año (un rango en
# curso, como el año actual, se compara hasta el mismo día; un rango de más de
# un año no se compara).

ESTADOS_COMPRA_EXCLUIDOS = ('ELIMINADA', 'CANCELADA')
FAMILIAS_TEMPORADA = {'VERANO', 'OTONO', 'INVIERNO', 'PRIMAVERA'}
FAMILIA_TEMPORADA_LEGIBLE = {
    'VERANO': 'Verano', 'OTONO': 'Otoño', 'INVIERNO': 'Invierno', 'PRIMAVERA': 'Primavera',
}
# Presets del filtro Período: N días calendario incluido hoy (igual que
# "Últimos 7/30 días" de los reportes de ventas: hoy - (N-1) .. hoy).
PRESETS_PERIODO_DIAS = {'trimestre': 90, 'mes': 30, 'semana': 7}
# Cota inferior del rango personalizado (la superior es 31-dic del año próximo).
# Sin cotas, 0001-01-01 reventaba al restar un año, 9999-12-31 al sumar un día
# y un rango de siglos devolvía decenas de MB de meses vacíos.
FECHA_MIN_DASHBOARD_COMPRAS = date(2000, 1, 1)
# Prefijo con que los flujos de ingreso de stock sin OC nombran la compra que
# crean (views.crear_producto_manual, existencias_nuevo): se registran ya
# recibidas, así que su "cumplimiento" es ingreso sobre ingreso, no de proveedor.
PREFIJO_COMPRA_MANUAL = 'Compra Manual -'
MESES_NOMBRES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
MESES_CORTOS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
                'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
CONCEPTO_SALIDA, CONCEPTO_ENTRADA = 'TRASPASO_SALIDA', 'TRASPASO_ENTRADA'


@login_required
def verDashboardComprasMejorado(request):
    """Vista principal del dashboard de compras mejorado"""
    return render(request, 'vistas/modulo_dashboards/dashboard_compras_mejorado.html')


def _normalizar_temporada(texto):
    """'Otoño' → 'OTONO': mayúsculas sin acentos, comparable con temporada_familia."""
    return ''.join(c for c in unicodedata.normalize('NFKD', (texto or '').strip().upper())
                   if not unicodedata.combining(c))


def _q_filtros_compra(temporada, proveedor_id, prefijo=''):
    """Q con los filtros Temporada y Proveedor sobre campos de Compras.

    `prefijo` permite aplicarlos desde una relación, p. ej.
    'compra_producto__compras__' desde Compras_Producto_Talla. Es el ÚNICO
    lugar donde se interpretan esos filtros: antes la evolución mensual y la
    comparativa anual los reimplementaban sin plegar acentos, así que
    "Otoño" filtraba por familia en los KPI y por texto libre en los gráficos.
    """
    q = Q()
    if temporada:
        t_norm = _normalizar_temporada(temporada)
        if t_norm in FAMILIAS_TEMPORADA:
            q &= Q(**{f'{prefijo}temporada_familia': t_norm})
        else:
            q &= Q(**{f'{prefijo}temporada__icontains': temporada})
    if proveedor_id:
        q &= Q(**{f'{prefijo}empresa_id': proveedor_id})
    return q


def _restar_un_anio(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 de febrero
        return d.replace(year=d.year - 1, day=28)


def _fmt(d):
    return d.strftime('%d-%m-%Y')


def _xlsx_append_seguro(ws, valores):
    """ws.append(...) que nunca deja una fórmula (exportar_dashboard_compras).

    openpyxl convierte en fórmula todo str que empieza con '=': un nombre de
    compra, proveedor, temporada o producto '=HYPERLINK(...)' se ejecutaría al
    abrir el Excel. Mismo criterio que _xlsx_celda: esos textos (y los que
    empiezan con + - @) quedan como TEXTO con quotePrefix, se ven tal cual.
    Los números (también negativos) no se tocan."""
    ws.append(valores)
    for celda in ws[ws.max_row]:
        if isinstance(celda.value, str) and celda.value.startswith(_PREFIJOS_FORMULA):
            celda.data_type = 's'
            celda.quotePrefix = True


def _resolver_periodo(request):
    """Convierte los filtros GET en UN rango de fechas [desde, hasta].

    - anual (default): 1 ene – 31 dic del año elegido.
    - trimestre / mes / semana: últimos 90 / 30 / 7 días calendario incluido
      HOY (hoy - (N-1) .. hoy, como los reportes de ventas). Van anclados a
      hoy, así que el selector de año no aplica y se informa el año de `hasta`.
    - personalizado: fecha_desde / fecha_hasta (YYYY-MM-DD); si faltan, cae a
      anual. Las fechas se acotan a [2000-01-01, 31-dic del año próximo].

    La comparación "año anterior" es el mismo rango corrido un año, salvo:
    - rango que empezó y termina después de hoy (año en curso, o un
      personalizado con fin futuro): se compara lo transcurrido, desde..hoy,
      contra desde..mismo día del año anterior (antes el año pasado COMPLETO,
      y la tarjeta salía -90 % por construcción; y el personalizado
      01-01..31-12 daba otra tendencia que el anual de las mismas fechas).
      Solo se recorta el lado anterior: desde/hasta no cambian. Un rango
      enteramente futuro (desde > hoy) se compara sin recorte, como antes.
    - rango de más de un año: se solaparía con su propio "año anterior"
      (meses contados en ambos lados), así que `comparable` = False y no se
      compara (se evalúa sobre el rango pedido, antes del recorte).
    """
    hoy = timezone.localdate()
    periodo = (request.GET.get('periodo') or 'anual').strip().lower()
    try:
        anio = int(request.GET.get('anio') or hoy.year)
    except (TypeError, ValueError):
        anio = hoy.year
    if not 2000 <= anio <= hoy.year + 1:   # evita date(99999, 1, 1) por un GET malformado
        anio = hoy.year

    desde = hasta = None
    if periodo == 'personalizado':
        try:
            desde = parse_date((request.GET.get('fecha_desde') or '').strip())
            hasta = parse_date((request.GET.get('fecha_hasta') or '').strip())
        except ValueError:
            desde = hasta = None
        if not desde or not hasta:
            periodo = 'anual'
        else:
            if desde > hasta:
                desde, hasta = hasta, desde
            # Se acota en vez de caer a anual: con un año a medio teclear
            # (0002, 0020, 0202…) caer a anual cambiaría el selector de Período.
            tope = date(hoy.year + 1, 12, 31)
            desde = min(max(desde, FECHA_MIN_DASHBOARD_COMPRAS), tope)
            hasta = min(max(hasta, FECHA_MIN_DASHBOARD_COMPRAS), tope)
    if periodo in PRESETS_PERIODO_DIAS:
        hasta = hoy
        desde = hoy - timedelta(days=PRESETS_PERIODO_DIAS[periodo] - 1)
    elif periodo != 'personalizado':
        periodo = 'anual'
        desde, hasta = date(anio, 1, 1), date(anio, 12, 31)
    if periodo != 'anual':
        anio = hasta.year

    desde_ant, hasta_ant = _restar_un_anio(desde), _restar_un_anio(hasta)
    # Sin solape entre el rango y su "año anterior" (rangos de hasta un año).
    # Se evalúa sobre el rango pedido, antes del recorte: el recorte no vuelve
    # comparable un rango de más de un año.
    comparable = desde > hasta_ant
    # Rango en curso (desde <= hoy < hasta): el anual del año actual o un
    # personalizado con fin futuro. Se compara solo lo transcurrido, hasta el
    # mismo día del año anterior; así el mismo rango da la misma tendencia
    # se elija como "anual" o como "personalizado". Solo acorta el lado
    # anterior, así que `comparable` sigue valiendo.
    corte = min(hasta, hoy)
    recortado = comparable and desde <= corte < hasta
    if recortado:
        hasta_ant = _restar_un_anio(corte)

    if periodo == 'anual':
        etiqueta = f'Año {anio} completo'
        if recortado:
            etiqueta_comparacion = f'vs {anio - 1} al {hasta_ant:%d-%m}'
            etiqueta_anterior = f'{anio - 1} (al {_fmt(hasta_ant)})'
        else:
            etiqueta_comparacion = f'vs {anio - 1}'
            etiqueta_anterior = str(anio - 1)
    else:
        if periodo in PRESETS_PERIODO_DIAS:
            etiqueta = f'Últimos {PRESETS_PERIODO_DIAS[periodo]} días ({_fmt(desde)} al {_fmt(hasta)})'
        else:
            etiqueta = f'Del {_fmt(desde)} al {_fmt(hasta)}'
        # Un rango que cruza el 1 de enero se compara contra dos años (antes
        # decía solo el de `hasta`: "vs 2025" para 01-12-2024..31-01-2025).
        anios_ant = (str(hasta_ant.year) if desde_ant.year == hasta_ant.year
                     else f'{desde_ant.year}-{hasta_ant.year}')
        etiqueta_comparacion = f'vs mismo período {anios_ant}'
        if recortado:
            etiqueta_comparacion += f' al {hasta_ant:%d-%m}'
        etiqueta_anterior = f'{_fmt(desde_ant)} al {_fmt(hasta_ant)}'
    if not comparable:
        etiqueta_comparacion = 'sin comparación (rango mayor a 1 año)'
        etiqueta_anterior = 'sin comparación'

    return {
        'anio': anio, 'periodo': periodo,
        'desde': desde, 'hasta': hasta,
        'desde_ant': desde_ant, 'hasta_ant': hasta_ant,
        'comparable': comparable,
        'anual': periodo == 'anual',
        'etiqueta': etiqueta,
        'etiqueta_actual': str(anio) if periodo == 'anual' else f'{_fmt(desde)} al {_fmt(hasta)}',
        'etiqueta_anterior': etiqueta_anterior,
        'etiqueta_comparacion': etiqueta_comparacion,
    }


def _rango_datetime(desde, hasta):
    """[desde 00:00, hasta+1 00:00) en hora local (America/Santiago).

    Equivale a `campo__year=` / `campo__date__range=` sobre un DateTimeField,
    pero comparando timestamps directamente, así que usa el índice en vez de
    castear cada fila a fecha."""
    tz = timezone.get_current_timezone()
    ini = timezone.make_aware(datetime.combine(desde, time.min), tz)
    fin = timezone.make_aware(datetime.combine(hasta + timedelta(days=1), time.min), tz)
    return ini, fin


def _meses_en_rango(desde, hasta):
    """[(año, mes), ...] de los meses que toca el rango, en orden cronológico."""
    y, m = desde.year, desde.month
    out = []
    while (y, m) <= (hasta.year, hasta.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _costo_linea():
    return F('stock') * F('compra_producto__costo')


def _valor_linea():
    return F('stock') * F('compra_producto__precioSugerido')


class _DashboardComprasDatos:
    """Consultas compartidas del dashboard. Cada propiedad consulta una sola vez
    (cached_property) y las secciones re-suman en Python lo que necesitan."""

    def __init__(self, rango, temporada, proveedor_id, empresa_actual_id):
        self.r = rango
        self.temporada = temporada
        self.proveedor_id = proveedor_id
        self.empresa_actual_id = empresa_actual_id
        self.q_filtros = _q_filtros_compra(temporada, proveedor_id)
        self.q_filtros_cpt = _q_filtros_compra(temporada, proveedor_id, 'compra_producto__compras__')

    # ---------- Compras del período ----------

    @cached_property
    def compras(self):
        """Cabeceras del período (id, proveedor, fecha, nombre, temporada).

        De aquí salen: total de compras, proveedores activos, compras por mes,
        compras por proveedor y las 20 filas de "Rendimiento detallado" —
        antes eran cinco consultas separadas sobre el mismo queryset."""
        # Más recientes primero: la tabla de rendimiento muestra las 20 primeras
        # y antes salían en orden arbitrario (sin ORDER BY).
        qs = (Compras.objects
              .filter(fecha__range=(self.r['desde'], self.r['hasta']))
              .exclude(estado__in=ESTADOS_COMPRA_EXCLUIDOS)
              .filter(self.q_filtros)
              .order_by('-fecha', '-id'))
        return list(qs.values('id', 'empresa_id', 'empresa__nombre', 'fecha', 'nombre', 'temporada'))

    @cached_property
    def compras_ids(self):
        return [c['id'] for c in self.compras]

    @cached_property
    def lineas_por_compra(self):
        """Por compra: unidades pedidas, inversión (costo neto), valor de lista y
        cuánto de esa inversión está enlazada a un producto del catálogo."""
        rows = (Compras_Producto_Talla.objects
                .filter(compra_producto__compras_id__in=self.compras_ids)
                .values('compra_producto__compras_id')
                .annotate(esperadas=Sum('stock'),
                          inversion=Sum(_costo_linea()),
                          valor=Sum(_valor_linea()),
                          inv_enlazada=Sum(_costo_linea(), filter=Q(producto_talla__isnull=False)))
                .order_by())
        return {r['compra_producto__compras_id']: r for r in rows}

    @cached_property
    def recibidas_por_compra(self):
        rows = (Productos_Recepcionados.objects
                .filter(compra_producto_talla__compra_producto__compras_id__in=self.compras_ids)
                .values('compra_producto_talla__compra_producto__compras_id')
                .annotate(recibidas=Sum('stockArribado'))
                .order_by())
        return {r['compra_producto_talla__compra_producto__compras_id']: int(r['recibidas'] or 0)
                for r in rows}

    @cached_property
    def totales(self):
        lin = list(self.lineas_por_compra.values())
        return {
            'unidades_esperadas': sum(int(x['esperadas'] or 0) for x in lin),
            'inversion': sum(x['inversion'] or 0 for x in lin),
            'valor_venta': sum(x['valor'] or 0 for x in lin),
            'inv_enlazada': sum(x['inv_enlazada'] or 0 for x in lin),
            'unidades_recepcionadas': sum(self.recibidas_por_compra.values()),
        }

    @cached_property
    def desglose_origen(self):
        """Mismos totales separados en OC reales e ingresos sin OC ("Compra
        Manual -"), para rotular qué mide cada cifra. Sin consultas propias.

        Un ingreso manual crea su línea y su recepción con la misma cantidad,
        así que su cumplimiento es ~100 % por construcción: el cumplimiento de
        proveedores solo se puede leer sobre las OC."""
        out = {k: {'n': 0, 'inversion': 0, 'esperadas': 0, 'recibidas': 0}
               for k in ('oc', 'manual')}
        for c in self.compras:
            k = 'manual' if (c['nombre'] or '').startswith(PREFIJO_COMPRA_MANUAL) else 'oc'
            acc = out[k]
            acc['n'] += 1
            lin = self.lineas_por_compra.get(c['id'])
            if lin:
                acc['inversion'] += lin['inversion'] or 0
                acc['esperadas'] += int(lin['esperadas'] or 0)
            acc['recibidas'] += self.recibidas_por_compra.get(c['id'], 0)
        return out

    @cached_property
    def por_proveedor(self):
        """{empresa_id: nombre, inversion, esperadas, recibidas, n_compras}.
        Alimenta Pareto, Top proveedores y Cumplimiento (antes 6 consultas)."""
        out = {}
        for c in self.compras:
            d = out.setdefault(c['empresa_id'], {
                'nombre': c['empresa__nombre'], 'inversion': 0,
                'esperadas': 0, 'recibidas': 0, 'n_compras': 0,
            })
            d['n_compras'] += 1
            lin = self.lineas_por_compra.get(c['id'])
            if lin:
                d['inversion'] += lin['inversion'] or 0
                d['esperadas'] += int(lin['esperadas'] or 0)
            d['recibidas'] += self.recibidas_por_compra.get(c['id'], 0)
        return out

    # ---------- Series mensuales ----------

    @cached_property
    def inversion_mensual(self):
        """{(año, mes): {'filtrada', 'filtrada_ant', 'total'}}.

        - filtrada: inversión del período con los filtros temporada/proveedor
          (evolución mensual y barra "actual" de la comparativa).
        - filtrada_ant: lo mismo para el período corrido un año (barra "anterior").
        - total: inversión del período SIN temporada/proveedor (flujo Compras →
          Despachos → Ventas, que no se filtra por proveedor porque despachos y
          ventas tampoco pueden filtrarse así).
        Una consulta en vez de cuatro."""
        r = self.r
        q_act = Q(compra_producto__compras__fecha__range=(r['desde'], r['hasta']))
        q_ant = Q(compra_producto__compras__fecha__range=(r['desde_ant'], r['hasta_ant']))
        rows = (Compras_Producto_Talla.objects
                .filter(q_act | q_ant)
                .exclude(compra_producto__compras__estado__in=ESTADOS_COMPRA_EXCLUIDOS)
                .annotate(y=ExtractYear('compra_producto__compras__fecha'),
                          m=ExtractMonth('compra_producto__compras__fecha'))
                .values('y', 'm')
                .annotate(filtrada=Sum(_costo_linea(), filter=self.q_filtros_cpt & q_act),
                          filtrada_ant=Sum(_costo_linea(), filter=self.q_filtros_cpt & q_ant),
                          total=Sum(_costo_linea(), filter=q_act))
                .order_by())
        return {(row['y'], row['m']): row for row in rows}

    @cached_property
    def ventas_sucursal_mes(self):
        """Tickets PAGADOS del período por (sucursal, año, mes)."""
        ini, fin = _rango_datetime(self.r['desde'], self.r['hasta'])
        return list(Ticket.objects
                    .filter(created_at__gte=ini, created_at__lt=fin, estado='PAGADO')
                    .annotate(y=ExtractYear('created_at'), m=ExtractMonth('created_at'))
                    .values('sucursal_id', 'y', 'm')
                    .annotate(t=Sum('total'))
                    .order_by())

    @cached_property
    def ventas_por_mes(self):
        out = {}
        for v in self.ventas_sucursal_mes:
            out[(v['y'], v['m'])] = out.get((v['y'], v['m']), 0) + (v['t'] or 0)
        return {k: float(v) for k, v in out.items()}

    @cached_property
    def ventas_por_sucursal(self):
        out = {}
        for v in self.ventas_sucursal_mes:
            out[v['sucursal_id']] = out.get(v['sucursal_id'], 0) + (v['t'] or 0)
        return {k: float(v) for k, v in out.items()}

    @cached_property
    def lineas_venta_por_sucursal(self):
        """{sucursal_id: {'unidades', 'costo'}}: unidades vendidas y costo FIFO
        de lo vendido en el período (costo_fifo=0 en líneas legacy: ahí el
        margen no se publica, ver rentabilidad de sucursales vendedoras)."""
        ini, fin = _rango_datetime(self.r['desde'], self.r['hasta'])
        rows = (Ticket_Productos.objects
                .filter(idTicket__created_at__gte=ini, idTicket__created_at__lt=fin,
                        idTicket__estado='PAGADO')
                .values('idTicket__sucursal_id')
                .annotate(unidades=Sum('stock'), costo=Sum(F('stock') * F('costo_fifo')))
                .order_by())
        return {r['idTicket__sucursal_id']: r for r in rows}

    # ---------- Kardex de traspasos ----------

    @cached_property
    def traspasos_kardex(self):
        """Salidas y entradas de traspaso COMPLETADAS del período, agrupadas por
        (concepto, origen, destino, año, mes).

        Seis lecturas distintas (unidades despachadas desde CD, despachos por
        sucursal, flujo mensual, sobreprecio por destino, sobreprecio por CD y
        entradas por destino) salen de esta única consulta. `cantidad` es
        negativa en las salidas: por eso se guardan tanto la suma con ABS
        (unidades, costo, sobreprecio) como la suma con signo (cantidad, valor)."""
        rows = (Movimientos_Producto.objects
                .filter(fecha__range=(self.r['desde'], self.r['hasta']),
                        concepto__in=(CONCEPTO_SALIDA, CONCEPTO_ENTRADA),
                        estado='COMPLETADO')
                .annotate(y=ExtractYear('fecha'), m=ExtractMonth('fecha'))
                .values('concepto', 'sucursal_origen_id', 'sucursal_destino_id',
                        'sucursal_destino__alias', 'sucursal_destino__empresa__nombre', 'y', 'm')
                # Nombres distintos de los campos (costo, cantidad, sobreprecio):
                # dentro de un mismo annotate() una anotación tapa al campo homónimo.
                .annotate(unidades=Sum(Abs(F('cantidad'))),
                          cant_signo=Sum('cantidad'),
                          costo_total=Sum(F('costo') * Abs(F('cantidad'))),
                          sobreprecio_total=Sum(F('sobreprecio') * Abs(F('cantidad'))),
                          valor_signo=Sum(F('cantidad') * F('costo')))
                .order_by())
        return list(rows)

    def salidas(self, solo_cd=False):
        cd_ids = self.sucursales_cd_ids
        for t in self.traspasos_kardex:
            if t['concepto'] != CONCEPTO_SALIDA:
                continue
            if solo_cd and t['sucursal_origen_id'] not in cd_ids:
                continue
            yield t

    # ---------- Sucursales ----------

    @cached_property
    def sucursales_cd(self):
        """Centros de distribución: [{'id', 'alias', 'empresa'}]. Mismo criterio
        para las tres secciones que lo usan (antes se consultaba 5 veces)."""
        rows = (Sucursal.objects
                .filter(Q(es_centro_distribucion=True) | Q(tipo_sucursal='CENTRO_DISTRIBUCION'))
                .values('id', 'alias', 'empresa__nombre'))
        return [{'id': r['id'], 'alias': r['alias'], 'empresa': r['empresa__nombre'] or '-'}
                for r in rows]

    @cached_property
    def sucursales_cd_ids(self):
        return {s['id'] for s in self.sucursales_cd}

    @cached_property
    def sucursales_vendedoras(self):
        """Las 10 primeras sucursales que no son CD (compartida por comparativa
        de costos y rentabilidad de vendedoras)."""
        rows = (Sucursal.objects
                .exclude(id__in=list(self.sucursales_cd_ids))
                .values('id', 'alias', 'empresa__nombre')[:10])
        return [{'id': r['id'], 'alias': r['alias'], 'empresa': r['empresa__nombre'] or '-'}
                for r in rows]


@login_required
@require_GET
def dashboard_compras_mejorado_api(request):
    """
    API completa para el dashboard de compras mejorado.
    Proporciona métricas estratégicas, gráficos y análisis para toma de decisiones.

    Filtros GET: anio, periodo (anual | trimestre | mes | semana | personalizado),
    fecha_desde / fecha_hasta (solo personalizado), temporada, proveedor.
    """
    try:
        rango = _resolver_periodo(request)
        temporada = request.GET.get('temporada', '')
        # Solo ids numéricos: 'abc' llegaba a Q(empresa_id='abc') y reventaba
        # en 500. Un valor inválido se ignora (sin filtro) y filtros_aplicados
        # informa lo que de verdad se aplicó.
        proveedor_id = (request.GET.get('proveedor') or '').strip()
        if not (proveedor_id.isascii() and proveedor_id.isdigit() and len(proveedor_id) <= 18):
            proveedor_id = ''
        datos = _DashboardComprasDatos(rango, temporada, proveedor_id,
                                       request.session.get('idEmpresaActual'))

        metricas = calcular_metricas_principales_mejorado(datos)
        evolucion_mensual = calcular_evolucion_mensual_mejorado(datos)
        pareto_proveedores = calcular_pareto_proveedores_mejorado(datos)
        comparativa_anual = calcular_comparativa_anual_mejorado(datos)
        roi_temporadas = calcular_roi_temporadas_mejorado(datos)
        top_proveedores = calcular_top_proveedores_mejorado(datos)
        top_productos = calcular_top_productos_mejorado(datos.compras_ids)

        try:
            categoria_marca = calcular_compras_por_categoria_marca(datos)
        except Exception as e:
            logger.warning('Error compras por categoria/marca: %s', e)
            categoria_marca = {'categorias': [], 'marcas': [], 'inversion_total': 0,
                               'inversion_enlazada': 0, 'inversion_sin_enlace': 0, 'pct_enlace': 0}

        # La alerta "Proveedores Críticos" cuenta sobre la lista COMPLETA; el
        # gráfico (y el JSON) muestran solo los 12 peores.
        cumplimiento_todos = calcular_cumplimiento_proveedores_mejorado(datos)
        cumplimiento_proveedores = cumplimiento_todos[:12]
        rendimiento_detallado = calcular_rendimiento_detallado_mejorado(datos)
        alertas = generar_alertas_compras_mejorado(metricas, cumplimiento_todos, roi_temporadas)
        insights = generar_insights_compras_mejorado(metricas, pareto_proveedores, comparativa_anual)

        # ===== DISTRIBUCIÓN (CENTRO DE COMPRAS) =====
        try:
            distribucion = calcular_metricas_distribucion(datos)
        except Exception as e:
            logger.warning('Error en metricas distribucion: %s', e)
            distribucion = {'unidades_compradas': 0, 'unidades_despachadas': 0,
                            'stock_centro_distribucion': 0, 'eficiencia_distribucion': 0}
        try:
            despachos_sucursal = calcular_despachos_por_sucursal(datos)
        except Exception as e:
            logger.warning('Error en despachos sucursal: %s', e)
            despachos_sucursal = []
        try:
            sucursales_destino = calcular_rendimiento_sucursales_destino(datos)
        except Exception as e:
            logger.warning('Error en rendimiento sucursales destino: %s', e)
            sucursales_destino = []
        try:
            flujo_distribucion = calcular_flujo_distribucion_mensual(datos)
        except Exception as e:
            logger.warning('Error en flujo distribucion: %s', e)
            flujo_distribucion = []

        # ===== MÁRGENES CENTRO DE DISTRIBUCIÓN =====
        try:
            margenes_cd = calcular_margenes_centro_distribucion(datos)
        except Exception as e:
            logger.warning('Error en margenes CD: %s', e)
            margenes_cd = {'margen_total_cd': 0, 'costo_proveedor_total': 0, 'costo_destino_total': 0,
                           'margen_promedio_pct': 0, 'unidades_despachadas': 0,
                           'detalle_por_sucursal': [], 'centros_distribucion': []}
        try:
            comparativa_costos = calcular_comparativa_costos_cd_vs_sucursales(datos)
        except Exception as e:
            logger.warning('Error en comparativa costos: %s', e)
            comparativa_costos = []
        try:
            rentabilidad_tipo = calcular_rentabilidad_por_tipo_sucursal(datos)
        except Exception as e:
            logger.warning('Error en rentabilidad tipo: %s', e)
            rentabilidad_tipo = {'centros_distribucion': [], 'sucursales_vendedoras': []}

        # ===== COMPRAS POR CONCEPTO (no inventariables) =====
        # Facturas de compra registradas solo como cabecera (Dte.es_por_concepto),
        # sin productos ni stock: invisibles para el modelo Compras/OC, se exponen
        # como bucket $ aparte.
        # B14-08: el flag histórico no es confiable (crearDteCompras lo marcaba en
        # TODA carga manual, también en NC y en facturas que después se
        # recepcionan), así que el KPI se blinda solo: únicamente facturas / ND
        # (no NC, guías ni cotizaciones), no anuladas/rechazadas, y SIN
        # recepciones, líneas (Dte_Productos) ni ingresos de stock. Mismo
        # criterio que el comando dte_compra_normalizar_es_por_concepto.
        try:
            concepto_qs = Dte.objects.filter(
                tipo_transaccion='COMPRA', es_por_concepto=True, descartado=False,
                fecha_emision__range=(rango['desde'], rango['hasta']),
                tipo_documento__in=('FACTURA ELECTRONICA', 'FACTURA EXENTA', 'NOTA DE DEBITO'),
                es_nota_credito=False,
            ).exclude(
                Q(estado_dte__iexact='RECHAZADO') | Q(estado_dte__iexact='ANULADO')
                | Q(estado_dte__iexact='CANCELADO')
            ).exclude(
                models.Exists(Productos_Recepcionados.objects.filter(dte=models.OuterRef('pk')))
            ).exclude(
                models.Exists(Dte_Productos.objects.filter(dte=models.OuterRef('pk')))
            ).exclude(
                models.Exists(Movimientos_Producto.objects.filter(
                    dte=models.OuterRef('pk'), cantidad__gt=0))
            )
            if datos.empresa_actual_id:
                concepto_qs = concepto_qs.filter(receptor_id=datos.empresa_actual_id)
            if proveedor_id:
                concepto_qs = concepto_qs.filter(emisor_id=proveedor_id)
            _ca = concepto_qs.aggregate(n=Count('id'), monto=Sum('monto_con_iva'))
            compras_no_inventariables = {
                'cantidad': _ca['n'] or 0,
                'monto': float(_ca['monto'] or 0),
            }
        except Exception as e:
            logger.warning('Error compras por concepto: %s', e)
            compras_no_inventariables = {'cantidad': 0, 'monto': 0}

        return JsonResponse({
            'success': True,
            'metricas': metricas,
            'compras_no_inventariables': compras_no_inventariables,
            'evolucion_mensual': evolucion_mensual,
            'pareto_proveedores': pareto_proveedores,
            'comparativa_anual': comparativa_anual,
            'roi_temporadas': roi_temporadas,
            'top_proveedores': top_proveedores,
            'top_productos': top_productos,
            'categoria_marca': categoria_marca,
            'cumplimiento_proveedores': cumplimiento_proveedores,
            'rendimiento_detallado': rendimiento_detallado,
            'alertas': alertas,
            'insights': insights,
            # Datos de distribución (Centro de Compras)
            'distribucion': distribucion,
            'despachos_sucursal': despachos_sucursal,
            'sucursales_destino': sucursales_destino,
            'flujo_distribucion': flujo_distribucion,
            # Datos de márgenes Centro de Distribución
            'margenes_cd': margenes_cd,
            'comparativa_costos': comparativa_costos,
            'rentabilidad_tipo_sucursal': rentabilidad_tipo,
            'filtros_aplicados': {
                'anio': rango['anio'],
                'periodo': rango['periodo'],
                'temporada': temporada,
                'proveedor_id': proveedor_id,
                'fecha_desde': rango['desde'].isoformat(),
                'fecha_hasta': rango['hasta'].isoformat(),
                # None cuando el rango (> 1 año) no tiene comparación válida.
                'fecha_desde_anterior': rango['desde_ant'].isoformat() if rango['comparable'] else None,
                'fecha_hasta_anterior': rango['hasta_ant'].isoformat() if rango['comparable'] else None,
                'comparable': rango['comparable'],
                'etiqueta': rango['etiqueta'],
                'etiqueta_comparacion': rango['etiqueta_comparacion'],
            }
        })

    except Exception:
        # Antes devolvía str(e) + traceback completo (rutas del servidor y
        # código) al navegador y no dejaba el detalle en el log 'app'.
        logger.exception('Error dashboard compras mejorado (GET=%s)', dict(request.GET))
        return JsonResponse({
            'success': False,
            'error': 'Error al generar dashboard',
        }, status=500)


def calcular_metricas_principales_mejorado(d):
    """Calcula las métricas principales del dashboard"""
    t = d.totales
    total_compras = len(d.compras_ids)
    unidades_esperadas = t['unidades_esperadas']
    inversion_total = t['inversion']
    valor_venta = t['valor_venta']
    unidades_recepcionadas = t['unidades_recepcionadas']

    cumplimiento_general = 0
    if unidades_esperadas > 0:
        cumplimiento_general = round((unidades_recepcionadas / unidades_esperadas) * 100, 1)

    # MARKUP DE LISTA (no es ROI): compara el precio sugerido de venta contra el
    # costo de lo ORDENADO — no de lo vendido, así que no hay retorno realizado.
    # Además `precioSugerido` es precio público CON IVA y `costo` es neto: sin
    # descontar el IVA el indicador salía ~35 puntos inflado (medido contra prod
    # 2026: mostraba 120,3% cuando el markup real es 85,2%).
    valor_venta_neto = float(valor_venta) / IVA_FACTOR_COMPRAS if valor_venta else 0
    roi_promedio = 0
    if inversion_total > 0:
        roi_promedio = round(((valor_venta_neto - float(inversion_total)) / float(inversion_total)) * 100, 1)

    # ===== TENDENCIAS vs MISMO PERÍODO AÑO ANTERIOR =====
    # Misma exclusión de eliminadas/canceladas y MISMOS filtros de temporada/
    # proveedor que el período actual (antes la base no se filtraba: comparaba
    # al proveedor elegido contra TODA la red del año anterior y la tarjeta
    # contradecía a la comparativa y al insight). Una sola consulta: número de
    # compras (DISTINCT sobre el LEFT JOIN a las líneas) y sumas de inversión
    # y valor de lista. Con un rango de más de un año no hay comparación.
    total_anterior = inversion_anterior = valor_anterior = 0
    if d.r['comparable']:
        ant = (Compras.objects
               .filter(fecha__range=(d.r['desde_ant'], d.r['hasta_ant']))
               .exclude(estado__in=ESTADOS_COMPRA_EXCLUIDOS)
               .filter(d.q_filtros)
               .aggregate(
                   n=Count('id', distinct=True),
                   inversion=Sum(F('compras_producto__compras_producto_talla__stock')
                                 * F('compras_producto__costo')),
                   valor=Sum(F('compras_producto__compras_producto_talla__stock')
                             * F('compras_producto__precioSugerido'))))
        total_anterior = ant['n'] or 0
        inversion_anterior = ant['inversion'] or 0
        valor_anterior = ant['valor'] or 0

    # None = sin base de comparación (el front muestra "sin base de
    # comparación"): antes se devolvía 0 y se leía como "0 % · estable".
    trend_compras = None
    if total_anterior > 0:
        trend_compras = round(((total_compras - total_anterior) / total_anterior) * 100, 1)

    trend_inversion = None
    if inversion_anterior > 0:
        trend_inversion = round(((float(inversion_total) - float(inversion_anterior)) / float(inversion_anterior)) * 100, 1)

    # Markup anterior con la MISMA base neta de IVA que roi_promedio (antes
    # restaba neto contra bruto: +1,3 pts cuando el cambio real era +31,1), y
    # solo si ambos lados tienen inversión (antes, sin base, el delta era el
    # markup entero: "+56,6 pts vs 2024" sin compras en 2024).
    trend_roi = None
    if inversion_anterior > 0 and inversion_total > 0:
        roi_anterior = ((float(valor_anterior) / IVA_FACTOR_COMPRAS - float(inversion_anterior))
                        / float(inversion_anterior)) * 100
        trend_roi = round(roi_promedio - roi_anterior, 1)

    # Qué mide cada cifra: OC reales vs ingresos sin OC ("Compra Manual -").
    # Los totales de arriba no cambian (suman ambos); esto solo los rotula.
    origen = {}
    for k, acc in d.desglose_origen.items():
        origen[k] = {
            'compras': acc['n'],
            'inversion': float(acc['inversion'] or 0),
            'unidades_esperadas': acc['esperadas'],
            'unidades_recepcionadas': acc['recibidas'],
            'cumplimiento': (round(acc['recibidas'] / acc['esperadas'] * 100, 1)
                             if acc['esperadas'] > 0 else None),
        }

    return {
        'total_compras': total_compras,
        'inversion_total': float(inversion_total) if inversion_total else 0,
        # `valor_venta` se sigue publicando en bruto (es el PVP de lista), pero el
        # frontend calcula el "Margen Esperado" contra la inversión NETA, así que
        # se agrega la versión sin IVA para que reste magnitudes comparables.
        'valor_venta': float(valor_venta) if valor_venta else 0,
        'valor_venta_neto': round(valor_venta_neto, 2),
        'unidades_esperadas': int(unidades_esperadas),
        'unidades_recepcionadas': int(unidades_recepcionadas),
        'cumplimiento_general': cumplimiento_general,
        'roi_promedio': roi_promedio,
        'proveedores_activos': len({c['empresa_id'] for c in d.compras}),
        'trend_compras': trend_compras,
        'trend_inversion': trend_inversion,
        'trend_roi': trend_roi,
        # {'oc': {...}, 'manual': {...}}: compras, inversion, unidades y
        # cumplimiento (None sin unidades) de cada origen.
        'origen': origen,
    }


def _etiqueta_mes(y, m, con_anio, corto=False):
    nombre = (MESES_CORTOS if corto else MESES_NOMBRES)[m - 1]
    return f'{nombre} {y}' if con_anio else nombre


def calcular_evolucion_mensual_mejorado(d):
    """Evolución mensual: inversión en compras (con filtros), número de compras
    y ventas totales de la red (tickets pagados de todas las sucursales — no
    dependen del proveedor/temporada). Un punto por mes del período."""
    meses = _meses_en_rango(d.r['desde'], d.r['hasta'])
    con_anio = len({y for y, _ in meses}) > 1
    n_mes = Counter((c['fecha'].year, c['fecha'].month) for c in d.compras)
    inv = d.inversion_mensual
    return [{
        'mes': m,
        'anio': y,
        'mes_nombre': _etiqueta_mes(y, m, con_anio),
        'inversion': float((inv.get((y, m)) or {}).get('filtrada') or 0),
        'ventas': d.ventas_por_mes.get((y, m), 0.0),
        'total_compras': n_mes.get((y, m), 0),
    } for y, m in meses]


def calcular_pareto_proveedores_mejorado(d):
    """Análisis Pareto (80/20) de proveedores por inversión.

    Devuelve TODOS los proveedores con inversión > 0, no un top-N: el frontend
    calcula el % acumulado sobre la lista recibida, así que truncarla hacía que
    el badge "N proveedores = 80%" se midiera contra el subtotal del top-15 y
    exagerara la concentración. El gráfico ya recorta a 8 barras por su cuenta."""
    por_nombre = {}
    for p in d.por_proveedor.values():
        if not p['inversion'] or p['inversion'] <= 0:
            continue
        nombre = p['nombre'] or 'Sin nombre'
        por_nombre[nombre] = por_nombre.get(nombre, 0) + p['inversion']
    return [{'proveedor': nombre, 'inversion': float(inv)}
            for nombre, inv in sorted(por_nombre.items(), key=lambda kv: (-kv[1], kv[0]))]


def calcular_comparativa_anual_mejorado(d):
    """Inversión mes a mes del período vs el mismo período un año antes, con
    los filtros temporada/proveedor. Para "anual" son los 12 meses de cada año
    (un rango en curso, como el año actual, compara solo hasta el mismo día del
    año anterior, así que la serie anterior termina en el mes en curso). Con un rango de más de un
    año no hay comparación: 'anterior' = [] (y el insight de crecimiento no
    se genera)."""
    meses = _meses_en_rango(d.r['desde'], d.r['hasta'])
    inv = d.inversion_mensual
    con_anio = len({y for y, _ in meses}) > 1
    anterior = []
    if d.r['comparable']:
        meses_ant = _meses_en_rango(d.r['desde_ant'], d.r['hasta_ant'])
        anterior = [float((inv.get(k) or {}).get('filtrada_ant') or 0) for k in meses_ant]
    return {
        'actual': [float((inv.get(k) or {}).get('filtrada') or 0) for k in meses],
        'anterior': anterior,
        'meses': [_etiqueta_mes(y, m, con_anio, corto=True) for y, m in meses],
        'etiqueta_actual': d.r['etiqueta_actual'],
        'etiqueta_anterior': d.r['etiqueta_anterior'],
        'comparable': d.r['comparable'],
    }


def calcular_roi_temporadas_mejorado(d):
    """Markup de lista por temporada, agrupando por `temporada_familia` +
    `temporada_anio` (normalizados) para permitir comparativas YoY reales
    (ej. Invierno 2025 vs Invierno 2026).

    Fallback: si la compra aún no tiene los campos normalizados (data legacy),
    se agrupa por el texto libre `temporada` como antes.

    OJO: aquí `roi` = (PVP de lista CON IVA − costo neto) / costo neto; no se
    descuenta el IVA (a diferencia del KPI "Markup de lista"), por eso sale
    ~19 puntos más alto que aquel. Se conserva así para no mover el histórico.

    Devuelve una lista de objetos:
      {
        'temporada': 'Invierno 2025',
        'temporada_familia': 'INVIERNO',
        'temporada_anio': 2025,
        'roi': 45.2,
        'inversion': 1234.0,
        'valor_venta': 1800.0,
        # Comparativa YoY (mismo rubro, año anterior)
        'roi_anterior': 38.1,
        'inversion_anterior': 1000.0,
        'delta_roi': 7.1,
        'delta_inversion_pct': 23.4,
      }
    """

    def _roi(inv, val):
        return round(((float(val) - float(inv)) / float(inv)) * 100, 1) if inv else 0

    # Una consulta agrupada por (familia, año, texto libre); en Python se separa
    # lo normalizado de lo legacy (antes: dos consultas).
    rows = (Compras_Producto_Talla.objects
            .filter(compra_producto__compras_id__in=d.compras_ids)
            .values('compra_producto__compras__temporada_familia',
                    'compra_producto__compras__temporada_anio',
                    'compra_producto__compras__temporada')
            .annotate(inv=Sum(_costo_linea()), val=Sum(_valor_linea()))
            .order_by())
    rubros, legacy = {}, {}
    for r in rows:
        familia = r['compra_producto__compras__temporada_familia']
        anio = r['compra_producto__compras__temporada_anio']
        inv, val = r['inv'] or 0, r['val'] or 0
        if familia is not None and anio is not None:
            acc = rubros.setdefault((familia, anio), [0, 0])
        elif r['compra_producto__compras__temporada'] != '':
            acc = legacy.setdefault(r['compra_producto__compras__temporada'], [0, 0])
        else:
            continue
        acc[0] += inv
        acc[1] += val

    resultado = []

    # Año anterior de cada rubro visto, también agrupado (una query)
    anteriores = {}
    if rubros:
        familias = {familia for familia, _ in rubros}
        rows_ant = (Compras_Producto_Talla.objects
                    .filter(compra_producto__compras__temporada_familia__in=familias)
                    .exclude(compra_producto__compras__estado='ELIMINADA')
                    .values('compra_producto__compras__temporada_familia',
                            'compra_producto__compras__temporada_anio')
                    .annotate(inv=Sum(_costo_linea()), val=Sum(_valor_linea()))
                    .order_by())
        anteriores = {(r['compra_producto__compras__temporada_familia'],
                       r['compra_producto__compras__temporada_anio']): r
                      for r in rows_ant}

    for (familia, anio), (inv, val) in sorted(rubros.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        inv, val = float(inv), float(val)
        ant = anteriores.get((familia, anio - 1))
        inv_ant = float(ant['inv'] or 0) if ant else 0.0
        val_ant = float(ant['val'] or 0) if ant else 0.0
        roi, roi_ant = _roi(inv, val), _roi(inv_ant, val_ant)
        resultado.append({
            'temporada': f"{FAMILIA_TEMPORADA_LEGIBLE.get(familia, familia)} {anio}",
            'temporada_familia': familia,
            'temporada_anio': anio,
            'roi': roi,
            'inversion': inv,
            'valor_venta': val,
            'roi_anterior': roi_ant,
            'inversion_anterior': inv_ant,
            'delta_roi': round(roi - roi_ant, 1),
            'delta_inversion_pct': round(((inv - inv_ant) / inv_ant) * 100, 1) if inv_ant > 0 else 0.0,
        })

    for temporada, (inv, val) in sorted(legacy.items()):
        inv, val = float(inv), float(val)
        resultado.append({
            'temporada': temporada,
            'temporada_familia': None,
            'temporada_anio': None,
            'roi': _roi(inv, val),
            'inversion': inv,
            'valor_venta': val,
            # Sin comparativo YoY confiable cuando no hay familia/año
            'roi_anterior': 0,
            'inversion_anterior': 0,
            'delta_roi': 0,
            'delta_inversion_pct': 0,
        })

    return resultado


def calcular_top_proveedores_mejorado(d):
    """Top 10 proveedores por inversión con cumplimiento (cuenta también las
    compras sin líneas)."""
    resultado = []
    for p in d.por_proveedor.values():
        if not p['inversion'] or p['inversion'] <= 0:
            continue
        esperadas, recibidas = p['esperadas'], p['recibidas']
        resultado.append({
            'proveedor': p['nombre'] or 'Sin nombre',
            'inversion': float(p['inversion']),
            'total_compras': p['n_compras'],
            'cumplimiento': round((recibidas / esperadas) * 100, 1) if esperadas > 0 else 0,
        })
    resultado.sort(key=lambda x: (-x['inversion'], x['proveedor']))
    return resultado[:10]


def calcular_top_productos_mejorado(compras_ids):
    """Calcula top 10 productos por inversión"""

    productos = Compras_Producto.objects.filter(
        compras__in=compras_ids
    ).values(
        'nombre', 'atributo1'  # atributo1 = marca
    ).annotate(
        inversion_total=Sum(F('costo') * F('compras_producto_talla__stock')),
        unidades_total=Sum('compras_producto_talla__stock')
    ).order_by('-inversion_total')[:10]

    resultado = []
    for prod in productos:
        resultado.append({
            'nombre': prod['nombre'] or 'Sin nombre',
            'marca': prod['atributo1'] or '-',
            'inversion': float(prod['inversion_total'] or 0),
            'unidades': int(prod['unidades_total'] or 0)
        })

    return resultado


def calcular_cumplimiento_proveedores_mejorado(d):
    """Cumplimiento (recibido / pedido) por proveedor, PEORES primero.

    Ordenaba descendente y cortaba en 12: con 33 de 34 proveedores en 100% el
    gráfico mostraba doce 100% y el único incumplidor jamás aparecía — y la
    alerta "proveedores bajo 70%" se evaluaba sobre esa lista truncada de los
    mejores, así que no podía dispararse nunca.

    Devuelve la lista COMPLETA: la alerta cuenta sobre todos (antes, cortada
    en 12, nunca pasaba de "12 proveedores") y el API recorta a los 12 peores
    solo para el gráfico."""
    resultado = []
    for p in d.por_proveedor.values():
        esperadas = p['esperadas']
        if esperadas <= 0:
            continue
        resultado.append({
            'proveedor': p['nombre'] or 'Sin nombre',
            'cumplimiento': round((p['recibidas'] / esperadas) * 100, 1),
            'esperadas': esperadas,
            'recibidas': p['recibidas'],
        })
    # Desempate determinista: a igual cumplimiento, primero el que más pidió.
    resultado.sort(key=lambda x: (x['cumplimiento'], -x['esperadas'], x['proveedor']))
    return resultado


def calcular_compras_por_categoria_marca(d):
    """Inversión del período por categoría v1.2 (Padre › Hija) y por MARCA
    (Producto.atributo1, FK real — no el texto libre de la OC), cruzada con
    stock actual y venta 90d para responder dónde se está sobre/sub-invirtiendo.

    Usa Compras_Producto_Talla.producto_talla → Producto; las líneas sin
    enlace a producto se reportan aparte (inversion_sin_enlace) en vez de
    desaparecer en silencio. Stock y venta 90d son "hoy": no dependen del
    período elegido."""
    compras_ids = d.compras_ids
    enlazadas = Compras_Producto_Talla.objects.filter(
        compra_producto__compras_id__in=compras_ids, producto_talla__isnull=False)

    # Cobertura del enlace (honestidad del dato): mismas sumas que los KPI.
    inv_total = float(d.totales['inversion'] or 0)
    inv_enlazada = float(d.totales['inv_enlazada'] or 0)

    # --- Inversión por categoría v1.2 (solo hijas) ---
    inv_cat = (enlazadas
               .filter(producto_talla__producto__categoria__padre__isnull=False)
               .values('producto_talla__producto__categoria__nombre',
                       'producto_talla__producto__categoria__padre__nombre')
               .annotate(inversion=Sum(_costo_linea()),
                         unidades=Sum('stock'))
               .order_by('-inversion'))
    categorias = [{
        'categoria': r['producto_talla__producto__categoria__nombre'] or 'Sin categoría',
        'padre': r['producto_talla__producto__categoria__padre__nombre'] or '',
        'inversion': float(r['inversion'] or 0),
        'unidades': int(r['unidades'] or 0),
        'participacion': round(float(r['inversion'] or 0) / inv_enlazada * 100, 1) if inv_enlazada else 0,
    } for r in inv_cat[:12]]

    # --- Inversión por marca + contexto (stock actual y venta 90d) ---
    inv_mar = (enlazadas
               .values('producto_talla__producto__atributo1__valor')
               .annotate(inversion=Sum(_costo_linea()),
                         unidades=Sum('stock'))
               .order_by('-inversion'))[:12]
    marcas_nombres = [r['producto_talla__producto__atributo1__valor'] for r in inv_mar
                      if r['producto_talla__producto__atributo1__valor']]
    # Stock actual por marca (una query)
    stock_mar = {r['producto__atributo1__valor']: int(r['st'] or 0)
                 for r in (Producto_Talla.objects
                           .filter(producto__atributo1__valor__in=marcas_nombres, stock__gt=0)
                           .exclude(producto__excluir_de_analitica=True)
                           .values('producto__atributo1__valor')
                           .annotate(st=Sum('stock')))}
    # Venta 90d por marca (una query; created_at = fecha real)
    fi90 = timezone.localdate() - timedelta(days=90)
    tickets_90 = Ticket.objects.filter(
        created_at__date__gte=fi90, estado='PAGADO').values_list('id', flat=True)
    venta_mar = {r['ProductoTalla__producto__atributo1__valor']: int(r['u'] or 0)
                 for r in (Ticket_Productos.objects
                           .filter(idTicket_id__in=tickets_90,
                                   ProductoTalla__producto__atributo1__valor__in=marcas_nombres)
                           .exclude(ProductoTalla__producto__excluir_de_analitica=True)
                           .values('ProductoTalla__producto__atributo1__valor')
                           .annotate(u=Sum('stock')))}
    marcas = []
    for r in inv_mar:
        mk = r['producto_talla__producto__atributo1__valor']
        if not mk:
            continue
        st_actual = stock_mar.get(mk, 0)
        v90 = venta_mar.get(mk, 0)
        st_pct = round(v90 / (v90 + st_actual) * 100, 1) if (v90 + st_actual) else 0.0
        marcas.append({
            'marca': mk,
            'inversion': float(r['inversion'] or 0),
            'unidades': int(r['unidades'] or 0),
            'stock_actual': st_actual,
            'venta_90d': v90,
            'sell_through_90d': st_pct,
        })

    return {
        'categorias': categorias,
        'marcas': marcas,
        'inversion_total': inv_total,
        'inversion_enlazada': inv_enlazada,
        'inversion_sin_enlace': inv_total - inv_enlazada,
        'pct_enlace': round(inv_enlazada / inv_total * 100, 1) if inv_total else 0,
    }


def calcular_rendimiento_detallado_mejorado(d):
    """Rendimiento por compra para la tabla (20 primeras del período).
    Sin consultas propias: reusa las líneas y recepciones ya agrupadas por compra."""
    resultado = []
    for compra in d.compras[:20]:
        r = d.lineas_por_compra.get(compra['id'], {})
        inversion = r.get('inversion') or 0
        valor_venta = r.get('valor') or 0
        unidades_esperadas = int(r.get('esperadas') or 0)
        unidades_recibidas = d.recibidas_por_compra.get(compra['id'], 0)

        cumplimiento = 0
        if unidades_esperadas > 0:
            cumplimiento = round((unidades_recibidas / unidades_esperadas) * 100, 1)

        roi = 0
        if inversion > 0:
            roi = round(((valor_venta - inversion) / inversion) * 100, 1)

        if cumplimiento >= 100:
            estado = 'Completado'
        elif cumplimiento >= 80:
            estado = 'Pendiente'
        else:
            estado = 'Retrasado'

        resultado.append({
            'nombre': compra['nombre'] or f"Compra #{compra['id']}",
            'proveedor': compra['empresa__nombre'] if compra['empresa_id'] else 'Sin proveedor',
            'temporada': compra['temporada'] or 'N/A',
            'inversion': float(inversion),
            'cumplimiento': cumplimiento,
            'roi': roi,
            'unidades_esperadas': unidades_esperadas,
            'unidades_recibidas': unidades_recibidas,
            'estado': estado
        })

    return resultado


def generar_alertas_compras_mejorado(metricas, cumplimiento_proveedores, roi_temporadas):
    """Genera alertas inteligentes basadas en métricas.

    `cumplimiento_proveedores` debe ser la lista COMPLETA de proveedores con
    unidades pedidas (no el top-12 del gráfico)."""

    alertas = []

    # Sin unidades pedidas no hay nada que recepcionar: antes un período vacío
    # (p. ej. "Últimos 7 días" sin OC) salía con "Cumplimiento Bajo (0%)".
    if metricas['unidades_esperadas'] > 0 and metricas['cumplimiento_general'] < 80:
        alertas.append({
            'tipo': 'warning',
            'titulo': 'Cumplimiento Bajo',
            'mensaje': f"El cumplimiento general ({metricas['cumplimiento_general']}%) está por debajo del objetivo del 80%."
        })

    proveedores_criticos = [p for p in cumplimiento_proveedores if p['cumplimiento'] < 70]
    if proveedores_criticos:
        alertas.append({
            'tipo': 'danger',
            'titulo': 'Proveedores Críticos',
            'mensaje': (f"{len(proveedores_criticos)} de {len(cumplimiento_proveedores)} "
                        f"proveedor(es) tienen cumplimiento inferior al 70%.")
        })

    temporadas_bajo_roi = [t for t in roi_temporadas if t['roi'] < 15]
    if temporadas_bajo_roi:
        temp_nombres = ', '.join([t['temporada'] for t in temporadas_bajo_roi])
        alertas.append({
            'tipo': 'warning',
            'titulo': 'Markup de lista bajo por temporada',
            'mensaje': f"Las temporadas {temp_nombres} tienen un markup de lista inferior al 15%."
        })

    diferencia = metricas['unidades_esperadas'] - metricas['unidades_recepcionadas']
    if diferencia > 0 and metricas['unidades_esperadas'] > 0:
        porcentaje_pendiente = (diferencia / metricas['unidades_esperadas']) * 100
        if porcentaje_pendiente > 20:
            alertas.append({
                'tipo': 'info',
                'titulo': 'Recepciones Pendientes',
                'mensaje': f"Faltan {diferencia:,} unidades por recepcionar ({porcentaje_pendiente:.1f}%)."
            })

    if len(alertas) == 0 and metricas['total_compras'] > 0:
        alertas.append({
            'tipo': 'success',
            'titulo': 'Excelente Desempeño',
            'mensaje': 'Todos los indicadores están dentro de los parámetros esperados.'
        })

    return alertas


def generar_insights_compras_mejorado(metricas, pareto_proveedores, comparativa_anual):
    """Genera insights estratégicos para toma de decisiones.

    Antes, si esta lista venía vacía, el JS fabricaba sus propias tarjetas en
    el navegador (ticket promedio, cumplimiento, "ROI"). Esas reglas viven
    ahora aquí, al final, con el mismo disparador (solo si no hay otro
    insight): así la pantalla muestra únicamente lo que entrega la API."""

    insights = []

    if len(pareto_proveedores) >= 2:
        total_inversion = sum(p['inversion'] for p in pareto_proveedores)
        if total_inversion > 0:
            top_2 = pareto_proveedores[:2]
            concentracion = sum(p['inversion'] for p in top_2) / total_inversion * 100

            if concentracion > 60:
                insights.append({
                    'titulo': 'Alta Concentración',
                    'descripcion': f'Los 2 principales proveedores representan {concentracion:.1f}% de la inversión.',
                    'valor': f'{concentracion:.0f}%',
                    'icono': 'bi-building',
                    'color': 'warning'
                })

    total_actual = sum(comparativa_anual.get('actual', []))
    total_anterior = sum(comparativa_anual.get('anterior', []))

    if total_anterior > 0:
        crecimiento = ((total_actual - total_anterior) / total_anterior) * 100

        if crecimiento > 10:
            insights.append({
                'titulo': 'Crecimiento Positivo',
                'descripcion': 'La inversión en compras ha aumentado respecto al mismo período del año anterior.',
                'valor': f'+{crecimiento:.1f}%',
                'icono': 'bi-graph-up-arrow',
                'color': 'success'
            })
        elif crecimiento < -10:
            insights.append({
                'titulo': 'Reducción de Inversión',
                'descripcion': 'La inversión en compras ha disminuido respecto al mismo período del año anterior.',
                'valor': f'{crecimiento:.1f}%',
                'icono': 'bi-graph-down-arrow',
                'color': 'danger'
            })

    if metricas['roi_promedio'] >= 25:
        insights.append({
            'titulo': 'Markup de lista alto',
            'descripcion': 'El precio de lista (neto) supera el costo de lo ordenado en más de 25%. Es teórico: no mide venta real.',
            'valor': f"{metricas['roi_promedio']}%",
            'icono': 'bi-trophy',
            'color': 'success'
        })

    # Reglas de respaldo (ex-JS): solo cuando no se generó ningún insight.
    if not insights:
        # Mismo guard que la alerta: sin unidades pedidas no hay recepción que mejorar.
        if metricas['unidades_esperadas'] > 0 and metricas['cumplimiento_general'] < 80:
            insights.append({
                'titulo': 'Mejorar Cumplimiento',
                'descripcion': f"El cumplimiento actual ({metricas['cumplimiento_general']}%) está por debajo del objetivo. Revisar procesos de recepción.",
                'valor': f"{metricas['cumplimiento_general']}%",
                'icono': 'bi-clipboard-check',
                'color': 'warning'
            })
        if metricas['roi_promedio'] > 20:
            insights.append({
                'titulo': 'Markup de lista alto',
                'descripcion': 'El precio de lista (neto) supera el costo de lo ordenado en más de 20%. Es teórico: no mide venta real.',
                'valor': f"{metricas['roi_promedio']}%",
                'icono': 'bi-graph-up-arrow',
                'color': 'success'
            })
        if metricas['total_compras'] > 0:
            promedio = metricas['inversion_total'] / metricas['total_compras']
            insights.append({
                'titulo': 'Inversión promedio por compra',
                'descripcion': 'Inversión total dividida por el número de compras del período (OC e ingresos manuales sin OC).',
                'valor': f"${round(promedio):,}".replace(',', '.'),
                'icono': 'bi-receipt',
                'color': 'info'
            })

    return insights


# ========== FUNCIONES DE DISTRIBUCIÓN (CENTRO DE COMPRAS) ==========

def calcular_metricas_distribucion(d):
    """
    Métricas de distribución desde el centro de compras hacia sucursales vendedoras.
    Analiza el flujo: Compras → Recepciones → Despachos → Ventas
    """
    # Unidades compradas (recepcionadas de proveedores): misma suma que el KPI.
    unidades_compradas = d.totales['unidades_recepcionadas']

    # Unidades despachadas DESDE un centro de distribución (pierna de salida).
    # `cantidad` es NEGATIVA en los egresos (por eso se suma con ABS) y sin
    # filtrar el origen se contaban también los traspasos tienda↔tienda.
    unidades_despachadas = sum(int(t['unidades'] or 0) for t in d.salidas(solo_cd=True))

    # Fallback a Traspasos si el kardex no tiene la pierna de salida
    if not unidades_despachadas:
        unidades_despachadas = Traspaso_Detalle.objects.filter(
            traspaso__fecha_solicitud__range=(d.r['desde'], d.r['hasta']),
            traspaso__estado__in=['EN_TRANSITO', 'RECIBIDO']
        ).aggregate(total=Sum('cantidad_enviada'))['total'] or 0

    # Stock REAL hoy en los centros de distribución (no depende del período).
    stock_centro = Producto_Talla.objects.filter(
        Q(producto__sucursal__es_centro_distribucion=True) |
        Q(producto__sucursal__tipo_sucursal='CENTRO_DISTRIBUCION'),
        stock__gt=0,
    ).aggregate(total=Sum('stock'))['total'] or 0

    # OJO de lectura: compara el flujo de salida del período contra las compras
    # del período, así que puede superar el 100% cuando se despacha stock
    # comprado antes.
    eficiencia = 0
    if unidades_compradas > 0:
        eficiencia = round((unidades_despachadas / unidades_compradas) * 100, 1)

    return {
        'unidades_compradas': int(unidades_compradas),
        'unidades_despachadas': int(unidades_despachadas),
        'stock_centro_distribucion': int(stock_centro),
        'eficiencia_distribucion': eficiencia
    }


def calcular_despachos_por_sucursal(d):
    """Despachos (unidades) a cada sucursal destino, top 10."""
    por_destino = {}
    for t in d.salidas():
        if t['sucursal_destino_id'] is None:
            continue
        acc = por_destino.setdefault(t['sucursal_destino_id'], {
            'sucursal_id': t['sucursal_destino_id'],
            'sucursal': t['sucursal_destino__alias'] or 'Sin nombre',
            'empresa': t['sucursal_destino__empresa__nombre'] or '-',
            'unidades': 0,
        })
        acc['unidades'] += int(t['unidades'] or 0)
    resultado = sorted(por_destino.values(), key=lambda x: (-x['unidades'], x['sucursal']))

    # Si no hay datos en movimientos, intentar con Traspasos
    if not resultado:
        traspasos = Traspaso.objects.filter(
            fecha_solicitud__range=(d.r['desde'], d.r['hasta']),
            estado__in=['EN_TRANSITO', 'RECIBIDO']
        ).values(
            'sucursal_destino__id',
            'sucursal_destino__alias',
            'sucursal_destino__empresa__nombre'
        ).annotate(
            unidades=Sum('detalles__cantidad_enviada')
        ).order_by('-unidades')

        for t in traspasos:
            if t['unidades']:
                resultado.append({
                    'sucursal_id': t['sucursal_destino__id'],
                    'sucursal': t['sucursal_destino__alias'] or 'Sin nombre',
                    'empresa': t['sucursal_destino__empresa__nombre'] or '-',
                    'unidades': int(t['unidades'] or 0)
                })

    return resultado[:10]


def calcular_rendimiento_sucursales_destino(d):
    """
    Rendimiento de cada sucursal destino en el período:
    - Unidades recibidas por traspaso (kardex, pierna de entrada)
    - Unidades vendidas y ventas en dinero
    - Costo FIFO de lo vendido (0 en líneas legacy: ahí no hay margen, es dato real, no inventado)
    """
    por_destino = {}
    for t in d.traspasos_kardex:
        if t['concepto'] != CONCEPTO_ENTRADA or t['sucursal_destino_id'] is None:
            continue
        acc = por_destino.setdefault(t['sucursal_destino_id'], {
            'sucursal_id': t['sucursal_destino_id'],
            'sucursal': t['sucursal_destino__alias'] or 'Sin nombre',
            'empresa': t['sucursal_destino__empresa__nombre'] or '-',
            'despachado': 0,
        })
        acc['despachado'] += int(t['cant_signo'] or 0)
    if not por_destino:
        return []

    resultado = []
    for suc_id, acc in por_destino.items():
        lin = d.lineas_venta_por_sucursal.get(suc_id, {})
        acc['vendido'] = int(lin.get('unidades') or 0)
        acc['ventas_monto'] = d.ventas_por_sucursal.get(suc_id, 0.0)
        acc['costo'] = float(lin.get('costo') or 0)
        resultado.append(acc)

    # Ordenar por ventas (desempate: más despachado primero)
    resultado.sort(key=lambda x: (-x['ventas_monto'], -x['despachado'], x['sucursal']))
    return resultado[:15]


def calcular_flujo_distribucion_mensual(d):
    """
    Flujo mensual del período: Compras → Despachos → Ventas.
    La inversión aquí NO se filtra por temporada/proveedor (despachos y ventas
    tampoco pueden filtrarse así); la de "Evolución mensual" sí.
    """
    meses = _meses_en_rango(d.r['desde'], d.r['hasta'])
    con_anio = len({y for y, _ in meses}) > 1
    inv = d.inversion_mensual

    # Despachos ($ costo) por mes: suma con signo (los egresos son negativos) y abs.
    desp_mes = {}
    for t in d.salidas():
        k = (t['y'], t['m'])
        desp_mes[k] = desp_mes.get(k, 0) + (t['valor_signo'] or 0)

    return [{
        'mes': m,
        'anio': y,
        'mes_nombre': _etiqueta_mes(y, m, con_anio),
        'inversion': float((inv.get((y, m)) or {}).get('total') or 0),
        'despachos': abs(float(desp_mes.get((y, m), 0))),
        'ventas': d.ventas_por_mes.get((y, m), 0.0),
    } for y, m in meses]


# ========== FUNCIONES DE ANÁLISIS DE MÁRGENES CENTRO DE DISTRIBUCIÓN ==========

def calcular_margenes_centro_distribucion(d):
    """
    Márgenes que aplica el Centro de Distribución (EDEL, GILD) al despachar
    productos a sucursales vendedoras en el período.

    El costo para las sucursales vendedoras = Costo proveedor + Sobreprecio CD

    Retorna:
    - Margen bruto del CD (sobreprecio total)
    - Margen % promedio aplicado
    - Desglose por sucursal destino
    """
    # El criterio de "centro de distribución" es el MISMO que usan la comparativa
    # de costos y la rentabilidad por tipo (d.sucursales_cd). Antes había aquí un
    # `except:` desnudo que caía a `empresa__esProveedor=True`: como las 4
    # empresas del holding están marcadas esProveedor, ese fallback clasificaba
    # las 13 sucursales (incluidas las tiendas) como centros de distribución.
    # Si no hay ninguna marcada, lo correcto es devolver vacío y que se vea.
    sucursales_cd = d.sucursales_cd
    if not sucursales_cd:
        logger.warning(
            "margenes_cd: ninguna Sucursal marcada como centro de distribución; "
            "el panel de márgenes CD quedará vacío"
        )
    sucursales_cd_ids = list(d.sucursales_cd_ids)

    margen_total_cd = 0
    costo_proveedor_total = 0
    unidades_total = 0
    detalle_por_sucursal = {}

    def _acumular(suc_id, alias, empresa, unidades, costo_prov, margen, costo_dest):
        nonlocal margen_total_cd, costo_proveedor_total, unidades_total
        margen_total_cd += margen
        costo_proveedor_total += costo_prov
        unidades_total += unidades
        if suc_id is None:
            return
        det = detalle_por_sucursal.setdefault(suc_id, {
            'sucursal_id': suc_id, 'sucursal': alias, 'empresa': empresa or '-',
            'unidades': 0, 'costo_proveedor_total': 0,
            'sobreprecio_total': 0, 'costo_destino_total': 0,
        })
        det['unidades'] += unidades
        det['costo_proveedor_total'] += costo_prov
        det['sobreprecio_total'] += margen
        det['costo_destino_total'] += costo_dest

    # 1. Desde Traspasos (sobreprecio/costo/costo_destino), agrupado por destino
    # en UNA query. costo_destino==0/null cae a costo+sobreprecio.
    _costo_destino_expr = models.Case(
        models.When(Q(costo_destino__isnull=True) | Q(costo_destino=0),
                    then=(F('costo') + F('sobreprecio')) * Coalesce(F('cantidad_enviada'), 0)),
        default=F('costo_destino') * Coalesce(F('cantidad_enviada'), 0),
        output_field=models.BigIntegerField(),
    )
    traspaso_rows = (Traspaso_Detalle.objects.filter(
        traspaso__fecha_solicitud__range=(d.r['desde'], d.r['hasta']),
        traspaso__sucursal_origen__in=sucursales_cd_ids,
        traspaso__estado__in=['EN_TRANSITO', 'RECIBIDO'])
        .values('traspaso__sucursal_destino_id',
                'traspaso__sucursal_destino__alias',
                'traspaso__sucursal_destino__empresa__nombre')
        .annotate(unidades=Sum(Coalesce(F('cantidad_enviada'), 0)),
                  costo_prov=Sum(F('costo') * Coalesce(F('cantidad_enviada'), 0)),
                  margen=Sum(F('sobreprecio') * Coalesce(F('cantidad_enviada'), 0)),
                  costo_dest=Sum(_costo_destino_expr))
        .order_by())
    for r in traspaso_rows:
        _acumular(r['traspaso__sucursal_destino_id'],
                  r['traspaso__sucursal_destino__alias'],
                  r['traspaso__sucursal_destino__empresa__nombre'],
                  int(r['unidades'] or 0), r['costo_prov'] or 0,
                  r['margen'] or 0, r['costo_dest'] or 0)

    # 2. Si no hay datos en Traspasos, usar el kardex (salidas desde un CD), que
    # ya está agrupado en memoria: costo destino = costo + sobreprecio.
    if unidades_total == 0:
        detalle_por_sucursal.clear()
        margen_total_cd = costo_proveedor_total = 0
        for t in d.salidas(solo_cd=True):
            costo_prov = t['costo_total'] or 0
            margen = t['sobreprecio_total'] or 0
            _acumular(t['sucursal_destino_id'], t['sucursal_destino__alias'],
                      t['sucursal_destino__empresa__nombre'],
                      int(t['unidades'] or 0), costo_prov, margen, costo_prov + margen)

    # Calcular margen promedio %
    margen_promedio_pct = 0
    if costo_proveedor_total > 0:
        margen_promedio_pct = round((margen_total_cd / costo_proveedor_total) * 100, 2)

    # Ordenar detalle por unidades (desempate por alias, determinista)
    detalle_lista = sorted(detalle_por_sucursal.values(),
                           key=lambda x: (-x['unidades'], x['sucursal'] or ''))

    # Calcular margen % para cada sucursal
    for item in detalle_lista:
        if item['costo_proveedor_total'] > 0:
            item['margen_pct'] = round((item['sobreprecio_total'] / item['costo_proveedor_total']) * 100, 2)
        else:
            item['margen_pct'] = 0

    return {
        'centros_distribucion': [{'id': s['id'], 'alias': s['alias']} for s in sucursales_cd],
        'margen_total_cd': float(margen_total_cd),
        'costo_proveedor_total': float(costo_proveedor_total),
        'costo_destino_total': float(costo_proveedor_total + margen_total_cd),
        'margen_promedio_pct': margen_promedio_pct,
        'unidades_despachadas': int(unidades_total),
        'detalle_por_sucursal': detalle_lista[:10]
    }


def calcular_comparativa_costos_cd_vs_sucursales(d):
    """
    Costo promedio de los lotes ingresados en el período en las sucursales
    vendedoras: costo proveedor (lo que paga EDEL/GILD) vs sobreprecio interno
    (lo que pagan de más por pasar por el CD).
    """
    sucursales_vendedoras = d.sucursales_vendedoras

    # La sucursal del lote es la del producto: producto_talla → producto → sucursal.
    ini, fin = _rango_datetime(d.r['desde'], d.r['hasta'])
    lotes_map = {r['producto_talla__producto__sucursal_id']: r for r in (
        LoteProducto.objects.filter(
            producto_talla__producto__sucursal_id__in=[s['id'] for s in sucursales_vendedoras],
            fecha_ingreso__gte=ini, fecha_ingreso__lt=fin)
        .values('producto_talla__producto__sucursal_id')
        .annotate(costo=Sum(F('costo_unitario') * F('cantidad_inicial')),
                  sobreprecio=Sum(Coalesce(F('sobreprecio_unitario'), 0) * F('cantidad_inicial')),
                  unidades=Sum('cantidad_inicial'))
        .order_by())}

    comparativa = []
    for suc in sucursales_vendedoras:
        r = lotes_map.get(suc['id'], {})
        total_costo = r.get('costo') or 0
        total_sobreprecio = r.get('sobreprecio') or 0
        total_unidades = r.get('unidades') or 0

        costo_promedio = 0
        sobreprecio_promedio = 0
        if total_unidades > 0:
            costo_promedio = round(total_costo / total_unidades)
            sobreprecio_promedio = round(total_sobreprecio / total_unidades)

        comparativa.append({
            'sucursal_id': suc['id'],
            'sucursal': suc['alias'],
            'empresa': suc['empresa'],
            'unidades_recibidas': int(total_unidades),
            'costo_promedio': costo_promedio,
            'sobreprecio_promedio': sobreprecio_promedio,
            'costo_total_promedio': costo_promedio + sobreprecio_promedio,
            'incremento_pct': round((sobreprecio_promedio / costo_promedio * 100), 2) if costo_promedio > 0 else 0
        })

    return sorted(comparativa, key=lambda x: -x['unidades_recibidas'])


def calcular_rentabilidad_por_tipo_sucursal(d):
    """
    Rentabilidad diferenciada en el período:
    - CD: compra a proveedor → despacha a sucursales con sobreprecio
    - Sucursales vendedoras: reciben del CD → venden a cliente final
    """
    resultado = {
        'centros_distribucion': [],
        'sucursales_vendedoras': []
    }
    sucursales_cd = d.sucursales_cd
    sucursales_cd_ids = list(d.sucursales_cd_ids)

    # Inversión de compras atribuible a cada CD: lo recepcionado en esa sucursal
    # (recepción → producto → sucursal), valorizado al costo de la OC.
    inversion_map = {r['s']: float(r['inv'] or 0) for r in (
        Productos_Recepcionados.objects.filter(
            producto_talla__producto__sucursal_id__in=sucursales_cd_ids,
            compra_producto_talla__compra_producto__compras__fecha__range=(d.r['desde'], d.r['hasta']))
        .exclude(compra_producto_talla__compra_producto__compras__estado__in=ESTADOS_COMPRA_EXCLUIDOS)
        .annotate(s=F('producto_talla__producto__sucursal_id'))
        .values('s')
        .annotate(inv=Sum(F('stockArribado') * F('compra_producto_talla__compra_producto__costo')))
        .order_by())}

    # Despachos por CD (kardex ya agrupado, sumado por sucursal origen)
    despachos_map = {}
    for t in d.salidas(solo_cd=True):
        acc = despachos_map.setdefault(t['sucursal_origen_id'], {'total_sobreprecio': 0, 'total_costo': 0})
        acc['total_sobreprecio'] += t['sobreprecio_total'] or 0
        acc['total_costo'] += t['costo_total'] or 0

    for suc_cd in sucursales_cd:
        dm = despachos_map.get(suc_cd['id'], {})
        sobreprecio_generado = dm.get('total_sobreprecio') or 0
        costo_despachado = dm.get('total_costo') or 0

        rentabilidad_cd = 0
        if costo_despachado > 0:
            rentabilidad_cd = round((sobreprecio_generado / costo_despachado) * 100, 2)

        resultado['centros_distribucion'].append({
            'sucursal_id': suc_cd['id'],
            'sucursal': suc_cd['alias'],
            'empresa': suc_cd['empresa'],
            'inversion_proveedores': inversion_map.get(suc_cd['id'], 0.0),
            'costo_despachado': float(costo_despachado),
            'sobreprecio_generado': float(sobreprecio_generado),
            'rentabilidad_pct': rentabilidad_cd
        })

    # Sucursales vendedoras: ventas y costo FIFO de lo vendido (compartidos).
    for suc_vend in d.sucursales_vendedoras:
        total_ventas = d.ventas_por_sucursal.get(suc_vend['id'], 0.0)
        costo_ventas = float((d.lineas_venta_por_sucursal.get(suc_vend['id']) or {}).get('costo') or 0)
        # Sin costo FIFO no hay margen: `costo_fifo` viene en 0 en todas las
        # líneas de venta, y restar cero daba "100% de rentabilidad" en cada
        # sucursal. Se marca como sin dato en vez de publicar un margen falso.
        costo_disponible = costo_ventas > 0
        margen_bruto = (total_ventas - costo_ventas) if costo_disponible else 0
        rentabilidad_vend = 0
        if costo_disponible and total_ventas > 0:
            rentabilidad_vend = round((margen_bruto / total_ventas) * 100, 2)

        resultado['sucursales_vendedoras'].append({
            'sucursal_id': suc_vend['id'],
            'sucursal': suc_vend['alias'],
            'empresa': suc_vend['empresa'],
            'ventas_total': total_ventas,
            'costo_ventas': costo_ventas,
            'costo_disponible': costo_disponible,
            'margen_bruto': margen_bruto,
            'rentabilidad_pct': rentabilidad_vend
        })

    return resultado


# ========== COMPENSACIÓN FACTURA-CONTRA-FACTURA ("Pagar con Factura") ==========
#
# Cuando ya no se puede emitir/asociar una Nota de Crédito a una factura de compra
# antigua, el proveedor puede pedir saldarla asociándole OTRA factura del mismo
# proveedor como instrumento de pago. Esto es una compensación de tesorería / neteo
# de cuentas por pagar, NO una relación tributaria SII: ambas facturas siguen siendo
# documentos tributarios válidos. Se registra en el libro de pagos (Dte_Detalle_Pago)
# con metodo_pago=METODO_COMPENSACION, igual que una Nota de Crédito.
# Espeja el patrón de obtener_ncs_disponibles / asociar_nc_existente / desasociar_nc.
#
# Alcance (auditoría 2026-09, B3-01 / B5-01 / B13-01): la factura objetivo y la
# factura instrumento deben ser DTE de COMPRA de la empresa en sesión o sin
# receptor (el mismo alcance de la grilla, cargarDteCompra y registrarPagoDTE:
# views._dtes_compra_alcance). Fuera de ese alcance se responde 404, igual que
# si no existiera. Antes cualquier usuario con la pantalla compensaba o
# revertía por id facturas de otra empresa del grupo.

_ESTADOS_DTE_SIN_EFECTO = ('RECHAZADO', 'ANULADO', 'CANCELADO')


def _alcance_compras_sesion(request):
    """QuerySet de DTE de COMPRA visibles para la empresa en sesión (receptor =
    empresa o NULL); None si la sesión no trae empresa."""
    from app.views import _dtes_compra_alcance
    return _dtes_compra_alcance(request)


def _sin_empresa_en_sesion():
    return JsonResponse({'success': False, 'error': 'Empresa no identificada en sesión'}, status=403)


def _id_entero(valor):
    """int positivo de un id recibido del cliente, o None ('abc' reventaba en 500)."""
    try:
        valor = int(valor)
    except (TypeError, ValueError):
        return None
    return valor if 0 < valor <= 9223372036854775807 else None


def _motivo_documento_sin_efecto(dte, rol):
    """Mensaje si `dte` no puede participar en una compensación (descartado,
    anulado / rechazado / cancelado, o nota de crédito); None si puede."""
    if dte.descartado:
        return f'La {rol} está descartada.'
    if (dte.estado_dte or '').strip().upper() in _ESTADOS_DTE_SIN_EFECTO:
        return f'No se puede compensar: la {rol} está anulada/rechazada.'
    if dte.es_nota_credito or dte.tipo_documento == 'NOTA DE CREDITO':
        return f'La {rol} no puede ser una Nota de Crédito.'
    return None


def _ids_fichas_proveedor(proveedor):
    """IDs de todas las fichas Empresa con el RUT del proveedor, sin mirar
    puntos, espacios ni guion ('77300003-7' = '77.300.003-7' = '773000037'),
    más la propia ficha."""
    if proveedor is None:
        return []
    return list(set(_empresas_por_rut(proveedor.rut).values_list('id', flat=True)) | {proveedor.id})


def _q_instrumento_ya_usado(instrumento_folio, ids_proveedor):
    """Pagos 'Compensación con Factura' cuyo voucher es el folio del
    instrumento, del mismo proveedor (por RUT). El folio solo identifica la
    factura dentro de su emisor: antes el control era global y una factura del
    proveedor A con folio 123 bloqueaba la 123 del proveedor B."""
    return Q(
        metodo_pago=METODO_COMPENSACION,
        voucher=str(instrumento_folio),
        dte__emisor_id__in=list(ids_proveedor),
    )


@login_required
@require_GET
def obtener_facturas_compensar_disponibles(request):
    """
    Lista facturas de compra del mismo proveedor que pueden usarse como instrumento
    de compensación contra una factura objetivo. Excluye la propia factura objetivo,
    las ya usadas como instrumento de compensación y las que no tienen saldo propio
    disponible. Espeja obtener_ncs_disponibles (views.py).
    Objetivo e instrumentos: solo del alcance de la empresa en sesión, no
    descartados ni anulados/rechazados.
    """
    try:
        alcance = _alcance_compras_sesion(request)
        if alcance is None:
            return _sin_empresa_en_sesion()

        dte_id = _id_entero(request.GET.get('dte_id'))
        if not dte_id:
            return JsonResponse({'success': False, 'error': 'ID de factura objetivo requerido'}, status=400)

        try:
            objetivo = alcance.select_related('emisor').get(id=dte_id)
        except Dte.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Factura objetivo no encontrada'}, status=404)

        busqueda = (request.GET.get('busqueda', '') or '').strip()
        try:
            limit = max(1, min(int(request.GET.get('limit', 200)), 500))
        except (TypeError, ValueError):
            limit = 200

        candidatos = alcance.filter(
            tipo_documento='FACTURA ELECTRONICA',
            emisor=objetivo.emisor,           # mismo proveedor
            descartado=False,
            es_nota_credito=False,
        ).exclude(id=objetivo.id).exclude(
            Q(estado_dte__iexact='RECHAZADO') | Q(estado_dte__iexact='ANULADO')
            | Q(estado_dte__iexact='CANCELADO')
        )

        if busqueda:
            candidatos = candidatos.filter(numero_documento__icontains=busqueda)

        # Excluir facturas ya usadas como instrumento de compensación: mismo
        # criterio que el control de asociar_factura_compensacion (folio del
        # instrumento dentro del mismo proveedor por RUT).
        ids_proveedor = _ids_fichas_proveedor(objetivo.emisor)
        numeros_usados = set(
            Dte_Detalle_Pago.objects.filter(
                metodo_pago=METODO_COMPENSACION,
                dte__emisor_id__in=ids_proveedor,
            ).exclude(voucher__isnull=True)
             .values_list('voucher', flat=True)
        )
        numeros_usados = {int(v) for v in numeros_usados if str(v).strip().isdigit()
                          and int(v) <= _LIMITE_ENTERO}
        if numeros_usados:
            candidatos = candidatos.exclude(numero_documento__in=numeros_usados)

        candidatos = list(
            candidatos.select_related('emisor').order_by('-fecha_emision')[:limit]
        )

        # Saldo propio disponible de cada candidato (monto - sus propios pagos),
        # en una sola consulta. Sólo se ofrecen facturas con saldo > 0.
        ids = [c.id for c in candidatos]
        pagos_map = dict(
            Dte_Detalle_Pago.objects.filter(dte_id__in=ids)
                .values_list('dte_id')
                .annotate(total=Sum('monto'))
                .values_list('dte_id', 'total')
        )

        resultado = []
        for c in candidatos:
            pagado = float(pagos_map.get(c.id, 0) or 0)
            saldo_disponible = float(c.monto_con_iva or 0) - pagado
            if saldo_disponible <= 0:
                continue
            resultado.append({
                'id': c.id,
                'numero_documento': c.numero_documento,
                'proveedor': c.emisor.nombre if c.emisor else 'N/A',
                'fecha_emision': c.fecha_emision.strftime('%Y-%m-%d') if c.fecha_emision else '',
                'monto_con_iva': float(c.monto_con_iva or 0),
                'saldo_disponible': saldo_disponible,
                'estado': c.estado_dte,
            })

        return JsonResponse({'success': True, 'facturas': resultado})
    except Exception:
        logger.exception('Compensación: error en %s', request.path)
        return JsonResponse({
            'success': False,
            'error': 'No se pudo completar la operación. Reintenta; si persiste, avisa a soporte.',
        }, status=500)


@login_required
@require_POST
def asociar_factura_compensacion(request):
    """
    Asocia una factura existente como instrumento de compensación (pago no en
    efectivo) sobre una factura objetivo. Espeja asociar_nc_existente, pero el
    instrumento es una factura del mismo proveedor y el monto es editable/parcial.

    NOTA TRIBUTARIA: compensación de tesorería / neteo de cuentas por pagar, NO una
    relación tributaria SII. Ambas facturas siguen siendo documentos tributarios
    válidos; sólo se registra en el libro de pagos (Dte_Detalle_Pago).

    Concurrencia (B3-07): todas las guardas (mismo proveedor, incidencias,
    instrumento ya usado, saldos) corren DENTRO del atomic con ambas facturas
    bloqueadas (select_for_update, en orden de id para no cruzar bloqueos),
    como la variante con factura emitida. Antes dos envíos simultáneos pasaban
    el control de "ya usada" y el tope de saldo a la vez.
    Alcance: objetivo e instrumento deben ser de la empresa en sesión (o sin
    receptor); si no, 404.
    """
    import math

    try:
        alcance = _alcance_compras_sesion(request)
        if alcance is None:
            return _sin_empresa_en_sesion()

        try:
            data = json.loads(request.body)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Datos inválidos'}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({'success': False, 'error': 'Datos inválidos'}, status=400)
        dte_id = _id_entero(data.get('dte_id'))
        instrumento_id = _id_entero(data.get('factura_compensadora_id'))
        monto_pedido = data.get('monto')

        if not dte_id or not instrumento_id:
            return JsonResponse({'success': False, 'error': 'Datos incompletos'}, status=400)

        # Guard: no auto-compensar
        if instrumento_id == dte_id:
            return JsonResponse({'success': False, 'error': 'Una factura no puede compensarse a sí misma'}, status=400)

        with transaction.atomic():
            # of=('self',): select_related sobre FK nullable (receptor) haría un
            # LEFT JOIN y PostgreSQL rechaza FOR UPDATE sobre ese lado.
            bloqueados = {
                d.id: d for d in alcance.select_for_update(of=('self',))
                .select_related('emisor')
                .filter(id__in=[dte_id, instrumento_id])
                .order_by('id')
            }
            objetivo = bloqueados.get(dte_id)
            instrumento = bloqueados.get(instrumento_id)
            if (objetivo is None or instrumento is None
                    or instrumento.tipo_documento != 'FACTURA ELECTRONICA'):
                return JsonResponse({'success': False, 'error': 'Documento no encontrado'}, status=404)

            for dte, rol in ((objetivo, 'factura objetivo'), (instrumento, 'factura de compensación')):
                motivo = _motivo_documento_sin_efecto(dte, rol)
                if motivo:
                    return JsonResponse({'success': False, 'error': motivo}, status=400)

            # Guard: mismo proveedor
            if instrumento.emisor_id != objetivo.emisor_id:
                return JsonResponse({'success': False, 'error': 'La factura de compensación debe ser del mismo proveedor'}, status=400)

            # Guard: incidencias pendientes en la factura objetivo (espeja registrarPagoDTE)
            if Dte_Incidencia.objects.filter(dte=objetivo, estado__in=['PENDIENTE', 'EN_GESTION']).exists():
                return JsonResponse({'success': False, 'error': 'No se puede compensar mientras existan incidencias pendientes o en gestión para esta factura.'}, status=400)

            # Guard: el instrumento no debe estar ya usado como compensación
            # (espeja NC ya_asociada). El folio identifica la factura dentro de
            # su proveedor (por RUT), no en todo el padrón.
            ids_proveedor = _ids_fichas_proveedor(instrumento.emisor)
            if Dte_Detalle_Pago.objects.filter(
                _q_instrumento_ya_usado(instrumento.numero_documento, ids_proveedor)
            ).exists():
                return JsonResponse({'success': False, 'error': 'Esta factura ya fue usada como compensación en otro documento'}, status=400)

            # Saldos reales (monto - pagos) de ambas facturas, bajo el bloqueo
            pagos_objetivo = Dte_Detalle_Pago.objects.filter(dte=objetivo).aggregate(total=Sum('monto'))['total'] or 0
            saldo_objetivo = float(objetivo.monto_con_iva or 0) - float(pagos_objetivo)

            pagos_instrumento = Dte_Detalle_Pago.objects.filter(dte=instrumento).aggregate(total=Sum('monto'))['total'] or 0
            saldo_instrumento = float(instrumento.monto_con_iva or 0) - float(pagos_instrumento)

            if saldo_objetivo <= 0:
                return JsonResponse({'success': False, 'error': 'La factura objetivo no tiene saldo pendiente'}, status=400)
            if saldo_instrumento <= 0:
                return JsonResponse({'success': False, 'error': 'La factura de compensación no tiene saldo disponible'}, status=400)

            # Monto a aplicar: editable, acotado por el saldo del objetivo y del instrumento.
            try:
                monto_default = min(saldo_objetivo, saldo_instrumento)
                monto_aplicado = float(monto_pedido) if monto_pedido not in (None, '') else monto_default
            except (TypeError, ValueError):
                return JsonResponse({'success': False, 'error': 'Monto inválido'}, status=400)
            if not math.isfinite(monto_aplicado):
                return JsonResponse({'success': False, 'error': 'Monto inválido'}, status=400)

            # monto es IntegerField -> redondear; nunca exceder ninguno de los dos saldos.
            monto_aplicado = int(round(min(monto_aplicado, saldo_objetivo, saldo_instrumento)))
            if monto_aplicado <= 0:
                return JsonResponse({'success': False, 'error': 'El monto a compensar debe ser mayor a cero'}, status=400)

            Dte_Detalle_Pago.objects.create(
                dte=objetivo,
                metodo_pago=METODO_COMPENSACION,
                voucher=str(instrumento.numero_documento),
                monto=monto_aplicado,
                notas=f'Compensación con Factura #{instrumento.numero_documento} - {instrumento.emisor.nombre if instrumento.emisor else "N/A"}',
                fecha_pago=timezone.localdate(),
            )

            _recalcular_estado_pago(objetivo)

        return JsonResponse({
            'success': True,
            'message': 'Factura asociada como compensación correctamente',
            'monto_aplicado': monto_aplicado,
        })
    except Exception:
        logger.exception('Compensación: error en %s', request.path)
        return JsonResponse({
            'success': False,
            'error': 'No se pudo completar la operación. Reintenta; si persiste, avisa a soporte.',
        }, status=500)


@login_required
@require_POST
def desasociar_factura_compensacion(request, pago_id):
    """
    Revierte una compensación: elimina la fila de Dte_Detalle_Pago y recalcula el
    estado de pago de la factura objetivo. Espeja desasociar_nc, pero por id de fila
    (más preciso que el lookup por voucher).
    Solo sobre facturas del alcance de la empresa en sesión (si no, 404); la
    factura objetivo se bloquea antes de borrar y recalcular.
    """
    return _desasociar_compensacion(request, pago_id, METODO_COMPENSACION)


def _desasociar_compensacion(request, pago_id, metodo):
    """Borra la fila de compensación `pago_id` (del método `metodo`) y
    recalcula el estado de pago de su factura objetivo, con la factura
    bloqueada y solo dentro del alcance de la empresa en sesión."""
    try:
        alcance = _alcance_compras_sesion(request)
        if alcance is None:
            return _sin_empresa_en_sesion()

        no_encontrada = JsonResponse({'success': False, 'error': 'Compensación no encontrada'}, status=404)
        dte_id = (
            Dte_Detalle_Pago.objects.filter(id=pago_id, metodo_pago=metodo)
            .values_list('dte_id', flat=True).first()
        )
        if not dte_id:
            return no_encontrada

        with transaction.atomic():
            objetivo = alcance.select_for_update(of=('self',)).filter(id=dte_id).first()
            if objetivo is None:
                return no_encontrada
            # Releída con la factura bloqueada: otro envío pudo borrarla ya.
            pago = Dte_Detalle_Pago.objects.filter(id=pago_id, metodo_pago=metodo, dte=objetivo).first()
            if pago is None:
                return no_encontrada

            if Dte_Incidencia.objects.filter(dte=objetivo, estado__in=['PENDIENTE', 'EN_GESTION']).exists():
                return JsonResponse({'success': False, 'error': 'No se puede modificar la compensación mientras existan incidencias pendientes o en gestión.'}, status=400)

            pago.delete()
            _recalcular_estado_pago(objetivo)

        return JsonResponse({'success': True, 'message': 'Compensación revertida correctamente'})
    except Exception:
        logger.exception('Compensación: error en %s', request.path)
        return JsonResponse({
            'success': False,
            'error': 'No se pudo completar la operación. Reintenta; si persiste, avisa a soporte.',
        }, status=500)


def _recalcular_estado_pago(dte):
    """
    Recalcula y guarda estado_pago de una factura según sus pagos (efectivo + NC +
    compensaciones). Usa los mismos valores que el flujo de pagos existente:
    'PAGADO' / 'Parcial' / 'Pendiente' (pagoBadge() en el front es case-insensitive).
    Tolerancia de 1 peso por el redondeo de Dte_Detalle_Pago.monto (IntegerField).
    """
    total_pagos = Dte_Detalle_Pago.objects.filter(dte=dte).aggregate(total=Sum('monto'))['total'] or 0
    monto_total = float(dte.monto_con_iva or 0)
    if total_pagos >= monto_total - 1:
        dte.estado_pago = 'PAGADO'
    elif total_pagos > 0:
        dte.estado_pago = 'Parcial'
    else:
        dte.estado_pago = 'Pendiente'
    dte.save(update_fields=['estado_pago'])


# ====================================================================================
# COMPENSACIÓN CON FACTURA EMITIDA (instrumento = DTE de VENTA a este proveedor)
# ====================================================================================
#
# Cuando ya no se puede emitir/cargar una NC del proveedor, EDEL o nosotros le emitimos
# una factura (un cargo) al proveedor y la asociamos a su factura de compra para rebajar
# el saldo a pagar. El instrumento es un DTE tipo_transaccion='VENTA' cuyo receptor es ese
# mismo proveedor (mismo RUT). Mismo libro de pagos (Dte_Detalle_Pago) que la compensación
# mismo-proveedor, pero con FK documento_compensacion al DTE emitido (permite uso parcial y
# multi-uso de una misma factura emitida, y evita colisiones por número repetido).
# Es un neteo de tesorería / cuentas por pagar, NO una relación tributaria SII.


def _norm_rut(rut):
    """Normaliza un RUT a sólo alfanuméricos en mayúscula para comparar entre formatos
    (12.345.678-9 vs 12345678-9)."""
    return ''.join(ch for ch in (rut or '') if ch.isalnum()).upper()


def _folio_normalizado(texto):
    """Folio escrito a mano -> solo dígitos y sin ceros a la izquierda
    ('0099887766', ' 99 887 766', 'F-99887766' -> '99887766'); '' si no trae
    dígitos. Así el control de reuso del modo manual (B5-08) no se salta con
    otra forma de escribir el mismo número."""
    return re.sub(r'\D', '', str(texto or '')).lstrip('0')


def _empresas_mismo_rut_ids(proveedor):
    """IDs de Empresa que comparten el RUT del proveedor (normalizado). Acotado a empresas
    que figuran como receptor de algún DTE de VENTA, para no escanear todo el padrón.
    Incluye siempre el id del propio proveedor."""
    ids = {proveedor.id}
    rut_norm = _norm_rut(getattr(proveedor, 'rut', ''))
    if not rut_norm:
        return ids
    receptor_ids = (
        Dte.objects.filter(tipo_transaccion='VENTA')
        .exclude(receptor__isnull=True)
        .values_list('receptor_id', flat=True).distinct()
    )
    for e in Empresa.objects.filter(id__in=list(receptor_ids)).only('id', 'rut'):
        if _norm_rut(e.rut) == rut_norm:
            ids.add(e.id)
    return ids


@login_required
@require_GET
def obtener_documentos_emitidos_compensar_disponibles(request):
    """
    Lista facturas EMITIDAS (tipo_transaccion='VENTA') por nosotros/EDEL cuyo receptor es el
    proveedor de la factura objetivo (mismo RUT), para usarlas como instrumento de
    compensación. Devuelve sólo las que tienen saldo disponible (monto - lo ya compensado vía
    el FK documento_compensacion). Espeja obtener_facturas_compensar_disponibles.
    La factura objetivo debe ser del alcance de la empresa en sesión (si no,
    404). Las emitidas se ofrecen aunque las haya emitido otra empresa del
    grupo (EDEL): es el caso de uso documentado de esta variante.
    """
    try:
        alcance = _alcance_compras_sesion(request)
        if alcance is None:
            return _sin_empresa_en_sesion()

        dte_id = _id_entero(request.GET.get('dte_id'))
        if not dte_id:
            return JsonResponse({'success': False, 'error': 'ID de factura objetivo requerido'}, status=400)

        try:
            objetivo = alcance.select_related('emisor').get(id=dte_id)
        except Dte.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Factura objetivo no encontrada'}, status=404)

        proveedor = objetivo.emisor
        if not proveedor:
            return JsonResponse({'success': False, 'error': 'La factura objetivo no tiene proveedor (emisor)'}, status=400)

        busqueda = (request.GET.get('busqueda', '') or '').strip()
        try:
            limit = max(1, min(int(request.GET.get('limit', 200)), 500))
        except (TypeError, ValueError):
            limit = 200

        receptor_ids = _empresas_mismo_rut_ids(proveedor)

        candidatos = Dte.objects.filter(
            tipo_transaccion='VENTA',
            tipo_documento__in=['FACTURA ELECTRONICA', 'FACTURA EXENTA'],
            receptor_id__in=list(receptor_ids),
            descartado=False,
        ).exclude(es_nota_credito=True).exclude(
            estado_dte__in=['ANULADO', 'RECHAZADO', 'CANCELADO']
        )

        if busqueda:
            candidatos = candidatos.filter(numero_documento__icontains=busqueda)

        candidatos = list(
            candidatos.select_related('emisor').order_by('-fecha_emision')[:limit]
        )

        # Monto ya compensado de cada instrumento (suma de filas que lo referencian por FK).
        ids = [c.id for c in candidatos]
        usados_map = dict(
            Dte_Detalle_Pago.objects.filter(
                documento_compensacion_id__in=ids,
                metodo_pago=METODO_COMPENSACION_EMITIDA,
            ).values_list('documento_compensacion_id')
             .annotate(total=Sum('monto'))
             .values_list('documento_compensacion_id', 'total')
        )

        resultado = []
        for c in candidatos:
            usado = float(usados_map.get(c.id, 0) or 0)
            saldo_disponible = float(c.monto_con_iva or 0) - usado
            if saldo_disponible <= 0:
                continue
            resultado.append({
                'id': c.id,
                'numero_documento': c.numero_documento,
                'tipo_documento': c.tipo_documento,
                'emisor': c.emisor.nombre if c.emisor else 'N/A',
                'fecha_emision': c.fecha_emision.strftime('%Y-%m-%d') if c.fecha_emision else '',
                'monto_con_iva': float(c.monto_con_iva or 0),
                'saldo_disponible': saldo_disponible,
                'estado': c.estado_dte,
            })

        return JsonResponse({'success': True, 'documentos': resultado})
    except Exception:
        logger.exception('Compensación: error en %s', request.path)
        return JsonResponse({
            'success': False,
            'error': 'No se pudo completar la operación. Reintenta; si persiste, avisa a soporte.',
        }, status=500)


@login_required
@require_POST
def asociar_documento_emitido_compensacion(request):
    """
    Asocia una factura EMITIDA al proveedor (o un registro manual de la misma) como
    instrumento de compensación sobre una factura de compra objetivo. Espeja
    asociar_factura_compensacion. modo='existente' (DTE del sistema) | 'manual'.

    NOTA TRIBUTARIA: neteo de tesorería / cuentas por pagar, NO una relación SII.
    Alcance: la factura objetivo debe ser de la empresa en sesión (o sin
    receptor); si no, 404. La emitida puede ser de otra empresa del grupo
    (EDEL), siempre dirigida al mismo RUT del proveedor.
    """
    try:
        from django.utils.dateparse import parse_date

        alcance = _alcance_compras_sesion(request)
        if alcance is None:
            return _sin_empresa_en_sesion()

        try:
            data = json.loads(request.body)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Datos inválidos'}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({'success': False, 'error': 'Datos inválidos'}, status=400)
        dte_id = _id_entero(data.get('dte_id'))
        modo = (str(data.get('modo') or 'existente')).strip()
        monto_pedido = data.get('monto')

        if not dte_id:
            return JsonResponse({'success': False, 'error': 'Datos incompletos'}, status=400)

        with transaction.atomic():
            try:
                # of=('self',): Dte.receptor es nullable, y select_related sobre un FK
                # nullable genera LEFT OUTER JOIN -> PostgreSQL rechaza FOR UPDATE sobre
                # el lado nullable del join. Con `of` sólo se bloquea la fila de app_dte.
                objetivo = alcance.select_for_update(of=('self',)).select_related('emisor').get(id=dte_id)
            except Dte.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Factura objetivo no encontrada'}, status=404)

            proveedor = objetivo.emisor
            if not proveedor:
                return JsonResponse({'success': False, 'error': 'La factura objetivo no tiene proveedor (emisor)'}, status=400)

            if (objetivo.estado_dte or '').strip().upper() in _ESTADOS_DTE_SIN_EFECTO:
                return JsonResponse({'success': False, 'error': 'No se puede compensar una factura anulada/rechazada'}, status=400)
            motivo = _motivo_documento_sin_efecto(objetivo, 'factura objetivo')
            if motivo:
                return JsonResponse({'success': False, 'error': motivo}, status=400)

            # Guard: incidencias pendientes (espeja asociar_factura_compensacion)
            if Dte_Incidencia.objects.filter(dte=objetivo, estado__in=['PENDIENTE', 'EN_GESTION']).exists():
                return JsonResponse({'success': False, 'error': 'No se puede compensar mientras existan incidencias pendientes o en gestión para esta factura.'}, status=400)

            pagos_objetivo = Dte_Detalle_Pago.objects.filter(dte=objetivo).aggregate(total=Sum('monto'))['total'] or 0
            saldo_objetivo = float(objetivo.monto_con_iva or 0) - float(pagos_objetivo)
            if saldo_objetivo <= 0:
                return JsonResponse({'success': False, 'error': 'La factura objetivo no tiene saldo pendiente'}, status=400)

            instrumento = None
            advertencia = None
            if modo == 'existente':
                instrumento_id = _id_entero(data.get('documento_emitido_id'))
                if not instrumento_id:
                    return JsonResponse({'success': False, 'error': 'Debe seleccionar la factura emitida'}, status=400)
                try:
                    instrumento = Dte.objects.select_for_update(of=('self',)).select_related('emisor', 'receptor').get(
                        id=instrumento_id, tipo_transaccion='VENTA',
                    )
                except Dte.DoesNotExist:
                    return JsonResponse({'success': False, 'error': 'Factura emitida no encontrada'}, status=404)

                if instrumento.es_nota_credito:
                    return JsonResponse({'success': False, 'error': 'El instrumento no puede ser una Nota de Crédito'}, status=400)
                if instrumento.descartado or (instrumento.estado_dte or '').strip().upper() in _ESTADOS_DTE_SIN_EFECTO:
                    return JsonResponse({'success': False, 'error': 'La factura emitida está anulada, rechazada o descartada'}, status=400)

                # Guard: el receptor de la factura emitida debe ser el mismo proveedor (por RUT)
                receptor = instrumento.receptor
                if not receptor or _norm_rut(receptor.rut) != _norm_rut(proveedor.rut):
                    return JsonResponse({'success': False, 'error': 'La factura emitida debe estar dirigida a este proveedor (mismo RUT)'}, status=400)

                # Guard: no asociar dos veces el mismo instrumento a la misma factura objetivo
                if Dte_Detalle_Pago.objects.filter(
                    dte=objetivo, metodo_pago=METODO_COMPENSACION_EMITIDA,
                    documento_compensacion=instrumento,
                ).exists():
                    return JsonResponse({'success': False, 'error': 'Esta factura emitida ya está asociada a esta factura de compra'}, status=400)

                # Saldo disponible del instrumento = monto - lo ya compensado (por FK)
                usado = Dte_Detalle_Pago.objects.filter(
                    documento_compensacion=instrumento,
                    metodo_pago=METODO_COMPENSACION_EMITIDA,
                ).aggregate(total=Sum('monto'))['total'] or 0
                saldo_instrumento = float(instrumento.monto_con_iva or 0) - float(usado)
                if saldo_instrumento <= 0:
                    return JsonResponse({'success': False, 'error': 'La factura emitida no tiene saldo disponible'}, status=400)

                try:
                    monto_default = min(saldo_objetivo, saldo_instrumento)
                    monto_aplicado = float(monto_pedido) if monto_pedido not in (None, '') else monto_default
                except (TypeError, ValueError):
                    return JsonResponse({'success': False, 'error': 'Monto inválido'}, status=400)
                if monto_aplicado != monto_aplicado or monto_aplicado in (float('inf'), float('-inf')):
                    return JsonResponse({'success': False, 'error': 'Monto inválido'}, status=400)

                monto_aplicado = int(round(min(monto_aplicado, saldo_objetivo, saldo_instrumento)))
                if monto_aplicado <= 0:
                    return JsonResponse({'success': False, 'error': 'El monto a compensar debe ser mayor a cero'}, status=400)

                emisor_nombre = instrumento.emisor.nombre if instrumento.emisor else 'N/A'
                Dte_Detalle_Pago.objects.create(
                    dte=objetivo,
                    metodo_pago=METODO_COMPENSACION_EMITIDA,
                    voucher=str(instrumento.numero_documento),
                    monto=monto_aplicado,
                    notas=f'Compensación con factura emitida #{instrumento.numero_documento} - emitida por {emisor_nombre}',
                    fecha_pago=timezone.localdate(),
                    documento_compensacion=instrumento,
                )

            elif modo == 'manual':
                numero_escrito = (str(data.get('numero') or '')).strip()
                emisor_label = (str(data.get('emisor_label') or '')).strip() or 'N/A'
                if not numero_escrito:
                    return JsonResponse({'success': False, 'error': 'El número de la factura emitida es obligatorio'}, status=400)
                # Folio canónico (solo dígitos, sin ceros a la izquierda): es lo
                # que se valida y lo que se guarda en el voucher (B5-08).
                numero = _folio_normalizado(numero_escrito)
                if not numero or len(numero) > 18:
                    return JsonResponse({
                        'success': False,
                        'error': 'El número de la factura emitida debe ser su folio (solo dígitos).',
                    }, status=400)

                try:
                    monto_aplicado = float(monto_pedido) if monto_pedido not in (None, '') else 0
                except (TypeError, ValueError):
                    return JsonResponse({'success': False, 'error': 'Monto inválido'}, status=400)
                if monto_aplicado != monto_aplicado or monto_aplicado in (float('inf'), float('-inf')):
                    return JsonResponse({'success': False, 'error': 'Monto inválido'}, status=400)

                monto_aplicado = int(round(min(monto_aplicado, saldo_objetivo)))
                if monto_aplicado <= 0:
                    return JsonResponse({'success': False, 'error': 'El monto a compensar debe ser mayor a cero y no exceder el saldo'}, status=400)

                # B5-08: el modo manual no tiene FK al documento, así que el
                # control de saldo del instrumento se hace aquí.
                proveedor_ids = set(_empresas_por_rut(proveedor.rut).values_list('id', flat=True)) | {proveedor.id}
                # (1) Si la factura emitida está en el sistema, debe usarse la
                # pestaña de facturas existentes (controla su saldo por FK).
                if int(numero) <= _LIMITE_ENTERO and Dte.objects.filter(
                    tipo_transaccion='VENTA',
                    tipo_documento__in=['FACTURA ELECTRONICA', 'FACTURA EXENTA'],
                    numero_documento=int(numero),
                    receptor_id__in=proveedor_ids,
                ).exclude(es_nota_credito=True).exclude(
                    estado_dte__in=['ANULADO', 'RECHAZADO', 'CANCELADO'],
                ).exists():
                    return JsonResponse({
                        'success': False,
                        'error': f'La factura emitida #{numero} está en el sistema: usa la pestaña de '
                                 'facturas existentes, que controla su saldo disponible.',
                    }, status=400)
                # (2) Reuso del mismo número con el mismo proveedor: repartir una
                # factura emitida entre varias compras es válido, así que NO se
                # bloquea (la pantalla no tiene paso de confirmación): se
                # registra y la respuesta avisa cuánto llevaba aplicado y dónde.
                # Los vouchers viejos pueden estar escritos con ceros o espacios:
                # se comparan normalizados.
                usos_previos = [
                    (monto, folio)
                    for voucher, monto, folio in Dte_Detalle_Pago.objects.filter(
                        metodo_pago=METODO_COMPENSACION_EMITIDA,
                        documento_compensacion__isnull=True,
                        dte__emisor_id__in=proveedor_ids,
                    ).values_list('voucher', 'monto', 'dte__numero_documento')
                    if _folio_normalizado(voucher) == numero
                ]
                ya_usado = sum(int(monto or 0) for monto, _folio in usos_previos)
                try:
                    total_emitida = float(data.get('monto_total_emitida') or 0)
                except (TypeError, ValueError):
                    total_emitida = 0
                if total_emitida > 0 and ya_usado + monto_aplicado > total_emitida + 1:
                    return JsonResponse({
                        'success': False,
                        'error': (
                            f'La factura emitida #{numero} ya tiene ${ya_usado:,.0f} aplicados; '.replace(',', '.')
                            + f'con este monto se supera su total de ${total_emitida:,.0f}.'.replace(',', '.')
                        ),
                        'ya_usado': ya_usado,
                    }, status=400)
                if ya_usado > 0:
                    folios_previos = sorted({str(folio) for _monto, folio in usos_previos})[:10]
                    advertencia = (
                        f'La factura emitida #{numero} ya se había aplicado por ${ya_usado:,.0f} '.replace(',', '.')
                        + f'en la(s) factura(s) {", ".join(folios_previos)} de este proveedor; '
                        'verifica que no supere su total.'
                    )

                fecha_doc = parse_date(str(data.get('fecha') or '')) or timezone.localdate()
                Dte_Detalle_Pago.objects.create(
                    dte=objetivo,
                    metodo_pago=METODO_COMPENSACION_EMITIDA,
                    voucher=numero,
                    monto=monto_aplicado,
                    notas=f'Compensación con factura emitida (manual) #{numero} - emitida por {emisor_label} ({fecha_doc.isoformat()})',
                    fecha_pago=fecha_doc,
                    documento_compensacion=None,
                )
            else:
                return JsonResponse({'success': False, 'error': 'Modo inválido'}, status=400)

            _recalcular_estado_pago(objetivo)

        respuesta = {
            'success': True,
            'message': 'Factura emitida asociada como compensación correctamente',
            'monto_aplicado': monto_aplicado,
        }
        if advertencia:
            # La pantalla muestra 'message': el aviso de reuso va ahí mismo
            # (solo contiene folios y montos, nada escrito por el usuario).
            respuesta['advertencia'] = advertencia
            respuesta['message'] += '. Ojo: ' + advertencia
        return JsonResponse(respuesta)
    except Exception:
        logger.exception('Compensación: error en %s', request.path)
        return JsonResponse({
            'success': False,
            'error': 'No se pudo completar la operación. Reintenta; si persiste, avisa a soporte.',
        }, status=500)


@login_required
@require_POST
def desasociar_documento_emitido_compensacion(request, pago_id):
    """
    Revierte una compensación con factura emitida: elimina la fila de Dte_Detalle_Pago y
    recalcula el estado de pago de la factura objetivo. Espeja desasociar_factura_compensacion.
    Solo sobre facturas del alcance de la empresa en sesión (si no, 404); la
    factura objetivo se bloquea antes de borrar y recalcular.
    """
    return _desasociar_compensacion(request, pago_id, METODO_COMPENSACION_EMITIDA)


# ========== DOCUMENTOS VINCULADOS A UN TRASPASO (NC / AJUSTES) ==========

# Estados en los que un traspaso ya pasó por la mesa de recepción. Fuera de esta
# lista el documento sigue "en camino" y las NC vivas son un riesgo a futuro,
# no un hecho consumado.
_ESTADOS_TRASPASO_RECIBIDO = (
    'RECEPCIONADO_COMPLETO', 'RECEPCIONADO_PARCIAL',
    'RECEPCIONADO_SOBRANTE', 'EN_REGULARIZACION',
)


@requiere_alguno_de_los_permisos('recepcion_dte', 'gestion_dte_compras')
@require_GET
def dte_documentos_vinculados_api(request, dte_id):
    """Notas de crédito y ajustes emitidos contra un DTE de traspaso.

    Permiso: ver Recepción DTE (único consumidor, recepcion_dte.html) o
    Gestión Documentos Compras. No se mapea en URL_PERMISO_MAP porque bajo
    '/app/dte/<id>/' viven rutas de otras pantallas (auditoría 2026-09, SEC g).

    La pantalla de recepción avisaba "2 NC" y el número del documento que anula
    solo vivía en un tooltip: no había forma de abrirlo ni de saber qué líneas
    tocó cada uno, que es justo lo que se necesita para decidir si un faltante
    ya quedó cubierto. Acá se devuelve, por documento vinculado, el detalle de
    líneas y si esas unidades siguen o no contadas dentro del original.
    """
    try:
        dte = Dte.objects.select_related('emisor', 'receptor', 'sucursal').get(id=dte_id)
    except Dte.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'DTE no encontrado.'}, status=404)

    vinculados = list(
        Dte.objects.filter(documento_afectado_id=dte.id)
        .select_related('emisor')
        .order_by('fecha_emision', 'id')
    )

    # Lo que realmente entró al stock del destino, por talla. Sirve para decir
    # si una NC "aún contada" terminó siendo un problema o no.
    ingresado_por_talla = {}
    for row in (
        Productos_Recepcionados.objects
        .filter(dte_id=dte.id, producto_talla__isnull=False)
        .values('producto_talla_id')
        .annotate(arribado=Sum('stockArribado'), danado=Sum('cantidad_danada'))
    ):
        neto = int(row['arribado'] or 0) - int(row['danado'] or 0)
        ingresado_por_talla[row['producto_talla_id']] = max(0, neto)

    ya_recibido = (
        dte.estado_dte in _ESTADOS_TRASPASO_RECIBIDO or bool(dte.fecha_recepcion)
    )

    lineas_por_dte = {}
    if vinculados:
        for dp in (
            Dte_Productos.objects
            .filter(dte_id__in=[d.id for d in vinculados])
            .select_related(
                'productoTalla__producto__atributo1',
                'productoTalla__producto__atributo2',
            )
            .order_by('id')
        ):
            pt = dp.productoTalla
            prod = pt.producto if pt else None
            lineas_por_dte.setdefault(dp.dte_id, []).append({
                'sku': pt.sku if pt else '-',
                'articulo': (prod.articulo if prod else '') or '-',
                'marca': prod.atributo1.valor if (prod and prod.atributo1) else '',
                'color': prod.atributo2.valor if (prod and prod.atributo2) else '',
                'talla': pt.talla if pt else '-',
                'cantidad': int(dp.stock or 0),
                # Texto congelado al emitir: si la ficha se editó después, esto
                # es lo que dice el papel.
                'descripcion': (dp.descripcion or '').strip(),
                'producto_talla_id': pt.id if pt else None,
            })

    documentos = []
    for doc in vinculados:
        lineas = lineas_por_dte.get(doc.id, [])
        unidades = sum(l['cantidad'] for l in lineas) or int(doc.unidades_productos or 0)
        anulado = doc.estado_dte in ('CANCELADO', 'ANULADO')
        redujo = bool(doc.redujo_lineas_documento)

        # Unidades de esta NC que el documento original sigue contando y que
        # además entraron al stock del destino.
        ingresadas = 0
        if not redujo and not anulado:
            saldo = {}
            for linea in lineas:
                if linea['producto_talla_id']:
                    saldo[linea['producto_talla_id']] = (
                        saldo.get(linea['producto_talla_id'], 0) + linea['cantidad']
                    )
            for talla_id, uds in saldo.items():
                ingresadas += min(uds, ingresado_por_talla.get(talla_id, 0))

        if anulado:
            efecto = 'anulado'
            efecto_texto = 'Este documento fue anulado: no afecta al original.'
        elif redujo:
            efecto = 'descontada'
            efecto_texto = (
                f'Las {unidades} uds ya salieron del total del documento original '
                f'(las líneas se redujeron al emitirla).'
            )
        elif not ya_recibido:
            efecto = 'pre_recepcion'
            efecto_texto = (
                f'El documento original SIGUE contando estas {unidades} uds. '
                f'Al recepcionar no deberían ingresar al stock.'
            )
        elif ingresadas > 0:
            efecto = 'ingresada'
            efecto_texto = (
                f'{ingresadas} de estas {unidades} uds se ingresaron igual al stock '
                f'del destino. Hay que corregirlo.'
            )
        else:
            efecto = 'no_ingresada'
            efecto_texto = (
                f'Estas {unidades} uds no ingresaron al stock (se recibieron como '
                f'faltante). El documento queda cuadrado.'
            )

        documentos.append({
            'id': doc.id,
            'numero_documento': doc.numero_documento,
            'tipo_documento': doc.tipo_documento,
            'es_nota_credito': bool(doc.es_nota_credito),
            'estado_dte': doc.estado_dte,
            'fecha_emision': doc.fecha_emision.strftime('%d-%m-%Y') if doc.fecha_emision else '-',
            'responsable': doc.responsable or '-',
            'motivo': (doc.motivo_nc or doc.referencias or '').strip(),
            'monto_con_iva': float(doc.monto_con_iva or 0),
            'unidades': unidades,
            'redujo_lineas_documento': redujo,
            'unidades_ingresadas': ingresadas,
            'efecto': efecto,
            'efecto_texto': efecto_texto,
            'lineas': lineas,
        })

    return JsonResponse({
        'success': True,
        'dte': {
            'id': dte.id,
            'numero_documento': dte.numero_documento,
            'tipo_documento': dte.tipo_documento,
            'estado_dte': dte.estado_dte,
            'fecha_emision': dte.fecha_emision.strftime('%d-%m-%Y') if dte.fecha_emision else '-',
            'fecha_recepcion': dte.fecha_recepcion.strftime('%d-%m-%Y') if dte.fecha_recepcion else '',
            'ya_recibido': ya_recibido,
        },
        'documentos': documentos,
    })
