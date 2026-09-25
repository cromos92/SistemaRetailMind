"""
Conciliación Mercado Pago por empresa: pestañas «Liberaciones y banco» y
«Asignación de retiros» de /app/ventas/dineros-mercadopago/.

Solo lectura. La lógica vive en services/conciliacion_mp_empresas.py; el permiso
de página lo pone el middleware (prefijo /app/api/mercadopago/conciliacion/).
"""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from .services import conciliacion_mp_empresas as emp
from .views_mercadopago import _es_admin, _sucursal_filtro_conciliacion

logger = logging.getLogger('app')


def _sucursal(request):
    valor = _sucursal_filtro_conciliacion(request)
    return int(valor) if str(valor or '').isdigit() else None


@login_required
def api_conciliacion_empresas_mp(request):
    """GET .../conciliacion/empresas/?desde=&hasta=&sucursal_id=

    Por empresa (cuenta MP): dónde está hoy la plata y los retiros del período
    con lo que se llevó cada uno. Quien no es administrador ve solo la empresa
    de su tienda.
    """
    sucursal_id = _sucursal(request)
    if not _es_admin(request) and sucursal_id is None:
        return JsonResponse({'success': True, 'empresas': [], 'partes': emp.PARTES})
    data = emp.liberaciones_por_empresa(request.GET.get('desde'), request.GET.get('hasta'), sucursal_id)
    return JsonResponse({'success': True, **data})


@login_required
def api_conciliacion_asignacion_empresa_mp(request):
    """GET .../conciliacion/asignacion-empresa/?mes=YYYY-MM&empresa=<clave>&sucursal_id= (solo admin).

    De UNA empresa: cómo quedó repartido lo cobrado en el mes entre los retiros
    al banco, lo que sigue en Mercado Pago y cada cobro con su retiro.
    """
    if not _es_admin(request):
        return JsonResponse({'success': False, 'error': 'Solo administradores.'}, status=403)
    data = emp.asignacion_empresa(request.GET.get('mes'), request.GET.get('empresa') or None, _sucursal(request))
    return JsonResponse({'success': True, **data})
