"""
URLs principales para API v1 - App Desktop
==========================================

Incluye:
- /api/v1/desktop/ - Autenticación y configuración
- /api/v1/sync/ - Sincronización bidireccional
- /api/v1/health/ - Health check
"""

from django.urls import path, include
from app.api.desktop.views import HealthCheckView, SyncStatusView

app_name = 'api_v1'

urlpatterns = [
    # Health check (sin autenticación)
    path('health/', HealthCheckView.as_view(), name='health'),
    
    # Estado de sync (sin autenticación completa)
    path('sync/status/', SyncStatusView.as_view(), name='sync-status'),
    
    # Desktop auth y config
    path('desktop/', include('app.api.desktop.urls', namespace='desktop')),
    
    # Sincronización
    path('sync/', include('app.api.sync.urls', namespace='sync')),
]
