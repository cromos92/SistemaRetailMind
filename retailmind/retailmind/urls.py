"""
URL configuration for retailmind project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from . import views
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.decorators import login_required
from django.views.static import serve

from app.views_modulo_fidelizacion import descargar_app_puntos

urlpatterns = [
    path('admin/', admin.site.urls),
    path('app/', include('app.urls')),
    path('empresa_management/', include('empresa_management.urls')),
    path('users/', include('users.urls')),  # Nueva aplicación de usuarios
    path('assistant/', include('assistant.urls')),  # Asistente Conversacional
    path('', views.login_view, name='login'),
    path('login-pin/', views.login_pin_request_view, name='login_pin_request'),
    path('login-2fa/', views.login_2fa_view, name='login_2fa'),
    path('login-2fa/reenviar/', views.login_2fa_resend_view, name='login_2fa_resend'),
    path('login-google/', views.google_login_view, name='login_google'),
    path('logout/', views.logout_view, name='logout'),

    # Landing pública "Mis Puntos" — URL CORTA EN RAÍZ porque es la que va
    # impresa en el QR de cada ticket (app.urls vive bajo el prefijo /app/).
    path('puntos/app/', descargar_app_puntos, name='descargar_app_puntos_raiz'),
    path('api/check-session/', views.check_session_status, name='check_session_status'),
    path('api/check-login-method/', views.check_login_method_view, name='check_login_method'),
    
    # === API v1 para App Desktop (POS Físico) ===
    path('api/v1/', include('app.api.urls', namespace='api_v1')),

    # === API externa — contrato con AllConnected/VicentAllEcommercesConected ===
    path('api/', include('app.api.external.urls')),
]

# Servir archivos media SIEMPRE (desarrollo y producción local), SOLO con
# sesión iniciada. Antes cualquier anónimo descargaba por URL los TXT SII de
# notas de crédito (RUT, montos, líneas), comprobantes bancarios de depósitos,
# PDFs de cotizaciones y facturas de proveedor subidas al agente de carga
# (verificado 26-09-2026). Nada público enlaza a /media/: ni la API externa,
# ni las tiendas, ni los correos, ni la impresión QZ (que no usa flavor
# 'file'). Todos los consumidores son páginas internas con sesión.
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', login_required(serve), {
        'document_root': settings.MEDIA_ROOT,
    }),
]

# Servir archivos static en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
