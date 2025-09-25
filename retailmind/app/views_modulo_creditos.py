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
from django.core.paginator import Paginator
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import re
from decimal import Decimal

from .models import (
    CreditoTrabajador, PagoCreditoTrabajador, FirmaCreditoTrabajador,
    Vendedor, Empresa, Sucursal, EmpresaUser,
    ESTADO_CREDITO_CHOICES, TIPO_CREDITO_CHOICES, METODO_PAGO_TICKET_CHOICES
)


# ========== GESTIÓN DE CRÉDITOS ==========

@login_required
def gestion_creditos(request):
    """Vista principal para gestión de créditos a trabajadores"""
    return render(request, 'vistas/modulo_administracion/gestion_creditos.html')


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
        
        # Validar trabajador
        trabajador = get_object_or_404(Vendedor, id=trabajador_id)
        empresa = get_object_or_404(Empresa, id=empresa_actual_id)
        sucursal = get_object_or_404(Sucursal, id=sucursal_actual_id)
        
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
        
        # Crear crédito
        credito = CreditoTrabajador.objects.create(
            trabajador=trabajador,
            empresa_origen=empresa,
            sucursal=sucursal,
            tipo_credito=tipo_credito,
            monto_solicitado=monto_solicitado,
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
            fecha_primer_pago=data.get('fecha_primer_pago')
        )
        
        # Crear registro de firma
        FirmaCreditoTrabajador.objects.create(credito=credito)
        
        return JsonResponse({
            'success': True,
            'message': 'Crédito creado exitosamente',
            'credito_id': credito.id,
            'numero_credito': credito.numero_credito
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
        data = json.loads(request.body)
        
        # Parámetros de filtro
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        estado = data.get('estado')
        trabajador_id = data.get('trabajador_id')
        tipo_credito = data.get('tipo_credito')
        numero_credito = data.get('numero_credito')
        
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
        ).select_related('trabajador', 'empresa_origen', 'sucursal', 'autorizado_por', 'solicitado_por')
        
        # Aplicar filtros
        if fecha_inicio:
            queryset = queryset.filter(fecha_solicitud__date__gte=fecha_inicio)
        
        if fecha_fin:
            queryset = queryset.filter(fecha_solicitud__date__lte=fecha_fin)
        
        if estado:
            queryset = queryset.filter(estado=estado)
        
        if trabajador_id:
            queryset = queryset.filter(trabajador_id=trabajador_id)
        
        if tipo_credito:
            queryset = queryset.filter(tipo_credito=tipo_credito)
        
        if numero_credito:
            queryset = queryset.filter(numero_credito__icontains=numero_credito)
        
        # Ordenar por fecha descendente
        queryset = queryset.order_by('-fecha_solicitud')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        creditos_page = paginator.get_page(page)
        
        # Serializar datos
        creditos_data = []
        for credito in creditos_page:
            creditos_data.append({
                'id': credito.id,
                'numero_credito': credito.numero_credito,
                'trabajador': {
                    'id': credito.trabajador.id,
                    'nombre': credito.trabajador.nombre,
                    'rut': credito.trabajador.rut,
                    'codigo_vendedor': credito.trabajador.codigo_vendedor
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
                'esta_vencido': credito.esta_vencido,
                'dias_para_vencimiento': credito.dias_para_vencimiento,
                'numero_cuotas': credito.numero_cuotas,
                'valor_cuota': float(credito.valor_cuota) if credito.valor_cuota else None,
                'tasa_interes': float(credito.tasa_interes),
                'requiere_aval': credito.requiere_aval
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
        for pago in credito.pagos.all():
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
            'trabajador': {
                'id': credito.trabajador.id,
                'nombre': credito.trabajador.nombre,
                'rut': credito.trabajador.rut,
                'codigo_vendedor': credito.trabajador.codigo_vendedor,
                'correo': credito.trabajador.correo
            },
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
            registrado_por=request.user
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
    """Obtener lista de trabajadores/vendedores para créditos"""
    try:
        # Obtener vendedores activos
        vendedores = Vendedor.objects.filter(
            nombre__isnull=False
        ).exclude(nombre='').order_by('nombre')
        
        vendedores_data = []
        for vendedor in vendedores:
            # Calcular créditos activos
            creditos_activos = CreditoTrabajador.objects.filter(
                trabajador=vendedor,
                estado__in=['ACTIVO', 'APROBADO']
            ).count()
            
            vendedores_data.append({
                'id': vendedor.id,
                'nombre': vendedor.nombre,
                'rut': vendedor.rut or '',
                'codigo_vendedor': vendedor.codigo_vendedor,
                'correo': vendedor.correo or '',
                'creditos_activos': creditos_activos
            })
        
        return JsonResponse({
            'success': True,
            'trabajadores': vendedores_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener trabajadores: {str(e)}'
        })


@login_required
@require_GET
def reporte_creditos_trabajadores(request):
    """Generar reporte de créditos de trabajadores"""
    try:
        # Obtener empresa actual
        empresa_actual_id = request.session.get('idEmpresaActual')
        if not empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa en la sesión'
            }, status=400)
        
        # Estadísticas generales
        creditos = CreditoTrabajador.objects.filter(empresa_origen_id=empresa_actual_id)
        
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
                    'trabajador': credito.trabajador.nombre,
                    'monto_pendiente': credito.saldo_pendiente,
                    'dias_vencido': abs(credito.dias_para_vencimiento),
                    'fecha_vencimiento': credito.fecha_vencimiento.strftime('%d/%m/%Y')
                })
        
        # Top trabajadores con más créditos
        from django.db.models import Count
        top_trabajadores = creditos.values(
            'trabajador__nombre', 'trabajador__id'
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
                    'trabajador_id': item['trabajador__id'],
                    'trabajador': item['trabajador__nombre'],
                    'total_creditos': item['total_creditos'],
                    'total_monto': float(item['total_monto'] or 0)
                }
                for item in top_trabajadores
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
