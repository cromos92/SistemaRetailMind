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

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.decorators import solo_administrador_o_jefe
from app.models import CredencialesEcommerce, Empresa
from app.services.realsport_imagenes_service import probar_conexion
from app.utils_permisos import obtener_empresas_usuario

logger = logging.getLogger('app')

# Una integración de fotos se considera "atrasada" si su última sincronización
# es más vieja que esto (el sync se corre a mano o por scheduler diario).
HORAS_SYNC_ATRASADA = 48


def _empresa_ids_usuario(user):
    """IDs de las empresas visibles para el usuario (admins: todas las activas)."""
    return set(obtener_empresas_usuario(user).values_list('id', flat=True))


def _credencial_con_scope(request, pk):
    """Obtiene la credencial validando que sea de una empresa del usuario.

    Devuelve ``(credencial, None)`` o ``(None, JsonResponse 403)``. Antes estas
    vistas usaban ``get_object_or_404`` sin validar la empresa: un jefe de la
    empresa A podía probar, sincronizar, editar o borrar las credenciales
    (y las fotos) de la empresa B pasando el ``pk`` por URL.
    """
    cred = get_object_or_404(CredencialesEcommerce, pk=pk)
    if cred.empresa_id not in _empresa_ids_usuario(request.user):
        logger.warning(
            'Usuario %s intentó operar la integración %s de otra empresa (%s).',
            request.user, cred.codigo, cred.empresa_id,
        )
        return None, JsonResponse(
            {'ok': False, 'error': 'Sin acceso a esta empresa.'}, status=403,
        )
    return cred, None


def _estado_sync(cred, ahora):
    """Clasifica el estado de la última sincronización de una credencial.

    Devuelve ``(estado, horas)`` con estado en:
      * ``inactiva``  — la integración está apagada.
      * ``nunca``     — jamás se sincronizó.
      * ``error``     — el último resultado registrado menciona un error.
      * ``atrasada``  — hace más de ``HORAS_SYNC_ATRASADA`` horas.
      * ``ok``        — sincronizada recientemente y sin error.
    """
    if not cred.activo:
        return 'inactiva', None
    if not cred.ultima_sync_at:
        return 'nunca', None
    horas = round((ahora - cred.ultima_sync_at).total_seconds() / 3600.0, 1)
    resultado = (cred.ultima_sync_resultado or '').lower()
    if 'error' in resultado or 'fall' in resultado or 'timeout' in resultado:
        return 'error', horas
    if horas > HORAS_SYNC_ATRASADA:
        return 'atrasada', horas
    return 'ok', horas


@login_required
@solo_administrador_o_jefe
def integraciones_ecommerce(request):
    """Listado + alta/edición de credenciales de ecommerce externos.

    Scoped a las empresas asignadas al usuario (admins ven todas). Incluye el
    estado de sincronización de cada integración (KPIs + semáforo por fila).
    """
    empresas = obtener_empresas_usuario(request.user)
    empresa_ids = list(empresas.values_list('id', flat=True))

    credenciales = list(
        CredencialesEcommerce.objects
        .filter(empresa_id__in=empresa_ids)
        .select_related('empresa')
        .annotate(total_fotos=Count('fotos'))
        .order_by('-prioridad', 'nombre')
    )

    # KPIs calculados en la vista: en el template estaban con `dictsortreversed`
    # y un `add` dentro de un `for`, que imprimían el objeto y las sumas
    # parciales concatenadas en vez de un número.
    ahora = timezone.now()
    for cred in credenciales:
        cred.estado_sync, cred.horas_sync = _estado_sync(cred, ahora)

    total = len(credenciales)
    activas = sum(1 for c in credenciales if c.activo)
    total_fotos = sum(c.total_fotos for c in credenciales)
    con_problema = sum(
        1 for c in credenciales if c.estado_sync in ('nunca', 'error', 'atrasada')
    )

    context = {
        'credenciales': credenciales,
        'empresas': empresas,
        'tipos': CredencialesEcommerce.TIPO_CHOICES,
        'kpi_total': total,
        'kpi_activas': activas,
        'kpi_total_fotos': total_fotos,
        'kpi_con_problema': con_problema,
        'horas_sync_atrasada': HORAS_SYNC_ATRASADA,
        'limite_sync': ahora - timedelta(hours=HORAS_SYNC_ATRASADA),
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

    # Scope: no se puede crear/mover una integración hacia una empresa ajena.
    empresa_ids = _empresa_ids_usuario(request.user)
    if empresa.id not in empresa_ids:
        messages.error(request, 'Sin acceso a la empresa seleccionada.')
        return redirect('integraciones_ecommerce')

    if pk:
        cred = get_object_or_404(CredencialesEcommerce, pk=pk)
        if cred.empresa_id not in empresa_ids:
            messages.error(request, 'Sin acceso a esta integración.')
            return redirect('integraciones_ecommerce')
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
    if cred.empresa_id not in _empresa_ids_usuario(request.user):
        messages.error(request, 'Sin acceso a esta integración.')
        return redirect('integraciones_ecommerce')
    nombre = cred.nombre
    # CASCADE borra también las FotoPortadaArticulo asociadas.
    cred.delete()
    messages.success(request, f'Integración "{nombre}" eliminada.')
    return redirect('integraciones_ecommerce')


@login_required
@solo_administrador_o_jefe
def probar_integracion_ecommerce(request, pk):
    """Pega un /health/ contra el ecommerce y devuelve JSON para el botón."""
    cred, deny = _credencial_con_scope(request, pk)
    if deny:
        return deny
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
    cred, deny = _credencial_con_scope(request, pk)
    if deny:
        return deny

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


@login_required
@solo_administrador_o_jefe
@require_http_methods(['POST'])
def verificar_integracion_ecommerce(request, pk):
    """Verifica que las portadas de la integración "realmente se pasaron".

    Cobertura del catálogo (por sucursal) + liveness HTTP de una MUESTRA de URLs
    (rápido, para no exceder el tiempo del request). El barrido completo es el
    management command ``verificar_fotos_ecommerce``. Devuelve JSON para la UI.
    """
    from app.services.verificacion_fotos_service import (
        VerificacionFotosError, persistir_resultado, verificar_credencial,
    )

    # Scope: la credencial debe ser de una empresa asignada al usuario.
    cred, deny = _credencial_con_scope(request, pk)
    if deny:
        return deny

    solo_cobertura = request.POST.get('solo_cobertura') == '1'
    try:
        muestra = int(request.POST.get('muestra') or 300)
    except (TypeError, ValueError):
        muestra = 300

    try:
        resultado = verificar_credencial(
            cred, muestra=muestra, solo_cobertura=solo_cobertura,
        )
        persistir_resultado(cred, resultado)
        ok = True
    except VerificacionFotosError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)[:300]}, status=200)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Error verificando fotos de %s', cred.codigo)
        return JsonResponse({'ok': False, 'error': str(exc)[:300]}, status=200)

    cred.refresh_from_db()
    return JsonResponse({
        'ok': ok,
        'resultado': resultado,
        'ultima_verif_at': cred.ultima_verif_at.isoformat() if cred.ultima_verif_at else None,
        'ultima_verif_resultado': cred.ultima_verif_resultado,
    })
