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
