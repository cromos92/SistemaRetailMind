import logging
import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from app.models import EmpresaUser

logger = logging.getLogger('users')


def csrf_failure(request, reason=""):
    """Vista personalizada para errores CSRF: redirige al login con mensaje claro."""
    messages.warning(
        request,
        'Tu sesión ha expirado o el formulario quedó desactualizado. Por favor, intenta nuevamente.'
    )
    next_url = request.GET.get('next') or request.POST.get('next', '')
    if next_url:
        return redirect(f'/?next={next_url}')
    return redirect('/')


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
            if usuario.fecha_codigo_2fa.date() == timezone.localdate():
                return usuario.codigo_2fa

    return usuario.generar_codigo_2fa()


def _enviar_pin_2fa(usuario, codigo):
    subject = f'🔐 NEXO - Tu código de acceso: {codigo}'
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


def _destino_post_login(request, user):
    """Deja la sesión lista (empresa/sucursal) y devuelve la URL de destino.

    Se separó de `_finalizar_login` porque el mismo cálculo lo necesitan el
    login normal, el ingreso con Google y el reintento del PIN cuando el
    usuario ya quedó autenticado (doble click en "Verificar").
    """
    if getattr(user, 'requiere_cambio_password', False):
        messages.warning(request, '🔐 Debes cambiar tu contraseña temporal para continuar.')
        return reverse('cambiar_password_obligatorio')

    try:
        # 1) Buscar EmpresaUser activa con sucursal
        empresa_user = EmpresaUser.objects.filter(
            user=user, active=True, sucursal__isnull=False
        ).select_related('empresa', 'sucursal').first()

        # 2) Si no hay activa con sucursal, buscar cualquiera con status=True y sucursal
        if not empresa_user:
            empresa_user = EmpresaUser.objects.filter(
                user=user, status=True, sucursal__isnull=False
            ).select_related('empresa', 'sucursal').first()
            if empresa_user:
                EmpresaUser.objects.filter(user=user).update(active=False)
                empresa_user.active = True
                empresa_user.save(update_fields=['active'])

        if empresa_user:
            request.session['idEmpresaActual'] = empresa_user.empresa.id
            request.session['idSucursalActual'] = empresa_user.sucursal.id
            request.session['direccionSucursal'] = empresa_user.sucursal.direccion or 'Sin dirección'
            request.session['alias'] = empresa_user.sucursal.alias or 'Sin sucursal'
            request.session['nombreEmpresaActual'] = empresa_user.empresa.nombre
            request.session['rutEmpresaActual'] = empresa_user.empresa.rut
            return reverse('verHome')

        # 3) Tiene empresa pero sin sucursal → cargar empresa y redirigir a seleccionar sucursal
        empresa_sin_suc = EmpresaUser.objects.filter(
            user=user, status=True
        ).select_related('empresa').first()

        if empresa_sin_suc:
            request.session['idEmpresaActual'] = empresa_sin_suc.empresa.id
            request.session['idSucursalActual'] = None
            request.session['alias'] = 'Sin sucursal'
            request.session['nombreEmpresaActual'] = empresa_sin_suc.empresa.nombre
            request.session['rutEmpresaActual'] = empresa_sin_suc.empresa.rut
            messages.warning(request, 'No tienes sucursal asignada. Selecciona una para continuar, o contacta al administrador.')
            return reverse('cambiar_empresa')

        messages.warning(request, 'No tienes una empresa activa asignada. Contacta al administrador.')
        return reverse('verHome')
    except Exception as e:
        messages.error(request, f'Error al acceder a la empresa: {str(e)}')
        return reverse('verHome')


def _finalizar_login(request, user):
    # Si el usuario llega al 2FA por el flujo passwordless no pasó por authenticate(),
    # por lo que Django no tiene asociado un backend de auth al request. Se lo anexamos
    # explícitamente para poder llamar a login() sin errores.
    if not hasattr(user, 'backend'):
        user.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, user)
    return redirect(_destino_post_login(request, user))


def _requiere_2fa(user):
    if getattr(settings, 'REQUIRE_2FA_FOR_ALL', False):
        return True
    return getattr(user, 'requiere_2fa', False)


