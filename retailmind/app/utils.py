# utils.py
from app.models import Empresa

def get_empresa_actual(request):
    empresa_id = request.session.get('idEmpresaActual')
    if empresa_id:
        try:
            return Empresa.objects.get(id=empresa_id)
        except Empresa.DoesNotExist:
            return None
    return None
