from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth import login, authenticate
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.crypto import get_random_string
from django.urls import reverse
import json
import re
import csv
from datetime import datetime, timedelta

from .models import Usuario, LogAcceso
from app.models import Sucursal, EmpresaUser

# ========== FUNCIONES DE VALIDACIÓN ==========

def validar_rut_chileno(rut):
    """
    Valida un RUT chileno
    Retorna: (es_valido, mensaje_error)
    """
    try:
        # Limpiar el RUT de puntos y guiones
        rut_limpio = re.sub(r'[.-]', '', rut.upper())
        
        # Verificar formato básico
        if not re.match(r'^\d{7,8}[0-9K]$', rut_limpio):
            return False, "El RUT debe tener 7 u 8 dígitos seguidos de un dígito verificador (0-9 o K)"
        
        # Separar número y dígito verificador
        numero = rut_limpio[:-1]
        dv = rut_limpio[-1]
        
        # Calcular dígito verificador
        suma = 0
        multiplicador = 2
        
        for digito in reversed(numero):
            suma += int(digito) * multiplicador
            multiplicador = multiplicador + 1 if multiplicador < 7 else 2
        
        # Calcular dígito verificador esperado
        resto = suma % 11
        dv_esperado = 11 - resto if resto != 0 else 0
        
        # Convertir a string
        if dv_esperado == 10:
            dv_esperado = 'K'
        else:
            dv_esperado = str(dv_esperado)
        
        # Comparar
        if dv == dv_esperado:
            return True, ""
        else:
            return False, f"El dígito verificador es incorrecto. Debería ser {dv_esperado}"
            
    except Exception as e:
        return False, f"Error al validar RUT: {str(e)}"

def validar_campos_usuario(data):
    """
    Valida los campos de un usuario
    Retorna: (es_valido, errores)
    """
    errores = []
    
    # Campos obligatorios
    campos_obligatorios = [
        'username', 'first_name', 'last_name', 'email'
    ]
    
    for campo in campos_obligatorios:
        if not data.get(campo) or str(data.get(campo)).strip() == '':
            errores.append(f"El campo '{campo.replace('_', ' ').title()}' es obligatorio")
    
    # Validar formato de email
    if data.get('email'):
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, data['email']):
            errores.append("El correo electrónico no tiene un formato válido")
    
    # Validar RUT si se proporciona
    if data.get('rut'):
        rut_valido, mensaje_rut = validar_rut_chileno(data['rut'])
        if not rut_valido:
            errores.append(f"RUT inválido: {mensaje_rut}")
    
    # Validar username único
    if data.get('username'):
        username = data['username'].strip()
        if Usuario.objects.filter(username=username).exists():
            errores.append("El nombre de usuario ya existe")
    
    # Validar email único
    if data.get('email'):
        email = data['email'].strip()
        if Usuario.objects.filter(email=email).exists():
            errores.append("El correo electrónico ya está registrado")
    
    return len(errores) == 0, errores

def registrar_log_acceso(request, usuario, exito=True):
    """
    Registra un log de acceso
    """
    try:
        LogAcceso.objects.create(
            usuario=usuario,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            exito=exito
        )
    except Exception as e:
        print(f"Error al registrar log de acceso: {e}")

# ========== VISTAS PRINCIPALES ==========

@login_required
def gestion_usuarios(request):
    """
    Vista principal para gestión de usuarios
    """
    # Verificar permisos
    if not request.user.tiene_permiso_usuarios('crear') and not request.user.is_superuser:
        messages.error(request, "No tienes permisos para acceder a la gestión de usuarios")
        return redirect('app:verHome')
    
    return render(request, 'users/gestion_usuarios.html')

