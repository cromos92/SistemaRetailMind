"""
Comando de Django para inicializar m?dulos, opciones y permisos del sistema
python manage.py inicializar_permisos
"""
from django.core.management.base import BaseCommand
from app.models import ModuloSistema, OpcionMenu, PermisoRol


class Command(BaseCommand):
    help = 'Inicializa los m?dulos, opciones del men? y permisos por defecto del sistema'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('>> Iniciando configuracion de permisos...'))
        
        # Crear m?dulos y sus opciones
        self.crear_modulo_dashboard()
        self.crear_modulo_ventas()
        self.crear_modulo_documentos()
        self.crear_modulo_existencias()
        self.crear_modulo_compras()
        self.crear_modulo_requerimientos()
        self.crear_modulo_reportes()
        self.crear_modulo_configuracion()
        self.crear_modulo_ecommerce()
        self.crear_modulo_fidelizacion()  # GiftCards + Puntos de fidelizaci?n
        self.crear_modulo_usuario()  # Nuevo m?dulo para opciones de usuario
        
        # Crear permisos por defecto para cada rol
        # Nota: is_superuser de Django tiene acceso total autom?ticamente
        self.crear_permisos_administrador()
        self.crear_permisos_administracion()
        self.crear_permisos_jefe_local()
        self.crear_permisos_cajero()
        self.crear_permisos_vendedor()
        
        self.stdout.write(self.style.SUCCESS('>> Permisos inicializados correctamente!'))

    def crear_modulo_dashboard(self):
        """Crear m?dulo Dashboard y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='dashboard',
            defaults={
                'nombre': 'Dashboard',
                'descripcion': 'Tableros de control y m?tricas',
                'icono': 'ri-pie-chart-line',
                'orden': 1
            }
        )
        
        opciones = [
            ('dashboard_general', 'Dashboard General', 'verHome', None, 'ri-dashboard-3-line', 1),
            ('dashboard_ventas', 'Dashboard Ventas', 'dashboard_ventas', None, 'ri-dashboard-line', 2),
            ('dashboard_productos', 'Dashboard Productos', None, '/app/dashboard_productos/', 'bi-box-seam', 3),
            ('dashboard_fifo', 'Dashboard FIFO', None, '/app/dashboard_fifo/', 'bi-arrow-repeat', 4),
            ('dashboard_compras_estrategico', 'Dashboard Compras', None, '/app/verDashboardCompras/', 'ri-shopping-bag-line', 5),
            ('dashboard_documentos', 'Dashboard Documentos', None, '/app/dashboard-documentos/', 'ri-file-list-line', 6),
            ('dashboard_despachos', 'Dashboard Despachos', None, '/app/dashboard-despachos/', 'ri-truck-line', 7),
            ('dashboard_requerimientos', 'Dashboard Requerimientos', None, '/app/dashboard-requerimientos/', 'ri-customer-service-2-line', 8),
        ]
        
        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Dashboard] Modulo Dashboard creado')

    def crear_modulo_ventas(self):
        """Crear m?dulo Ventas y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='ventas',
            defaults={
                'nombre': 'M?dulo Ventas',
                'descripcion': 'Gesti?n de ventas y punto de venta',
                'icono': 'ri-money-cny-circle-line',
                'orden': 2
            }
        )
        
        opciones = [
            ('ticket_venta', 'Ticket de Venta', 'ticket_venta', 'mdi-receipt', 1),
            ('cambios_devoluciones', 'Cambios y Devoluciones', 'gestion_cambios_devoluciones', 'ri-exchange-line', 2),
            ('pos_dashboard', 'Generar Venta (POS)', 'pos_dashboard', 'ri-dashboard-3-line', 3),
            ('gestion_documentos_ventas', 'Consulta Documentos', 'gestion_ventas_documentos', 'ri-file-search-line', 4),
            ('cuadratura_caja', 'Cuadratura y Arqueo', 'cuadratura_caja', 'ri-calculator-line', 5),
            ('pos_transbank', 'POS Transbank', 'gestion_transbank_pos_sdk', 'ri-bank-card-line', 6),
            ('revision_arqueos', 'Revisi?n Arqueos y Dep?sitos', 'revision_arqueos', 'ri-shield-check-line', 7),
        ]
        
        for codigo, nombre, url_name, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Ventas] Modulo Ventas creado')

    def crear_modulo_documentos(self):
        """Crear m?dulo Documentos y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='documentos',
            defaults={
                'nombre': 'M?dulo Documentos',
                'descripcion': 'Gesti?n de documentos tributarios',
                'icono': 'ri-file-list-line',
                'orden': 3
            }
        )
        
        opciones = [
            ('emision_dte', 'Emisi?n DTE', None, '/app/emisionDTE/', 'bi-file-earmark-plus', 1),
            ('gestion_dte', 'Gesti?n DTE', None, '/app/documentos/gestion-dte/', 'bi-file-earmark-text', 2),
            ('recepcion_dte', 'Recepci?n Documentos', 'recepcion_dte', None, 'bi-box-arrow-in-down', 3),
            ('gestion_cotizaciones', 'Gesti?n Cotizaciones', 'gestion_cotizaciones', None, 'ri-file-text-line', 5),
            ('gestion_correlativos', 'Gesti?n Correlativos', None, '/app/documentos/gestion-correlativos/', 'ri-file-list-3-line', 6),
            ('gestion_creditos', 'Gesti?n Cr?ditos', None, '/app/documentos/gestion-creditos/', 'ri-bank-card-line', 7),
            # Permiso granular: controla el bot?n "Descargar TXT Acepta"
            # en la pantalla Gesti?n DTE y el endpoint que genera el TXT.
            ('dte_descargar_txt', 'Descargar TXT Acepta de DTE', None, None, 'bi-file-earmark-text', 8),
        ]
        
        for item in opciones:
            if len(item) == 6:
                codigo, nombre, url_name, url_path, icono, orden = item
            else:
                codigo, nombre, url_name, icono, orden = item
                url_path = None
            
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )

        # Deprecated: regularización ahora vive dentro de Recepción Documentos.
        OpcionMenu.objects.filter(codigo='regularizar_recepciones').update(activo=False)
        
        self.stdout.write('[Documentos] Modulo Documentos creado')

    def crear_modulo_existencias(self):
        """Crear m?dulo Existencias y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='existencias',
            defaults={
                'nombre': 'M?dulo Existencias',
                'descripcion': 'Gesti?n de inventario y productos',
                'icono': 'ri-store-3-line',
                'orden': 4
            }
        )
        
        opciones = [
            ('gestion_producto', 'Gesti?n Producto', None, '/app/verGestionProducto/', 'ri-archive-line', 1),
            ('edicion_rapida_precios', 'Gesti?n de Precios', 'edicion_rapida_precios', None, 'ri-price-tag-3-line', 2),
            ('revisar_cambios_precios', 'Alertas de Precios', 'revisar_cambios_precios', None, 'ri-notification-badge-line', 3),
            ('movimientos_producto', 'Movimientos Por Sucursal', None, '/app/verMovimientosProducto/', 'ri-arrow-left-right-line', 4),
            ('gestion_inventarios', 'Gesti?n de Inventarios', 'gestion_inventarios', None, 'ri-clipboard-line', 5),
            ('gestion_etiquetas_zebra', 'Impresi?n Etiquetas Zebra', 'gestion_etiquetas_zebra', None, 'ri-printer-line', 6),
            ('buscar_productos_sucursal', 'Buscar Producto Sucursal', 'buscar_productos_sucursal', None, 'ri-search-line', 7),
            ('tarjeta_movimiento_producto', 'Tarjeta Movimiento Producto', 'tarjeta_movimiento_producto', None, 'ri-file-list-3-line', 8),
            ('despacho_sucursales', 'Despacho a Sucursales', 'despacho_todas_sucursales', None, 'ri-truck-line', 9),
            ('trazabilidad_producto', 'Trazabilidad Completa', 'trazabilidad_producto', None, 'ri-route-line', 10),
            ('modificacion_precios_costos', 'Modificaci?n Precios y Costos', 'modificacion_precios_costos', None, 'ri-money-dollar-circle-line', 11),
            ('ver_guias_talla', 'Guias de Talla', 'ver_guias_talla', None, 'ri-ruler-line', 12),
        ]
        
        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Existencias] Modulo Existencias creado')

    def crear_modulo_compras(self):
        """Crear m?dulo Compras y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='compras',
            defaults={
                'nombre': 'M?dulo Compras',
                'descripcion': 'Gesti?n de compras y proveedores',
                'icono': 'ri-shopping-bag-line',
                'orden': 5
            }
        )
        
        opciones = [
            ('gestion_compras', 'Gesti?n Compras', None, '/app/verGestionCompras/', 'ri-shopping-bag-line', 1),
            ('gestion_dte_compras', 'Gesti?n Documentos Compras', None, '/app/verGestionDteCompras/', 'ri-file-list-line', 2),
            ('prediccion_compras', 'Predicci?n de Compras', None, '/app/prediccion/', 'ri-line-chart-line', 3),
        ]
        
        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Compras] Modulo Compras creado')

    def crear_modulo_requerimientos(self):
        """Crear m?dulo Requerimientos y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='requerimientos',
            defaults={
                'nombre': 'M?dulo Requerimientos',
                'descripcion': 'Gesti?n de garant?as y servicios',
                'icono': 'ri-customer-service-2-line',
                'orden': 6
            }
        )
        
        opciones = [
            ('lista_requerimientos', 'Lista de Requerimientos', 'modulo_requerimientos', None, 'ri-list-check', 1),
            ('crear_requerimiento', 'Crear Requerimiento', 'crear_requerimiento_vista', None, 'ri-add-circle-line', 2),
            ('gestionar_requerimientos', 'Gestionar Requerimientos', 'gestionar_requerimientos_vista', None, 'ri-settings-3-line', 3),
        ]
        
        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Requerimientos] Modulo Requerimientos creado')

    def crear_modulo_reportes(self):
        """Crear m?dulo Reportes y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='reportes',
            defaults={
                'nombre': 'M?dulo Reportes',
                'descripcion': 'Reportes y an?lisis de datos',
                'icono': 'ri-bar-chart-grouped-line',
                'orden': 7
            }
        )
        
        opciones = [
            # Reportes Ventas
            ('reporte_ventas_sucursal', 'Ventas por Sucursal', None, '/app/reportes/ventas-sucursal/', 'ri-store-2-line', 1),
            ('reporte_ventas_comparativo', 'Comparativo de Ventas', None, '/app/reportes/ventas-comparativo/', 'ri-bar-chart-2-line', 2),
            ('reporte_productos_vendidos', 'Productos Vendidos', None, '/app/reportes/productos-vendidos/', 'ri-shopping-bag-line', 3),
            ('reporte_ventas_internet', 'Ventas Internet', 'ver_reporte_ventas_internet', None, 'ri-global-line', 4),
            ('reporte_documentos_emitidos', 'Documentos Emitidos', None, '/app/reportes/documentos-emitidos/', 'ri-file-list-3-line', 5),
            # Permiso granular embebido dentro del reporte ventas-sucursal:
            # controla la visibilidad del botón "Comisiones" y los endpoints
            # `obtener_comisiones_por_vendedor` / `exportar_comisiones_vendedor_excel`.
            ('reporte_comisiones_vendedor', 'Reporte Comisiones por Vendedor', None, None, 'ri-percent-line', 6),
            # Reportes Existencias
            ('reporte_existencias', 'Reporte de Existencias', 'ver_reporte_existencias', None, 'ri-file-list-3-line', 3),
            ('reporte_existencias_marca', 'Existencias por Marca', 'ver_reporte_existencias_marca', None, 'ri-price-tag-3-line', 4),
            ('reporte_existencias_sucursal', 'Existencias por Sucursal', 'ver_reporte_existencias_sucursal', None, 'ri-store-2-line', 5),
            ('resumen_existencias', 'Resumen Existencias', 'ver_resumen_existencias', None, 'ri-pie-chart-line', 6),
            ('reporte_movimientos_sucursal', 'Inicial vs Restante', 'ver_reporte_movimientos_sucursal', None, 'ri-exchange-line', 7),
            # Reportes Compras
            ('reporte_despachos_proveedor', 'Despachos por Proveedor', None, '/app/verReporteDespachosProveedor/', 'bi-truck', 8),
            ('reporte_compras', 'Reporte de Compras', None, '/app/reportes/compras/', 'bi-bag', 9),
            ('reporte_rendimiento_proveedor', 'Rendimiento por Proveedor', None, '/app/reportes/rendimiento-proveedor/', 'bi-people', 10),
        ]
        
        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Reportes] Modulo Reportes creado')

    def crear_modulo_configuracion(self):
        """Crear m?dulo Configuraci?n y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='configuracion',
            defaults={
                'nombre': 'Configuraci?n',
                'descripcion': 'Configuraci?n del sistema',
                'icono': 'ri-settings-4-line',
                'orden': 8
            }
        )
        
        opciones = [
            ('gestion_usuarios', 'Gesti?n Usuarios', None, '/app/gestion_usuarios/', 'bi-people-fill', 1),
            ('gestion_sucursales', 'Gesti?n Sucursales', None, '/app/gestion-sucursales/', 'bi-building', 2),
            ('gestion_empresas', 'Gesti?n Empresas', None, '/empresa_management/lista_empresas/', 'bi-building-fill', 3),
            ('gestion_clientes', 'Gesti?n Clientes', None, '/empresa_management/lista_clientes/', 'bi-person-badge-fill', 4),
            ('gestion_vendedores', 'Gesti?n Vendedores', None, '/app/gestion_vendedores/', 'bi-people', 5),
            ('gestion_permisos', 'Gesti?n Permisos', 'gestion_permisos', None, 'bi-shield-lock', 6),
            ('interfaz_acepta', 'Interfaz Prueba Acepta', None, '/app/configuracion/interfaz-prueba-acepta/', 'ri-file-text-line', 7),
            ('integraciones_ecommerce', 'Integraciones Ecommerce', 'integraciones_ecommerce', None, 'ri-image-line', 8),
        ]
        
        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Configuracion] Modulo Configuracion creado')

    def crear_modulo_ecommerce(self):
        """Crear m?dulo Ecommerce y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='ecommerce',
            defaults={
                'nombre': 'Ecommerce',
                'descripcion': 'Gesti?n de pedidos de comercio electr?nico',
                'icono': 'ri-shopping-cart-2-line',
                'orden': 9
            }
        )

        opciones = [
            ('ecommerce_pedidos_pendientes', 'Pendientes de Facturar', 'pedidos_ecommerce_list', None, 'bi-hourglass-split', 1),
            ('ecommerce_pedidos_facturados', 'Facturados', 'pedidos_ecommerce_list', None, 'bi-check-circle', 2),
            ('ecommerce_pedidos_todos', 'Todos los Pedidos', 'pedidos_ecommerce_list', None, 'bi-grid-3x3-gap', 3),
        ]

        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )

        self.stdout.write('[Ecommerce] Modulo Ecommerce creado')

    def crear_modulo_usuario(self):
        """Crear m?dulo Usuario con opciones de perfil y acciones r?pidas"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='usuario',
            defaults={
                'nombre': 'Mi Cuenta',
                'descripcion': 'Opciones de perfil de usuario y acciones r?pidas',
                'icono': 'ri-user-settings-line',
                'orden': 10
            }
        )
        
        opciones = [
            ('mi_perfil', 'Mi Perfil', 'users:mi_perfil', None, 'ri-user-settings-line', 1),
            ('ajuste_stock_rapido', 'Ajuste de Stock', 'ajuste_stock_rapido', None, 'ri-inbox-line', 2),
            ('cambiar_empresa', 'Cambiar Empresa/Sucursal', 'cambiar_empresa', None, 'ri-building-line', 3),
        ]
        
        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )
        
        self.stdout.write('[Usuario] Modulo Mi Cuenta creado')

    def crear_modulo_fidelizacion(self):
        """Crear modulo Fidelizacion (GiftCards + Puntos) y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='fidelizacion',
            defaults={
                'nombre': 'Fidelizaci?n',
                'descripcion': 'Gift cards y programa de puntos de clientes',
                'icono': 'ri-gift-line',
                'orden': 8
            }
        )

        opciones = [
            ('giftcards_listado', 'Gift Cards', None, '/app/giftcards/', 'ri-gift-line', 1),
            ('giftcards_emitir', 'Emitir Gift Card', None, '/app/giftcards/emitir/', 'ri-add-circle-line', 2),
            ('fidelizacion_cuentas', 'Clientes y Puntos', None, '/app/fidelizacion/', 'ri-user-star-line', 3),
            ('fidelizacion_programa', 'Configuraci?n Programa', None, '/app/fidelizacion/configuracion/', 'ri-settings-3-line', 4),
            ('fidelizacion_reporte', 'Reporte Fidelizaci?n', None, '/app/fidelizacion/reporte/', 'ri-bar-chart-box-line', 5),
        ]

        for codigo, nombre, url_name, url_path, icono, orden in opciones:
            OpcionMenu.objects.get_or_create(
                codigo=codigo,
                defaults={
                    'modulo': modulo,
                    'nombre': nombre,
                    'url_name': url_name,
                    'url_path': url_path,
                    'icono': icono,
                    'orden': orden
                }
            )

        self.stdout.write('[Fidelizacion] Modulo Fidelizacion creado')

    def crear_permisos_administrador(self):
        """Crear permisos para el rol Administrador (acceso total)"""
        self.stdout.write('[ADMIN] Creando permisos para Administrador...')
        
        opciones = OpcionMenu.objects.all()
        for opcion in opciones:
            PermisoRol.objects.get_or_create(
                rol='administrador',
                opcion_menu=opcion,
                defaults={
                    'puede_ver': True,
                    'puede_crear': True,
                    'puede_editar': True,
                    'puede_eliminar': True,
                    'puede_exportar': True,
                }
            )
        
        self.stdout.write(f'   >> {opciones.count()} permisos creados para Administrador')

    def crear_permisos_administracion(self):
        """Crear permisos para el rol Administracion."""
        self.stdout.write('[ADMINISTRACION] Creando permisos para Administracion...')

        opciones = OpcionMenu.objects.all()
        for opcion in opciones:
            PermisoRol.objects.get_or_create(
                rol='administracion',
                opcion_menu=opcion,
                defaults={
                    'puede_ver': True,
                    'puede_crear': True,
                    'puede_editar': True,
                    'puede_eliminar': False,
                    'puede_exportar': True,
                    'puede_aprobar': True,
                }
            )

        self.stdout.write(f'   >> {opciones.count()} permisos creados para Administracion')

    def crear_permisos_jefe_local(self):
        """Crear permisos para el rol Jefe Local"""
        self.stdout.write('[JEFE] Creando permisos para Jefe Local...')
        
        # C?digos que el jefe local puede ver
        codigos_permitidos = [
            # Dashboard
            'dashboard_general', 'dashboard_ventas', 'dashboard_productos', 'dashboard_fifo',
            'dashboard_compras_estrategico', 'dashboard_documentos', 'dashboard_despachos',
            'dashboard_requerimientos',
            # Ventas
            'ticket_venta', 'cambios_devoluciones', 'pos_dashboard', 'gestion_documentos_ventas',
            'cuadratura_caja', 'pos_transbank', 'revision_arqueos',
            # Documentos
            'emision_dte', 'gestion_dte', 'recepcion_dte',
            'gestion_cotizaciones', 'gestion_creditos',
            # Existencias
            'gestion_producto', 'edicion_rapida_precios', 'revisar_cambios_precios', 'movimientos_producto',
            'gestion_inventarios', 'gestion_etiquetas_zebra', 'buscar_productos_sucursal',
            'tarjeta_movimiento_producto', 'despacho_sucursales', 'trazabilidad_producto',
            'modificacion_precios_costos', 'ver_guias_talla',
            # Compras
            'gestion_compras', 'gestion_dte_compras', 'prediccion_compras',
            # Ecommerce
            'ecommerce_pedidos_pendientes', 'ecommerce_pedidos_facturados', 'ecommerce_pedidos_todos',
            # Requerimientos
            'lista_requerimientos', 'crear_requerimiento', 'gestionar_requerimientos',
            # Reportes
            'reporte_ventas_sucursal', 'reporte_ventas_comparativo', 'reporte_productos_vendidos', 'reporte_ventas_internet', 'reporte_documentos_emitidos', 'reporte_existencias',
            'reporte_existencias_marca', 'reporte_existencias_sucursal', 'reporte_despachos_proveedor',
            'resumen_existencias', 'reporte_movimientos_sucursal', 'reporte_compras',
            'reporte_rendimiento_proveedor',
            # Configuraci?n
            'gestion_clientes', 'gestion_vendedores',
            # Fidelizaci?n (sin config del programa)
            'giftcards_listado', 'giftcards_emitir', 'fidelizacion_cuentas',
            'fidelizacion_reporte',
            # Mi Cuenta
            'mi_perfil', 'ajuste_stock_rapido', 'cambiar_empresa',
        ]

        opciones = OpcionMenu.objects.filter(codigo__in=codigos_permitidos)
        for opcion in opciones:
            PermisoRol.objects.get_or_create(
                rol='jefe_local',
                opcion_menu=opcion,
                defaults={
                    'puede_ver': True,
                    'puede_crear': True,
                    'puede_editar': True,
                    'puede_eliminar': False,  # No puede eliminar
                    'puede_exportar': True,
                }
            )
        
        self.stdout.write(f'   >> {opciones.count()} permisos creados para Jefe Local')

    def crear_permisos_cajero(self):
        """Crear permisos para el rol Cajero"""
        self.stdout.write('[CAJERO] Creando permisos para Cajero...')
        
        # C?digos que el cajero puede ver
        codigos_permitidos = [
            # Dashboard
            'dashboard_general',
            # Ventas
            'ticket_venta', 'cambios_devoluciones', 'pos_dashboard', 'gestion_documentos_ventas',
            'cuadratura_caja', 'pos_transbank',
            # Existencias (solo consulta)
            'buscar_productos_sucursal',
            # Requerimientos (solo crear)
            'lista_requerimientos', 'crear_requerimiento',
            # Documentos internos: puede recibir/reportar problemas, no aprobar regularizaciones
            'recepcion_dte',
            # Fidelizaci?n: solo consultar gift cards (redime al cobrar) y ver puntos
            'giftcards_listado', 'fidelizacion_cuentas',
            # Mi Cuenta
            'mi_perfil', 'ajuste_stock_rapido',
        ]

        opciones = OpcionMenu.objects.filter(codigo__in=codigos_permitidos)
        for opcion in opciones:
            # Determinar permisos seg?n la opci?n
            puede_crear = opcion.codigo in ['ticket_venta', 'pos_dashboard', 'crear_requerimiento', 'recepcion_dte']
            puede_editar = opcion.codigo in ['cuadratura_caja']
            
            permiso, created = PermisoRol.objects.get_or_create(
                rol='cajero',
                opcion_menu=opcion,
                defaults={
                    'puede_ver': True,
                    'puede_crear': puede_crear,
                    'puede_editar': puede_editar,
                    'puede_eliminar': False,
                    'puede_exportar': False,
                    'puede_aprobar': False,
                }
            )
            if not created and opcion.codigo == 'recepcion_dte':
                permiso.puede_ver = True
                permiso.puede_crear = True
                permiso.puede_aprobar = False
                permiso.save(update_fields=['puede_ver', 'puede_crear', 'puede_aprobar'])
        
        self.stdout.write(f'   >> {opciones.count()} permisos creados para Cajero')

    def crear_permisos_vendedor(self):
        """Crear permisos para el rol Vendedor"""
        self.stdout.write('[VENDEDOR] Creando permisos para Vendedor...')
        
        # C?digos que el vendedor puede ver
        codigos_permitidos = [
            # Dashboard
            'dashboard_general',
            # Ventas
            'ticket_venta', 'pos_dashboard',
            # Existencias (solo consulta)
            'buscar_productos_sucursal',
            # Requerimientos
            'lista_requerimientos', 'crear_requerimiento',
            # Fidelizaci?n: solo lectura
            'giftcards_listado', 'fidelizacion_cuentas',
            # Mi Cuenta
            'mi_perfil',
        ]

        # Fidelizaci?n es solo lectura para el vendedor (no crea gift cards)
        solo_lectura = {'giftcards_listado', 'fidelizacion_cuentas'}
        opciones = OpcionMenu.objects.filter(codigo__in=codigos_permitidos)
        for opcion in opciones:
            # Solo puede crear ventas y requerimientos
            puede_crear = opcion.codigo not in solo_lectura

            PermisoRol.objects.get_or_create(
                rol='vendedor',
                opcion_menu=opcion,
                defaults={
                    'puede_ver': True,
                    'puede_crear': puede_crear,
                    'puede_editar': False,
                    'puede_eliminar': False,
                    'puede_exportar': False,
                }
            )
        
        self.stdout.write(f'   >> {opciones.count()} permisos creados para Vendedor')

