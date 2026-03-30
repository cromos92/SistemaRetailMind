from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal
from .crm import Cliente
from .ventas import METODO_PAGO_TICKET_CHOICES

ESTADO_ARQUEO_CHOICES = [
    ('ABIERTO', 'En Proceso'),
    ('CERRADO', 'Finalizado'),
    ('CON_DIFERENCIAS', 'Con Diferencias'),
    ('REVISADO', 'Revisado por Supervisor'),
]

class ArqueoCaja(models.Model):
    """
    Modelo para registrar arqueos de caja diarios
    Guarda los mismos totales que se calculan en la cuadratura
    """
    # === INFORMACIÓN BÁSICA ===
    fecha_arqueo = models.DateField()
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name='arqueos_caja')
    usuario_responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='arqueos_realizados')
    
    # === TOTALES TEÓRICOS (CALCULADOS AUTOMÁTICAMENTE) ===
    # Tarjetas Comerciales (solo Hites)
    total_hites_teorico = models.IntegerField(default=0)
    total_tarjetas_comerciales_teorico = models.IntegerField(default=0)
    
    # Efectivo
    total_efectivo_teorico = models.IntegerField(default=0)
    
    # Venta Internet (Falabella, Paris, Ripley, MercadoPago, Klap)
    total_falabella_teorico = models.IntegerField(default=0)
    total_paris_teorico = models.IntegerField(default=0)
    total_ripley_teorico = models.IntegerField(default=0)
    total_mercadopago_teorico = models.IntegerField(default=0)
    total_klap_teorico = models.IntegerField(default=0)
    total_venta_internet_teorico = models.IntegerField(default=0)
    
    # Otros métodos
    total_tarjeta_debito_teorico = models.IntegerField(default=0)
    total_tarjeta_credito_teorico = models.IntegerField(default=0)
    total_transbank_teorico = models.IntegerField(default=0)
    total_transferencia_teorico = models.IntegerField(default=0)
    total_cheque_teorico = models.IntegerField(default=0)
    total_convenio_teorico = models.IntegerField(default=0)
    total_credito_trabajador_teorico = models.IntegerField(default=0)
    
    # Documentos
    total_tickets_teorico = models.IntegerField(default=0)
    total_boletas_electronicas_teorico = models.IntegerField(default=0)
    total_facturas_teorico = models.IntegerField(default=0)
    total_facturas_exentas_teorico = models.IntegerField(default=0)
    total_notas_credito_teorico = models.IntegerField(default=0)
    
    # Cantidades de documentos
    cantidad_tickets = models.IntegerField(default=0)
    cantidad_boletas_electronicas = models.IntegerField(default=0)
    cantidad_facturas = models.IntegerField(default=0)
    cantidad_facturas_exentas = models.IntegerField(default=0)
    
    # Total general
    venta_total_teorica = models.IntegerField(default=0)
    
    # === CONTEO FÍSICO (SOLO EFECTIVO) ===
    # Billetes
    billetes_20000 = models.IntegerField(default=0)
    billetes_10000 = models.IntegerField(default=0)
    billetes_5000 = models.IntegerField(default=0)
    billetes_2000 = models.IntegerField(default=0)
    billetes_1000 = models.IntegerField(default=0)
    
    # Monedas
    monedas_500 = models.IntegerField(default=0)
    monedas_100 = models.IntegerField(default=0)
    monedas_50 = models.IntegerField(default=0)
    monedas_10 = models.IntegerField(default=0)
    monedas_5 = models.IntegerField(default=0)
    monedas_1 = models.IntegerField(default=0)
    
    # Total físico calculado
    total_efectivo_fisico = models.IntegerField(default=0)
    
    # === DIFERENCIAS ===
    diferencia_efectivo = models.IntegerField(default=0)  # físico - teórico
    
    # === CIERRE POS (TRANSBANK) ===
    cierre_pos_fisico = models.IntegerField(default=0, help_text="Monto real del cierre de máquina POS (total)")
    cierre_debito_fisico = models.IntegerField(default=0, help_text="Monto real cierre débito Transbank")
    cierre_credito_fisico = models.IntegerField(default=0, help_text="Monto real cierre crédito Transbank")
    numero_lote_pos = models.CharField(max_length=50, blank=True, help_text="Número de lote del cierre POS")
    diferencia_transbank = models.IntegerField(default=0, help_text="Diferencia entre cierre POS físico y teórico")
    diferencia_debito = models.IntegerField(default=0, help_text="Diferencia débito: físico - teórico")
    diferencia_credito = models.IntegerField(default=0, help_text="Diferencia crédito: físico - teórico")
    
    # === CONTROL Y ESTADO ===
    estado = models.CharField(max_length=20, choices=ESTADO_ARQUEO_CHOICES, default='ABIERTO')
    observaciones = models.TextField(blank=True, null=True)
    observaciones_diferencia = models.TextField(blank=True, null=True)
    
    # === SUPERVISIÓN ===
    supervisor_revision = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='arqueos_supervisados'
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)
    observaciones_supervisor = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_arqueo', '-fecha_creacion']
        unique_together = ['fecha_arqueo', 'sucursal']  # Un arqueo por día por sucursal
        verbose_name = 'Arqueo de Caja'
        verbose_name_plural = 'Arqueos de Caja'
        indexes = [
            models.Index(fields=['fecha_arqueo', 'sucursal']),
            models.Index(fields=['estado', 'fecha_arqueo']),
            models.Index(fields=['diferencia_efectivo']),
        ]
    
    def __str__(self):
        return f"Arqueo {self.fecha_arqueo} - {self.sucursal.alias} - {self.get_estado_display()}"
    
    def save(self, *args, **kwargs):
        # Calcular total físico automáticamente
        self.total_efectivo_fisico = (
            (self.billetes_20000 * 20000) +
            (self.billetes_10000 * 10000) +
            (self.billetes_5000 * 5000) +
            (self.billetes_2000 * 2000) +
            (self.billetes_1000 * 1000) +
            (self.monedas_500 * 500) +
            (self.monedas_100 * 100) +
            (self.monedas_50 * 50) +
            (self.monedas_10 * 10) +
            (self.monedas_5 * 5) +
            (self.monedas_1 * 1)
        )
        
        # Calcular diferencia
        self.diferencia_efectivo = self.total_efectivo_fisico - self.total_efectivo_teorico
        
        # Auto-determinar estado
        if self.estado == 'ABIERTO' and self.fecha_cierre:
            if self.diferencia_efectivo == 0:
                self.estado = 'CERRADO'
            else:
                self.estado = 'CON_DIFERENCIAS'
        
        super().save(*args, **kwargs)
    
    @property
    def tiene_diferencias(self):
        """Retorna True si hay diferencias en efectivo"""
        return self.diferencia_efectivo != 0
    
    @property
    def diferencia_absoluta(self):
        """Retorna el valor absoluto de la diferencia"""
        return abs(self.diferencia_efectivo)
    
    @property
    def tipo_diferencia(self):
        """Retorna si es sobrante o faltante"""
        if self.diferencia_efectivo > 0:
            return 'SOBRANTE'
        elif self.diferencia_efectivo < 0:
            return 'FALTANTE'
        else:
            return 'EXACTO'
    
    @property
    def porcentaje_diferencia(self):
        """Calcula el porcentaje de diferencia respecto al teórico"""
        if self.total_efectivo_teorico == 0:
            return 0
        return (self.diferencia_absoluta / self.total_efectivo_teorico) * 100
    
    @property
    def requiere_supervision(self):
        """Determina si requiere supervisión (diferencia > $1000 o > 1%)"""
        return self.diferencia_absoluta > 1000 or self.porcentaje_diferencia > 1.0
    
    @property
    def total_depositos(self):
        """Calcula el total de depósitos bancarios realizados"""
        return sum([d.monto for d in self.depositos.all()])
    
    @property
    def efectivo_en_caja(self):
        """Calcula el efectivo que realmente queda en caja (después de depósitos)"""
        return self.total_efectivo_fisico - self.total_depositos
    
    @property
    def diferencia_efectivo_real(self):
        """Diferencia de efectivo considerando depósitos: (Efectivo en caja - Teórico)"""
        return self.efectivo_en_caja - self.total_efectivo_teorico
    
    @property
    def diferencia_total_real(self):
        """Diferencia total considerando efectivo en caja + diferencia POS"""
        return self.diferencia_efectivo_real + self.diferencia_transbank


