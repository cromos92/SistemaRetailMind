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

from .models import Usuario, LogAcceso, SesionActiva, TokenResetPassword
from app.models import Sucursal, EmpresaUser, Empresa
import io

# Intentar importar Pillow para procesamiento de imágenes
try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

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

def generar_username_desde_email(email):
    """
    Genera un nombre de usuario único a partir del correo electrónico.
    Si el username ya existe, agrega un número al final.
    Ejemplo: jperez@empresa.cl -> jperez, jperez1, jperez2, etc.
    """
    if not email:
        return None
    
    # Extraer la parte antes del @
    base_username = email.split('@')[0].lower()
    
    # Limpiar caracteres especiales, mantener solo letras, números y puntos
    base_username = re.sub(r'[^a-z0-9.]', '', base_username)
    
    # Si está vacío después de limpiar, usar 'usuario'
    if not base_username:
        base_username = 'usuario'
    
    # Verificar si el username ya existe
    username = base_username
    contador = 1
    
    while Usuario.objects.filter(username=username).exists():
        username = f"{base_username}{contador}"
        contador += 1
        
        # Seguridad: evitar bucle infinito
        if contador > 1000:
            username = f"{base_username}{get_random_string(4)}"
            break
    
    return username


def validar_campos_usuario(data, es_edicion=False, usuario_id=None):
    """
    Valida los campos de un usuario
    Retorna: (es_valido, errores)
    """
    errores = []
    
    # Campos obligatorios (username ya no es obligatorio, se genera automáticamente)
    campos_obligatorios = [
        'first_name', 'last_name', 'email'
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
    
    # Validar username único (solo si se proporciona manualmente)
    if data.get('username'):
        username = data['username'].strip()
        query = Usuario.objects.filter(username=username)
        if es_edicion and usuario_id:
            query = query.exclude(id=usuario_id)
        if query.exists():
            errores.append("El nombre de usuario ya existe")
    
    # Validar email único
    if data.get('email'):
        email = data['email'].strip()
        query = Usuario.objects.filter(email=email)
        if es_edicion and usuario_id:
            query = query.exclude(id=usuario_id)
        if query.exists():
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
    RESTRINGIDO: Solo usuarios con rol 'administrador' o superusuarios pueden acceder
    """
    # Verificar si es administrador o superusuario
    es_admin = getattr(request.user, 'rol', None) == 'administrador'
    
    if not es_admin:
        messages.error(request, "Acceso restringido. Solo los administradores pueden gestionar usuarios.")
        return redirect('verHome')
    
    return render(request, 'users/gestion_usuarios.html')

@require_GET
@login_required
def listar_usuarios(request):
    """
    Obtener lista de usuarios con filtros y paginación
    RESTRINGIDO: Solo administradores
    """
    # Verificar si es administrador
    es_admin = getattr(request.user, 'rol', None) == 'administrador'
    if not es_admin:
        return JsonResponse({'error': 'No tienes permisos para acceder a esta información'}, status=403)
    
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
            empresa_actual = EmpresaUser.objects.filter(
                user=usuario,
                active=True
            ).select_related('empresa', 'sucursal').first()
            empresa_actual_nombre = None
            sucursal_actual_alias = None
            if empresa_actual:
                empresa_actual_nombre = empresa_actual.empresa.nombre
                if empresa_actual.sucursal:
                    sucursal_actual_alias = empresa_actual.sucursal.alias

            usuarios_data.append({
                'id': usuario.id,
                'username': usuario.username,
                'first_name': usuario.first_name,
                'last_name': usuario.last_name,
                'email': usuario.email,
                'rut': usuario.rut,
                'telefono': usuario.telefono,
                'empresa': usuario.empresa,
                'empresa_actual': empresa_actual_nombre,
                'sucursal_actual': sucursal_actual_alias,
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
    RESTRINGIDO: Solo administradores
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden crear usuarios.'
            }, status=403)
        
        data = json.loads(request.body)
        
        # ✅ GENERAR USERNAME AUTOMÁTICAMENTE DESDE EL EMAIL
        email = data.get('email', '').strip()
        if email and not data.get('username'):
            data['username'] = generar_username_desde_email(email)
        
        # Validar campos
        es_valido, errores = validar_campos_usuario(data)
        if not es_valido:
            return JsonResponse({
                'success': False,
                'error': ' | '.join(errores)
            }, status=400)
        
        # Crear contraseña temporal y forzar cambio al primer acceso
        password_temp = get_random_string(12)
        
        # ✅ Usar el username generado o el proporcionado
        username = data.get('username', '').strip() or generar_username_desde_email(email)
        
        # Crear usuario
        # IMPORTANTE: Convertir RUT vacío a None para evitar conflictos de unicidad
        rut_valor = data.get('rut', '').strip()
        if not rut_valor:
            rut_valor = None  # None permite múltiples usuarios sin RUT (unique=True ignora NULL)
        
        usuario = Usuario.objects.create_user(
            username=username,
            email=email,
            password=password_temp,
            first_name=data['first_name'].strip(),
            last_name=data['last_name'].strip(),
            rut=rut_valor,
            telefono=data.get('telefono', '').strip() or None,
            direccion=data.get('direccion', '').strip() or None,
            empresa=data.get('empresa', '').strip() or None,
            cargo=data.get('cargo', '').strip() or None,
            departamento=data.get('departamento', '').strip() or None,
            rol=data.get('rol', 'vendedor'),
            fecha_nacimiento=data.get('fecha_nacimiento') or None,
            es_activo=data.get('es_activo', True),
            requiere_2fa=data.get('requiere_2fa', False),
            puede_crear_usuarios=data.get('puede_crear_usuarios', False),
            puede_editar_usuarios=data.get('puede_editar_usuarios', False),
            puede_eliminar_usuarios=data.get('puede_eliminar_usuarios', False),
            is_staff=data.get('is_staff', False)
        )
        password_temp = usuario.generar_password_temporal()
        
        # Enviar correo con credenciales
        try:
            login_url = getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/') + '/'
            enviar_credenciales_usuario(usuario, password_temp, login_url=login_url)
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


@require_GET
@login_required
def verificar_username(request):
    """
    Verifica si un username está disponible y sugiere uno alternativo si no lo está.
    Se usa para auto-generar username desde email.
    """
    try:
        username_base = request.GET.get('username', '').strip().lower()
        
        if not username_base:
            return JsonResponse({
                'success': False,
                'error': 'Username no proporcionado'
            }, status=400)
        
        # Limpiar caracteres especiales
        username_base = re.sub(r'[^a-z0-9.]', '', username_base)
        
        if not username_base:
            username_base = 'usuario'
        
        # Buscar un username disponible
        username_disponible = username_base
        contador = 1
        
        while Usuario.objects.filter(username=username_disponible).exists():
            username_disponible = f"{username_base}{contador}"
            contador += 1
            
            # Seguridad: evitar bucle infinito
            if contador > 100:
                username_disponible = f"{username_base}{get_random_string(4)}"
                break
        
        return JsonResponse({
            'success': True,
            'username_base': username_base,
            'username_disponible': username_disponible,
            'modificado': username_disponible != username_base
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
def editar_usuario(request, usuario_id):
    """
    Editar información de un usuario existente
    RESTRINGIDO: Solo administradores
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden editar usuarios.'
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
        
        # IMPORTANTE: Convertir RUT vacío a None para evitar conflictos de unicidad
        rut_valor = data.get('rut', '').strip() if data.get('rut') else ''
        usuario.rut = rut_valor if rut_valor else None  # None permite múltiples sin RUT
        
        usuario.telefono = data.get('telefono', usuario.telefono).strip() if data.get('telefono') else usuario.telefono
        usuario.direccion = data.get('direccion', usuario.direccion).strip() if data.get('direccion') else usuario.direccion
        usuario.empresa = data.get('empresa', usuario.empresa).strip() if data.get('empresa') else usuario.empresa
        usuario.cargo = data.get('cargo', usuario.cargo).strip() if data.get('cargo') else usuario.cargo
        usuario.departamento = data.get('departamento', usuario.departamento).strip() if data.get('departamento') else usuario.departamento
        usuario.rol = data.get('rol', usuario.rol)
        usuario.requiere_2fa = data.get('requiere_2fa', usuario.requiere_2fa)
        
        # Actualizar permisos solo si el usuario actual es administrador
        if getattr(request.user, 'rol', None) == 'administrador':
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
    RESTRINGIDO: Solo administradores
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden cambiar el estado de usuarios.'
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
    RESTRINGIDO: Solo administradores
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden ver información de usuarios.'
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
                'requiere_2fa': usuario.requiere_2fa,
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
    RESTRINGIDO: Solo administradores
    
    Acepta:
    - Sin body: genera contraseña automática segura
    - Con body JSON { "password_personalizada": "clave" }: usa contraseña especificada
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden resetear contraseñas.'
            }, status=403)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        
        # Verificar si se envió una contraseña personalizada
        password_personalizada = None
        es_password_personalizada = False
        
        if request.body:
            try:
                data = json.loads(request.body)
                password_personalizada = data.get('password_personalizada', '').strip()
            except json.JSONDecodeError:
                pass
        
        # Generar o usar contraseña
        if password_personalizada:
            nueva_password = password_personalizada
            es_password_personalizada = True
            tipo_password = "personalizada"
        else:
            nueva_password = get_random_string(12)
            tipo_password = "automática"
        
        usuario.set_password(nueva_password)
        usuario.save()
        
        # Intentar enviar correo
        email_enviado = False
        mensaje_email = ""
        
        try:
            enviar_nueva_password(usuario, nueva_password)
            email_enviado = True
            mensaje_email = f"y enviada por correo a {usuario.email}"
        except Exception as e:
            print(f"⚠️ Error al enviar email: {str(e)}")
            mensaje_email = "(email no configurado)"
        
        return JsonResponse({
            'success': True,
            'message': f"Contraseña {tipo_password} establecida exitosamente {mensaje_email}",
            'email_enviado': email_enviado,
            'nueva_password': nueva_password if not email_enviado else None,  # Mostrar si no se envió
            'es_personalizada': es_password_personalizada,
            'usuario': {
                'nombre': usuario.get_full_name(),
                'email': usuario.email,
                'username': usuario.username
            }
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

@require_POST
@login_required
@csrf_exempt
def reenviar_credenciales(request, usuario_id):
    """
    Reenviar correo con credenciales (genera nueva password temporal y envía email de bienvenida)
    RESTRINGIDO: Solo administradores
    """
    try:
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden reenviar credenciales.'
            }, status=403)

        usuario = get_object_or_404(Usuario, id=usuario_id)

        if not usuario.email:
            return JsonResponse({
                'success': False,
                'error': 'El usuario no tiene correo electrónico configurado'
            }, status=400)

        nueva_password = get_random_string(12)
        usuario.set_password(nueva_password)
        usuario.requiere_cambio_password = True
        usuario.save()

        login_url = getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/') + '/'
        try:
            enviar_credenciales_usuario(usuario, nueva_password, login_url=login_url)
            return JsonResponse({
                'success': True,
                'message': f'Credenciales reenviadas exitosamente a {usuario.email}',
                'email': usuario.email
            })
        except Exception as e:
            return JsonResponse({
                'success': True,
                'message': f'Contraseña reseteada pero no se pudo enviar el correo: {str(e)}',
                'nueva_password': nueva_password,
                'email_enviado': False,
                'usuario': {
                    'nombre': usuario.get_full_name(),
                    'email': usuario.email,
                    'username': usuario.username
                }
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
    RESTRINGIDO: Solo administradores
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden exportar usuarios.'
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


@require_GET
@login_required
def descargar_plantilla_importacion(request):
    """
    Descargar plantilla CSV para importación de usuarios
    RESTRINGIDO: Solo administradores
    """
    try:
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden descargar la plantilla.'
            }, status=403)
        
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="plantilla_importacion_usuarios.csv"'
        
        # Escribir BOM para UTF-8
        response.write('\ufeff')
        
        writer = csv.writer(response)
        # Encabezados de la plantilla
        writer.writerow([
            'email', 'nombre', 'apellido', 'rut', 'telefono', 
            'empresa', 'cargo', 'departamento', 'rol'
        ])
        
        # Fila de ejemplo
        writer.writerow([
            'ejemplo@empresa.cl', 'Juan', 'Pérez', '12.345.678-9', '+56912345678',
            'Mi Empresa', 'Vendedor', 'Ventas', 'vendedor'
        ])
        
        # Segunda fila de ejemplo
        writer.writerow([
            'maria@empresa.cl', 'María', 'González', '11.222.333-4', '+56987654321',
            'Mi Empresa', 'Cajera', 'Caja', 'cajero'
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
@transaction.atomic
def importar_usuarios(request):
    """
    Importar usuarios desde archivo CSV
    RESTRINGIDO: Solo administradores
    
    Formato CSV esperado (con encabezados):
    email, nombre, apellido, rut, telefono, empresa, cargo, departamento, rol
    
    - email: Obligatorio, se usa para generar username
    - nombre: Obligatorio
    - apellido: Obligatorio
    - rol: Opcional (por defecto: vendedor). Valores: vendedor, cajero, jefe_local, administracion, administrador
    - Los demás campos son opcionales
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido. Solo los administradores pueden importar usuarios.'
            }, status=403)
        
        if 'archivo' not in request.FILES:
            return JsonResponse({
                'success': False,
                'error': 'No se ha proporcionado ningún archivo'
            }, status=400)
        
        archivo = request.FILES['archivo']
        
        # Validar extensión
        if not archivo.name.lower().endswith('.csv'):
            return JsonResponse({
                'success': False,
                'error': 'El archivo debe ser un CSV'
            }, status=400)
        
        # Validar tamaño (máximo 5MB)
        if archivo.size > 5 * 1024 * 1024:
            return JsonResponse({
                'success': False,
                'error': 'El archivo es muy grande. Máximo 5MB'
            }, status=400)
        
        # Leer el archivo CSV
        try:
            # Intentar decodificar con diferentes encodings
            try:
                contenido = archivo.read().decode('utf-8-sig')  # utf-8 con BOM
            except UnicodeDecodeError:
                archivo.seek(0)
                contenido = archivo.read().decode('latin-1')
            
            # Usar StringIO para leer como CSV
            csv_file = io.StringIO(contenido)
            reader = csv.DictReader(csv_file)
            
            # Normalizar nombres de columnas (minúsculas, sin espacios)
            if reader.fieldnames:
                reader.fieldnames = [f.lower().strip() for f in reader.fieldnames]
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al leer el archivo CSV: {str(e)}'
            }, status=400)
        
        # Mapeo de nombres de columnas alternativos
        columnas_map = {
            'email': ['email', 'correo', 'mail', 'e-mail'],
            'nombre': ['nombre', 'first_name', 'nombres', 'name'],
            'apellido': ['apellido', 'last_name', 'apellidos', 'surname'],
            'rut': ['rut', 'dni', 'documento'],
            'telefono': ['telefono', 'phone', 'tel', 'celular', 'movil'],
            'empresa': ['empresa', 'company', 'compania'],
            'cargo': ['cargo', 'position', 'puesto'],
            'departamento': ['departamento', 'department', 'area'],
            'rol': ['rol', 'role', 'perfil', 'tipo']
        }
        
        def obtener_valor(row, campo):
            """Obtiene el valor de una columna, probando nombres alternativos"""
            for nombre in columnas_map.get(campo, [campo]):
                if nombre in row and row[nombre]:
                    return row[nombre].strip()
            return ''
        
        # Mapeo de roles (para aceptar diferentes formatos)
        roles_map = {
            'vendedor': 'vendedor',
            'cajero': 'cajero',
            'cajera': 'cajero',
            'jefe local': 'jefe_local',
            'jefe_local': 'jefe_local',
            'jefelocal': 'jefe_local',
            'administracion': 'administracion',
            'administración': 'administracion',
            'admin': 'administrador',
            'administrador': 'administrador',
            'bodeguero': 'bodeguero',
            'contador': 'contador'
        }
        
        # Procesar filas
        usuarios_creados = []
        usuarios_actualizados = []
        errores = []
        fila_num = 1  # Empezamos en 1 (el encabezado es fila 0)
        
        for row in reader:
            fila_num += 1
            
            try:
                # Obtener valores
                email = obtener_valor(row, 'email')
                nombre = obtener_valor(row, 'nombre')
                apellido = obtener_valor(row, 'apellido')
                rut = obtener_valor(row, 'rut')
                telefono = obtener_valor(row, 'telefono')
                empresa = obtener_valor(row, 'empresa')
                cargo = obtener_valor(row, 'cargo')
                departamento = obtener_valor(row, 'departamento')
                rol_input = obtener_valor(row, 'rol').lower()
                
                # Validar campos obligatorios
                if not email:
                    errores.append(f"Fila {fila_num}: Email es obligatorio")
                    continue
                
                if not nombre:
                    errores.append(f"Fila {fila_num}: Nombre es obligatorio")
                    continue
                
                if not apellido:
                    errores.append(f"Fila {fila_num}: Apellido es obligatorio")
                    continue
                
                # Validar formato de email
                email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
                if not re.match(email_regex, email):
                    errores.append(f"Fila {fila_num}: Email '{email}' no tiene formato válido")
                    continue
                
                # Determinar rol
                rol = roles_map.get(rol_input, 'vendedor')
                
                # Validar RUT si se proporciona
                if rut:
                    rut_valido, msg_rut = validar_rut_chileno(rut)
                    if not rut_valido:
                        # No es error crítico, solo advertencia
                        rut = None  # Ignorar RUT inválido
                
                # Verificar si el usuario ya existe (por email)
                usuario_existente = Usuario.objects.filter(email=email).first()
                
                if usuario_existente:
                    # Actualizar usuario existente
                    usuario_existente.first_name = nombre
                    usuario_existente.last_name = apellido
                    if rut:
                        usuario_existente.rut = rut
                    if telefono:
                        usuario_existente.telefono = telefono
                    if empresa:
                        usuario_existente.empresa = empresa
                    if cargo:
                        usuario_existente.cargo = cargo
                    if departamento:
                        usuario_existente.departamento = departamento
                    usuario_existente.rol = rol
                    usuario_existente.save()
                    
                    usuarios_actualizados.append({
                        'email': email,
                        'nombre': f"{nombre} {apellido}"
                    })
                else:
                    # Crear nuevo usuario
                    username = generar_username_desde_email(email)
                    password_temp = get_random_string(12)
                    
                    nuevo_usuario = Usuario.objects.create_user(
                        username=username,
                        email=email,
                        password=password_temp,
                        first_name=nombre,
                        last_name=apellido,
                        rut=rut if rut else None,
                        telefono=telefono if telefono else None,
                        empresa=empresa if empresa else None,
                        cargo=cargo if cargo else None,
                        departamento=departamento if departamento else None,
                        rol=rol,
                        es_activo=True
                    )
                    
                    # Generar contraseña temporal
                    password_temp = nuevo_usuario.generar_password_temporal()
                    
                    usuarios_creados.append({
                        'email': email,
                        'nombre': f"{nombre} {apellido}",
                        'username': username,
                        'password': password_temp
                    })
                    
            except Exception as e:
                errores.append(f"Fila {fila_num}: Error al procesar - {str(e)}")
                continue
        
        # Preparar respuesta
        resultado = {
            'success': True,
            'message': f'Importación completada',
            'creados': len(usuarios_creados),
            'actualizados': len(usuarios_actualizados),
            'errores': len(errores),
            'usuarios_creados': usuarios_creados,
            'usuarios_actualizados': usuarios_actualizados,
            'detalle_errores': errores[:20] if errores else []  # Limitar a 20 errores
        }
        
        if len(errores) > 20:
            resultado['detalle_errores'].append(f"... y {len(errores) - 20} errores más")
        
        return JsonResponse(resultado)
        
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

def enviar_credenciales_usuario(usuario, password, login_url=None):
    """
    Envía las credenciales de un nuevo usuario por correo con template HTML
    """
    from django.core.mail import EmailMultiAlternatives
    
    subject = '🎉 Bienvenido a NEXO - Tus Credenciales de Acceso'
    
    # Mensaje en texto plano (fallback)
    login_url = login_url or (settings.SITE_URL if hasattr(settings, 'SITE_URL') else 'http://localhost:8000')
    text_message = f"""
Hola {usuario.get_full_name()},

¡Bienvenido/a a NEXO!

Tus credenciales de acceso son:
━━━━━━━━━━━━━━━━━━━━━━━━
Usuario: {usuario.username}
Contraseña: {password}
━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ IMPORTANTE: Por seguridad, debes cambiar tu contraseña en tu primer acceso.

Para ingresar al sistema, visita: {login_url}

Si tienes alguna duda, contacta al administrador del sistema.

Saludos cordiales,
Equipo NEXO
    """
    
    # Mensaje HTML
    html_message = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #F5F5F7; margin: 0; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #FFFFFF; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(26, 26, 46, 0.1); }}
            .header {{ background: linear-gradient(135deg, #0066FF 0%, #0052CC 100%); padding: 30px; text-align: center; }}
            .header h1 {{ color: #FFFFFF; margin: 0; font-size: 28px; }}
            .header p {{ color: rgba(255,255,255,0.85); margin: 10px 0 0 0; }}
            .content {{ padding: 30px; }}
            .credentials-box {{ background: #E6F0FF; border-radius: 12px; padding: 20px; margin: 20px 0; border-left: 4px solid #0066FF; }}
            .credentials-box h3 {{ color: #0052CC; margin: 0 0 15px 0; }}
            .credential {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #D1D1D9; }}
            .credential:last-child {{ border-bottom: none; }}
            .credential-label {{ color: #8A8A9A; }}
            .credential-value {{ color: #1A1A2E; font-weight: bold; font-family: monospace; font-size: 16px; }}
            .warning {{ background: rgba(255, 176, 32, 0.1); border-left: 4px solid #FFB020; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .warning strong {{ color: #996B00; }}
            .btn {{ display: inline-block; background: #0066FF; color: #FFFFFF; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; margin: 20px 0; }}
            .btn:hover {{ background: #0052CC; }}
            .footer {{ background: #F5F5F7; padding: 20px; text-align: center; color: #8A8A9A; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎉 ¡Bienvenido/a a NEXO!</h1>
                <p>Tu cuenta ha sido creada exitosamente</p>
            </div>
            <div class="content">
                <p style="color: #4A4A5A; font-size: 16px;">Hola <strong>{usuario.get_full_name()}</strong>,</p>
                <p style="color: #4A4A5A;">Te damos la bienvenida al sistema NEXO. A continuación encontrarás tus credenciales de acceso:</p>
                
                <div class="credentials-box">
                    <h3>🔐 Tus Credenciales</h3>
                    <div class="credential">
                        <span class="credential-label">Usuario:</span>
                        <span class="credential-value">{usuario.username}</span>
                    </div>
                    <div class="credential">
                        <span class="credential-label">Contraseña:</span>
                        <span class="credential-value">{password}</span>
                    </div>
                </div>
                
                <div class="warning">
                    <strong>⚠️ Importante:</strong> Por seguridad, debes cambiar tu contraseña en tu primer acceso al sistema.
                </div>

                <a class="btn" href="{login_url}">Iniciar sesión</a>
                
                <p style="color: #4A4A5A;">Si tienes alguna pregunta, no dudes en contactar al administrador del sistema.</p>
            </div>
            <div class="footer">
                <p>Este correo fue enviado automáticamente por el sistema NEXO.</p>
                <p>© {timezone.now().year} NEXO - Sistema de Gestión Retail</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[usuario.email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        print(f"✅ Email de credenciales enviado a {usuario.email}")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")
        raise e

def enviar_nueva_password(usuario, nueva_password):
    """
    Envía la nueva contraseña por correo con template HTML
    """
    from django.core.mail import EmailMultiAlternatives
    
    subject = '🔑 NEXO - Nueva Contraseña Generada'
    
    # Mensaje en texto plano (fallback)
    text_message = f"""
Hola {usuario.get_full_name()},

Se ha generado una nueva contraseña para tu cuenta en NEXO.

━━━━━━━━━━━━━━━━━━━━━━━━
Tu nueva contraseña es: {nueva_password}
━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ Por seguridad, cambia tu contraseña después de iniciar sesión.

Si no solicitaste este cambio, contacta inmediatamente al administrador del sistema.

Saludos cordiales,
Equipo NEXO
    """
    
    # Mensaje HTML
    html_message = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #F5F5F7; margin: 0; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #FFFFFF; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(26, 26, 46, 0.1); }}
            .header {{ background: linear-gradient(135deg, #FFB020 0%, #E69A00 100%); padding: 30px; text-align: center; }}
            .header h1 {{ color: #1A1A2E; margin: 0; font-size: 28px; }}
            .header p {{ color: rgba(26,26,46,0.75); margin: 10px 0 0 0; }}
            .content {{ padding: 30px; }}
            .password-box {{ background: linear-gradient(135deg, #0066FF 0%, #0052CC 100%); border-radius: 12px; padding: 25px; margin: 20px 0; text-align: center; }}
            .password-box p {{ color: rgba(255,255,255,0.85); margin: 0 0 10px 0; font-size: 14px; }}
            .password {{ color: #FFFFFF; font-size: 28px; font-weight: bold; font-family: monospace; letter-spacing: 2px; margin: 0; }}
            .warning {{ background: rgba(255, 77, 77, 0.1); border-left: 4px solid #FF4D4D; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .warning strong {{ color: #FF4D4D; }}
            .info {{ background: rgba(0, 102, 255, 0.1); border-left: 4px solid #0066FF; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .footer {{ background: #F5F5F7; padding: 20px; text-align: center; color: #8A8A9A; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔑 Nueva Contraseña</h1>
                <p>Se ha reseteado tu contraseña de acceso</p>
            </div>
            <div class="content">
                <p style="color: #4A4A5A; font-size: 16px;">Hola <strong>{usuario.get_full_name()}</strong>,</p>
                <p style="color: #4A4A5A;">Se ha generado una nueva contraseña temporal para tu cuenta en NEXO.</p>
                
                <div class="password-box">
                    <p>Tu nueva contraseña es:</p>
                    <p class="password">{nueva_password}</p>
                </div>
                
                <div class="info">
                    <strong>💡 Recomendación:</strong> Te sugerimos cambiar esta contraseña temporal por una de tu elección una vez que inicies sesión.
                </div>
                
                <div class="warning">
                    <strong>⚠️ ¿No solicitaste este cambio?</strong><br>
                    Si no fuiste tú quien solicitó el reseteo de contraseña, contacta inmediatamente al administrador del sistema.
                </div>
            </div>
            <div class="footer">
                <p>Este correo fue enviado automáticamente por el sistema NEXO.</p>
                <p>© {timezone.now().year} NEXO - Sistema de Gestión Retail</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[usuario.email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        print(f"✅ Email de nueva contraseña enviado a {usuario.email}")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")
        raise e


# ========== PERFIL DE USUARIO ==========

@login_required
def mi_perfil(request):
    """Vista del perfil del usuario actual con sesiones activas"""
    try:
        usuario = request.user
        
        # Registrar/actualizar sesión actual
        if request.session.session_key:
            sesion_actual = SesionActiva.registrar_sesion(request, usuario)
            # Marcar como sesión actual
            SesionActiva.objects.filter(usuario=usuario).update(es_actual=False)
            sesion_actual.es_actual = True
            sesion_actual.save()
        
        # Obtener sesiones activas
        sesiones = SesionActiva.objects.filter(
            usuario=usuario,
            activa=True
        ).order_by('-ultima_actividad')[:10]
        
        # Obtener empresa y sucursal actual
        empresa_user = EmpresaUser.objects.filter(
            user=usuario,
            active=True
        ).select_related('empresa', 'sucursal').first()
        
        return render(request, 'users/mi_perfil.html', {
            'usuario': usuario,
            'sesiones': sesiones,
            'sesion_actual_key': request.session.session_key,
            'empresa_user': empresa_user
        })
    except Exception as e:
        print(f"❌ Error en mi_perfil: {str(e)}")
        import traceback
        traceback.print_exc()
        from django.http import HttpResponse
        return HttpResponse(f"Error: {str(e)}", status=500)

@login_required
@require_POST
@csrf_exempt
def actualizar_perfil(request):
    """Actualizar datos del perfil del usuario"""
    try:
        data = json.loads(request.body)
        usuario = request.user
        
        # Actualizar campos editables
        usuario.first_name = data.get('first_name', usuario.first_name).strip()
        usuario.last_name = data.get('last_name', usuario.last_name).strip()
        usuario.email = data.get('email', usuario.email).strip()
        usuario.telefono = data.get('telefono', usuario.telefono or '').strip()
        usuario.direccion = data.get('direccion', usuario.direccion or '').strip()
        usuario.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Perfil actualizado exitosamente'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
@require_POST
@csrf_exempt
def cambiar_password(request):
    """Cambiar contraseña del usuario actual"""
    try:
        data = json.loads(request.body)
        usuario = request.user
        
        password_actual = data.get('password_actual', '').strip()
        password_nueva = data.get('password_nueva', '').strip()
        
        if not password_actual or not password_nueva:
            return JsonResponse({
                'success': False,
                'error': 'Todos los campos son obligatorios'
            }, status=400)
        
        # Verificar contraseña actual
        if not usuario.check_password(password_actual):
            return JsonResponse({
                'success': False,
                'error': 'La contraseña actual es incorrecta'
            }, status=400)
        
        # Cambiar contraseña
        usuario.set_password(password_nueva)
        usuario.save()
        
        # Actualizar sesión para no cerrar sesión
        from django.contrib.auth import update_session_auth_hash
        update_session_auth_hash(request, usuario)
        
        return JsonResponse({
            'success': True,
            'message': 'Contraseña cambiada exitosamente'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
@require_POST
@csrf_exempt
def subir_foto_perfil(request):
    """Subir foto de perfil del usuario"""
    try:
        if 'foto' not in request.FILES:
            return JsonResponse({
                'success': False,
                'error': 'No se proporcionó ninguna imagen'
            }, status=400)
        
        foto = request.FILES['foto']
        usuario = request.user
        
        # Validar tipo de archivo
        tipos_permitidos = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
        if foto.content_type not in tipos_permitidos:
            return JsonResponse({
                'success': False,
                'error': 'Tipo de archivo no permitido. Solo se permiten: JPG, PNG, GIF, WEBP'
            }, status=400)
        
        # Validar tamaño (máximo 5MB)
        if foto.size > 5 * 1024 * 1024:
            return JsonResponse({
                'success': False,
                'error': 'La imagen es muy grande. Máximo 5MB'
            }, status=400)
        
        # Eliminar foto anterior si existe
        if usuario.foto_perfil:
            try:
                usuario.foto_perfil.delete(save=False)
            except:
                pass
        
        # Procesar y redimensionar imagen si Pillow está disponible
        if PILLOW_AVAILABLE:
            try:
                img = Image.open(foto)
                
                # Convertir a RGB si es necesario (para PNG con transparencia)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                # Redimensionar si es muy grande (máximo 500x500)
                max_size = (500, 500)
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                
                # Guardar en memoria
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=85)
                output.seek(0)
                
                # Guardar nueva foto procesada
                from django.core.files.uploadedfile import InMemoryUploadedFile
                foto_procesada = InMemoryUploadedFile(
                    output,
                    'ImageField',
                    f"perfil_{usuario.id}.jpg",
                    'image/jpeg',
                    output.getbuffer().nbytes,
                    None
                )
                
                usuario.foto_perfil = foto_procesada
                usuario.save()
                
            except Exception as img_error:
                print(f"Error procesando imagen con Pillow: {img_error}")
                # Si falla el procesamiento, guardar la imagen original
                usuario.foto_perfil = foto
                usuario.save()
        else:
            # Sin Pillow, guardar la imagen original
            usuario.foto_perfil = foto
            usuario.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Foto de perfil actualizada exitosamente',
            'foto_url': usuario.foto_perfil.url if usuario.foto_perfil else None
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
@csrf_exempt
def eliminar_foto_perfil(request):
    """Eliminar la foto de perfil del usuario"""
    try:
        usuario = request.user
        
        if usuario.foto_perfil:
            try:
                usuario.foto_perfil.delete(save=False)
            except:
                pass
            usuario.foto_perfil = None
            usuario.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Foto de perfil eliminada'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ========== GESTIÓN DE SESIONES ==========

@login_required
@require_GET
def listar_sesiones(request):
    """Listar todas las sesiones activas del usuario"""
    try:
        sesiones = SesionActiva.objects.filter(
            usuario=request.user,
            activa=True
        ).order_by('-ultima_actividad')
        
        sesiones_data = []
        for sesion in sesiones:
            sesiones_data.append({
                'id': sesion.id,
                'dispositivo': sesion.dispositivo or 'Desconocido',
                'navegador': sesion.navegador or 'Desconocido',
                'sistema_operativo': sesion.sistema_operativo or 'Desconocido',
                'ip_address': sesion.ip_address,
                'fecha_inicio': sesion.fecha_inicio.strftime('%d/%m/%Y %H:%M'),
                'ultima_actividad': sesion.ultima_actividad.strftime('%d/%m/%Y %H:%M'),
                'es_actual': sesion.session_key == request.session.session_key
            })
        
        return JsonResponse({
            'success': True,
            'sesiones': sesiones_data
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
@csrf_exempt
def cerrar_sesion(request, sesion_id):
    """Cerrar una sesión específica"""
    try:
        sesion = SesionActiva.objects.filter(
            id=sesion_id,
            usuario=request.user,
            activa=True
        ).first()
        
        if not sesion:
            return JsonResponse({
                'success': False,
                'error': 'Sesión no encontrada'
            }, status=404)
        
        # No permitir cerrar la sesión actual
        if sesion.session_key == request.session.session_key:
            return JsonResponse({
                'success': False,
                'error': 'No puedes cerrar tu sesión actual. Usa el botón de cerrar sesión del menú.'
            }, status=400)
        
        sesion.cerrar_sesion()
        
        return JsonResponse({
            'success': True,
            'message': f'Sesión en {sesion.dispositivo} cerrada exitosamente'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
@csrf_exempt
def cerrar_todas_sesiones(request):
    """Cerrar todas las sesiones excepto la actual"""
    try:
        sesiones = SesionActiva.objects.filter(
            usuario=request.user,
            activa=True
        ).exclude(session_key=request.session.session_key)
        
        count = sesiones.count()
        
        for sesion in sesiones:
            sesion.cerrar_sesion()
        
        return JsonResponse({
            'success': True,
            'message': f'{count} sesión(es) cerrada(s) exitosamente'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ========== RESET DE PASSWORD POR CORREO ==========

@login_required
@require_POST
@csrf_exempt
def solicitar_cambio_password_email(request):
    """
    Envía un correo con link para cambiar la contraseña.
    El usuario debe estar logueado para solicitar esto.
    """
    try:
        usuario = request.user
        
        # Crear token de reset
        token_obj = TokenResetPassword.crear_token(usuario, request)
        
        # Construir URL del link
        site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/')
        reset_url = f"{site_url}{reverse('users:confirmar_cambio_password', args=[str(token_obj.token)])}"
        
        # Enviar correo
        enviar_email_reset_password(usuario, reset_url, token_obj)
        
        return JsonResponse({
            'success': True,
            'message': f'Se ha enviado un correo a {usuario.email} con instrucciones para cambiar tu contraseña'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


def confirmar_cambio_password(request, token):
    """
    Vista para confirmar el cambio de contraseña desde el link del correo.
    GET: Muestra el formulario para nueva contraseña
    POST: Procesa el cambio de contraseña
    """
    try:
        # Buscar token
        token_obj = TokenResetPassword.objects.filter(token=token).first()
        
        if not token_obj:
            return render(request, 'users/reset_password_error.html', {
                'error': 'Link inválido o expirado',
                'detalle': 'El enlace que utilizaste no es válido. Por favor solicita un nuevo enlace.'
            })
        
        if not token_obj.es_valido():
            return render(request, 'users/reset_password_error.html', {
                'error': 'Link expirado',
                'detalle': 'Este enlace ha expirado. Por favor solicita un nuevo enlace de recuperación.'
            })
        
        if request.method == 'POST':
            password_nueva = request.POST.get('password_nueva', '').strip()
            password_confirmar = request.POST.get('password_confirmar', '').strip()
            
            # Validaciones
            if not password_nueva or not password_confirmar:
                return render(request, 'users/reset_password_form.html', {
                    'token': token,
                    'error': 'Todos los campos son obligatorios'
                })
            
            if password_nueva != password_confirmar:
                return render(request, 'users/reset_password_form.html', {
                    'token': token,
                    'error': 'Las contraseñas no coinciden'
                })
            
            if len(password_nueva) < 6:
                return render(request, 'users/reset_password_form.html', {
                    'token': token,
                    'error': 'La contraseña debe tener al menos 6 caracteres'
                })
            
            # Cambiar contraseña
            usuario = token_obj.usuario
            usuario.set_password(password_nueva)
            usuario.requiere_cambio_password = False
            usuario.save()
            
            # Marcar token como usado
            token_obj.usado = True
            token_obj.save()
            
            # Cerrar todas las sesiones del usuario por seguridad
            SesionActiva.objects.filter(usuario=usuario, activa=True).update(activa=False)
            
            return render(request, 'users/reset_password_success.html', {
                'usuario': usuario
            })
        
        # GET: Mostrar formulario
        return render(request, 'users/reset_password_form.html', {
            'token': token,
            'usuario': token_obj.usuario
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return render(request, 'users/reset_password_error.html', {
            'error': 'Error inesperado',
            'detalle': str(e)
        })


def enviar_email_reset_password(usuario, reset_url, token_obj):
    """
    Envía el email con el link para resetear la contraseña
    """
    from django.core.mail import EmailMultiAlternatives
    
    subject = '🔑 NEXO - Cambio de Contraseña Solicitado'
    
    # Calcular tiempo de expiración
    horas_restantes = int((token_obj.fecha_expiracion - timezone.now()).total_seconds() / 3600)
    
    # Mensaje en texto plano
    text_message = f"""
Hola {usuario.get_full_name()},

Has solicitado cambiar tu contraseña en NEXO.

Para continuar, haz clic en el siguiente enlace:
{reset_url}

⚠️ Este enlace expirará en {horas_restantes} horas.

Si no solicitaste este cambio, puedes ignorar este correo y tu contraseña permanecerá igual.

Saludos cordiales,
Equipo NEXO
    """
    
    # Mensaje HTML
    html_message = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #F5F5F7; margin: 0; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #FFFFFF; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(26, 26, 46, 0.1); }}
            .header {{ background: linear-gradient(135deg, #0066FF 0%, #0052CC 100%); padding: 30px; text-align: center; }}
            .header h1 {{ color: #FFFFFF; margin: 0; font-size: 28px; }}
            .header p {{ color: rgba(255,255,255,0.85); margin: 10px 0 0 0; }}
            .content {{ padding: 30px; }}
            .btn {{ display: inline-block; background: linear-gradient(135deg, #00D4AA 0%, #00B38A 100%); color: #FFFFFF; padding: 16px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px; margin: 20px 0; }}
            .btn:hover {{ background: linear-gradient(135deg, #00B38A 0%, #009977 100%); }}
            .warning {{ background: rgba(255, 176, 32, 0.1); border-left: 4px solid #FFB020; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .warning strong {{ color: #996B00; }}
            .info {{ background: rgba(0, 102, 255, 0.1); border-left: 4px solid #0066FF; padding: 15px; border-radius: 8px; margin: 20px 0; color: #0052CC; }}
            .footer {{ background: #F5F5F7; padding: 20px; text-align: center; color: #8A8A9A; font-size: 12px; }}
            .link-text {{ word-break: break-all; background: #F5F5F7; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 12px; color: #4A4A5A; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔑 Cambio de Contraseña</h1>
                <p>Solicitud recibida</p>
            </div>
            <div class="content">
                <p style="color: #4A4A5A; font-size: 16px;">Hola <strong>{usuario.get_full_name()}</strong>,</p>
                <p style="color: #4A4A5A;">Has solicitado cambiar tu contraseña en NEXO. Haz clic en el botón de abajo para continuar:</p>
                
                <div style="text-align: center;">
                    <a href="{reset_url}" class="btn">Cambiar mi Contraseña</a>
                </div>
                
                <div class="warning">
                    <strong>⏰ Este enlace expirará en {horas_restantes} horas</strong><br>
                    Después de ese tiempo, deberás solicitar un nuevo enlace.
                </div>
                
                <div class="info">
                    <strong>💡 ¿No solicitaste este cambio?</strong><br>
                    Puedes ignorar este correo de forma segura. Tu contraseña no será modificada.
                </div>
                
                <p style="color: #8A8A9A; font-size: 12px; margin-top: 20px;">Si el botón no funciona, copia y pega este enlace en tu navegador:</p>
                <p class="link-text">{reset_url}</p>
            </div>
            <div class="footer">
                <p>Este correo fue enviado automáticamente por el sistema NEXO.</p>
                <p>© {timezone.now().year} NEXO - Sistema de Gestión Retail</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[usuario.email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        print(f"✅ Email de reset de password enviado a {usuario.email}")
    except Exception as e:
        print(f"❌ Error enviando correo de reset: {e}")
        raise e


# ========== APIS PARA GESTIÓN DE PERMISOS ==========

@require_GET
@login_required
def usuarios_por_rol(request):
    """
    API para obtener usuarios filtrados por rol
    RESTRINGIDO: Solo administradores
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido'
            }, status=403)
        
        rol = request.GET.get('rol', '')
        
        if not rol:
            return JsonResponse({
                'success': False,
                'error': 'Rol no especificado'
            }, status=400)
        
        # Obtener usuarios con ese rol
        usuarios = Usuario.objects.filter(rol=rol, es_activo=True).order_by('first_name', 'last_name')
        
        usuarios_data = []
        for usuario in usuarios:
            usuarios_data.append({
                'id': usuario.id,
                'nombre': usuario.first_name,
                'apellido': usuario.last_name,
                'email': usuario.email,
                'username': usuario.username,
                'rol': usuario.rol,
                'rol_display': usuario.get_rol_display(),
                'cargo': usuario.cargo or '',
                'es_activo': usuario.es_activo
            })
        
        return JsonResponse({
            'success': True,
            'usuarios': usuarios_data,
            'total': len(usuarios_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@require_POST
@login_required
@csrf_exempt
def cambiar_rol_usuario(request):
    """
    API para cambiar el rol de un usuario
    RESTRINGIDO: Solo administradores
    """
    try:
        # Verificar si es administrador
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({
                'success': False,
                'error': 'Acceso restringido'
            }, status=403)
        
        data = json.loads(request.body)
        user_id = data.get('user_id')
        nuevo_rol = data.get('nuevo_rol')
        
        if not user_id or not nuevo_rol:
            return JsonResponse({
                'success': False,
                'error': 'Parámetros incompletos'
            }, status=400)
        
        # Obtener usuario
        usuario = get_object_or_404(Usuario, id=user_id)
        
        # No permitir cambiar el rol del propio usuario
        if usuario.id == request.user.id:
            return JsonResponse({
                'success': False,
                'error': 'No puedes cambiar tu propio rol'
            }, status=400)
        
        # Validar que el rol sea válido
        roles_validos = ['administrador', 'administracion', 'jefe_local', 'cajero', 'vendedor', 'bodeguero', 'contador']
        if nuevo_rol not in roles_validos:
            return JsonResponse({
                'success': False,
                'error': f'Rol inválido. Roles permitidos: {", ".join(roles_validos)}'
            }, status=400)
        
        rol_anterior = usuario.get_rol_display()
        
        # Actualizar rol
        usuario.rol = nuevo_rol
        usuario.save()
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Rol de {usuario.get_full_name()} cambiado de "{rol_anterior}" a "{usuario.get_rol_display()}"',
            'usuario': {
                'id': usuario.id,
                'nombre': usuario.get_full_name(),
                'rol': usuario.rol,
                'rol_display': usuario.get_rol_display()
            }
        })
        
    except Usuario.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Usuario no encontrado'
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


# ========== GESTIÓN DE ASIGNACIONES EMPRESA/SUCURSAL ==========

@require_GET
@login_required
def obtener_empresas_sucursales(request):
    """
    Obtener todas las empresas con sus sucursales para el selector
    """
    try:
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({'success': False, 'error': 'Acceso restringido'}, status=403)
        
        empresas = Empresa.objects.all().order_by('nombre')
        resultado = []
        
        for empresa in empresas:
            sucursales = Sucursal.objects.filter(empresa=empresa).order_by('alias')
            resultado.append({
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut,
                'sucursales': [{
                    'id': s.id,
                    'alias': s.alias,
                    'direccion': s.direccion or ''
                } for s in sucursales]
            })
        
        return JsonResponse({'success': True, 'empresas': resultado})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@login_required
def obtener_asignaciones_usuario(request, usuario_id):
    """
    Obtener todas las asignaciones (EmpresaUser) de un usuario específico
    """
    try:
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({'success': False, 'error': 'Acceso restringido'}, status=403)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        
        # Obtener todas las asignaciones del usuario
        asignaciones = EmpresaUser.objects.filter(
            user=usuario
        ).select_related('empresa', 'sucursal').order_by('empresa__nombre', 'sucursal__alias')
        
        # Agrupar por empresa
        empresas_dict = {}
        for asig in asignaciones:
            emp_id = asig.empresa.id
            if emp_id not in empresas_dict:
                empresas_dict[emp_id] = {
                    'id': emp_id,
                    'nombre': asig.empresa.nombre,
                    'rut': asig.empresa.rut,
                    'sucursales': []
                }
            
            if asig.sucursal:
                empresas_dict[emp_id]['sucursales'].append({
                    'id': asig.sucursal.id,
                    'alias': asig.sucursal.alias,
                    'direccion': asig.sucursal.direccion or '',
                    'status': asig.status,
                    'active': asig.active,
                    'empresa_user_id': asig.id
                })
        
        return JsonResponse({
            'success': True,
            'usuario': {
                'id': usuario.id,
                'nombre': usuario.get_full_name(),
                'username': usuario.username
            },
            'asignaciones': list(empresas_dict.values())
        })
    except Usuario.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Usuario no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@login_required
@csrf_exempt
def agregar_asignacion_usuario(request, usuario_id):
    """
    Agregar una o más asignaciones empresa/sucursal a un usuario
    Soporta:
    - sucursal_id: ID único (compatibilidad)
    - sucursales_ids: Array de IDs (múltiple selección)
    """
    try:
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({'success': False, 'error': 'Acceso restringido'}, status=403)
        
        data = json.loads(request.body)
        marcar_activa = data.get('marcar_activa', False)
        
        # Soportar tanto sucursal_id único como sucursales_ids array
        sucursales_ids = data.get('sucursales_ids', [])
        if not sucursales_ids:
            sucursal_id = data.get('sucursal_id')
            if sucursal_id:
                sucursales_ids = [sucursal_id]
        
        if not sucursales_ids:
            return JsonResponse({'success': False, 'error': 'Debe seleccionar al menos una sucursal'}, status=400)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        
        asignaciones_creadas = []
        asignaciones_reactivadas = []
        asignaciones_existentes = []
        primera_asignacion = None
        
        for sucursal_id in sucursales_ids:
            sucursal = Sucursal.objects.select_related('empresa').filter(id=sucursal_id).first()
            if not sucursal:
                continue
            
            # Verificar si ya existe la asignación
            asig_existente = EmpresaUser.objects.filter(
                user=usuario,
                empresa=sucursal.empresa,
                sucursal=sucursal
            ).first()
            
            if asig_existente:
                # Si existe pero estaba desactivada, reactivarla
                if not asig_existente.status:
                    asig_existente.status = True
                    asig_existente.save()
                    asignaciones_reactivadas.append(sucursal.alias)
                    if not primera_asignacion:
                        primera_asignacion = asig_existente
                else:
                    asignaciones_existentes.append(sucursal.alias)
            else:
                # Crear nueva asignación
                nueva_asig = EmpresaUser.objects.create(
                    user=usuario,
                    empresa=sucursal.empresa,
                    sucursal=sucursal,
                    status=True,
                    active=False
                )
                asignaciones_creadas.append(sucursal.alias)
                if not primera_asignacion:
                    primera_asignacion = nueva_asig
        
        # Si se debe marcar la primera como activa
        if marcar_activa and primera_asignacion:
            EmpresaUser.objects.filter(user=usuario).update(active=False)
            primera_asignacion.active = True
            primera_asignacion.save()
        
        # Construir mensaje de respuesta
        mensajes = []
        if asignaciones_creadas:
            mensajes.append(f'Creadas: {", ".join(asignaciones_creadas)}')
        if asignaciones_reactivadas:
            mensajes.append(f'Reactivadas: {", ".join(asignaciones_reactivadas)}')
        if asignaciones_existentes:
            mensajes.append(f'Ya existían: {", ".join(asignaciones_existentes)}')
        
        total_procesadas = len(asignaciones_creadas) + len(asignaciones_reactivadas)
        
        if total_procesadas == 0 and asignaciones_existentes:
            return JsonResponse({
                'success': False,
                'error': f'Todas las sucursales ya estaban asignadas: {", ".join(asignaciones_existentes)}'
            }, status=400)
        
        mensaje = f'{total_procesadas} sucursal(es) asignada(s). ' + '. '.join(mensajes)
        if marcar_activa and primera_asignacion:
            mensaje += ' (primera marcada como activa)'
        
        return JsonResponse({
            'success': True,
            'message': mensaje,
            'creadas': len(asignaciones_creadas),
            'reactivadas': len(asignaciones_reactivadas),
            'existentes': len(asignaciones_existentes)
        })
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@login_required
@csrf_exempt
def eliminar_asignacion_usuario(request, usuario_id, empresa_user_id):
    """
    Eliminar (desactivar) una asignación empresa/sucursal de un usuario
    """
    try:
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({'success': False, 'error': 'Acceso restringido'}, status=403)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        asignacion = get_object_or_404(EmpresaUser.objects.select_related('empresa', 'sucursal'), 
                                        id=empresa_user_id, user=usuario)
        
        sucursal_alias = asignacion.sucursal.alias if asignacion.sucursal else 'Sin sucursal'
        empresa_nombre = asignacion.empresa.nombre
        era_activa = asignacion.active
        
        # Desactivar la asignación (soft delete)
        asignacion.status = False
        asignacion.active = False
        asignacion.save()
        
        # Si era la activa, buscar otra para activar
        if era_activa:
            otra_asignacion = EmpresaUser.objects.filter(
                user=usuario, 
                status=True,
                sucursal__isnull=False
            ).first()
            if otra_asignacion:
                otra_asignacion.active = True
                otra_asignacion.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Asignación eliminada: {empresa_nombre} - {sucursal_alias}'
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@login_required
@csrf_exempt
def cambiar_sucursal_activa_usuario(request, usuario_id):
    """
    Cambiar la sucursal activa de un usuario (sin crear nueva asignación)
    """
    try:
        es_admin = getattr(request.user, 'rol', None) == 'administrador'
        if not es_admin:
            return JsonResponse({'success': False, 'error': 'Acceso restringido'}, status=403)
        
        data = json.loads(request.body)
        empresa_user_id = data.get('empresa_user_id')
        
        if not empresa_user_id:
            return JsonResponse({'success': False, 'error': 'Debe seleccionar una asignación'}, status=400)
        
        usuario = get_object_or_404(Usuario, id=usuario_id)
        asignacion = get_object_or_404(EmpresaUser.objects.select_related('empresa', 'sucursal'),
                                        id=empresa_user_id, user=usuario, status=True)
        
        # Desactivar todas y activar la seleccionada
        EmpresaUser.objects.filter(user=usuario).update(active=False)
        asignacion.active = True
        asignacion.save()
        
        sucursal_alias = asignacion.sucursal.alias if asignacion.sucursal else 'Sin sucursal'
        
        return JsonResponse({
            'success': True,
            'message': f'Sucursal activa cambiada a: {asignacion.empresa.nombre} - {sucursal_alias}',
            'sucursal_activa': {
                'empresa_user_id': asignacion.id,
                'empresa': asignacion.empresa.nombre,
                'sucursal': sucursal_alias
            }
        })
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
