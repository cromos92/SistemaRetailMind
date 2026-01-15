"""
Modelos para sincronización con App Desktop (POS Físico)
========================================================

Modelos:
- DispositivoAutorizado: Control de dispositivos autorizados para sync
- RefreshTokenDesktop: Tokens de refresco para autenticación desktop
- SyncLog: Registro de sincronizaciones
"""

import uuid
import hashlib
from django.db import models
from django.conf import settings
from django.utils import timezone


class DispositivoAutorizado(models.Model):
    """
    Modelo para dispositivos autorizados a usar la app desktop.
    Cada dispositivo debe estar registrado para poder sincronizar.
    """
    
    ESTADO_CHOICES = [
        ('ACTIVO', 'Activo'),
        ('SUSPENDIDO', 'Suspendido'),
        ('REVOCADO', 'Revocado'),
    ]
    
    device_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name='ID del Dispositivo'
    )
    
    # Relaciones
    sucursal = models.ForeignKey(
        'Sucursal',
        on_delete=models.CASCADE,
        related_name='dispositivos_autorizados',
        verbose_name='Sucursal'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dispositivos_autorizados',
        verbose_name='Usuario'
    )
    
    # Identificación
    nombre = models.CharField(
        max_length=100,
        verbose_name='Nombre del Dispositivo',
        help_text='Ej: "Caja 1", "Notebook Vendedor Juan"'
    )
    descripcion = models.TextField(
        blank=True, null=True,
        verbose_name='Descripción',
        help_text='Información adicional sobre el dispositivo'
    )
    
    # Información del dispositivo
    sistema_operativo = models.CharField(
        max_length=100,
        blank=True, null=True,
        verbose_name='Sistema Operativo'
    )
    version_app = models.CharField(
        max_length=20,
        blank=True, null=True,
        verbose_name='Versión de la App'
    )
    
    # Estado y control
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default='ACTIVO',
        verbose_name='Estado'
    )
    activo = models.BooleanField(
        default=True,
        verbose_name='Activo'
    )
    
    # Fechas
    ultimo_acceso = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Último Acceso'
    )
    ultima_sincronizacion = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Última Sincronización'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Registro'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Última Actualización'
    )
    
    # Límites y configuración
    max_tickets_offline = models.IntegerField(
        default=1000,
        verbose_name='Máx. Tickets Offline',
        help_text='Máximo de tickets que puede almacenar offline'
    )
    
    class Meta:
        verbose_name = 'Dispositivo Autorizado'
        verbose_name_plural = 'Dispositivos Autorizados'
        ordering = ['-ultimo_acceso']
    
    def __str__(self):
        return f"{self.nombre} - {self.sucursal.alias}"
    
    def registrar_acceso(self):
        """Registra un nuevo acceso del dispositivo"""
        self.ultimo_acceso = timezone.now()
        self.save(update_fields=['ultimo_acceso'])
    
    def registrar_sync(self):
        """Registra una sincronización"""
        self.ultima_sincronizacion = timezone.now()
        self.save(update_fields=['ultima_sincronizacion'])
    
    def esta_activo(self):
        """Verifica si el dispositivo puede sincronizar"""
        return self.activo and self.estado == 'ACTIVO'
    
    def suspender(self, motivo=None):
        """Suspende el dispositivo"""
        self.estado = 'SUSPENDIDO'
        self.activo = False
        if motivo:
            self.descripcion = f"{self.descripcion or ''}\n[SUSPENDIDO]: {motivo}"
        self.save()
    
    def revocar(self, motivo=None):
        """Revoca permanentemente el dispositivo"""
        self.estado = 'REVOCADO'
        self.activo = False
        # Invalida todos los tokens
        self.refresh_tokens.all().update(revocado=True)
        if motivo:
            self.descripcion = f"{self.descripcion or ''}\n[REVOCADO]: {motivo}"
        self.save()