# ========== MODELO PARA DEPÓSITOS BANCARIOS ==========

BANCO_CHOICES = [
    ('ESTADO', 'BancoEstado'),
    ('CHILE', 'Banco de Chile'),
    ('SANTANDER', 'Santander'),
    ('BCI', 'BCI'),
    ('SCOTIABANK', 'Scotiabank'),
    ('ITAU', 'Itaú'),
    ('SECURITY', 'Banco Security'),
    ('FALABELLA', 'Banco Falabella'),
    ('RIPLEY', 'Banco Ripley'),
    ('OTRO', 'Otro'),
]

class DepositoBancario(models.Model):
    """
    Modelo simple para registrar depósitos bancarios realizados
    Relacionado con el arqueo de caja del día
    """
    # === RELACIÓN CON ARQUEO ===
    arqueo = models.ForeignKey(
        ArqueoCaja, 
        on_delete=models.CASCADE, 
        related_name='depositos',
        help_text="Arqueo de caja al que pertenece este depósito"
    )
    
    # === DATOS DEL DEPÓSITO ===
    fecha_deposito = models.DateField(
        help_text="Fecha en que se realizó el depósito bancario"
    )
    monto = models.IntegerField(
        default=0,
        help_text="Monto depositado en pesos chilenos"
    )
    banco = models.CharField(
        max_length=20, 
        choices=BANCO_CHOICES,
        default='ESTADO',
        help_text="Banco donde se realizó el depósito"
    )
    numero_comprobante = models.CharField(
        max_length=50, 
        blank=True,
        help_text="Número del comprobante bancario (opcional)"
    )
    imagen_comprobante = models.ImageField(
        upload_to='comprobantes_bancarios/',
        blank=True,
        null=True,
        help_text="Foto o imagen del comprobante bancario"
    )
    observaciones = models.TextField(
        blank=True,
        help_text="Observaciones adicionales sobre el depósito"
    )
    
    # === DECLARACIÓN POR CAJERO ===
    monto_declarado = models.IntegerField(
        default=0,
        help_text="Monto que el cajero declara llevar a depositar"
    )
    declarado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='depositos_declarados',
        help_text="Cajero que declaró el envío del depósito"
    )
    fecha_declaracion = models.DateTimeField(
        null=True, blank=True,
        help_text="Fecha y hora en que el cajero hizo la declaración"
    )
    monto_confirmado = models.IntegerField(
        default=0,
        help_text="Monto confirmado por el supervisor al recibir el comprobante bancario"
    )

    # === VERIFICACIÓN POR SUPERVISOR ===
    verificado = models.BooleanField(default=False, help_text="Si el depósito fue verificado por un supervisor")
    verificado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='depositos_verificados',
        help_text="Supervisor que verificó el depósito"
    )
    fecha_verificacion = models.DateTimeField(null=True, blank=True)
    
    # === METADATOS ===
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT,
        help_text="Usuario que registró el depósito"
    )
    fecha_registro = models.DateTimeField(
        auto_now_add=True,
        help_text="Fecha y hora en que se registró el depósito"
    )
    
    @property
    def diferencia_deposito(self):
        """Diferencia entre monto confirmado y monto declarado (positivo = más de lo declarado)"""
        return self.monto_confirmado - self.monto_declarado

    @property
    def tiene_diferencia(self):
        return self.monto_declarado > 0 and self.monto_confirmado != self.monto_declarado

    class Meta:
        db_table = 'deposito_bancario'
        ordering = ['-fecha_deposito']
        verbose_name = 'Depósito Bancario'
        verbose_name_plural = 'Depósitos Bancarios'
    
    def __str__(self):
        return f"Depósito {self.fecha_deposito} - {self.get_banco_display()} - ${self.monto:,}"


