"""
Middleware para verificar permisos de acceso basados en la configuración de menú.
Intercepta las peticiones y verifica si el usuario tiene permiso para acceder a la URL.
"""
from django.shortcuts import redirect
from django.http import JsonResponse
from django.contrib import messages
from django.urls import resolve
from .models import PermisoRol, OpcionMenu, Sucursal


# Mapeo de URLs a códigos de opción del menú
# Clave: parte de la URL (se busca si la URL contiene esta cadena)
# Valor: código de la opción en el sistema de permisos
URL_PERMISO_MAP = {
    # Dashboard
    '/app/dashboard_productos/': 'dashboard_productos',
    '/app/dashboard_fifo/': 'dashboard_fifo',
    '/app/verDashboardCompras/': 'dashboard_compras_estrategico',
    '/app/ventas/dashboard': 'dashboard_ventas',
    '/app/dashboard-documentos/': 'dashboard_documentos',
    '/app/dashboard-despachos/': 'dashboard_despachos',
    '/app/dashboard-requerimientos/': 'dashboard_requerimientos',
    '/app/prediccion/': 'prediccion_compras',
    
    # Ventas
    '/app/ticket-venta/': 'ticket_venta',
    '/app/cambios-devoluciones/': 'cambios_devoluciones',
    '/app/pos-dashboard/': 'pos_dashboard',
    '/app/gestion-ventas-documentos/': 'gestion_documentos_ventas',
    '/app/cuadratura-caja/': 'cuadratura_caja',
    '/app/transbank/': 'pos_transbank',
    
    # Documentos
    '/app/emisionDTE/': 'emision_dte',
    '/app/documentos/gestion-dte/': 'gestion_dte',
    '/app/recepcion-dte/': 'recepcion_dte',
    '/app/regularizar-recepciones/': 'regularizar_recepciones',
    '/app/cotizaciones/': 'gestion_cotizaciones',
    '/app/documentos/gestion-correlativos/': 'gestion_correlativos',
    '/app/documentos/gestion-creditos/': 'gestion_creditos',
    
    # Existencias
    '/app/verGestionProducto/': 'gestion_producto',
    '/app/edicion-rapida-precios/': 'edicion_rapida_precios',
    '/app/gestion-precios/edicion-rapida/': 'edicion_rapida_precios',
    '/app/revisar-cambios-precios/': 'revisar_cambios_precios',
    '/app/verMovimientosProducto/': 'movimientos_producto',
    '/app/gestion-inventarios/': 'gestion_inventarios',
    '/app/etiquetas-zebra/': 'gestion_etiquetas_zebra',
    '/app/buscar-productos-sucursal/': 'buscar_productos_sucursal',
    '/app/tarjeta-movimiento/': 'tarjeta_movimiento_producto',
    '/app/despacho-sucursales/': 'despacho_sucursales',
    '/app/trazabilidad-producto/': 'trazabilidad_producto',
    '/app/precios-costos/': 'modificacion_precios_costos',
    
    # Compras
    '/app/verGestionCompras/': 'gestion_compras',
    '/app/eliminar_compra/': 'gestion_compras',
    '/app/verGestionDteCompras/': 'gestion_dte_compras',
    
    # Requerimientos
    '/app/requerimientos/': 'lista_requerimientos',
    
    # Reportes
    '/app/reportes/ventas-sucursal/': 'reporte_ventas_sucursal',
    '/app/reportes/documentos-emitidos/': 'reporte_documentos_emitidos',
    '/app/reporte-existencias/': 'reporte_existencias',
    '/app/reporte-existencias-marca/': 'reporte_existencias_marca',
    '/app/reporte-existencias-sucursal/': 'reporte_existencias_sucursal',
    '/app/resumen-existencias/': 'resumen_existencias',
    '/app/reporte-movimientos-sucursal/': 'reporte_movimientos_sucursal',
    '/app/verReporteDespachosProveedor/': 'reporte_despachos_proveedor',
    '/app/reportes/compras/': 'reporte_compras',
    '/app/reportes/rendimiento-proveedor/': 'reporte_rendimiento_proveedor',
    
    # Ventas (adicionales)
    '/app/ventas/revision-arqueos/': 'revision_arqueos',
    
    # Ecommerce
    '/app/ecommerce/pedidos/': 'ecommerce_pedidos_todos',
    
    # Configuración
    '/app/gestion_usuarios/': 'gestion_usuarios',
    '/app/gestion-sucursales/': 'gestion_sucursales',
    '/empresa_management/lista_empresas/': 'gestion_empresas',
    '/empresa_management/lista_clientes/': 'gestion_clientes',
    '/app/gestion_vendedores/': 'gestion_vendedores',
    '/app/permisos/gestion/': 'gestion_permisos',
    '/app/permisos/': 'gestion_permisos',
    '/app/configuracion/interfaz-prueba-acepta/': 'interfaz_acepta',
    
    # Usuario
    '/users/mi-perfil/': 'mi_perfil',
    '/app/ajuste-stock-rapido/': 'ajuste_stock_rapido',
    '/app/cambiar-empresa/': 'cambiar_empresa',
}

