"""Cifrado en reposo de credenciales Mercado Pago guardadas en BD.

Los access tokens y webhook secrets viven en `MercadoPagoCuenta` (una por
empresa/RUT) CIFRADOS con Fernet (cryptography, ya declarada en
requirements-railway.txt). La clave de cifrado NO está en la BD:

- Si existe la env var `MP_CRED_KEY`, se deriva de ella (recomendado: así un
  dump de la BD + el repo no bastan para leer los tokens).
- Si no, se deriva de `settings.SECRET_KEY` (cero configuración extra).

⚠️ Rotar `SECRET_KEY` (o `MP_CRED_KEY`) invalida lo cifrado: hay que volver a
guardar los tokens desde el admin. `descifrar` devuelve '' y loggea el error
en vez de reventar.

Este módulo NO importa app.models (lo importan los modelos — evita el import
circular con mercadopago_service).
"""
import base64
import hashlib
import logging
import os

from django.conf import settings

logger = logging.getLogger('app')

# Marca los valores cifrados; un valor sin prefijo se trata como texto plano
# (compatibilidad con cargas manuales directas en BD).
_PREFIJO = 'enc:'


def _clave_fernet():
    material = (os.environ.get('MP_CRED_KEY') or settings.SECRET_KEY).encode()
    return base64.urlsafe_b64encode(hashlib.sha256(material).digest())


def cifrar(texto):
    """Cifra un secreto para guardarlo en BD. '' entra, '' sale."""
    if not texto:
        return ''
    from cryptography.fernet import Fernet
    return _PREFIJO + Fernet(_clave_fernet()).encrypt(texto.encode()).decode()


def descifrar(texto):
    """Devuelve el secreto en claro. Tolerante: texto plano legacy pasa tal
    cual; cifrado con otra clave devuelve '' (con error en el log)."""
    if not texto:
        return ''
    if not texto.startswith(_PREFIJO):
        return texto
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_clave_fernet()).decrypt(texto[len(_PREFIJO):].encode()).decode()
    except InvalidToken:
        logger.error(
            'MP: no se pudo descifrar una credencial guardada en BD '
            '(¿cambió SECRET_KEY o MP_CRED_KEY? Re-guardar el token en el admin)'
        )
        return ''


def esta_cifrado(texto):
    return bool(texto) and texto.startswith(_PREFIJO)
