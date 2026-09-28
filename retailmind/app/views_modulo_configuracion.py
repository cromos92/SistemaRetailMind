"""
Vistas del módulo Configuración — Integraciones con ecommerce externos.

Pantalla HTML para gestionar credenciales (URL + API key + header + empresa)
de cada ecommerce que provee fotos de portada a RetailMind (realsport.cl,
calzadospaola.cl, ...). Reusa el patrón de FBV del proyecto y el design system
NEXO.

Acciones por integración (todas con scope de empresa):
  * probar       — GET /health/ del ecommerce con la API key.
  * sincronizar  — trae el catálogo de portadas y hace upsert (en proceso,
                   sin subprocess: antes se levantaba un Django entero por
                   cada click y la invalidación de cache no llegaba al worker).
  * verificar    — cobertura del catálogo + liveness HTTP de una muestra de
                   URLs, acotada para caber en el timeout de gunicorn.
  * fotos        — galería paginada de las portadas sincronizadas (o de las
                   URLs muertas de la última verificación) para VER que las
                   fotos realmente sirven, no solo contarlas.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.decorators import solo_administrador_o_jefe
from app.models import CredencialesEcommerce, Empresa, FotoPortadaArticulo, Producto
from app.services.realsport_imagenes_service import probar_conexion
from app.utils_permisos import obtener_empresas_usuario
from app.utils_texto import limpiar_html

logger = logging.getLogger('app')

# Una integración de fotos se considera "atrasada" si su última sincronización
# es más vieja que esto (el scheduler la corre a diario; desde la UI a mano).
HORAS_SYNC_ATRASADA = 48

# Valor que manda el formulario al editar cuando NO se quiere cambiar la key.
API_KEY_SIN_CAMBIO = '__sin_cambio__'
CODIGO_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,49}$')

# Miniaturas por fila del listado y tamaño de página de la galería.
MUESTRA_FOTOS_FILA = 4
FOTOS_POR_PAGINA = 24
FOTOS_POR_PAGINA_MAX = 48

# Verificación disparada desde la UI: muestra y timeout acotados para que el
# peor caso (todas las URLs muertas) quepa en el timeout de gunicorn (60 s).
# El barrido completo es el comando ``verificar_fotos_ecommerce``.
VERIF_UI_MUESTRA = 120
VERIF_UI_MUESTRA_MAX = 300
VERIF_UI_TIMEOUT = 5

_RE_SYNC = re.compile(r'procesados=(\d+).*?con_foto=(\d+).*?sin_match=(\d+)')
_RE_VERIF_COBERTURA = re.compile(r'cobertura (\d+)/(\d+)')
_RE_VERIF_URLS = re.compile(
    r'urls (\d+) ok, (\d+) 404, (\d+) no-img, (\d+) otro, (\d+) red'
)


# ───────────────────────── Helpers ─────────────────────────

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


def _resumen_sync(texto):
    """Estructura ``ultima_sync_resultado`` para mostrarlo como chips.

    El sync persiste un string tipo ``paginas=3, procesados=1095,
    con_foto=1087 (...), sin_match=8`` o ``ERROR: ...``. Devuelve ``None`` si
    no hay nada que mostrar.
    """
    texto = (texto or '').strip()
    if not texto:
        return None
    if texto.upper().startswith('ERROR'):
        return {'error': texto.split(':', 1)[-1].strip() or texto}
    m = _RE_SYNC.search(texto)
    if not m:
        return {'texto': texto}
    return {
        'procesados': int(m.group(1)),
        'con_foto': int(m.group(2)),
        'sin_match': int(m.group(3)),
    }


def _resumen_verif(texto):
    """Estructura ``ultima_verif_resultado`` (ver ``construir_resumen``)."""
    texto = (texto or '').strip()
    if not texto:
        return None
    out = {
        'con_foto': None, 'articulos': None,
        'urls_ok': 0, 'urls_malas': 0, 'tiene_urls': False,
        'muestra': '(muestra)' in texto,
    }
    m = _RE_VERIF_COBERTURA.search(texto)
    if m:
        out['con_foto'] = int(m.group(1))
        out['articulos'] = int(m.group(2))
    m = _RE_VERIF_URLS.search(texto)
    if m:
        ok, e404, no_img, otro, red = (int(x) for x in m.groups())
        out['urls_ok'] = ok
        out['urls_malas'] = e404 + no_img + otro + red
        out['tiene_urls'] = True
    return out


def _muertas_de(cred, q=''):
    """Lista de URLs con problema guardada por la última verificación."""
    try:
        lista = json.loads(cred.ultima_verif_detalle or '[]')
    except ValueError:
        lista = []
    q = (q or '').lower()
    items = []
    for m in lista:
        if not isinstance(m, dict):
            continue
        articulo = str(m.get('articulo') or '')
        if q and q not in articulo.lower():
            continue
        items.append({
            'articulo': articulo,
            'url': m.get('url') or '',
            'descripcion': '',
            'sync_at': None,
            'motivo': m.get('motivo') or '',
            'status': m.get('status'),
        })
    return items


def _muestra_fotos(cred):
    """Últimas ``MUESTRA_FOTOS_FILA`` portadas de la integración (para la fila)."""
    return list(
        FotoPortadaArticulo.objects.filter(origen=cred)
        .order_by('-sync_at', 'articulo')
        .values('articulo', 'url_foto')[:MUESTRA_FOTOS_FILA]
    )


def _descripciones(articulos, empresa_id):
    """``{articulo: descripcion}`` priorizando los productos de la empresa dueña."""
    articulos = [a for a in articulos if a]
    if not articulos:
        return {}
    out = {}
    filas = (
        Producto.objects
        .filter(articulo__in=articulos, sucursal__empresa_id=empresa_id)
        .values_list('articulo', 'descripcion')
    )
    for art, desc in filas:
        out.setdefault(art, limpiar_html(desc)[:120])
    faltan = [a for a in articulos if a not in out]
    if faltan:
        filas = (
            Producto.objects.filter(articulo__in=faltan)
            .values_list('articulo', 'descripcion')
        )
        for art, desc in filas:
            out.setdefault(art, limpiar_html(desc)[:120])
    return out


def _entero(valor, default, minimo=None, maximo=None):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        n = default
    if minimo is not None:
        n = max(minimo, n)
    if maximo is not None:
        n = min(maximo, n)
    return n


def _anotar_estado(cred, ahora):
    """Agrega a la credencial los campos calculados que usa el template/JSON."""
    cred.estado_sync, cred.horas_sync = _estado_sync(cred, ahora)
    cred.sync_resumen = _resumen_sync(cred.ultima_sync_resultado)
    cred.verif_resumen = _resumen_verif(cred.ultima_verif_resultado)
    cred.verif_muertas = len(_muertas_de(cred))
    cred.muestra_fotos = _muestra_fotos(cred)
    return cred


# ───────────────────────── Listado ─────────────────────────

@login_required
@solo_administrador_o_jefe
def integraciones_ecommerce(request):
    """Listado + alta/edición de credenciales de ecommerce externos.

    Scoped a las empresas asignadas al usuario (admins ven todas). Incluye el
    estado de sincronización de cada integración (KPIs + semáforo por fila) y
    una muestra de miniaturas para ver de un vistazo si las fotos cargan.
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

    ahora = timezone.now()
    for cred in credenciales:
        _anotar_estado(cred, ahora)

    total = len(credenciales)
    activas = sum(1 for c in credenciales if c.activo)
    total_fotos = sum(c.total_fotos for c in credenciales)
    con_problema = sum(
        1 for c in credenciales if c.estado_sync in ('nunca', 'error', 'atrasada')
    )

    # Datos para el modal de edición (json_script en el template, sin api_key).
    credenciales_js = [
        {
            'id': c.id, 'nombre': c.nombre, 'codigo': c.codigo, 'tipo': c.tipo,
            'empresa_id': c.empresa_id, 'url_api': c.url_api,
            'header_name': c.header_name, 'activo': c.activo,
            'prioridad': c.prioridad,
        }
        for c in credenciales
    ]

    context = {
        'credenciales': credenciales,
        'credenciales_js': credenciales_js,
        'empresas': empresas,
        'tipos': CredencialesEcommerce.TIPO_CHOICES,
        'kpi_total': total,
        'kpi_activas': activas,
        'kpi_total_fotos': total_fotos,
        'kpi_con_problema': con_problema,
        'horas_sync_atrasada': HORAS_SYNC_ATRASADA,
        'limite_sync': ahora - timedelta(hours=HORAS_SYNC_ATRASADA),
        'verif_ui_muestra': VERIF_UI_MUESTRA,
        'api_key_sin_cambio': API_KEY_SIN_CAMBIO,
    }
    return render(
        request,
        'vistas/modulo_configuracion/integraciones_ecommerce.html',
        context,
    )