class RefreshTokenDesktop(models.Model):
    """
    Modelo para almacenar refresh tokens de la app desktop.
    Implementa rotación de tokens y detección de reutilización.
    """
    
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    
    # Relaciones
    dispositivo = models.ForeignKey(
        DispositivoAutorizado,
        on_delete=models.CASCADE,
        related_name='refresh_tokens',
        verbose_name='Dispositivo'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='refresh_tokens_desktop',
        verbose_name='Usuario'
    )
    
    # Token (almacenamos hash por seguridad)
    token_hash = models.CharField(
        max_length=128,
        unique=True,
        verbose_name='Hash del Token'
    )
    
    # Familia de tokens (para detectar reutilización)
    familia_id = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        verbose_name='ID de Familia',
        help_text='Todos los tokens de una misma sesión comparten familia'
    )
    
    # Estado
    revocado = models.BooleanField(
        default=False,
        verbose_name='Revocado'
    )
    utilizado = models.BooleanField(
        default=False,
        verbose_name='Utilizado',
        help_text='True si ya fue usado para generar un nuevo token'
    )
    
    # Fechas
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Creación'
    )
    expires_at = models.DateTimeField(
        verbose_name='Fecha de Expiración'
    )
    used_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha de Uso'
    )
    
    # Metadata
    ip_address = models.GenericIPAddressField(
        null=True, blank=True,
        verbose_name='Dirección IP'
    )
    user_agent = models.TextField(
        blank=True, null=True,
        verbose_name='User Agent'
    )
    
    class Meta:
        verbose_name = 'Refresh Token Desktop'
        verbose_name_plural = 'Refresh Tokens Desktop'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['familia_id']),
            models.Index(fields=['token_hash']),
            models.Index(fields=['expires_at']),
        ]
    
    def __str__(self):
        estado = "Revocado" if self.revocado else ("Usado" if self.utilizado else "Activo")
        return f"RefreshToken {self.id} - {estado}"
    
    @staticmethod
    def hash_token(token: str) -> str:
        """Genera un hash SHA256 del token"""
        return hashlib.sha256(token.encode()).hexdigest()
    
    @classmethod
    def crear_token(cls, dispositivo, usuario, dias_expiracion=30, ip=None, user_agent=None, familia_id=None):
        """
        Crea un nuevo refresh token.
        
        Args:
            dispositivo: DispositivoAutorizado
            usuario: Usuario
            dias_expiracion: Días hasta expiración (default 30)
            ip: Dirección IP (opcional)
            user_agent: User Agent (opcional)
            familia_id: UUID de familia existente (para rotación)
        
        Returns:
            tuple: (RefreshTokenDesktop, token_plano)
        """
        # Generar token plano
        token_plano = str(uuid.uuid4())
        
        # Crear registro
        refresh_token = cls.objects.create(
            dispositivo=dispositivo,
            usuario=usuario,
            token_hash=cls.hash_token(token_plano),
            familia_id=familia_id or uuid.uuid4(),
            expires_at=timezone.now() + timezone.timedelta(days=dias_expiracion),
            ip_address=ip,
            user_agent=user_agent
        )
        
        return refresh_token, token_plano
    
    @classmethod
    def validar_y_rotar(cls, token_plano: str) -> tuple:
        """
        Valida un refresh token y genera uno nuevo (rotación).
        
        Si detecta reutilización de un token ya usado, revoca toda la familia.
        
        Returns:
            tuple: (nuevo_refresh_token, nuevo_token_plano, error_mensaje)
        """
        token_hash = cls.hash_token(token_plano)
        
        try:
            refresh_token = cls.objects.select_related('dispositivo', 'usuario').get(
                token_hash=token_hash
            )
        except cls.DoesNotExist:
            return None, None, "Token inválido"
        
        # Verificar revocación
        if refresh_token.revocado:
            return None, None, "Token revocado"
        
        # Verificar expiración
        if refresh_token.expires_at < timezone.now():
            return None, None, "Token expirado"
        
        # Verificar dispositivo activo
        if not refresh_token.dispositivo.esta_activo():
            return None, None, "Dispositivo no autorizado"
        
        # DETECCIÓN DE REUTILIZACIÓN
        if refresh_token.utilizado:
            # ¡Alerta! Token ya fue usado - posible robo
            # Revocar TODA la familia de tokens
            cls.objects.filter(familia_id=refresh_token.familia_id).update(revocado=True)
            return None, None, "Detección de reutilización de token - sesión invalidada"
        
        # Marcar como utilizado
        refresh_token.utilizado = True
        refresh_token.used_at = timezone.now()
        refresh_token.save(update_fields=['utilizado', 'used_at'])
        
        # Crear nuevo token (rotación) con la misma familia
        nuevo_token, nuevo_token_plano = cls.crear_token(
            dispositivo=refresh_token.dispositivo,
            usuario=refresh_token.usuario,
            familia_id=refresh_token.familia_id,
            ip=refresh_token.ip_address
        )
        
        return nuevo_token, nuevo_token_plano, None
    
    def revocar_familia(self):
        """Revoca todos los tokens de esta familia"""
        RefreshTokenDesktop.objects.filter(
            familia_id=self.familia_id
        ).update(revocado=True)
    
    def es_valido(self):
        """Verifica si el token es válido"""
        return (
            not self.revocado and 
            not self.utilizado and 
            self.expires_at > timezone.now() and
            self.dispositivo.esta_activo()
        )


