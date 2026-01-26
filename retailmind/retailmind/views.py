from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from app.models import EmpresaUser


@require_GET
def check_session_status(request):
    """
    API endpoint para verificar si la sesión del usuario sigue activa.
    Usado para polling periódico desde el frontend.
    """
    if request.user.is_authenticated:
        return JsonResponse({
            'authenticated': True,
            'username': request.user.username,
            'email': request.user.email
        })
    else:
        return JsonResponse({
            'authenticated': False
        })
def _obtener_codigo_2fa(usuario):
    modo_pin = getattr(settings, 'PIN_2FA_MODE', 'session')
    if modo_pin == 'daily':
        if usuario.codigo_2fa and usuario.fecha_codigo_2fa:
            if usuario.fecha_codigo_2fa.date() == timezone.now().date():
                return usuario.codigo_2fa

    return usuario.generar_codigo_2fa()


def _enviar_pin_2fa(usuario, codigo):
    subject = '🔐 NEXO - Tu código de acceso'
    nombre = usuario.get_full_name() or usuario.username
    
    # Mensaje de texto plano (fallback)
    text_message = f"""
Hola {nombre},

Tu código de acceso es: {codigo}

Este código es válido por 10 minutos.
Si no solicitaste este código, ignora este mensaje.

— Equipo NEXO
    """.strip()

    # Mensaje HTML con diseño moderno
    html_message = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f7;">
        <table role="presentation" style="width: 100%; border-collapse: collapse;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" style="width: 100%; max-width: 440px; border-collapse: collapse;">
                        <!-- Header -->
                        <tr>
                            <td style="background: linear-gradient(135deg, #1A1A2E 0%, #2A2A4E 100%); padding: 30px 40px; border-radius: 16px 16px 0 0; text-align: center;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 700; letter-spacing: -0.5px;">
                                    🔐 Código de Acceso
                                </h1>
                            </td>
                        </tr>
                        
                        <!-- Body -->
                        <tr>
                            <td style="background-color: #ffffff; padding: 40px; border-radius: 0 0 16px 16px; box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);">
                                <p style="margin: 0 0 24px; color: #4A4A5A; font-size: 16px; line-height: 1.5;">
                                    Hola <strong style="color: #1A1A2E;">{nombre}</strong>,
                                </p>
                                
                                <p style="margin: 0 0 24px; color: #4A4A5A; font-size: 15px; line-height: 1.5;">
                                    Usa el siguiente código para completar tu inicio de sesión:
                                </p>
                                
                                <!-- Código PIN destacado -->
                                <div style="background: linear-gradient(135deg, #0066FF 0%, #0052CC 100%); border-radius: 12px; padding: 24px; text-align: center; margin: 0 0 24px;">
                                    <span style="font-family: 'SF Mono', 'Consolas', 'Monaco', monospace; font-size: 42px; font-weight: 700; color: #ffffff; letter-spacing: 12px; text-shadow: 0 2px 4px rgba(0,0,0,0.2);">
                                        {codigo}
                                    </span>
                                </div>
                                
                                <div style="background-color: #FFF8E6; border-left: 4px solid #FFB020; padding: 16px; border-radius: 0 8px 8px 0; margin: 0 0 24px;">
                                    <p style="margin: 0; color: #8B6914; font-size: 14px;">
                                        ⏱️ Este código es válido por <strong>10 minutos</strong>.
                                    </p>
                                </div>
                                
                                <p style="margin: 0; color: #8A8A9A; font-size: 13px; line-height: 1.5;">
                                    Si no solicitaste este código, puedes ignorar este mensaje de forma segura. Tu cuenta permanece protegida.
                                </p>
                            </td>
                        </tr>
                        
                        <!-- Footer -->
                        <tr>
                            <td style="padding: 24px 40px; text-align: center;">
                                <p style="margin: 0; color: #8A8A9A; font-size: 12px;">
                                    Este es un mensaje automático de <strong style="color: #1A1A2E;">NEXO</strong>
                                </p>
                                <p style="margin: 8px 0 0; color: #C1C1C9; font-size: 11px;">
                                    © {timezone.now().year} RetailMind. Todos los derechos reservados.
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    send_mail(
        subject=subject,
        message=text_message,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        recipient_list=[usuario.email],
        html_message=html_message,
        fail_silently=False,
    )


