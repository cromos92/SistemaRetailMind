"""
Módulo de Impresión de Etiquetas Zebra - RetailMind
Permite imprimir etiquetas de productos desde documentos (DTEs, Traspasos, Recepciones)
o manualmente por artículo/SKU.
"""

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Sum, Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from django.db import transaction
import json
import os
from decimal import Decimal
from datetime import datetime
from contextlib import contextmanager

import mysql.connector

from .models import (
    Dte, Dte_Productos, Sucursal, Producto_Talla, Producto, 
    Traspaso, Traspaso_Detalle, Ticket, Ticket_Productos,
    Productos_Recepcionados, EmpresaUser, Empresa,
    HistorialImpresionEtiqueta, DetalleImpresionEtiqueta
)

MYSQL_TIPO_COMPRA = 'MYSQL_DTE_COMPRA'
MYSQL_TIPO_TRASPASO = 'MYSQL_DTE_TRASPASO'


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@contextmanager
def _get_mysql_connection():
    """Conexión temporal a MySQL HoldingTebes para lectura."""
    host = os.getenv('MYSQL_HOST')
    database = os.getenv('MYSQL_DATABASE')
    user = os.getenv('MYSQL_USER')
    password = os.getenv('MYSQL_PASSWORD')
    port = _safe_int(os.getenv('MYSQL_PORT', 3306), 3306)

    if not all([host, database, user]):
        raise Exception('Configuración MySQL incompleta en variables de entorno')

    conn = mysql.connector.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        connection_timeout=8
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _obtener_alias_sucursal(sucursal_id):
    if not sucursal_id:
        return None
    sucursal = Sucursal.objects.filter(id=sucursal_id).only('alias').first()
    return sucursal.alias if sucursal else None


def _build_mysql_doc_item(doc, tipo, productos_count, ya_impreso=False, veces_impreso=0, ultima_impresion=None):
    fecha = doc.get('fecha_emision')
    if hasattr(fecha, 'strftime'):
        fecha_display = fecha.strftime('%d/%m/%Y')
        fecha_sort = fecha.strftime('%Y-%m-%d')
    else:
        fecha_display = ''
        fecha_sort = ''

    tipo_doc = (doc.get('tipo_documento') or '').strip() or ('DTE Compra MySQL' if tipo == MYSQL_TIPO_COMPRA else 'DTE Traspaso MySQL')
    destino = (doc.get('bodega_destino') or '').strip()
    origen = (doc.get('bodega_inicio') or '').strip()

    return {
        'id': doc.get('ID'),
        'tipo': tipo,
        'tipo_display': tipo_doc if tipo == MYSQL_TIPO_COMPRA else f'{tipo_doc} (Traspaso)',
        'numero': doc.get('n_documento'),
        'fecha': fecha_display,
        'fecha_sort': fecha_sort,
        'origen': origen or (doc.get('rut_emisor') or 'Sin origen'),
        'destino': destino or origen or 'Sin destino',
        'total_productos': productos_count,
        'monto': float(doc.get('monto_total') or 0),
        'estado': doc.get('estado') or 'Vigente',
        'ya_impreso': ya_impreso,
        'veces_impreso': veces_impreso,
        'ultima_impresion': ultima_impresion
    }


