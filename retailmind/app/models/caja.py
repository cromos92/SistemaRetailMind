from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal
from .crm import Cliente
from .ventas import METODO_PAGO_TICKET_CHOICES

ESTADO_ARQUEO_CHOICES = [
    ('ABIERTO', 'En Proceso'),
    ('CERRADO', 'Cerrado'),
    ('CON_DIFERENCIAS', 'Con Diferencias'),
    ('DEPOSITO_DECLARADO', 'Depósito Declarado'),
    ('DEPOSITO_CONFIRMADO', 'Depósito Confirmado'),
    ('REVISADO', 'Revisado por Supervisor'),
]

RESULTADO_REVISION_CHOICES = [
    ('PENDIENTE', 'Pendiente de revisión'),
    ('OK', 'Aprobado sin observaciones'),
    ('OK_CON_OBS', 'Aprobado con observaciones'),
    ('REQUIERE_ACCION', 'Requiere acción correctiva'),
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
    
    # === CAJA CHICA / FONDO FIJO ===
    fondo_fijo_snapshot = models.IntegerField(
        default=0,
        help_text="Snapshot del fondo fijo de caja chica al momento del cierre"
    )

    # === METADATA DE CONTEO ===
    timestamp_conteo_fisico = models.DateTimeField(
        null=True, blank=True,
        help_text="Momento exacto en que se guardó el conteo físico"
    )
    modo_conteo = models.CharField(
        max_length=10,
        choices=[('DETALLADO', 'Detallado'), ('EXPRESS', 'Express')],
        default='DETALLADO',
        help_text="Modo usado para el conteo físico"
    )
    requiere_revision_express = models.BooleanField(
        default=False,
        help_text="True si el conteo fue en modo express (revisión recomendada)"
    )

    # === CONTROL Y ESTADO ===
    estado = models.CharField(max_length=20, choices=ESTADO_ARQUEO_CHOICES, default='ABIERTO')
    observaciones = models.TextField(blank=True, null=True)
    observaciones_diferencia = models.TextField(blank=True, null=True)
    categoria_diferencia = models.CharField(
        max_length=30, blank=True, null=True,
        choices=[
            ('ERROR_VUELTO', 'Error de vuelto'),
            ('BILLETE_FALSO', 'Billete falso'),
            ('DIFERENCIA_POS', 'Diferencia POS'),
            ('FALTANTE_SIN_EXPLICAR', 'Faltante sin explicar'),
            ('SOBRANTE', 'Sobrante'),
            ('OTRO', 'Otro'),
        ],
        help_text="Categoría de la diferencia (obligatorio si >$5,000)"
    )
    
    # === SUPERVISIÓN ===
    supervisor_revision = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='arqueos_supervisados'
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)
    observaciones_supervisor = models.TextField(blank=True, null=True)
    resultado_revision = models.CharField(
        max_length=20,
        choices=RESULTADO_REVISION_CHOICES,
        default='PENDIENTE',
        help_text="Resultado de la revisión del supervisor"
    )
    
    # === METADATA ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    # === CACHE DENORMALIZADO DE TOTALES DE DEPÓSITOS ===
    # Se recalcula via signal post_save/post_delete en DepositoBancario y el
    # management command `recalcular_cache_arqueos`. Permite que `listar_arqueos`
    # lea los totales sin JOINs adicionales (antes: 10+ queries por arqueo).
    cache_total_depositos = models.IntegerField(
        default=0,
        help_text='Suma de `depositos.monto` — actualizado por signal',
    )
    cache_total_dep_verificado = models.IntegerField(
        default=0,
        help_text='Suma de `depositos.monto_confirmado` verificados',
    )
    cache_total_dep_efectivo_verif = models.IntegerField(
        default=0,
        help_text='Total depósitos efectivo verificados (monto_confirmado)',
    )
    cache_total_dep_cheque_verif = models.IntegerField(
        default=0,
        help_text='Total depósitos cheque verificados (monto_confirmado)',
    )
    cache_depositos_declarados = models.IntegerField(
        default=0,
        help_text='Cantidad de depósitos con monto_declarado > 0',
    )
    cache_depositos_confirmados = models.IntegerField(
        default=0,
        help_text='Cantidad de depósitos con verificado=True',
    )
    cache_depositos_pendientes = models.IntegerField(
        default=0,
        help_text='Cantidad de depósitos verificado=False y monto_declarado > 0',
    )
    cache_depositos_actualizado = models.DateTimeField(
        null=True, blank=True,
        help_text='Última vez que el signal recalculó los contadores',
    )

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
        
        # Calcular diferencia (considerando fondo fijo de caja chica)
        self.diferencia_efectivo = self.total_efectivo_fisico - (self.total_efectivo_teorico + self.fondo_fijo_snapshot)
        
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
        """Total de depósitos bancarios realizados (lee cache denormalizado)."""
        # Delega al cache; si nunca se recalculó cae al sum() en vivo.
        if self.cache_depositos_actualizado is not None:
            return self.cache_total_depositos or 0
        return sum(d.monto for d in self.depositos.all())

    @property
    def efectivo_en_caja(self):
        """Calcula el efectivo que realmente queda en caja (después de depósitos y fondo fijo)"""
        return self.total_efectivo_fisico - self.total_depositos - self.fondo_fijo_snapshot

    @property
    def diferencia_efectivo_real(self):
        """Diferencia de efectivo considerando depósitos: (Efectivo en caja - Teórico)"""
        return self.efectivo_en_caja - self.total_efectivo_teorico

    @property
    def diferencia_total_real(self):
        """Diferencia total considerando efectivo en caja + diferencia POS"""
        return self.diferencia_efectivo_real + self.diferencia_transbank

    # === CONTROL POR DEPÓSITO BANCARIO (el control real) ===

    @property
    def total_depositado_efectivo_verificado(self):
        """Total de depósitos en efectivo verificados (lee cache denormalizado)."""
        if self.cache_depositos_actualizado is not None:
            total = self.cache_total_dep_efectivo_verif or 0
            if total == 0 and self.cache_depositos_confirmados and self.cache_total_depositos:
                return sum(
                    (d.monto_confirmado or d.monto) for d in self.depositos.filter(
                        verificado=True, tipo_medio='EFECTIVO'
                    )
                )
            return total
        return sum(
            (d.monto_confirmado or d.monto) for d in self.depositos.filter(
                verificado=True, tipo_medio='EFECTIVO'
            )
        )

    @property
    def total_depositado_cheque_verificado(self):
        """Total de depósitos en cheque verificados (lee cache denormalizado)."""
        if self.cache_depositos_actualizado is not None:
            total = self.cache_total_dep_cheque_verif or 0
            if total == 0 and self.cache_depositos_confirmados and self.cache_total_depositos:
                return sum(
                    (d.monto_confirmado or d.monto) for d in self.depositos.filter(
                        verificado=True, tipo_medio='CHEQUE'
                    )
                )
            return total
        return sum(
            (d.monto_confirmado or d.monto) for d in self.depositos.filter(
                verificado=True, tipo_medio='CHEQUE'
            )
        )

    @property
    def total_depositado_verificado(self):
        """Total de todos los depósitos verificados (lee cache denormalizado)."""
        if self.cache_depositos_actualizado is not None:
            total = self.cache_total_dep_verificado or 0
            if total == 0 and self.cache_depositos_confirmados and self.cache_total_depositos:
                return self.cache_total_depositos or 0
            return total
        return sum(
            (d.monto_confirmado or d.monto) for d in self.depositos.filter(verificado=True)
        )

    def recalcular_cache_depositos(self, save: bool = True) -> None:
        """
        Recalcula los campos `cache_*` a partir de los depósitos actuales
        en una sola query (aggregate con filter=Q) y los persiste.
        Llamado desde el signal `post_save`/`post_delete` de DepositoBancario
        y desde el management command de backfill.
        """
        from django.db.models import (
            Sum as _Sum, Count as _Count, Q as _Q, Case as _Case,
            When as _When, F as _F, IntegerField as _IntegerField,
        )

        monto_verificado = _Case(
            _When(monto_confirmado__gt=0, then=_F('monto_confirmado')),
            default=_F('monto'),
            output_field=_IntegerField(),
        )

        agg = self.depositos.aggregate(
            total_depositos=_Sum('monto'),
            total_verif=_Sum(monto_verificado, filter=_Q(verificado=True)),
            total_efectivo_verif=_Sum(
                monto_verificado,
                filter=_Q(verificado=True, tipo_medio='EFECTIVO'),
            ),
            total_cheque_verif=_Sum(
                monto_verificado,
                filter=_Q(verificado=True, tipo_medio='CHEQUE'),
            ),
            declarados=_Count('id', filter=_Q(monto_declarado__gt=0)),
            confirmados=_Count('id', filter=_Q(verificado=True)),
            pendientes=_Count(
                'id', filter=_Q(verificado=False, monto_declarado__gt=0)
            ),
        )
        self.cache_total_depositos = agg['total_depositos'] or 0
        self.cache_total_dep_verificado = agg['total_verif'] or 0
        self.cache_total_dep_efectivo_verif = agg['total_efectivo_verif'] or 0
        self.cache_total_dep_cheque_verif = agg['total_cheque_verif'] or 0
        self.cache_depositos_declarados = agg['declarados'] or 0
        self.cache_depositos_confirmados = agg['confirmados'] or 0
        self.cache_depositos_pendientes = agg['pendientes'] or 0
        self.cache_depositos_actualizado = timezone.now()
        if save:
            # Solo update_fields para no retriggerar la lógica costosa de save()
            ArqueoCaja.objects.filter(pk=self.pk).update(
                cache_total_depositos=self.cache_total_depositos,
                cache_total_dep_verificado=self.cache_total_dep_verificado,
                cache_total_dep_efectivo_verif=self.cache_total_dep_efectivo_verif,
                cache_total_dep_cheque_verif=self.cache_total_dep_cheque_verif,
                cache_depositos_declarados=self.cache_depositos_declarados,
                cache_depositos_confirmados=self.cache_depositos_confirmados,
                cache_depositos_pendientes=self.cache_depositos_pendientes,
                cache_depositos_actualizado=self.cache_depositos_actualizado,
            )

    @property
    def diferencia_deposito_vs_teorico(self):
        """Control real: ¿el dinero de ventas en efectivo llegó al banco?"""
        return self.total_depositado_efectivo_verificado - self.total_efectivo_teorico

    @property
    def diferencia_cheques_vs_teorico(self):
        """Control de cheques: ¿los cheques recibidos se depositaron?"""
        return self.total_depositado_cheque_verificado - self.total_cheque_teorico

    @property
    def estado_deposito(self):
        """Estado del depósito: COMPLETO, PARCIAL, SIN_DEPOSITO"""
        total_dep = self.total_depositado_verificado
        esperado = self.total_efectivo_teorico + self.total_cheque_teorico
        if esperado == 0:
            return 'SIN_DEPOSITO'
        if total_dep == 0:
            return 'SIN_DEPOSITO'
        if abs(total_dep - esperado) <= 1000:  # Tolerancia $1,000
            return 'COMPLETO'
        return 'PARCIAL'

    @property
    def dias_sin_revision(self):
        """Días desde el arqueo sin ser revisado"""
        if self.estado == 'REVISADO':
            return 0
        return (timezone.localdate() - self.fecha_arqueo).days

    @property
    def requiere_revision_urgente(self):
        """True si el arqueo tiene >3 días sin ser revisado"""
        return self.dias_sin_revision > 3 and self.estado != 'REVISADO'


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

