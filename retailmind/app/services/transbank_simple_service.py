"""
Servicio simplificado para Transbank POS
Solo maneja persistencia de transacciones en base de datos
La comunicación con el POS se hace desde el navegador (Web Serial API)
"""

from django.db import transaction as db_transaction
from django.utils import timezone
from ..models import TransaccionPOS, ConfiguracionPOS, Ticket, TicketDetallePago
import logging

logger = logging.getLogger(__name__)


class TransbankPersistenceService:
    """
    Servicio para guardar transacciones Transbank en la base de datos
    NO se comunica con el POS - eso lo hace JavaScript en el navegador
    """
    
    @staticmethod
    def guardar_transaccion(data, sucursal_id, user):
        """
        Guarda una transacción POS en la base de datos
        
        Args:
            data (dict): Datos de la transacción desde el frontend
            sucursal_id (int): ID de la sucursal
            user: Usuario que realiza la transacción
            
        Returns:
            dict: Resultado con ID de transacción creada
        """
        try:
            with db_transaction.atomic():
                # Obtener configuración POS de la sucursal
                config = ConfiguracionPOS.objects.filter(
                    sucursal_id=sucursal_id,
                    activo=True,
                    es_principal=True
                ).first()
                
                # Obtener ticket si se proporcionó.
                # OJO: el frontend NO manda la PK, manda el CORRELATIVO del
                # ticket: `construir_ticket_data()` (views_modulo_ventas.py)
                # publica 'ticket_id': ticket.correlativo, y generacionVentas.html
                # envía `ticket_id: ticketActual.ticket_id`. Buscarlo por `id`
                # colgaba la transacción de un ticket ajeno que casualmente
                # tenía esa PK. Caso verificado en producción: TransaccionPOS
                # 8691 ($24.990) quedó apuntando al ticket pk=11250 (total
                # $23.980, correlativo 10906) cuando la venta real era el
                # correlativo 11250 (pk=15808, total $24.990). Las 19 filas con
                # FK poblada estaban TODAS mal vinculadas.
                # (sucursal, correlativo) es unique_together, así que la
                # búsqueda por correlativo es exacta y determinística. Si no
                # calza se deja la FK en NULL: mejor sin ticket que con el
                # ticket equivocado (el correlativo igual queda en observaciones).
                ticket_obj = None
                ticket_id = data.get('ticket_id')
                if ticket_id:
                    try:
                        correlativo = int(
                            str(ticket_id).upper().replace('TKT', '').strip()
                        )
                    except (TypeError, ValueError):
                        correlativo = None
                        logger.warning(
                            "ticket_id no numérico en transacción POS: %r", ticket_id
                        )
                    if correlativo is not None:
                        ticket_obj = Ticket.objects.filter(
                            sucursal_id=sucursal_id,
                            correlativo=correlativo
                        ).first()
                        if ticket_obj is None:
                            logger.warning(
                                "No existe ticket correlativo=%s en sucursal=%s; "
                                "la transacción POS queda sin vincular.",
                                correlativo, sucursal_id
                            )

                # Datos crudos del POS que el modelo TransaccionPOS no tiene
                # como columnas (accountingDate / realDate / realTime). Se guardan
                # dentro de `observaciones` para no perder la auditoría sin
                # exigir migración del modelo en producción.
                fecha_contable = data.get('accountingDate')
                fecha_real = data.get('realDate')
                hora_real = data.get('realTime')
                observaciones_extra = (
                    f'Ticket POS: {data.get("ticket", "")} | '
                    f'accountingDate={fecha_contable} | '
                    f'realDate={fecha_real} | '
                    f'realTime={hora_real}'
                )

                # Respuesta del terminal: las columnas existen en el modelo
                # (pos.py 165-166) pero nunca se poblaban, así que las 8.796
                # filas tienen codigo_respuesta NULL y no hay forma de
                # diagnosticar un rechazo a posteriori. `responseCode` sí viene
                # en el payload del frontend; `responseMessage` aún no, pero se
                # deja cableado para cuando el JS lo incluya.
                codigo_respuesta = data.get('responseCode')
                codigo_respuesta = (
                    '' if codigo_respuesta is None else str(codigo_respuesta)[:10]
                )
                mensaje_respuesta = (data.get('responseMessage') or '')[:200]

                # Crear TransaccionPOS
                transaccion = TransaccionPOS.objects.create(
                    configuracion_pos=config,
                    ticket=ticket_obj,
                    monto=int(data.get('amount', 0)),
                    tipo_transaccion='VENTA',
                    estado='APROBADA' if data.get('successful', False) else 'RECHAZADA',
                    codigo_respuesta=codigo_respuesta,
                    mensaje_respuesta=mensaje_respuesta,
                    codigo_autorizacion=data.get('authorizationCode', ''),
                    numero_operacion=str(data.get('operationNumber', '')),
                    tipo_tarjeta=data.get('cardType', 'DESCONOCIDO'),
                    ultimos_4_digitos=data.get('last4Digits', ''),
                    nombre_tarjeta=data.get('cardBrand', ''),
                    numero_cuotas=data.get('sharesNumber', 0),
                    codigo_comercio=data.get('commerceCode', ''),
                    terminal_id=data.get('terminalId', ''),
                    usuario_operador=user,
                    observaciones=observaciones_extra,
                )
                
                logger.info(f"✅ TransaccionPOS guardada: {transaccion.id}")
                
                return {
                    'success': True,
                    'transaccion_id': transaccion.id,
                    'message': 'Transacción guardada correctamente'
                }
                
        except Exception as e:
            logger.error(f"❌ Error guardando transacción: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'message': 'Error al guardar la transacción'
            }
    
    @staticmethod
    def guardar_configuracion_pos(data, sucursal_id):
        """
        Guarda o actualiza configuración de POS detectado
        
        Args:
            data (dict): Datos del POS (puerto, baudrate, etc.)
            sucursal_id (int): ID de la sucursal
            
        Returns:
            dict: Resultado con ID de configuración
        """
        try:
            with db_transaction.atomic():
                from ..models import Sucursal
                sucursal = Sucursal.objects.get(id=sucursal_id)
                
                # Actualizar o crear configuración
                config, created = ConfiguracionPOS.objects.update_or_create(
                    sucursal=sucursal,
                    tipo_pos='SDK_SERIAL',
                    defaults={
                        'nombre': data.get('nombre', f'POS-{data.get("port", "USB")}'),
                        'puerto_conexion': data.get('port', ''),
                        'velocidad_conexion': data.get('baudrate', 115200),
                        'activo': True,
                        'es_principal': True,
                        'estado_conexion': 'CONECTADO',
                        'ultima_conexion': timezone.now(),
                        'observaciones': data.get('observaciones', '')
                    }
                )
                
                logger.info(f"✅ ConfiguracionPOS {'creada' if created else 'actualizada'}: {config.id}")
                
                return {
                    'success': True,
                    'config_id': config.id,
                    'created': created,
                    'message': f'Configuración {"creada" if created else "actualizada"} correctamente'
                }
                
        except Exception as e:
            logger.error(f"❌ Error guardando configuración: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'message': 'Error al guardar la configuración'
            }
