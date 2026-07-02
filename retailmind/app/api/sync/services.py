"""
Servicios para sincronización bidireccional App Desktop
=======================================================

Contiene la lógica de negocio para:
- Procesamiento de tickets offline
- Validación de stock
- Generación de correlativos
- Manejo de conflictos
"""

import logging
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.db.models import F, Sum

from app.models import (
    Ticket, Ticket_Productos, TicketDetallePago,
    Producto_Talla, Vendedor, Sucursal, Correlativo,
    Movimientos_Producto, ESTADO_TICKET_CHOICES
)
from app.models_sync import SyncLog, CuadraturaCaja, MovimientoCaja

logger = logging.getLogger(__name__)


class TicketSyncService:
    """
    Servicio para sincronización de tickets desde app desktop.
    """
    
    def __init__(self, sucursal, dispositivo=None, usuario=None):
        self.sucursal = sucursal
        self.dispositivo = dispositivo
        self.usuario = usuario
        self.warnings = []
    
    def procesar_tickets(self, tickets_data):
        """
        Procesa un lote de tickets desde desktop.
        
        Args:
            tickets_data: Lista de diccionarios con datos de tickets
        
        Returns:
            dict: Resultados del procesamiento
        """
        resultados = []
        total_procesados = 0
        total_fallidos = 0
        
        # Iniciar log de sync
        sync_log = SyncLog.iniciar(
            tipo='tickets_up',
            dispositivo=self.dispositivo,
            sucursal=self.sucursal,
            usuario=self.usuario,
            registros_enviados=len(tickets_data)
        )
        
        for ticket_data in tickets_data:
            try:
                resultado = self._procesar_ticket_individual(ticket_data)
                resultados.append(resultado)
                
                if resultado['success']:
                    total_procesados += 1
                else:
                    total_fallidos += 1
                    
            except Exception as e:
                logger.error(f"Error procesando ticket {ticket_data.get('local_id')}: {e}")
                resultados.append({
                    'local_id': ticket_data.get('local_id'),
                    'server_id': None,
                    'correlativo': None,
                    'success': False,
                    'error': str(e),
                    'warnings': []
                })
                total_fallidos += 1
        
        # Finalizar log
        sync_log.finalizar(
            exitoso=total_fallidos == 0,
            registros_procesados=total_procesados,
            registros_fallidos=total_fallidos,
            detalles={
                'resultados': [
                    {'local_id': str(r['local_id']), 'success': r['success']}
                    for r in resultados
                ]
            }
        )
        
        return {
            'success': total_fallidos == 0,
            'results': resultados,
            'total_enviados': len(tickets_data),
            'total_procesados': total_procesados,
            'total_fallidos': total_fallidos,
            'sync_timestamp': timezone.now()
        }
    
    @transaction.atomic
    def _procesar_ticket_individual(self, ticket_data):
        """
        Procesa un ticket individual con transacción atómica.
        """
        local_id = ticket_data['local_id']
        warnings = []
        
        # Verificar si ya existe (idempotencia)
        ticket_existente = Ticket.objects.filter(local_id=local_id).first()
        if ticket_existente:
            return {
                'local_id': local_id,
                'server_id': ticket_existente.id,
                'correlativo': ticket_existente.correlativo,
                'success': True,
                'error': None,
                'warnings': ['Ticket ya existía - retornando existente']
            }
        
        # Obtener vendedor
        vendedor = ticket_data.get('vendedor')
        if not vendedor:
            vendedor = Vendedor.objects.get(id=ticket_data['vendedor_id'])
        
        # Obtener siguiente correlativo
        correlativo = self._obtener_siguiente_correlativo()
        
        # Validar y procesar stock
        items_procesados = []
        requiere_revision = False
        
        for item_data in ticket_data['items']:
            producto_talla = item_data.get('producto_talla')
            if not producto_talla:
                producto_talla = Producto_Talla.objects.select_for_update().get(
                    id=item_data['producto_talla_id']
                )
            
            cantidad = item_data['cantidad']
            
            # Verificar stock disponible
            stock_disponible = producto_talla.stock
            if stock_disponible < cantidad:
                # La venta ya ocurrió offline, procesarla igual pero marcar
                warnings.append(
                    f'Stock insuficiente para SKU {producto_talla.sku}: '
                    f'disponible={stock_disponible}, vendido={cantidad}'
                )
                requiere_revision = True
            
            items_procesados.append({
                'producto_talla': producto_talla,
                'data': item_data
            })
        
        # Crear ticket
        ticket = Ticket.objects.create(
            vendedor=vendedor,
            sucursal=self.sucursal,
            correlativo=correlativo,
            estado='PAGADO',
            subTotal=ticket_data['subtotal'],
            descuento=ticket_data.get('descuento_total', 0),
            total=ticket_data['total'],
            fecha=ticket_data['created_at'].date(),
            hora=ticket_data['created_at'].time(),
            responsable=vendedor.nombre or vendedor.codigo_vendedor,
            
            # Datos de cliente
            cliente_nombre=ticket_data.get('cliente_nombre'),
            cliente_rut=ticket_data.get('cliente_rut'),
            cliente_email=ticket_data.get('cliente_email'),
            cliente_telefono=ticket_data.get('cliente_telefono'),
            cliente_direccion=ticket_data.get('cliente_direccion'),
            cliente_giro=ticket_data.get('cliente_giro'),
            cliente_comuna=ticket_data.get('cliente_comuna'),
            cliente_ciudad=ticket_data.get('cliente_ciudad'),
            
            # Campos de sync
            local_id=local_id,
            device_id=self.dispositivo.device_id if self.dispositivo else None,
            synced_at=timezone.now(),
            created_offline=True,
            requiere_revision=requiere_revision,
            notas_sync='\n'.join(warnings) if warnings else None,
            
            # Otros campos
            metodo_pago=ticket_data.get('metodo_pago', 'EFECTIVO'),
            observaciones=ticket_data.get('observaciones'),
            modulo_origen='POS',
            tipo_dte=ticket_data.get('tipo_dte', 'TICKET'),
        )
        
        # Crear items del ticket
        for item_proc in items_procesados:
            producto_talla = item_proc['producto_talla']
            item_data = item_proc['data']
            
            Ticket_Productos.objects.create(
                ProductoTalla=producto_talla,
                idTicket=ticket,
                stock=item_data['cantidad'],
                precio=item_data['precio_unitario'],
                descuento_unitario=item_data.get('descuento_unitario', 0),
                subtotal=item_data['subtotal'],
                precio_original=item_data.get('precio_original', item_data['precio_unitario']),
                porcentaje_descuento=item_data.get('porcentaje_descuento', 0),
            )
            
            # Descontar stock
            self._descontar_stock(producto_talla, item_data['cantidad'], ticket)
        
        # Crear pagos
        for pago_data in ticket_data['pagos']:
            TicketDetallePago.objects.create(
                ticket=ticket,
                metodo_pago=pago_data['tipo'],
                tipo_tarjeta=pago_data.get('tipo_tarjeta'),
                voucher=pago_data.get('voucher'),
                numero_orden_compra=pago_data.get('numero_orden_compra'),
                monto=pago_data['monto'],
                notas=pago_data.get('notas'),
            )
        
        return {
            'local_id': local_id,
            'server_id': ticket.id,
            'correlativo': ticket.correlativo,
            'success': True,
            'error': None,
            'warnings': warnings
        }
    
    def _obtener_siguiente_correlativo(self):
        """
        Obtiene el siguiente correlativo para tickets de la sucursal.
        """
        # Buscar correlativo en tabla Correlativo
        try:
            correlativo_obj = Correlativo.objects.select_for_update().get(
                sucursal=self.sucursal,
                tipo_dte='TICKET'
            )
            numero = correlativo_obj.obtener_siguiente_numero()
            return numero
        except Correlativo.DoesNotExist:
            pass
        
        # Fallback: obtener máximo correlativo + 1
        ultimo = Ticket.objects.filter(
            sucursal=self.sucursal
        ).order_by('-correlativo').first()
        
        if ultimo:
            return ultimo.correlativo + 1
        return 1
    
    def _descontar_stock(self, producto_talla, cantidad, ticket):
        """
        Descuenta stock, consume lotes FIFO (mejor esfuerzo) y crea movimiento de inventario.
        """
        # Consumir lotes FIFO disponibles (mejor esfuerzo) antes de tocar el stock
        # plano, que sigue siendo la fuente de verdad. No bloquea la sincronización
        # de la venta si no hay lotes suficientes.
        from app.views_edicion_productos import _consumir_lotes_fifo_ajuste
        try:
            _consumir_lotes_fifo_ajuste(producto_talla, cantidad)
        except Exception:
            logger.exception("No se pudieron bajar lotes FIFO en sync, sku=%s", producto_talla.sku)

        # Descontar del campo stock directo
        producto_talla.stock = F('stock') - cantidad
        producto_talla.save(update_fields=['stock'])
        producto_talla.refresh_from_db()

        # Crear movimiento de inventario (si el modelo existe)
        try:
            Movimientos_Producto.objects.create(
                ProductoTalla=producto_talla,
                sucursal_origen=self.sucursal,
                tipo_movimiento='EGRESO',
                concepto='VENTA_PUBLICO',
                cantidad=-cantidad,  # Negativo porque es salida
                estado='COMPLETADO',
                responsable=self.usuario.username if self.usuario else 'SYNC',
                observaciones=f'Venta sincronizada - Ticket {ticket.correlativo}',
                dte=None,
            )
        except Exception as e:
            logger.warning(f"No se pudo crear movimiento de inventario: {e}")