TIPO_MEDIO_DEPOSITO_CHOICES = [
    ('EFECTIVO', 'Efectivo'),
    ('CHEQUE', 'Cheque'),
]


class GrupoDeposito(models.Model):
    """
    Comprobante bancario real que puede cubrir efectivo de múltiples días.
    Cada día incluido genera su propio DepositoBancario vinculado a este grupo,
    y la suma de esos depósitos debe coincidir con monto_total.
    """
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.CASCADE, related_name='grupos_deposito'
    )
    fecha_deposito = models.DateField(help_text="Fecha en que se realizó el depósito bancario")
    monto_total = models.IntegerField(help_text="Monto total del comprobante bancario")
    banco = models.CharField(max_length=20, choices=BANCO_CHOICES, default='ESTADO')
    numero_comprobante = models.CharField(max_length=50, blank=True)
    imagen_comprobante = models.ImageField(
        upload_to='comprobantes_bancarios/', blank=True, null=True
    )
    observaciones = models.TextField(blank=True)

    verificado = models.BooleanField(default=False)
    verificado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='grupos_deposito_verificados'
    )
    fecha_verificacion = models.DateTimeField(null=True, blank=True)

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='grupos_deposito_registrados'
    )
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'grupo_deposito'
        ordering = ['-fecha_deposito']
        verbose_name = 'Grupo de Depósito'
        verbose_name_plural = 'Grupos de Depósito'

    def __str__(self):
        return f"Grupo Depósito {self.fecha_deposito} - {self.get_banco_display()} - ${self.monto_total:,}"

    @property
    def suma_desglose(self):
        """Suma de los depósitos individuales vinculados a este grupo."""
        return sum(d.monto for d in self.depositos.all())

    @property
    def esta_cuadrado(self):
        """True si la suma del desglose por día coincide con el monto del comprobante."""
        return self.suma_desglose == self.monto_total

    @property
    def diferencia(self):
        return self.monto_total - self.suma_desglose

    @property
    def cantidad_dias(self):
        return self.depositos.values('arqueo__fecha_arqueo').distinct().count()


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
    
    # === RELACIÓN OPCIONAL CON GRUPO (depósito multi-día) ===
    grupo = models.ForeignKey(
        GrupoDeposito,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='depositos',
        help_text="Grupo de depósito cuando un solo comprobante cubre varios días"
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

    # === TIPO DE MEDIO DEPOSITADO ===
    tipo_medio = models.CharField(
        max_length=20,
        choices=TIPO_MEDIO_DEPOSITO_CHOICES,
        default='EFECTIVO',
        help_text="Tipo de medio depositado (efectivo o cheque)"
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
    observaciones_supervisor = models.TextField(
        blank=True,
        help_text="Observaciones del supervisor al confirmar o rechazar el depósito"
    )

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
        indexes = [
            # Agregaciones de depósitos por arqueo (listar_arqueos annotate)
            models.Index(fields=['arqueo', 'verificado'], name='depbanc_arq_verif_idx'),
            # Totales por tipo de medio dentro de un arqueo (efectivo / cheque)
            models.Index(
                fields=['arqueo', 'tipo_medio', 'verificado'],
                name='depbanc_arq_med_ver_idx',
            ),
            # Confirmaciones pendientes en indicadores mensuales
            models.Index(fields=['verificado', 'fecha_deposito'], name='depbanc_ver_fecha_idx'),
        ]

    def __str__(self):
        return f"Depósito {self.fecha_deposito} - {self.get_banco_display()} - ${self.monto:,}"


# ========== HISTORIAL DE REAPERTURAS DE ARQUEO ==========

class HistorialReaperturaArqueo(models.Model):
    """Registro de auditoría para reaperturas de arqueos cerrados."""
    arqueo = models.ForeignKey(
        ArqueoCaja, on_delete=models.CASCADE, related_name='historial_reaperturas'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reaperturas_realizadas'
    )
    fecha_reapertura = models.DateTimeField(auto_now_add=True)
    estado_anterior = models.CharField(max_length=20)
    justificacion = models.TextField(help_text="Motivo de la reapertura")

    class Meta:
        ordering = ['-fecha_reapertura']
        verbose_name = 'Historial de Reapertura'
        verbose_name_plural = 'Historial de Reaperturas'

    def __str__(self):
        return f"Reapertura {self.arqueo} por {self.usuario} - {self.fecha_reapertura:%d/%m/%Y %H:%M}"


# ========== LOG DE AUDITORÍA DE ACCIONES DE CAJA ==========

class LogAccionCaja(models.Model):
    """Registro de auditoría para todas las acciones sensibles del módulo de caja."""
    ACCIONES = [
        ('VER_CUADRATURA', 'Visualizar Cuadratura'),
        ('GENERAR_CUADRATURA', 'Generar Cuadratura'),
        ('GUARDAR_CONTEO', 'Guardar Conteo Físico'),
        ('CERRAR_ARQUEO', 'Cerrar Arqueo'),
        ('ELIMINAR_ARQUEO', 'Eliminar Arqueo'),
        ('REABRIR_ARQUEO', 'Reabrir Arqueo'),
        ('CORREGIR_EXPRESS', 'Corrección Express'),
        ('DECLARAR_DEPOSITO', 'Declarar Depósito'),
        ('CONFIRMAR_DEPOSITO', 'Confirmar Depósito'),
        ('REVISAR_ARQUEO', 'Revisar Arqueo'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='logs_caja'
    )
    accion = models.CharField(max_length=30, choices=ACCIONES)
    arqueo = models.ForeignKey(
        ArqueoCaja, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='logs_auditoria'
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    datos_extra = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Log de Acción de Caja'
        verbose_name_plural = 'Logs de Acciones de Caja'
        indexes = [
            models.Index(fields=['usuario', 'timestamp']),
            models.Index(fields=['accion', 'timestamp']),
            models.Index(fields=['arqueo', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.get_accion_display()} - {self.usuario.username} - {self.timestamp:%d/%m/%Y %H:%M}"


def log_accion_caja(request, accion, arqueo=None, **extra):
    """Helper para registrar acciones de auditoría de caja."""
    LogAccionCaja.objects.create(
        usuario=request.user,
        accion=accion,
        arqueo=arqueo,
        ip_address=request.META.get('REMOTE_ADDR'),
        datos_extra=extra or {}
    )


# ========== BITÁCORA DE OBSERVACIONES ==========

class ObservacionArqueo(models.Model):
    """Bitácora bidireccional de observaciones entre cajera y supervisor."""
    TIPO_CHOICES = [
        ('CAJERA', 'Observación de Cajera'),
        ('SUPERVISOR', 'Observación de Supervisor'),
        ('SISTEMA', 'Nota del Sistema'),
    ]
    arqueo = models.ForeignKey(
        ArqueoCaja, on_delete=models.CASCADE, related_name='bitacora'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='observaciones_arqueo'
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    texto = models.TextField()
    fecha = models.DateTimeField(auto_now_add=True)
    visible_para_cajera = models.BooleanField(default=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Observación de Arqueo'
        verbose_name_plural = 'Observaciones de Arqueo'

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.arqueo.fecha_arqueo} - {self.usuario}"


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
                            self.valor_cuota = self._calcular_valor_cuota()

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
                self.valor_cuota = self._calcular_valor_cuota()

            super().save(*args, **kwargs)

    def _calcular_valor_cuota(self):
        """
        Valor de cada cuota usando Decimal para evitar imprecisión de float
        en montos. Con interés: cuota fija (sistema francés). Sin interés:
        reparto simple. Resultado cuantizado a 2 decimales (campo DecimalField).
        """
        from decimal import Decimal, ROUND_HALF_UP

        monto = Decimal(self.monto_aprobado)
        cuotas = Decimal(self.numero_cuotas)
        if self.tasa_interes and self.tasa_interes > 0:
            tasa_mensual = Decimal(self.tasa_interes) / Decimal(100)
            factor = (Decimal(1) + tasa_mensual) ** int(self.numero_cuotas)
            valor = (monto * tasa_mensual * factor) / (factor - Decimal(1))
        else:
            valor = monto / cuotas
        return valor.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
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
        return (
            self.estado in ['ACTIVO', 'APROBADO'] and 
            self.fecha_vencimiento < timezone.localdate() and
            self.saldo_pendiente > 0
        )
    
    @property
    def dias_para_vencimiento(self):
        """Días restantes para el vencimiento"""
        if self.fecha_vencimiento:
            delta = self.fecha_vencimiento - timezone.localdate()
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
