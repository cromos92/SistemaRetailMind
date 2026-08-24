# -*- coding: utf-8 -*-
"""Diagnostica la COLA MANUAL que dejó abierta la recategorización v1.2.

La v1.2 llevó el catálogo a un árbol de 2 niveles (Calzado / Ropa / Accesorios →
hijas) y cerró en ~98%. Lo que quedó son dos residuos que conviven con el árbol:

  * **planas vivas** — raíces sin hijas y sin prefijo ``_ZZ_``. Son las
    categorías viejas del legacy que TODAVÍA tienen productos colgando. Los
    reportes v1.2 las mandan al bucket "Sin recategorizar" (ver
    ``views_resumen_existencias``) o las rotulan "(sin recategorizar)"
    (``obtener_atributo_opciones``), así que su plata queda fuera de toda
    lectura por rama del árbol.
  * **_ZZ_** — planas ya deprecadas por ``limpiar_categorias_viejas`` (se
    renombran, NO se borran: ``Producto.categoria`` es ``on_delete=CASCADE`` y
    un DELETE arrastraría los productos). Están ocultas de los selects.

Este comando MIDE el tamaño real de esa cola (productos, stock, $ de stock y
venta de los últimos N días por categoría), PROPONE un mapeo plana→hija v1.2 con
su nivel de confianza, y escribe un CSV para que la decisión la tome una persona.

Dos reglas que se aprendieron a golpes y hay que respetar al tocarlo:

  * **La venta se mide con el criterio COMPLETO de productos-vendidos**: tickets
    sin DTE + DTE de venta vivos, monto por línea ``monto_item`` con fallback
    ``precio × stock``, Y las líneas guardadas en neto llevadas a bruto por el
    factor de su propia cabecera (``_factores_iva_dte_lineas_netas``). Sin ese
    último paso el total no es el del reporte: en la ventana de 365 días
    medidos el 22-ago-2026 son $2.988.714 de diferencia en las hijas (0,10%).
  * **Un destino que no calza por NOMBRE no puede salir accionable** (MEDIA /
    MAPEAR) sólo porque figure en una lista del pipeline viejo. Lo tiene que
    sostener la evidencia de los propios productos, y si las señales
    contradicen la propuesta, la contradicción se imprime en la tabla.

**ES SOLO LECTURA Y NO TIENE FORMA DE APLICAR NADA.** No existe ``--aplicar`` ni
equivalente. Además:

  * todas las consultas corren dentro de ``transaction.atomic()`` con
    ``set_rollback(True)``;
  * un ``connection.execute_wrapper`` aborta el comando si alguna capa intentara
    emitir INSERT / UPDATE / DELETE / DDL.

Para APLICAR un mapeo, el insumo es el CSV: la decisión es humana y la escritura
va por otro camino (hoy, edición del producto o un comando aparte con --apply).

Uso:
    python manage.py diagnosticar_cola_categorias_v12
    python manage.py diagnosticar_cola_categorias_v12 --dias 180 --top 40
    python manage.py diagnosticar_cola_categorias_v12 --csv C:/temp/cola.csv
    python manage.py diagnosticar_cola_categorias_v12 --sin-csv --incluir-vacias
"""
import csv
import logging
import re
import time
import unicodedata
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import (
    Case, Count, DecimalField, ExpressionWrapper, F, Q, Sum, When,
)
from django.utils import timezone

from app.models import (
    Categoria, Dte_Productos, Producto, Producto_Talla, Ticket_Productos,
)

from ._data_recategorizacion import (
    CALZADO as DEPARTAMENTO_CALZADO, IDS_DEFAULT_ZAPATILLAS, MAPEO_DIRECTO,
    REGLAS_MARCA,
)
from ._data_recategorizacion_v12 import PADRES, SUBS

logger = logging.getLogger('app')

PREFIJO_ZZ = '_ZZ_'

# ─────────────────────────────────────────────────────────── guarda read-only

ESCRITURAS = (
    'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP', 'CREATE',
    'GRANT', 'REVOKE', 'MERGE', 'COPY', 'REPLACE', 'UPSERT',
)


class EscrituraProhibida(RuntimeError):
    """Se intentó escribir en la BD desde un comando declarado de solo lectura."""


def guard_solo_lectura(execute, sql, params, many, context):
    """``connection.execute_wrapper`` que aborta ante cualquier SQL de escritura."""
    if (sql or '').lstrip().upper().startswith(ESCRITURAS):
        raise EscrituraProhibida(
            'diagnosticar_cola_categorias_v12 es SOLO LECTURA y se intentó '
            'ejecutar: %s' % (sql or '')[:200]
        )
    return execute(sql, params, many, context)


# ───────────────────────────────────────────────── normalización de nombres

_STOPWORDS = {'DE', 'DEL', 'Y', 'E', 'LA', 'EL', 'LOS', 'LAS', 'A', 'EN',
              'CON', 'PARA', 'O', 'U'}