class ProductoSyncService:
    """
    Servicio para sincronización de productos hacia desktop.
    """
    
    def __init__(self, sucursal):
        self.sucursal = sucursal
    
    def obtener_productos_actualizados(self, since=None, page=1, page_size=100):
        """
        Obtiene productos actualizados desde una fecha.
        
        Args:
            since: datetime desde cuando buscar cambios
            page: número de página
            page_size: tamaño de página
        
        Returns:
            dict: Productos y metadata
        """
        from app.models import Producto
        
        # Query base
        queryset = Producto.objects.filter(
            sucursal=self.sucursal
        ).select_related(
            'categoria', 'atributo1', 'atributo2'
        ).prefetch_related(
            'producto_talla'
        )
        
        # TODO: Agregar campo updated_at a Producto para filtrar por fecha
        # if since:
        #     queryset = queryset.filter(updated_at__gt=since)
        
        # Paginación
        total = queryset.count()
        offset = (page - 1) * page_size
        productos = queryset.order_by('id')[offset:offset + page_size]
        
        # IDs eliminados (si implementas soft delete)
        deleted_ids = []  # TODO: implementar tracking de eliminados
        
        return {
            'productos': list(productos),
            'deleted_ids': deleted_ids,
            'sync_timestamp': timezone.now(),
            'total': total,
            'page': page,
            'page_size': page_size,
            'has_more': (offset + page_size) < total
        }