def _obtener_documentos_mysql(empresa_actual_id, sucursal_id=None, tipo_documento='todos', fecha_inicio=None, fecha_fin=None, search=''):
    alias_sucursal = _obtener_alias_sucursal(sucursal_id)

    # Historial de impresiones de documentos provenientes de MySQL
    historial_mysql = HistorialImpresionEtiqueta.objects.filter(
        sucursal__empresa_id=empresa_actual_id,
        completado=True,
        tipo_origen__in=[MYSQL_TIPO_COMPRA, MYSQL_TIPO_TRASPASO]
    )

    impresiones_por_doc = {}
    for hist in historial_mysql.order_by('-fecha_impresion'):
        key = (hist.tipo_origen, hist.documento_id)
        if key not in impresiones_por_doc:
            impresiones_por_doc[key] = {
                'count': 1,
                'last': hist
            }
        else:
            impresiones_por_doc[key]['count'] += 1

    documentos = []

    filtros_comunes = []
    params_comunes = []
    if fecha_inicio:
        filtros_comunes.append('d.fecha_emision >= %s')
        params_comunes.append(fecha_inicio)
    if fecha_fin:
        filtros_comunes.append('d.fecha_emision <= %s')
        params_comunes.append(fecha_fin)
    if search:
        filtros_comunes.append('(CAST(d.n_documento AS CHAR) LIKE %s OR d.rut_emisor LIKE %s)')
        like = f'%{search}%'
        params_comunes.extend([like, like])
    if alias_sucursal:
        filtros_comunes.append('(d.bodega_inicio = %s OR d.bodega_destino = %s)')
        params_comunes.extend([alias_sucursal, alias_sucursal])

    def fetch_docs(extra_filter_sql):
        where = ['1=1', extra_filter_sql]
        where.extend(filtros_comunes)
        sql = f"""
            SELECT
                d.ID, d.n_documento, d.tipo_documento, d.monto_total, d.estado,
                d.fecha_emision, d.rut_emisor, d.rut_cliente,
                d.bodega_inicio, d.bodega_destino,
                COALESCE(SUM(pd.cantidad), 0) AS total_unidades
            FROM dte d
            LEFT JOIN productos_dte pd ON pd.IdDte = d.ID
            WHERE {' AND '.join(where)}
            GROUP BY d.ID
            ORDER BY d.fecha_emision DESC
            LIMIT 200
        """
        with _get_mysql_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params_comunes)
            rows = cursor.fetchall()
            cursor.close()
            return rows

    if tipo_documento in ['todos', 'compra', 'dte_compra']:
        rows = fetch_docs("(d.bodega_inicio IS NOT NULL AND d.bodega_inicio <> '') AND (d.bodega_destino IS NULL OR d.bodega_destino = '')")
        for row in rows:
            key = (MYSQL_TIPO_COMPRA, row.get('ID'))
            imp = impresiones_por_doc.get(key)
            veces = imp['count'] if imp else 0
            ultima = None
            if imp and imp.get('last'):
                hist = imp['last']
                usuario_nombre = hist.usuario.get_full_name() or hist.usuario.username if hist.usuario else 'Sistema'
                ultima = f'{hist.fecha_impresion.strftime("%d/%m/%Y %H:%M")} por {usuario_nombre}'
            documentos.append(
                _build_mysql_doc_item(
                    row,
                    MYSQL_TIPO_COMPRA,
                    _safe_int(row.get('total_unidades')),
                    ya_impreso=veces > 0,
                    veces_impreso=veces,
                    ultima_impresion=ultima
                )
            )

    if tipo_documento in ['todos', 'traspaso', 'dte_traspaso']:
        rows = fetch_docs("(d.bodega_inicio IS NOT NULL AND d.bodega_inicio <> '') AND (d.bodega_destino IS NOT NULL AND d.bodega_destino <> '')")
        for row in rows:
            key = (MYSQL_TIPO_TRASPASO, row.get('ID'))
            imp = impresiones_por_doc.get(key)
            veces = imp['count'] if imp else 0
            ultima = None
            if imp and imp.get('last'):
                hist = imp['last']
                usuario_nombre = hist.usuario.get_full_name() or hist.usuario.username if hist.usuario else 'Sistema'
                ultima = f'{hist.fecha_impresion.strftime("%d/%m/%Y %H:%M")} por {usuario_nombre}'
            documentos.append(
                _build_mysql_doc_item(
                    row,
                    MYSQL_TIPO_TRASPASO,
                    _safe_int(row.get('total_unidades')),
                    ya_impreso=veces > 0,
                    veces_impreso=veces,
                    ultima_impresion=ultima
                )
            )

    return documentos


def _obtener_productos_documento_mysql(tipo_documento, documento_id):
    with _get_mysql_connection() as conn:
        cursor_doc = conn.cursor(dictionary=True)
        cursor_doc.execute("""
            SELECT ID, n_documento, tipo_documento, fecha_emision, rut_emisor, bodega_inicio, bodega_destino
            FROM dte
            WHERE ID = %s
            LIMIT 1
        """, (documento_id,))
        dte = cursor_doc.fetchone()
        cursor_doc.close()

        if not dte:
            raise Exception('Documento MySQL no encontrado')

        cursor_prod = conn.cursor(dictionary=True)
        cursor_prod.execute("""
            SELECT
                ID, codigo_asociado, articulo, descripcion, talla, cantidad,
                precio_publico, precio_interno, marca, color
            FROM productos_dte
            WHERE IdDte = %s
            ORDER BY ID
        """, (documento_id,))
        rows = cursor_prod.fetchall()
        cursor_prod.close()

    fecha = dte.get('fecha_emision')
    documento_info = {
        'tipo': tipo_documento,
        'tipo_display': dte.get('tipo_documento') or ('DTE Compra MySQL' if tipo_documento == MYSQL_TIPO_COMPRA else 'DTE Traspaso MySQL'),
        'numero': dte.get('n_documento'),
        'fecha': fecha.strftime('%d/%m/%Y') if hasattr(fecha, 'strftime') else '',
        'proveedor': dte.get('rut_emisor') or '',
        'sucursal_destino': dte.get('bodega_destino') or dte.get('bodega_inicio') or ''
    }

    productos = []
    for row in rows:
        precio = _safe_int(row.get('precio_publico')) or _safe_int(row.get('precio_interno'))
        articulo = (row.get('articulo') or '').strip()
        descripcion = (row.get('descripcion') or articulo).strip()
        sku = str(row.get('codigo_asociado') or '')

        productos.append({
            'id': row.get('ID'),
            'producto_talla_id': None,  # No hay FK local garantizada para registros MySQL
            'sku': sku,
            'articulo': articulo[:50],
            'descripcion': descripcion,
            'marca': (row.get('marca') or '')[:10],
            'talla': str(row.get('talla') or '')[:4],
            'color': (row.get('color') or '')[:10],
            'cantidad': _safe_int(row.get('cantidad'), 0),
            'precio': float(precio),
            'sucursal': dte.get('bodega_destino') or dte.get('bodega_inicio') or '',
            'factura': str(dte.get('n_documento') or ''),
            'seleccionado': True
        })

    return documento_info, productos