def normalizar(nombre):
    """MAYÚSCULAS, sin tildes, sin puntuación, espacios colapsados.

    Es la comparación de "nombre idéntico": distingue sólo lo que un humano
    consideraría el mismo nombre escrito distinto (mayúsculas, tildes, guiones).
    """
    s = unicodedata.normalize('NFKD', str(nombre or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^0-9A-Za-zÑñ ]+', ' ', s.upper())
    return re.sub(r'\s+', ' ', s).strip()


def _singular(palabra):
    """Raíz común del singular y el plural castellano de una palabra.

    No intenta ser el singular correcto — intenta que ``BOTINES``/``BOTIN`` y
    ``TRAJES``/``TRAJE`` caigan en la misma raíz. Un ``-ES`` a secas no sirve
    (``TRAJES`` daría ``TRAJ`` pero ``TRAJE`` daría ``TRAJE``); por eso se poda
    primero la ``S`` y después la ``E`` final:

        BOTINES → BOTINE → BOTIN   ·   BOTIN → BOTIN
        TRAJES  → TRAJE  → TRAJ    ·   TRAJE → TRAJ
        MEDIAS  → MEDIA            ·   MEDIA → MEDIA
    """
    if len(palabra) > 3 and palabra.endswith('S'):
        palabra = palabra[:-1]
    if len(palabra) > 3 and palabra.endswith('E'):
        palabra = palabra[:-1]
    return palabra


def tokens(nombre):
    """Conjunto de tokens singularizados y sin conectores.

    ``'BUZO- PANTALONES'`` y ``'Buzos y Pantalones'`` colapsan al mismo set.
    """
    return frozenset(
        _singular(p) for p in normalizar(nombre).split()
        if p and p not in _STOPWORDS
    )


# ──────────────────────────── puente taxonomía vieja (dep, tipo) → sub v1.2
#
# Es el mismo puente que usó `preview_recategorizacion_v12` para proyectar el
# árbol viejo sobre el nuevo. Los tipos de DEPORTE no aparecen: en v1.2 el
# deporte dejó de ser categoría y pasó a ser ESPECIALIDAD (atributo múltiple),
# así que una plana deportiva NO tiene hija equivalente — su tipo físico hay
# que sacarlo del producto.
TIPO_A_SUB = {
    ('Calzado', 'Zapatillas Urbanas'): 'zapatillas',
    ('Calzado', 'Zapatillas Lona'): 'zapatillas',
    ('Calzado', 'Escolar'): 'zapatillas',
    ('Calzado', 'Botas'): 'botas',
    ('Calzado', 'Botines'): 'botines',
    ('Calzado', 'Mocasines'): 'mocasin',
    ('Calzado', 'Sandalias y Chalas'): 'sandalias',
    ('Calzado', 'Ballerinas y Bajas'): 'ballerinas',
    ('Calzado', 'Plataformas'): 'plataformas',
    ('Calzado', 'Pantuflas'): 'pantuflas',
    ('Calzado', 'Zuecos'): 'danza',
    ('Calzado', 'Zapatos de Vestir'): 'zvestir',
    ('Calzado', 'Alpargatas'): 'alpargata',
    ('Calzado', 'Cueca'): 'zvestir',
    ('Calzado', 'Bebé y Gateadores'): 'gateadores',
    ('Calzado', 'Confort y Ortopédico'): 'zvestir',
    ('Ropa', 'Poleras y Camisetas'): 'poleras',
    ('Ropa', 'Polerones y Chaquetas'): 'chaquetas',
    ('Ropa', 'Pantalones y Buzos'): 'buzo',
    ('Ropa', 'Shorts'): 'shorts',
    ('Ropa', 'Trajes de Baño y Mallas'): 'tbano',
    ('Ropa', 'Calcetines y Medias'): 'medias',
    ('Accesorios', 'Bolsos y Mochilas'): 'mochilas',
    ('Accesorios', 'Gorros'): 'gorros',
    ('Accesorios', 'Correas'): 'accvest',
    ('Accesorios', 'Pulseras y Joyas'): 'accvest',
    ('Accesorios', 'Accesorios de Pelo'): 'accvest',
    ('Accesorios', 'Protecciones y Canilleras'): 'protecciones',
    ('Otros', 'Trofeos y Medallas'): 'trofeos',
    ('Otros', 'Implementos y Repuestos'): 'accdep',
    ('Otros', 'Juegos de Salón'): 'accdep',
    ('Otros', 'Seguridad'): 'accvest',
}

# Keywords sobre articulo+descripcion → sub v1.2. Orden = prioridad (lo
# específico primero). Sólo se usa para el CONTEO por producto de las
# categorías que no resuelven por nombre; nunca decide sola el mapeo.
KEYWORDS_SUB = [
    (r'BOTIN', 'botines'),
    (r'\bBOTA', 'botas'),
    (r'SANDALIA|CHALA|HAWAI|FLIP', 'sandalias'),
    (r'ZUECO|DANZA|BALLET', 'danza'),
    (r'PANTUFLA', 'pantuflas'),
    (r'MOCASIN', 'mocasin'),
    (r'ALPARGATA', 'alpargata'),
    (r'BALLERINA|BALERINA|CHINITA|MAFALDA|GUILLERMINA', 'ballerinas'),
    (r'PLATAFORMA', 'plataformas'),
    (r'\bTACO', 'tacos'),
    (r'GATEADOR', 'gateadores'),
    (r'CANILLERA|ESPINILLERA|RODILLERA|MUNEQUERA|COQUILLA|PROTEC|CODERA', 'protecciones'),
    (r'MOCHILA|BOLSO|MORRAL|BOLSON|CARTERA|RINONERA|BALONERO', 'mochilas'),
    (r'GORRO|JOCKEY|JOKEY|VISERA', 'gorros'),
    (r'CALCETIN|CALCETA|SOQUETE|MEDIA', 'medias'),
    (r'GUANTE', 'guantes'),
    (r'BALON|PELOTA', 'balones'),
    (r'TROFEO|MEDALLA', 'trofeos'),
    (r'PESA|MANCUERNA|BANDA ELAST|CUERDA|BARRA|SACO|COLCHONETA', 'equipamiento'),
    (r'HOODIE|POLERON|PARKA|CHAQUETA|CHALECO|CANGURO', 'chaquetas'),
    (r'POLERA|CAMISETA|CAMISA|T SHIRT|TSHIRT', 'poleras'),
    (r'SHORT|CALZA|BERMUDA', 'shorts'),
    (r'PANTALON|JOGGER|LEGGING|BUZO', 'buzo'),
    (r'BIKINI|TRAJE DE BANO|TRIKINI|SUNGA', 'tbano'),
    (r'MALLA', 'mallas'),
    (r'ZAPATILLA|ZAPATO', 'zapatillas'),
]
KEYWORDS_SUB_COMPILADO = [(re.compile(pat), sub) for pat, sub in KEYWORDS_SUB]

CONFIANZAS = ('ALTA', 'MEDIA', 'REVISAR', 'BAJA')


def sub_por_texto(texto):
    """Sub-categoría v1.2 sugerida por keywords de artículo+descripción."""
    t = normalizar(texto)
    for rx, sub in KEYWORDS_SUB_COMPILADO:
        if rx.search(t):
            return sub
    return None


TALLAS_ROPA = {'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL', '2XL', '3XL', '4XL'}


def es_talla_calzado(talla):
    """True si la talla tiene forma de número de calzado.

    Tres formas conviven en la BD (medido sobre las categorías de la cola):

      * ``38`` / ``40,5``  — escala CL/EU, 15-50.
      * ``9,0`` / ``8,5``  — escala US con decimal, 4-15 (se exige el separador
        decimal: un ``10`` pelado también puede ser una talla infantil de ropa).
      * ``650`` / ``900``  — escala US ×100 del legacy (6.5 / 9.0). Se exige
        múltiplo de 50 entre 400 y 1600 para no tragarse códigos sueltos.

    ``00`` (talla única) no es señal de nada y queda fuera a propósito.
    """
    # OJO: aquí NO se puede usar `normalizar` — se come el separador decimal y
    # convertiría '40,5' en 405.
    tf = str(talla or '').strip().replace(' ', '')
    decimal = ',' in tf or '.' in tf
    try:
        v = float(tf.replace(',', '.'))
    except ValueError:
        return False
    if 15 <= v <= 50:
        return True
    if decimal and 4 <= v <= 15:
        return True
    return 400 <= v <= 1600 and v % 50 == 0


# Forma física que debería tener un producto de cada padre v1.2. `Accesorios`
# mezcla formas (mochilas sin talla, medias en S/M/L, gorros talla única), así
# que no se le exige ninguna y nunca genera contradicción.
FORMA_POR_PADRE = {'Calzado': 'CALZADO', 'Ropa': 'ROPA'}

# Marcas que el propio pipeline v1.1 declaró de CALZADO (`REGLAS_MARCA`). No se
# inventa una lista nueva: sirve como señal corroborante de que una categoría
# sin calce de nombre sea efectivamente de zapatillas.
MARCAS_CALZADO = frozenset(
    normalizar(marca) for marca, (dep, _tipo) in REGLAS_MARCA.items()
    if dep == DEPARTAMENTO_CALZADO
)

# ── Umbrales (medidos contra prod el 22-ago-2026; ver el docstring de `clasificar`)
#
# RESPALDO: cuánto tienen que decir los productos para que un mapeo que NO calza
# por nombre pueda salir MEDIA/MAPEAR. Están altos a propósito: la fila más cara
# de la cola (RAMA CASUAL, 546 productos) salía MEDIA→Zapatillas por estar en
# `IDS_DEFAULT_ZAPATILLAS` teniendo 99,3% de productos sin keyword y sólo 27,5%
# con talla de calzado.
UMBRAL_RESPALDO_KEYWORD = 60.0   # % de productos cuyo artículo nombra el tipo
UMBRAL_RESPALDO_FORMA = 70.0     # % de productos con la forma física del padre
UMBRAL_RESPALDO_MARCA = 50.0     # % de productos de marcas del rubro

# REPARO: cuánto tiene que pesar una señal contraria para gritarla en la tabla.
UMBRAL_REPARO_FORMA = 40.0       # % con la forma CONTRARIA a la del padre
UMBRAL_REPARO_KEYWORD = 50.0     # % cuya keyword apunta a otro padre
UMBRAL_AVISO_KEYWORD = 20.0      # % cuya keyword apunta a otra hija del padre
UMBRAL_AVISO_SIN_KEYWORD = 90.0  # % sin ninguna keyword → propuesta sin respaldo
# Muestra mínima: sobre 1 o 3 productos un porcentaje no es una señal, es ruido.
# Debajo de esto la contradicción se degrada a aviso y NO baja la confianza.
MIN_PRODUCTOS_CONTRADICE = 5
MIN_PRODUCTOS_SIN_KEYWORD = 10


def forma_por_tallas(tallas):
    """'CALZADO' si predominan tallas de calzado, 'ROPA' si S/M/L, '' si empata.

    Es la señal barata que separa un botín de fútbol de una camiseta de fútbol
    cuando la categoría vieja sólo dice el DEPORTE (en v1.2 el deporte pasó a
    ser Especialidad y la categoría tiene que decir el tipo FÍSICO).
    """
    num = alpha = 0
    for t in tallas:
        if es_talla_calzado(t):
            num += 1
        elif normalizar(t).replace(' ', '') in TALLAS_ROPA:
            alpha += 1
    if num > alpha and num:
        return 'CALZADO'
    if alpha > num and alpha:
        return 'ROPA'
    return ''


def sub_a_nombre_hija(sub):
    """Slug de sub v1.2 → (nombre hija, nombre padre) según la tabla de la v1.2."""
    dato = SUBS.get(sub)
    if not dato:
        return None, None
    padre_slug, nombre = dato
    return nombre, PADRES.get(padre_slug, padre_slug)


# ─────────────────────────────────────────────────────── clasificador de mapeo

def clasificar(categoria_id, nombre, hijas, senales=None):
    """Propone la hija v1.2 para una categoría plana y califica la confianza.

    ``hijas`` es una lista de dicts ``{'id','nombre','padre'}`` (las hijas v1.2
    vivas de la BD). ``senales`` es el resumen por producto de esa categoría
    (ver ``senales_de_categoria``): sólo lo usa el último escalón, que es el
    único que NO calza por nombre. Devuelve un dict con ``hija_id / hija /
    padre / confianza / fuente / motivo / candidatas``.

    Escalones (el primero que resuelve gana):

    =========================  =========  ======================================
    fuente                     confianza  criterio
    =========================  =========  ======================================
    exacto                     ALTA       nombre idéntico (case/tilde-insens.)
    normalizado                MEDIA      mismo set de tokens que UNA hija
    mapeo_v12                  MEDIA      MAPEO_DIRECTO[id] → (dep,tipo) → hija
    token                      MEDIA      tokens contenidos, UNA hija
    productos_keyword          MEDIA      sin nombre, pero ≥60% de los productos
                                          nombran el mismo tipo
    default_v12_respaldado     MEDIA      sin nombre, pero forma ≥70% + marcas
                                          del rubro ≥50%
    default_v12_sin_respaldo   REVISAR    está en IDS_DEFAULT_ZAPATILLAS y NADA
                                          la respalda → cajón de sastre
    (varias)                   REVISAR    el escalón devolvió >1 hija (homónimas)
    especialidad               REVISAR    la plana es un DEPORTE: en v1.2 no es
                                          categoría
    mixta                      REVISAR    MAPEO_DIRECTO la marcó revisar=True
    ninguna                    BAJA       ningún escalón encontró candidata
    =========================  =========  ======================================

    REGLA DURA del escalón de default: pertenecer a ``IDS_DEFAULT_ZAPATILLAS``
    NO es un calce de nombre — es el cajón de sastre del pipeline v1.1. Un
    destino que no se apoya en el nombre sólo puede salir MEDIA (o sea,
    accionable como MAPEAR) si lo sostiene la evidencia de los propios
    productos; si no, sale REVISAR y la candidata descartada queda anotada en
    ``candidatas`` para que no se pierda.
    """
    def _res(hija, confianza, fuente, motivo, candidatas=()):
        return {
            'hija_id': hija['id'] if hija else None,
            'hija': hija['nombre'] if hija else '',
            'padre': hija['padre'] if hija else '',
            'confianza': confianza,
            'fuente': fuente,
            'motivo': motivo,
            'candidatas': [h['nombre'] for h in candidatas],
        }

    n_plana = normalizar(nombre)
    t_plana = tokens(nombre)

    # 1) nombre idéntico
    exactas = [h for h in hijas if normalizar(h['nombre']) == n_plana]
    if len(exactas) == 1:
        return _res(exactas[0], 'ALTA', 'exacto',
                    'nombre idéntico a la hija v1.2 (mapeo directo)')
    if len(exactas) > 1:
        return _res(None, 'REVISAR', 'exacto_ambiguo',
                    'el nombre calza exacto con %d hijas homónimas de padres '
                    'distintos' % len(exactas), exactas)

    # 2) mismo set de tokens (normalización: plural, tildes, conectores)
    if t_plana:
        norm = [h for h in hijas if tokens(h['nombre']) == t_plana]
        if len(norm) == 1:
            return _res(norm[0], 'MEDIA', 'normalizado',
                        'calza con la hija tras normalizar (plural/tildes/conectores)')
        if len(norm) > 1:
            return _res(None, 'REVISAR', 'normalizado_ambiguo',
                        'normaliza igual que %d hijas distintas' % len(norm), norm)

    # 3) mapeo histórico de la v1.2 por id de categoría
    mapeo = MAPEO_DIRECTO.get(categoria_id)
    if mapeo:
        dep, tipo, revisar = mapeo
        sub = TIPO_A_SUB.get((dep, tipo))
        if sub is None:
            return _res(None, 'REVISAR', 'especialidad',
                        'la v1.2 la mandó a «%s / %s»: en el árbol nuevo el deporte '
                        'es ESPECIALIDAD, no categoría — el tipo físico sale del '
                        'producto' % (dep, tipo))
        nombre_hija, _padre = sub_a_nombre_hija(sub)
        cand = [h for h in hijas if normalizar(h['nombre']) == normalizar(nombre_hija)]
        if len(cand) == 1:
            if revisar:
                return _res(cand[0], 'REVISAR', 'mapeo_v12_mixto',
                            'la v1.2 mapeaba a «%s» pero marcó el bucket como MIXTO '
                            '(revisar=True)' % nombre_hija)
            return _res(cand[0], 'MEDIA', 'mapeo_v12',
                        'mapeo histórico v1.2: %s / %s → %s' % (dep, tipo, nombre_hija))
        if len(cand) > 1:
            return _res(None, 'REVISAR', 'mapeo_v12_ambiguo',
                        'el mapeo v1.2 apunta a «%s» y hay %d hijas con ese nombre'
                        % (nombre_hija, len(cand)), cand)

    # 4) contención de tokens ("CHALAS" ⊂ "Sandalias y Chalas")
    if t_plana:
        sub_tok = [h for h in hijas
                   if tokens(h['nombre']) and (t_plana <= tokens(h['nombre'])
                                               or tokens(h['nombre']) <= t_plana)]
        if len(sub_tok) == 1:
            return _res(sub_tok[0], 'MEDIA', 'token',
                        'el nombre de la plana está contenido en el de la hija (o viceversa)')
        if len(sub_tok) > 1:
            return _res(None, 'REVISAR', 'token_ambiguo',
                        'los tokens del nombre calzan con %d hijas' % len(sub_tok),
                        sub_tok)

    # 5) default histórico del pipeline v1.1 (RAMA CASUAL / Casual = sneakers).
    #    Acá NO hay calce de nombre: el destino tiene que sostenerlo la
    #    evidencia de los productos o la fila se va a REVISAR.
    if categoria_id in IDS_DEFAULT_ZAPATILLAS:
        nombre_default, _padre = sub_a_nombre_hija('zapatillas')
        cand_default = [h for h in hijas
                        if normalizar(h['nombre']) == normalizar(nombre_default)]
        s = senales or {}
        n_prod = int(s.get('n_productos') or 0)
        pct_modal = float(s.get('pct_modal') or 0.0)
        pct_forma = float(s.get('pct_forma_dominante') or 0.0)
        pct_marca = float(s.get('pct_marcas_calzado') or 0.0)
        pct_sin_kw = float(s.get('pct_sin_keyword') or 0.0)

        # 5a) los propios productos nombran un tipo, y por mayoría amplia.
        #     Puede NO ser el default: manda lo que dicen los artículos.
        sub_modal = s.get('sub_modal')
        if sub_modal and pct_modal >= UMBRAL_RESPALDO_KEYWORD:
            nombre_modal, _pm = sub_a_nombre_hija(sub_modal)
            cand_modal = [h for h in hijas if nombre_modal
                          and normalizar(h['nombre']) == normalizar(nombre_modal)]
            if len(cand_modal) == 1:
                return _res(cand_modal[0], 'MEDIA', 'productos_keyword',
                            'no calza por nombre, pero el %.1f%% de sus %d productos '
                            'dice «%s» en el artículo' % (pct_modal, n_prod,
                                                          nombre_modal))

        # 5b) el default sólo se sostiene si la FORMA y las MARCAS lo respaldan
        if (len(cand_default) == 1 and s.get('forma_dominante') == 'CALZADO'
                and pct_forma >= UMBRAL_RESPALDO_FORMA
                and pct_marca >= UMBRAL_RESPALDO_MARCA):
            return _res(cand_default[0], 'MEDIA', 'default_v12_respaldado',
                        'default histórico de «%s» RESPALDADO por los productos: '
                        'talla de calzado %.1f%% y marcas de calzado %.1f%% de %d '
                        'productos' % (nombre_default, pct_forma, pct_marca, n_prod))

        # 5c) sin respaldo: el default es un cajón de sastre, no un mapeo
        return _res(None, 'REVISAR', 'default_v12_sin_respaldo',
                    'la v1.1 la usaba de DEFAULT de «%s» (IDS_DEFAULT_ZAPATILLAS), '
                    'pero no calza por nombre y sus productos no lo respaldan: '
                    '%.1f%% sin keyword, talla %s %.1f%%, marcas de calzado %.1f%% '
                    '(%d productos) — hay que clasificar producto a producto'
                    % (nombre_default, pct_sin_kw,
                       s.get('forma_dominante') or 'SIN_TALLA', pct_forma,
                       pct_marca, n_prod),
                    cand_default)

    return _res(None, 'BAJA', 'ninguna',
                'ninguna hija v1.2 calza por nombre ni por el mapeo histórico')


# ──────────────────────────────── señales por producto y reparo a la propuesta

def senales_de_categoria(n_productos, cnt_keyword, cnt_forma, cnt_marca):
    """Resume en un dict lo que dicen los PRODUCTOS de una categoría.

    Es la evidencia que se cruza contra el destino propuesto: la keyword modal
    del artículo, la forma de la talla (calzado vs ropa) y el mix de marcas.
    Los tres contadores llegan ya armados por ``construir_filas``.
    """
    def _pct(x):
        return round(100.0 * x / n_productos, 1) if n_productos else 0.0

    top = [x for x in cnt_keyword.most_common() if x[0] != '(sin keyword)'][:1]
    sub_modal, n_modal = (top[0] if top else (None, 0))
    sin_kw = cnt_keyword.get('(sin keyword)', 0)

    n_cal, n_ropa = cnt_forma.get('CALZADO', 0), cnt_forma.get('ROPA', 0)
    if not (n_cal or n_ropa):
        forma_dom, n_forma = 'SIN_TALLA', 0
    elif n_cal >= 0.7 * (n_cal + n_ropa):
        forma_dom, n_forma = 'CALZADO', n_cal
    elif n_ropa >= 0.7 * (n_cal + n_ropa):
        forma_dom, n_forma = 'ROPA', n_ropa
    else:
        forma_dom, n_forma = 'MIXTA', n_cal + n_ropa

    marca_modal, n_marca_modal = (cnt_marca.most_common(1)[0] if cnt_marca
                                  else ('', 0))
    n_marca_calzado = sum(v for k, v in cnt_marca.items() if k in MARCAS_CALZADO)

    return {
        'n_productos': n_productos,
        'sub_modal': sub_modal,
        'hija_modal': sub_a_nombre_hija(sub_modal)[0] or '' if sub_modal else '',
        'padre_modal': sub_a_nombre_hija(sub_modal)[1] or '' if sub_modal else '',
        'n_modal': n_modal,
        'pct_modal': _pct(n_modal),
        'sin_keyword': sin_kw,
        'pct_sin_keyword': _pct(sin_kw),
        'n_talla_calzado': n_cal,
        'n_talla_ropa': n_ropa,
        'forma_dominante': forma_dom,
        'pct_forma_dominante': _pct(n_forma),
        'marca_modal': marca_modal,
        'pct_marca_modal': _pct(n_marca_modal),
        'n_marcas_calzado': n_marca_calzado,
        'pct_marcas_calzado': _pct(n_marca_calzado),
    }


def evaluar_reparo(prop, senales):
    """Contradicción entre la hija PROPUESTA y lo que dicen sus productos.

    Devuelve ``(nivel, texto)`` con nivel ``''`` / ``'AVISO'`` / ``'CONTRADICE'``.

    Se evalúa **sólo cuando hay hija propuesta**: es exactamente ahí donde el
    humano necesita el reparo. Cuando no hay propuesta la tabla ya imprime las
    señales crudas, así que no hay nada que contradecir.
    """
    if not prop.get('hija'):
        return '', ''
    s = senales or {}
    n = int(s.get('n_productos') or 0)
    if not n:
        return '', ''

    padre = prop.get('padre') or ''
    esperada = FORMA_POR_PADRE.get(padre)
    muestra_ok = n >= MIN_PRODUCTOS_CONTRADICE
    motivos_fuertes, motivos_aviso = [], []

    # (1) la keyword modal de los artículos apunta a OTRA hija
    hija_modal = s.get('hija_modal') or ''
    pct_modal = float(s.get('pct_modal') or 0.0)
    # Un modal de 10 productos sobre 241 (4,1%) no es "evidencia por keyword":
    # es ruido, y la categoría sigue contando como sin respaldo.
    hay_keyword = bool(hija_modal) and pct_modal >= UMBRAL_AVISO_KEYWORD
    if (hija_modal and normalizar(hija_modal) != normalizar(prop['hija'])
            and pct_modal >= UMBRAL_AVISO_KEYWORD):
        texto = ('el %.1f%% de los artículos dice «%s», no «%s»'
                 % (pct_modal, hija_modal, prop['hija']))
        otro_padre = (s.get('padre_modal') or '') != padre
        if pct_modal >= UMBRAL_REPARO_KEYWORD and otro_padre and muestra_ok:
            motivos_fuertes.append(texto + ' (y es de otro padre: %s)'
                                   % (s.get('padre_modal') or '?'))
        else:
            motivos_aviso.append(texto)

    # (2) la forma de la talla es la CONTRARIA a la del padre propuesto
    forma = s.get('forma_dominante')
    pct_forma = float(s.get('pct_forma_dominante') or 0.0)
    if (esperada and forma in ('CALZADO', 'ROPA') and forma != esperada
            and pct_forma >= UMBRAL_AVISO_KEYWORD):
        texto = ('talla %s en el %.1f%% de sus %d productos y la propuesta es '
                 '%s' % (forma, pct_forma, n, padre))
        if pct_forma >= UMBRAL_REPARO_FORMA and muestra_ok:
            motivos_fuertes.append(texto)
        else:
            motivos_aviso.append(texto)
    elif esperada and forma == 'MIXTA' and muestra_ok:
        motivos_aviso.append('talla MIXTA (%d calzado / %d ropa) y la propuesta '
                             'es %s' % (s.get('n_talla_calzado', 0),
                                        s.get('n_talla_ropa', 0), padre))

    # (3) la propuesta no la respalda NINGÚN producto. No aplica cuando el
    #     nombre calza exacto con la hija (el nombre ES la evidencia) ni sobre
    #     un puñado de productos, donde "0 keywords" no dice nada.
    pct_sin = float(s.get('pct_sin_keyword') or 0.0)
    if (pct_sin >= UMBRAL_AVISO_SIN_KEYWORD and not hay_keyword
            and n >= MIN_PRODUCTOS_SIN_KEYWORD
            and prop.get('fuente') != 'exacto'):
        motivos_aviso.append('%d de %d productos (%.1f%%) no dan ninguna keyword: '
                             'la propuesta no la respalda ningún artículo'
                             % (s.get('sin_keyword', 0), n, pct_sin))

    if motivos_fuertes:
        return 'CONTRADICE', ' · '.join(motivos_fuertes + motivos_aviso)
    if motivos_aviso:
        return 'AVISO', ' · '.join(motivos_aviso)
    return '', ''


# ────────────────────────────────────────────────────────── recolección de datos

def _expr_monto_linea_dte():
    """Monto de la línea de DTE: `monto_item`, con fallback `precio * stock`.

    Copia deliberada de `views_modulo_reportes._expr_monto_linea_dte`. Es sólo
    la MITAD del criterio del reporte de productos vendidos: la otra mitad es
    llevar a bruto las líneas guardadas en neto, y va en
    `_factores_iva_dte_lineas_netas` (abajo). Las dos juntas —y sólo las dos
    juntas— son "la misma regla que productos-vendidos".
    """
    return Case(
        When(monto_item__gt=0,
             then=ExpressionWrapper(F('monto_item'), output_field=DecimalField())),
        default=ExpressionWrapper(F('precio') * F('stock'),
                                  output_field=DecimalField()),
        output_field=DecimalField(),
    )


def _qs_lineas_dte_venta(desde, hoy):
    """Líneas de DTE de VENTA vivos en la ventana, sin ningún filtro de producto.

    Mismos filtros de cabecera que `_agregar_productos_vendidos`: se excluyen
    anulados/cancelados/rechazados, las notas de crédito y los documentos que
    una empresa se emite a sí misma.
    """
    return (
        Dte_Productos.objects.filter(
            dte__fecha_emision__gte=desde,
            dte__fecha_emision__lte=hoy,
            dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        )
        .exclude(dte__estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])
        .exclude(dte__tipo_documento='NOTA DE CREDITO')
        .exclude(dte__receptor__isnull=False, dte__receptor_id=F('dte__emisor_id'))
    )