# ───────────────────────── Alta / edición / baja ─────────────────────────

@login_required
@solo_administrador_o_jefe
@require_http_methods(['POST'])
def guardar_integracion_ecommerce(request):
    """Crea o actualiza una CredencialesEcommerce (modal con form POST).

    Al editar, una API key vacía (o el centinela ``__sin_cambio__``) conserva
    la actual. Un ``codigo`` repetido responde con mensaje, no con 500.
    """
    pk = request.POST.get('id') or None
    codigo = (request.POST.get('codigo') or '').strip().lower()
    nombre = (request.POST.get('nombre') or '').strip()[:100]
    tipo = (request.POST.get('tipo') or '').strip()
    empresa_id = request.POST.get('empresa_id') or None
    # Normalizar URL: sin trailing slash para no terminar con //api/v1/...
    url_api = (request.POST.get('url_api') or '').strip().rstrip('/')
    api_key = (request.POST.get('api_key') or '').strip()
    header_name = (request.POST.get('header_name') or '').strip()[:50] or 'X-AllConnected-Key'
    activo = request.POST.get('activo') == 'on'
    prioridad = _entero(request.POST.get('prioridad'), 0)

    if not codigo or not nombre or not tipo or not empresa_id or not url_api:
        messages.error(request, 'Faltan campos obligatorios.')
        return redirect('integraciones_ecommerce')
    if not CODIGO_RE.match(codigo):
        messages.error(
            request,
            'El código solo admite minúsculas, números, guion y guion bajo '
            '(ej: realsport, paola).',
        )
        return redirect('integraciones_ecommerce')
    if tipo not in dict(CredencialesEcommerce.TIPO_CHOICES):
        messages.error(request, 'Tipo de ecommerce no válido.')
        return redirect('integraciones_ecommerce')
    if not url_api.lower().startswith(('http://', 'https://')):
        messages.error(request, 'La URL base debe empezar con http:// o https://.')
        return redirect('integraciones_ecommerce')

    empresa = get_object_or_404(Empresa, pk=empresa_id)

    # Scope: no se puede crear/mover una integración hacia una empresa ajena.
    empresa_ids = _empresa_ids_usuario(request.user)
    if empresa.id not in empresa_ids:
        messages.error(request, 'Sin acceso a la empresa seleccionada.')
        return redirect('integraciones_ecommerce')

    cambia_key = bool(api_key) and api_key != API_KEY_SIN_CAMBIO

    try:
        with transaction.atomic():
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
                if cambia_key:
                    cred.api_key = api_key
                cred.header_name = header_name
                cred.activo = activo
                cred.prioridad = prioridad
                cred.save()
                messages.success(request, f'Integración "{nombre}" actualizada.')
            else:
                if not cambia_key:
                    messages.error(request, 'Falta la API key de la integración.')
                    return redirect('integraciones_ecommerce')
                CredencialesEcommerce.objects.create(
                    codigo=codigo, nombre=nombre, tipo=tipo, empresa=empresa,
                    url_api=url_api, api_key=api_key, header_name=header_name,
                    activo=activo, prioridad=prioridad,
                )
                messages.success(request, f'Integración "{nombre}" creada.')
    except IntegrityError:
        messages.error(
            request, f'Ya existe una integración con el código "{codigo}".',
        )

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
    fotos = FotoPortadaArticulo.objects.filter(origen=cred).count()
    # CASCADE borra también las FotoPortadaArticulo asociadas.
    cred.delete()
    messages.success(
        request, f'Integración "{nombre}" eliminada ({fotos} foto(s) de portada borradas).',
    )
    return redirect('integraciones_ecommerce')