class SyncLog(models.Model):
    """
    Modelo para registrar todas las sincronizaciones.
    Útil para debugging, auditoría y monitoreo.
    """
    
    TIPO_SYNC_CHOICES = [
        # Descarga (Server → Desktop)
        ('productos_down', 'Descarga de Productos'),
        ('categorias_down', 'Descarga de Categorías'),
        ('vendedores_down', 'Descarga de Vendedores'),
        ('configuracion_down', 'Descarga de Configuración'),
        
        # Subida (Desktop → Server)
        ('tickets_up', 'Subida de Tickets'),
        ('movimientos_caja_up', 'Subida de Movimientos de Caja'),
        ('cuadraturas_up', 'Subida de Cuadraturas'),
        
        # Otros
        ('full_sync', 'Sincronización Completa'),
        ('status_check', 'Verificación de Estado'),
    ]
    
    ESTADO_CHOICES = [
        ('INICIADO', 'Iniciado'),
        ('EN_PROCESO', 'En Proceso'),
        ('COMPLETADO', 'Completado'),
        ('FALLIDO', 'Fallido'),
        ('PARCIAL', 'Parcialmente Completado'),
    ]
    
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    
    # Relaciones
    dispositivo = models.ForeignKey(
        DispositivoAutorizado,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sync_logs',
        verbose_name='Dispositivo'
    )
    sucursal = models.ForeignKey(
        'Sucursal',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sync_logs',
        verbose_name='Sucursal'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sync_logs_desktop',
        verbose_name='Usuario'
    )
    
    # Tipo y estado
    tipo = models.CharField(
        max_length=50,
        choices=TIPO_SYNC_CHOICES,
        verbose_name='Tipo de Sincronización'
    )
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default='INICIADO',
        verbose_name='Estado'
    )
    
    # Estadísticas
    registros_enviados = models.IntegerField(
        default=0,
        verbose_name='Registros Enviados'
    )
    registros_procesados = models.IntegerField(
        default=0,
        verbose_name='Registros Procesados'
    )
    registros_fallidos = models.IntegerField(
        default=0,
        verbose_name='Registros Fallidos'
    )
    
    # Timing
    timestamp_inicio = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Inicio'
    )
    timestamp_fin = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fin'
    )
    duracion_ms = models.IntegerField(
        null=True, blank=True,
        verbose_name='Duración (ms)'
    )
    
    # Resultado
    exitoso = models.BooleanField(
        default=False,
        verbose_name='Exitoso'
    )
    error_mensaje = models.TextField(
        blank=True, null=True,
        verbose_name='Mensaje de Error'
    )
    detalles = models.JSONField(
        default=dict, blank=True,
        verbose_name='Detalles',
        help_text='JSON con detalles adicionales de la sync'
    )
    
    # Metadata
    version_app = models.CharField(
        max_length=20,
        blank=True, null=True,
        verbose_name='Versión App'
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True,
        verbose_name='Dirección IP'
    )
    
    class Meta:
        verbose_name = 'Log de Sincronización'
        verbose_name_plural = 'Logs de Sincronización'
        ordering = ['-timestamp_inicio']
        indexes = [
            models.Index(fields=['tipo', 'timestamp_inicio']),
            models.Index(fields=['dispositivo', 'timestamp_inicio']),
            models.Index(fields=['sucursal', 'timestamp_inicio']),
            models.Index(fields=['exitoso', 'timestamp_inicio']),
        ]
    
    def __str__(self):
        return f"Sync {self.get_tipo_display()} - {self.timestamp_inicio.strftime('%Y-%m-%d %H:%M')}"
    
    def finalizar(self, exitoso=True, error=None, registros_procesados=0, registros_fallidos=0, detalles=None):
        """
        Marca la sincronización como finalizada.
        """
        self.timestamp_fin = timezone.now()
        self.exitoso = exitoso
        self.registros_procesados = registros_procesados
        self.registros_fallidos = registros_fallidos
        
        if error:
            self.error_mensaje = str(error)
            self.estado = 'FALLIDO'
        elif registros_fallidos > 0:
            self.estado = 'PARCIAL'
        else:
            self.estado = 'COMPLETADO'
        
        if detalles:
            self.detalles = detalles
        
        # Calcular duración
        if self.timestamp_inicio:
            delta = self.timestamp_fin - self.timestamp_inicio
            self.duracion_ms = int(delta.total_seconds() * 1000)
        
        self.save()
        
        # Actualizar última sincronización del dispositivo
        if self.dispositivo and exitoso:
            self.dispositivo.registrar_sync()
    
    @classmethod
    def iniciar(cls, tipo, dispositivo=None, sucursal=None, usuario=None, registros_enviados=0, version_app=None, ip=None):
        """
        Inicia un nuevo registro de sincronización.
        """
        return cls.objects.create(
            tipo=tipo,
            dispositivo=dispositivo,
            sucursal=sucursal,
            usuario=usuario,
            registros_enviados=registros_enviados,
            version_app=version_app,
            ip_address=ip,
            estado='EN_PROCESO'
        )


