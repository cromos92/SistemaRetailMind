"""
Comando de Django para inicializar módulos, opciones y permisos del sistema
python manage.py inicializar_permisos
"""
from django.core.management.base import BaseCommand
from app.models import ModuloSistema, OpcionMenu, PermisoRol

# Opciones que ningún rol recibe por defecto: solo el Maestro (que pasa todo).
SOLO_MAESTRO = ('asociar_pagos_mercadopago',)


class Command(BaseCommand):
    help = 'Inicializa los módulos, opciones del menú y permisos por defecto del sistema'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('>> Iniciando configuracion de permisos...'))
        
        # Crear módulos y sus opciones
        self.crear_modulo_dashboard()
        self.crear_modulo_ventas()
        self.crear_modulo_documentos()
        self.crear_modulo_existencias()
        self.crear_modulo_compras()
        self.crear_modulo_requerimientos()
        self.crear_modulo_reportes()
        self.crear_modulo_liquidacion()  # Plan de Liquidacion + Campanas
        self.crear_modulo_configuracion()
        self.crear_modulo_ecommerce()
        self.crear_modulo_fidelizacion()  # GiftCards + Puntos de fidelización
        self.crear_modulo_usuario()  # Nuevo módulo para opciones de usuario
        
        # Crear permisos por defecto para cada rol
        # Nota: is_superuser de Django NO otorga privilegios. El rol 'maestro'
        # tiene acceso total sin filas (ver PermisoRol.tiene_permiso). Los
        # bloqueos del Administrador (NC, Conciliación MP) los aplica el
        # comando configurar_rol_maestro; este seeder solo CREA filas que
        # faltan (get_or_create), nunca vuelve a encender una apagada.
        self.crear_permisos_administrador()
        self.crear_permisos_administracion()
        self.crear_permisos_jefe_local()
        self.crear_permisos_cajero()
        self.crear_permisos_vendedor()
        self.crear_permisos_jefe()
        
        self.stdout.write(self.style.SUCCESS('>> Permisos inicializados correctamente!'))

    def crear_modulo_dashboard(self):
        """Crear módulo Dashboard y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='dashboard',
            defaults={
                'nombre': 'Dashboard',
                'descripcion': 'Tableros de control y métricas',
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
        """Crear módulo Ventas y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='ventas',
            defaults={
                'nombre': 'Módulo Ventas',
                'descripcion': 'Gestión de ventas y punto de venta',
                'icono': 'ri-money-cny-circle-line',
                'orden': 2
            }
        )
        
        opciones = [
            ('ticket_venta', 'Ticket de Venta', 'ticket_venta', 'mdi-receipt', 1),
            ('cambios_devoluciones', 'Cambios y Devoluciones', 'gestion_cambios_devoluciones', 'ri-exchange-line', 2),
            ('devolucion_garantia', 'Devolucion por Garantia', 'modulo_devolucion_garantia', 'ri-refund-2-line', 3),
            ('pos_dashboard', 'Generar Venta (POS)', 'pos_dashboard', 'ri-dashboard-3-line', 4),
            ('gestion_documentos_ventas', 'Consulta Documentos', 'gestion_ventas_documentos', 'ri-file-search-line', 5),
            ('cuadratura_caja', 'Cuadratura y Arqueo', 'cuadratura_caja', 'ri-calculator-line', 6),
            ('pos_transbank', 'Mercado Pago y Transbank', 'gestion_transbank_pos_sdk', 'ri-bank-card-line', 7),
            ('revision_arqueos', 'Revisión Arqueos y Depósitos', 'revision_arqueos', 'ri-shield-check-line', 8),
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
        """Crear módulo Documentos y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='documentos',
            defaults={
                'nombre': 'Módulo Documentos',
                'descripcion': 'Gestión de documentos tributarios',
                'icono': 'ri-file-list-line',
                'orden': 3
            }
        )
        
        opciones = [
            ('emision_dte', 'Emisión DTE', None, '/app/emisionDTE/', 'bi-file-earmark-plus', 1),
            ('gestion_dte', 'Gestión DTE', None, '/app/documentos/gestion-dte/', 'bi-file-earmark-text', 2),
            ('recepcion_dte', 'Recepción Documentos', 'recepcion_dte', None, 'bi-box-arrow-in-down', 3),
            ('gestion_cotizaciones', 'Gestión Cotizaciones', 'gestion_cotizaciones', None, 'ri-file-text-line', 5),
            ('gestion_correlativos', 'Gestión Correlativos', None, '/app/documentos/gestion-correlativos/', 'ri-file-list-3-line', 6),
            ('gestion_creditos', 'Gestión Créditos', None, '/app/documentos/gestion-creditos/', 'ri-bank-card-line', 7),
            # Permiso granular: controla el botón "Descargar TXT Acepta"
            # en la pantalla Gestión DTE y el endpoint que genera el TXT.
            ('dte_descargar_txt', 'Descargar TXT Acepta de DTE', None, None, 'bi-file-earmark-text', 8),
            # Conciliación Mercado Pago (pendiente de liberación / depósitos /
            # conciliación) — solo administrador/administración (0224/0227)
            ('dineros_mercadopago', 'Conciliación Mercado Pago', None, '/app/ventas/dineros-mercadopago/', 'ri-money-dollar-circle-line', 9),
            # Permisos finos de Nota de Crédito (0233). Se exigen ADEMÁS del
            # permiso de cada pantalla: sin 'puede_crear' no se emite la NC.
            ('emitir_nota_credito', 'Emitir Nota de Crédito (clientes)', None, None, 'ri-refund-2-line', 10),
            ('emitir_nota_credito_traspaso', 'Emitir NC de traspasos internos (recepción)', None, None, 'ri-arrow-go-back-line', 11),
            # 0234: asociar cobros MP a su venta (nadie por defecto: el Maestro
            # pasa siempre) y edición/eliminación de documentos y sus pagos.
            ('asociar_pagos_mercadopago', 'Asociar pagos Mercado Pago', None, None, 'ri-links-line', 12),
            ('dte_eliminar_documento', 'Eliminar / anular documento de venta', None, None, 'ri-delete-bin-6-line', 13),
            ('dte_compras_pagos', 'Editar / eliminar pagos de documentos de compra', None, None, 'ri-bank-card-2-line', 14),
            ('dte_compras_eliminar', 'Eliminar documento de compra', None, None, 'ri-file-reduce-line', 15),
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
        """Crear módulo Existencias y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='existencias',
            defaults={
                'nombre': 'Módulo Existencias',
                'descripcion': 'Gestión de inventario y productos',
                'icono': 'ri-store-3-line',
                'orden': 4
            }
        )
        
        opciones = [
            ('gestion_producto', 'Gestión Producto', None, '/app/verGestionProducto/', 'ri-archive-line', 1),
            ('edicion_rapida_precios', 'Gestión de Precios', 'edicion_rapida_precios', None, 'ri-price-tag-3-line', 2),
            ('revisar_cambios_precios', 'Alertas de Precios', 'revisar_cambios_precios', None, 'ri-notification-badge-line', 3),
            ('movimientos_producto', 'Movimientos Por Sucursal', None, '/app/verMovimientosProducto/', 'ri-arrow-left-right-line', 4),
            ('gestion_inventarios', 'Gestión de Inventarios', 'gestion_inventarios', None, 'ri-clipboard-line', 5),
            ('gestion_etiquetas_zebra', 'Impresión Etiquetas Zebra', 'gestion_etiquetas_zebra', None, 'ri-printer-line', 6),
            ('buscar_productos_sucursal', 'Buscar Producto Sucursal', 'buscar_productos_sucursal', None, 'ri-search-line', 7),
            ('tarjeta_movimiento_producto', 'Tarjeta Movimiento Producto', 'tarjeta_movimiento_producto', None, 'ri-file-list-3-line', 8),
            ('despacho_sucursales', 'Despacho a Sucursales', 'despacho_todas_sucursales', None, 'ri-truck-line', 9),
            ('trazabilidad_producto', 'Trazabilidad Completa', 'trazabilidad_producto', None, 'ri-route-line', 10),
            ('modificacion_precios_costos', 'Modificación Precios y Costos', 'modificacion_precios_costos', None, 'ri-money-dollar-circle-line', 11),
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
        """Crear módulo Compras y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='compras',
            defaults={
                'nombre': 'Módulo Compras',
                'descripcion': 'Gestión de compras y proveedores',
                'icono': 'ri-shopping-bag-line',
                'orden': 5
            }
        )
        
        opciones = [
            ('gestion_compras', 'Gestión Compras', None, '/app/verGestionCompras/', 'ri-shopping-bag-line', 1),
            ('gestion_dte_compras', 'Gestión Documentos Compras', None, '/app/verGestionDteCompras/', 'ri-file-list-line', 2),
            ('prediccion_compras', 'Predicción de Compras', None, '/app/prediccion/', 'ri-line-chart-line', 3),
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
        """Crear módulo Requerimientos y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='requerimientos',
            defaults={
                'nombre': 'Módulo Requerimientos',
                'descripcion': 'Gestión de garantías y servicios',
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
        """Crear módulo Reportes y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='reportes',
            defaults={
                'nombre': 'Módulo Reportes',
                'descripcion': 'Reportes y análisis de datos',
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
            # Diferencias de recepcion y mercaderia en transito. Los campos
            # cantidad_faltante / cantidad_danada / cantidad_sobrante ya se
            # llenaban en cada recepcion y ningun reporte los leia.
            ('reporte_diferencias_recepcion', 'Diferencias de Recepcion', None, '/app/reportes/diferencias-recepcion/', 'ri-error-warning-line', 11),
            ('reporte_mercaderia_transito', 'Mercaderia en Transito', None, '/app/reportes/mercaderia-transito/', 'ri-truck-line', 12),
            # Auditoria Reportes 2026-08 (P1-10): estos tres reportes existian
            # en urls.py sin codigo en OpcionMenu, asi que el middleware
            # (fail-open) dejaba pasar a cualquier autenticado. Las vistas se
            # decoran con @requiere_permiso (fail-closed): este command debe
            # correrse en prod ANTES/JUNTO con el deploy o daran 403 a todos,
            # como paso el 05-ago con diferencias/transito.
            ('reporte_ventas_global', 'Ventas Global por Empresa', None, '/app/reportes/ventas-global/', 'ri-earth-line', 13),
            ('reporte_productos_origen', 'Productos por Origen', None, '/app/reportes/productos-origen/', 'ri-git-branch-line', 14),
            ('inteligencia_compra', 'Inteligencia de Compra', None, '/app/reportes/inteligencia-compra/', 'ri-lightbulb-flash-line', 15),
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
        """Crear módulo Configuración y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='configuracion',
            defaults={
                'nombre': 'Configuración',
                'descripcion': 'Configuración del sistema',
                'icono': 'ri-settings-4-line',
                'orden': 8
            }
        )
        
        opciones = [
            ('gestion_usuarios', 'Gestión Usuarios', None, '/app/gestion_usuarios/', 'bi-people-fill', 1),
            ('gestion_sucursales', 'Gestión Sucursales', None, '/app/gestion-sucursales/', 'bi-building', 2),
            ('gestion_empresas', 'Gestión Empresas', None, '/empresa_management/lista_empresas/', 'bi-building-fill', 3),
            ('gestion_clientes', 'Gestión Clientes', None, '/empresa_management/lista_clientes/', 'bi-person-badge-fill', 4),
            ('gestion_vendedores', 'Gestión Vendedores', None, '/app/gestion_vendedores/', 'bi-people', 5),
            ('gestion_permisos', 'Gestión Permisos', 'gestion_permisos', None, 'bi-shield-lock', 6),
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
        """Crear módulo Ecommerce y sus opciones"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='ecommerce',
            defaults={
                'nombre': 'Ecommerce',
                'descripcion': 'Gestión de pedidos de comercio electrónico',
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
        """Crear módulo Usuario con opciones de perfil y acciones rápidas"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='usuario',
            defaults={
                'nombre': 'Mi Cuenta',
                'descripcion': 'Opciones de perfil de usuario y acciones rápidas',
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
                'nombre': 'Fidelización',
                'descripcion': 'Gift cards y programa de puntos de clientes',
                'icono': 'ri-gift-line',
                'orden': 8
            }
        )

        opciones = [
            ('giftcards_listado', 'Gift Cards', None, '/app/giftcards/', 'ri-gift-line', 1),
            ('giftcards_emitir', 'Emitir Gift Card', None, '/app/giftcards/emitir/', 'ri-add-circle-line', 2),
            ('fidelizacion_cuentas', 'Clientes y Puntos', None, '/app/fidelizacion/', 'ri-user-star-line', 3),
            ('fidelizacion_programa', 'Configuración Programa', None, '/app/fidelizacion/configuracion/', 'ri-settings-3-line', 4),
            ('fidelizacion_reporte', 'Reporte Fidelización', None, '/app/fidelizacion/reporte/', 'ri-bar-chart-box-line', 5),
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

    def crear_modulo_liquidacion(self):
        """Crear modulo Liquidacion (Plan de Liquidacion + Campanas)"""
        modulo, created = ModuloSistema.objects.get_or_create(
            codigo='liquidacion',
            defaults={
                'nombre': 'Liquidación',
                'descripcion': 'Plan de liquidación de stock y campañas de precios/NxM',
                'icono': 'ri-scissors-cut-line',
                'orden': 11
            }
        )

        opciones = [
            ('plan_liquidacion', 'Plan de Liquidación', 'ver_plan_liquidacion', None, 'ri-scissors-cut-line', 1),
            ('campanas_liquidacion', 'Campañas de Liquidación', 'ver_campanas_liquidacion', None, 'ri-price-tag-2-line', 2),
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

        self.stdout.write('[Liquidacion] Modulo Liquidacion creado')

    def crear_permisos_administrador(self):
        """Crear permisos para el rol Administrador (acceso total)"""
        self.stdout.write('[ADMIN] Creando permisos para Administrador...')
        
        opciones = OpcionMenu.objects.exclude(codigo__in=SOLO_MAESTRO)
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
                    'puede_aprobar': True,
                }
            )

        self.stdout.write(f'   >> {opciones.count()} permisos creados para Administrador')

    def crear_permisos_administracion(self):
        """Crear permisos para el rol Administracion."""
        self.stdout.write('[ADMINISTRACION] Creando permisos para Administracion...')

        opciones = OpcionMenu.objects.exclude(codigo__in=SOLO_MAESTRO)
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
        
        # Códigos que el jefe local puede ver
        codigos_permitidos = [
            # Dashboard
            'dashboard_general', 'dashboard_ventas', 'dashboard_productos', 'dashboard_fifo',
            'dashboard_compras_estrategico', 'dashboard_documentos', 'dashboard_despachos',
            'dashboard_requerimientos',
            # Ventas
            'ticket_venta', 'cambios_devoluciones', 'devolucion_garantia', 'pos_dashboard', 'gestion_documentos_ventas',
            'cuadratura_caja', 'pos_transbank', 'revision_arqueos',
            # Documentos
            'emision_dte', 'gestion_dte', 'recepcion_dte',
            'gestion_cotizaciones', 'gestion_creditos',
            'emitir_nota_credito', 'emitir_nota_credito_traspaso',
            'dte_compras_pagos', 'dte_compras_eliminar',
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
            'reporte_rendimiento_proveedor', 'reporte_diferencias_recepcion',
            'reporte_mercaderia_transito', 'reporte_ventas_global',
            'reporte_productos_origen', 'inteligencia_compra',
            # Liquidación
            'plan_liquidacion', 'campanas_liquidacion',
            # Configuración
            'gestion_clientes', 'gestion_vendedores',
            # Fidelización (sin config del programa)
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
        
        # Códigos que el cajero puede ver
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
            # Fidelización: solo consultar gift cards (redime al cobrar) y ver puntos
            'giftcards_listado', 'fidelizacion_cuentas',
            # Mi Cuenta
            'mi_perfil', 'ajuste_stock_rapido',
        ]

        opciones = OpcionMenu.objects.filter(codigo__in=codigos_permitidos)
        for opcion in opciones:
            # Determinar permisos según la opción
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

    def crear_permisos_jefe(self):
        """Rol Jefe: como Administrador con menos permisos (política en
        app/services/perfiles_permisos.py). Solo crea filas que falten."""
        from app.services import perfiles_permisos
        self.stdout.write('[JEFE] Creando permisos para Jefe...')
        cambios = perfiles_permisos.aplicar('jefe', escribir=True, solo_faltantes=True)
        self.stdout.write(f'   >> {len(cambios)} permisos creados para Jefe')

    def crear_permisos_vendedor(self):
        """Crear permisos para el rol Vendedor"""
        self.stdout.write('[VENDEDOR] Creando permisos para Vendedor...')
        
        # Códigos que el vendedor puede ver
        codigos_permitidos = [
            # Dashboard
            'dashboard_general',
            # Ventas
            'ticket_venta', 'pos_dashboard',
            # Existencias (solo consulta)
            'buscar_productos_sucursal',
            # Requerimientos
            'lista_requerimientos', 'crear_requerimiento',
            # Fidelización: solo lectura
            'giftcards_listado', 'fidelizacion_cuentas',
            # Mi Cuenta
            'mi_perfil',
        ]

        # Fidelización es solo lectura para el vendedor (no crea gift cards)
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