# ───────────────────────── Acciones JSON ─────────────────────────

@login_required
@solo_administrador_o_jefe
@require_http_methods(['GET'])
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
    """Corre ``sincronizar_fotos_ecommerce --codigo X`` en el mismo proceso.

    Antes se lanzaba como ``subprocess`` (un Django entero por click, ~15 s de
    arranque y el doble de RAM en un contenedor de 2 workers) y, como corría
    en otro proceso, ``_invalidar_cache`` no alcanzaba al worker web. En
    proceso sigue tardando lo mismo que el sync (15-25 s por tienda), dentro
    del timeout de gunicorn. Devuelve JSON con el log y los datos para
    refrescar la fila sin recargar.
    """
    cred, deny = _credencial_con_scope(request, pk)
    if deny:
        return deny

    if not cred.activo:
        return JsonResponse({
            'ok': False, 'stdout': '',
            'stderr': 'La integración está inactiva: activala desde "Editar" antes de sincronizar.',
            'ultima_sync_at': cred.ultima_sync_at.isoformat() if cred.ultima_sync_at else None,
            'ultima_sync_resultado': cred.ultima_sync_resultado,
        })

    inicio = timezone.now()
    salida = StringIO()
    stderr = ''
    try:
        call_command(
            'sincronizar_fotos_ecommerce', codigo=cred.codigo,
            stdout=salida, stderr=salida,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception('Error corriendo sync de %s', cred.codigo)
        stderr = str(exc)[:500]

    # Releer credencial para devolver ultima_sync_at/resultado actualizados.
    cred.refresh_from_db()
    resultado = cred.ultima_sync_resultado or ''
    ok = (
        not stderr
        and cred.ultima_sync_at is not None
        and cred.ultima_sync_at >= inicio
        and not resultado.upper().startswith('ERROR')
    )
    ahora = timezone.now()
    _anotar_estado(cred, ahora)
    total_fotos = FotoPortadaArticulo.objects.filter(origen=cred).count()
    kpi_total_fotos = FotoPortadaArticulo.objects.filter(
        origen__empresa_id__in=_empresa_ids_usuario(request.user),
    ).count()

    return JsonResponse({
        'ok': ok,
        'stdout': salida.getvalue()[-4000:],
        'stderr': stderr,
        'ultima_sync_at': cred.ultima_sync_at.isoformat() if cred.ultima_sync_at else None,
        'ultima_sync_resultado': resultado,
        'resumen': cred.sync_resumen,
        'estado_sync': cred.estado_sync,
        'horas_sync': cred.horas_sync,
        'total_fotos': total_fotos,
        'kpi_total_fotos': kpi_total_fotos,
        'muestra': [
            {'articulo': f['articulo'], 'url': f['url_foto']} for f in cred.muestra_fotos
        ],
    })


@login_required
@solo_administrador_o_jefe
@require_http_methods(['POST'])
def verificar_integracion_ecommerce(request, pk):
    """Verifica que las portadas de la integración "realmente se pasaron".

    Cobertura del catálogo (por sucursal) + liveness HTTP de una MUESTRA de URLs
    (acotada en cantidad y timeout para caber en el request). El barrido
    completo es el management command ``verificar_fotos_ecommerce``.
    """
    from app.services.verificacion_fotos_service import (
        VerificacionFotosError, persistir_resultado, verificar_credencial,
    )

    cred, deny = _credencial_con_scope(request, pk)
    if deny:
        return deny

    solo_cobertura = request.POST.get('solo_cobertura') == '1'
    muestra = _entero(
        request.POST.get('muestra'), VERIF_UI_MUESTRA,
        minimo=10, maximo=VERIF_UI_MUESTRA_MAX,
    )

    try:
        resultado = verificar_credencial(
            cred, muestra=muestra, solo_cobertura=solo_cobertura,
            timeout=VERIF_UI_TIMEOUT,
        )
        persistir_resultado(cred, resultado)
    except VerificacionFotosError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)[:300]}, status=200)
    except Exception as exc:  # noqa: BLE001
        logger.exception('Error verificando fotos de %s', cred.codigo)
        return JsonResponse({'ok': False, 'error': str(exc)[:300]}, status=200)

    cred.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'resultado': resultado,
        'ultima_verif_at': cred.ultima_verif_at.isoformat() if cred.ultima_verif_at else None,
        'ultima_verif_resultado': cred.ultima_verif_resultado,
        'verif_resumen': _resumen_verif(cred.ultima_verif_resultado),
        'verif_muertas': len(_muertas_de(cred)),
    })


