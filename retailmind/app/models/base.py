"""
Base model utilities: validators and abstract base classes.
"""
import re
from django.core.validators import RegexValidator


RUT_REGEX_VALIDATOR = RegexValidator(
    regex=r'^(\d{1,2}\.\d{3}\.\d{3}-[\dkK]|\d{7,8}-[\dkK])$',
    message='Formato de RUT inválido. Use formato: 12345678-9 o 12.345.678-9'
)


def validar_rut_chileno(rut):
    """Validates a Chilean RUT algorithmically (modulus 11)."""
    if not rut:
        return False

    rut = rut.replace('.', '').replace('-', '').upper()
    if len(rut) < 2:
        return False

    numero = rut[:-1]
    dv = rut[-1]

    if not numero.isdigit():
        return False

    suma = 0
    multiplicador = 2
    for digito in reversed(numero):
        suma += int(digito) * multiplicador
        multiplicador = multiplicador + 1 if multiplicador < 7 else 2

    resto = suma % 11
    dv_calculado = 11 - resto

    if dv_calculado == 11:
        dv_calculado = '0'
    elif dv_calculado == 10:
        dv_calculado = 'K'
    else:
        dv_calculado = str(dv_calculado)

    return dv == dv_calculado