class CuadraturaSyncService:
    """
    Servicio para sincronización de cuadraturas de caja.
    """
    
    def __init__(self, sucursal, dispositivo=None, usuario=None):
        self.sucursal = sucursal
        self.dispositivo = dispositivo
        self.usuario = usuario
    
    @transaction.atomic
    def procesar_cuadraturas(self, cuadraturas_data):
        """
        Procesa un lote de cuadraturas desde desktop.
        """
        resultados = []
        
        for cuadratura_data in cuadraturas_data:
            try:
                local_id = cuadratura_data['local_id']
                
                # Verificar si ya existe
                existente = CuadraturaCaja.objects.filter(local_id=local_id).first()
                if existente:
                    resultados.append({
                        'local_id': local_id,
                        'server_id': str(existente.id),
                        'success': True,
                        'warning': 'Ya existía'
                    })
                    continue
                
                # Obtener vendedor
                vendedor = Vendedor.objects.filter(
                    id=cuadratura_data['vendedor_id']
                ).first()
                
                # Crear cuadratura
                cuadratura = CuadraturaCaja.objects.create(
                    local_id=local_id,
                    sucursal=self.sucursal,
                    vendedor=vendedor,
                    dispositivo=self.dispositivo,
                    
                    fecha_apertura=cuadratura_data['fecha_apertura'],
                    monto_apertura=cuadratura_data.get('monto_apertura', 0),
                    fecha_cierre=cuadratura_data.get('fecha_cierre'),
                    estado=cuadratura_data.get('estado', 'ABIERTA'),
                    
                    # Esperados
                    efectivo_esperado=cuadratura_data.get('efectivo_esperado', 0),
                    tarjeta_debito_esperado=cuadratura_data.get('tarjeta_debito_esperado', 0),
                    tarjeta_credito_esperado=cuadratura_data.get('tarjeta_credito_esperado', 0),
                    transferencia_esperado=cuadratura_data.get('transferencia_esperado', 0),
                    otros_esperado=cuadratura_data.get('otros_esperado', 0),
                    
                    # Contados
                    efectivo_contado=cuadratura_data.get('efectivo_contado', 0),
                    tarjeta_debito_contado=cuadratura_data.get('tarjeta_debito_contado', 0),
                    tarjeta_credito_contado=cuadratura_data.get('tarjeta_credito_contado', 0),
                    transferencia_contado=cuadratura_data.get('transferencia_contado', 0),
                    otros_contado=cuadratura_data.get('otros_contado', 0),
                    
                    # Diferencias
                    diferencia_efectivo=cuadratura_data.get('efectivo_contado', 0) - cuadratura_data.get('efectivo_esperado', 0),
                    
                    # Conteo
                    cantidad_tickets=cuadratura_data.get('cantidad_tickets', 0),
                    cantidad_boletas=cuadratura_data.get('cantidad_boletas', 0),
                    cantidad_facturas=cuadratura_data.get('cantidad_facturas', 0),
                    
                    # Totales
                    total_ingresos=cuadratura_data.get('total_ingresos', 0),
                    total_egresos=cuadratura_data.get('total_egresos', 0),
                    total_retiros=cuadratura_data.get('total_retiros', 0),
                    
                    observaciones=cuadratura_data.get('observaciones'),
                    synced_at=timezone.now(),
                )
                
                # Calcular diferencia total
                cuadratura.diferencia_total = cuadratura.total_contado - cuadratura.total_esperado
                cuadratura.save(update_fields=['diferencia_total'])
                
                # Procesar movimientos si existen
                for mov_data in cuadratura_data.get('movimientos', []):
                    MovimientoCaja.objects.create(
                        local_id=mov_data['local_id'],
                        cuadratura=cuadratura,
                        sucursal=self.sucursal,
                        vendedor=vendedor,
                        tipo=mov_data['tipo'],
                        monto=mov_data['monto'],
                        concepto=mov_data['concepto'],
                        fecha_hora=mov_data['fecha_hora'],
                        ticket_id=mov_data.get('ticket_id'),
                        observaciones=mov_data.get('observaciones'),
                        synced_at=timezone.now(),
                    )
                
                resultados.append({
                    'local_id': local_id,
                    'server_id': str(cuadratura.id),
                    'success': True,
                })
                
            except Exception as e:
                logger.error(f"Error procesando cuadratura: {e}")
                resultados.append({
                    'local_id': cuadratura_data.get('local_id'),
                    'server_id': None,
                    'success': False,
                    'error': str(e)
                })
        
        return {
            'success': all(r['success'] for r in resultados),
            'results': resultados,
            'sync_timestamp': timezone.now()
        }