# URLs que nunca deben verificar permisos (páginas públicas o con su propia autenticación)
URLS_SIN_VERIFICACION = [
    '/app/home/',       # Home siempre accesible (tiene su propia verificación interna)
    '/app/dashboard/',  # Dashboard general siempre accesible (es diferente a dashboard_general del menú)
    '/app/bienvenida/', # Página de bienvenida básica siempre accesible
]

# URLs restringidas a sucursales específicas (por alias).
# Si la sucursal activa NO está en la lista, se deniega el acceso con un mensaje claro.
URL_SOLO_SUCURSALES = {
    '/app/verGestionProducto/': ['EDEL', 'GILD', 'IMP', 'PA00'],
}

# URLs que siempre están permitidas (login, logout, static, etc.)
URLS_SIEMPRE_PERMITIDAS = [
    '/accounts/',
    '/admin/',
    '/static/',
    '/media/',
    '/api/',  # APIs pueden tener su propia autenticación
    '/__debug__/',  # Django Debug Toolbar
    '/logout/',
    '/login/',
    '/favicon.ico',
]


class PermisosMenuMiddleware:
    """
    Middleware que verifica los permisos de acceso basados en la configuración del menú.
    
    Si el usuario no tiene permiso 'puede_ver' para una opción del menú,
    no podrá acceder a la URL correspondiente.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if (request.user.is_authenticated
                and not request.session.get('idSucursalActual')
                and request.path.startswith('/app/')):
            self._corregir_sesion_sin_sucursal(request)

        resultado_verificacion = self.verificar_permiso(request)
        
        if resultado_verificacion is not None:
            return resultado_verificacion
        
        response = self.get_response(request)
        return response

    @staticmethod
    def _corregir_sesion_sin_sucursal(request):
        from .models import EmpresaUser
        eu = EmpresaUser.objects.filter(
            user=request.user, status=True, sucursal__isnull=False
        ).select_related('empresa', 'sucursal').first()
        if not eu:
            return
        if not eu.active:
            EmpresaUser.objects.filter(user=request.user).update(active=False)
            eu.active = True
            eu.save(update_fields=['active'])
        request.session['idEmpresaActual'] = eu.empresa.id
        request.session['idSucursalActual'] = eu.sucursal.id
        request.session['direccionSucursal'] = eu.sucursal.direccion or 'Sin dirección'
        request.session['alias'] = eu.sucursal.alias or 'Sin sucursal'
        request.session['nombreEmpresaActual'] = eu.empresa.nombre
        request.session['rutEmpresaActual'] = eu.empresa.rut
    
    def verificar_permiso(self, request):
        """
        Verifica si el usuario tiene permiso para acceder a la URL actual.
        
        Returns:
            None si tiene permiso, o una respuesta de error/redirect si no tiene.
        """
        path = request.path
        
        # 1. Verificar si la URL está siempre permitida (login, logout, static, etc.)
        for url_permitida in URLS_SIEMPRE_PERMITIDAS:
            if path.startswith(url_permitida):
                return None  # Permitir acceso
        
        # 2. Verificar si la URL no requiere verificación de permisos
        for url_sin_verificacion in URLS_SIN_VERIFICACION:
            if path.startswith(url_sin_verificacion):
                return None  # Permitir acceso
        
        # 3. Si el usuario no está autenticado, dejar que Django maneje la autenticación
        if not request.user.is_authenticated:
            return None  # Django redirigirá al login si es necesario

        # 4a. Verificar restricción por sucursal activa (antes del chequeo de roles)
        for url_restringida, sucursales_permitidas in URL_SOLO_SUCURSALES.items():
            if url_restringida in path:
                alias_actual = request.session.get('alias', '')
                if alias_actual.upper() not in [s.upper() for s in sucursales_permitidas]:
                    return self.denegar_acceso_sucursal(
                        request, sucursales_permitidas, alias_actual
                    )
                break
        
        # 4. Buscar si la URL tiene un permiso asociado
        # Nota: Todos los usuarios respetan los permisos por rol. is_superuser no otorga privilegios.
        codigo_opcion = self.obtener_codigo_opcion(path)
        
        if codigo_opcion is None:
            # URL no está en el mapa de permisos, permitir acceso
            # (esto incluye páginas de error, APIs sin mapeo, etc.)
            return None
        
        # 6. Obtener la sucursal actual de la sesión
        sucursal_id = request.session.get('idSucursalActual')
        
        # 7. Verificar permiso (por rol y por sucursal)
        tiene_permiso = PermisoRol.tiene_permiso(
            usuario=request.user,
            codigo_opcion=codigo_opcion,
            tipo_permiso='puede_ver',
            sucursal_id=sucursal_id
        )
        
        if tiene_permiso:
            return None  # Permitir acceso
        
        # 7. Denegar acceso
        return self.denegar_acceso(request, codigo_opcion)
    
    def obtener_codigo_opcion(self, path):
        """
        Obtiene el código de opción del menú para una URL dada.
        """
        # Buscar coincidencia exacta primero
        if path in URL_PERMISO_MAP:
            return URL_PERMISO_MAP[path]
        
        # Buscar coincidencia parcial (la URL contiene la clave)
        for url_pattern, codigo in URL_PERMISO_MAP.items():
            if url_pattern in path:
                return codigo
        
        return None
    
    def denegar_acceso(self, request, codigo_opcion):
        """
        Genera la respuesta de acceso denegado.
        """
        # Determinar si es petición AJAX
        is_ajax = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            request.content_type == 'application/json' or
            request.headers.get('Accept', '').startswith('application/json')
        )
        
        if is_ajax:
            return JsonResponse({
                'success': False,
                'error': True,
                'mensaje': 'No tienes permiso para acceder a esta funcionalidad.',
                'codigo_requerido': codigo_opcion
            }, status=403)
        else:
            messages.error(
                request,
                '⚠️ Acceso denegado: No tienes permiso para acceder a esta funcionalidad. '
                'Contacta al administrador si crees que deberías tener acceso.'
            )
            # Redirigir a página de bienvenida (siempre accesible)
            return redirect('bienvenida')

    def denegar_acceso_sucursal(self, request, sucursales_permitidas, alias_actual):
        """
        Deniega el acceso porque la sucursal activa no está en la lista permitida.
        """
        is_ajax = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            request.content_type == 'application/json' or
            request.headers.get('Accept', '').startswith('application/json')
        )
        lista = ', '.join(sucursales_permitidas)
        mensaje = (
            f'⚠️ Módulo restringido: solo accesible desde las sucursales {lista}. '
            f'Tu sucursal activa es "{alias_actual or "Sin sucursal"}". '
            'Cambia de sucursal para continuar.'
        )
        if is_ajax:
            return JsonResponse({
                'success': False,
                'error': True,
                'mensaje': mensaje,
            }, status=403)
        messages.error(request, mensaje)
        return redirect('bienvenida')
