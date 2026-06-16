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
    validar_rut_chileno,
)
from app.services import fidelizacion_service


# ========== AUTH ==========

class SolicitarOTPSerializer(serializers.Serializer):
    rut = serializers.CharField(max_length=20)
    canal = serializers.ChoiceField(
        choices=['EMAIL', 'SMS'], default='EMAIL', required=False,
    )

    def validate_rut(self, value):
        if not validar_rut_chileno(value):
            raise serializers.ValidationError('El RUT no es válido.')
        return value


class VerificarOTPSerializer(serializers.Serializer):
    rut = serializers.CharField(max_length=20)
    codigo = serializers.CharField(min_length=6, max_length=6)

    def validate_rut(self, value):
        if not validar_rut_chileno(value):
            raise serializers.ValidationError('El RUT no es válido.')
        return value


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


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
