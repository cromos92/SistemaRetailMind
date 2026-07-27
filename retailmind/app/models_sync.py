"""
Modelos para sincronización con App Desktop (POS Físico)
========================================================

Modelos:
- DispositivoAutorizado: Control de dispositivos autorizados para sync
- RefreshTokenDesktop: Tokens de refresco para autenticación desktop
- SyncLog: Registro de sincronizaciones
- DesafioPinMovil: 2º factor por correo del login de la app móvil de staff
"""

import uuid
import hashlib
import secrets
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
    # OJO — ESTE CAMPO YA NO AUTORIZA NADA.
    #
    # Nació como "este teléfono ya pasó el PIN, déjalo entrar sin PIN". Eso
    # tenía sentido cuando el login móvil además pedía contraseña: saltarse el
    # PIN dejaba igual un factor en pie. El login móvil pasó a ser SOLO PIN
    # (passwordless), y ahí esa misma lógica se convierte en "cualquiera que
    # escriba un nombre de usuario en este teléfono entra sin credencial
    # alguna". Por eso se eliminó `pin_esta_verificado()` y NADIE debe volver
    # a usar esta fecha para decidir un login.
    #
    # Su rol hoy es de REGISTRO: cuándo se enroló este teléfono. Sirve para
    # auditar y para reconocer un dispositivo nuevo. Por lo mismo ya no se
    # borra al suspender o revocar (ver `suspender()` / `revocar()`): borrarla
    # perdería el dato histórico y no protege de nada, porque quien corta el
    # acceso es `esta_activo()`, que el login consulta en cada intento.
    #
    # El `help_text` se deja EXACTAMENTE como estaba (sigue siendo cierto: es
    # cuándo se completó el segundo factor) para no generar una `AlterField`
    # que obligaría a una migración nueva por un cambio puramente cosmético.
    pin_verificado_en = models.DateTimeField(
        null=True, blank=True,
        verbose_name='PIN verificado el',
        help_text=(
            'Momento en que este dispositivo completó el segundo factor por '
            'correo (app móvil de staff). NULL = todavía no lo hizo.'
        )
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

    # AQUÍ VIVÍA `pin_esta_verificado()`. Se eliminó al pasar el login móvil a
    # passwordless: era la comprobación que permitía entregar tokens sin PIN a
    # un teléfono ya enrolado, y sin contraseña detrás eso equivale a no pedir
    # ninguna credencial. Si aparece la tentación de reponerla, la respuesta
    # es no: la comodidad de "no pedir PIN cada vez" la da la SESIÓN (el JWT
    # guardado que se refresca solo), no un atajo en el login.

    def suspender(self, motivo=None):
        """Suspende el dispositivo"""
        self.estado = 'SUSPENDIDO'
        self.activo = False
        # `pin_verificado_en` NO se toca: es el registro del enrolamiento, no
        # un permiso. Quien corta el acceso es `esta_activo()`, que el login
        # consulta en cada intento y que acaba de quedar en False.
        if motivo:
            self.descripcion = f"{self.descripcion or ''}\n[SUSPENDIDO]: {motivo}"
        self.save()

    def revocar(self, motivo=None):
        """Revoca permanentemente el dispositivo"""
        self.estado = 'REVOCADO'
        self.activo = False
        # Invalida todos los tokens
        self.refresh_tokens.all().update(revocado=True)
        # `pin_verificado_en` se conserva a propósito (ver `suspender()`).
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


class DesafioPinMovil(models.Model):
    """
    Desafío de 2º factor por correo para el login de la app móvil de staff
    (NEXO Staff). Un desafío = un intento de enrolar UN teléfono para UN
    usuario.

    Por qué un modelo y no el cache: el cache `default` del proyecto es
    LocMemCache (por proceso). Con varios workers de gunicorn el desafío
    creado en el worker A no existiría en el B y el flujo fallaría de forma
    intermitente. En BD es correcto siempre.

    Por qué guarda el PIN hasheado en vez de leer `Usuario.codigo_2fa`:
    `codigo_2fa` es un único campo compartido con el login web. Si la persona
    entra por la web mientras tiene un desafío móvil abierto, el código se
    pisa y el PIN que recibió por correo deja de servir. El hash aquí hace
    el desafío autocontenido. El PIN se sigue GENERANDO con los helpers ya
    existentes (`_obtener_codigo_2fa` / `generar_codigo_2fa`), así se respeta
    el ajuste `PIN_2FA_MODE`.

    El PIN nunca se guarda ni se devuelve en claro: sólo viaja por correo.
    """

    # Máximo de intentos de PIN por desafío. Un PIN de 6 dígitos son 1.000.000
    # de combinaciones: sin tope se fuerza por fuerza bruta.
    MAX_INTENTOS = 3
    # Segundos mínimos entre reenvíos del PIN (anti mail-bombing).
    SEGUNDOS_ENTRE_REENVIOS = 60
    # Envíos totales por desafío (el inicial + 2 reenvíos).
    MAX_ENVIOS = 3
    # Vida del PIN / del desafío.
    MINUTOS_EXPIRACION = 10

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='desafios_pin_movil',
        verbose_name='Usuario'
    )
    # El dispositivo TODAVÍA no existe cuando se crea el desafío: se registra
    # recién al verificar el PIN, para no ensuciar DispositivoAutorizado con
    # teléfonos que nunca completaron el segundo factor.
    device_id = models.UUIDField(db_index=True, verbose_name='ID del Dispositivo')
    sucursal = models.ForeignKey(
        'Sucursal',
        on_delete=models.CASCADE,
        related_name='desafios_pin_movil',
        verbose_name='Sucursal'
    )

    # Datos del alta del dispositivo, congelados en el paso 1 para no volver a
    # confiar en lo que mande el cliente en el paso 2.
    device_name = models.CharField(max_length=100, blank=True, default='')
    sistema_operativo = models.CharField(max_length=100, blank=True, default='')
    version_app = models.CharField(max_length=20, blank=True, default='')

    # Token opaco del desafío (se guarda SOLO el hash, como RefreshTokenDesktop).
    token_hash = models.CharField(
        max_length=64, unique=True, db_index=True,
        verbose_name='Hash del token de desafío'
    )
    # PIN hasheado (SHA-256). Nunca en claro.
    pin_hash = models.CharField(max_length=64, verbose_name='Hash del PIN')

    intentos = models.IntegerField(default=0, verbose_name='Intentos de PIN')
    envios = models.IntegerField(default=1, verbose_name='Correos enviados')
    consumido = models.BooleanField(
        default=False, verbose_name='Consumido',
        help_text='Un desafío es de un solo uso: se marca al verificar OK o al agotar los intentos'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField(db_index=True, verbose_name='Expira el')
    ultimo_envio_en = models.DateTimeField(verbose_name='Último envío')
    consumido_en = models.DateTimeField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = 'Desafío PIN Móvil'
        verbose_name_plural = 'Desafíos PIN Móvil'
        ordering = ['-created_at']
        # Los nombres van explícitos y son EXACTAMENTE los que creó la
        # migración 0193 (ya aplicada en producción). Sin `name=`, Django
        # calcula un nombre con hash distinto al que hay en la base y
        # `makemigrations` propone renombrar los tres índices en cada corrida.
        indexes = [
            models.Index(fields=['token_hash'], name='app_desafio_token_h_idx'),
            models.Index(fields=['expira_en'], name='app_desafio_expira_idx'),
            models.Index(fields=['usuario', 'device_id'], name='app_desafio_user_dev_idx'),
        ]

    def __str__(self):
        return f"Desafío PIN {self.usuario_id} / {self.device_id}"

    # ---------- helpers ----------

    @staticmethod
    def hash_valor(valor: str) -> str:
        return hashlib.sha256(str(valor).encode()).hexdigest()

    @property
    def esta_expirado(self) -> bool:
        return timezone.now() >= self.expira_en

    @property
    def intentos_restantes(self) -> int:
        return max(0, self.MAX_INTENTOS - (self.intentos or 0))

    @property
    def segundos_para_reenvio(self) -> int:
        transcurridos = (timezone.now() - self.ultimo_envio_en).total_seconds()
        return max(0, int(self.SEGUNDOS_ENTRE_REENVIOS - transcurridos))

    @classmethod
    def crear(cls, usuario, device_id, sucursal, pin_plano, device_name='',
              sistema_operativo='', version_app='', ip=None, user_agent=None,
              token_plano=None):
        """
        Crea un desafío y devuelve (desafio, token_plano).

        Invalida los desafíos abiertos previos del mismo usuario+dispositivo:
        sólo puede haber uno vivo, así un atacante no acumula desafíos para
        multiplicar los intentos.

        `token_plano` permite que quien llama traiga el token ya generado. Lo
        usa el login móvil: la respuesta al cliente sale ANTES de que la fila
        exista (todo el trabajo de base se hace fuera del hilo de la petición
        para no delatar por tiempo si el usuario existe), así que el token
        tiene que nacer en el hilo que responde. Si no se pasa, se genera aquí
        como siempre.
        """
        ahora = timezone.now()
        cls.objects.filter(
            usuario=usuario, device_id=device_id, consumido=False
        ).update(consumido=True, consumido_en=ahora)

        token_plano = token_plano or secrets.token_urlsafe(32)
        desafio = cls.objects.create(
            usuario=usuario,
            device_id=device_id,
            sucursal=sucursal,
            device_name=(device_name or '')[:100],
            sistema_operativo=(sistema_operativo or '')[:100],
            version_app=(version_app or '')[:20],
            token_hash=cls.hash_valor(token_plano),
            pin_hash=cls.hash_valor(pin_plano),
            expira_en=ahora + timezone.timedelta(minutes=cls.MINUTOS_EXPIRACION),
            ultimo_envio_en=ahora,
            ip_address=ip,
            user_agent=user_agent,
        )
        return desafio, token_plano

    @classmethod
    def buscar_vigente(cls, token_plano):
        """
        Devuelve el desafío por su token opaco, sin juzgar expiración ni
        intentos (de eso se encarga la vista, que tiene que distinguir los
        códigos de error del contrato).
        """
        if not token_plano:
            return None
        return cls.objects.select_related('usuario', 'sucursal').filter(
            token_hash=cls.hash_valor(token_plano), consumido=False
        ).first()

    def registrar_reenvio(self, pin_plano):
        """Actualiza el desafío tras reenviar el PIN. NO reinicia `intentos`."""
        ahora = timezone.now()
        self.pin_hash = self.hash_valor(pin_plano)
        self.envios = (self.envios or 0) + 1
        self.ultimo_envio_en = ahora
        self.expira_en = ahora + timezone.timedelta(minutes=self.MINUTOS_EXPIRACION)
        self.save(update_fields=['pin_hash', 'envios', 'ultimo_envio_en', 'expira_en'])

    def consumir(self):
        self.consumido = True
        self.consumido_en = timezone.now()
        self.save(update_fields=['consumido', 'consumido_en'])
