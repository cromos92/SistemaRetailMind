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
from decimal import Decimal, ROUND_HALF_UP

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
    ('CUMPLEANOS', 'Bono de cumpleaños'),
    ('REFERIDO', 'Bono por referido'),
    ('DESAFIO', 'Bono por desafío/promoción'),
]

NIVEL_CHOICES = [
    ('PLATA', 'Plata'),
    ('ORO', 'Oro'),
    ('PLATINO', 'Platino'),
]

# Tipos que CREAN un lote consumible (suman puntos con fecha de expiración)
TIPOS_LOTE = ('ACUMULACION', 'BIENVENIDA', 'CUMPLEANOS', 'REFERIDO', 'DESAFIO')

CANAL_MOV_CHOICES = [
    ('POS',    'Caja POS'),
    ('APP',    'App Móvil'),
    ('WEB',    'Ecommerce'),
    ('MANUAL', 'Ajuste Manual'),
    ('AUTO',   'Automático'),
]


class ProgramaFidelizacion(models.Model):
    """
    Configuración del programa de puntos. Se espera un único registro activo.
    Valores por defecto = estrategia "1 punto = $1":
    10 puntos por cada $1.000, 1 punto = $1, vence a 12 meses.
    """

    nombre = models.CharField(max_length=100, default='Programa de Puntos')

    # === ACUMULACIÓN ===
    puntos_por_monto = models.IntegerField(
        default=10,
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
        default=1,
        help_text="Cuántos pesos de descuento vale 1 punto al canjear",
    )
    minimo_canje_puntos = models.IntegerField(
        default=500,
        help_text="Puntos mínimos para poder canjear",
    )

    # === VIGENCIA ===
    vigencia_dias = models.IntegerField(
        default=365,
        help_text="Días hasta que un lote de puntos expira",
    )

    # === BIENVENIDA Y CUMPLEAÑOS ===
    puntos_bienvenida = models.IntegerField(
        default=3000,
        help_text="Puntos al crear la cuenta del cliente (0 = sin bono)",
    )
    puntos_cumpleanos = models.IntegerField(
        default=3000,
        help_text="Puntos de regalo en el mes de cumpleaños (0 = sin bono)",
    )
    bono_referido_padrino = models.IntegerField(
        default=2000,
        help_text="Puntos para quien invita, al concretarse la 1ª compra "
                  "del invitado (0 = referidos desactivados)",
    )
    bono_referido_ahijado = models.IntegerField(
        default=2000,
        help_text="Puntos para el invitado, al concretarse su 1ª compra",
    )

    # === NIVELES (Plata / Oro / Platino) ===
    tasa_plata = models.DecimalField(
        max_digits=5, decimal_places=2, default=3.00,
        help_text="% de cashback en puntos para nivel Plata",
    )
    tasa_oro = models.DecimalField(
        max_digits=5, decimal_places=2, default=4.00,
        help_text="% de cashback en puntos para nivel Oro",
    )
    tasa_platino = models.DecimalField(
        max_digits=5, decimal_places=2, default=5.00,
        help_text="% de cashback en puntos para nivel Platino",
    )
    umbral_oro = models.IntegerField(
        default=300000,
        help_text="Gasto acumulado en 12 meses (pesos) para alcanzar nivel Oro",
    )
    umbral_platino = models.IntegerField(
        default=800000,
        help_text="Gasto acumulado en 12 meses (pesos) para alcanzar nivel Platino",
    )

    # === CANJE INCREMENTAL ===
    incremento_canje = models.IntegerField(
        default=100,
        help_text="El canje debe ser múltiplo de este valor (0 = sin restricción)",
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
        Tasa plana (puntos_por_monto / monto_base_acumulacion). Se mantiene
        para compatibilidad; el hook de cobro usa `calcular_puntos_con_nivel`.
        """
        if not monto or monto <= 0 or self.monto_base_acumulacion <= 0:
            return 0
        import math
        bruto = (monto / self.monto_base_acumulacion) * self.puntos_por_monto
        if self.redondeo == 'CEIL':
            return int(math.ceil(bruto))
        if self.redondeo == 'ROUND':
            return int(math.floor(bruto + 0.5))
        return int(bruto)  # FLOOR

    def calcular_puntos_con_nivel(self, monto, nivel='PLATA'):
        """
        Regla de acumulación con niveles (Plata/Oro/Platino). Cashback en %.
        1 punto = 1 peso → puntos = monto × tasa%.
        """
        if not monto or monto <= 0:
            return 0
        import math
        tasa_map = {
            'PLATA': float(self.tasa_plata),
            'ORO': float(self.tasa_oro),
            'PLATINO': float(self.tasa_platino),
        }
        tasa = tasa_map.get(nivel, float(self.tasa_plata))
        bruto = monto * tasa / 100
        if self.redondeo == 'CEIL':
            return int(math.ceil(bruto))
        if self.redondeo == 'ROUND':
            return int(math.floor(bruto + 0.5))
        return int(bruto)  # FLOOR

    def calcular_nivel(self, gasto_12_meses):
        """Devuelve el nivel (PLATA/ORO/PLATINO) según el gasto acumulado."""
        if gasto_12_meses >= self.umbral_platino:
            return 'PLATINO'
        if gasto_12_meses >= self.umbral_oro:
            return 'ORO'
        return 'PLATA'

    @property
    def tasa_descuento_efectiva(self):
        """
        % del valor de la venta que se devuelve al cliente como puntos.
        Sirve para mostrar el costo del programa en la UI de configuración.
        Ej.: 10 pts/$1.000 y $1/pto => 10 * 1 / 1000 = 1%.
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

    # === NIVELES ===
    nivel = models.CharField(
        max_length=10,
        choices=NIVEL_CHOICES,
        default='PLATA',
        db_index=True,
        help_text="Nivel del cliente (Plata/Oro/Platino) según gasto 12 meses",
    )
    gasto_12_meses = models.IntegerField(
        default=0,
        help_text="Cache de gasto acumulado en los últimos 12 meses (pesos)",
    )
    codigo_referido = models.CharField(
        max_length=12,
        unique=True,
        null=True,
        blank=True,
        help_text="Código para invitar amigos (se genera al primer uso)",
    )
    nivel_actualizado = models.DateTimeField(
        null=True, blank=True,
        help_text="Última vez que se recalculó el nivel",
    )

    # === CUMPLEAÑOS ===
    anio_ultimo_bono_cumpleanos = models.IntegerField(
        null=True, blank=True,
        help_text="Año en que se otorgó el último bono de cumpleaños (evita duplicar)",
    )

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

    # === AUDITORÍA / TRAZABILIDAD ===
    rut_cliente = models.CharField(
        max_length=20, blank=True, null=True, db_index=True,
        help_text='RUT denormalizado del cliente (reportes sin join, prueba de presentación)',
    )
    canal = models.CharField(
        max_length=10, choices=CANAL_MOV_CHOICES, default='POS', db_index=True,
        help_text='Canal que originó el movimiento (POS/APP/WEB/MANUAL/AUTO)',
    )

    class Meta:
        ordering = ['fecha']
        verbose_name = 'Movimiento de Puntos'
        verbose_name_plural = 'Movimientos de Puntos'
        indexes = [
            models.Index(fields=['cuenta', 'fecha']),
            models.Index(fields=['cuenta', 'fecha_expiracion']),
            models.Index(fields=['ticket']),
            models.Index(fields=['idempotency_key']),
            models.Index(fields=['rut_cliente', 'tipo']),
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


PLATAFORMA_DISPOSITIVO_CHOICES = [
    ('android', 'Android'),
    ('ios', 'iOS'),
]


class DispositivoCliente(models.Model):
    """
    Dispositivo móvil registrado por la app "Mis Puntos" para notificaciones
    push (token FCM). Un cliente puede tener varios dispositivos; un token es
    único y se re-asigna si otro cliente inicia sesión en el mismo teléfono.
    """

    cliente = models.ForeignKey(
        'Cliente',
        on_delete=models.CASCADE,
        related_name='dispositivos',
    )
    token = models.CharField(
        max_length=512,
        unique=True,
        help_text="Token FCM del dispositivo",
    )
    plataforma = models.CharField(
        max_length=10,
        choices=PLATAFORMA_DISPOSITIVO_CHOICES,
    )
    activo = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(
        auto_now=True,
        help_text="Última vez que la app refrescó/confirmó el token",
    )

    class Meta:
        ordering = ['-last_seen']
        verbose_name = 'Dispositivo de Cliente (push)'
        verbose_name_plural = 'Dispositivos de Clientes (push)'
        indexes = [
            models.Index(fields=['cliente', 'activo']),
        ]

    def __str__(self):
        return f"{self.cliente_id} · {self.plataforma} · {self.token[:16]}…"


ESTADO_REFERIDO_CHOICES = [
    ('REGISTRADO', 'Registrado (espera 1ª compra)'),
    ('PAGADO', 'Bonos pagados'),
    ('ANULADO', 'Anulado'),
]


class Referido(models.Model):
    """
    Vínculo padrino → ahijado del programa "invita y gana". El ahijado ingresa
    el código del padrino en la app; los bonos (a ambos) se pagan recién cuando
    el ahijado concreta su PRIMERA compra con acumulación (anti-fraude básico:
    crear cuentas no paga nada).
    """

    ahijado = models.OneToOneField(
        Cliente,
        on_delete=models.CASCADE,
        related_name='referido_recibido',
        help_text="El invitado: solo puede ser referido una vez",
    )
    padrino = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name='referidos_hechos',
    )
    estado = models.CharField(
        max_length=12,
        choices=ESTADO_REFERIDO_CHOICES,
        default='REGISTRADO',
        db_index=True,
    )
    # Montos pagados (se congelan al pagar, por si el programa cambia después)
    puntos_padrino = models.IntegerField(default=0)
    puntos_ahijado = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    pagado_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Referido'
        verbose_name_plural = 'Referidos'
        indexes = [
            models.Index(fields=['padrino', 'estado']),
        ]

    def __str__(self):
        return f"{self.padrino_id} → {self.ahijado_id} ({self.estado})"


TIPO_DESAFIO_CHOICES = [
    ('COMPRAS_N', 'Cantidad de compras en el período'),
    ('MONTO_ACUMULADO', 'Monto comprado en el período ($)'),
]


class DesafioPromo(models.Model):
    """
    Desafío/promoción de fidelización: "haz N compras (o compra $X) entre
    fecha_inicio y fecha_fin y gana un bono de puntos". El progreso se calcula
    on-the-fly desde el ledger (ACUMULACION del período) — sin estado por
    cliente — y el bono se paga automáticamente al completarse (idempotente
    por `desafio:{id}:{cliente_id}`).
    """

    nombre = models.CharField(max_length=80)
    descripcion = models.TextField(
        blank=True,
        help_text="Texto que ve el cliente en la app",
    )
    tipo = models.CharField(max_length=20, choices=TIPO_DESAFIO_CHOICES)
    meta_valor = models.IntegerField(
        help_text="COMPRAS_N: nº de compras · MONTO_ACUMULADO: pesos",
    )
    bono_puntos = models.IntegerField(help_text="Puntos de premio al completar")

    fecha_inicio = models.DateField(db_index=True)
    fecha_fin = models.DateField(db_index=True, help_text="Inclusive")

    nivel_objetivo = models.CharField(
        max_length=10,
        choices=NIVEL_CHOICES,
        blank=True,
        default='',
        help_text="Vacío = para todos los niveles",
    )
    activo = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_inicio']
        verbose_name = 'Desafío / Promoción'
        verbose_name_plural = 'Desafíos / Promociones'

    def __str__(self):
        return f"{self.nombre} ({self.fecha_inicio} → {self.fecha_fin})"

    @property
    def vigente(self):
        hoy = timezone.localdate()
        return self.activo and self.fecha_inicio <= hoy <= self.fecha_fin


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


# ========== CUPONES DE DESCUENTO NOMINATIVOS ==========
#
# Un cupón es un código de descuento que la empresa REGALA a un cliente concreto
# (no está respaldado por puntos: es margen). Se emite desde el módulo de
# fidelización sobre la ficha de un cliente y sólo lo puede usar ese cliente,
# identificándose con su RUT en la caja.
#
# Regla de negocio dura (decisión 2026-08-05): en una venta hay UN SOLO
# beneficio. El cupón NO se combina ni con el descuento manual del cajero ni con
# un vale de puntos. Esa exclusión vive en `cupon_service.validar_cupon` y NO es
# un flag configurable a propósito: un checkbox que alguien destilde sería la
# forma más barata de regalar 5% + 5% + descuento manual sin que nadie se entere.

TIPO_VALOR_CUPON_CHOICES = [
    ('PORCENTAJE', '% sobre el total'),
    ('MONTO', 'Monto fijo ($)'),
]

ESTADO_CUPON_CHOICES = [
    ('PENDIENTE', 'Pendiente de uso'),
    ('CANJEADO', 'Canjeado'),
    ('EXPIRADO', 'Expirado'),
    ('ANULADO', 'Anulado'),
]

# Cuántos cupones de una campaña puede recibir un mismo cliente.
# UNICO es el default porque es la promesa típica: "5% por ser cliente nuevo",
# una vez en la vida. VIVO sirve para campañas recurrentes (ej. un cupón por mes)
# donde sólo se busca evitar que acumule varios sin usar.
LIMITE_CUPON_CLIENTE_CHOICES = [
    ('UNICO', 'Uno por cliente, para siempre'),
    ('VIVO', 'Uno sin usar a la vez (permite reemitir tras usarlo)'),
    ('SIN_LIMITE', 'Sin límite'),
]

ORIGEN_CUPON_CHOICES = [
    ('EMITIDO', 'Emitido al cliente'),
    ('PUBLICO', 'Canje de código público'),
]

# Alfabeto permitido en un código público. Sin espacios ni símbolos: el cajero
# lo teclea a mano y el cliente lo dicta por teléfono. Se admiten letras y
# dígitos para que el nombre de la campaña quepa dentro del código.
_CODIGO_PUBLICO_PERMITIDO = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'


def normalizar_codigo_publico(valor):
    """
    Normaliza un código público tecleado a mano: MAYÚSCULAS, sin espacios ni
    acentos, y sólo caracteres del alfabeto permitido.

    Devuelve `None` cuando queda vacío — NO cadena vacía. `codigo_publico` es
    `unique`, y en Postgres dos NULL no colisionan pero dos `''` sí: guardar la
    cadena vacía haría que la segunda campaña nominativa reventara con
    IntegrityError.
    """
    import unicodedata

    bruto = (valor or '').strip().upper()
    if not bruto:
        return None
    # 'GISÉ' -> 'GISE' (el cajero no va a tipear tildes en un código)
    bruto = ''.join(
        c for c in unicodedata.normalize('NFD', bruto)
        if unicodedata.category(c) != 'Mn'
    )
    limpio = ''.join(c for c in bruto if c in _CODIGO_PUBLICO_PERMITIDO)
    return limpio or None


def calcular_descuento_cupon(tipo_valor, valor, tope_descuento, monto):
    """
    Descuento en pesos que produce un cupón sobre `monto`.

    Función compartida por la campaña (preview) y el cupón emitido (canje real),
    para que la plata que se muestra al crear la campaña sea exactamente la que
    se descuenta en caja. Nunca devuelve más que `monto` (un cupón no deja el
    total en negativo ni convierte una venta en una devolución).
    """
    if not monto or monto <= 0:
        return 0
    if tipo_valor == 'MONTO':
        bruto = int(valor or 0)
    else:
        bruto = int(
            (Decimal(monto) * Decimal(valor or 0) / Decimal(100))
            .quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        )
    if tope_descuento:
        bruto = min(bruto, int(tope_descuento))
    return max(0, min(bruto, int(monto)))


class CampanaCupon(models.Model):
    """
    Plantilla de emisión: define QUÉ descuento se regala y bajo qué condiciones.

    Separada de `CuponCliente` porque emitir cientos de cupones repitiendo los
    parámetros a mano es inviable, y cambiar la vigencia obligaría a editar cada
    fila. Misma relación que `ProgramaFidelizacion` ↔ `CuentaPuntos`.
    """

    nombre = models.CharField(max_length=80)
    descripcion = models.TextField(blank=True, default='')

    # === Código público (campaña de código único compartido) ===
    # Si está poblado, la campaña deja de emitir cupones uno por uno: se publica
    # UN código (ej. "GISE2438") y cualquier cliente lo presenta en caja. El
    # límite `limite_por_cliente` sigue mandando, así que con UNICO cada RUT lo
    # usa una sola vez.
    #
    # Vacío (None) = campaña NOMINATIVA clásica: se emite desde la ficha y cada
    # cliente recibe su propio DC-XXXXXXXX. Los dos modos conviven.
    #
    # Es `null` y no `''` en el vacío a propósito: con unique=True, dos campañas
    # nominativas con cadena vacía chocarían entre sí (en Postgres los NULL no
    # colisionan). Ver `normalizar_codigo_publico`.
    codigo_publico = models.CharField(
        max_length=20, unique=True, null=True, blank=True, db_index=True,
        help_text='Código único que se publica y cualquier cliente presenta en '
                  'caja (ej. GISE2438). Vacío = campaña nominativa (un código '
                  'distinto por cliente).',
    )

    # Alcance por empresa (decisión de negocio): un cupón siempre nace atado a
    # una cadena y sólo se canjea en sus sucursales. NO nullable.
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.PROTECT,
        related_name='campanas_cupon',
    )

    tipo_valor = models.CharField(
        max_length=12, choices=TIPO_VALOR_CUPON_CHOICES, default='PORCENTAJE',
    )
    valor = models.DecimalField(
        max_digits=7, decimal_places=2,
        help_text='5.00 = 5% (PORCENTAJE) o $5.000 (MONTO)',
    )
    tope_descuento = models.IntegerField(
        null=True, blank=True,
        help_text='Techo en pesos del descuento (sólo PORCENTAJE). Vacío = sin techo',
    )
    monto_minimo = models.IntegerField(
        default=0, help_text='Compra mínima en pesos para poder usar el cupón',
    )

    vigencia_dias = models.IntegerField(
        default=30, help_text='Días que vive cada cupón desde que se emite',
    )
    fecha_inicio = models.DateField(
        db_index=True, help_text='Desde cuándo se pueden EMITIR cupones',
    )
    fecha_fin = models.DateField(
        db_index=True, help_text='Hasta cuándo se pueden emitir (inclusive)',
    )
    activo = models.BooleanField(default=True, db_index=True)

    limite_por_cliente = models.CharField(
        max_length=12,
        choices=LIMITE_CUPON_CLIENTE_CHOICES,
        default='UNICO',
        help_text='UNICO: un solo cupón por cliente en toda la campaña, aunque ya '
                  'lo haya usado. VIVO: sólo impide acumular varios sin usar.',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='campanas_cupon_creadas',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_inicio', 'nombre']
        verbose_name = 'Campaña de Cupones'
        verbose_name_plural = 'Campañas de Cupones'
        indexes = [
            models.Index(fields=['activo', 'fecha_inicio', 'fecha_fin']),
            models.Index(fields=['empresa', 'activo']),
        ]

    def __str__(self):
        etiqueta = (f"{self.valor}%" if self.tipo_valor == 'PORCENTAJE'
                    else f"${int(self.valor):,}".replace(',', '.'))
        return f"{self.nombre} ({etiqueta})"

    @property
    def vigente(self):
        """¿Se pueden emitir cupones hoy?"""
        hoy = timezone.localdate()
        return self.activo and self.fecha_inicio <= hoy <= self.fecha_fin

    @property
    def es_codigo_publico(self):
        """¿Esta campaña usa un código único compartido en vez de emitir uno a uno?"""
        return bool(self.codigo_publico)

    def calcular_descuento(self, monto):
        """Preview: cuánto descontaría esta campaña sobre `monto`."""
        return calcular_descuento_cupon(
            self.tipo_valor, self.valor, self.tope_descuento, monto,
        )


class CuponCliente(models.Model):
    """
    Cupón NOMINATIVO de un solo uso. Calcado de `CanjeVale`, con dos diferencias:
    no está respaldado por puntos (es margen que regala la empresa) y su dueño es
    parte de la validación, no un dato informativo.

    Al ser de un solo uso, este registro ES el ledger del uso: `ticket`,
    `monto_descuento`, `canjeado_en` y `usuario_canje` cuentan toda la historia.
    No hace falta una tabla de usos aparte.
    """

    campana = models.ForeignKey(
        CampanaCupon, on_delete=models.PROTECT, related_name='cupones',
    )
    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name='cupones_descuento',
    )
    # RUT denormalizado y NORMALIZADO (sin puntos ni guion): en caja se compara
    # contra `ticket.cliente_rut` sin join y sin depender del formato que tecleó
    # el cajero. Es EL gate del cupón — sin esto, el código sería al portador.
    rut_cliente = models.CharField(max_length=20, db_index=True)

    codigo = models.CharField(
        max_length=32, unique=True, db_index=True,
        help_text='Código de un solo uso que el cliente presenta en caja',
    )

    # De dónde salió esta fila:
    #   EMITIDO → se le entregó al cliente por adelantado (flujo nominativo).
    #   PUBLICO → nació EN EL CANJE, cuando el cliente presentó el código
    #             compartido de la campaña. La fila ES el registro de ese uso:
    #             sin ella no habría forma de saber que ese RUT ya lo usó.
    # En PUBLICO, `codigo` guarda el código realmente consumido
    # ("GISE2438-XK7M"): el sufijo existe sólo para respetar el unique.
    origen = models.CharField(
        max_length=10, choices=ORIGEN_CUPON_CHOICES, default='EMITIDO',
        db_index=True,
    )
    empresa = models.ForeignKey(
        Empresa, on_delete=models.PROTECT, related_name='cupones_cliente',
        help_text='Copia de campana.empresa: permite validar en caja sin join',
    )

    # === SNAPSHOT al emitir ===
    # Si mañana se edita la campaña, los cupones ya entregados NO mutan. Mismo
    # principio que `CanjeVale.valor_pesos` ("snapshot al generarse").
    tipo_valor = models.CharField(max_length=12, choices=TIPO_VALOR_CUPON_CHOICES)
    valor = models.DecimalField(max_digits=7, decimal_places=2)
    tope_descuento = models.IntegerField(null=True, blank=True)
    monto_minimo = models.IntegerField(default=0)

    estado = models.CharField(
        max_length=12, choices=ESTADO_CUPON_CHOICES, default='PENDIENTE',
        db_index=True,
    )
    expira_en = models.DateTimeField(db_index=True)

    # === Datos del canje efectivo (al validarlo en el POS) ===
    ticket = models.ForeignKey(
        'app.Ticket', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cupones_descuento',
    )
    monto_descuento = models.IntegerField(
        default=0, help_text='Pesos realmente descontados al canjear',
    )
    sucursal_canje = models.ForeignKey(
        Sucursal, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cupones_canjeados',
    )
    usuario_canje = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cupones_canjeados',
    )
    canjeado_en = models.DateTimeField(null=True, blank=True)

    idempotency_key = models.CharField(
        max_length=100, unique=True, null=True, blank=True, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cupones_emitidos',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Cupón de Cliente'
        verbose_name_plural = 'Cupones de Clientes'
        indexes = [
            models.Index(fields=['estado', 'expira_en']),
            models.Index(fields=['cliente', 'estado']),
            models.Index(fields=['rut_cliente', 'estado']),
        ]
        constraints = [
            # Un solo cupón VIVO por cliente y campaña. Los ya canjeados no
            # cuentan, así que una segunda campaña o una reemisión posterior
            # siguen siendo posibles.
            models.UniqueConstraint(
                fields=['campana', 'cliente'],
                condition=models.Q(estado='PENDIENTE'),
                name='uniq_cupon_vivo_por_cliente',
            ),
            # Un ticket no puede consumir dos cupones.
            models.UniqueConstraint(
                fields=['ticket'],
                condition=models.Q(estado='CANJEADO'),
                name='uniq_cupon_canjeado_por_ticket',
            ),
        ]

    def __str__(self):
        return f"{self.codigo} · {self.estado} · {self.cliente_id}"

    @property
    def vigente(self):
        return self.estado == 'PENDIENTE' and self.expira_en > timezone.now()

    def calcular_descuento(self, monto):
        """Descuento en pesos que aplica este cupón sobre `monto`."""
        return calcular_descuento_cupon(
            self.tipo_valor, self.valor, self.tope_descuento, monto,
        )