def _finalizar_login(request, user):
    login(request, user)

    # Verificar si el usuario necesita cambiar su contraseña
    if hasattr(user, 'requiere_cambio_password') and user.requiere_cambio_password:
        messages.warning(request, '🔐 Debes cambiar tu contraseña temporal para continuar.')
        return redirect('cambiar_password_obligatorio')

    try:
        # Buscar todas las empresas activas del usuario
        empresas_activas = EmpresaUser.objects.filter(user=user, active=True)

        if empresas_activas.exists():
            # Si hay múltiples empresas activas, tomar la primera (puedes cambiar esta lógica)
            # Alternativa: ordenar por fecha de creación o permitir al usuario elegir
            empresa_user = empresas_activas.first()

            request.session['idEmpresaActual'] = empresa_user.empresa.id
            request.session['idSucursalActual'] = empresa_user.sucursal.id if empresa_user.sucursal else None
            request.session['direccionSucursal'] = empresa_user.sucursal.direccion if empresa_user.sucursal else 'Sin dirección'
            request.session['alias'] = empresa_user.sucursal.alias if empresa_user.sucursal else 'Sin sucursal'
            request.session['nombreEmpresaActual'] = empresa_user.empresa.nombre
            request.session['rutEmpresaActual'] = empresa_user.empresa.rut

            # Si hay múltiples empresas, mostrar mensaje informativo
            if empresas_activas.count() > 1:
                messages.info(request, f'Tienes acceso a {empresas_activas.count()} empresas. Actualmente trabajando con: {empresa_user.empresa.nombre}')

            return redirect('verHome')

        messages.warning(request, 'No tienes una empresa activa asignada. Contacta al administrador.')
        return redirect('verHome')
    except Exception as e:
        messages.error(request, f'Error al acceder a la empresa: {str(e)}')
        return redirect('verHome')


def _requiere_2fa(user):
    return getattr(user, 'requiere_2fa', False)


def login_view(request):
    # Si el usuario ya está autenticado, mostrar opción de continuar o cambiar cuenta
    if request.user.is_authenticated:
        # Si hace clic en "continuar", redirigir al home
        if request.GET.get('continue') == 'true':
            return redirect('verHome')
        # Si hace clic en "cambiar cuenta", cerrar sesión y mostrar login
        if request.GET.get('switch') == 'true':
            logout(request)
            return redirect('login')
        # Mostrar template con sesión activa
        return render(request, 'registration/login.html', {'session_active': True})

    if request.method == 'POST':
        email = request.POST['email'].lower()
        password = request.POST['password-input']
        User = get_user_model()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            messages.error(request, 'No existe un usuario con ese correo electrónico.')
            return render(request, 'registration/login.html')

        user = authenticate(request, username=user.username, password=password)
        if user is not None:
            if _requiere_2fa(user):
                if not user.email:
                    messages.error(request, 'No tienes un correo registrado para recibir el PIN.')
                    return render(request, 'registration/login.html')

                try:
                    codigo = _obtener_codigo_2fa(user)
                    _enviar_pin_2fa(user, codigo)
                    request.session['pending_2fa_user_id'] = user.id
                    request.session['pending_2fa_created_at'] = timezone.now().isoformat()
                    return redirect('login_2fa')
                except Exception as e:
                    messages.error(request, f'No se pudo enviar el PIN: {str(e)}')
                    return render(request, 'registration/login.html')

            return _finalizar_login(request, user)

        messages.error(request, 'Credenciales incorrectas. Por favor, inténtalo de nuevo.')

    return render(request, 'registration/login.html')


def login_2fa_view(request):
    user_id = request.session.get('pending_2fa_user_id')
    if not user_id:
        messages.error(request, 'La verificación de PIN no está activa. Inicia sesión nuevamente.')
        return redirect('login')

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, 'El usuario no es válido. Inicia sesión nuevamente.')
        return redirect('login')

    if request.method == 'POST':
        codigo = request.POST.get('pin', '').strip()
        if not codigo:
            messages.error(request, 'Debes ingresar el PIN recibido.')
            return render(request, 'registration/login_2fa.html', {'email': user.email})

        modo_pin = getattr(settings, 'PIN_2FA_MODE', 'session')
        minutos_expiracion = 1440 if modo_pin == 'daily' else 10
        if user.validar_codigo_2fa(codigo, minutos_expiracion=minutos_expiracion):
            request.session.pop('pending_2fa_user_id', None)
            request.session.pop('pending_2fa_created_at', None)
            return _finalizar_login(request, user)

        messages.error(request, 'El PIN es incorrecto o ha expirado. Solicita uno nuevo.')
        return render(request, 'registration/login_2fa.html', {'email': user.email})

    if request.GET.get('resend') == 'true':
        try:
            codigo = _obtener_codigo_2fa(user)
            _enviar_pin_2fa(user, codigo)
            messages.info(request, 'Se envió un nuevo PIN a tu correo.')
        except Exception as e:
            messages.error(request, f'No se pudo reenviar el PIN: {str(e)}')

    return render(request, 'registration/login_2fa.html', {'email': user.email})

def logout_view(request):
    logout(request) 
    return render(request, 'registration/logout.html')

