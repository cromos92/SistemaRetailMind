"""
Autenticación JWT para CLIENTES finales (app móvil de fidelización).

El subject de estos tokens es un `Cliente`, que NO es el AUTH_USER_MODEL
(`users.Usuario`). Por eso el access token se emite con claims propios
(`tipo='cliente'`, `cliente_id`) y SIN `user_id` (ver `cliente_app_service.emitir_tokens`).

`ClienteJWTAuthentication` hereda de `JWTAuthentication` para reusar la
verificación de firma/expiración, pero sobrescribe `get_user` para resolver una
`CuentaClienteApp` por `cliente_id` — nunca toca `users.Usuario`.

Aislamiento staff ↔ cliente:
- Un token de cliente contra un endpoint staff: `JWTAuthentication` estándar
  busca `user_id` (ausente) → InvalidToken. Cierre natural.
- Un token de staff contra un endpoint de cliente: aquí `get_user` rechaza si
  `tipo != 'cliente'` → InvalidToken.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from app.models import CuentaClienteApp


class ClienteJWTAuthentication(JWTAuthentication):
    """JWTAuthentication cuyo `request.user` es una CuentaClienteApp."""

    def get_user(self, validated_token):
        if validated_token.get('tipo') != 'cliente':
            raise InvalidToken('El token no corresponde a un cliente.')

        cliente_id = validated_token.get('cliente_id')
        if not cliente_id:
            raise InvalidToken('Token de cliente sin identificador.')

        cuenta = (
            CuentaClienteApp.objects
            .select_related('cliente')
            .filter(cliente_id=cliente_id, activa=True)
            .first()
        )
        if not cuenta or not cuenta.cliente.activo:
            raise InvalidToken('Cuenta de cliente inhabilitada.')

        return cuenta
