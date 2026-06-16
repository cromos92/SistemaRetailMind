"""
GiftCards (tarjetas de regalo con saldo en pesos).

Patrón de saldo: `saldo_actual` denormalizado en `GiftCard` (consulta en
caliente desde POS / API desktop) + `MovimientoGiftCard` como ledger inmutable
(fuente de verdad auditable). Mismo enfoque que `CreditoTrabajador` en
`app/models/caja.py`.

Decisión de negocio: las giftcards son GLOBALES en toda la cadena (no se
particionan por empresa); sirven en cualquier sucursal/empresa.
"""
import secrets

from django.db import models, transaction, IntegrityError
from django.utils import timezone
from django.conf import settings

from .organizacion import Sucursal, Vendedor
from .crm import Cliente


# ========== CONSTANTES ==========

ESTADO_GIFTCARD_CHOICES = [
    ('ACTIVA', 'Activa'),
    ('AGOTADA', 'Agotada'),
    ('ANULADA', 'Anulada'),
    ('VENCIDA', 'Vencida'),
    ('BLOQUEADA', 'Bloqueada'),
]

TIPO_MOV_GIFTCARD_CHOICES = [
    ('EMISION', 'Emisión'),
    ('CARGA', 'Carga / Recarga'),
    ('CONSUMO', 'Consumo (pago de venta)'),
    ('ANULACION', 'Anulación'),
    ('AJUSTE', 'Ajuste manual'),
    ('REVERSA', 'Reversa (devolución/anulación de venta)'),
]

# Alfabeto Crockford base32 sin caracteres ambiguos (I, O, 0, 1, U)
_CODIGO_ALFABETO = 'ABCDEFGHJKLMNPQRSTVWXYZ23456789'
GIFTCARD_PREFIJO = 'GC'
# Vigencia por defecto al emitir (meses) si no se especifica fecha
GIFTCARD_VIGENCIA_MESES_DEFAULT = 12


def generar_codigo_giftcard():
    """
    Genera un código legible y NO adivinable con formato GC-XXXX-XXXX-XXXX.
    Usa `secrets` (CSPRNG) sobre un alfabeto sin caracteres confusos.

    No garantiza unicidad por sí solo: `GiftCard.save` reintenta ante colisión
    (igual que `CreditoTrabajador.save`).
    """
    bloques = [
        ''.join(secrets.choice(_CODIGO_ALFABETO) for _ in range(4))
        for _ in range(3)
    ]
    return f"{GIFTCARD_PREFIJO}-{bloques[0]}-{bloques[1]}-{bloques[2]}"


class GiftCard(models.Model):
    """Tarjeta de regalo con saldo en pesos chilenos (IntegerField)."""

    codigo = models.CharField(
        max_length=30,
        unique=True,
        db_index=True,
        help_text="Código legible único (GC-XXXX-XXXX-XXXX)",
    )
    pin = models.CharField(
        max_length=8,
        blank=True,
        null=True,
        help_text="PIN opcional de redención (no obligatorio para arrancar)",
    )

    # === SALDOS (pesos) ===
    saldo_inicial = models.IntegerField(help_text="Monto con que se emitió")
    saldo_actual = models.IntegerField(
        default=0,
        db_index=True,
        help_text="Saldo disponible (cache; derivado del ledger)",
    )

    # === ESTADO ===
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_GIFTCARD_CHOICES,
        default='ACTIVA',
        db_index=True,
    )

    # === FECHAS ===
    fecha_emision = models.DateTimeField(auto_now_add=True)
    fecha_vencimiento = models.DateField(
        blank=True,
        null=True,
        help_text="Fecha límite de uso (null = sin vencimiento)",
    )

    # === RELACIONES ===
    sucursal_emision = models.ForeignKey(
        Sucursal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='giftcards_emitidas',
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='giftcards',
        help_text="Cliente comprador / titular (opcional)",
    )
    vendedor = models.ForeignKey(
        Vendedor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='giftcards_vendidas',
    )
    ticket_emision = models.ForeignKey(
        'app.Ticket',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='giftcards_vendidas',
        help_text="Venta en la que se vendió esta giftcard",
    )

    observaciones = models.TextField(blank=True, null=True)

    # === AUDITORÍA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='giftcards_creadas',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='giftcards_modificadas',
    )

    class Meta:
        ordering = ['-fecha_emision']
        verbose_name = 'Gift Card'
        verbose_name_plural = 'Gift Cards'
        indexes = [
            models.Index(fields=['codigo']),
            models.Index(fields=['estado', 'fecha_vencimiento']),
            models.Index(fields=['cliente', 'estado']),
        ]

    def __str__(self):
        return f"{self.codigo} (${self.saldo_actual:,} - {self.get_estado_display()})"

    def save(self, *args, **kwargs):
        # Generar código único si no existe, reintentando ante colisión.
        if not self.codigo:
            max_intentos = 10
            for intento in range(max_intentos):
                self.codigo = generar_codigo_giftcard()
                try:
                    with transaction.atomic():
                        super().save(*args, **kwargs)
                    return
                except IntegrityError as e:
                    if 'codigo' in str(e) and intento < max_intentos - 1:
                        self.codigo = ''
                        continue
                    raise
        else:
            super().save(*args, **kwargs)

    @property
    def saldo_calculado(self):
        """SUM del ledger; solo para reconciliación, NO usar en hot path."""
        agregado = self.movimientos.aggregate(total=models.Sum('monto'))
        return agregado['total'] or 0

    @property
    def esta_vencida(self):
        if not self.fecha_vencimiento:
            return False
        return self.fecha_vencimiento < timezone.localdate()


class MovimientoGiftCard(models.Model):
    """
    Ledger inmutable de movimientos de saldo de una giftcard.
    Nunca se hace update/delete: cada cambio de saldo es una fila nueva.
    """

    giftcard = models.ForeignKey(
        GiftCard,
        on_delete=models.CASCADE,
        related_name='movimientos',
    )
    tipo = models.CharField(max_length=20, choices=TIPO_MOV_GIFTCARD_CHOICES)
    monto = models.IntegerField(
        help_text="Positivo = carga/emisión/reversa; negativo = consumo/anulación",
    )
    saldo_resultante = models.IntegerField(
        help_text="Saldo de la giftcard tras aplicar este movimiento",
    )

    # === VÍNCULOS A LA VENTA ===
    ticket = models.ForeignKey(
        'app.Ticket',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_giftcard',
    )
    pago_ticket = models.ForeignKey(
        'app.TicketDetallePago',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_giftcard',
    )
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_giftcard',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_giftcard',
    )

    # Clave de idempotencia: evita doble-consumo ante reintentos del POS.
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
        verbose_name = 'Movimiento de Gift Card'
        verbose_name_plural = 'Movimientos de Gift Card'
        indexes = [
            models.Index(fields=['giftcard', 'fecha']),
            models.Index(fields=['ticket']),
            models.Index(fields=['idempotency_key']),
        ]

    def __str__(self):
        signo = '+' if self.monto >= 0 else ''
        return f"{self.giftcard.codigo} {self.get_tipo_display()} {signo}{self.monto:,}"