def _buscar_producto_mysql(termino, sucursal_id=None):
    alias_sucursal = _obtener_alias_sucursal(sucursal_id)
    like = f'%{termino}%'
    filtros = [
        '(CAST(t.codigo_asociado AS CHAR) LIKE %s OR t.articulo LIKE %s OR t.descripcion LIKE %s)'
    ]
    params = [like, like, like]

    if alias_sucursal:
        filtros.append('t.alias = %s')
        params.append(alias_sucursal)

    sql = f"""
        SELECT
            t.codigo_asociado, t.articulo, t.descripcion, t.marca, t.color,
            t.size, t.precioventapublico, t.alias
        FROM talla t
        WHERE {' AND '.join(filtros)}
        ORDER BY t.articulo ASC
        LIMIT 30
    """

    with _get_mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()

    productos = []
    for row in rows:
        precio = _safe_int(row.get('precioventapublico'))
        articulo = (row.get('articulo') or '').strip()
        descripcion = (row.get('descripcion') or articulo).strip()
        productos.append({
            'id': row.get('codigo_asociado'),
            'producto_talla_id': None,
            'sku': str(row.get('codigo_asociado') or ''),
            'articulo': articulo[:50],
            'descripcion': descripcion,
            'marca': (row.get('marca') or '')[:10],
            'talla': str(row.get('size') or '')[:4],
            'color': (row.get('color') or '')[:10],
            'precio': float(precio),
            'sucursal': row.get('alias') or '',
            'cantidad': 1,
            'seleccionado': True
        })

    return productos


# ========== VISTA PRINCIPAL ==========

@login_required
def gestion_etiquetas_zebra(request):
    """Vista principal para gestión de impresión de etiquetas Zebra"""
    
    # Obtener sucursales del usuario
    empresa_actual_id = request.session.get('idEmpresaActual')
    sucursal_actual_id = request.session.get('idSucursalActual')
    
    sucursales = Sucursal.objects.filter(empresa_id=empresa_actual_id).order_by('alias')
    
    # Obtener últimas impresiones
    ultimas_impresiones = HistorialImpresionEtiqueta.objects.filter(
        sucursal__empresa_id=empresa_actual_id
    ).order_by('-fecha_impresion')[:10]
    
    context = {
        'sucursales': sucursales,
        'sucursal_actual_id': sucursal_actual_id,
        'ultimas_impresiones': ultimas_impresiones,
    }
    
    return render(request, 'vistas/modulo_existencias/gestion_etiquetas_zebra.html', context)


# ========== APIS DE DOCUMENTOS ==========

