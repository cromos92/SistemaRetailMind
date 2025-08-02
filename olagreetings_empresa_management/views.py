from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
import json
import re
import csv
from datetime import datetime, timedelta

from .models import Empresa, Sucursal, ContactoEmpresa, Cliente, Proveedor, LogEmpresa

# ========== FUNCIONES DE VALIDACIÓN ==========

def validar_rut_chileno(rut):
    """Valida un RUT chileno"""
    try:
        rut_limpio = re.sub(r'[.-]', '', rut.upper())
        if not re.match(r'^\d{7,8}[0-9K]$', rut_limpio):
            return False, "El RUT debe tener 7 u 8 dígitos seguidos de un dígito verificador"
        
        numero = rut_limpio[:-1]
        dv = rut_limpio[-1]
        
        suma = 0
        multiplicador = 2
        for digito in reversed(numero):
            suma += int(digito) * multiplicador
            multiplicador = multiplicador + 1 if multiplicador < 7 else 2
        
        resto = suma % 11
        dv_esperado = 11 - resto if resto != 0 else 0
        
        if dv_esperado == 10:
            dv_esperado = 'K'
        else:
            dv_esperado = str(dv_esperado)
        
        if dv == dv_esperado:
            return True, ""
        else:
            return False, f"El dígito verificador es incorrecto. Debería ser {dv_esperado}"
            
    except Exception as e:
        return False, f"Error al validar RUT: {str(e)}"

def validar_campos_empresa(data):
    """Valida los campos de una empresa"""
    errores = []
    
    campos_obligatorios = ['nombre', 'rut']
    for campo in campos_obligatorios:
        if not data.get(campo) or str(data.get(campo)).strip() == '':
            errores.append(f"El campo '{campo.replace('_', ' ').title()}' es obligatorio")
    
    # Validar RUT
    if data.get('rut'):
        rut_valido, mensaje_rut = validar_rut_chileno(data['rut'])
        if not rut_valido:
            errores.append(f"RUT inválido: {mensaje_rut}")
    
    # Validar emails
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    emails = ['correo_vendedor', 'correo_intercambio', 'correo_administrador', 'correo_facturacion', 'email_contacto']
    for email_field in emails:
        if data.get(email_field) and not re.match(email_regex, data[email_field]):
            errores.append(f"El correo '{email_field.replace('_', ' ').title()}' no tiene un formato válido")
    
    return len(errores) == 0, errores