def _factores_iva_dte_lineas_netas(desde, hoy):
    """`{dte_id: factor}` de los DTE cuyas líneas están guardadas en NETO.

    Segunda mitad del criterio de productos-vendidos, portada de
    `views_modulo_reportes._factores_iva_dte_lineas_netas`: el reporte expresa
    TODO con IVA incluido, pero `Dte_Productos` no tiene criterio único (guías
    y algunas facturas guardan la línea neta). Se decide documento por
    documento comparando la suma de TODAS sus líneas contra la cabecera: si
    empata con `monto_neto` y el documento es afecto, sus líneas están netas y
    se corrigen por `monto_con_iva / monto_neto`.

    ÚNICA diferencia con el reporte: el reporte elige los documentos con un
    subquery ``dte_id IN (líneas con productoTalla)`` y acá los filtros de
    cabecera van inline. Da lo mismo y cuesta 7 veces menos. Medido contra
    prod el 22-ago-2026 (ventana 365d): 333 factores por las dos vías, MISMAS
    claves y 0 factores distintos sobre los documentos que se usan; el inline
    devuelve además 21 documentos que no tienen ni una línea con
    ``productoTalla`` y que por eso nunca se aplican. 60,4s el subquery contra
    8,5s el inline.
    """
    tolerancia = Decimal('0.01')
    factores = {}
    filas = _qs_lineas_dte_venta(desde, hoy).filter(
        dte__monto_neto__gt=0,
        dte__monto_con_iva__gt=F('dte__monto_neto'),
    ).values(
        'dte_id',
        neto=F('dte__monto_neto'),
        bruto=F('dte__monto_con_iva'),
    ).annotate(total_lineas=Sum(_expr_monto_linea_dte()))

    for fila in filas:
        neto = Decimal(fila['neto'] or 0)
        bruto = Decimal(fila['bruto'] or 0)
        base = Decimal(fila['total_lineas'] or 0)
        if base <= 0 or neto <= 0:
            continue
        if abs(base - neto) <= neto * tolerancia:
            factores[fila['dte_id']] = bruto / neto
    return factores