@require_GET
@login_required
def listar_usuarios(request):
    """
    Obtener lista de usuarios con filtros y paginación
    """
    try:
        # Parámetros de búsqueda y paginación
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))
        search = request.GET.get('search', '').strip()
        estado = request.GET.get('estado', '')
        
        # Query base
        usuarios = Usuario.objects.all()
        
        # Aplicar filtros
        if search:
            usuarios = usuarios.filter(
                Q(username__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search) |
                Q(rut__icontains=search) |
                Q(empresa__icontains=search)
            )
        
        if estado == 'activo':
            usuarios = usuarios.filter(es_activo=True)
        elif estado == 'inactivo':
            usuarios = usuarios.filter(es_activo=False)
        
        # Ordenar
        usuarios = usuarios.order_by('username')
        
        # Paginación
        paginator = Paginator(usuarios, page_size)
        usuarios_page = paginator.get_page(page)
        
        # Preparar datos para JSON
        usuarios_data = []
        for usuario in usuarios_page:
            usuarios_data.append({
                'id': usuario.id,
                'username': usuario.username,
                'first_name': usuario.first_name,
                'last_name': usuario.last_name,
                'email': usuario.email,
                'rut': usuario.rut,
                'telefono': usuario.telefono,
                'empresa': usuario.empresa,
                'cargo': usuario.cargo,
                'rol': usuario.rol,
                'rol_display': usuario.get_rol_display(),
                'es_activo': usuario.es_activo,
                'fecha_creacion': usuario.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'fecha_ultimo_acceso': usuario.fecha_ultimo_acceso.strftime('%d/%m/%Y %H:%M') if usuario.fecha_ultimo_acceso else 'Nunca',
                'puede_crear_usuarios': usuario.puede_crear_usuarios,
                'puede_editar_usuarios': usuario.puede_editar_usuarios,
                'puede_eliminar_usuarios': usuario.puede_eliminar_usuarios,
                'is_superuser': usuario.is_superuser,
                'is_staff': usuario.is_staff
            })
        
        # Calcular métricas
        total_usuarios = usuarios.count()
        usuarios_activos = usuarios.filter(es_activo=True).count()
        usuarios_inactivos = usuarios.filter(es_activo=False).count()
        
        return JsonResponse({
            'success': True,
            'usuarios': usuarios_data,
            'pagination': {
                'page': page,
                'total_pages': paginator.num_pages,
                'total_count': paginator.count,
                'has_previous': usuarios_page.has_previous(),
                'has_next': usuarios_page.has_next(),
            },
            'metricas': {
                'total_usuarios': total_usuarios,
                'usuarios_activos': usuarios_activos,
                'usuarios_inactivos': usuarios_inactivos
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
def crear_usuario(request):
    """
    Crear nuevo usuario
    """
    try:
        # Verificar permisos
        if not request.user.tiene_permiso_usuarios('crear') and not request.user.is_superuser:
            return JsonResponse({
                'success': False,
                'error': 'No tienes permisos para crear usuarios'
            }, status=403)
        
        data = json.loads(request.body)
        
        # Validar campos
        es_valido, errores = validar_campos_usuario(data)
        if not es_valido:
            return JsonResponse({
                'success': False,
                'error': ' | '.join(errores)
            }, status=400)
        
        # Generar contraseña temporal
        password_temp = get_random_string(12)
        
        # Crear usuario
        usuario = Usuario.objects.create_user(
            username=data['username'].strip(),
            email=data['email'].strip(),
            password=password_temp,
            first_name=data['first_name'].strip(),
            last_name=data['last_name'].strip(),
            rut=data.get('rut', '').strip(),
            telefono=data.get('telefono', '').strip(),
            direccion=data.get('direccion', '').strip(),
            empresa=data.get('empresa', '').strip(),
            cargo=data.get('cargo', '').strip(),
            departamento=data.get('departamento', '').strip(),
            rol=data.get('rol', 'vendedor'),
            fecha_nacimiento=data.get('fecha_nacimiento'),
            es_activo=data.get('es_activo', True),
            puede_crear_usuarios=data.get('puede_crear_usuarios', False),
            puede_editar_usuarios=data.get('puede_editar_usuarios', False),
            puede_eliminar_usuarios=data.get('puede_eliminar_usuarios', False),
            is_staff=data.get('is_staff', False)
        )
        
        # Enviar correo con credenciales
        try:
            enviar_credenciales_usuario(usuario, password_temp)
            mensaje_correo = " y se han enviado las credenciales por correo"
        except Exception as e:
            mensaje_correo = f" pero hubo un error al enviar el correo: {str(e)}"
        
        return JsonResponse({
            'success': True,
            'message': f'Usuario {usuario.get_full_name()} creado exitosamente{mensaje_correo}',
            'usuario_id': usuario.id
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

@require_POST
@login_required
@csrf_exempt
@transaction.atomic
def editar_usuario(request, usuario_id):
    """
    Editar información de un usuario existente
    """
    try:
        # Verificar permisos
        if not request.user.tiene_permiso_usuarios('editar') and not request.user.is_superuser:
            return JsonResponse({
                'success': False,
                'error': 'No tienes permisos para editar usuarios'
            }, status=403)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        data = json.loads(request.body)
        
        # Validar campos (excluyendo username y email si no cambiaron)
        data_validacion = data.copy()
        if data.get('username') == usuario.username:
            data_validacion.pop('username', None)
        if data.get('email') == usuario.email:
            data_validacion.pop('email', None)
            
        # Validar campos únicos solo si cambiaron
        errores = []
        if data.get('username') and data['username'] != usuario.username:
            if Usuario.objects.filter(username=data['username']).exists():
                errores.append("El nombre de usuario ya existe")
        
        if data.get('email') and data['email'] != usuario.email:
            if Usuario.objects.filter(email=data['email']).exists():
                errores.append("El correo electrónico ya está registrado")
        
        # Validar RUT si cambió
        if data.get('rut') and data['rut'] != usuario.rut:
            rut_valido, mensaje_rut = validar_rut_chileno(data['rut'])
            if not rut_valido:
                errores.append(f"RUT inválido: {mensaje_rut}")
        
        if errores:
            return JsonResponse({
                'success': False,
                'error': ' | '.join(errores)
            }, status=400)
        
        # Actualizar campos
        usuario.username = data.get('username', usuario.username).strip()
        usuario.email = data.get('email', usuario.email).strip()
        usuario.first_name = data.get('first_name', usuario.first_name).strip()
        usuario.last_name = data.get('last_name', usuario.last_name).strip()
        usuario.rut = data.get('rut', usuario.rut).strip() if data.get('rut') else usuario.rut
        usuario.telefono = data.get('telefono', usuario.telefono).strip() if data.get('telefono') else usuario.telefono
        usuario.direccion = data.get('direccion', usuario.direccion).strip() if data.get('direccion') else usuario.direccion
        usuario.empresa = data.get('empresa', usuario.empresa).strip() if data.get('empresa') else usuario.empresa
        usuario.cargo = data.get('cargo', usuario.cargo).strip() if data.get('cargo') else usuario.cargo
        usuario.departamento = data.get('departamento', usuario.departamento).strip() if data.get('departamento') else usuario.departamento
        usuario.rol = data.get('rol', usuario.rol)
        
        # Actualizar permisos solo si el usuario actual tiene permisos de superuser
        if request.user.is_superuser:
            usuario.puede_crear_usuarios = data.get('puede_crear_usuarios', usuario.puede_crear_usuarios)
            usuario.puede_editar_usuarios = data.get('puede_editar_usuarios', usuario.puede_editar_usuarios)
            usuario.puede_eliminar_usuarios = data.get('puede_eliminar_usuarios', usuario.puede_eliminar_usuarios)
            usuario.is_staff = data.get('is_staff', usuario.is_staff)
        
        usuario.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Usuario {usuario.get_full_name()} actualizado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Usuario.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Usuario no encontrado'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_POST
@login_required
@csrf_exempt
def toggle_estado_usuario(request, usuario_id):
    """
    Cambiar el estado activo/inactivo de un usuario
    """
    try:
        # Verificar permisos
        if not request.user.tiene_permiso_usuarios('editar') and not request.user.is_superuser:
            return JsonResponse({
                'success': False,
                'error': 'No tienes permisos para cambiar el estado de usuarios'
            }, status=403)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        
        # No permitir desactivar al propio usuario
        if usuario.id == request.user.id:
            return JsonResponse({
                'success': False,
                'error': 'No puedes cambiar tu propio estado'
            }, status=400)
        
        # Cambiar estado
        usuario.es_activo = not usuario.es_activo
        usuario.save()
        
        estado_texto = "activado" if usuario.es_activo else "desactivado"
        
        return JsonResponse({
            'success': True,
            'message': f'Usuario {usuario.get_full_name()} {estado_texto} exitosamente',
            'nuevo_estado': usuario.es_activo
        })
        
    except Usuario.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Usuario no encontrado'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_GET
@login_required
def obtener_usuario(request, usuario_id):
    """
    Obtener información de un usuario para edición
    """
    try:
        # Verificar permisos
        if not request.user.tiene_permiso_usuarios('editar') and not request.user.is_superuser:
            return JsonResponse({
                'success': False,
                'error': 'No tienes permisos para ver información de usuarios'
            }, status=403)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        
        # Obtener TODAS las sucursales del sistema
        todas_sucursales = Sucursal.objects.select_related('empresa').order_by('empresa__nombre', 'alias')
        
        # Obtener las sucursales ya asignadas al usuario
        empresas_usuario = EmpresaUser.objects.filter(
            user=usuario,
            status=True,
            sucursal__isnull=False
        ).select_related('sucursal', 'empresa')
        
        # Crear diccionario de sucursales del usuario para búsqueda rápida
        sucursales_usuario_dict = {}
        sucursal_actual = None
        
        for eu in empresas_usuario:
            sucursales_usuario_dict[eu.sucursal.id] = {
                'asignada': True,
                'active': eu.active,
                'empresa_user_id': eu.id
            }
            if eu.active:
                sucursal_actual = {
                    'id': eu.sucursal.id,
                    'alias': eu.sucursal.alias,
                    'nombre': eu.sucursal.alias,  # En app.models solo hay alias
                    'empresa': eu.empresa.nombre
                }
        
        # Preparar lista de todas las sucursales con información de asignación
        sucursales_disponibles = []
        for sucursal in todas_sucursales:
            info_usuario = sucursales_usuario_dict.get(sucursal.id, {})
            sucursal_data = {
                'id': sucursal.id,
                'alias': sucursal.alias,
                'nombre': sucursal.alias,  # En app.models solo hay alias
                'empresa': sucursal.empresa.nombre,
                'empresa_id': sucursal.empresa.id,
                'asignada': info_usuario.get('asignada', False),
                'active': info_usuario.get('active', False)
            }
            sucursales_disponibles.append(sucursal_data)
        
        return JsonResponse({
            'success': True,
            'usuario': {
                'id': usuario.id,
                'username': usuario.username,
                'first_name': usuario.first_name,
                'last_name': usuario.last_name,
                'email': usuario.email,
                'rut': usuario.rut or '',
                'telefono': usuario.telefono or '',
                'direccion': usuario.direccion or '',
                'empresa': usuario.empresa or '',
                'cargo': usuario.cargo or '',
                'departamento': usuario.departamento or '',
                'rol': usuario.rol,
                'es_activo': usuario.es_activo,
                'puede_crear_usuarios': usuario.puede_crear_usuarios,
                'puede_editar_usuarios': usuario.puede_editar_usuarios,
                'puede_eliminar_usuarios': usuario.puede_eliminar_usuarios,
                'is_staff': usuario.is_staff,
                'is_superuser': usuario.is_superuser,
                'sucursales_disponibles': sucursales_disponibles,
                'sucursal_actual': sucursal_actual
            }
        })
        
    except Usuario.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Usuario no encontrado'
        }, status=404)
    except Exception as e:
        import traceback
        print("Error completo:", traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_POST
@login_required
@csrf_exempt
def resetear_password(request, usuario_id):
    """
    Resetear contraseña de un usuario y enviar por correo
    """
    try:
        # Verificar permisos
        if not request.user.tiene_permiso_usuarios('editar') and not request.user.is_superuser:
            return JsonResponse({
                'success': False,
                'error': 'No tienes permisos para resetear contraseñas'
            }, status=403)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        
        # Generar nueva contraseña
        nueva_password = get_random_string(12)
        usuario.set_password(nueva_password)
        usuario.save()
        
        # Enviar correo con nueva contraseña
        try:
            enviar_nueva_password(usuario, nueva_password)
            mensaje = f"Contraseña reseteada y enviada por correo a {usuario.email}"
        except Exception as e:
            mensaje = f"Contraseña reseteada pero hubo un error al enviar el correo: {str(e)}"
        
        return JsonResponse({
            'success': True,
            'message': mensaje
        })
        
    except Usuario.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Usuario no encontrado'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_GET
@login_required
def exportar_usuarios(request):
    """
    Exportar lista de usuarios a CSV
    """
    try:
        # Verificar permisos
        if not request.user.tiene_permiso_usuarios('editar') and not request.user.is_superuser:
            return JsonResponse({
                'success': False,
                'error': 'No tienes permisos para exportar usuarios'
            }, status=403)
        
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="usuarios_retailmind.csv"'
        
        # Escribir BOM para UTF-8
        response.write('\ufeff')
        
        writer = csv.writer(response)
        writer.writerow([
            'ID', 'Usuario', 'Nombre', 'Apellido', 'Email', 'RUT', 'Teléfono',
            'Empresa', 'Cargo', 'Rol', 'Departamento', 'Estado', 'Fecha Creación',
            'Último Acceso', 'Permisos'
        ])
        
        usuarios = Usuario.objects.all().order_by('username')
        
        for usuario in usuarios:
            permisos = []
            if usuario.puede_crear_usuarios:
                permisos.append('Crear')
            if usuario.puede_editar_usuarios:
                permisos.append('Editar')
            if usuario.puede_eliminar_usuarios:
                permisos.append('Eliminar')
            if usuario.is_staff:
                permisos.append('Staff')
            if usuario.is_superuser:
                permisos.append('Superuser')
            
            writer.writerow([
                usuario.id,
                usuario.username,
                usuario.first_name or '',
                usuario.last_name or '',
                usuario.email or '',
                usuario.rut or '',
                usuario.telefono or '',
                usuario.empresa or '',
                usuario.cargo or '',
                usuario.get_rol_display(),
                usuario.departamento or '',
                'Activo' if usuario.es_activo else 'Inactivo',
                usuario.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                usuario.fecha_ultimo_acceso.strftime('%d/%m/%Y %H:%M') if usuario.fecha_ultimo_acceso else 'Nunca',
                ', '.join(permisos)
            ])
        
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_POST
@login_required
@csrf_exempt
def asignar_sucursal_sesion(request, usuario_id):
    """
    Asignar sucursal a la sesión del usuario
    """
    try:
        data = json.loads(request.body)
        sucursal_id = data.get('sucursal_id')
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Debe seleccionar una sucursal'
            }, status=400)
        
        # Obtener la sucursal
        try:
            sucursal = Sucursal.objects.select_related('empresa').get(id=sucursal_id)
        except Sucursal.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Sucursal no encontrada'
            }, status=404)
        
        # Buscar si ya existe un EmpresaUser para este usuario y sucursal
        empresa_user = EmpresaUser.objects.filter(
            user_id=usuario_id,
            sucursal_id=sucursal_id,
            empresa_id=sucursal.empresa.id
        ).first()
        
        if empresa_user:
            # Ya existe, solo activarlo
            empresa_user.status = True
            empresa_user.active = False  # Se activará después
            empresa_user.save()
        else:
            # No existe, crear nuevo registro
            empresa_user = EmpresaUser.objects.create(
                user_id=usuario_id,
                sucursal_id=sucursal_id,
                empresa_id=sucursal.empresa.id,
                status=True,
                active=False
            )
        
        # Desactivar todas las sucursales del usuario
        EmpresaUser.objects.filter(user_id=usuario_id).update(active=False)
        
        # Activar la sucursal seleccionada
        empresa_user.active = True
        empresa_user.save()
        
        # Si es el usuario actual, actualizar la sesión
        if request.user.id == usuario_id:
            request.session['idEmpresaActual'] = sucursal.empresa.id
            request.session['empresaActual'] = sucursal.empresa.id
            request.session['nombreEmpresaActual'] = sucursal.empresa.nombre
            request.session['rutEmpresaActual'] = sucursal.empresa.rut
            request.session['idSucursalActual'] = sucursal.id
            request.session['sucursalActual'] = sucursal.id
            request.session['alias'] = sucursal.alias
            request.session['direccionSucursal'] = sucursal.direccion
        
        return JsonResponse({
            'success': True,
            'message': f'Sucursal "{sucursal.alias}" asignada exitosamente a {Usuario.objects.get(id=usuario_id).get_full_name()}',
            'sucursal': {
                'id': sucursal.id,
                'alias': sucursal.alias,
                'nombre': sucursal.alias  # app.models.Sucursal solo tiene alias
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
            'error': str(e)
        }, status=500)

# ========== FUNCIONES DE CORREO ==========

def enviar_credenciales_usuario(usuario, password):
    """
    Envía las credenciales de un nuevo usuario por correo
    """
    subject = 'Bienvenido a RetailMind - Tus Credenciales de Acceso'
    
    context = {
        'usuario': usuario,
        'password': password,
        'fecha': timezone.now().strftime('%d/%m/%Y %H:%M'),
        'sistema': 'RetailMind'
    }
    
    # Template básico en texto plano por ahora
    message = f"""
    Hola {usuario.get_full_name()},

    Te damos la bienvenida a RetailMind.

    Tus credenciales de acceso son:
    Usuario: {usuario.username}
    Contraseña: {password}

    Por favor, cambia tu contraseña en el primer acceso.

    Saludos,
    Equipo RetailMind
    """
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[usuario.email],
            fail_silently=False
        )
    except Exception as e:
        print(f"Error enviando correo: {e}")
        raise e

def enviar_nueva_password(usuario, nueva_password):
    """
    Envía la nueva contraseña por correo
    """
    subject = 'RetailMind - Nueva Contraseña Generada'
    
    message = f"""
    Hola {usuario.get_full_name()},

    Se ha generado una nueva contraseña para tu cuenta en RetailMind.

    Tu nueva contraseña es: {nueva_password}

    Por favor, cambia tu contraseña después de iniciar sesión.

    Saludos,
    Equipo RetailMind
    """
    
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[usuario.email],
            fail_silently=False
        )
    except Exception as e:
        print(f"Error enviando correo: {e}")
        raise e