class CuadraturaCaja(models.Model):
    """
    Modelo para cuadraturas/cierres de caja sincronizados desde desktop.
    """
    
    ESTADO_CHOICES = [
        ('ABIERTA', 'Abierta'),
        ('CERRADA', 'Cerrada'),
        ('CUADRADA', 'Cuadrada'),
        ('DESCUADRADA', 'Descuadrada'),
    ]
    
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    
    # Para sincronización offline
    local_id = models.UUIDField(
        unique=True,
        db_index=True,
        verbose_name='ID Local',
        help_text='UUID generado en app desktop'
    )
    
    # Relaciones
    sucursal = models.ForeignKey(
        'Sucursal',
        on_delete=models.CASCADE,
        related_name='cuadraturas_caja',
        verbose_name='Sucursal'
    )
    vendedor = models.ForeignKey(
        'Vendedor',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cuadraturas_caja',
        verbose_name='Vendedor/Cajero'
    )
    dispositivo = models.ForeignKey(
        DispositivoAutorizado,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cuadraturas_caja',
        verbose_name='Dispositivo'
    )
    
    # Datos de apertura
    fecha_apertura = models.DateTimeField(
        verbose_name='Fecha/Hora Apertura'
    )
    monto_apertura = models.IntegerField(
        default=0,
        verbose_name='Monto Apertura'
    )
    
    # Datos de cierre
    fecha_cierre = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha/Hora Cierre'
    )
    
    # Montos esperados (del sistema)
    efectivo_esperado = models.IntegerField(default=0, verbose_name='Efectivo Esperado')
    tarjeta_debito_esperado = models.IntegerField(default=0, verbose_name='Débito Esperado')
    tarjeta_credito_esperado = models.IntegerField(default=0, verbose_name='Crédito Esperado')
    transferencia_esperado = models.IntegerField(default=0, verbose_name='Transferencias Esperado')
    otros_esperado = models.IntegerField(default=0, verbose_name='Otros Esperado')
    
    # Montos contados (arqueo)
    efectivo_contado = models.IntegerField(default=0, verbose_name='Efectivo Contado')
    tarjeta_debito_contado = models.IntegerField(default=0, verbose_name='Débito Contado')
    tarjeta_credito_contado = models.IntegerField(default=0, verbose_name='Crédito Contado')
    transferencia_contado = models.IntegerField(default=0, verbose_name='Transferencias Contado')
    otros_contado = models.IntegerField(default=0, verbose_name='Otros Contado')
    
    # Diferencias
    diferencia_efectivo = models.IntegerField(default=0, verbose_name='Diferencia Efectivo')
    diferencia_total = models.IntegerField(default=0, verbose_name='Diferencia Total')
    
    # Estado
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default='ABIERTA',
        verbose_name='Estado'
    )
    
    # Conteo de documentos
    cantidad_tickets = models.IntegerField(default=0, verbose_name='Cantidad Tickets')
    cantidad_boletas = models.IntegerField(default=0, verbose_name='Cantidad Boletas')
    cantidad_facturas = models.IntegerField(default=0, verbose_name='Cantidad Facturas')
    
    # Movimientos de caja
    total_ingresos = models.IntegerField(default=0, verbose_name='Total Ingresos')
    total_egresos = models.IntegerField(default=0, verbose_name='Total Egresos')
    total_retiros = models.IntegerField(default=0, verbose_name='Total Retiros')
    
    # Observaciones
    observaciones = models.TextField(
        blank=True, null=True,
        verbose_name='Observaciones'
    )
    
    # Sync
    synced_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Sincronizado el'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Creado el'
    )
    
    class Meta:
        verbose_name = 'Cuadratura de Caja'
        verbose_name_plural = 'Cuadraturas de Caja'
        ordering = ['-fecha_apertura']
    
    def __str__(self):
        return f"Cuadratura {self.sucursal.alias} - {self.fecha_apertura.strftime('%Y-%m-%d')}"
    
    @property
    def total_esperado(self):
        return (
            self.efectivo_esperado +
            self.tarjeta_debito_esperado +
            self.tarjeta_credito_esperado +
            self.transferencia_esperado +
            self.otros_esperado
        )
    
    @property
    def total_contado(self):
        return (
            self.efectivo_contado +
            self.tarjeta_debito_contado +
            self.tarjeta_credito_contado +
            self.transferencia_contado +
            self.otros_contado
        )


