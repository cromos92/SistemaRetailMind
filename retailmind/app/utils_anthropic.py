"""
Opciones comunes para construir el cliente de Anthropic (lectura de facturas
en app/services/carga_factura/lectura.py y el asistente en assistant/agent.py).

Variables de entorno:
  - ANTHROPIC_API_KEY: la clave (settings.ANTHROPIC_API_KEY la lee de ahí).
  - ANTHROPIC_WORKSPACE_ID (opcional; la pantalla Configuración → Inteligencia
    Artificial manda sobre ella): id del workspace (wrkspc_…). Una clave
    creada a nivel de organización, sin workspace, es rechazada con 400
    «This API key is not scoped to a workspace…» salvo que cada petición
    lleve la cabecera anthropic-workspace-id; el SDK la envía desde
    default_headers. Una clave creada DENTRO de un workspace no la necesita.
"""


def opciones_cliente_anthropic():
    """kwargs extra para anthropic.Anthropic(...) según la configuración."""
    from app.utils_ia import workspace_anthropic
    workspace = workspace_anthropic()
    if not workspace:
        return {}
    return {'default_headers': {'anthropic-workspace-id': workspace}}


def explicar_error_anthropic(exc):
    """Mensaje para la persona a partir de un error del SDK (o None si no es
    uno de los casos conocidos de configuración)."""
    texto = str(getattr(exc, 'message', '') or exc)
    nombre = type(exc).__name__
    if nombre == 'AuthenticationError':
        return ('Anthropic rechazó la clave: revísala en Configuración → Inteligencia Artificial '
                '(o ANTHROPIC_API_KEY); está inválida o revocada.')
    if nombre == 'BadRequestError' and 'workspace' in texto.lower():
        return ('La clave de Anthropic es de la organización y no está asociada a un workspace. '
                'Anótalo en Configuración → Inteligencia Artificial (o ANTHROPIC_WORKSPACE_ID): '
                'el id del workspace (Console → Settings → '
                'Workspaces → el workspace → ID, empieza con wrkspc_) o crea la clave dentro '
                'de un workspace.')
    if nombre == 'PermissionDeniedError':
        return f'Anthropic no autorizó la petición: {texto}'
    if nombre in ('RateLimitError', 'OverloadedError'):
        return 'Anthropic está saturado o se alcanzó el límite de uso; vuelve a intentar en un rato.'
    return None