@login_required
@solo_administrador_o_jefe
@require_http_methods(['GET'])
def fotos_integracion_ecommerce(request, pk):
    """Galería paginada de las portadas sincronizadas de una integración.

    ``?q=`` filtra por articulo, ``?page=`` pagina, ``?solo=muertas`` lista en
    cambio las URLs con problema que dejó la última verificación. Cada item
    trae la descripción del producto (de la empresa dueña si existe) para que
    la galería sirva para reconocer el artículo, no solo el código.
    """
    cred, deny = _credencial_con_scope(request, pk)
    if deny:
        return deny

    q = (request.GET.get('q') or '').strip()[:100]
    page = _entero(request.GET.get('page'), 1, minimo=1)
    page_size = _entero(
        request.GET.get('page_size'), FOTOS_POR_PAGINA,
        minimo=1, maximo=FOTOS_POR_PAGINA_MAX,
    )
    solo = 'muertas' if request.GET.get('solo') == 'muertas' else ''

    if solo == 'muertas':
        paginator = Paginator(_muertas_de(cred, q), page_size)
        pagina = paginator.get_page(page)
        items = list(pagina.object_list)
    else:
        qs = FotoPortadaArticulo.objects.filter(origen=cred)
        if q:
            qs = qs.filter(articulo__icontains=q)
        qs = qs.order_by('-sync_at', 'articulo').values('articulo', 'url_foto', 'sync_at')
        paginator = Paginator(qs, page_size)
        pagina = paginator.get_page(page)
        filas = list(pagina.object_list)
        descripciones = _descripciones([f['articulo'] for f in filas], cred.empresa_id)
        items = [
            {
                'articulo': f['articulo'],
                'url': f['url_foto'],
                'descripcion': descripciones.get(f['articulo'], ''),
                'sync_at': f['sync_at'].isoformat() if f['sync_at'] else None,
                'motivo': '',
                'status': None,
            }
            for f in filas
        ]

    return JsonResponse({
        'ok': True,
        'items': items,
        'page': pagina.number,
        'pages': paginator.num_pages,
        'total': paginator.count,
        'q': q,
        'solo': solo,
    })
