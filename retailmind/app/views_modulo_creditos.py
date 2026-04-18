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
import re
from decimal import Decimal

from .models import (
    CreditoTrabajador, PagoCreditoTrabajador, FirmaCreditoTrabajador,
    Cliente, Empresa, Sucursal, EmpresaUser,
    ESTADO_CREDITO_CHOICES, TIPO_CREDITO_CHOICES, TIPO_BENEFICIARIO_CHOICES,
    METODO_PAGO_TICKET_CHOICES,
)


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


# ========== GESTIÓN DE CRÉDITOS ==========

@login_required
def gestion_creditos(request):
    """Vista principal para gestión de créditos a trabajadores"""
    sucursal_id = request.session.get('idSucursalActual')
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


@login_required
@require_POST
def cargar_creditos_trabajadores(request):
    """Cargar créditos con filtros y paginación"""
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

        data = json.loads(request.body)
        
        # Parámetros de filtro
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        estado = data.get('estado')
        trabajador_id = data.get('trabajador_id')
        tipo_credito = data.get('tipo_credito')
        numero_credito = data.get('numero_credito')
        trabajador_texto = data.get('trabajador_texto')
        sucursal_texto = data.get('sucursal_texto')
        saldo_min = data.get('saldo_min')
        saldo_max = data.get('saldo_max')
        
        # Parámetros de paginación
        page = int(data.get('page', 1))
        per_page = int(data.get('per_page', 20))
        
        # Obtener empresa actual
        empresa_actual_id = request.session.get('idEmpresaActual')
        if not empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa en la sesión'
            }, status=400)
        
        # Construir queryset base
        queryset = CreditoTrabajador.objects.filter(
            empresa_origen_id=empresa_actual_id
        ).select_related('beneficiario', 'empresa_origen', 'sucursal', 'autorizado_por', 'solicitado_por')
        
        # Aplicar filtros de fecha (DD/MM/YYYY o DD-MM-YYYY)
        fecha_inicio = normalize_fecha(fecha_inicio)
        fecha_fin = normalize_fecha(fecha_fin)
        if fecha_inicio:
            queryset = queryset.filter(fecha_solicitud__date__gte=fecha_inicio)
        if fecha_fin:
            queryset = queryset.filter(fecha_solicitud__date__lte=fecha_fin)
        
        if estado:
            queryset = queryset.filter(estado=estado)
        
        if trabajador_id:
            queryset = queryset.filter(beneficiario_id=trabajador_id)
        
        if tipo_credito:
            queryset = queryset.filter(tipo_credito=tipo_credito)
        
        if numero_credito:
            queryset = queryset.filter(numero_credito__icontains=numero_credito)

        if trabajador_texto:
            texto = trabajador_texto.strip()
            queryset = queryset.filter(
                Q(beneficiario__nombre__icontains=texto) |
                Q(beneficiario__apellido__icontains=texto) |
                Q(beneficiario__rut__icontains=texto)
            )

        if sucursal_texto:
            texto = sucursal_texto.strip()
            queryset = queryset.filter(
                Q(sucursal__alias__icontains=texto) |
                Q(sucursal__direccion__icontains=texto)
            )

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
        
        # Ordenar por fecha descendente
        queryset = queryset.order_by('-fecha_solicitud')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        creditos_page = paginator.get_page(page)
        
        # Serializar datos
        creditos_data = []
        for credito in creditos_page:
            # Obtener información del último pago si existe
            ultimo_pago = credito.pagos.select_related('sucursal_cobro', 'registrado_por').order_by('-fecha_pago').first()
            
            # Datos del pago
            pago_info = None
            if ultimo_pago:
                # Intentar extraer número de boleta de observaciones o referencia
                numero_boleta = ''
                if ultimo_pago.referencia_pago:
                    numero_boleta = ultimo_pago.referencia_pago
                elif ultimo_pago.observaciones:
                    # Buscar patrón de ticket en observaciones
                    import re
                    match = re.search(r'Ticket\s*#?(\d+)', ultimo_pago.observaciones)
                    if match:
                        numero_boleta = match.group(1)
                
                pago_info = {
                    'sucursal_cobro': ultimo_pago.sucursal_cobro.alias if ultimo_pago.sucursal_cobro else '',
                    'numero_boleta': numero_boleta,
                    'fecha_pago': ultimo_pago.fecha_pago.strftime('%d/%m/%Y'),
                    'monto_pago': float(ultimo_pago.monto_pago),
                    'metodo_pago': ultimo_pago.get_metodo_pago_display(),
                    'registrado_por': ultimo_pago.registrado_por.username if ultimo_pago.registrado_por else ''
                }
            
            creditos_data.append({
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
                'ultimo_pago': pago_info
            })
        
        return JsonResponse({
            'success': True,
            'creditos': creditos_data,
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
        
        # Verificar permisos
        empresa_actual_id = request.session.get('idEmpresaActual')
        if credito.empresa_origen_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver este crédito'
            }, status=403)
        
        # Obtener pagos del crédito
        pagos = []
        for pago in credito.pagos.select_related('sucursal_cobro', 'registrado_por').all():
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
        
        credito_data = {
            'id': credito.id,
            'numero_credito': credito.numero_credito,
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
        empresa_actual_id = request.session.get('idEmpresaActual')
        if credito.empresa_origen_id != empresa_actual_id:
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
        empresa_actual_id = request.session.get('idEmpresaActual')
        if credito.empresa_origen_id != empresa_actual_id:
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
        empresa_actual_id = request.session.get('idEmpresaActual')
        if credito.empresa_origen_id != empresa_actual_id:
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
        empresa_actual_id = request.session.get('idEmpresaActual')
        if credito.empresa_origen_id != empresa_actual_id:
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
        empresa_actual_id = request.session.get('idEmpresaActual')
        if credito.empresa_origen_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para registrar pagos en este crédito'
            }, status=403)
        
        # Verificar estado del crédito
        if credito.estado not in ['ACTIVO', 'APROBADO']:
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden registrar pagos en créditos activos o aprobados'
            }, status=400)
        
        # Validar monto
        try:
            monto_pago = Decimal(str(monto_pago))
            if monto_pago <= 0:
                raise ValueError("El monto debe ser mayor a 0")
            
            # Verificar que no exceda el saldo pendiente
            if monto_pago > credito.saldo_pendiente:
                return JsonResponse({
                    'success': False,
                    'error': f'El monto no puede exceder el saldo pendiente (${credito.saldo_pendiente:,})'
                }, status=400)
                
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Monto inválido'
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
            'message': 'Pago registrado exitosamente',
            'pago_id': pago.id,
            'numero_pago': pago.numero_pago,
            'nuevo_saldo': credito.saldo_pendiente,
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
        empresa_actual_id = request.session.get('idEmpresaActual')
        if credito.empresa_origen_id != empresa_actual_id:
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
        # Obtener empresa actual
        empresa_actual_id = request.session.get('idEmpresaActual')
        if not empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa en la sesión'
            }, status=400)
        
        fecha_inicio = normalize_fecha(request.GET.get('fecha_inicio'))
        fecha_fin = normalize_fecha(request.GET.get('fecha_fin'))

        # Estadísticas generales
        creditos = CreditoTrabajador.objects.filter(empresa_origen_id=empresa_actual_id)

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
        
        # Estadísticas por estado
        stats_por_estado = {}
        for estado, display in ESTADO_CREDITO_CHOICES:
            count = creditos.filter(estado=estado).count()
            stats_por_estado[estado] = {
                'count': count,
                'display': display
            }
        
        # Créditos vencidos
        creditos_vencidos = []
        for credito in creditos.filter(estado__in=['ACTIVO', 'APROBADO']):
            if credito.esta_vencido:
                creditos_vencidos.append({
                    'id': credito.id,
                    'numero_credito': credito.numero_credito,
                    'trabajador': credito.nombre_beneficiario,
                    'monto_pendiente': credito.saldo_pendiente,
                    'dias_vencido': abs(credito.dias_para_vencimiento),
                    'fecha_vencimiento': credito.fecha_vencimiento.strftime('%d/%m/%Y')
                })
        
        from django.db.models import Count
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
                'tipo_credito': credito.get_tipo_credito_display()
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
        
        # Registrar el uso del crédito
        with transaction.atomic():
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