def recolectar(dias):
    """Lee todo lo necesario de la BD. SOLO SELECT, dentro de atomic+rollback."""
    hoy = timezone.localdate()
    desde = hoy - timedelta(days=dias)
    datos = {'hoy': hoy, 'desde': desde, 'dias': dias}

    with transaction.atomic():
        cats = list(Categoria.objects.values('id', 'nombre', 'padre_id'))
        con_hijas = set(
            Categoria.objects.filter(subcategorias__isnull=False)
            .values_list('id', flat=True).distinct()
        )

        prod_rows = list(
            Producto.objects.values('categoria_id').annotate(
                n=Count('id'),
                n_excl=Count('id', filter=Q(excluir_de_analitica=True)),
            )
        )

        stock_rows = list(
            Producto_Talla.objects
            .filter(stock__gt=0, producto__excluir_de_analitica=False)
            .values('producto__categoria_id')
            .annotate(u=Sum('stock'),
                      m=Sum(F('stock') * F('producto__costo')))
        )

        # Venta: misma regla anti-doble-contado que productos-vendidos
        # (tickets SIN DTE + todos los DTE de venta vivos).
        tp_rows = list(
            Ticket_Productos.objects.filter(
                idTicket__created_at__date__gte=desde,
                idTicket__created_at__date__lte=hoy,
                idTicket__estado='PAGADO',
                idTicket__modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
                idTicket__dte_generado=False,
                ProductoTalla__isnull=False,
                ProductoTalla__producto__excluir_de_analitica=False,
            ).values(cid=F('ProductoTalla__producto__categoria_id'))
            .annotate(u=Sum('stock'), m=Sum('subtotal'))
        )

        qs_dp = _qs_lineas_dte_venta(desde, hoy).filter(
            productoTalla__isnull=False,
            productoTalla__producto__excluir_de_analitica=False,
        )

        dp_rows = list(
            qs_dp.values(cid=F('productoTalla__producto__categoria_id'))
            .annotate(u=Sum('stock'), m=Sum(_expr_monto_linea_dte()))
        )

        # Mitad 2 del criterio de productos-vendidos: las líneas guardadas en
        # neto se llevan a bruto con el factor de su propia cabecera.
        factores_iva = _factores_iva_dte_lineas_netas(desde, hoy)
        dp_iva_rows = list(
            qs_dp.filter(dte_id__in=list(factores_iva))
            .values('dte_id', cid=F('productoTalla__producto__categoria_id'))
            .annotate(m=Sum(_expr_monto_linea_dte()))
        ) if factores_iva else []

        # Productos de la cola (para el conteo por keyword). Se acota a las
        # categorías planas/_ZZ_ para no traer el catálogo entero.
        raices = {c['id'] for c in cats if c['padre_id'] is None}
        cola_ids = sorted(raices - con_hijas)
        prods_cola = list(
            Producto.objects.filter(categoria_id__in=cola_ids)
            .values('id', 'categoria_id', 'articulo', 'descripcion',
                    'excluir_de_analitica', marca=F('atributo1__valor'))
        ) if cola_ids else []

        # Tallas de esos mismos productos: la forma (numérica vs S/M/L) es la
        # señal que separa un botín de fútbol de una camiseta de fútbol.
        tallas_cola = list(
            Producto_Talla.objects
            .filter(producto__categoria_id__in=cola_ids)
            .values('producto_id', 'talla')
        ) if cola_ids else []

        transaction.set_rollback(True)

    datos.update({
        'cats': cats, 'con_hijas': con_hijas, 'prod_rows': prod_rows,
        'stock_rows': stock_rows, 'tp_rows': tp_rows, 'dp_rows': dp_rows,
        'dp_iva_rows': dp_iva_rows, 'factores_iva': factores_iva,
        'prods_cola': prods_cola, 'tallas_cola': tallas_cola,
        'cola_ids': cola_ids,
    })
    return datos


