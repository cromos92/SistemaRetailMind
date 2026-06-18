"""
Fidelización por puntos.

Modelo de datos:
- `ProgramaFidelizacion`: configuración (1 activo global). Centraliza la regla
  de cálculo de puntos en `calcular_puntos()`.
- `CuentaPuntos`: saldo de puntos por cliente (cache `saldo_puntos`
  denormalizado), 1 cuenta por Cliente.
- `MovimientoPuntos`: ledger inmutable. Las ACUMULACION/BIENVENIDA crean
  "lotes" con `fecha_expiracion`; el canje y la expiración consumen los lotes
  más antiguos primero (FIFO), igual que el stock por lotes del proyecto.

Decisión de negocio: los puntos son GLOBALES en toda la cadena (una sola bolsa
por cliente, sin partición por empresa).

Este modelo (CuentaPuntos + MovimientoPuntos) es la base que la futura app
móvil "Paola" / puntos.realsport.cl consumirá vía API REST, sin rediseño.
"""
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.conf import settings

from .organizacion import Sucursal, Empresa
from .crm import Cliente


# ========== CONSTANTES ==========

REDONDEO_CHOICES = [
    ('FLOOR', 'Hacia abajo (trunca)'),
    ('ROUND', 'Al más cercano'),
    ('CEIL', 'Hacia arriba'),
]

ACUMULA_SOBRE_CHOICES = [
    ('TOTAL', 'Total de la venta (con impuestos)'),
    ('NETO', 'Neto (sin impuestos)'),
]

TIPO_MOV_PUNTOS_CHOICES = [
    ('ACUMULACION', 'Acumulación por compra'),
    ('CANJE', 'Canje (descuento)'),
    ('EXPIRACION', 'Expiración de puntos'),
    ('AJUSTE', 'Ajuste manual'),
    ('REVERSA', 'Reversa (devolución/anulación de venta)'),
    ('BIENVENIDA', 'Bono de bienvenida'),
]

# Tipos que CREAN un lote consumible (suman puntos con fecha de expiración)
TIPOS_LOTE = ('ACUMULACION', 'BIENVENIDA')


class ProgramaFidelizacion(models.Model):
    """
    Configuración del programa de puntos. Se espera un único registro activo.
    Valores por defecto = estrategia de arranque investigada:
    1 punto por cada $1.000, 1 punto = $10, vence a 12 meses.
    """

    nombre = models.CharField(max_length=100, default='Programa de Puntos')

    # === ACUMULACIÓN ===
    puntos_por_monto = models.IntegerField(
        default=1,
        help_text="Puntos otorgados por cada `monto_base_acumulacion` pesos",
    )
    monto_base_acumulacion = models.IntegerField(
        default=1000,
        help_text="Monto en pesos que otorga `puntos_por_monto` puntos",
    )
    acumula_sobre = models.CharField(
        max_length=10,
        choices=ACUMULA_SOBRE_CHOICES,
        default='TOTAL',
    )
    redondeo = models.CharField(
        max_length=10,
        choices=REDONDEO_CHOICES,
        default='FLOOR',
    )

    # === CANJE ===
    valor_punto_en_pesos = models.IntegerField(
        default=10,
        help_text="Cuántos pesos de descuento vale 1 punto al canjear",
    )
    minimo_canje_puntos = models.IntegerField(
        default=50,
        help_text="Puntos mínimos para poder canjear",
    )

    # === VIGENCIA ===
    vigencia_dias = models.IntegerField(
        default=365,
        help_text="Días hasta que un lote de puntos expira",
    )

    # === BIENVENIDA ===
    puntos_bienvenida = models.IntegerField(
        default=20,
        help_text="Puntos al crear la cuenta del cliente (0 = sin bono)",
    )

    activo = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='programas_fidelizacion_modificados',
    )

    class Meta:
        ordering = ['-activo', '-updated_at']
        verbose_name = 'Programa de Fidelización'
        verbose_name_plural = 'Programas de Fidelización'

    def __str__(self):
        estado = 'activo' if self.activo else 'inactivo'
        return f"{self.nombre} ({estado})"

    def calcular_puntos(self, monto):
        """
        Regla central de acumulación. Devuelve los puntos enteros que otorga
        `monto` pesos según la tasa configurada y el modo de redondeo.

        Único punto de verdad: lo usan el hook de cobro, la API desktop y los
        reportes para que el resultado siempre coincida.
        """
        if not monto or monto <= 0 or self.monto_base_acumulacion <= 0:
            return 0
        import math
        bruto = (monto / self.monto_base_acumulacion) * self.puntos_por_monto
        if self.redondeo == 'CEIL':
            return int(math.ceil(bruto))
        if self.redondeo == 'ROUND':
            # Half-up explícito (evita banker's rounding de round(): round(10.5)==10).
            return int(math.floor(bruto + 0.5))
        return int(bruto)  # FLOOR

    @property
    def tasa_descuento_efectiva(self):
        """
        % del valor de la venta que se devuelve al cliente como puntos.
        Sirve para mostrar el costo del programa en la UI de configuración.
        Ej.: 1 pto/$1.000 y $10/pto => 1 * 10 / 1000 = 1%.
        """
        if self.monto_base_acumulacion <= 0:
            return 0.0
        return round(
            (self.puntos_por_monto * self.valor_punto_en_pesos)
            / self.monto_base_acumulacion * 100,
            2,
        )

    @classmethod
    def get_activo(cls):
        """Programa activo (o None). Helper para los servicios."""
        return cls.objects.filter(activo=True).order_by('-updated_at').first()