def registrar_log_empresa(request, empresa, tipo_accion, campo_modificado="", valor_anterior="", valor_nuevo=""):
    """Registra un log de cambio en empresa"""
    try:
        LogEmpresa.objects.create(
            empresa=empresa,
            usuario=request.user if request.user.is_authenticated else None,
            tipo_accion=tipo_accion,
            campo_modificado=campo_modificado,
            valor_anterior=valor_anterior,
            valor_nuevo=valor_nuevo,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
    except Exception as e:
        print(f"Error al registrar log de empresa: {e}")

# ========== VISTAS PRINCIPALES ==========

@login_required
def gestion_empresas(request):
    """Vista principal para gestión de empresas"""
    return render(request, 'empresas/gestion_empresas.html')

@require_GET
@login_required
def listar_empresas(request):
    """Obtener lista de empresas con filtros y paginación"""
    try:
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))
        search = request.GET.get('search', '').strip()
        tipo_empresa = request.GET.get('tipo_empresa', '')
        estado = request.GET.get('estado', '')
        
        empresas = Empresa.objects.all()
        
        # Aplicar filtros
        if search:
            empresas = empresas.filter(
                Q(nombre__icontains=search) |
                Q(rut__icontains=search) |
                Q(nombre_fantasia__icontains=search) |
                Q(razon_social__icontains=search) |
                Q(giro__icontains=search) |
                Q(ciudad__icontains=search)
            )
        
        if tipo_empresa:
            empresas = empresas.filter(tipo_empresa=tipo_empresa)
        
        if estado == 'activa':
            empresas = empresas.filter(es_activa=True)
        elif estado == 'inactiva':
            empresas = empresas.filter(es_activa=False)
        
        empresas = empresas.order_by('nombre')
        
        # Paginación
        paginator = Paginator(empresas, page_size)
        empresas_page = paginator.get_page(page)
        
        # Preparar datos
        empresas_data = []
        for empresa in empresas_page:
            empresas_data.append({
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut,
                'nombre_fantasia': empresa.nombre_fantasia,
                'tipo_empresa': empresa.get_tipo_empresa_display(),
                'ciudad': empresa.ciudad,
                'telefono': empresa.telefono,
                'es_activa': empresa.es_activa,
                'fecha_creacion': empresa.fecha_creacion.strftime('%d/%m/%Y'),
                'contacto_principal': empresa.contacto_principal,
                'email_contacto': empresa.email_contacto,
                'sucursales_count': empresa.sucursales.count(),
                'contactos_count': empresa.contactos.count(),
            })
        
        # Calcular métricas
        total_empresas = empresas.count()
        empresas_activas = empresas.filter(es_activa=True).count()
        clientes = empresas.filter(tipo_empresa__in=['CLIENTE', 'CLIENTE_PROVEEDOR']).count()
        proveedores = empresas.filter(tipo_empresa__in=['PROVEEDOR', 'CLIENTE_PROVEEDOR']).count()
        
        return JsonResponse({
            'success': True,
            'empresas': empresas_data,
            'pagination': {
                'page': page,
                'total_pages': paginator.num_pages,
                'total_count': paginator.count,
                'has_previous': empresas_page.has_previous(),
                'has_next': empresas_page.has_next(),
            },
            'metricas': {
                'total_empresas': total_empresas,
                'empresas_activas': empresas_activas,
                'clientes': clientes,
                'proveedores': proveedores
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_POST
@login_required
@csrf_exempt
@transaction.atomic
def crear_empresa(request):
    """Crear nueva empresa"""
    try:
        data = json.loads(request.body)
        
        # Validar campos
        es_valido, errores = validar_campos_empresa(data)
        if not es_valido:
            return JsonResponse({
                'success': False,
                'error': ' | '.join(errores)
            }, status=400)
        
        # Verificar RUT único
        if Empresa.objects.filter(rut=data['rut']).exists():
            return JsonResponse({
                'success': False,
                'error': 'Ya existe una empresa con ese RUT'
            }, status=400)
        
        # Crear empresa
        empresa = Empresa.objects.create(
            nombre=data['nombre'].strip(),
            rut=data['rut'].strip(),
            nombre_fantasia=data.get('nombre_fantasia', '').strip(),
            razon_social=data.get('razon_social', '').strip(),
            giro=data.get('giro', '').strip(),
            direccion=data.get('direccion', '').strip(),
            comuna=data.get('comuna', '').strip(),
            ciudad=data.get('ciudad', '').strip(),
            region=data.get('region', '').strip(),
            codigo_postal=data.get('codigo_postal', '').strip(),
            telefono=data.get('telefono', '').strip(),
            fax=data.get('fax', '').strip(),
            sitio_web=data.get('sitio_web', '').strip(),
            correo_vendedor=data.get('correo_vendedor', '').strip(),
            correo_intercambio=data.get('correo_intercambio', '').strip(),
            correo_administrador=data.get('correo_administrador', '').strip(),
            correo_facturacion=data.get('correo_facturacion', '').strip(),
            tipo_empresa=data.get('tipo_empresa', 'CLIENTE'),
            es_activa=data.get('es_activa', True),
            observaciones=data.get('observaciones', '').strip(),
            notas_internas=data.get('notas_internas', '').strip(),
            condicion_pago=data.get('condicion_pago', '').strip(),
            dias_credito=data.get('dias_credito', 0),
            limite_credito=data.get('limite_credito', 0),
            contacto_principal=data.get('contacto_principal', '').strip(),
            cargo_contacto=data.get('cargo_contacto', '').strip(),
            telefono_contacto=data.get('telefono_contacto', '').strip(),
            email_contacto=data.get('email_contacto', '').strip(),
        )
        
        # Registrar log
        registrar_log_empresa(request, empresa, 'CREAR')
        
        return JsonResponse({
            'success': True,
            'message': f'Empresa {empresa.nombre} creada exitosamente',
            'empresa_id': empresa.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_http_methods(["GET", "PUT", "DELETE"])
@login_required
@csrf_exempt
@transaction.atomic
def gestionar_empresa(request, empresa_id):
    """Obtener, editar o eliminar una empresa específica"""
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        if request.method == 'GET':
            return JsonResponse({
                'success': True,
                'empresa': {
                    'id': empresa.id,
                    'nombre': empresa.nombre,
                    'rut': empresa.rut,
                    'nombre_fantasia': empresa.nombre_fantasia,
                    'razon_social': empresa.razon_social,
                    'giro': empresa.giro,
                    'direccion': empresa.direccion,
                    'comuna': empresa.comuna,
                    'ciudad': empresa.ciudad,
                    'region': empresa.region,
                    'codigo_postal': empresa.codigo_postal,
                    'telefono': empresa.telefono,
                    'fax': empresa.fax,
                    'sitio_web': empresa.sitio_web,
                    'correo_vendedor': empresa.correo_vendedor,
                    'correo_intercambio': empresa.correo_intercambio,
                    'correo_administrador': empresa.correo_administrador,
                    'correo_facturacion': empresa.correo_facturacion,
                    'tipo_empresa': empresa.tipo_empresa,
                    'es_activa': empresa.es_activa,
                    'observaciones': empresa.observaciones,
                    'notas_internas': empresa.notas_internas,
                    'condicion_pago': empresa.condicion_pago,
                    'dias_credito': empresa.dias_credito,
                    'limite_credito': float(empresa.limite_credito),
                    'contacto_principal': empresa.contacto_principal,
                    'cargo_contacto': empresa.cargo_contacto,
                    'telefono_contacto': empresa.telefono_contacto,
                    'email_contacto': empresa.email_contacto,
                }
            })
        
        elif request.method == 'PUT':
            data = json.loads(request.body)
            
            # Validar campos
            es_valido, errores = validar_campos_empresa(data)
            if not es_valido:
                return JsonResponse({
                    'success': False,
                    'error': ' | '.join(errores)
                }, status=400)
            
            # Verificar RUT único (excluyendo la empresa actual)
            if Empresa.objects.filter(rut=data['rut']).exclude(id=empresa_id).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'Ya existe otra empresa con ese RUT'
                }, status=400)
            
            # Actualizar empresa
            campos_actualizables = [
                'nombre', 'rut', 'nombre_fantasia', 'razon_social', 'giro',
                'direccion', 'comuna', 'ciudad', 'region', 'codigo_postal',
                'telefono', 'fax', 'sitio_web', 'correo_vendedor', 'correo_intercambio',
                'correo_administrador', 'correo_facturacion', 'tipo_empresa', 'es_activa',
                'observaciones', 'notas_internas', 'condicion_pago', 'dias_credito',
                'limite_credito', 'contacto_principal', 'cargo_contacto',
                'telefono_contacto', 'email_contacto'
            ]
            
            for campo in campos_actualizables:
                if campo in data:
                    setattr(empresa, campo, data[campo])
            
            empresa.save()
            
            # Registrar log
            registrar_log_empresa(request, empresa, 'EDITAR')
            
            return JsonResponse({
                'success': True,
                'message': f'Empresa {empresa.nombre} actualizada exitosamente'
            })
        
        elif request.method == 'DELETE':
            # Verificar si tiene relaciones
            if empresa.sucursales.exists():
                return JsonResponse({
                    'success': False,
                    'error': 'No se puede eliminar la empresa porque tiene sucursales asociadas'
                }, status=400)
            
            if empresa.contactos.exists():
                return JsonResponse({
                    'success': False,
                    'error': 'No se puede eliminar la empresa porque tiene contactos asociados'
                }, status=400)
            
            nombre_empresa = empresa.nombre
            empresa.delete()
            
            # Registrar log
            registrar_log_empresa(request, empresa, 'ELIMINAR')
            
            return JsonResponse({
                'success': True,
                'message': f'Empresa {nombre_empresa} eliminada exitosamente'
            })
    
    except Empresa.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Empresa no encontrada'
        }, status=404)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_POST
@login_required
@csrf_exempt
def activar_desactivar_empresa(request, empresa_id):
    """Activar o desactivar una empresa"""
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        empresa.es_activa = not empresa.es_activa
        empresa.save()
        
        estado = "activada" if empresa.es_activa else "desactivada"
        
        # Registrar log
        accion = 'ACTIVAR' if empresa.es_activa else 'DESACTIVAR'
        registrar_log_empresa(request, empresa, accion, 'es_activa', str(not empresa.es_activa), str(empresa.es_activa))
        
        return JsonResponse({
            'success': True,
            'message': f'Empresa {empresa.nombre} {estado} exitosamente',
            'es_activa': empresa.es_activa
        })
        
    except Empresa.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Empresa no encontrada'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_GET
@login_required
def exportar_empresas(request):
    """Exportar lista de empresas a CSV"""
    try:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="empresas_olagreetings.csv"'
        
        response.write('\ufeff')  # BOM para UTF-8
        
        writer = csv.writer(response)
        writer.writerow([
            'ID', 'Nombre', 'RUT', 'Nombre Fantasía', 'Razón Social', 'Giro',
            'Dirección', 'Comuna', 'Ciudad', 'Región', 'Teléfono', 'Email Contacto',
            'Tipo Empresa', 'Estado', 'Fecha Creación', 'Contacto Principal',
            'Sucursales', 'Contactos'
        ])
        
        empresas = Empresa.objects.all().order_by('nombre')
        
        for empresa in empresas:
            writer.writerow([
                empresa.id,
                empresa.nombre,
                empresa.rut,
                empresa.nombre_fantasia or '',
                empresa.razon_social or '',
                empresa.giro or '',
                empresa.direccion or '',
                empresa.comuna or '',
                empresa.ciudad or '',
                empresa.region or '',
                empresa.telefono or '',
                empresa.email_contacto or '',
                empresa.get_tipo_empresa_display(),
                'Activa' if empresa.es_activa else 'Inactiva',
                empresa.fecha_creacion.strftime('%d/%m/%Y'),
                empresa.contacto_principal or '',
                empresa.sucursales.count(),
                empresa.contactos.count()
            ])
        
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500) 