def construir_filas(datos):
    """Cruza lo recolectado y devuelve (filas, censo). Sin tocar la BD."""
    cats = datos['cats']
    con_hijas = datos['con_hijas']
    por_id = {c['id']: c for c in cats}

    hijas = [{'id': c['id'], 'nombre': c['nombre'],
              'padre': por_id.get(c['padre_id'], {}).get('nombre', '')}
             for c in cats
             if c['padre_id'] is not None and not c['nombre'].startswith(PREFIJO_ZZ)]

    prod = {r['categoria_id']: r for r in datos['prod_rows']}
    stk = {r['producto__categoria_id']: r for r in datos['stock_rows']}
    ven = {}
    for r in datos['tp_rows'] + datos['dp_rows']:
        b = ven.setdefault(r['cid'], {'u': 0, 'm': 0})
        b['u'] += int(r['u'] or 0)
        b['m'] += int(r['m'] or 0)

    # Corrección de IVA de las líneas guardadas en neto: se suma sólo el
    # diferencial sobre el monto ya agregado, igual que productos-vendidos.
    factores_iva = datos.get('factores_iva') or {}
    for r in datos.get('dp_iva_rows') or []:
        factor = factores_iva.get(r['dte_id'])
        if not factor:
            continue
        base = Decimal(r['m'] or 0)
        b = ven.setdefault(r['cid'], {'u': 0, 'm': 0})
        b['m'] += int(base * factor) - int(base)

    # keyword / forma de talla / marca por producto, agrupado por categoría
    tallas_por_prod = {}
    for t in datos.get('tallas_cola') or []:
        tallas_por_prod.setdefault(t['producto_id'], []).append(t['talla'])

    modal = {}
    formas = {}
    marcas = {}
    for p in datos['prods_cola']:
        sub = sub_por_texto('%s %s' % (p['articulo'] or '', p['descripcion'] or ''))
        modal.setdefault(p['categoria_id'], Counter())[sub or '(sin keyword)'] += 1
        forma = forma_por_tallas(tallas_por_prod.get(p['id']) or [])
        formas.setdefault(p['categoria_id'], Counter())[forma or 'SIN_TALLA'] += 1
        marcas.setdefault(p['categoria_id'], Counter())[
            normalizar(p.get('marca')) or '(SIN MARCA)'] += 1

    filas = []
    for c in cats:
        if c['padre_id'] is not None or c['id'] in con_hijas:
            continue  # hijas y padres v1.2 no son cola
        es_zz = c['nombre'].startswith(PREFIJO_ZZ)
        nombre_limpio = c['nombre'][len(PREFIJO_ZZ):] if es_zz else c['nombre']
        p = prod.get(c['id']) or {}
        s = stk.get(c['id']) or {}
        v = ven.get(c['id']) or {}
        n_prod = int(p.get('n', 0) or 0)

        senales = senales_de_categoria(
            n_prod, modal.get(c['id']) or Counter(),
            formas.get(c['id']) or Counter(), marcas.get(c['id']) or Counter())

        prop = clasificar(c['id'], nombre_limpio, hijas, senales)

        # El reparo se calcula SIEMPRE que haya propuesta: es justo ahí donde la
        # contradicción tiene que verse (antes sólo se mostraban las señales
        # cuando NO había destino, o sea nunca donde hacían falta).
        reparo_nivel, reparo = evaluar_reparo(prop, senales)
        if reparo_nivel == 'CONTRADICE' and prop['confianza'] in ('ALTA', 'MEDIA'):
            # una propuesta que los productos desmienten no puede quedar como
            # accionable: baja a REVISAR y deja de salir en el filtro MAPEAR.
            prop = dict(prop, confianza='REVISAR',
                        fuente=prop['fuente'] + '_contradicho',
                        motivo='%s — PERO los productos lo contradicen: %s'
                               % (prop['motivo'], reparo))

        if es_zz:
            accion = 'ZZ_CON_PRODUCTOS' if n_prod else 'YA_DEPRECADA'
        elif n_prod == 0:
            accion = 'DEPRECAR_ZZ'
        elif prop['confianza'] in ('ALTA', 'MEDIA'):
            accion = 'MAPEAR'
        else:
            accion = 'REVISAR_MANUAL'

        filas.append({
            'categoria_id': c['id'],
            'categoria': c['nombre'],
            'estado': 'ZZ_DEPRECADA' if es_zz else 'PLANA_VIVA',
            'productos': n_prod,
            'productos_excluidos_analitica': int(p.get('n_excl', 0) or 0),
            'stock_unidades': int(s.get('u', 0) or 0),
            'stock_costo': int(s.get('m', 0) or 0),
            'venta_unidades': int(v.get('u', 0) or 0),
            'venta_monto': int(v.get('m', 0) or 0),
            'impacto_pesos': int(s.get('m', 0) or 0) + int(v.get('m', 0) or 0),
            'accion_sugerida': accion,
            'confianza': prop['confianza'],
            'fuente': prop['fuente'],
            'hija_sugerida_id': prop['hija_id'] or '',
            'hija_sugerida': prop['hija'],
            'padre_sugerido': prop['padre'],
            'candidatas': ' | '.join(prop['candidatas']),
            'hija_modal_productos': senales['hija_modal'],
            'productos_modal': senales['n_modal'],
            'pct_modal': senales['pct_modal'],
            'productos_sin_keyword': senales['sin_keyword'],
            'productos_talla_calzado': senales['n_talla_calzado'],
            'productos_talla_ropa': senales['n_talla_ropa'],
            'forma_dominante': senales['forma_dominante'],
            'pct_forma_dominante': senales['pct_forma_dominante'],
            'marca_modal': senales['marca_modal'],
            'pct_marca_modal': senales['pct_marca_modal'],
            'pct_marcas_calzado': senales['pct_marcas_calzado'],
            'reparo_nivel': reparo_nivel,
            'reparo': reparo,
            'motivo': prop['motivo'],
        })

    filas.sort(key=lambda r: (-r['impacto_pesos'], -r['productos'], r['categoria']))

    # censo global (para el encabezado)
    def _clase(cid):
        c = por_id.get(cid)
        if c is None:
            return 'SIN_CAT'
        if c['nombre'].startswith(PREFIJO_ZZ):
            return 'ZZ'
        if c['padre_id'] is not None:
            return 'HIJA'
        return 'PADRE' if cid in con_hijas else 'PLANA'

    censo = {k: {'cats': 0, 'cats_con_prod': 0, 'prod': 0, 'stock_u': 0,
                 'stock_m': 0, 'venta_u': 0, 'venta_m': 0}
             for k in ('HIJA', 'PLANA', 'ZZ', 'PADRE', 'SIN_CAT')}
    # El censo censa el ÁRBOL COMPLETO: se recorren TODAS las categorías, no
    # sólo las que aparecen en algún agregado. Antes se iteraba el set de ids
    # con productos/stock/venta y por eso imprimía HIJA 28 (faltaba Mallas 102,
    # sin productos) y PLANA 48 (faltaba RAMA DE HANDBALL 69, vacía): un bloque
    # titulado CENSO tiene que contar también lo que está en cero.
    for c in cats:
        censo[_clase(c['id'])]['cats'] += 1
    for cid in set(list(prod) + list(stk) + list(ven)):
        b = censo[_clase(cid)]
        n_prod = int((prod.get(cid) or {}).get('n', 0) or 0)
        if n_prod:
            b['cats_con_prod'] += 1
        b['prod'] += n_prod
        b['stock_u'] += int((stk.get(cid) or {}).get('u', 0) or 0)
        b['stock_m'] += int((stk.get(cid) or {}).get('m', 0) or 0)
        b['venta_u'] += int((ven.get(cid) or {}).get('u', 0) or 0)
        b['venta_m'] += int((ven.get(cid) or {}).get('m', 0) or 0)

    censo['_total_cats'] = len(cats)
    censo['_n_hijas'] = len(hijas)
    censo['_padres'] = sorted(
        por_id[cid]['nombre'] for cid in con_hijas
        if por_id.get(cid) and por_id[cid]['padre_id'] is None
        and not por_id[cid]['nombre'].startswith(PREFIJO_ZZ)
    )
    # Las categorías que existen pero no tienen ni un producto: son las que el
    # censo viejo hacía desaparecer.
    censo['_vacias'] = sorted(
        (c['id'], c['nombre'], _clase(c['id'])) for c in cats
        if not int((prod.get(c['id']) or {}).get('n', 0) or 0)
        and _clase(c['id']) in ('HIJA', 'PLANA')
    )
    return filas, censo