class CuentaPuntos(models.Model):
    """Saldo de puntos de un cliente. Una cuenta por Cliente."""

    cliente = models.OneToOneField(
        Cliente,
        on_delete=models.CASCADE,
        related_name='cuenta_puntos',
    )
    saldo_puntos = models.IntegerField(
        default=0,
        db_index=True,
        help_text="Saldo disponible (cache; derivado del ledger)",
    )
    activa = models.BooleanField(default=True)
    fecha_alta = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-saldo_puntos']
        verbose_name = 'Cuenta de Puntos'
        verbose_name_plural = 'Cuentas de Puntos'
        indexes = [
            models.Index(fields=['saldo_puntos']),
        ]

    def __str__(self):
        return f"{self.cliente.nombre_completo} - {self.saldo_puntos} pts"

    @property
    def saldo_calculado(self):
        """SUM del ledger; solo reconciliación, NO usar en hot path."""
        agregado = self.movimientos.aggregate(total=models.Sum('puntos'))
        return agregado['total'] or 0

    def valor_en_pesos(self, programa=None):
        """Valor del saldo en pesos según el programa activo."""
        programa = programa or ProgramaFidelizacion.get_activo()
        if not programa:
            return 0
        return self.saldo_puntos * programa.valor_punto_en_pesos


class MovimientoPuntos(models.Model):
    """
    Ledger inmutable de puntos. Las filas ACUMULACION/BIENVENIDA actúan como
    lotes con `fecha_expiracion`; las CANJE/EXPIRACION apuntan al lote que
    consumen vía `lote_origen` (FIFO por fecha más antigua).
    """

    cuenta = models.ForeignKey(
        CuentaPuntos,
        on_delete=models.CASCADE,
        related_name='movimientos',
    )
    tipo = models.CharField(max_length=20, choices=TIPO_MOV_PUNTOS_CHOICES)
    puntos = models.IntegerField(
        help_text="Positivo = acumula/bienvenida/reversa+; negativo = canje/expira",
    )
    saldo_resultante = models.IntegerField(
        help_text="Saldo de la cuenta tras aplicar este movimiento",
    )

    # === LOTE / VENCIMIENTO (FIFO) ===
    fecha_expiracion = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Solo en lotes (ACUMULACION/BIENVENIDA): cuándo expira",
    )
    puntos_consumidos_del_lote = models.IntegerField(
        default=0,
        help_text="Cuánto del lote ya se gastó/expiró (solo en lotes)",
    )
    lote_origen = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consumos',
        help_text="Lote que consume este CANJE/EXPIRACION",
    )

    # === VÍNCULOS ===
    ticket = models.ForeignKey(
        'app.Ticket',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_puntos',
    )
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_puntos',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_puntos',
    )

    # Idempotencia: evita doble acumulación ante reintentos del POS.
    idempotency_key = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    observaciones = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['fecha']
        verbose_name = 'Movimiento de Puntos'
        verbose_name_plural = 'Movimientos de Puntos'
        indexes = [
            models.Index(fields=['cuenta', 'fecha']),
            models.Index(fields=['cuenta', 'fecha_expiracion']),
            models.Index(fields=['ticket']),
            models.Index(fields=['idempotency_key']),
        ]

    def __str__(self):
        signo = '+' if self.puntos >= 0 else ''
        return f"{self.cuenta_id} {self.get_tipo_display()} {signo}{self.puntos}"

    @property
    def es_lote(self):
        return self.tipo in TIPOS_LOTE

    @property
    def saldo_lote(self):
        """Puntos aún disponibles en este lote (solo si es lote)."""
        if not self.es_lote:
            return 0
        return self.puntos - self.puntos_consumidos_del_lote

    @property
    def lote_expirado(self):
        if not self.es_lote or not self.fecha_expiracion:
            return False
        return self.fecha_expiracion < timezone.localdate()


def calcular_fecha_expiracion(programa, desde=None):
    """Fecha de expiración de un lote nuevo según la vigencia del programa."""
    desde = desde or timezone.localdate()
    if not programa or not programa.vigencia_dias:
        return None
    return desde + timedelta(days=programa.vigencia_dias)


ESTADO_RESERVA_CHOICES = [
    ('RESERVADA', 'Reservada'),
    ('CONFIRMADA', 'Confirmada'),
    ('LIBERADA', 'Liberada'),
    ('EXPIRADA', 'Expirada'),
]


