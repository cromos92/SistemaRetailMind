from django.conf import settings

from app.models import PedidoEcommerce


def ecommerce_context(request):
    """Inyecta conteo de pedidos ecommerce pendientes (de toda la empresa) en todos los templates."""
    if not request.user.is_authenticated:
        return {}
    try:
        count = PedidoEcommerce.objects.filter(estado='PENDIENTE').count()
    except Exception:
        count = 0
    return {'pending_ecommerce_count': count}


def pos_kiosk_context(request):
    """
    Resuelve DOS flags para los templates:

      pos_kiosk         → aplica estilos táctiles (botones 48-64px, fuentes
                          16-18px, sin :hover, viewport fijo). El SIDEBAR
                          SE SIGUE VIENDO.

      pos_kiosk_strict  → además oculta sidebar y topbar, para el POS físico
                          que arranca con `?kiosk=1`. Es "kiosko puro".

    Prioridad de resolución:
      1. `?kiosk=1` en la URL → pos_kiosk=True y pos_kiosk_strict=True.
         Persiste en sesión.
      2. `?kiosk=0` en la URL → ambos False. Persiste en sesión.
      3. Si la sesión ya trae un valor explícito, se respeta.
      4. Default: `pos_kiosk = POS_KIOSK_DEFAULT` (True por defecto),
         `pos_kiosk_strict = False` (el menú se ve).

    Nota: `pos_kiosk` se mantiene True para que en desarrollo local el
    operador ya vea los tamaños POS definitivos mientras diseña, pero
    conserva la navegación por el menú.
    """
    default_flag = bool(getattr(settings, 'POS_KIOSK_DEFAULT', False))

    explicit = request.GET.get('kiosk')

    session_strict = None
    session_touch = None
    try:
        if 'pos_kiosk_strict' in request.session:
            session_strict = bool(request.session['pos_kiosk_strict'])
        if 'pos_kiosk' in request.session:
            session_touch = bool(request.session['pos_kiosk'])
    except Exception:
        session_strict = None
        session_touch = None

    if explicit == '1':
        pos_kiosk = True
        pos_kiosk_strict = True
        try:
            request.session['pos_kiosk'] = True
            request.session['pos_kiosk_strict'] = True
        except Exception:
            pass
    elif explicit == '0':
        pos_kiosk = False
        pos_kiosk_strict = False
        try:
            request.session['pos_kiosk'] = False
            request.session['pos_kiosk_strict'] = False
        except Exception:
            pass
    else:
        pos_kiosk = session_touch if session_touch is not None else default_flag
        pos_kiosk_strict = bool(session_strict) if session_strict is not None else False

    return {
        'pos_kiosk': pos_kiosk,
        'pos_kiosk_strict': pos_kiosk_strict,
    }