@login_required
@require_GET
def obtener_documentos_etiquetas(request):
    """
    Obtener documentos disponibles para generar etiquetas
    Incluye indicador de si ya fueron impresos
    """
    try:
        # Parámetros de filtro
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        tipo_documento = request.GET.get('tipo_documento', 'todos')
        fuente = request.GET.get('fuente', 'postgres').strip().lower()
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        search = request.GET.get('search', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        empresa_actual_id = request.session.get('idEmpresaActual')
        
        if not empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa en la sesión'
            })
        
        documentos = []

        # ===== FUENTE MYSQL (HoldingTebes) =====
        if fuente == 'mysql':
            documentos = _obtener_documentos_mysql(
                empresa_actual_id=empresa_actual_id,
                sucursal_id=sucursal_id,
                tipo_documento=tipo_documento,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                search=search
            )
            documentos.sort(key=lambda x: x['fecha_sort'], reverse=True)

            total_documentos = len(documentos)
            inicio = (page - 1) * per_page
            fin = inicio + per_page
            documentos_paginados = documentos[inicio:fin]

            return JsonResponse({
                'success': True,
                'documentos': documentos_paginados,
                'pagination': {
                    'current_page': page,
                    'total_pages': (total_documentos + per_page - 1) // per_page,
                    'total_items': total_documentos,
                    'has_next': fin < total_documentos,
                    'has_previous': page > 1
                }
            })
        
        # Obtener IDs de documentos ya impresos
        docs_impresos = set(
            HistorialImpresionEtiqueta.objects.filter(
                sucursal__empresa_id=empresa_actual_id,
                completado=True
            ).values_list('tipo_origen', 'documento_id')
        )
        
        # ===== 1. DTEs de COMPRA (Facturas de proveedores) =====
        if tipo_documento in ['todos', 'compra', 'dte_compra']:
            dtes_compra = Dte.objects.filter(
                receptor__id=empresa_actual_id,
                tipo_transaccion='COMPRA'
            ).select_related('emisor', 'receptor', 'sucursal')
            
            if sucursal_id:
                dtes_compra = dtes_compra.filter(sucursal_id=sucursal_id)
            
            if fecha_inicio:
                dtes_compra = dtes_compra.filter(fecha_emision__gte=fecha_inicio)
            if fecha_fin:
                dtes_compra = dtes_compra.filter(fecha_emision__lte=fecha_fin)
            
            if search:
                dtes_compra = dtes_compra.filter(
                    Q(numero_documento__icontains=search) |
                    Q(emisor__razon_social__icontains=search)
                )
            
            for dte in dtes_compra.order_by('-fecha_emision')[:100]:
                # Contar productos
                productos_count = Dte_Productos.objects.filter(dte=dte).aggregate(
                    total_unidades=Sum('stock')
                )['total_unidades'] or 0
                
                # Verificar si ya fue impreso y contar veces
                impresiones = HistorialImpresionEtiqueta.objects.filter(
                    tipo_origen='DTE_COMPRA',
                    documento_id=dte.id,
                    completado=True
                ).order_by('-fecha_impresion')
                
                veces_impreso = impresiones.count()
                ya_impreso = veces_impreso > 0
                
                # Obtener última impresión si existe
                ultima_impresion = None
                if ya_impreso and impresiones.exists():
                    hist = impresiones.first()
                    usuario_nombre = hist.usuario.get_full_name() or hist.usuario.username if hist.usuario else 'Sistema'
                    ultima_impresion = f'{hist.fecha_impresion.strftime("%d/%m/%Y %H:%M")} por {usuario_nombre}'
                
                documentos.append({
                    'id': dte.id,
                    'tipo': 'DTE_COMPRA',
                    'tipo_display': f'{dte.tipo_documento}',
                    'numero': dte.numero_documento,
                    'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                    'fecha_sort': dte.fecha_emision.strftime('%Y-%m-%d'),
                    'origen': dte.emisor.razon_social if dte.emisor else 'Sin proveedor',
                    'destino': dte.sucursal.alias if dte.sucursal else 'Sin sucursal',
                    'total_productos': productos_count,
                    'monto': float(dte.monto_con_iva),
                    'estado': dte.estado_dte,
                    'ya_impreso': ya_impreso,
                    'veces_impreso': veces_impreso,
                    'ultima_impresion': ultima_impresion
                })
        
        # ===== 2. DTEs de TRASPASO INTERNO =====
        if tipo_documento in ['todos', 'traspaso', 'dte_traspaso']:
            dtes_traspaso = Dte.objects.filter(
                Q(emisor_id=empresa_actual_id) | Q(receptor_id=empresa_actual_id),
                tipo_transaccion='TRASPASO'
            ).select_related('emisor', 'receptor', 'sucursal')
            
            if sucursal_id:
                dtes_traspaso = dtes_traspaso.filter(
                    Q(sucursal_id=sucursal_id) |
                    Q(dte_movimientos__sucursal_destino_id=sucursal_id)
                ).distinct()
            
            if fecha_inicio:
                dtes_traspaso = dtes_traspaso.filter(fecha_emision__gte=fecha_inicio)
            if fecha_fin:
                dtes_traspaso = dtes_traspaso.filter(fecha_emision__lte=fecha_fin)
            
            if search:
                dtes_traspaso = dtes_traspaso.filter(numero_documento__icontains=search)
            
            for dte in dtes_traspaso.order_by('-fecha_emision')[:100]:
                productos_count = Dte_Productos.objects.filter(dte=dte).aggregate(
                    total_unidades=Sum('stock')
                )['total_unidades'] or 0
                
                # Obtener sucursal destino del movimiento
                sucursal_destino = dte.sucursal.alias if dte.sucursal else ''
                mov = dte.dte_movimientos.filter(sucursal_destino__isnull=False).first()
                if mov and mov.sucursal_destino:
                    sucursal_destino = mov.sucursal_destino.alias
                
                # Verificar si ya fue impreso y contar veces
                impresiones = HistorialImpresionEtiqueta.objects.filter(
                    tipo_origen='DTE_TRASPASO',
                    documento_id=dte.id,
                    completado=True
                ).order_by('-fecha_impresion')
                
                veces_impreso = impresiones.count()
                ya_impreso = veces_impreso > 0
                
                ultima_impresion = None
                if ya_impreso and impresiones.exists():
                    hist = impresiones.first()
                    usuario_nombre = hist.usuario.get_full_name() or hist.usuario.username if hist.usuario else 'Sistema'
                    ultima_impresion = f'{hist.fecha_impresion.strftime("%d/%m/%Y %H:%M")} por {usuario_nombre}'
                
                documentos.append({
                    'id': dte.id,
                    'tipo': 'DTE_TRASPASO',
                    'tipo_display': f'{dte.tipo_documento} (Traspaso)',
                    'numero': dte.numero_documento,
                    'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                    'fecha_sort': dte.fecha_emision.strftime('%Y-%m-%d'),
                    'origen': dte.emisor.razon_social if dte.emisor else 'Sin origen',
                    'destino': sucursal_destino,
                    'total_productos': productos_count,
                    'monto': float(dte.monto_con_iva),
                    'estado': dte.estado_dte,
                    'ya_impreso': ya_impreso,
                    'veces_impreso': veces_impreso,
                    'ultima_impresion': ultima_impresion
                })
        
        # ===== 3. TRASPASOS INTERNOS (modelo Traspaso) =====
        if tipo_documento in ['todos', 'traspaso_interno']:
            traspasos = Traspaso.objects.filter(
                Q(sucursal_origen__empresa_id=empresa_actual_id) |
                Q(sucursal_destino__empresa_id=empresa_actual_id)
            ).select_related('sucursal_origen', 'sucursal_destino')
            
            if sucursal_id:
                traspasos = traspasos.filter(
                    Q(sucursal_origen_id=sucursal_id) |
                    Q(sucursal_destino_id=sucursal_id)
                )
            
            if fecha_inicio:
                traspasos = traspasos.filter(fecha_solicitud__gte=fecha_inicio)
            if fecha_fin:
                traspasos = traspasos.filter(fecha_solicitud__lte=fecha_fin)
            
            if search:
                traspasos = traspasos.filter(id__icontains=search)
            
            for traspaso in traspasos.order_by('-fecha_solicitud')[:100]:
                productos_count = Traspaso_Detalle.objects.filter(
                    traspaso=traspaso
                ).aggregate(total=Sum('cantidad_solicitada'))['total'] or 0
                
                # Verificar si ya fue impreso y contar veces
                impresiones = HistorialImpresionEtiqueta.objects.filter(
                    tipo_origen='TRASPASO_INTERNO',
                    documento_id=traspaso.id,
                    completado=True
                ).order_by('-fecha_impresion')
                
                veces_impreso = impresiones.count()
                ya_impreso = veces_impreso > 0
                
                ultima_impresion = None
                if ya_impreso and impresiones.exists():
                    hist = impresiones.first()
                    usuario_nombre = hist.usuario.get_full_name() or hist.usuario.username if hist.usuario else 'Sistema'
                    ultima_impresion = f'{hist.fecha_impresion.strftime("%d/%m/%Y %H:%M")} por {usuario_nombre}'
                
                documentos.append({
                    'id': traspaso.id,
                    'tipo': 'TRASPASO_INTERNO',
                    'tipo_display': 'Traspaso Interno',
                    'numero': f'TR-{traspaso.id}',
                    'fecha': traspaso.fecha_solicitud.strftime('%d/%m/%Y'),
                    'fecha_sort': traspaso.fecha_solicitud.strftime('%Y-%m-%d'),
                    'origen': traspaso.sucursal_origen.alias if traspaso.sucursal_origen else '',
                    'destino': traspaso.sucursal_destino.alias if traspaso.sucursal_destino else '',
                    'total_productos': productos_count,
                    'monto': 0,
                    'estado': traspaso.estado,
                    'ya_impreso': ya_impreso,
                    'veces_impreso': veces_impreso,
                    'ultima_impresion': ultima_impresion
                })
        
        # Ordenar por fecha descendente
        documentos.sort(key=lambda x: x['fecha_sort'], reverse=True)
        
        # Paginación
        total_documentos = len(documentos)
        inicio = (page - 1) * per_page
        fin = inicio + per_page
        documentos_paginados = documentos[inicio:fin]
        
        return JsonResponse({
            'success': True,
            'documentos': documentos_paginados,
            'pagination': {
                'current_page': page,
                'total_pages': (total_documentos + per_page - 1) // per_page,
                'total_items': total_documentos,
                'has_next': fin < total_documentos,
                'has_previous': page > 1
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener documentos: {str(e)}'
        })


@login_required
@require_GET
def obtener_productos_documento(request, tipo_documento, documento_id):
    """
    Obtener productos de un documento específico para generar etiquetas
    SIEMPRE usa precio de venta al público (precioventa del Producto)
    """
    try:
        sucursal_actual_id = request.session.get('idSucursalActual')
        sucursal_actual = Sucursal.objects.filter(id=sucursal_actual_id).first()
        
        productos = []
        documento_info = {}
        
        if tipo_documento in [MYSQL_TIPO_COMPRA, MYSQL_TIPO_TRASPASO]:
            documento_info, productos = _obtener_productos_documento_mysql(tipo_documento, documento_id)

        elif tipo_documento == 'DTE_COMPRA' or tipo_documento == 'DTE_TRASPASO':
            # Obtener DTE
            dte = get_object_or_404(Dte, id=documento_id)
            
            documento_info = {
                'tipo': tipo_documento,
                'numero': dte.numero_documento,
                'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                'proveedor': dte.emisor.razon_social if dte.emisor else '',
                'sucursal_destino': dte.sucursal.alias if dte.sucursal else ''
            }
            
            # Obtener productos del DTE
            for dte_prod in Dte_Productos.objects.filter(dte=dte).select_related(
                'productoTalla__producto'
            ):
                producto_talla = dte_prod.productoTalla
                producto = producto_talla.producto
                
                # Obtener datos del producto
                marca = producto.atributo1.valor if producto.atributo1 else ''
                color = producto.atributo2.valor if producto.atributo2 else ''
                
                # SIEMPRE usar precio de venta al público (precioventa del Producto)
                precio_venta = producto.precioventa
                
                productos.append({
                    'id': dte_prod.id,
                    'producto_talla_id': producto_talla.id,
                    'sku': str(producto_talla.sku),
                    'articulo': producto.articulo,
                    'descripcion': producto.descripcion if producto.descripcion else producto.articulo,
                    'marca': marca[:10] if marca else '',
                    'talla': str(producto_talla.talla) if producto_talla.talla else '',
                    'color': color[:10] if color else '',
                    'cantidad': dte_prod.stock,
                    'precio': float(precio_venta),  # PRECIO DE VENTA PÚBLICO
                    'sucursal': sucursal_actual.alias if sucursal_actual else '',
                    'factura': str(dte.numero_documento),
                    'seleccionado': True  # Por defecto seleccionados
                })
        
        elif tipo_documento == 'TRASPASO_INTERNO':
            # Obtener Traspaso
            traspaso = get_object_or_404(Traspaso, id=documento_id)
            
            documento_info = {
                'tipo': tipo_documento,
                'numero': f'TR-{traspaso.id}',
                'fecha': traspaso.fecha_solicitud.strftime('%d/%m/%Y'),
                'proveedor': traspaso.sucursal_origen.alias if traspaso.sucursal_origen else '',
                'sucursal_destino': traspaso.sucursal_destino.alias if traspaso.sucursal_destino else ''
            }
            
            # Obtener productos del traspaso
            for detalle in Traspaso_Detalle.objects.filter(
                traspaso=traspaso
            ).select_related('producto_talla__producto'):
                producto_talla = detalle.producto_talla
                producto = producto_talla.producto
                
                marca = producto.atributo1.valor if producto.atributo1 else ''
                color = producto.atributo2.valor if producto.atributo2 else ''
                
                # SIEMPRE usar precio de venta al público
                precio_venta = producto.precioventa
                
                productos.append({
                    'id': detalle.id,
                    'producto_talla_id': producto_talla.id,
                    'sku': str(producto_talla.sku),
                    'articulo': producto.articulo,
                    'descripcion': producto.descripcion if producto.descripcion else producto.articulo,
                    'marca': marca[:10] if marca else '',
                    'talla': str(producto_talla.talla) if producto_talla.talla else '',
                    'color': color[:10] if color else '',
                    'cantidad': detalle.cantidad_solicitada,
                    'precio': float(precio_venta),  # PRECIO DE VENTA PÚBLICO
                    'sucursal': traspaso.sucursal_destino.alias if traspaso.sucursal_destino else '',
                    'factura': f'TR-{traspaso.id}',
                    'seleccionado': True
                })
        
        # Verificar si este documento ya fue impreso antes
        historial_previo = HistorialImpresionEtiqueta.objects.filter(
            tipo_origen=tipo_documento,
            documento_id=documento_id,
            completado=True
        ).order_by('-fecha_impresion')
        
        impresiones_previas = []
        for hist in historial_previo[:5]:
            impresiones_previas.append({
                'fecha': hist.fecha_impresion.strftime('%d/%m/%Y %H:%M'),
                'usuario': hist.usuario.get_full_name() or hist.usuario.username if hist.usuario else 'Sistema',
                'total_etiquetas': hist.total_etiquetas
            })

        # Historial de impresión por SKU individual
        skus_impresos = {}
        historial_ids = list(historial_previo.values_list('id', flat=True))
        if historial_ids:
            for detalle in DetalleImpresionEtiqueta.objects.filter(
                historial_id__in=historial_ids
            ):
                sku = str(detalle.sku)
                if sku not in skus_impresos:
                    skus_impresos[sku] = {'veces': 0, 'etiquetas': 0}
                skus_impresos[sku]['veces'] += 1
                skus_impresos[sku]['etiquetas'] += detalle.cantidad_etiquetas

        # Anotar cada producto con su historial
        for prod in productos:
            info = skus_impresos.get(str(prod['sku']))
            prod['ya_impreso'] = info is not None
            prod['veces_impreso'] = info['veces'] if info else 0
            prod['etiquetas_impresas'] = info['etiquetas'] if info else 0

        # Calcular totales
        total_productos = len(productos)
        total_etiquetas = sum(p['cantidad'] for p in productos)
        
        return JsonResponse({
            'success': True,
            'documento': documento_info,
            'productos': productos,
            'resumen': {
                'total_productos': total_productos,
                'total_etiquetas': total_etiquetas
            },
            'impresiones_previas': impresiones_previas,
            'ya_impreso': len(impresiones_previas) > 0
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener productos: {str(e)}'
        })


# ========== BÚSQUEDA MANUAL DE PRODUCTOS ==========

@login_required
@require_GET  
def buscar_producto_etiqueta(request):
    """
    Buscar productos para generar etiquetas manualmente (sin documento)
    SIEMPRE usa precio de venta al público
    """
    try:
        termino = request.GET.get('termino', '').strip()
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        fuente = request.GET.get('fuente', 'postgres').strip().lower()
        
        if not termino or len(termino) < 2:
            return JsonResponse({
                'success': False,
                'error': 'Ingrese al menos 2 caracteres para buscar'
            })
        
        if fuente == 'mysql':
            productos_data = _buscar_producto_mysql(termino, sucursal_id=sucursal_id)
            return JsonResponse({
                'success': True,
                'productos': productos_data,
                'total': len(productos_data)
            })

        # Buscar por SKU, artículo o descripción en PostgreSQL
        productos = Producto_Talla.objects.filter(
            Q(sku__icontains=termino) |
            Q(producto__articulo__icontains=termino) |
            Q(producto__descripcion__icontains=termino)
        ).select_related(
            'producto', 
            'producto__atributo1', 
            'producto__atributo2'
        )[:30]
        
        sucursal = Sucursal.objects.filter(id=sucursal_id).first()
        
        productos_data = []
        for pt in productos:
            producto = pt.producto
            marca = producto.atributo1.valor if producto.atributo1 else ''
            color = producto.atributo2.valor if producto.atributo2 else ''
            
            # SIEMPRE usar precio de venta al público
            precio_venta = producto.precioventa
            
            productos_data.append({
                'id': pt.id,
                'producto_talla_id': pt.id,
                'sku': str(pt.sku),
                'articulo': producto.articulo,
                'descripcion': producto.descripcion if producto.descripcion else producto.articulo,
                'marca': marca[:10] if marca else '',
                'talla': str(pt.talla) if pt.talla else '',
                'color': color[:10] if color else '',
                'precio': float(precio_venta),  # PRECIO DE VENTA PÚBLICO
                'sucursal': sucursal.alias if sucursal else '',
                'cantidad': 1,  # Por defecto 1 etiqueta
                'seleccionado': True
            })
        
        return JsonResponse({
            'success': True,
            'productos': productos_data,
            'total': len(productos_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en búsqueda: {str(e)}'
        })


# ========== GENERACIÓN DE ETIQUETAS ==========

@login_required
@require_POST
@transaction.atomic
def generar_datos_etiquetas(request):
    """
    Generar datos de etiquetas en formato JSON para enviar a Zebra Browser Print
    Registra la impresión en el historial
    """
    try:
        data = json.loads(request.body)
        
        productos_seleccionados = data.get('productos', [])
        fecha_impresion = data.get('fecha_impresion', timezone.now().strftime('%d-%m-%Y'))
        numero_documento = data.get('numero_documento', '')
        sucursal_nombre = data.get('sucursal', '')
        tipo_origen = data.get('tipo_origen', 'MANUAL')
        documento_id = data.get('documento_id')
        registrar_historial = data.get('registrar_historial', True)
        
        if not productos_seleccionados:
            return JsonResponse({
                'success': False,
                'error': 'No hay productos seleccionados para generar etiquetas'
            })
        
        etiquetas = []
        detalles_historial = []
        
        for item in productos_seleccionados:
            cantidad = int(item.get('cantidad_etiquetas', item.get('cantidad', 1)))
            
            # Formatear precio
            precio = item.get('precio', 0)
            try:
                precio_int = int(float(precio))
                precio_formateado = f"{precio_int:,}".replace(',', '.')
            except:
                precio_formateado = str(precio)
            
            # Guardar para historial
            detalles_historial.append({
                'producto_talla_id': item.get('producto_talla_id'),
                'sku': str(item.get('sku', '')),
                'articulo': item.get('articulo', ''),
                'descripcion': item.get('descripcion', ''),
                'marca': item.get('marca', ''),
                'talla': str(item.get('talla', '')),
                'color': item.get('color', ''),
                'precio_impreso': int(float(precio)),
                'cantidad_etiquetas': cantidad
            })
            
            # Generar etiquetas según la cantidad
            for i in range(cantidad):
                etiquetas.append({
                    'sucursal': (item.get('sucursal', sucursal_nombre) or '')[:8],
                    'desc': (item.get('descripcion', item.get('articulo', '')) or '')[:28],
                    'marca': (item.get('marca', '') or '')[:10],
                    'sku': str(item.get('sku', ''))[:10],
                    'art': (item.get('articulo', '') or '')[:14],
                    'precio': precio_formateado[:10],
                    'factura': str(numero_documento)[:8],
                    'talla': str(item.get('talla', ''))[:4],
                    'color': (item.get('color', '') or '')[:10],
                    'fecha': fecha_impresion
                })
        
        # Registrar en historial
        historial_id = None
        if registrar_historial:
            sucursal_actual_id = request.session.get('idSucursalActual')
            
            historial = HistorialImpresionEtiqueta.objects.create(
                tipo_origen=tipo_origen,
                documento_id=documento_id,
                numero_documento=numero_documento,
                sucursal_id=sucursal_actual_id,
                usuario=request.user,
                total_productos=len(detalles_historial),
                total_etiquetas=len(etiquetas),
                completado=True
            )
            historial_id = historial.id
            
            # Guardar detalles
            for det in detalles_historial:
                DetalleImpresionEtiqueta.objects.create(
                    historial=historial,
                    producto_talla_id=det['producto_talla_id'],
                    sku=det['sku'],
                    articulo=det['articulo'],
                    descripcion=det['descripcion'],
                    marca=det['marca'],
                    talla=det['talla'],
                    color=det['color'],
                    precio_impreso=det['precio_impreso'],
                    cantidad_etiquetas=det['cantidad_etiquetas']
                )
        
        # Validaciones y alertas
        alertas = []
        
        if len(etiquetas) > 500:
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'Se generarán {len(etiquetas)} etiquetas. Esto puede tomar varios minutos.'
            })
        
        # Verificar etiquetas sin SKU
        sin_sku = sum(1 for e in etiquetas if not e['sku'])
        if sin_sku > 0:
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'{sin_sku} etiquetas no tienen código SKU y no generarán código de barras.'
            })
        
        # Verificar etiquetas sin precio
        sin_precio = sum(1 for e in etiquetas if not e['precio'] or e['precio'] == '0')
        if sin_precio > 0:
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'{sin_precio} etiquetas no tienen precio definido.'
            })
        
        return JsonResponse({
            'success': True,
            'etiquetas': etiquetas,
            'total_etiquetas': len(etiquetas),
            'total_filas': (len(etiquetas) + 1) // 2,
            'alertas': alertas,
            'historial_id': historial_id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar datos de etiquetas: {str(e)}'
        })