class MovimientoCaja(models.Model):
    """
    Modelo para movimientos de caja (ingresos, egresos, retiros) sincronizados desde desktop.
    """
    
    TIPO_CHOICES = [
        ('APERTURA', 'Apertura de Caja'),
        ('INGRESO', 'Ingreso'),
        ('EGRESO', 'Egreso'),
        ('RETIRO', 'Retiro'),
        ('DEVOLUCION', 'Devolución'),
        ('CIERRE', 'Cierre de Caja'),
    ]
    
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    
    # Para sincronización offline
    local_id = models.UUIDField(
        unique=True,
        db_index=True,
        verbose_name='ID Local'
    )
    
    # Relaciones
    cuadratura = models.ForeignKey(
        CuadraturaCaja,
        on_delete=models.CASCADE,
        related_name='movimientos',
        verbose_name='Cuadratura'
    )
    sucursal = models.ForeignKey(
        'Sucursal',
        on_delete=models.CASCADE,
        related_name='movimientos_caja_desktop',
        verbose_name='Sucursal'
    )
    vendedor = models.ForeignKey(
        'Vendedor',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='movimientos_caja_desktop',
        verbose_name='Vendedor/Cajero'
    )
    
    # Datos del movimiento
    tipo = models.CharField(
        max_length=20,
        choices=TIPO_CHOICES,
        verbose_name='Tipo'
    )
    monto = models.IntegerField(
        verbose_name='Monto'
    )
    concepto = models.CharField(
        max_length=200,
        verbose_name='Concepto'
    )
    
    # Fechas
    fecha_hora = models.DateTimeField(
        verbose_name='Fecha/Hora'
    )
    
    # Referencia (ticket asociado si aplica)
    ticket_id = models.IntegerField(
        null=True, blank=True,
        verbose_name='ID Ticket Asociado'
    )
    
    # Observaciones
    observaciones = models.TextField(
        blank=True, null=True,
        verbose_name='Observaciones'
    )
    
    # Sync
    synced_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Sincronizado el'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Creado el'
    )
    
    class Meta:
        verbose_name = 'Movimiento de Caja'
        verbose_name_plural = 'Movimientos de Caja'
        ordering = ['-fecha_hora']
    
    def __str__(self):
        return f"{self.get_tipo_display()} - ${self.monto:,} - {self.fecha_hora.strftime('%H:%M')}"
