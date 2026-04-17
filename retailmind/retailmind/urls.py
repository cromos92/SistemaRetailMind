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
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('app/', include('app.urls')),
    path('empresa_management/', include('empresa_management.urls')),
    path('users/', include('users.urls')),  # Nueva aplicación de usuarios
    path('assistant/', include('assistant.urls')),  # Asistente Conversacional
    path('', views.login_view, name='login'),
    path('login-pin/', views.login_pin_request_view, name='login_pin_request'),
    path('login-2fa/', views.login_2fa_view, name='login_2fa'),
    path('logout/', views.logout_view, name='logout'),
    path('api/check-session/', views.check_session_status, name='check_session_status'),
    path('api/check-login-method/', views.check_login_method_view, name='check_login_method'),
    
    # === API v1 para App Desktop (POS Físico) ===
    path('api/v1/', include('app.api.urls', namespace='api_v1')),

    # === API externa — contrato con AllConnected/VicentAllEcommercesConected ===
    path('api/', include('app.api.external.urls')),
]

# Servir archivos media SIEMPRE (desarrollo y producción local)
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {
        'document_root': settings.MEDIA_ROOT,
    }),
]

# Servir archivos static en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
