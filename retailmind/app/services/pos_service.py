"""
Servicios para la gestión de POS Transbank
Contiene la lógica de negocio para terminales POS y transacciones
"""

import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User

from ..models import (
    ConfiguracionPOS, TransaccionPOS, LogPOS, Ticket, TicketDetallePago,
    Sucursal, Vendedor, TIPO_POS_CHOICES, ESTADO_TRANSACCION_POS_CHOICES,
    TIPO_TARJETA_CHOICES, METODO_PAGO_TICKET_CHOICES
)

logger = logging.getLogger(__name__)


class POSConfigurationService:
    """Servicio para gestión de configuraciones POS"""
    
    @staticmethod
    def crear_configuracion(sucursal: Sucursal, datos: Dict[str, Any]) -> ConfiguracionPOS:
        """
        Crear nueva configuración POS
        
        Args:
            sucursal: Sucursal donde se creará la configuración
            datos: Diccionario con los datos de configuración
            
        Returns:
            ConfiguracionPOS: La configuración creada
            
        Raises:
            ValidationError: Si los datos son inválidos
        """
        # Validar datos requeridos
        campos_requeridos = ['nombre', 'tipo_pos', 'puerto_conexion']
        for campo in campos_requeridos:
            if not datos.get(campo):
                raise ValidationError(f'El campo {campo} es requerido')
        
        # Validar tipo de POS
        tipos_validos = [choice[0] for choice in TIPO_POS_CHOICES]
        if datos['tipo_pos'] not in tipos_validos:
            raise ValidationError('Tipo de POS inválido')
        
        # Verificar que el nombre no exista en la sucursal
        if ConfiguracionPOS.objects.filter(
            sucursal=sucursal, 
            nombre=datos['nombre']
        ).exists():
            raise ValidationError('Ya existe una configuración con ese nombre en esta sucursal')
        
        # Crear configuración
        configuracion = ConfiguracionPOS.objects.create(
            sucursal=sucursal,
            nombre=datos['nombre'],
            tipo_pos=datos['tipo_pos'],
            puerto_conexion=datos['puerto_conexion'],
            velocidad_conexion=datos.get('velocidad_conexion', 115200),
            activo=datos.get('activo', True),
            es_principal=datos.get('es_principal', False),
            timeout_conexion=datos.get('timeout_conexion', 30),
            numero_serie=datos.get('numero_serie', ''),
            version_firmware=datos.get('version_firmware', ''),
            observaciones=datos.get('observaciones', '')
        )
        
        # Log de creación
        LogPOS.objects.create(
            configuracion_pos=configuracion,
            tipo_evento='INFO',
            mensaje=f'Configuración POS creada: {configuracion.nombre}',
            datos_tecnicos={
                'tipo_pos': configuracion.tipo_pos,
                'puerto': configuracion.puerto_conexion,
                'velocidad': configuracion.velocidad_conexion
            }
        )
        
        logger.info(f'Configuración POS creada: {configuracion.nombre} en {sucursal.alias}')
        return configuracion
    
    @staticmethod
    def actualizar_configuracion(configuracion: ConfiguracionPOS, datos: Dict[str, Any]) -> ConfiguracionPOS:
        """
        Actualizar configuración POS existente
        
        Args:
            configuracion: Configuración a actualizar
            datos: Nuevos datos
            
        Returns:
            ConfiguracionPOS: La configuración actualizada
        """
        # Actualizar campos
        campos_actualizables = [
            'nombre', 'tipo_pos', 'puerto_conexion', 'velocidad_conexion',
            'activo', 'es_principal', 'timeout_conexion', 'numero_serie',
            'version_firmware', 'observaciones'
        ]
        
        cambios = []
        for campo in campos_actualizables:
            if campo in datos:
                valor_anterior = getattr(configuracion, campo)
                valor_nuevo = datos[campo]
                if valor_anterior != valor_nuevo:
                    setattr(configuracion, campo, valor_nuevo)
                    cambios.append(f'{campo}: {valor_anterior} → {valor_nuevo}')
        
        if cambios:
            configuracion.save()
            
            # Log de actualización
            LogPOS.objects.create(
                configuracion_pos=configuracion,
                tipo_evento='INFO',
                mensaje=f'Configuración actualizada: {", ".join(cambios)}',
                datos_tecnicos={'cambios': cambios}
            )
            
            logger.info(f'Configuración POS actualizada: {configuracion.nombre}')
        
        return configuracion
    
    @staticmethod
    def probar_conexion(configuracion: ConfiguracionPOS) -> Dict[str, Any]:
        """
        Probar conexión con terminal POS
        
        Args:
            configuracion: Configuración POS a probar
            
        Returns:
            Dict con resultado de la prueba
        """
        try:
            # Aquí iría la lógica real de conexión con el SDK de Transbank
            # Por ahora simulamos la prueba
            
            # Simular tiempo de conexión
            import time
            time.sleep(1)
            
            # Actualizar estado de conexión
            configuracion.ultima_conexion = timezone.now()
            configuracion.estado_conexion = 'CONECTADO'
            configuracion.save()
            
            # Crear log exitoso
            LogPOS.objects.create(
                configuracion_pos=configuracion,
                tipo_evento='CONEXION',
                mensaje=f'Prueba de conexión exitosa en puerto {configuracion.puerto_conexion}',
                datos_tecnicos={
                    'puerto': configuracion.puerto_conexion,
                    'velocidad': configuracion.velocidad_conexion,
                    'timeout': configuracion.timeout_conexion,
                    'resultado': 'EXITOSO'
                }
            )
            
            return {
                'success': True,
                'message': 'Conexión exitosa',
                'estado_conexion': configuracion.get_estado_conexion_display(),
                'ultima_conexion': configuracion.ultima_conexion
            }
            
        except Exception as e:
            # Actualizar estado de error
            configuracion.estado_conexion = 'ERROR'
            configuracion.save()
            
            # Crear log de error
            LogPOS.objects.create(
                configuracion_pos=configuracion,
                tipo_evento='ERROR',
                mensaje=f'Error en prueba de conexión: {str(e)}',
                datos_tecnicos={
                    'puerto': configuracion.puerto_conexion,
                    'error': str(e)
                }
            )
            
            logger.error(f'Error probando conexión POS {configuracion.nombre}: {str(e)}')
            
            return {
                'success': False,
                'error': str(e),
                'suggestion': POSConfigurationService._get_error_suggestion(str(e))
            }
    
    @staticmethod
    def _get_error_suggestion(error_message: str) -> str:
        """Obtener sugerencia para error de conexión"""
        error_lower = error_message.lower()
        
        if 'puerto' in error_lower or 'port' in error_lower:
            return 'Verifique que el puerto esté disponible y no esté siendo usado por otra aplicación'
        elif 'timeout' in error_lower:
            return 'Verifique la conexión del terminal y aumente el timeout si es necesario'
        elif 'permission' in error_lower or 'permiso' in error_lower:
            return 'Verifique los permisos de acceso al puerto serie'
        else:
            return 'Verifique que el terminal esté conectado y encendido'
    
    @staticmethod
    def obtener_configuraciones_activas(sucursal: Sucursal) -> List[ConfiguracionPOS]:
        """Obtener configuraciones POS activas de una sucursal"""
        return ConfiguracionPOS.objects.filter(
            sucursal=sucursal,
            activo=True
        ).order_by('-es_principal', 'nombre')
    
    @staticmethod
    def obtener_configuracion_principal(sucursal: Sucursal) -> Optional[ConfiguracionPOS]:
        """Obtener configuración principal de una sucursal"""
        return ConfiguracionPOS.objects.filter(
            sucursal=sucursal,
            activo=True,
            es_principal=True
        ).first()


