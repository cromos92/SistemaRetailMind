"""
Perfiles de carga por marca: lo que cambia de una marca a otra al cargar sus
facturas (tipo de talla y guías, siglas de género en la descripción, regla de
precio, tablas de referencia para crear las guías).

El JSON de la factura manda sobre el perfil: "tipo_talla" y "guias_talla" del
JSON pisan los del perfil, y las opciones de línea de comando pisan la regla
de precio. Una marca sin perfil usa DEFECTO, que reproduce el comportamiento
genérico que tenía el comando antes de existir los perfiles.

Para sumar una marca: crear su Perfil y agregarlo a PERFILES (clave = nombre
canónico de la marca, ver clave_marca()).
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal


def clave_marca(valor):
    """'NIKE' ≡ 'Nike' ≡ 'NIKE .' (misma clave que unificar_marcas_duplicadas)."""
    return re.sub(r'[^A-Z0-9]', '', str(valor or '').upper())


# Prefijo de la descripción que declara el género en la factura Nike
# (W/WMNS = mujer, M = hombre).
_RE_GENERO_NIKE = re.compile(r'^(W|WMNS|W MNS|M)\b')


@dataclass(frozen=True)
class Perfil:
    marca: str
    # Tipo de talla de las fichas nuevas y guías por tipo de línea
    # (HOMBRE / MUJER / UNISEX / INFANTIL / DEFAULT → nombre de GuiaTalla).
    tipo_talla: str = 'CL'
    guias: dict = field(default_factory=dict)
    # Letras que marcan talla de niño en la factura (11C, 1.5Y): la línea usa
    # la guía INFANTIL.
    sufijos_nino: tuple = ('C', 'Y')
    # Si la descripción declara el género, no se infiere por otros colores.
    re_genero_explicito: re.Pattern = _RE_GENERO_NIKE
    # Separador modelo-color del código ('DV4342-002' → 'DV4342'): el género
    # de un código nuevo se toma de otros colores del mismo modelo. None = no.
    separador_modelo: str = '-'
    # ¿El color es parte de la identidad? En Nike el código YA lleva el color
    # (HQ6034-001) y basta artículo + marca. En marcas como Chalada el código
    # es el modelo ('12-REBI-1') y cada color es otra ficha: con el color
    # conocido solo calzan las fichas de ese color; si no hay, es variante nueva.
    identidad_color: bool = True
    # Regla de precio de venta: costo × factor_bajo bajo el umbral, × factor_alto
    # desde él; factor_piso = mínimo que usa ajustar_productos_factura.
    umbral_costo: int = 40000
    factor_bajo: Decimal = Decimal('1.85')
    factor_alto: Decimal = Decimal('1.8')
    factor_piso: Decimal = Decimal('1.9')
    color_defecto: str = 'MULTI'
    # Tablas para CREAR guías que no existen: {nombre guía (mayúsculas):
    # [(orden, cl, us, eu, uk, br, cm), ...]}.
    referencia_guias: dict = field(default_factory=dict)
    # Pistas para leer las facturas de la marca (van al pedido de lectura del PDF).
    pistas_lectura: str = ''


# Copiadas de las guías NIKE HOMBRE / MUJER / INFANTIL de la BD (mayo 2026),
# con la Y agregada a las tallas juveniles de INFANTIL.
_REF_NIKE = {
    'NIKE HOMBRE': [
        (0, '36', '4', '', '', '', '22.5'), (1, '36.5', '4.5', '', '', '', '23'),
        (2, '37', '5', '', '', '', '23.5'), (3, '37.5', '5.5', '', '', '', '24'),
        (4, '38', '6', '', '', '', '24'), (5, '38.5', '6.5', '', '', '', '24.5'),
        (6, '39', '7', '', '', '', '25'), (7, '39.5', '7.5', '', '', '', '25.5'),
        (8, '40', '8', '', '', '', '26'), (9, '40.5', '8.5', '', '', '', '26.5'),
        (10, '41', '9', '', '', '', '27'), (11, '41.5', '9.5', '', '', '', '27.5'),
        (12, '42', '10', '', '', '', '28'), (13, '42.5', '10.5', '', '', '', '28.5'),
        (14, '43', '11', '', '', '', '29'), (15, '43.5', '11.5', '', '', '', '29.5'),
        (16, '44', '12', '', '', '', '30'), (17, '44.5', '12.5', '', '', '', '30.5'),
        (18, '45', '13', '', '', '', '31'), (19, '45.5', '13.5', '', '', '', '31.5'),
        (20, '46', '14', '', '', '', '32'), (21, '46.5', '14.5', '', '', '', '32.5'),
    ],
    'NIKE MUJER': [
        (0, '35', '5', '', '', '', '22'), (1, '35.5', '5.5', '', '', '', '22.5'),
        (2, '36', '6', '', '', '', '23'), (3, '36.5', '6.5', '', '', '', '23.5'),
        (4, '37', '7', '', '', '', '24'), (5, '37.5', '7.5', '', '', '', '24.5'),
        (6, '38', '8', '', '', '', '25'), (7, '38.5', '8.5', '', '', '', '25.5'),
        (8, '39', '9', '', '', '', '26'), (9, '39.5', '9.5', '', '', '', '26.5'),
        (10, '40', '10', '', '', '', '27'), (11, '40.5', '10.5', '', '', '', '27.5'),
        (12, '41', '11', '', '', '', '28'),
    ],
    'NIKE INFANTIL': [
        (0, '16', '2C', '', '', '', '8'), (1, '17.5', '3C', '', '', '', '9'),
        (2, '18.5', '4C', '', '', '', '10'), (3, '20', '5C', '', '', '', '11'),
        (4, '21', '6C', '', '', '', '12'), (5, '22.5', '7C', '', '', '', '13'),
        (6, '24', '8C', '', '', '', '14'), (7, '25', '9C', '', '', '', '15'),
        (8, '26', '10C', '', '', '', '16'), (9, '26.5', '10.5C', '', '', '', '16.5'),
        (10, '27', '11C', '', '', '', '17'), (11, '27.5', '11.5C', '', '', '', '17.5'),
        (12, '28.5', '12C', '', '', '', '18'), (13, '29', '12.5C', '', '', '', '18.5'),
        (14, '30', '13C', '', '', '', '19'), (15, '30.5', '13.5C', '', '', '', '19.5'),
        (16, '31', '1Y', '', '', '', '20'), (17, '32', '1.5Y', '', '', '', '20.5'),
        (18, '32.5', '2Y', '', '', '', '21'), (19, '33', '2.5Y', '', '', '', '21.5'),
        (20, '34', '3Y', '', '', '', '22'), (21, '34.5', '3.5Y', '', '', '', '22.5'),
        (22, '35', '4Y', '', '', '', '23'), (23, '35.5', '4.5Y', '', '', '', '23.5'),
        (24, '36', '5Y', '', '', '', '24'), (25, '37', '5.5Y', '', '', '', '24.5'),
        (26, '37.5', '6Y', '', '', '', '25'), (27, '38', '6.5Y', '', '', '', '25.5'),
        (28, '38.5', '7Y', '', '', '', '26'),
    ],
}

NIKE = Perfil(
    marca='NIKE',
    tipo_talla='US',
    identidad_color=False,
    guias={'HOMBRE': 'NIKE HOMBRE', 'UNISEX': 'NIKE HOMBRE', 'MUJER': 'NIKE MUJER',
           'INFANTIL': 'NIKE INFANTIL'},
    referencia_guias=_REF_NIKE,
    pistas_lectura=(
        'Facturas Nike (distribuidor Equinox Los Andes): el código de artículo es '
        'ESTILO-COLOR, por ejemplo HQ6034-001 o 749680-402; va completo, con el guion. '
        'Las tallas vienen como grilla talla/cantidad en inglés US: las de bebé llevan C '
        '(11C, 10.5C) y las juveniles Y (1.5Y, 6Y); las de adulto van sin letra (7, 7.5, 10). '
        'Conserva la letra C o Y tal cual. Género: descripción que empieza con W o WMNS = '
        'MUJER, con M = HOMBRE; todo lo demás, incluidos niños (JR, GS, PS, TD, BG, BPV), '
        'es UNISEX. Especialidad por la suela: TF = baby (fútbol sintético), IC = sala, '
        'FG o MG = pasto; SLIDE es una sandalia (categoría Sandalias y Chalas). '
        'A veces alguien escribe a mano, junto al precio unitario, el PRECIO DE VENTA al '
        'público (termina en 990, p.ej. 69990): en esa letra el 9 parece una "e" con lazo, '
        'el 6 una "b" y el 0 final un óvalo ancho. También pueden anotar columnas NICK1 / '
        'NICK2 con cuántas unidades van a cada tienda.'
    ),
)

DEFECTO = Perfil(marca='')

PERFILES = {clave_marca(p.marca): p for p in (NIKE,)}


def perfil_para(marca):
    """Perfil de la marca (por nombre canónico) o DEFECTO si no tiene."""
    return PERFILES.get(clave_marca(marca), DEFECTO)