# ========== MÓDULO DE CRÉDITOS A TRABAJADORES ==========

ESTADO_CREDITO_CHOICES = [
    ('PENDIENTE', 'Pendiente de Aprobación'),
    ('APROBADO', 'Aprobado'),
    ('ACTIVO', 'Activo'),
    ('PAGADO', 'Pagado Completamente'),
    ('VENCIDO', 'Vencido'),
    ('CANCELADO', 'Cancelado'),
    ('RECHAZADO', 'Rechazado'),
]

TIPO_CREDITO_CHOICES = [
    ('ANTICIPO_SUELDO', 'Anticipo de Sueldo'),
    ('PRESTAMO_EMPRESA', 'Préstamo de Empresa'),
    ('CREDITO_COMPRA', 'Crédito para Compra'),
    ('EMERGENCIA', 'Crédito de Emergencia'),
    ('OTRO', 'Otro'),
]

TIPO_BENEFICIARIO_CHOICES = [
    ('EMPLEADO', 'Empleado / Trabajador Interno'),
    ('CLIENTE_EXTERNO', 'Cliente Externo'),
]


class CreditoTrabajador(models.Model):
    """
    Modelo para gestionar créditos otorgados a clientes (empleados internos o externos).
    """
    # === RELACIONES ===
    beneficiario = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name='creditos_recibidos',
        null=True, blank=True,
        help_text="Cliente que recibe el crédito",
    )
    tipo_beneficiario = models.CharField(
        max_length=20,
        choices=TIPO_BENEFICIARIO_CHOICES,
        default='EMPLEADO',
    )
    empresa_origen = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='creditos_otorgados',
        help_text="Empresa que otorga el crédito"
    )
    sucursal = models.ForeignKey(
        Sucursal, 
        on_delete=models.CASCADE, 
        related_name='creditos_sucursal',
        help_text="Sucursal donde se otorga el crédito"
    )
    
    # === DATOS DEL CRÉDITO ===
    numero_credito = models.CharField(max_length=50, unique=True, help_text="Número único del crédito")
    tipo_credito = models.CharField(max_length=20, choices=TIPO_CREDITO_CHOICES, default='PRESTAMO_EMPRESA')
    monto_solicitado = models.DecimalField(max_digits=12, decimal_places=2, help_text="Monto solicitado")
    monto_aprobado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Monto aprobado")
    monto_pagado = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Monto pagado hasta la fecha")
    
    # === FECHAS ===
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    fecha_vencimiento = models.DateField(help_text="Fecha límite para pago")
    fecha_primer_pago = models.DateField(null=True, blank=True, help_text="Fecha del primer pago programado")
    
    # === ESTADO Y AUTORIZACIÓN ===
    estado = models.CharField(max_length=20, choices=ESTADO_CREDITO_CHOICES, default='PENDIENTE')
    autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='creditos_autorizados',
        help_text="Usuario que autorizó el crédito"
    )
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='creditos_solicitados',
        help_text="Usuario que registró la solicitud"
    )
    
    # === CONDICIONES DEL CRÉDITO ===
    tasa_interes = models.DecimalField(
        max_digits=5, decimal_places=2, 
        default=0, 
        help_text="Tasa de interés mensual (%)"
    )
    numero_cuotas = models.IntegerField(default=1, help_text="Número de cuotas para el pago")
    valor_cuota = models.DecimalField(
        max_digits=12, decimal_places=2, 
        null=True, blank=True,
        help_text="Valor de cada cuota (calculado automáticamente)"
    )
    
    # === OBSERVACIONES Y JUSTIFICACIÓN ===
    motivo_solicitud = models.TextField(help_text="Motivo o justificación del crédito")
    observaciones_solicitud = models.TextField(blank=True, null=True)
    observaciones_aprobacion = models.TextField(blank=True, null=True)
    observaciones_rechazo = models.TextField(blank=True, null=True)
    
    # === GARANTÍAS ===
    requiere_aval = models.BooleanField(default=False)
    aval_nombre = models.CharField(max_length=200, blank=True, null=True)
    aval_rut = models.CharField(max_length=20, blank=True, null=True)
    aval_telefono = models.CharField(max_length=20, blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_solicitud']
        verbose_name = 'Crédito'
        verbose_name_plural = 'Créditos'
        indexes = [
            models.Index(fields=['numero_credito']),
            models.Index(fields=['beneficiario', 'estado']),
            models.Index(fields=['empresa_origen', 'fecha_solicitud']),
            models.Index(fields=['estado', 'fecha_vencimiento']),
        ]
    
    @property
    def nombre_beneficiario(self):
        if self.beneficiario:
            return self.beneficiario.nombre_completo
        return 'Sin asignar'

    def __str__(self):
        return f"Crédito {self.numero_credito} - {self.nombre_beneficiario} - ${self.monto_aprobado or self.monto_solicitado:,}"
    
    def save(self, *args, **kwargs):
        # Generar número de crédito si no existe
        if not self.numero_credito:
            from django.utils import timezone
            from django.db import transaction, IntegrityError
            
            max_intentos = 10
            for intento in range(max_intentos):
                try:
                    with transaction.atomic():
                        fecha = timezone.now()
                        
                        # Buscar el último crédito del año para esta empresa
                        # Usar select_for_update() para bloquear y evitar race conditions
                        ultimo_credito = CreditoTrabajador.objects.filter(
                            empresa_origen=self.empresa_origen,
                            numero_credito__startswith=f"CR-{fecha.year}"
                        ).select_for_update().order_by('-numero_credito').first()
                        
                        if ultimo_credito:
                            try:
                                # Extraer el número del último crédito (formato: CR-2025-0001)
                                ultimo_num = int(ultimo_credito.numero_credito.split('-')[-1])
                                nuevo_numero = ultimo_num + 1
                            except (ValueError, IndexError):
                                # Si hay error al parsear, buscar siguiente disponible
                                nuevo_numero = 1
                        else:
                            nuevo_numero = 1
                        
                        # Verificar que no exista (doble check)
                        while CreditoTrabajador.objects.filter(
                            numero_credito=f"CR-{fecha.year}-{nuevo_numero:04d}"
                        ).exists():
                            nuevo_numero += 1
                            if nuevo_numero > 9999:
                                raise ValueError(f"No hay números disponibles para el año {fecha.year}")
                        
                        self.numero_credito = f"CR-{fecha.year}-{nuevo_numero:04d}"
                        
                        # Calcular valor de cuota si está aprobado
                        if self.estado == 'APROBADO' and self.monto_aprobado and self.numero_cuotas > 0:
                            if self.tasa_interes > 0:
                                # Cálculo con interés compuesto
                                tasa_mensual = float(self.tasa_interes) / 100
                                factor = (1 + tasa_mensual) ** self.numero_cuotas
                                self.valor_cuota = (float(self.monto_aprobado) * tasa_mensual * factor) / (factor - 1)
                            else:
                                # Sin interés
                                self.valor_cuota = float(self.monto_aprobado) / self.numero_cuotas
                        
                        super().save(*args, **kwargs)
                        break  # Si llegó aquí, el save fue exitoso
                        
                except IntegrityError as e:
                    if 'numero_credito' in str(e) and intento < max_intentos - 1:
                        # Si el error es por número duplicado, reintentar
                        continue
                    else:
                        # Si es otro error o ya no hay más intentos, lanzar la excepción
                        raise
        else:
            # Si ya tiene numero_credito, solo calcular cuota si es necesario
            if self.estado == 'APROBADO' and self.monto_aprobado and self.numero_cuotas > 0:
                if self.tasa_interes > 0:
                    tasa_mensual = float(self.tasa_interes) / 100
                    factor = (1 + tasa_mensual) ** self.numero_cuotas
                    self.valor_cuota = (float(self.monto_aprobado) * tasa_mensual * factor) / (factor - 1)
                else:
                    self.valor_cuota = float(self.monto_aprobado) / self.numero_cuotas
            
            super().save(*args, **kwargs)
    
    @property
    def saldo_pendiente(self):
        """Saldo pendiente de pago"""
        monto_base = self.monto_aprobado or self.monto_solicitado
        return float(monto_base) - float(self.monto_pagado)
    
    @property
    def porcentaje_pagado(self):
        """Porcentaje pagado del crédito"""
        monto_base = self.monto_aprobado or self.monto_solicitado
        if monto_base > 0:
            return (float(self.monto_pagado) / float(monto_base)) * 100
        return 0
    
    @property
    def esta_vencido(self):
        """Verifica si el crédito está vencido"""
        from django.utils import timezone
        return (
            self.estado in ['ACTIVO', 'APROBADO'] and 
            self.fecha_vencimiento < timezone.now().date() and
            self.saldo_pendiente > 0
        )
    
    @property
    def dias_para_vencimiento(self):
        """Días restantes para el vencimiento"""
        from django.utils import timezone
        if self.fecha_vencimiento:
            delta = self.fecha_vencimiento - timezone.now().date()
            return delta.days
        return None
    
    def aprobar_credito(self, usuario_autorizador, monto_aprobado=None, observaciones=None):
        """Aprobar el crédito"""
        from django.utils import timezone
        
        self.estado = 'APROBADO'
        self.autorizado_por = usuario_autorizador
        self.fecha_aprobacion = timezone.now()
        self.monto_aprobado = monto_aprobado or self.monto_solicitado
        
        if observaciones:
            self.observaciones_aprobacion = observaciones
        
        self.save()
    
    def rechazar_credito(self, usuario_autorizador, motivo_rechazo):
        """Rechazar el crédito"""
        self.estado = 'RECHAZADO'
        self.autorizado_por = usuario_autorizador
        self.observaciones_rechazo = motivo_rechazo
        self.save()
    
    def activar_credito(self):
        """Activar el crédito (cuando se entrega el dinero)"""
        if self.estado == 'APROBADO':
            self.estado = 'ACTIVO'
            self.save()


class PagoCreditoTrabajador(models.Model):
    """
    Modelo para registrar pagos/abonos a créditos de trabajadores
    """
    # === RELACIONES ===
    credito = models.ForeignKey(
        CreditoTrabajador, 
        on_delete=models.CASCADE, 
        related_name='pagos'
    )
    
    # === DATOS DEL PAGO ===
    numero_pago = models.CharField(max_length=50, help_text="Número del pago/abono")
    monto_pago = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_pago = models.DateField()
    metodo_pago = models.CharField(
        max_length=50, 
        choices=METODO_PAGO_TICKET_CHOICES,
        default='EFECTIVO'
    )
    
    # === DETALLES DEL PAGO ===
    numero_cuota = models.IntegerField(null=True, blank=True, help_text="Número de cuota si aplica")
    es_pago_total = models.BooleanField(default=False, help_text="Si es el pago total del crédito")
    referencia_pago = models.CharField(max_length=100, blank=True, null=True, help_text="Referencia del pago (voucher, etc.)")
    
    # === RESPONSABLES ===
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pagos_credito_registrados'
    )
    
    # === SUCURSAL DE COBRO ===
    sucursal_cobro = models.ForeignKey(
        'Sucursal',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pagos_credito_cobrados',
        help_text="Sucursal donde se registró el cobro"
    )

    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_pago', '-created_at']
        verbose_name = 'Pago de Crédito'
        verbose_name_plural = 'Pagos de Créditos'
        indexes = [
            models.Index(fields=['credito', 'fecha_pago']),
            models.Index(fields=['numero_pago']),
        ]
    
    def __str__(self):
        return f"Pago {self.numero_pago} - ${self.monto_pago:,} - {self.credito.numero_credito}"
    
    def save(self, *args, **kwargs):
        # Generar número de pago si no existe
        if not self.numero_pago:
            ultimo_numero = PagoCreditoTrabajador.objects.filter(
                credito=self.credito
            ).count()
            self.numero_pago = f"{self.credito.numero_credito}-P{ultimo_numero + 1:02d}"
        
        super().save(*args, **kwargs)
        
        # Actualizar monto pagado en el crédito
        total_pagado = self.credito.pagos.aggregate(
            total=models.Sum('monto_pago')
        )['total'] or 0
        
        self.credito.monto_pagado = total_pagado
        
        # Actualizar estado del crédito
        if self.credito.saldo_pendiente <= 0:
            self.credito.estado = 'PAGADO'
        elif self.credito.estado == 'APROBADO':
            self.credito.estado = 'ACTIVO'
        
        self.credito.save()