class ReservaPuntos(models.Model):
    """
    Reserva (bloqueo lógico) de puntos para una compra híbrida desde la app móvil.

    NO debita el ledger: solo marca puntos como comprometidos mientras el cliente
    paga en el checkout del ecommerce. El "saldo disponible para reservar" =
    `CuentaPuntos.saldo_puntos` − Σ(reservas RESERVADA vivas). El débito real
    (movimiento CANJE) ocurre al CONFIRMAR, por los puntos REALMENTE aplicados
    (el descuento del pedido pagado). Si el pago se abandona, la reserva expira y
    NUNCA se debitan puntos. Cada reserva se materializa como un cupón de descuento
    en el ecommerce (`codigo_cupon` = ``PTS-<id>``).
    """

    cuenta = models.ForeignKey(
        CuentaPuntos, on_delete=models.CASCADE, related_name='reservas',
    )
    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name='reservas_puntos',
    )
    puntos_reservados = models.IntegerField()
    valor_pesos = models.IntegerField(
        help_text='Valor en pesos de los puntos reservados (snapshot al reservar)',
    )
    tienda = models.CharField(
        max_length=20,
        help_text='Código de la tienda/ecommerce destino (realsport/paola)',
    )
    empresa = models.ForeignKey(
        Empresa, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservas_puntos',
    )
    codigo_cupon = models.CharField(
        max_length=40, unique=True, null=True, blank=True, db_index=True,
        help_text='Código del cupón creado en el ecommerce (PTS-<id>)',
    )
    estado = models.CharField(
        max_length=12, choices=ESTADO_RESERVA_CHOICES, default='RESERVADA',
        db_index=True,
    )
    puntos_consumidos = models.IntegerField(
        default=0,
        help_text='Puntos realmente debitados al confirmar (por el descuento real)',
    )
    order_number = models.CharField(
        max_length=80, blank=True, default='',
        help_text='Número del pedido del ecommerce que usó la reserva',
    )
    expira_en = models.DateTimeField(db_index=True)
    idempotency_key = models.CharField(
        max_length=100, unique=True, null=True, blank=True, db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Reserva de Puntos'
        verbose_name_plural = 'Reservas de Puntos'
        indexes = [
            models.Index(fields=['estado', 'expira_en']),
            models.Index(fields=['cuenta', 'estado']),
        ]

    def __str__(self):
        return f"{self.codigo_cupon or self.pk} · {self.estado} · {self.puntos_reservados} pts"

    @property
    def vigente(self):
        return self.estado == 'RESERVADA' and self.expira_en > timezone.now()


ESTADO_VALE_CHOICES = [
    ('PENDIENTE', 'Pendiente de canje'),
    ('CANJEADO', 'Canjeado'),
    ('EXPIRADO', 'Expirado'),
    ('ANULADO', 'Anulado'),
]


class CanjeVale(models.Model):
    """
    Vale de canje "con código": el cliente convierte puntos en un código de
    descuento que presenta en tienda física para que el cajero lo aplique.

    Mismo principio de oro que `ReservaPuntos`: NO debita el ledger al generarse.
    Solo COMPROMETE puntos (el "saldo disponible" los descuenta) mientras el vale
    está PENDIENTE. El débito real (movimiento CANJE FIFO) ocurre en
    `canjear_vale()` cuando el cajero valida el código en el POS. Si el vale
    expira o se anula sin usarse, NUNCA se debitan puntos: vuelven al disponible.
    El código es de un solo uso e ininteligible (generado con `secrets`).
    """

    cuenta = models.ForeignKey(
        CuentaPuntos, on_delete=models.CASCADE, related_name='vales_canje',
    )
    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name='vales_canje',
    )
    puntos = models.IntegerField(help_text='Puntos que canjea el vale')
    valor_pesos = models.IntegerField(
        help_text='Valor en pesos del vale (snapshot al generarse)',
    )
    codigo = models.CharField(
        max_length=24, unique=True, db_index=True,
        help_text='Código de un solo uso que el cliente presenta en tienda',
    )
    estado = models.CharField(
        max_length=12, choices=ESTADO_VALE_CHOICES, default='PENDIENTE',
        db_index=True,
    )
    empresa = models.ForeignKey(
        Empresa, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vales_canje',
        help_text='Empresa/holding donde puede canjearse (opcional: global si vacío)',
    )

    # === Datos del canje efectivo (al validar en POS) ===
    sucursal_canje = models.ForeignKey(
        Sucursal, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vales_canjeados',
    )
    usuario_canje = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vales_canjeados',
    )
    ticket = models.ForeignKey(
        'app.Ticket', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vales_canje',
        help_text='Venta donde se aplicó el vale',
    )
    canjeado_en = models.DateTimeField(null=True, blank=True)

    expira_en = models.DateTimeField(db_index=True)
    idempotency_key = models.CharField(
        max_length=100, unique=True, null=True, blank=True, db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Vale de Canje'
        verbose_name_plural = 'Vales de Canje'
        indexes = [
            models.Index(fields=['estado', 'expira_en']),
            models.Index(fields=['cuenta', 'estado']),
        ]

    def __str__(self):
        return f"{self.codigo} · {self.estado} · {self.puntos} pts"

    @property
    def vigente(self):
        return self.estado == 'PENDIENTE' and self.expira_en > timezone.now()