CAMPOS_CSV = [
    'categoria_id', 'categoria', 'estado', 'productos',
    'productos_excluidos_analitica', 'stock_unidades', 'stock_costo',
    'venta_unidades', 'venta_monto', 'impacto_pesos', 'accion_sugerida',
    'confianza', 'fuente', 'hija_sugerida_id', 'hija_sugerida',
    'padre_sugerido', 'candidatas', 'reparo_nivel', 'reparo',
    'hija_modal_productos', 'productos_modal',
    'pct_modal', 'productos_sin_keyword', 'productos_talla_calzado',
    'productos_talla_ropa', 'forma_dominante', 'pct_forma_dominante',
    'marca_modal', 'pct_marca_modal', 'pct_marcas_calzado', 'motivo',
]


def escribir_csv(filas, ruta):
    with open(ruta, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS_CSV, extrasaction='ignore')
        w.writeheader()
        for f in filas:
            w.writerow(f)
    return ruta


# ────────────────────────────────────────────────────────────────── comando

class Command(BaseCommand):
    help = ('Diagnostica la cola manual de la recategorización v1.2 y propone el '
            'mapeo plana→hija en un CSV. SOLO LECTURA: no tiene --aplicar.')

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=365,
                            help='Ventana de venta a medir (default 365).')
        parser.add_argument('--top', type=int, default=25,
                            help='Filas a imprimir en la tabla priorizada (default 25).')
        parser.add_argument('--csv', dest='csv_path', default=None,
                            help='Ruta del CSV de propuesta (default: '
                                 'cola_categorias_v12_<timestamp>.csv en el CWD).')
        parser.add_argument('--sin-csv', action='store_true',
                            help='No escribir el CSV (solo salida por pantalla).')
        parser.add_argument('--incluir-vacias', action='store_true',
                            help='Incluir en la tabla las categorías sin productos, '
                                 'stock ni venta.')
        parser.add_argument('--incluir-zz', action='store_true',
                            help='Incluir las _ZZ_ ya deprecadas en la tabla '
                                 '(siempre van en el CSV).')

    @staticmethod
    def _destino(f):
        """Texto de la columna destino: la hija propuesta o las senales que hay.

        Cuando SÍ hay propuesta, el reparo (la señal que la contradice) NO va
        acá: sale en su propia línea destacada, ver `_linea_reparo`.
        """
        if f['hija_sugerida']:
            base = ('%s › %s' % (f['padre_sugerido'], f['hija_sugerida'])
                    if f['padre_sugerido'] else f['hija_sugerida'])
            return base if f['confianza'] != 'REVISAR' else '¿%s?' % base
        senales = []
        # Umbral de ruido: un modal de 2 productos sobre 546 no es una señal.
        if f['hija_modal_productos'] and f['pct_modal'] >= UMBRAL_AVISO_KEYWORD:
            senales.append('keyword→%s %s%%' % (f['hija_modal_productos'],
                                                f['pct_modal']))
        if f['forma_dominante'] == 'MIXTA':
            senales.append('talla MIXTA (%d calzado / %d ropa)'
                           % (f['productos_talla_calzado'], f['productos_talla_ropa']))
        elif (f['forma_dominante'] in ('CALZADO', 'ROPA')
              and f['pct_forma_dominante'] >= UMBRAL_AVISO_KEYWORD):
            senales.append('talla %s %s%%' % (f['forma_dominante'],
                                              f['pct_forma_dominante']))
        if f['productos'] and f['productos_sin_keyword']:
            senales.append('%d sin keyword' % f['productos_sin_keyword'])
        if f['candidatas']:
            # La candidata que se descartó no se esconde: se dice cuál era.
            senales.append('%s: %s' % ('ambigua entre'
                                       if f['fuente'].endswith('_ambiguo')
                                       else 'descartada', f['candidatas']))
        return '— ' + (' · '.join(senales) if senales else 'sin señal')

    def _linea_reparo(self, f):
        """Línea destacada con la señal que CONTRADICE la hija propuesta.

        Es el arreglo del defecto que hacía invisible la evidencia contraria: la
        tabla priorizada es lo que la persona lee para triar, así que la
        contradicción tiene que estar ahí y no sólo en el CSV.
        """
        if not f.get('reparo'):
            return None
        if f['reparo_nivel'] == 'CONTRADICE':
            return self.style.ERROR('       !! CONTRADICE la propuesta: %s'
                                    % f['reparo'])
        return self.style.WARNING('       !  ojo: %s' % f['reparo'])

    def handle(self, *args, **opts):
        t0 = time.time()
        dias = max(1, int(opts['dias']))
        q0 = len(connection.queries)

        with connection.execute_wrapper(guard_solo_lectura):
            datos = recolectar(dias)
        filas, censo = construir_filas(datos)

        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(
            '[SOLO LECTURA] diagnosticar_cola_categorias_v12 — ventana %s → %s (%d días)'
            % (datos['desde'], datos['hoy'], dias)))
        w('  Este comando NO escribe en la BD y no tiene bandera para aplicar nada.')
        w('  Universo de stock/venta: productos con excluir_de_analitica=False '
          '(el mismo de los reportes).')
        w('  Criterio de venta: tickets sin DTE + DTE de venta vivos, TODO con '
          'IVA incluido (%d DTE con líneas netas llevados a bruto por su propia '
          'cabecera) — la regla completa de productos-vendidos.'
          % len(datos.get('factores_iva') or {}))
        w('')

        # ── censo (cuenta el ÁRBOL COMPLETO, incluidas las categorías en cero)
        w(self.style.MIGRATE_HEADING('CENSO DEL ÁRBOL v1.2'))
        w('  %d categorías en total. `cats` = categorías que EXISTEN; `c/prod` = '
          'cuántas tienen al menos un producto.' % censo['_total_cats'])
        w('  padres v1.2 : %s' % (', '.join(censo['_padres']) or '(ninguno)'))
        w('  hijas v1.2  : %d' % censo['_n_hijas'])
        w('  %-8s %5s %6s %10s %10s %16s %10s %16s'
          % ('clase', 'cats', 'c/prod', 'productos', 'stock u', '$ stock',
             'venta u', '$ venta'))
        tot_m = sum(censo[k]['stock_m'] + censo[k]['venta_m']
                    for k in ('HIJA', 'PLANA', 'ZZ', 'PADRE', 'SIN_CAT')) or 1
        for k in ('HIJA', 'PLANA', 'ZZ', 'PADRE', 'SIN_CAT'):
            b = censo[k]
            if not (b['cats'] or b['prod'] or b['stock_u'] or b['venta_u']):
                continue
            w('  %-8s %5d %6d %10s %10s %16s %10s %16s   (%.2f%% del $)'
              % (k, b['cats'], b['cats_con_prod'], f"{b['prod']:,}",
                 f"{b['stock_u']:,}", f"{b['stock_m']:,}", f"{b['venta_u']:,}",
                 f"{b['venta_m']:,}",
                 100.0 * (b['stock_m'] + b['venta_m']) / tot_m))
        if censo['_vacias']:
            w('  categorías del árbol SIN ni un producto (no aparecen en ningún '
              'agregado, por eso se listan acá):')
            for cid, nombre, clase in censo['_vacias']:
                w('     %-6s %4d %s' % (clase, cid, nombre))
        pl, hi = censo['PLANA'], censo['HIJA']
        base = pl['prod'] + hi['prod']
        if base:
            w('  → recategorización v1.2 al %.2f%% de los productos con categoría '
              '(%s en hijas / %s en planas vivas)'
              % (100.0 * hi['prod'] / base, f"{hi['prod']:,}", f"{pl['prod']:,}"))
        w('')

        # ── tabla priorizada
        visibles = [f for f in filas
                    if (opts['incluir_zz'] or f['estado'] != 'ZZ_DEPRECADA')
                    and (opts['incluir_vacias'] or f['productos'] or f['stock_unidades']
                         or f['venta_unidades'])]
        # `n_top` es UNO solo para el encabezado, el corte y el resto: antes el
        # encabezado usaba `opts['top']` crudo y el corte un `max(1, top)`, así
        # que `--top 0` anunciaba 0 filas, imprimía 1 y descuadraba el resto.
        n_top = min(max(0, int(opts['top'])), len(visibles))
        w(self.style.MIGRATE_HEADING(
            'COLA PRIORIZADA POR IMPACTO ($ stock + $ venta %dd) — %d de %d filas'
            % (dias, n_top, len(visibles))))
        w('  %4s %-24s %6s %8s %13s %8s %13s  %-8s %s'
          % ('id', 'categoría', 'prod', 'stock u', '$ stock', 'vta u', '$ venta',
             'confianza', 'hija v1.2 sugerida'))
        for f in visibles[:n_top]:
            destino = self._destino(f)
            estilo = (self.style.SUCCESS if f['confianza'] == 'ALTA'
                      else self.style.WARNING if f['confianza'] == 'MEDIA'
                      else self.style.NOTICE)
            w('  %4d %-24s %6s %8s %13s %8s %13s  %s %s'
              % (f['categoria_id'], f['categoria'][:24], f"{f['productos']:,}",
                 f"{f['stock_unidades']:,}", f"{f['stock_costo']:,}",
                 f"{f['venta_unidades']:,}", f"{f['venta_monto']:,}",
                 estilo('%-8s' % f['confianza']), destino))
            reparo = self._linea_reparo(f)
            if reparo:
                w(reparo)
        resto = visibles[n_top:]
        if resto:
            w('  … %d filas más (%s en $ de impacto) — están todas en el CSV'
              % (len(resto), f"{sum(r['impacto_pesos'] for r in resto):,}"))
        n_contra = sum(1 for f in visibles if f['reparo_nivel'] == 'CONTRADICE')
        if n_contra:
            w(self.style.ERROR(
                '  %d de las %d filas visibles tienen una propuesta que sus propios '
                'productos CONTRADICEN (columna `reparo` del CSV).'
                % (n_contra, len(visibles))))
        w('')

        # ── resumen por confianza / acción
        w(self.style.MIGRATE_HEADING('RESUMEN DE LA PROPUESTA (solo planas vivas)'))
        vivas = [f for f in filas if f['estado'] == 'PLANA_VIVA']
        for conf in CONFIANZAS:
            g = [f for f in vivas if f['confianza'] == conf]
            if not g:
                continue
            w('  %-8s %2d categorías · %6s productos · %13s de impacto'
              % (conf, len(g), f"{sum(x['productos'] for x in g):,}",
                 f"{sum(x['impacto_pesos'] for x in g):,}"))
        w('')
        for accion in ('MAPEAR', 'REVISAR_MANUAL', 'DEPRECAR_ZZ'):
            g = [f for f in vivas if f['accion_sugerida'] == accion]
            if not g:
                continue
            w('  %-15s %2d categorías · %6s productos · %13s de impacto'
              % (accion, len(g), f"{sum(x['productos'] for x in g):,}",
                 f"{sum(x['impacto_pesos'] for x in g):,}"))
        vacias = [f for f in vivas if f['accion_sugerida'] == 'DEPRECAR_ZZ']
        if vacias:
            w('     (las %d vacías las cierra `limpiar_categorias_viejas --apply`: '
              '%s)' % (len(vacias), ', '.join(str(f['categoria_id']) for f in vacias)))

        # ── concentración: la SUMA la hace el comando, no una persona a mano
        # (el informe de la primera pasada sumó $10.647.867 donde iban
        # $10.650.149 — $2.282 de desfase por sumar las cifras en prosa).
        con_impacto = [f for f in vivas if f['impacto_pesos'] > 0]
        total_impacto = sum(f['impacto_pesos'] for f in con_impacto)
        if total_impacto:
            acum, k = 0, 0
            for f in con_impacto:          # ya vienen ordenadas por impacto desc
                k += 1
                acum += f['impacto_pesos']
                if acum >= 0.95 * total_impacto:
                    break
            w('')
            w('  CONCENTRACIÓN: %d de las %d categorías con impacto suman %s de %s '
              '(%.2f%%)' % (k, len(con_impacto), f'{acum:,}', f'{total_impacto:,}',
                            100.0 * acum / total_impacto))
            w('     %s' % ' · '.join('%s(%d) %s' % (f['categoria'], f['categoria_id'],
                                                    f"{f['impacto_pesos']:,}")
                                     for f in con_impacto[:k]))

        # ── _ZZ_
        zz = [f for f in filas if f['estado'] == 'ZZ_DEPRECADA']
        zz_sucias = [f for f in zz if f['productos'] or f['stock_unidades']
                     or f['venta_unidades']]
        w('')
        w(self.style.MIGRATE_HEADING('DEPRECADAS _ZZ_'))
        w('  %d categorías _ZZ_; %d con productos/stock/venta (deberían estar en 0)'
          % (len(zz), len(zz_sucias)))
        for f in zz_sucias:
            w(self.style.WARNING(
                '     ANOMALÍA %4d %-26s prod=%s stock=%s $venta=%s'
                % (f['categoria_id'], f['categoria'][:26], f['productos'],
                   f['stock_unidades'], f"{f['venta_monto']:,}")))

        # ── CSV
        w('')
        if opts['sin_csv']:
            w(self.style.WARNING('  CSV omitido (--sin-csv).'))
        else:
            ruta = opts['csv_path'] or ('cola_categorias_v12_%s.csv'
                                        % timezone.localtime().strftime('%Y%m%d_%H%M%S'))
            escribir_csv(filas, ruta)
            w(self.style.SUCCESS('  CSV de propuesta: %s (%d filas, %d columnas)'
                                 % (ruta, len(filas), len(CAMPOS_CSV))))
            w('  El CSV es el INSUMO de una decisión humana: este comando no aplica '
              'nada por diseño.')

        w('')
        if connection.queries:
            w('  %d queries (todas SELECT, revertidas) · %.1fs'
              % (len(connection.queries) - q0, time.time() - t0))
        else:
            w('  queries no medibles (DEBUG=False) · %.1fs' % (time.time() - t0))
        logger.info(
            'diagnosticar_cola_categorias_v12: %d planas vivas, %d _ZZ_, '
            '%d productos en cola, $%d de impacto',
            len(vivas), len(zz), sum(f['productos'] for f in vivas),
            sum(f['impacto_pesos'] for f in vivas))
