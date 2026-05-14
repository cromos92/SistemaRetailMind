"""
Vistas del módulo Configuración — Integraciones con ecommerce externos.

Pantalla HTML para gestionar credenciales (URL + API key + header + empresa)
de cada ecommerce que provee fotos de portada a RetailMind (realsport.cl,
paola.cl, ...). Reusa el patrón de FBV del proyecto y el design system NEXO.
"""
from __future__ import annotations

import logging
import subprocess
import sys

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from app.decorators import solo_administrador_o_jefe
from app.models import CredencialesEcommerce, Empresa
from app.services.realsport_imagenes_service import probar_conexion

logger = logging.getLogger('app')


@login_required
@solo_administrador_o_jefe
def integraciones_ecommerce(request):
    """Listado + alta/edición de credenciales de ecommerce externos."""
    credenciales = (
        CredencialesEcommerce.objects
        .select_related('empresa')
        .annotate(total_fotos=Count('fotos'))
        .order_by('-prioridad', 'nombre')
    )

    empresas = Empresa.objects.filter(activo=True).order_by('nombre')

    context = {
        'credenciales': credenciales,
        'empresas': empresas,
        'tipos': CredencialesEcommerce.TIPO_CHOICES,
    }
    return render(
        request,
        'vistas/modulo_configuracion/integraciones_ecommerce.html',
        context,
    )


@login_required
@solo_administrador_o_jefe
@require_http_methods(['POST'])
def guardar_integracion_ecommerce(request):
    """Crea o actualiza una CredencialesEcommerce (modal con form POST)."""
    pk = request.POST.get('id') or None
    codigo = (request.POST.get('codigo') or '').strip().lower()
    nombre = (request.POST.get('nombre') or '').strip()
    tipo = (request.POST.get('tipo') or '').strip()
    empresa_id = request.POST.get('empresa_id') or None
    # Normalizar URL: sin trailing slash para no terminar con //api/v1/...
    url_api = (request.POST.get('url_api') or '').strip().rstrip('/')
    api_key = (request.POST.get('api_key') or '').strip()
    header_name = (request.POST.get('header_name') or 'X-AllConnected-Key').strip()
    activo = request.POST.get('activo') == 'on'
    try:
        prioridad = int(request.POST.get('prioridad') or 0)
    except (TypeError, ValueError):
        prioridad = 0

    if not codigo or not nombre or not tipo or not empresa_id or not url_api or not api_key:
        messages.error(request, 'Faltan campos obligatorios.')
        return redirect('integraciones_ecommerce')

    empresa = get_object_or_404(Empresa, pk=empresa_id)

    if pk:
        cred = get_object_or_404(CredencialesEcommerce, pk=pk)
        cred.codigo = codigo
        cred.nombre = nombre
        cred.tipo = tipo
        cred.empresa = empresa
        cred.url_api = url_api
        # Sólo reemplazar la api_key si el usuario escribió una nueva.
        if api_key and api_key != '__sin_cambio__':
            cred.api_key = api_key
        cred.header_name = header_name
        cred.activo = activo
        cred.prioridad = prioridad
        cred.save()
        messages.success(request, f'Integración "{nombre}" actualizada.')
    else:
        CredencialesEcommerce.objects.create(
            codigo=codigo, nombre=nombre, tipo=tipo, empresa=empresa,
            url_api=url_api, api_key=api_key, header_name=header_name,
            activo=activo, prioridad=prioridad,
        )
        messages.success(request, f'Integración "{nombre}" creada.')

    return redirect('integraciones_ecommerce')


@login_required
@solo_administrador_o_jefe
@require_http_methods(['POST'])
def eliminar_integracion_ecommerce(request, pk):
    cred = get_object_or_404(CredencialesEcommerce, pk=pk)
    nombre = cred.nombre
    # CASCADE borra también las FotoPortadaArticulo asociadas.
    cred.delete()
    messages.success(request, f'Integración "{nombre}" eliminada.')
    return redirect('integraciones_ecommerce')


@login_required
@solo_administrador_o_jefe
def probar_integracion_ecommerce(request, pk):
    """Pega un /health/ contra el ecommerce y devuelve JSON para el botón."""
    cred = get_object_or_404(CredencialesEcommerce, pk=pk)
    resultado = probar_conexion(cred)
    return JsonResponse({
        'ok': resultado['ok'],
        'status': resultado['status'],
        'detalle': resultado['detalle'],
        'url': cred.url_api,
    })


@login_required
@solo_administrador_o_jefe
@require_http_methods(['POST'])
def sincronizar_integracion_ecommerce(request, pk):
    """Dispara el management command sincronizar_fotos_ecommerce --codigo X.

    Se ejecuta como subprocess para no bloquear el request mucho tiempo.
    Devuelve JSON con stdout/stderr para mostrar en la UI.
    """
    cred = get_object_or_404(CredencialesEcommerce, pk=pk)

    try:
        completed = subprocess.run(
            [sys.executable, 'manage.py', 'sincronizar_fotos_ecommerce',
             '--codigo', cred.codigo],
            capture_output=True, text=True, timeout=300,
        )
        stdout = (completed.stdout or '')[-2000:]
        stderr = (completed.stderr or '')[-1000:]
        ok = completed.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
        stdout = ''
        stderr = 'Timeout — el sync tomó más de 5 minutos. Correr el comando desde shell.'
    except Exception as exc:
        logger.exception('Error disparando sync de %s', cred.codigo)
        ok = False
        stdout = ''
        stderr = str(exc)[:500]

    # Releer credencial para devolver ultima_sync_at/resultado actualizados.
    cred.refresh_from_db()

    return JsonResponse({
        'ok': ok,
        'stdout': stdout,
        'stderr': stderr,
        'ultima_sync_at': cred.ultima_sync_at.isoformat() if cred.ultima_sync_at else None,
        'ultima_sync_resultado': cred.ultima_sync_resultado,
    })