class POSTransactionService:
    """Servicio para gestión de transacciones POS"""
    
    @staticmethod
    def iniciar_transaccion(
        configuracion: ConfiguracionPOS,
        monto: Decimal,
        usuario: User,
        ticket: Optional[Ticket] = None,
        observaciones: str = ''
    ) -> TransaccionPOS:
        """
        Iniciar nueva transacción POS
        
        Args:
            configuracion: Configuración POS a usar
            monto: Monto de la transacción
            usuario: Usuario que inicia la transacción
            ticket: Ticket asociado (opcional)
            observaciones: Observaciones adicionales
            
        Returns:
            TransaccionPOS: La transacción creada
            
        Raises:
            ValidationError: Si los datos son inválidos
        """
        # Validaciones
        if not configuracion.activo:
            raise ValidationError('La configuración POS no está activa')
        
        if monto <= 0:
            raise ValidationError('El monto debe ser mayor a 0')
        
        if monto > Decimal('99999999.99'):
            raise ValidationError('El monto excede el límite máximo')
        
        # Crear transacción
        transaccion = TransaccionPOS.objects.create(
            configuracion_pos=configuracion,
            ticket=ticket,
            monto=monto,
            tipo_transaccion='VENTA',
            estado='INICIADA',
            usuario_operador=usuario,
            observaciones=observaciones
        )
        
        # Crear log de inicio
        LogPOS.objects.create(
            configuracion_pos=configuracion,
            transaccion_pos=transaccion,
            tipo_evento='COMANDO_ENVIADO',
            mensaje=f'Iniciando venta por ${monto:,}',
            datos_tecnicos={
                'monto': float(monto),
                'ticket_pos': transaccion.ticket_pos,
                'puerto': configuracion.puerto_conexion,
                'ticket_id': ticket.id if ticket else None
            }
        )
        
        logger.info(f'Transacción POS iniciada: {transaccion.ticket_pos} por ${monto}')
        return transaccion
    
    @staticmethod
    def completar_transaccion(
        transaccion: TransaccionPOS,
        respuesta_pos: Dict[str, Any]
    ) -> TransaccionPOS:
        """
        Completar transacción con respuesta del POS
        
        Args:
            transaccion: Transacción a completar
            respuesta_pos: Respuesta del terminal POS
            
        Returns:
            TransaccionPOS: La transacción completada
        """
        with transaction.atomic():
            # Actualizar transacción con respuesta del POS
            transaccion.codigo_respuesta = respuesta_pos.get('response_code', '')
            transaccion.mensaje_respuesta = respuesta_pos.get('response_message', '')
            transaccion.codigo_autorizacion = respuesta_pos.get('authorization_code', '')
            transaccion.tipo_tarjeta = respuesta_pos.get('card_type', 'DESCONOCIDO')
            transaccion.ultimos_4_digitos = respuesta_pos.get('card_number', '')[-4:] if respuesta_pos.get('card_number') else ''
            transaccion.nombre_tarjeta = respuesta_pos.get('card_brand', '')
            transaccion.numero_operacion = respuesta_pos.get('operation_number', '')
            transaccion.numero_cuotas = respuesta_pos.get('installments', 1)
            transaccion.codigo_comercio = respuesta_pos.get('commerce_code', '')
            transaccion.terminal_id = respuesta_pos.get('terminal_id', '')
            
            # Determinar estado final
            if respuesta_pos.get('success', False) and transaccion.codigo_autorizacion:
                transaccion.estado = 'APROBADA'
            else:
                transaccion.estado = 'RECHAZADA'
                transaccion.error_detalle = respuesta_pos.get('error_message', 'Transacción rechazada')
            
            transaccion.save()
            
            # Crear log de respuesta
            LogPOS.objects.create(
                configuracion_pos=transaccion.configuracion_pos,
                transaccion_pos=transaccion,
                tipo_evento='RESPUESTA_RECIBIDA',
                mensaje=f'Transacción {transaccion.get_estado_display().lower()}',
                datos_tecnicos=respuesta_pos
            )
            
            # Si la transacción fue exitosa y hay un ticket asociado, procesar pago
            if transaccion.es_exitosa and transaccion.ticket:
                POSTransactionService._procesar_pago_ticket(transaccion)
            
            logger.info(f'Transacción POS completada: {transaccion.ticket_pos} - {transaccion.estado}')
            return transaccion
    
    @staticmethod
    def _procesar_pago_ticket(transaccion: TransaccionPOS):
        """Procesar pago del ticket asociado a la transacción"""
        # Determinar método de pago según el tipo de tarjeta
        metodo_pago = 'TBK_POS_INTEGRADO'
        if transaccion.tipo_tarjeta == 'DEBITO':
            metodo_pago = 'TBK_DEBITO_POS'
        elif transaccion.tipo_tarjeta == 'CREDITO':
            metodo_pago = 'TBK_CREDITO_POS'
        elif transaccion.tipo_tarjeta == 'PREPAGO':
            metodo_pago = 'TBK_PREPAGO_POS'
        
        # Crear detalle de pago
        detalle_pago = TicketDetallePago.objects.create(
            ticket=transaccion.ticket,
            metodo_pago=metodo_pago,
            tipo_tarjeta=transaccion.nombre_tarjeta,
            voucher=transaccion.codigo_autorizacion,
            monto=int(transaccion.monto),
            notas=f'POS {transaccion.configuracion_pos.nombre} - Oper: {transaccion.numero_operacion}'
        )
        
        # Asociar el detalle de pago con la transacción
        transaccion.detalle_pago = detalle_pago
        transaccion.save()
        
        # Actualizar estado del ticket si está completamente pagado
        if transaccion.ticket.saldo_por_pagar <= 0:
            transaccion.ticket.estado = 'PAGADO'
            transaccion.ticket.save()
    
    @staticmethod
    def anular_transaccion(
        transaccion: TransaccionPOS,
        usuario: User,
        motivo: str = ''
    ) -> TransaccionPOS:
        """
        Anular transacción POS
        
        Args:
            transaccion: Transacción a anular
            usuario: Usuario que anula
            motivo: Motivo de anulación
            
        Returns:
            TransaccionPOS: Nueva transacción de anulación
            
        Raises:
            ValidationError: Si no se puede anular
        """
        if not transaccion.puede_anular:
            raise ValidationError('Esta transacción no puede ser anulada')
        
        with transaction.atomic():
            # Crear nueva transacción de anulación
            anulacion = TransaccionPOS.objects.create(
                configuracion_pos=transaccion.configuracion_pos,
                ticket=transaccion.ticket,
                monto=transaccion.monto,
                tipo_transaccion='ANULACION',
                estado='INICIADA',
                usuario_operador=usuario,
                observaciones=f'Anulación de {transaccion.ticket_pos}. Motivo: {motivo}'
            )
            
            # Simular anulación exitosa (en producción usar SDK real)
            anulacion.estado = 'APROBADA'
            anulacion.codigo_respuesta = '00'
            anulacion.mensaje_respuesta = 'Anulación aprobada'
            anulacion.codigo_autorizacion = f'ANU-{transaccion.codigo_autorizacion}'
            anulacion.save()
            
            # Marcar transacción original como anulada
            transaccion.estado = 'ANULADA'
            transaccion.save()
            
            # Si había un detalle de pago asociado, marcarlo como anulado
            if transaccion.detalle_pago:
                transaccion.detalle_pago.notas += f' - ANULADO {timezone.now().strftime("%d/%m/%Y %H:%M")}'
                transaccion.detalle_pago.save()
            
            # Crear logs
            LogPOS.objects.create(
                configuracion_pos=transaccion.configuracion_pos,
                transaccion_pos=anulacion,
                tipo_evento='COMANDO_ENVIADO',
                mensaje=f'Anulación exitosa de {transaccion.ticket_pos}',
                datos_tecnicos={
                    'transaccion_original': transaccion.ticket_pos,
                    'codigo_autorizacion_original': transaccion.codigo_autorizacion,
                    'monto': float(transaccion.monto),
                    'motivo': motivo
                }
            )
            
            logger.info(f'Transacción POS anulada: {transaccion.ticket_pos}')
            return anulacion
    
    @staticmethod
    def obtener_transacciones_por_fecha(
        sucursal: Sucursal,
        fecha_inicio: datetime,
        fecha_fin: datetime,
        estado: Optional[str] = None
    ) -> List[TransaccionPOS]:
        """Obtener transacciones por rango de fechas"""
        queryset = TransaccionPOS.objects.filter(
            configuracion_pos__sucursal=sucursal,
            fecha_inicio__range=[fecha_inicio, fecha_fin]
        ).select_related('configuracion_pos', 'ticket', 'usuario_operador')
        
        if estado:
            queryset = queryset.filter(estado=estado)
        
        return queryset.order_by('-fecha_inicio')
    
    @staticmethod
    def obtener_estadisticas_transacciones(
        sucursal: Sucursal,
        fecha_inicio: datetime,
        fecha_fin: datetime
    ) -> Dict[str, Any]:
        """Obtener estadísticas de transacciones"""
        transacciones = POSTransactionService.obtener_transacciones_por_fecha(
            sucursal, fecha_inicio, fecha_fin
        )
        
        total_transacciones = transacciones.count()
        aprobadas = transacciones.filter(estado='APROBADA')
        rechazadas = transacciones.filter(estado='RECHAZADA')
        anuladas = transacciones.filter(estado='ANULADA')
        
        monto_total = sum(t.monto for t in aprobadas)
        monto_promedio = monto_total / aprobadas.count() if aprobadas.count() > 0 else 0
        
        # Estadísticas por tipo de tarjeta
        tipos_tarjeta = {}
        for transaccion in aprobadas:
            tipo = transaccion.tipo_tarjeta or 'DESCONOCIDO'
            if tipo not in tipos_tarjeta:
                tipos_tarjeta[tipo] = {'cantidad': 0, 'monto': Decimal('0')}
            tipos_tarjeta[tipo]['cantidad'] += 1
            tipos_tarjeta[tipo]['monto'] += transaccion.monto
        
        return {
            'total_transacciones': total_transacciones,
            'aprobadas': aprobadas.count(),
            'rechazadas': rechazadas.count(),
            'anuladas': anuladas.count(),
            'tasa_aprobacion': (aprobadas.count() / total_transacciones * 100) if total_transacciones > 0 else 0,
            'monto_total': monto_total,
            'monto_promedio': monto_promedio,
            'tipos_tarjeta': tipos_tarjeta
        }


