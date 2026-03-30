"""
RetailMind Assistant Tools
==========================
Clase que proporciona herramientas para consultar datos del ERP.
Cada método está diseñado para ser usado como tool por Claude.
IMPORTANTE: Todos los métodos filtran por la empresa/sucursal del usuario.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Sum, Count, Avg, Q, F
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

# Importar modelos
from app.models import (
    Empresa, Sucursal, EmpresaUser, Vendedor, Correlativo,
    Dte, Dte_Productos, Dte_Detalle_Pago, Dte_Incidencia,
    Producto, Producto_Talla, Categoria, GuiaTalla, LoteProducto,
    Ticket, Ticket_Productos, TicketDetallePago, TicketReferencia,
    Compras, Compras_Producto, Compras_Producto_Talla, Productos_Recepcionados,
    Movimientos_Producto, Traspaso, Traspaso_Detalle, AjusteInventario,
    ArqueoCaja, DepositoBancario,
    CreditoTrabajador, PagoCreditoTrabajador,
    CambioDevolucion, CambioDevolucionDetalle,
    Cotizacion, Cotizacion_Detalle,
    Requerimiento,
)
from app.models import Cliente, Proveedor


class AssistantTools:
    """
    Herramientas para el asistente conversacional de RetailMind.
    
    Esta clase proporciona métodos para consultar información del ERP
    de forma segura, filtrando siempre por la empresa del usuario autenticado.
    
    Cada método tiene un docstring descriptivo que Claude usa para entender
    cuándo y cómo usar la herramienta.
    """
    
    def __init__(self, user):
        """
        Inicializa las herramientas con el usuario autenticado.
        
        Args:
            user: Usuario de Django autenticado
        """
        self.user = user
        self.empresa = self._get_empresa_usuario()
        self.sucursal = self._get_sucursal_usuario()
        self.es_admin = user.is_superuser or getattr(user, 'rol', '') == 'administrador'
    
    def _get_empresa_usuario(self):
        """Obtiene la empresa del usuario actual"""
        try:
            empresa_user = EmpresaUser.objects.filter(user=self.user, status=True).first()
            return empresa_user.empresa if empresa_user else None
        except:
            return None
    
    def _get_sucursal_usuario(self):
        """Obtiene la sucursal del usuario actual"""
        try:
            empresa_user = EmpresaUser.objects.filter(user=self.user, status=True).first()
            return empresa_user.sucursal if empresa_user else None
        except:
            return None
    
    def _format_currency(self, valor):
        """Formatea un valor como moneda chilena"""
        if valor is None:
            return "$0"
        return f"${int(valor):,}".replace(",", ".")
    
    def _format_date(self, fecha):
        """Formatea una fecha"""
        if fecha is None:
            return "N/A"
        return fecha.strftime("%d/%m/%Y")
    
    # ==================== VENTAS ====================
    
    def get_mis_ventas(self, fecha_desde: str = None, fecha_hasta: str = None, 
                       estado: str = None, limite: int = 20) -> dict:
        """
        Obtiene las ventas (tickets) realizadas en la sucursal del usuario.
        
        Args:
            fecha_desde: Fecha inicial en formato YYYY-MM-DD (opcional, por defecto hoy)
            fecha_hasta: Fecha final en formato YYYY-MM-DD (opcional, por defecto hoy)
            estado: Filtrar por estado: PENDIENTE, PAGADO, ANULADO, DEVUELTO (opcional)
            limite: Número máximo de resultados (default 20)
        
        Returns:
            dict con lista de ventas y totales
        
        Útil para: Ver ventas del día, consultar tickets pendientes, revisar historial de ventas
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        # Parsear fechas
        hoy = timezone.now().date()
        if fecha_desde:
            try:
                fecha_desde = datetime.strptime(fecha_desde, "%Y-%m-%d").date()
            except:
                fecha_desde = hoy
        else:
            fecha_desde = hoy
            
        if fecha_hasta:
            try:
                fecha_hasta = datetime.strptime(fecha_hasta, "%Y-%m-%d").date()
            except:
                fecha_hasta = hoy
        else:
            fecha_hasta = hoy
        
        # Consultar tickets
        queryset = Ticket.objects.filter(
            sucursal=self.sucursal,
            fecha__gte=fecha_desde,
            fecha__lte=fecha_hasta
        )
        
        if estado:
            queryset = queryset.filter(estado=estado.upper())
        
        # Obtener estadísticas
        stats = queryset.aggregate(
            total_ventas=Sum('total'),
            cantidad_tickets=Count('id'),
            promedio_ticket=Avg('total')
        )
        
        # Obtener tickets
        tickets = queryset.order_by('-fecha', '-hora')[:limite]
        
        ventas_lista = []
        for t in tickets:
            ventas_lista.append({
                "correlativo": t.correlativo,
                "fecha": self._format_date(t.fecha),
                "hora": t.hora.strftime("%H:%M") if t.hora else "N/A",
                "total": self._format_currency(t.total),
                "estado": t.estado,
                "tipo_dte": t.get_tipo_dte_display() if t.tipo_dte else "Ticket",
                "metodo_pago": t.get_metodo_pago_display() if t.metodo_pago else "N/A",
                "cliente": t.cliente_nombre or "Sin nombre",
                "vendedor": t.vendedor.nombre if t.vendedor else "N/A"
            })
        
        return {
            "sucursal": self.sucursal.alias,
            "periodo": f"{self._format_date(fecha_desde)} al {self._format_date(fecha_hasta)}",
            "resumen": {
                "total_ventas": self._format_currency(stats['total_ventas'] or 0),
                "cantidad_tickets": stats['cantidad_tickets'] or 0,
                "promedio_ticket": self._format_currency(stats['promedio_ticket'] or 0)
            },
            "ventas": ventas_lista
        }
    
    def get_detalle_ticket(self, correlativo: int) -> dict:
        """
        Obtiene el detalle completo de un ticket/venta específico.
        
        Args:
            correlativo: Número de correlativo del ticket
        
        Returns:
            dict con información completa del ticket incluyendo productos
        
        Útil para: Ver detalle de una venta, revisar productos vendidos, consultar pagos
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        try:
            ticket = Ticket.objects.get(
                sucursal=self.sucursal,
                correlativo=correlativo
            )
        except Ticket.DoesNotExist:
            return {"error": f"No se encontró el ticket {correlativo} en tu sucursal"}
        
        # Obtener productos del ticket
        productos = []
        for tp in ticket.ticket_productos.all():
            prod = tp.ProductoTalla.producto
            productos.append({
                "sku": tp.ProductoTalla.sku,
                "nombre": prod.articulo,
                "talla": tp.ProductoTalla.talla,
                "cantidad": tp.stock,
                "precio_unitario": self._format_currency(tp.precio),
                "descuento": self._format_currency(tp.descuento_unitario),
                "subtotal": self._format_currency(tp.subtotal)
            })
        
        # Obtener pagos
        pagos = []
        for pago in ticket.pagos.all():
            pagos.append({
                "metodo": pago.get_metodo_pago_display(),
                "monto": self._format_currency(pago.monto),
                "voucher": pago.voucher or "N/A"
            })
        
        return {
            "ticket": {
                "correlativo": ticket.correlativo,
                "fecha": self._format_date(ticket.fecha),
                "hora": ticket.hora.strftime("%H:%M") if ticket.hora else "N/A",
                "estado": ticket.estado,
                "tipo_documento": ticket.get_tipo_dte_display() if ticket.tipo_dte else "Ticket",
                "folio_dte": ticket.folio_dte
            },
            "cliente": {
                "nombre": ticket.cliente_nombre or "Sin nombre",
                "rut": ticket.cliente_rut or "N/A",
                "email": ticket.cliente_email or "N/A",
                "telefono": ticket.cliente_telefono or "N/A"
            },
            "vendedor": ticket.vendedor.nombre if ticket.vendedor else "N/A",
            "productos": productos,
            "pagos": pagos,
            "totales": {
                "subtotal": self._format_currency(ticket.subTotal),
                "descuento": self._format_currency(ticket.descuento or 0),
                "total": self._format_currency(ticket.total),
                "total_pagado": self._format_currency(ticket.total_pagado),
                "saldo_pendiente": self._format_currency(ticket.saldo_por_pagar)
            },
            "observaciones": ticket.observaciones
        }
    
    def get_estadisticas_ventas(self, periodo: str = "hoy") -> dict:
        """
        Obtiene estadísticas de ventas para un período específico.
        
        Args:
            periodo: "hoy", "ayer", "semana", "mes", "año" (default: hoy)
        
        Returns:
            dict con métricas de ventas incluyendo totales por método de pago
        
        Útil para: Dashboard de ventas, KPIs, comparativas de rendimiento
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        hoy = timezone.now().date()
        
        # Determinar rango de fechas
        if periodo == "hoy":
            fecha_desde = hoy
            fecha_hasta = hoy
        elif periodo == "ayer":
            fecha_desde = hoy - timedelta(days=1)
            fecha_hasta = hoy - timedelta(days=1)
        elif periodo == "semana":
            fecha_desde = hoy - timedelta(days=7)
            fecha_hasta = hoy
        elif periodo == "mes":
            fecha_desde = hoy.replace(day=1)
            fecha_hasta = hoy
        elif periodo == "año":
            fecha_desde = hoy.replace(month=1, day=1)
            fecha_hasta = hoy
        else:
            fecha_desde = hoy
            fecha_hasta = hoy
        
        # Tickets en el período
        tickets = Ticket.objects.filter(
            sucursal=self.sucursal,
            fecha__gte=fecha_desde,
            fecha__lte=fecha_hasta,
            estado='PAGADO'
        )
        
        # Estadísticas generales
        stats = tickets.aggregate(
            total_ventas=Sum('total'),
            cantidad_tickets=Count('id'),
            promedio_ticket=Avg('total')
        )
        
        # Por método de pago
        pagos_stats = TicketDetallePago.objects.filter(
            ticket__sucursal=self.sucursal,
            ticket__fecha__gte=fecha_desde,
            ticket__fecha__lte=fecha_hasta,
            ticket__estado='PAGADO'
        ).values('metodo_pago').annotate(
            total=Sum('monto'),
            cantidad=Count('id')
        ).order_by('-total')
        
        metodos_pago = []
        for p in pagos_stats:
            metodos_pago.append({
                "metodo": p['metodo_pago'],
                "total": self._format_currency(p['total']),
                "transacciones": p['cantidad']
            })
        
        # Por vendedor
        vendedores_stats = tickets.values(
            'vendedor__nombre'
        ).annotate(
            total=Sum('total'),
            cantidad=Count('id')
        ).order_by('-total')[:5]
        
        top_vendedores = []
        for v in vendedores_stats:
            if v['vendedor__nombre']:
                top_vendedores.append({
                    "vendedor": v['vendedor__nombre'],
                    "total": self._format_currency(v['total']),
                    "tickets": v['cantidad']
                })
        
        return {
            "sucursal": self.sucursal.alias,
            "periodo": periodo,
            "fechas": f"{self._format_date(fecha_desde)} al {self._format_date(fecha_hasta)}",
            "resumen": {
                "total_ventas": self._format_currency(stats['total_ventas'] or 0),
                "cantidad_tickets": stats['cantidad_tickets'] or 0,
                "promedio_ticket": self._format_currency(stats['promedio_ticket'] or 0)
            },
            "por_metodo_pago": metodos_pago,
            "top_vendedores": top_vendedores
        }
    
    def get_productos_mas_vendidos(self, dias: int = 30, limite: int = 10) -> dict:
        """
        Obtiene los productos más vendidos en un período.
        
        Args:
            dias: Número de días hacia atrás para el análisis (default 30)
            limite: Número máximo de productos a mostrar (default 10)
        
        Returns:
            dict con lista de productos más vendidos con cantidades y montos
        
        Útil para: Análisis de ventas, productos estrella, reposición de stock
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        fecha_desde = timezone.now().date() - timedelta(days=dias)
        
        # Productos más vendidos
        productos = Ticket_Productos.objects.filter(
            idTicket__sucursal=self.sucursal,
            idTicket__fecha__gte=fecha_desde,
            idTicket__estado='PAGADO'
        ).values(
            'ProductoTalla__producto__articulo',
            'ProductoTalla__producto__atributo1__valor',
            'ProductoTalla__producto__categoria__nombre'
        ).annotate(
            cantidad_vendida=Sum('stock'),
            total_vendido=Sum('subtotal')
        ).order_by('-cantidad_vendida')[:limite]
        
        lista_productos = []
        for p in productos:
            lista_productos.append({
                "producto": p['ProductoTalla__producto__articulo'],
                "marca": p['ProductoTalla__producto__atributo1__valor'] or "Sin marca",
                "categoria": p['ProductoTalla__producto__categoria__nombre'] or "Sin categoría",
                "unidades_vendidas": p['cantidad_vendida'],
                "total_vendido": self._format_currency(p['total_vendido'])
            })
        
        return {
            "sucursal": self.sucursal.alias,
            "periodo": f"Últimos {dias} días",
            "productos_mas_vendidos": lista_productos
        }
    
    # ==================== INVENTARIO ====================
    
    def get_productos_stock_bajo(self, minimo: int = 5, limite: int = 20) -> dict:
        """
        Obtiene productos con stock bajo o agotado.
        
        Args:
            minimo: Umbral de stock bajo (default 5)
            limite: Número máximo de resultados (default 20)
        
        Returns:
            dict con lista de productos con stock bajo
        
        Útil para: Alertas de reposición, gestión de inventario, compras urgentes
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        # Productos con stock bajo
        productos = Producto_Talla.objects.filter(
            producto__sucursal=self.sucursal,
            stock__lte=minimo
        ).select_related('producto', 'producto__atributo1').order_by('stock')[:limite]
        
        lista = []
        agotados = 0
        criticos = 0
        
        for pt in productos:
            estado = "AGOTADO" if pt.stock <= 0 else "CRÍTICO" if pt.stock <= 2 else "BAJO"
            if pt.stock <= 0:
                agotados += 1
            elif pt.stock <= 2:
                criticos += 1
            
            lista.append({
                "sku": pt.sku,
                "producto": pt.producto.articulo,
                "marca": pt.producto.atributo1.valor if pt.producto.atributo1 else "N/A",
                "talla": pt.talla,
                "stock_actual": pt.stock,
                "precio_venta": self._format_currency(pt.producto.precioventa),
                "estado": estado
            })
        
        return {
            "sucursal": self.sucursal.alias,
            "umbral_stock_bajo": minimo,
            "resumen": {
                "total_productos_bajo": len(lista),
                "agotados": agotados,
                "criticos": criticos
            },
            "productos": lista
        }
    
    def buscar_producto(self, query: str, limite: int = 10) -> dict:
        """
        Busca productos por nombre, SKU o código.
        
        Args:
            query: Texto a buscar (nombre, SKU, código)
            limite: Número máximo de resultados (default 10)
        
        Returns:
            dict con lista de productos encontrados
        
        Útil para: Buscar productos, verificar existencias, consultar precios
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        # Buscar en productos
        productos = Producto_Talla.objects.filter(
            producto__sucursal=self.sucursal
        ).filter(
            Q(producto__articulo__icontains=query) |
            Q(producto__descripcion__icontains=query) |
            Q(sku__icontains=query) |
            Q(talla__icontains=query)
        ).select_related(
            'producto', 'producto__atributo1', 'producto__atributo2', 'producto__categoria'
        )[:limite]
        
        lista = []
        for pt in productos:
            p = pt.producto
            lista.append({
                "sku": pt.sku,
                "nombre": p.articulo,
                "descripcion": p.descripcion,
                "marca": p.atributo1.valor if p.atributo1 else "N/A",
                "color": p.atributo2.valor if p.atributo2 else "N/A",
                "categoria": p.categoria.nombre if p.categoria else "N/A",
                "talla": pt.talla,
                "stock": pt.stock,
                "costo": self._format_currency(p.costo),
                "precio_venta": self._format_currency(p.precioventa),
                "margen": f"{((p.precioventa - p.costo) / p.costo * 100):.1f}%" if p.costo > 0 else "N/A"
            })
        
        return {
            "busqueda": query,
            "resultados_encontrados": len(lista),
            "productos": lista
        }
    
    def get_existencias_producto(self, sku: int) -> dict:
        """
        Obtiene las existencias detalladas de un producto por SKU.
        
        Args:
            sku: SKU del producto a consultar
        
        Returns:
            dict con información de existencias y lotes
        
        Útil para: Verificar stock disponible, consultar lotes FIFO, trazabilidad
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        try:
            pt = Producto_Talla.objects.select_related(
                'producto', 'producto__atributo1', 'producto__categoria'
            ).get(sku=sku, producto__sucursal=self.sucursal)
        except Producto_Talla.DoesNotExist:
            return {"error": f"No se encontró el SKU {sku} en tu sucursal"}
        
        # Obtener lotes FIFO
        lotes = LoteProducto.objects.filter(
            producto_talla=pt,
            activo=True,
            agotado=False
        ).order_by('fecha_ingreso')
        
        lotes_lista = []
        for lote in lotes:
            lotes_lista.append({
                "id_lote": lote.id,
                "cantidad_disponible": lote.cantidad_disponible,
                "costo_unitario": self._format_currency(lote.costo_unitario),
                "fecha_ingreso": self._format_date(lote.fecha_ingreso.date()) if lote.fecha_ingreso else "N/A",
                "numero_lote": lote.numero_lote or "N/A"
            })
        
        return {
            "producto": {
                "sku": pt.sku,
                "nombre": pt.producto.articulo,
                "marca": pt.producto.atributo1.valor if pt.producto.atributo1 else "N/A",
                "categoria": pt.producto.categoria.nombre if pt.producto.categoria else "N/A",
                "talla": pt.talla
            },
            "existencias": {
                "stock_actual": pt.stock,
                "costo_actual": self._format_currency(pt.producto.costo),
                "precio_venta": self._format_currency(pt.producto.precioventa)
            },
            "lotes_fifo": lotes_lista
        }
    
    def get_movimientos_kardex(self, sku: int = None, dias: int = 30, limite: int = 50) -> dict:
        """
        Obtiene los movimientos de inventario (kardex) de productos.
        
        Args:
            sku: SKU específico (opcional, si no se indica muestra todos)
            dias: Número de días hacia atrás (default 30)
            limite: Número máximo de resultados (default 50)
        
        Returns:
            dict con lista de movimientos de inventario
        
        Útil para: Auditoría de inventario, trazabilidad, análisis de rotación
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        fecha_desde = timezone.now().date() - timedelta(days=dias)
        
        queryset = Movimientos_Producto.objects.filter(
            fecha__gte=fecha_desde
        ).filter(
            Q(sucursal_origen=self.sucursal) | Q(sucursal_destino=self.sucursal)
        )
        
        if sku:
            queryset = queryset.filter(ProductoTalla__sku=sku)
        
        movimientos = queryset.select_related(
            'ProductoTalla', 'ProductoTalla__producto'
        ).order_by('-fecha', '-hora')[:limite]
        
        lista = []
        for m in movimientos:
            lista.append({
                "fecha": self._format_date(m.fecha),
                "hora": m.hora.strftime("%H:%M") if m.hora else "N/A",
                "sku": m.ProductoTalla.sku,
                "producto": m.ProductoTalla.producto.articulo,
                "tipo": m.tipo_movimiento,
                "concepto": m.get_concepto_display() if hasattr(m, 'get_concepto_display') else m.concepto,
                "cantidad": m.cantidad,
                "responsable": m.responsable,
                "estado": m.estado
            })
        
        return {
            "sucursal": self.sucursal.alias,
            "periodo": f"Últimos {dias} días",
            "total_movimientos": len(lista),
            "movimientos": lista
        }
    
    # ==================== COMPRAS Y DTEs ====================
    
    def get_dtes_pendientes(self, tipo: str = None, limite: int = 20) -> dict:
        """
        Obtiene los DTEs (facturas, boletas) pendientes de pago.
        
        Args:
            tipo: Tipo de documento (FACTURA ELECTRONICA, BOLETA, etc.) opcional
            limite: Número máximo de resultados (default 20)
        
        Returns:
            dict con lista de DTEs pendientes y montos
        
        Útil para: Gestión de cuentas por pagar, flujo de caja, pagos pendientes
        """
        if not self.empresa:
            return {"error": "No tienes una empresa asignada"}
        
        queryset = Dte.objects.filter(
            receptor=self.empresa,
            estado_pago='PENDIENTE'
        )
        
        if tipo:
            queryset = queryset.filter(tipo_documento=tipo.upper())
        
        # Estadísticas
        stats = queryset.aggregate(
            total_pendiente=Sum('monto_con_iva'),
            cantidad=Count('id')
        )
        
        # DTEs
        dtes = queryset.select_related('emisor').order_by('fecha_vencimiento')[:limite]
        
        lista = []
        vencidos = 0
        por_vencer = 0
        hoy = timezone.now().date()
        
        for dte in dtes:
            dias_vencimiento = (dte.fecha_vencimiento - hoy).days if dte.fecha_vencimiento else 0
            estado = "VENCIDO" if dias_vencimiento < 0 else "POR VENCER" if dias_vencimiento <= 7 else "AL DÍA"
            
            if dias_vencimiento < 0:
                vencidos += 1
            elif dias_vencimiento <= 7:
                por_vencer += 1
            
            lista.append({
                "numero": dte.numero_documento,
                "tipo": dte.tipo_documento,
                "proveedor": dte.emisor.nombre if dte.emisor else "N/A",
                "fecha_emision": self._format_date(dte.fecha_emision),
                "fecha_vencimiento": self._format_date(dte.fecha_vencimiento),
                "monto": self._format_currency(dte.monto_con_iva),
                "dias_vencimiento": dias_vencimiento,
                "estado": estado
            })
        
        return {
            "empresa": self.empresa.nombre,
            "resumen": {
                "total_pendiente": self._format_currency(stats['total_pendiente'] or 0),
                "cantidad_documentos": stats['cantidad'] or 0,
                "vencidos": vencidos,
                "por_vencer_7_dias": por_vencer
            },
            "documentos": lista
        }
    
    def get_dtes_vencidos(self) -> dict:
        """
        Obtiene los DTEs vencidos sin pagar.
        
        Returns:
            dict con lista de DTEs vencidos y montos
        
        Útil para: Alertas de pagos, cuentas por pagar urgentes
        """
        if not self.empresa:
            return {"error": "No tienes una empresa asignada"}
        
        hoy = timezone.now().date()
        
        dtes = Dte.objects.filter(
            receptor=self.empresa,
            estado_pago='PENDIENTE',
            fecha_vencimiento__lt=hoy
        ).select_related('emisor').order_by('fecha_vencimiento')
        
        stats = dtes.aggregate(
            total_vencido=Sum('monto_con_iva'),
            cantidad=Count('id')
        )
        
        lista = []
        for dte in dtes[:20]:
            dias_atraso = (hoy - dte.fecha_vencimiento).days
            lista.append({
                "numero": dte.numero_documento,
                "tipo": dte.tipo_documento,
                "proveedor": dte.emisor.nombre if dte.emisor else "N/A",
                "fecha_vencimiento": self._format_date(dte.fecha_vencimiento),
                "dias_atraso": dias_atraso,
                "monto": self._format_currency(dte.monto_con_iva)
            })
        
        return {
            "alerta": "⚠️ DOCUMENTOS VENCIDOS",
            "empresa": self.empresa.nombre,
            "resumen": {
                "total_vencido": self._format_currency(stats['total_vencido'] or 0),
                "cantidad_vencidos": stats['cantidad'] or 0
            },
            "documentos_vencidos": lista
        }
    
    def get_detalle_dte(self, numero_documento: int) -> dict:
        """
        Obtiene el detalle completo de un DTE (factura, boleta, etc.).
        
        Args:
            numero_documento: Número del documento
        
        Returns:
            dict con información completa del DTE
        
        Útil para: Ver detalle de factura, productos recibidos, pagos realizados
        """
        if not self.empresa:
            return {"error": "No tienes una empresa asignada"}
        
        try:
            dte = Dte.objects.select_related('emisor', 'receptor').get(
                numero_documento=numero_documento,
                receptor=self.empresa
            )
        except Dte.DoesNotExist:
            return {"error": f"No se encontró el documento {numero_documento}"}
        
        # Productos
        productos = []
        for dp in dte.dte_productos.all():
            productos.append({
                "sku": dp.productoTalla.sku if dp.productoTalla else "N/A",
                "descripcion": dp.descripcion,
                "cantidad": dp.stock,
                "costo": self._format_currency(dp.costo),
                "precio": self._format_currency(dp.precio)
            })
        
        # Pagos
        pagos = []
        for pago in dte.dte_asociado.all():
            pagos.append({
                "metodo": pago.metodo_pago,
                "monto": self._format_currency(pago.monto),
                "notas": pago.notas
            })
        
        return {
            "documento": {
                "numero": dte.numero_documento,
                "tipo": dte.tipo_documento,
                "estado_pago": dte.estado_pago,
                "estado_dte": dte.estado_dte
            },
            "emisor": {
                "nombre": dte.emisor.nombre if dte.emisor else "N/A",
                "rut": dte.emisor.rut if dte.emisor else "N/A"
            },
            "fechas": {
                "emision": self._format_date(dte.fecha_emision),
                "vencimiento": self._format_date(dte.fecha_vencimiento),
                "recepcion": self._format_date(dte.fecha_recepcion)
            },
            "montos": {
                "neto": self._format_currency(dte.monto_neto),
                "con_iva": self._format_currency(dte.monto_con_iva),
                "descuento": self._format_currency(dte.descuento)
            },
            "productos": productos,
            "pagos": pagos,
            "referencias": dte.referencias
        }
    
    def get_compras_temporada(self, anio: int = None) -> dict:
        """
        Obtiene el resumen de compras por temporada.
        
        Args:
            anio: Año a consultar (default: año actual)
        
        Returns:
            dict con lista de compras y totales por temporada
        
        Útil para: Análisis de compras, planificación de temporadas
        """
        if not self.empresa:
            return {"error": "No tienes una empresa asignada"}
        
        if not anio:
            anio = timezone.now().year
        
        compras = Compras.objects.filter(
            empresa=self.empresa,
            fecha__year=anio
        ).order_by('-fecha')
        
        lista = []
        for c in compras[:20]:
            productos_count = Compras_Producto.objects.filter(compras=c).count()
            lista.append({
                "correlativo": c.correlativo,
                "nombre": c.nombre,
                "temporada": c.temporada,
                "fecha": self._format_date(c.fecha),
                "productos": productos_count,
                "responsable": c.responsable
            })
        
        return {
            "empresa": self.empresa.nombre,
            "año": anio,
            "total_compras": compras.count(),
            "compras": lista
        }
    
    # ==================== CAJA Y ARQUEOS ====================
    
    def get_cuadratura_dia(self, fecha: str = None) -> dict:
        """
        Obtiene la cuadratura/arqueo de caja de un día específico.
        
        Args:
            fecha: Fecha en formato YYYY-MM-DD (default: hoy)
        
        Returns:
            dict con información de cuadratura del día
        
        Útil para: Cierre de caja, verificar diferencias, estado del arqueo
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        if fecha:
            try:
                fecha_consulta = datetime.strptime(fecha, "%Y-%m-%d").date()
            except:
                fecha_consulta = timezone.now().date()
        else:
            fecha_consulta = timezone.now().date()
        
        try:
            arqueo = ArqueoCaja.objects.get(
                sucursal=self.sucursal,
                fecha_arqueo=fecha_consulta
            )
        except ArqueoCaja.DoesNotExist:
            # No hay arqueo, calcular datos teóricos
            tickets = Ticket.objects.filter(
                sucursal=self.sucursal,
                fecha=fecha_consulta,
                estado='PAGADO'
            )
            
            total_ventas = tickets.aggregate(total=Sum('total'))['total'] or 0
            
            return {
                "fecha": self._format_date(fecha_consulta),
                "sucursal": self.sucursal.alias,
                "estado": "SIN ARQUEO",
                "mensaje": "No se ha realizado el arqueo de caja para este día",
                "ventas_teoricas": self._format_currency(total_ventas),
                "cantidad_tickets": tickets.count()
            }
        
        return {
            "fecha": self._format_date(arqueo.fecha_arqueo),
            "sucursal": self.sucursal.alias,
            "estado": arqueo.get_estado_display(),
            "responsable": arqueo.usuario_responsable.get_full_name() if arqueo.usuario_responsable else "N/A",
            "ventas": {
                "total_efectivo_teorico": self._format_currency(arqueo.total_efectivo_teorico),
                "total_efectivo_fisico": self._format_currency(arqueo.total_efectivo_fisico),
                "total_transbank_teorico": self._format_currency(arqueo.total_transbank_teorico),
                "total_tarjetas_comerciales": self._format_currency(arqueo.total_tarjetas_comerciales_teorico),
                "venta_total_teorica": self._format_currency(arqueo.venta_total_teorica)
            },
            "diferencias": {
                "diferencia_efectivo": self._format_currency(arqueo.diferencia_efectivo),
                "diferencia_transbank": self._format_currency(arqueo.diferencia_transbank),
                "tipo_diferencia": arqueo.tipo_diferencia,
                "requiere_supervision": arqueo.requiere_supervision
            },
            "documentos": {
                "tickets": arqueo.cantidad_tickets,
                "boletas_electronicas": arqueo.cantidad_boletas_electronicas,
                "facturas": arqueo.cantidad_facturas
            },
            "observaciones": arqueo.observaciones
        }
    
    def get_arqueos_pendientes(self) -> dict:
        """
        Obtiene los arqueos de caja pendientes o con diferencias.
        
        Returns:
            dict con lista de arqueos que requieren atención
        
        Útil para: Supervisión, alertas de diferencias de caja
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        arqueos = ArqueoCaja.objects.filter(
            sucursal=self.sucursal,
            estado__in=['ABIERTO', 'CON_DIFERENCIAS']
        ).order_by('-fecha_arqueo')[:10]
        
        lista = []
        for a in arqueos:
            lista.append({
                "fecha": self._format_date(a.fecha_arqueo),
                "estado": a.get_estado_display(),
                "diferencia_efectivo": self._format_currency(a.diferencia_efectivo),
                "tipo_diferencia": a.tipo_diferencia,
                "requiere_supervision": a.requiere_supervision
            })
        
        return {
            "sucursal": self.sucursal.alias,
            "arqueos_pendientes": len(lista),
            "arqueos": lista
        }
    
    # ==================== CLIENTES ====================
    
    def buscar_cliente(self, query: str) -> dict:
        """
        Busca clientes por nombre, RUT o email.
        
        Args:
            query: Texto a buscar (nombre, RUT, email)
        
        Returns:
            dict con lista de clientes encontrados
        
        Útil para: Buscar clientes, datos para facturación, historial
        """
        clientes = Cliente.objects.filter(
            Q(nombre__icontains=query) |
            Q(apellido__icontains=query) |
            Q(rut__icontains=query) |
            Q(email__icontains=query)
        ).filter(activo=True)[:10]
        
        lista = []
        for c in clientes:
            lista.append({
                "id": c.id,
                "nombre": c.nombre_completo,
                "rut": c.rut or "N/A",
                "email": c.email or "N/A",
                "telefono": c.telefono or "N/A",
                "tipo": c.get_tipo_cliente_display()
            })
        
        return {
            "busqueda": query,
            "resultados": len(lista),
            "clientes": lista
        }
    
    def get_historial_cliente(self, rut: str) -> dict:
        """
        Obtiene el historial de compras de un cliente.
        
        Args:
            rut: RUT del cliente
        
        Returns:
            dict con historial de compras y estadísticas
        
        Útil para: Ver compras de un cliente, fidelización, análisis
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        # Buscar cliente
        cliente = Cliente.objects.filter(rut__icontains=rut).first()
        
        # Buscar tickets del cliente
        tickets = Ticket.objects.filter(
            sucursal=self.sucursal,
            cliente_rut__icontains=rut
        ).order_by('-fecha')[:20]
        
        stats = tickets.aggregate(
            total_compras=Sum('total'),
            cantidad_compras=Count('id'),
            promedio_compra=Avg('total')
        )
        
        compras = []
        for t in tickets:
            compras.append({
                "correlativo": t.correlativo,
                "fecha": self._format_date(t.fecha),
                "total": self._format_currency(t.total),
                "productos": t.ticket_productos.count()
            })
        
        return {
            "cliente": {
                "rut": rut,
                "nombre": cliente.nombre_completo if cliente else "N/A",
                "email": cliente.email if cliente else "N/A"
            },
            "estadisticas": {
                "total_compras": self._format_currency(stats['total_compras'] or 0),
                "cantidad_compras": stats['cantidad_compras'] or 0,
                "promedio_compra": self._format_currency(stats['promedio_compra'] or 0)
            },
            "ultimas_compras": compras
        }
    
    # ==================== PROVEEDORES ====================
    
    def get_proveedores(self, limite: int = 20) -> dict:
        """
        Obtiene la lista de proveedores activos.
        
        Args:
            limite: Número máximo de resultados (default 20)
        
        Returns:
            dict con lista de proveedores
        
        Útil para: Consultar proveedores, datos de contacto, compras
        """
        if not self.empresa:
            return {"error": "No tienes una empresa asignada"}
        
        # Obtener empresas que son proveedores
        proveedores = Empresa.objects.filter(
            esProveedor=True
        )[:limite]
        
        lista = []
        for p in proveedores:
            lista.append({
                "nombre": p.nombre,
                "rut": p.rut,
                "razon_social": p.razon_social,
                "giro": p.giro,
                "email": p.correoVendedor or "N/A",
                "direccion": p.direccion
            })
        
        return {
            "total_proveedores": proveedores.count(),
            "proveedores": lista
        }
    
    def get_facturas_proveedor(self, rut_proveedor: str, estado: str = None) -> dict:
        """
        Obtiene las facturas de un proveedor específico.
        
        Args:
            rut_proveedor: RUT del proveedor
            estado: Estado del pago (PENDIENTE, PAGADO, VENCIDO) opcional
        
        Returns:
            dict con facturas del proveedor
        
        Útil para: Estado de cuenta con proveedor, facturas pendientes
        """
        if not self.empresa:
            return {"error": "No tienes una empresa asignada"}
        
        proveedor = Empresa.objects.filter(rut__icontains=rut_proveedor).first()
        if not proveedor:
            return {"error": f"No se encontró proveedor con RUT {rut_proveedor}"}
        
        queryset = Dte.objects.filter(
            emisor=proveedor,
            receptor=self.empresa
        )
        
        if estado:
            queryset = queryset.filter(estado_pago=estado.upper())
        
        stats = queryset.aggregate(
            total=Sum('monto_con_iva'),
            cantidad=Count('id')
        )
        
        facturas = []
        for dte in queryset.order_by('-fecha_emision')[:20]:
            facturas.append({
                "numero": dte.numero_documento,
                "tipo": dte.tipo_documento,
                "fecha_emision": self._format_date(dte.fecha_emision),
                "fecha_vencimiento": self._format_date(dte.fecha_vencimiento),
                "monto": self._format_currency(dte.monto_con_iva),
                "estado": dte.estado_pago
            })
        
        return {
            "proveedor": {
                "nombre": proveedor.nombre,
                "rut": proveedor.rut
            },
            "resumen": {
                "total": self._format_currency(stats['total'] or 0),
                "cantidad_documentos": stats['cantidad'] or 0
            },
            "facturas": facturas
        }
    
    # ==================== CRÉDITOS TRABAJADORES ====================
    
    def get_creditos_trabajadores(self, estado: str = None) -> dict:
        """
        Obtiene los créditos otorgados a trabajadores.
        
        Args:
            estado: Estado del crédito (PENDIENTE, ACTIVO, PAGADO, etc.) opcional
        
        Returns:
            dict con lista de créditos
        
        Útil para: Gestión de créditos, seguimiento de pagos
        """
        if not self.empresa:
            return {"error": "No tienes una empresa asignada"}
        
        queryset = CreditoTrabajador.objects.filter(
            empresa_origen=self.empresa
        )
        
        if estado:
            queryset = queryset.filter(estado=estado.upper())
        
        stats = queryset.aggregate(
            total_otorgado=Sum('monto_aprobado'),
            total_pagado=Sum('monto_pagado'),
            cantidad=Count('id')
        )
        
        creditos = []
        for c in queryset.select_related('beneficiario').order_by('-fecha_solicitud')[:20]:
            creditos.append({
                "numero": c.numero_credito,
                "trabajador": c.nombre_beneficiario,
                "tipo": c.get_tipo_credito_display(),
                "monto_aprobado": self._format_currency(c.monto_aprobado or 0),
                "monto_pagado": self._format_currency(c.monto_pagado),
                "saldo_pendiente": self._format_currency(c.saldo_pendiente),
                "estado": c.get_estado_display(),
                "fecha_vencimiento": self._format_date(c.fecha_vencimiento)
            })
        
        return {
            "empresa": self.empresa.nombre,
            "resumen": {
                "total_otorgado": self._format_currency(stats['total_otorgado'] or 0),
                "total_pagado": self._format_currency(stats['total_pagado'] or 0),
                "cantidad_creditos": stats['cantidad'] or 0
            },
            "creditos": creditos
        }
    
    # ==================== CAMBIOS Y DEVOLUCIONES ====================
    
    def get_cambios_devoluciones(self, estado: str = None, dias: int = 30) -> dict:
        """
        Obtiene los cambios y devoluciones recientes.
        
        Args:
            estado: Estado del cambio (PENDIENTE, APROBADO, COMPLETADO, etc.) opcional
            dias: Número de días hacia atrás (default 30)
        
        Returns:
            dict con lista de cambios y devoluciones
        
        Útil para: Gestión de cambios, seguimiento de devoluciones
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        fecha_desde = timezone.now().date() - timedelta(days=dias)
        
        queryset = CambioDevolucion.objects.filter(
            sucursal=self.sucursal,
            fecha_solicitud__gte=fecha_desde
        )
        
        if estado:
            queryset = queryset.filter(estado=estado.upper())
        
        stats = queryset.aggregate(
            total_monto=Sum('monto_devolucion'),
            cantidad=Count('id')
        )
        
        cambios = []
        for c in queryset.select_related('ticket_original').order_by('-fecha_solicitud')[:20]:
            cambios.append({
                "numero": c.numero_cambio,
                "tipo": c.get_tipo_cambio_display() if hasattr(c, 'get_tipo_cambio_display') else c.tipo_cambio,
                "ticket_original": c.ticket_original.correlativo if c.ticket_original else "N/A",
                "fecha": self._format_date(c.fecha_solicitud.date()) if c.fecha_solicitud else "N/A",
                "monto": self._format_currency(c.monto_devolucion or 0),
                "estado": c.get_estado_display() if hasattr(c, 'get_estado_display') else c.estado,
                "motivo": c.motivo_cambio[:50] + "..." if c.motivo_cambio and len(c.motivo_cambio) > 50 else c.motivo_cambio
            })
        
        return {
            "sucursal": self.sucursal.alias,
            "periodo": f"Últimos {dias} días",
            "resumen": {
                "total_monto": self._format_currency(stats['total_monto'] or 0),
                "cantidad": stats['cantidad'] or 0
            },
            "cambios_devoluciones": cambios
        }
    
    # ==================== REQUERIMIENTOS ====================
    
    def get_requerimientos_garantia(self, estado: str = None, limite: int = 20) -> dict:
        """
        Obtiene los requerimientos de garantía y reclamos.
        
        Args:
            estado: Estado del requerimiento (PENDIENTE, EN_PROCESO, etc.) opcional
            limite: Número máximo de resultados (default 20)
        
        Returns:
            dict con lista de requerimientos
        
        Útil para: Gestión de garantías, seguimiento de reclamos
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        queryset = Requerimiento.objects.filter(
            sucursal=self.sucursal
        )
        
        if estado:
            queryset = queryset.filter(estado=estado.upper())
        
        stats = queryset.aggregate(cantidad=Count('id'))
        
        requerimientos = []
        for r in queryset.order_by('-fecha_creacion')[:limite]:
            requerimientos.append({
                "numero": r.numero_requerimiento,
                "tipo": r.get_tipo_display() if hasattr(r, 'get_tipo_display') else r.tipo,
                "estado": r.get_estado_display() if hasattr(r, 'get_estado_display') else r.estado,
                "fecha": self._format_date(r.fecha_creacion.date()) if r.fecha_creacion else "N/A",
                "cliente": r.cliente_nombre if hasattr(r, 'cliente_nombre') else "N/A",
                "descripcion": r.descripcion[:50] + "..." if hasattr(r, 'descripcion') and r.descripcion and len(r.descripcion) > 50 else getattr(r, 'descripcion', 'N/A')
            })
        
        return {
            "sucursal": self.sucursal.alias,
            "total_requerimientos": stats['cantidad'] or 0,
            "requerimientos": requerimientos
        }
    
    # ==================== REPORTES ====================
    
    def get_resumen_diario(self, fecha: str = None) -> dict:
        """
        Obtiene un resumen ejecutivo del día.
        
        Args:
            fecha: Fecha en formato YYYY-MM-DD (default: hoy)
        
        Returns:
            dict con resumen completo del día incluyendo ventas, inventario y alertas
        
        Útil para: Dashboard diario, KPIs, resumen ejecutivo
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        if fecha:
            try:
                fecha_consulta = datetime.strptime(fecha, "%Y-%m-%d").date()
            except:
                fecha_consulta = timezone.now().date()
        else:
            fecha_consulta = timezone.now().date()
        
        # Ventas del día
        tickets = Ticket.objects.filter(
            sucursal=self.sucursal,
            fecha=fecha_consulta,
            estado='PAGADO'
        )
        
        ventas_stats = tickets.aggregate(
            total=Sum('total'),
            cantidad=Count('id'),
            promedio=Avg('total')
        )
        
        # Productos vendidos
        productos_vendidos = Ticket_Productos.objects.filter(
            idTicket__sucursal=self.sucursal,
            idTicket__fecha=fecha_consulta,
            idTicket__estado='PAGADO'
        ).aggregate(total_unidades=Sum('stock'))
        
        # Stock bajo
        stock_bajo = Producto_Talla.objects.filter(
            producto__sucursal=self.sucursal,
            stock__lte=5
        ).count()
        
        # DTEs vencidos
        dtes_vencidos = 0
        if self.empresa:
            dtes_vencidos = Dte.objects.filter(
                receptor=self.empresa,
                estado_pago='PENDIENTE',
                fecha_vencimiento__lt=fecha_consulta
            ).count()
        
        # Cambios/devoluciones pendientes
        cambios_pendientes = CambioDevolucion.objects.filter(
            sucursal=self.sucursal,
            estado__in=['PENDIENTE', 'APROBADO']
        ).count()
        
        return {
            "fecha": self._format_date(fecha_consulta),
            "sucursal": self.sucursal.alias,
            "ventas": {
                "total": self._format_currency(ventas_stats['total'] or 0),
                "cantidad_tickets": ventas_stats['cantidad'] or 0,
                "ticket_promedio": self._format_currency(ventas_stats['promedio'] or 0),
                "unidades_vendidas": productos_vendidos['total_unidades'] or 0
            },
            "alertas": {
                "productos_stock_bajo": stock_bajo,
                "dtes_vencidos": dtes_vencidos,
                "cambios_pendientes": cambios_pendientes
            }
        }
    
    def get_comparativa_ventas(self, dias: int = 7) -> dict:
        """
        Obtiene una comparativa de ventas de los últimos días.
        
        Args:
            dias: Número de días a comparar (default 7)
        
        Returns:
            dict con ventas por día y tendencias
        
        Útil para: Análisis de tendencias, comparativas de rendimiento
        """
        if not self.sucursal:
            return {"error": "No tienes una sucursal asignada"}
        
        hoy = timezone.now().date()
        fecha_desde = hoy - timedelta(days=dias-1)
        
        # Ventas por día
        ventas_dia = Ticket.objects.filter(
            sucursal=self.sucursal,
            fecha__gte=fecha_desde,
            fecha__lte=hoy,
            estado='PAGADO'
        ).values('fecha').annotate(
            total=Sum('total'),
            cantidad=Count('id')
        ).order_by('fecha')
        
        comparativa = []
        total_periodo = 0
        for v in ventas_dia:
            total_periodo += v['total'] or 0
            comparativa.append({
                "fecha": self._format_date(v['fecha']),
                "dia_semana": v['fecha'].strftime("%A"),
                "total": self._format_currency(v['total']),
                "tickets": v['cantidad']
            })
        
        promedio_diario = total_periodo / dias if dias > 0 else 0
        
        return {
            "sucursal": self.sucursal.alias,
            "periodo": f"Últimos {dias} días",
            "resumen": {
                "total_periodo": self._format_currency(total_periodo),
                "promedio_diario": self._format_currency(promedio_diario)
            },
            "ventas_por_dia": comparativa
        }
    
    def get_info_usuario(self) -> dict:
        """
        Obtiene información del usuario actual y su contexto.
        
        Returns:
            dict con información del usuario, empresa y sucursal
        
        Útil para: Verificar permisos, contexto del usuario
        """
        return {
            "usuario": {
                "nombre": self.user.get_full_name(),
                "username": self.user.username,
                "email": self.user.email,
                "rol": getattr(self.user, 'rol', 'N/A'),
                "es_admin": self.es_admin
            },
            "empresa": {
                "nombre": self.empresa.nombre if self.empresa else "Sin empresa",
                "rut": self.empresa.rut if self.empresa else "N/A"
            },
            "sucursal": {
                "alias": self.sucursal.alias if self.sucursal else "Sin sucursal",
                "direccion": self.sucursal.direccion if self.sucursal else "N/A"
            }
        }
    
    # ==================== MÉTODO PARA OBTENER DEFINICIONES DE TOOLS ====================
    
    @classmethod
    def get_tools_definitions(cls):
        """
        Retorna las definiciones de tools en formato Claude.
        Cada método público se convierte en una tool.
        """
        import inspect
        
        tools = []
        
        # Métodos a exponer como tools
        tool_methods = [
            # Ventas
            ('get_mis_ventas', 'Obtiene las ventas/tickets de la sucursal'),
            ('get_detalle_ticket', 'Obtiene el detalle de un ticket específico'),
            ('get_estadisticas_ventas', 'Obtiene estadísticas de ventas por período'),
            ('get_productos_mas_vendidos', 'Obtiene productos más vendidos'),
            # Inventario
            ('get_productos_stock_bajo', 'Obtiene productos con stock bajo'),
            ('buscar_producto', 'Busca productos por nombre, SKU o código'),
            ('get_existencias_producto', 'Obtiene existencias de un producto'),
            ('get_movimientos_kardex', 'Obtiene movimientos de inventario'),
            # Compras/DTEs
            ('get_dtes_pendientes', 'Obtiene facturas pendientes de pago'),
            ('get_dtes_vencidos', 'Obtiene facturas vencidas'),
            ('get_detalle_dte', 'Obtiene detalle de una factura'),
            ('get_compras_temporada', 'Obtiene compras por temporada'),
            # Caja
            ('get_cuadratura_dia', 'Obtiene la cuadratura de caja del día'),
            ('get_arqueos_pendientes', 'Obtiene arqueos pendientes o con diferencias'),
            # Clientes
            ('buscar_cliente', 'Busca clientes por nombre, RUT o email'),
            ('get_historial_cliente', 'Obtiene historial de compras de un cliente'),
            # Proveedores
            ('get_proveedores', 'Obtiene lista de proveedores'),
            ('get_facturas_proveedor', 'Obtiene facturas de un proveedor'),
            # Créditos
            ('get_creditos_trabajadores', 'Obtiene créditos de trabajadores'),
            # Cambios
            ('get_cambios_devoluciones', 'Obtiene cambios y devoluciones'),
            # Requerimientos
            ('get_requerimientos_garantia', 'Obtiene requerimientos de garantía'),
            # Reportes
            ('get_resumen_diario', 'Obtiene resumen ejecutivo del día'),
            ('get_comparativa_ventas', 'Obtiene comparativa de ventas'),
            ('get_info_usuario', 'Obtiene información del usuario actual'),
        ]
        
        for method_name, description in tool_methods:
            method = getattr(cls, method_name)
            sig = inspect.signature(method)
            
            # Construir parámetros
            properties = {}
            required = []
            
            for param_name, param in sig.parameters.items():
                if param_name == 'self':
                    continue
                
                # Determinar tipo
                param_type = "string"
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation == int:
                        param_type = "integer"
                    elif param.annotation == bool:
                        param_type = "boolean"
                
                prop = {"type": param_type}
                
                # Agregar descripción desde docstring
                if method.__doc__:
                    # Buscar descripción del parámetro en el docstring
                    for line in method.__doc__.split('\n'):
                        if f'{param_name}:' in line:
                            prop['description'] = line.split(':', 1)[1].strip()
                            break
                
                properties[param_name] = prop
                
                # Determinar si es requerido
                if param.default == inspect.Parameter.empty:
                    required.append(param_name)
            
            tool_def = {
                "name": method_name,
                "description": method.__doc__ or description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                }
            }
            
            if required:
                tool_def["input_schema"]["required"] = required
            
            tools.append(tool_def)
        
        return tools
