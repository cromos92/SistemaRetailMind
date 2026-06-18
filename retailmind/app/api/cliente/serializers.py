"""
Serializers de la API de clientes finales (app móvil de fidelización).

Los de auth son `Serializer` planos; los de datos son `ModelSerializer` de solo
lectura. Reusan los validadores del dominio (`validar_rut_chileno`,
`fidelizacion_service.validar_email`/`normalizar_celular`) para no duplicar reglas.
"""
from rest_framework import serializers

from app.models import (
    Cliente,
    CuentaPuntos,
    MovimientoPuntos,
    GiftCard,
    CanjeVale,
    validar_rut_chileno,
)
from app.services import fidelizacion_service


# ========== AUTH ==========

def _validar_identificador(attrs):
    """
    Acepta RUT o email (al menos uno). Valida el RUT si viene y deja el
    identificador a usar en `attrs['identificador']` (el email tiene prioridad
    si se enviaran ambos). El email se valida con DRF (EmailField).
    """
    rut = (attrs.get('rut') or '').strip()
    email = (attrs.get('email') or '').strip()
    if not rut and not email:
        raise serializers.ValidationError('Ingresa tu RUT o tu correo.')
    if rut and not validar_rut_chileno(rut):
        raise serializers.ValidationError({'rut': 'El RUT no es válido.'})
    attrs['identificador'] = email if email else rut
    return attrs


class SolicitarOTPSerializer(serializers.Serializer):
    rut = serializers.CharField(max_length=20, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    canal = serializers.ChoiceField(
        choices=['EMAIL', 'SMS'], default='EMAIL', required=False,
    )

    def validate(self, attrs):
        return _validar_identificador(attrs)


class VerificarOTPSerializer(serializers.Serializer):
    rut = serializers.CharField(max_length=20, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    codigo = serializers.CharField(min_length=6, max_length=6)

    def validate(self, attrs):
        return _validar_identificador(attrs)


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class CotizarCanjeSerializer(serializers.Serializer):
    """Cuántos puntos quiere aplicar el cliente (para mostrar su valor en $)."""
    puntos = serializers.IntegerField(min_value=1)


class ReservarPuntosSerializer(serializers.Serializer):
    """Reserva de puntos para una compra en una tienda."""
    puntos = serializers.IntegerField(min_value=1)
    tienda = serializers.CharField(max_length=20)
    idempotency_key = serializers.CharField(
        max_length=100, required=False, allow_blank=True,
    )


class CheckoutItemSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=120)
    quantity = serializers.IntegerField(min_value=1)


class IniciarCheckoutSerializer(serializers.Serializer):
    """
    Inicia el checkout en el ecommerce. Con `reserva_id` aplica el cupón de
    puntos; sin él (compra 100% dinero) se requiere `tienda`.
    """
    reserva_id = serializers.IntegerField(required=False, allow_null=True)
    tienda = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    items = CheckoutItemSerializer(many=True)
    first_name = serializers.CharField(required=False, allow_blank=True, default='')
    last_name = serializers.CharField(required=False, allow_blank=True, default='')
    phone = serializers.CharField(required=False, allow_blank=True, default='')


class GenerarValeCanjeSerializer(serializers.Serializer):
    """Genera un vale de canje con código por `puntos`."""
    puntos = serializers.IntegerField(min_value=1)
    idempotency_key = serializers.CharField(
        max_length=100, required=False, allow_blank=True,
    )


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)


# ========== DATOS ==========

class PerfilClienteSerializer(serializers.ModelSerializer):
    """Perfil del cliente. Solo `email`/`celular` son editables (PATCH)."""

    nombre_completo = serializers.CharField(read_only=True)

    class Meta:
        model = Cliente
        fields = [
            'id', 'nombre', 'apellido', 'nombre_completo', 'rut',
            'email', 'celular', 'tipo_cliente',
        ]
        read_only_fields = ['id', 'nombre', 'apellido', 'rut', 'tipo_cliente']

    def validate_email(self, value):
        if value and not fidelizacion_service.validar_email(value):
            raise serializers.ValidationError('El email no tiene un formato válido.')
        return value

    def validate_celular(self, value):
        if not value:
            return value
        cel = fidelizacion_service.normalizar_celular(value)
        if not cel:
            raise serializers.ValidationError('El celular no es válido (ej: +56 9 1234 5678).')
        return cel


class CuentaPuntosSerializer(serializers.ModelSerializer):
    valor_pesos = serializers.SerializerMethodField()

    class Meta:
        model = CuentaPuntos
        fields = ['saldo_puntos', 'valor_pesos', 'updated_at']

    def get_valor_pesos(self, obj):
        return obj.valor_en_pesos()


class MovimientoPuntosSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)

    class Meta:
        model = MovimientoPuntos
        fields = [
            'tipo', 'tipo_display', 'puntos', 'saldo_resultante',
            'fecha', 'fecha_expiracion', 'observaciones',
        ]


class GiftCardClienteSerializer(serializers.ModelSerializer):
    """Gift card del cliente. NO expone pin/saldo_inicial/auditoría."""

    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    esta_vencida = serializers.BooleanField(read_only=True)

    class Meta:
        model = GiftCard
        fields = [
            'codigo', 'saldo_actual', 'estado', 'estado_display',
            'fecha_vencimiento', 'esta_vencida',
        ]


class CanjeValeSerializer(serializers.ModelSerializer):
    """Vale de canje del cliente (lo que muestra la app para presentar en tienda)."""

    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    vigente = serializers.BooleanField(read_only=True)

    class Meta:
        model = CanjeVale
        fields = [
            'id', 'codigo', 'puntos', 'valor_pesos', 'estado', 'estado_display',
            'vigente', 'expira_en', 'canjeado_en', 'created_at',
        ]