class POSLogService:
    """Servicio para gestión de logs POS"""
    
    @staticmethod
    def crear_log(
        configuracion: ConfiguracionPOS,
        tipo_evento: str,
        mensaje: str,
        datos_tecnicos: Optional[Dict[str, Any]] = None,
        transaccion: Optional[TransaccionPOS] = None
    ) -> LogPOS:
        """Crear nuevo log POS"""
        return LogPOS.objects.create(
            configuracion_pos=configuracion,
            transaccion_pos=transaccion,
            tipo_evento=tipo_evento,
            mensaje=mensaje,
            datos_tecnicos=datos_tecnicos or {}
        )
    
    @staticmethod
    def obtener_logs_recientes(
        configuracion: ConfiguracionPOS,
        limite: int = 100,
        tipo_evento: Optional[str] = None
    ) -> List[LogPOS]:
        """Obtener logs recientes de una configuración"""
        queryset = LogPOS.objects.filter(
            configuracion_pos=configuracion
        ).select_related('transaccion_pos')
        
        if tipo_evento:
            queryset = queryset.filter(tipo_evento=tipo_evento)
        
        return queryset.order_by('-timestamp')[:limite]
    
    @staticmethod
    def limpiar_logs_antiguos(dias: int = 30):
        """Limpiar logs más antiguos que X días"""
        fecha_limite = timezone.now() - timedelta(days=dias)
        logs_eliminados = LogPOS.objects.filter(
            timestamp__lt=fecha_limite
        ).delete()
        
        logger.info(f'Logs POS limpiados: {logs_eliminados[0]} registros eliminados')
        return logs_eliminados[0]