# ========== HISTORIAL ==========

@login_required
@require_GET
def obtener_historial_impresiones(request):
    """Obtener historial de impresiones de etiquetas"""
    try:
        empresa_actual_id = request.session.get('idEmpresaActual')
        sucursal_id = request.GET.get('sucursal_id')
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        queryset = HistorialImpresionEtiqueta.objects.filter(
            sucursal__empresa_id=empresa_actual_id
        ).select_related('sucursal', 'usuario')
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        queryset = queryset.order_by('-fecha_impresion')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(page)
        
        historial_data = []
        for hist in page_obj:
            historial_data.append({
                'id': hist.id,
                'fecha': hist.fecha_impresion.strftime('%d/%m/%Y %H:%M'),
                'tipo_origen': hist.tipo_origen,
                'numero_documento': hist.numero_documento,
                'sucursal': hist.sucursal.alias if hist.sucursal else '',
                'usuario': hist.usuario.username if hist.usuario else 'Sistema',
                'total_productos': hist.total_productos,
                'total_etiquetas': hist.total_etiquetas,
                'completado': hist.completado
            })
        
        return JsonResponse({
            'success': True,
            'historial': historial_data,
            'pagination': {
                'current_page': page_obj.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous()
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener historial: {str(e)}'
        })


@login_required
@require_GET
def obtener_detalle_impresion(request, historial_id):
    """Obtener detalle de una impresión específica"""
    try:
        historial = get_object_or_404(HistorialImpresionEtiqueta, id=historial_id)
        
        detalles = []
        for det in historial.detalles.all():
            detalles.append({
                'sku': det.sku,
                'articulo': det.articulo,
                'marca': det.marca,
                'talla': det.talla,
                'color': det.color,
                'precio_impreso': det.precio_impreso,
                'cantidad_etiquetas': det.cantidad_etiquetas
            })
        
        return JsonResponse({
            'success': True,
            'historial': {
                'id': historial.id,
                'fecha': historial.fecha_impresion.strftime('%d/%m/%Y %H:%M'),
                'tipo_origen': historial.tipo_origen,
                'numero_documento': historial.numero_documento,
                'total_productos': historial.total_productos,
                'total_etiquetas': historial.total_etiquetas,
                'usuario': historial.usuario.username if historial.usuario else 'Sistema'
            },
            'detalles': detalles
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener detalle: {str(e)}'
        })


@login_required
@require_GET
def obtener_skus_articulo(request):
    """
    Dado un código de artículo, devuelve TODOS los SKUs (Producto_Talla)
    asociados, para poder imprimir etiquetas del artículo completo.
    """
    try:
        articulo = request.GET.get('articulo', '').strip()
        fuente = request.GET.get('fuente', 'postgres').strip().lower()
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')

        if not articulo:
            return JsonResponse({'success': False, 'error': 'Artículo requerido'})

        if fuente == 'mysql':
            alias_sucursal = _obtener_alias_sucursal(sucursal_id)
            filtros = ['t.articulo = %s']
            params = [articulo]
            if alias_sucursal:
                filtros.append('t.alias = %s')
                params.append(alias_sucursal)

            sql = f"""
                SELECT t.codigo_asociado, t.articulo, t.descripcion, t.marca,
                       t.color, t.size, t.precioventapublico, t.alias
                FROM talla t
                WHERE {' AND '.join(filtros)}
                ORDER BY t.size ASC
            """
            with _get_mysql_connection() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                cursor.close()

            productos_data = []
            for row in rows:
                precio = _safe_int(row.get('precioventapublico'))
                art = (row.get('articulo') or '').strip()
                desc = (row.get('descripcion') or art).strip()
                productos_data.append({
                    'id': row.get('codigo_asociado'),
                    'producto_talla_id': None,
                    'sku': str(row.get('codigo_asociado') or ''),
                    'articulo': art[:50],
                    'descripcion': desc,
                    'marca': (row.get('marca') or '')[:10],
                    'talla': str(row.get('size') or '')[:4],
                    'color': (row.get('color') or '')[:10],
                    'precio': float(precio),
                    'sucursal': row.get('alias') or '',
                    'cantidad': 1,
                    'seleccionado': True
                })
            return JsonResponse({'success': True, 'productos': productos_data, 'total': len(productos_data)})

        producto_tallas = Producto_Talla.objects.filter(
            producto__articulo=articulo
        ).select_related('producto', 'producto__atributo1', 'producto__atributo2').order_by('talla')

        sucursal = Sucursal.objects.filter(id=sucursal_id).first()

        productos_data = []
        for pt in producto_tallas:
            producto = pt.producto
            marca = producto.atributo1.valor if producto.atributo1 else ''
            color = producto.atributo2.valor if producto.atributo2 else ''

            productos_data.append({
                'id': pt.id,
                'producto_talla_id': pt.id,
                'sku': str(pt.sku),
                'articulo': producto.articulo,
                'descripcion': producto.descripcion or producto.articulo,
                'marca': marca[:10] if marca else '',
                'talla': str(pt.talla) if pt.talla else '',
                'color': color[:10] if color else '',
                'precio': float(producto.precioventa),
                'sucursal': sucursal.alias if sucursal else '',
                'cantidad': 1,
                'seleccionado': True
            })

        return JsonResponse({'success': True, 'productos': productos_data, 'total': len(productos_data)})

    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error al obtener SKUs del artículo: {str(e)}'})


@login_required
@require_GET
def obtener_sucursales_usuario(request):
    """Obtener sucursales disponibles para el usuario actual"""
    try:
        empresa_actual_id = request.session.get('idEmpresaActual')
        
        sucursales = Sucursal.objects.filter(
            empresa_id=empresa_actual_id
        ).order_by('alias')
        
        sucursales_data = [{
            'id': s.id,
            'nombre': s.alias,
            'direccion': s.direccion or ''
        } for s in sucursales]
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener sucursales: {str(e)}'
        })
