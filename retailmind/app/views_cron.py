"""
Disparador HTTP de tareas periódicas (expiración de reservas, vales y puntos).

Pensado para entornos sin cron nativo (Digital Ocean App Platform): un programador
externo —DigitalOcean Functions con trigger agendado, un monitor de uptime, o
GitHub Actions— hace POST a este endpoint cada pocos minutos. Es una ALTERNATIVA
al worker `run_scheduler` (que corre el mismo trabajo en un proceso de fondo).

Seguridad:
- Requiere la cabecera `X-Cron-Key` igual a la env var `CRON_TRIGGER_KEY`.
- Comparación en tiempo constante (`hmac.compare_digest`) → sin oráculo de timing.
- Si la clave no está configurada o no coincide → 404 (no revela la existencia
  del endpoint a un escáner).
- Solo POST. CSRF-exempt (no es un form de navegador; se autentica por clave).

Las operaciones que dispara son idempotentes: repetirlas no causa daño.
"""
import hmac
import logging
import os

from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from app.services import fidelizacion_service

logger = logging.getLogger('app')


def _autorizado(request):
    clave_esperada = (os.environ.get('CRON_TRIGGER_KEY', '') or '').strip()
    if not clave_esperada:
        return False  # sin clave configurada → endpoint deshabilitado
    enviada = request.headers.get('X-Cron-Key', '') or ''
    return hmac.compare_digest(enviada, clave_esperada)


@csrf_exempt
@require_POST
def ejecutar_tareas_periodicas(request):
    """
    POST /app/api/cron/tareas/?incluir_puntos=1
    Header: X-Cron-Key: <CRON_TRIGGER_KEY>

    Expira reservas y vales vencidos siempre. Expira los lotes de puntos vencidos
    solo si `incluir_puntos` está activo (esa tarea es diaria, no cada minuto).
    """
    if not _autorizado(request):
        raise Http404()

    reservas = fidelizacion_service.expirar_reservas_vencidas()
    vales = fidelizacion_service.expirar_vales_vencidos()
    lotes = 0
    if request.GET.get('incluir_puntos') in ('1', 'true', 'True'):
        lotes = fidelizacion_service.expirar_lotes_vencidos()

    logger.info('Cron HTTP ejecutado: reservas=%s vales=%s lotes_puntos=%s',
                reservas, vales, lotes)
    return JsonResponse({
        'ok': True,
        'reservas_expiradas': reservas,
        'vales_expirados': vales,
        'lotes_puntos_expirados': lotes,
    })
