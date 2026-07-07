"""
Notificaciones push a la app "Mis Puntos" vía FCM (HTTP v1).

Configuración (por entorno):
- FCM_SERVICE_ACCOUNT_FILE: ruta al JSON del service account del proyecto
  Firebase (Project settings → Service accounts → Generate new private key).
- Requiere `google-auth` instalado (import perezoso: si falta, las funciones
  hacen no-op con warning y la operación del negocio NO se ve afectada).

Uso típico:
    from app.services import push_service
    push_service.notificar_cliente(
        cliente,
        titulo="Tus puntos están por vencer",
        cuerpo="Tienes $5.000 que vencen el 15 de julio.",
        data={"ruta": "/movimientos"},
    )
"""
import json
import logging
import os

logger = logging.getLogger('app')

_SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'

# Errores FCM que indican token muerto → desactivar dispositivo.
# OJO: INVALID_ARGUMENT queda FUERA a propósito — también lo devuelve un
# payload malformado, y desactivaría todos los dispositivos por un bug propio.
_ERRORES_TOKEN_MUERTO = ('UNREGISTERED', 'NOT_FOUND')

# Cache de credenciales a nivel de módulo: el JSON se lee una vez y el token
# OAuth se refresca solo cuando expira (no un round-trip a Google por envío).
_cache_creds = None
_cache_project = None


def _credenciales():
    """Devuelve (credentials, project_id) o (None, None) si no hay config."""
    global _cache_creds, _cache_project
    if _cache_creds is not None:
        return _cache_creds, _cache_project

    ruta = os.environ.get('FCM_SERVICE_ACCOUNT_FILE', '')
    if not ruta or not os.path.exists(ruta):
        return None, None
    try:
        from google.oauth2 import service_account  # import perezoso
    except ImportError:
        logger.warning(
            "push_service: falta el paquete google-auth "
            "(pip install google-auth) — push deshabilitado."
        )
        return None, None
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            info = json.load(f)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[_SCOPE],
        )
        _cache_creds, _cache_project = creds, info.get('project_id')
        return _cache_creds, _cache_project
    except Exception:
        logger.exception("push_service: service account inválido en %s", ruta)
        return None, None


def esta_configurado():
    """True si el push puede enviarse (credencial presente y legible)."""
    creds, project_id = _credenciales()
    return bool(creds and project_id)


def enviar_a_token(token, titulo, cuerpo, data=None):
    """
    Envía una notificación a UN token FCM.

    Devuelve: 'ok' | 'token_muerto' | 'error' | 'no_configurado'.
    """
    creds, project_id = _credenciales()
    if not creds:
        return 'no_configurado'

    try:
        import requests  # import perezoso
        from google.auth.transport.requests import Request as GoogleRequest

        if not creds.valid:
            creds.refresh(GoogleRequest())
        payload = {
            'message': {
                'token': token,
                'notification': {'title': titulo, 'body': cuerpo},
                # data: valores string (requisito FCM)
                'data': {k: str(v) for k, v in (data or {}).items()},
                'android': {'priority': 'HIGH'},
                'apns': {'headers': {'apns-priority': '10'}},
            }
        }
        resp = requests.post(
            f'https://fcm.googleapis.com/v1/projects/{project_id}/messages:send',
            headers={
                'Authorization': f'Bearer {creds.token}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return 'ok'
        cuerpo_err = resp.text or ''
        if any(e in cuerpo_err for e in _ERRORES_TOKEN_MUERTO):
            return 'token_muerto'
        logger.warning(
            "push_service: FCM respondió %s: %s",
            resp.status_code, cuerpo_err[:300],
        )
        return 'error'
    except Exception:
        logger.exception("push_service: error enviando push")
        return 'error'


def notificar_cliente(cliente, titulo, cuerpo, data=None):
    """
    Envía la notificación a TODOS los dispositivos activos del cliente.
    Desactiva automáticamente los tokens muertos. Devuelve cuántos envíos OK.
    """
    from app.models import DispositivoCliente

    enviados = 0
    dispositivos = DispositivoCliente.objects.filter(
        cliente=cliente, activo=True,
    )
    for disp in dispositivos:
        resultado = enviar_a_token(disp.token, titulo, cuerpo, data=data)
        if resultado == 'ok':
            enviados += 1
        elif resultado == 'token_muerto':
            DispositivoCliente.objects.filter(pk=disp.pk).update(activo=False)
        elif resultado == 'no_configurado':
            break  # sin config no tiene sentido iterar el resto
    return enviados