class FirmaCreditoTrabajador(models.Model):
    """
    Modelo para manejar firmas digitales de créditos
    """
    # === RELACIONES ===
    credito = models.OneToOneField(
        CreditoTrabajador, 
        on_delete=models.CASCADE, 
        related_name='firma'
    )
    
    # === DATOS DE LA FIRMA ===
    firmado_por_trabajador = models.BooleanField(default=False)
    fecha_firma_trabajador = models.DateTimeField(null=True, blank=True)
    firma_trabajador_data = models.TextField(blank=True, null=True, help_text="Datos de la firma digital del trabajador")
    
    firmado_por_autorizador = models.BooleanField(default=False)
    fecha_firma_autorizador = models.DateTimeField(null=True, blank=True)
    firma_autorizador_data = models.TextField(blank=True, null=True, help_text="Datos de la firma digital del autorizador")
    
    # === DATOS DEL AVAL (SI APLICA) ===
    firmado_por_aval = models.BooleanField(default=False)
    fecha_firma_aval = models.DateTimeField(null=True, blank=True)
    firma_aval_data = models.TextField(blank=True, null=True, help_text="Datos de la firma digital del aval")
    
    # === METADATA ===
    ip_firma_trabajador = models.GenericIPAddressField(null=True, blank=True)
    ip_firma_autorizador = models.GenericIPAddressField(null=True, blank=True)
    ip_firma_aval = models.GenericIPAddressField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Firma de Crédito'
        verbose_name_plural = 'Firmas de Créditos'
    
    def __str__(self):
        return f"Firmas - {self.credito.numero_credito}"
    
    @property
    def esta_completamente_firmado(self):
        """Verifica si todas las firmas requeridas están completas"""
        firmas_requeridas = [self.firmado_por_trabajador, self.firmado_por_autorizador]
        
        if self.credito.requiere_aval:
            firmas_requeridas.append(self.firmado_por_aval)
        
        return all(firmas_requeridas)
    
    def registrar_firma_trabajador(self, firma_data, ip_address=None):
        """Registrar firma del trabajador"""
        from django.utils import timezone
        
        self.firmado_por_trabajador = True
        self.fecha_firma_trabajador = timezone.now()
        self.firma_trabajador_data = firma_data
        self.ip_firma_trabajador = ip_address
        self.save()
    
    def registrar_firma_autorizador(self, firma_data, ip_address=None):
        """Registrar firma del autorizador"""
        from django.utils import timezone
        
        self.firmado_por_autorizador = True
        self.fecha_firma_autorizador = timezone.now()
        self.firma_autorizador_data = firma_data
        self.ip_firma_autorizador = ip_address
        self.save()
    
    def registrar_firma_aval(self, firma_data, ip_address=None):
        """Registrar firma del aval"""
        from django.utils import timezone
        
        self.firmado_por_aval = True
        self.fecha_firma_aval = timezone.now()
        self.firma_aval_data = firma_data
        self.ip_firma_aval = ip_address
        self.save()

