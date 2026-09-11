"""
Alerta diaria de correlativos "en rojo" (agotados o por agotarse).

Antes de esto, la única forma de enterarse de que un correlativo se estaba por
agotar era abrir manualmente /app/documentos/gestion-correlativos/ y mirar la
tabla — si nadie entraba, la primera señal era que la emisión de un documento
fallaba en caja/venta. Este servicio junta en un solo correo diario todos los
correlativos con `nivel_alerta` en ('agotado', 'critico') (ver
`Correlativo.en_rojo` / `Correlativo.UMBRAL_CRITICO` en
app/models/organizacion.py) y lo manda a `settings.CORRELATIVOS_ALERTA_EMAILS`.

Un solo correo por corrida (no uno por correlativo): si cinco sucursales caen
en rojo el mismo día, es una alerta agrupada, no cinco correos separados.

Llamado desde:
  - management command `alertar_correlativos_rojo` (para probar a mano / cron externo)
  - app/management/commands/run_scheduler.py, una vez al día (bloque `incluir_diario`)
"""
import logging

from django.conf import settings
from django.template.loader import render_to_string
from django.db.models import F

from app.models import Correlativo
from app.services.correo_service import enviar_correo_trazado, CorreoError

logger = logging.getLogger('app')


def _destinatarios():
    crudo = getattr(settings, 'CORRELATIVOS_ALERTA_EMAILS', '') or ''
    return [e.strip() for e in crudo.split(',') if e.strip()]


def _url_gestion():
    base = (getattr(settings, 'CORREO_BASE_URL', '') or '').strip().rstrip('/')
    if not base:
        return ''
    return f'{base}/app/documentos/gestion-correlativos/'


def obtener_correlativos_en_rojo():
    """Correlativos con disponibles < Correlativo.UMBRAL_CRITICO, más críticos primero.

    Usa `annotate` en vez de filtrar por la property `nivel_alerta` en Python
    porque las properties no son queryable en el ORM — pero el umbral es el
    mismo (`Correlativo.UMBRAL_CRITICO`), no un valor duplicado a mano.
    """
    return list(
        Correlativo.objects
        .select_related('sucursal')
        .annotate(disponibles_anotado=F('termino') - F('inicio') + 1)
        .filter(disponibles_anotado__lt=Correlativo.UMBRAL_CRITICO)
        .order_by('disponibles_anotado')
    )


def alertar_correlativos_en_rojo(enviar=False):
    """Arma y —si `enviar=True`— manda el correo diario de correlativos en rojo.

    Devuelve un dict con lo que encontró/hizo, pensado tanto para el comando de
    management (que lo imprime) como para el scheduler (que lo loguea).
    Si no hay ningún correlativo en rojo, no manda nada (dict con enviado=False,
    motivo='sin_correlativos_en_rojo') — no tiene sentido un correo diario
    diciendo "todo bien" cuando nadie lo pidió así.
    """
    correlativos = obtener_correlativos_en_rojo()
    resultado = {
        'total_en_rojo': len(correlativos),
        'correlativos': [
            {
                'sucursal_alias': c.sucursal.alias,
                'tipo_dte': c.get_tipo_dte_display() if hasattr(c, 'get_tipo_dte_display') else c.tipo_dte,
                'disponibles': c.disponibles,
                'nivel_alerta': c.nivel_alerta,
            }
            for c in correlativos
        ],
        'enviado': False,
        'motivo': None,
    }

    if not correlativos:
        resultado['motivo'] = 'sin_correlativos_en_rojo'
        return resultado

    destinatarios = _destinatarios()
    if not destinatarios:
        resultado['motivo'] = 'sin_destinatarios_configurados'
        logger.warning(
            'alertar_correlativos_en_rojo: %s correlativo(s) en rojo pero '
            'CORRELATIVOS_ALERTA_EMAILS está vacío — no se manda nada.',
            len(correlativos),
        )
        return resultado

    if not enviar:
        resultado['motivo'] = 'dry_run'
        return resultado

    contexto = {
        'correlativos': resultado['correlativos'],
        'umbral_critico': Correlativo.UMBRAL_CRITICO,
        'url_gestion': _url_gestion(),
    }
    html = render_to_string('emails/correlativos_en_rojo.html', contexto)
    texto = 'Correlativos en rojo:\n' + '\n'.join(
        f"- {c['sucursal_alias']} / {c['tipo_dte']}: {c['disponibles']} disponibles "
        f"({'AGOTADO' if c['nivel_alerta'] == 'agotado' else 'CRÍTICO'})"
        for c in resultado['correlativos']
    )
    asunto = f"⚠ {len(correlativos)} correlativo(s) en rojo — revisar folios"

    errores = []
    for destinatario in destinatarios:
        try:
            enviar_correo_trazado(
                modulo='OTRO',  # no hay un choice de MODULO_CORREO_CHOICES
                # para esto; se prefirió no tocar ese modelo por un solo campo
                # de categorización en vez de agregar 'CORRELATIVOS' al choices.
                asunto=asunto,
                texto=texto,
                html=html,
                destinatario=destinatario,
                con_pixel=False,  # alerta interna, no hace falta rastrear apertura
                con_token_respuesta=False,  # no es un hilo que se espere responder
                tags=['correlativos', 'alerta-rojo'],
            )
        except CorreoError as e:
            errores.append({'destinatario': destinatario, 'error': str(e)})
            logger.exception(
                'alertar_correlativos_en_rojo: falló el envío a %s', destinatario,
            )

    resultado['enviado'] = len(errores) < len(destinatarios)
    resultado['motivo'] = 'enviado' if not errores else 'enviado_con_errores'
    resultado['errores'] = errores
    return resultado
