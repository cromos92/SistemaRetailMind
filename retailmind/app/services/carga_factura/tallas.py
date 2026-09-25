"""
Tallas: cómo se lee una talla de factura, cómo se compara con las de una
ficha y cómo se escribe según una guía de talla.
"""
import re
from decimal import Decimal

# Talla numérica con sufijo opcional de la factura Nike: C (toddler), Y (youth).
RE_TALLA = re.compile(r'^(\d+(?:[.,]\d+)?)\s*([CY]?)$')

# Talla única/código con cero adelante ('00', '0'): no es un número.
RE_TALLA_CERO = re.compile(r'^0\d*$')

# Formato de talla que dejó la migración Laravel en fichas de calzado:
# '700' = 7,0 · '750' = 7,5 · '100' = 10 · '105' = 10,5. Solo los de 3
# dígitos: los de 2 ('40', '45') chocan con tallas europeas y no se tocan.
RE_TALLA_LEGACY = re.compile(r'^(?:([2-9])([05])0|(1[0-3])([05]))$')

COLUMNAS_GUIA = ('cl', 'us', 'eu', 'uk', 'br', 'cm')


def numero(valor):
    """Decimal → texto sin exponente ni ceros de más: 10 → '10', 7.50 → '7.5'."""
    return format(Decimal(valor).normalize(), 'f')


def talla_casa(talla):
    """Pasa una talla US de factura al formato con que están cargados los Nike.

    '7' → '7,0' · '7.5' → '7,5' · '10' → '10' · '11C' → '11' · '1.5Y' → '1,5'.
    Coma decimal, ',0' solo bajo 10 y sin el sufijo C/Y (así están las fichas
    JR/TD nativas de EDEL). Lo no numérico (S, M, L) y '00' quedan igual.
    """
    s = str(talla).strip().upper()
    m = RE_TALLA.match(s)
    if not m or RE_TALLA_CERO.match(s):
        return s
    valor = Decimal(m.group(1).replace(',', '.'))
    if valor == valor.to_integral_value():
        n = int(valor)
        return f'{n},0' if n < 10 else str(n)
    return numero(valor).replace('.', ',')


def clave_talla(talla):
    """Clave de equivalencia: '7' ≡ '7,0' ≡ '7.0' ≡ '7C'. Respeta '00'."""
    s = str(talla or '').strip().upper()
    if RE_TALLA_CERO.match(s):
        return s
    m = RE_TALLA.match(s)
    if not m:
        return s
    return numero(m.group(1).replace(',', '.'))


def clave_guia(talla):
    """Clave para buscar una talla US en una guía: número + 'C' si es de bebé.

    '7' ≡ '7.0' ≡ '7,0' · '11C' ≠ '11' (bebé vs adulto) · '1.5Y' ≡ '1.5' (las
    guías Nike escriben las juveniles sin la Y)."""
    s = str(talla or '').strip().upper()
    m = RE_TALLA.match(s)
    if not m or RE_TALLA_CERO.match(s):
        return (s, '')
    return (numero(m.group(1).replace(',', '.')), 'C' if m.group(2) == 'C' else '')


def es_talla_legacy(talla):
    return bool(RE_TALLA_LEGACY.match(str(talla or '').strip()))


def clave_talla_ficha(talla):
    """clave_talla() para una talla que YA está en una ficha: entiende además
    el formato legacy ('700' ≡ 7,0), para no crear un '7,0' duplicado en una
    ficha que ya tiene esa talla escrita a la antigua."""
    m = RE_TALLA_LEGACY.match(str(talla or '').strip())
    if not m:
        return clave_talla(talla)
    entero, medio = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return numero(f'{entero}.{medio}')


def preferencia_talla(texto):
    """Orden para elegir entre varias filas de la MISMA talla en una ficha:
    formato de la casa ('7,0') > otro formato ('7') > legacy ('700') > con
    espacios (la vista no la encontraría)."""
    if texto != texto.strip():
        return 3
    if es_talla_legacy(texto):
        return 2
    return 0 if texto == talla_casa(texto) else 1


def mapa_guia(guia, tipo_talla):
    """{clave de la talla US: texto en la columna del tipo} de una guía.

    La factura trae tallas US; si la ficha es de otro tipo (CL, EU…) se
    convierte por la fila de la guía, igual que el modal al cambiar el tipo
    de talla."""
    columna = str(tipo_talla or 'US').lower()
    if columna not in COLUMNAS_GUIA:
        columna = 'us'
    mapa = {}
    for item in guia.items.order_by('orden', 'id'):
        us = (item.us or '').strip()
        texto = (getattr(item, columna) or '').strip()
        if us and texto:
            mapa.setdefault(clave_guia(us), texto)
    return mapa