def _es_ajax(request):
    """True si quien pide espera JSON (fetch del login/PIN) y no un render."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    return 'application/json' in (request.headers.get('Accept') or '')


def _login_context(request, **extra):
    """Contexto base de `registration/login.html`.

    Centraliza lo que el template necesita siempre —el client id de Google y el
    correo ya tecleado— para no repetirlo en cada `render()` del flujo.
    """
    ctx = {
        'google_client_id': getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '') or '',
        'email_previo': (request.POST.get('email') or '').strip().lower(),
    }
    ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------------
#  Estado del PIN 2FA en la sesión
# ---------------------------------------------------------------------------
# Mientras dura la verificación guardamos en sesión, además del usuario
# pendiente, los intentos fallidos y la marca del último envío. Eso permite
# frenar la fuerza bruta y el spam de reenvíos sin tocar el modelo Usuario.

def _pin_max_intentos():
    return int(getattr(settings, 'PIN_2FA_MAX_INTENTOS', 5))


def _pin_cooldown_reenvio():
    return int(getattr(settings, 'PIN_2FA_REENVIO_COOLDOWN', 60))


def _pin_minutos_expiracion():
    return 1440 if getattr(settings, 'PIN_2FA_MODE', 'session') == 'daily' else 10


def _limpiar_pendiente_2fa(request):
    for clave in ('pending_2fa_user_id', 'pending_2fa_created_at',
                  'pending_2fa_passwordless', 'pending_2fa_intentos',
                  'pending_2fa_ultimo_envio'):
        request.session.pop(clave, None)


def _marcar_pendiente_2fa(request, user, passwordless=False):
    request.session['pending_2fa_user_id'] = user.id
    request.session['pending_2fa_created_at'] = timezone.now().isoformat()
    request.session['pending_2fa_ultimo_envio'] = timezone.now().isoformat()
    request.session['pending_2fa_intentos'] = 0
    if passwordless:
        request.session['pending_2fa_passwordless'] = True


def _segundos_desde_ultimo_envio(request):
    """Segundos transcurridos desde el último envío de PIN (None si no hay marca)."""
    marca = request.session.get('pending_2fa_ultimo_envio')
    if not marca:
        return None
    try:
        enviado = datetime.fromisoformat(marca)
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(enviado):
        enviado = timezone.make_aware(enviado, timezone.get_current_timezone())
    return (timezone.now() - enviado).total_seconds()


def _segundos_para_reenvio(request):
    """Segundos que faltan para poder pedir otro PIN (0 si ya se puede)."""
    transcurridos = _segundos_desde_ultimo_envio(request)
    if transcurridos is None:
        return 0
    return max(0, int(_pin_cooldown_reenvio() - transcurridos))


def _segundos_para_expirar(request):
    """Segundos de vida que le quedan al PIN vigente (0 si ya no sirve)."""
    transcurridos = _segundos_desde_ultimo_envio(request)
    if transcurridos is None:
        return 0
    return max(0, int(_pin_minutos_expiracion() * 60 - transcurridos))


def _invalidar_codigo_2fa(usuario):
    usuario.codigo_2fa = None
    usuario.fecha_codigo_2fa = None
    usuario.save(update_fields=['codigo_2fa', 'fecha_codigo_2fa'])


def _contexto_2fa(request, user, **extra):
    intentos = int(request.session.get('pending_2fa_intentos', 0))
    ctx = {
        'email': user.email,
        'nombre': user.get_full_name() or user.username,
        'max_intentos': _pin_max_intentos(),
        'intentos_restantes': max(0, _pin_max_intentos() - intentos),
        'segundos_reenvio': _segundos_para_reenvio(request),
        'segundos_expiracion': _segundos_para_expirar(request),
        'minutos_expiracion': _pin_minutos_expiracion(),
        'passwordless': bool(request.session.get('pending_2fa_passwordless')),
    }
    ctx.update(extra)
    return ctx


def _reenviar_pin_2fa(request, user):
    """Genera y reenvía el PIN respetando el cooldown.

    Devuelve `(ok, mensaje, segundos_de_espera)`.
    """
    espera = _segundos_para_reenvio(request)
    if espera > 0:
        return False, f'Espera {espera} segundos antes de pedir otro PIN.', espera

    try:
        codigo = _obtener_codigo_2fa(user)
        _enviar_pin_2fa(user, codigo)
    except Exception as e:
        logger.error('No se pudo reenviar el PIN 2FA a %s: %s', user.email, e)
        return False, f'No se pudo reenviar el PIN: {str(e)}', 0

    request.session['pending_2fa_ultimo_envio'] = timezone.now().isoformat()
    request.session['pending_2fa_intentos'] = 0
    return True, 'Se envió un nuevo PIN a tu correo.', _pin_cooldown_reenvio()


@require_POST
def check_login_method_view(request):
    """API JSON: recibe un email y responde si el usuario existe y si usa
    PIN (2FA passwordless) o si debe ingresar contraseña.

    Se usa para el flujo de login en 2 pasos: primero se pide el correo y,
    según la configuración del usuario, se muestra el campo de contraseña
    o directamente se lo envía al flujo de PIN por correo.
    """
    email = (request.POST.get('email') or '').strip().lower()
    if not email:
        return JsonResponse({
            'ok': False,
            'error': 'Debes ingresar tu correo electrónico.'
        }, status=400)

    User = get_user_model()
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return JsonResponse({
            'ok': False,
            'exists': False,
            'error': 'No existe un usuario con ese correo electrónico.'
        })

    if not getattr(user, 'is_active', True) or not getattr(user, 'es_activo', True):
        return JsonResponse({
            'ok': False,
            'exists': True,
            'is_active': False,
            'error': 'Tu cuenta está desactivada. Contacta al administrador.'
        })

    requiere_pin = _requiere_2fa(user)
    if requiere_pin and not user.email:
        return JsonResponse({
            'ok': False,
            'error': 'No tienes un correo registrado para recibir el PIN.'
        })

    return JsonResponse({
        'ok': True,
        'exists': True,
        'is_active': True,
        'requiere_pin': bool(requiere_pin),
        'nombre': user.get_full_name() or user.username,
    })


@ensure_csrf_cookie
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
        return render(request, 'registration/login.html', _login_context(request, session_active=True))

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()
        password = request.POST.get('password-input') or ''
        User = get_user_model()

        if not email or not password:
            messages.error(request, 'Debes ingresar tu correo y tu contraseña.')
            return render(request, 'registration/login.html', _login_context(request))

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            messages.error(request, 'No existe un usuario con ese correo electrónico.')
            return render(request, 'registration/login.html', _login_context(request))

        user = authenticate(request, username=user.username, password=password)
        if user is not None:
            if _requiere_2fa(user):
                if not user.email:
                    messages.error(request, 'No tienes un correo registrado para recibir el PIN.')
                    return render(request, 'registration/login.html', _login_context(request))

                try:
                    codigo = _obtener_codigo_2fa(user)
                    _enviar_pin_2fa(user, codigo)
                    _marcar_pendiente_2fa(request, user)
                    return redirect('login_2fa')
                except Exception as e:
                    logger.error('No se pudo enviar el PIN 2FA a %s: %s', user.email, e)
                    messages.error(request, f'No se pudo enviar el PIN: {str(e)}')
                    return render(request, 'registration/login.html', _login_context(request))

            return _finalizar_login(request, user)

        messages.error(request, 'Credenciales incorrectas. Por favor, inténtalo de nuevo.')
        # `stage_previo` deja al front en el paso de contraseña con el correo ya
        # cargado, en vez de mandar al usuario de vuelta a teclear su correo.
        return render(request, 'registration/login.html', _login_context(request, stage_previo='password'))

    return render(request, 'registration/login.html', _login_context(request))


@ensure_csrf_cookie
def login_pin_request_view(request):
    """
    Login passwordless (solo con PIN por correo).

    Solo se permite si el usuario tiene `requiere_2fa=True`. Si no lo tiene,
    se le indica que debe usar el login tradicional con contraseña.
    """
    if request.user.is_authenticated:
        return redirect('verHome')

    if request.method != 'POST':
        return redirect('login')

    email = (request.POST.get('email') or '').strip().lower()
    if not email:
        messages.error(request, 'Ingresa tu correo electrónico para recibir el PIN.')
        return render(request, 'registration/login.html', _login_context(request))

    User = get_user_model()
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        messages.error(request, 'No existe un usuario con ese correo electrónico.')
        return render(request, 'registration/login.html', _login_context(request))

    if not getattr(user, 'is_active', True) or not getattr(user, 'es_activo', True):
        messages.error(request, 'Tu cuenta está desactivada. Contacta al administrador.')
        return render(request, 'registration/login.html', _login_context(request))

    if not _requiere_2fa(user):
        messages.error(
            request,
            'Este usuario no tiene habilitado el ingreso por PIN. Inicia sesión con tu contraseña.'
        )
        return render(request, 'registration/login.html', _login_context(request, stage_previo='password'))

    if not user.email:
        messages.error(request, 'No tienes un correo registrado para recibir el PIN.')
        return render(request, 'registration/login.html', _login_context(request))

    try:
        codigo = _obtener_codigo_2fa(user)
        _enviar_pin_2fa(user, codigo)
    except Exception as e:
        logger.error('No se pudo enviar el PIN 2FA a %s: %s', user.email, e)
        messages.error(request, f'No se pudo enviar el PIN: {str(e)}')
        return render(request, 'registration/login.html', _login_context(request, stage_previo='pin'))

    # Marca que el flujo es passwordless (no hubo validación de contraseña previa).
    _marcar_pendiente_2fa(request, user, passwordless=True)
    return redirect('login_2fa')


def _procesar_pin_2fa(request, user):
    """Valida el PIN enviado por POST. Responde JSON si la petición es AJAX."""
    ajax = _es_ajax(request)
    # El PIN suele llegar pegado desde el correo con espacios o guiones: nos
    # quedamos solo con los dígitos antes de comparar.
    codigo = re.sub(r'\D', '', request.POST.get('pin', '') or '')

    def responder_error(mensaje, **extra):
        if ajax:
            payload = {'ok': False, 'error': mensaje}
            payload.update(extra)
            return JsonResponse(payload)
        messages.error(request, mensaje)
        return render(request, 'registration/login_2fa.html', _contexto_2fa(request, user, **extra))

    if not codigo:
        return responder_error('Debes ingresar el PIN recibido.')

    max_intentos = _pin_max_intentos()
    intentos = int(request.session.get('pending_2fa_intentos', 0))

    if user.validar_codigo_2fa(codigo, minutos_expiracion=_pin_minutos_expiracion()):
        # En modo 'session' el PIN es de un solo uso: se consume al validarlo
        # para que no siga vivo los minutos que le quedaban.
        if getattr(settings, 'PIN_2FA_MODE', 'session') != 'daily':
            _invalidar_codigo_2fa(user)
        _limpiar_pendiente_2fa(request)
        respuesta = _finalizar_login(request, user)
        if ajax:
            return JsonResponse({'ok': True, 'redirect': respuesta['Location']})
        return respuesta

    intentos += 1
    request.session['pending_2fa_intentos'] = intentos
    restantes = max(0, max_intentos - intentos)

    if restantes == 0:
        # Se agotaron los intentos: matamos el código y el estado pendiente para
        # que tenga que pedir otro desde el inicio de sesión.
        _invalidar_codigo_2fa(user)
        _limpiar_pendiente_2fa(request)
        logger.warning('PIN 2FA bloqueado por intentos agotados: %s', user.email)
        return responder_error(
            'El PIN es incorrecto y se acabaron los intentos. Inicia sesión de nuevo para pedir otro.',
            expirado=True,
            redirect=reverse('login'),
        )

    if restantes == 1:
        detalle = 'Te queda 1 intento.'
    else:
        detalle = f'Te quedan {restantes} intentos.'

    return responder_error(
        f'El PIN es incorrecto o ha expirado. {detalle}',
        intentos_restantes=restantes,
    )


@ensure_csrf_cookie
def login_2fa_view(request):
    user_id = request.session.get('pending_2fa_user_id')

    if not user_id:
        # Caso clásico del doble click en "Verificar PIN": el primer POST ya
        # validó el código, hizo login y limpió el estado pendiente. El segundo
        # llegaba sin `pending_2fa_user_id` y terminaba en un error falso que
        # devolvía al usuario al login estando ya autenticado.
        if request.user.is_authenticated:
            destino = _destino_post_login(request, request.user)
            if _es_ajax(request):
                return JsonResponse({'ok': True, 'redirect': destino})
            return redirect(destino)

        if _es_ajax(request):
            return JsonResponse({
                'ok': False,
                'expirado': True,
                'error': 'La verificación de PIN no está activa. Inicia sesión nuevamente.',
                'redirect': reverse('login'),
            })
        messages.error(request, 'La verificación de PIN no está activa. Inicia sesión nuevamente.')
        return redirect('login')

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        _limpiar_pendiente_2fa(request)
        if _es_ajax(request):
            return JsonResponse({
                'ok': False,
                'expirado': True,
                'error': 'El usuario no es válido. Inicia sesión nuevamente.',
                'redirect': reverse('login'),
            })
        messages.error(request, 'El usuario no es válido. Inicia sesión nuevamente.')
        return redirect('login')

    if request.method == 'POST':
        return _procesar_pin_2fa(request, user)

    if request.GET.get('resend') == 'true':
        # Fallback sin JS. Se responde con redirect (patrón POST/redirect/GET)
        # para que un F5 no dispare otro correo.
        ok, mensaje, _espera = _reenviar_pin_2fa(request, user)
        if ok:
            messages.info(request, mensaje)
        else:
            messages.error(request, mensaje)
        return redirect('login_2fa')

    return render(request, 'registration/login_2fa.html', _contexto_2fa(request, user))


@require_POST
def login_2fa_resend_view(request):
    """Reenvío del PIN por AJAX, con cooldown, sin recargar la pantalla."""
    user_id = request.session.get('pending_2fa_user_id')
    if not user_id:
        return JsonResponse({
            'ok': False,
            'expirado': True,
            'error': 'La verificación de PIN no está activa. Inicia sesión nuevamente.',
            'redirect': reverse('login'),
        })

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        _limpiar_pendiente_2fa(request)
        return JsonResponse({
            'ok': False,
            'expirado': True,
            'error': 'El usuario no es válido. Inicia sesión nuevamente.',
            'redirect': reverse('login'),
        })

    ok, mensaje, espera = _reenviar_pin_2fa(request, user)
    payload = {'ok': ok, 'espera': espera, 'intentos_restantes': _pin_max_intentos()}
    payload['mensaje' if ok else 'error'] = mensaje
    return JsonResponse(payload)


# ---------------------------------------------------------------------------
#  Ingreso con Google (Google Identity Services)
# ---------------------------------------------------------------------------

def _verificar_id_token_google(token):
    """Valida el ID token que devuelve el botón de Google y entrega su payload.

    Si `google-auth` está instalado se verifica la firma localmente; si no, se
    cae al endpoint `tokeninfo` de Google usando `requests` (ya presente en el
    runtime). Así habilitar el ingreso con Google no obliga a instalar una
    dependencia nueva en el servidor que atiende el POS.

    Lanza `ValueError` con un mensaje mostrable si el token no sirve.
    """
    client_id = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '') or ''
    if not client_id:
        raise ValueError('El ingreso con Google no está configurado en el servidor.')

    try:
        from google.oauth2 import id_token as google_id_token  # import perezoso
        from google.auth.transport import requests as google_requests
    except ImportError:
        google_id_token = None

    if google_id_token is not None:
        payload = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), client_id
        )
    else:
        import requests as http_requests

        respuesta = http_requests.get(
            'https://oauth2.googleapis.com/tokeninfo',
            params={'id_token': token},
            timeout=10,
        )
        if respuesta.status_code != 200:
            raise ValueError('Google rechazó el token de acceso.')
        payload = respuesta.json()

    if payload.get('aud') != client_id:
        raise ValueError('El token no fue emitido para esta aplicación.')

    if payload.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise ValueError('El emisor del token no es Google.')

    # `verify_oauth2_token` ya valida la expiración; `tokeninfo` la entrega como
    # string y hay que revisarla a mano.
    try:
        expira = int(payload.get('exp') or 0)
    except (TypeError, ValueError):
        expira = 0
    if expira and expira < int(timezone.now().timestamp()):
        raise ValueError('El token de Google expiró. Intenta nuevamente.')

    if str(payload.get('email_verified', '')).lower() not in ('true', '1'):
        raise ValueError('Tu correo de Google no está verificado.')

    correo = (payload.get('email') or '').strip().lower()
    if not correo:
        raise ValueError('Google no entregó un correo para esta cuenta.')

    payload['email'] = correo
    return payload


@require_POST
def google_login_view(request):
    """Ingreso con Google. El navegador manda el ID token y acá se valida.

    NUNCA crea usuarios: la cuenta tiene que existir y estar activa en NEXO. El
    match es por correo, la misma llave que usa el login tradicional.
    """
    if request.user.is_authenticated:
        return JsonResponse({'ok': True, 'redirect': reverse('verHome')})

    token = (request.POST.get('credential') or '').strip()
    if not token:
        return JsonResponse({'ok': False, 'error': 'No se recibió el token de Google.'})

    try:
        datos = _verificar_id_token_google(token)
    except ValueError as e:
        logger.warning('Login con Google rechazado: %s', e)
        return JsonResponse({'ok': False, 'error': str(e)})
    except Exception as e:
        logger.error('Error validando el token de Google: %s', e)
        return JsonResponse({
            'ok': False,
            'error': 'No se pudo validar tu cuenta de Google. Intenta nuevamente.'
        })

    correo = datos['email']

    dominios = getattr(settings, 'GOOGLE_OAUTH_ALLOWED_DOMAINS', []) or []
    if dominios and correo.rsplit('@', 1)[-1] not in dominios:
        return JsonResponse({
            'ok': False,
            'error': 'Tu dominio de correo no está autorizado para ingresar.'
        })

    User = get_user_model()
    candidatos = User.objects.filter(email__iexact=correo)
    user = candidatos.filter(is_active=True, es_activo=True).first() or candidatos.first()

    if user is None:
        return JsonResponse({
            'ok': False,
            'error': f'No hay un usuario de NEXO con el correo {correo}. Pídele a un administrador que lo cree.'
        })

    if not getattr(user, 'is_active', True) or not getattr(user, 'es_activo', True):
        return JsonResponse({'ok': False, 'error': 'Tu cuenta está desactivada. Contacta al administrador.'})

    # El PIN por correo sirve para probar que controlas el buzón; Google ya lo
    # probó con ese mismo correo verificado, así que por defecto no se vuelve a
    # pedir. Se puede exigir igual con GOOGLE_OAUTH_BYPASS_2FA=False.
    if _requiere_2fa(user) and not getattr(settings, 'GOOGLE_OAUTH_BYPASS_2FA', True):
        if not user.email:
            return JsonResponse({'ok': False, 'error': 'No tienes un correo registrado para recibir el PIN.'})
        try:
            codigo = _obtener_codigo_2fa(user)
            _enviar_pin_2fa(user, codigo)
        except Exception as e:
            logger.error('No se pudo enviar el PIN 2FA a %s: %s', user.email, e)
            return JsonResponse({'ok': False, 'error': f'No se pudo enviar el PIN: {str(e)}'})
        _marcar_pendiente_2fa(request, user, passwordless=True)
        return JsonResponse({'ok': True, 'redirect': reverse('login_2fa')})

    logger.info('Login con Google exitoso: %s', correo)
    respuesta = _finalizar_login(request, user)
    return JsonResponse({'ok': True, 'redirect': respuesta['Location']})


def logout_view(request):
    logout(request)
    return render(request, 'registration/logout.html')