class POSIntegrationService:
    """Servicio para integración completa POS + Django"""
    
    @staticmethod
    def procesar_venta_completa(
        configuracion_id: int,
        monto: Decimal,
        usuario: User,
        ticket_id: Optional[int] = None,
        observaciones: str = ''
    ) -> Dict[str, Any]:
        """
        Procesar venta completa desde Django hasta POS
        
        Args:
            configuracion_id: ID de configuración POS
            monto: Monto a cobrar
            usuario: Usuario que procesa
            ticket_id: ID de ticket asociado (opcional)
            observaciones: Observaciones
            
        Returns:
            Dict con resultado del proceso
        """
        try:
            # Obtener configuración
            configuracion = ConfiguracionPOS.objects.get(
                id=configuracion_id,
                activo=True
            )
            
            # Obtener ticket si se especificó
            ticket = None
            if ticket_id:
                ticket = Ticket.objects.get(
                    id=ticket_id,
                    sucursal=configuracion.sucursal
                )
            
            # Iniciar transacción
            transaccion = POSTransactionService.iniciar_transaccion(
                configuracion=configuracion,
                monto=monto,
                usuario=usuario,
                ticket=ticket,
                observaciones=observaciones
            )
            
            return {
                'success': True,
                'transaccion': {
                    'id': transaccion.id,
                    'ticket_pos': transaccion.ticket_pos,
                    'monto': float(transaccion.monto),
                    'estado': transaccion.estado,
                    'puerto_conexion': configuracion.puerto_conexion,
                    'timeout': configuracion.timeout_conexion
                }
            }
            
        except ConfiguracionPOS.DoesNotExist:
            return {
                'success': False,
                'error': 'Configuración POS no encontrada o inactiva'
            }
        except Ticket.DoesNotExist:
            return {
                'success': False,
                'error': 'Ticket no encontrado'
            }
        except ValidationError as e:
            return {
                'success': False,
                'error': str(e)
            }
        except Exception as e:
            logger.error(f'Error en venta completa POS: {str(e)}')
            return {
                'success': False,
                'error': f'Error interno: {str(e)}'
            }
    
    @staticmethod
    def obtener_resumen_pos(sucursal: Sucursal) -> Dict[str, Any]:
        """Obtener resumen del estado POS de una sucursal"""
        configuraciones = ConfiguracionPOS.objects.filter(sucursal=sucursal)
        
        # Estadísticas de configuraciones
        total_configuraciones = configuraciones.count()
        activas = configuraciones.filter(activo=True).count()
        conectadas = configuraciones.filter(estado_conexion='CONECTADO').count()
        
        # Transacciones del día
        hoy = timezone.now().date()
        inicio_dia = timezone.datetime.combine(hoy, timezone.datetime.min.time())
        fin_dia = timezone.datetime.combine(hoy, timezone.datetime.max.time())
        
        transacciones_hoy = TransaccionPOS.objects.filter(
            configuracion_pos__sucursal=sucursal,
            fecha_inicio__range=[inicio_dia, fin_dia]
        )
        
        return {
            'configuraciones': {
                'total': total_configuraciones,
                'activas': activas,
                'conectadas': conectadas,
                'desconectadas': activas - conectadas
            },
            'transacciones_hoy': {
                'total': transacciones_hoy.count(),
                'aprobadas': transacciones_hoy.filter(estado='APROBADA').count(),
                'rechazadas': transacciones_hoy.filter(estado='RECHAZADA').count(),
                'monto_total': sum(
                    t.monto for t in transacciones_hoy.filter(estado='APROBADA')
                )
            }
        }
