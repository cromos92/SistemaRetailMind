"""
Datos de mapeo para la re-categorización del catálogo (Paola / RealSport).

NO es un management command (el prefijo `_` lo excluye de `manage.py help`).
Lo consume `recategorizar_catalogo.py`.

Contiene:
  - TAXONOMIA       : árbol objetivo Departamento -> [Tipo, ...]
  - MAPEO_DIRECTO   : id_categoria_actual -> (departamento, tipo, revisar)
  - REGLAS_KEYWORD  : tokens en `descripcion` -> (departamento, tipo)  (orden = prioridad)
  - REGLAS_MARCA    : marca -> (departamento, tipo)  (para productos sin categoría útil)
  - NO_PRODUCTO     : descripciones que NO son productos (envíos, saldos, fletes...)

Las decisiones se basan en el mapeo real de la BD de producción (137.402 filas,
63.707 artículos, 83 categorías planas). Ver el plan en
.claude/plans/tienes-acceso-a-la-graceful-dongarra.md
"""

# ── Departamentos (raíces del árbol). Reusan los stubs existentes por nombre:
#    Calzado=79, Deportes=82, Ropa=80, Accesorios=81 (get_or_create los encuentra).
CALZADO = "Calzado"
DEPORTE = "Deportes"
ROPA = "Ropa"
ACCESORIOS = "Accesorios"
OTROS = "Otros"

# ── Taxonomía objetivo: Departamento -> Tipos (hijos vía Categoria.padre)
TAXONOMIA = {
    CALZADO: [
        "Zapatillas Urbanas", "Zapatillas Lona", "Botas", "Botines", "Mocasines",
        "Sandalias y Chalas", "Ballerinas y Bajas", "Plataformas", "Pantuflas",
        "Zuecos", "Zapatos de Vestir", "Escolar", "Alpargatas", "Cueca",
        "Bebé y Gateadores", "Confort y Ortopédico",
    ],
    DEPORTE: [
        "Fútbol", "Running", "Training y Fitness", "Basketball", "Tenis", "Boxeo",
        "Artes Marciales", "Natación", "Voleyball", "Atletismo", "Ciclismo",
        "Outdoor y Camping", "Patinaje y Skate", "Pesca y Caza", "Gimnasia",
        "Handball", "Rugby", "Hockey", "Golf", "Béisbol", "Ping Pong", "Squash",
        "Pool y Billar", "Bádminton",
    ],
    ROPA: [
        "Poleras y Camisetas", "Polerones y Chaquetas", "Pantalones y Buzos",
        "Shorts", "Trajes de Baño y Mallas", "Calcetines y Medias",
    ],
    ACCESORIOS: [
        "Bolsos y Mochilas", "Gorros", "Correas", "Pulseras y Joyas",
        "Accesorios de Pelo", "Protecciones y Canilleras",
    ],
    OTROS: [
        "Trofeos y Medallas", "Implementos y Repuestos", "Juegos de Salón",
        "Seguridad", "No-Producto",
    ],
}

# ── Mapeo determinista: id de la categoría actual -> (departamento, tipo, revisar)
#    `revisar=True` marca buckets mixtos que conviene revisar a mano en el Excel.
#    Los ids NO listados aquí caen a la heurística (capa 2): principalmente
#    1=RAMA CASUAL, 63=SIN DEFINIR, 33=REINA, 56=MAFALDA, 83=Casual, stubs y junk.
MAPEO_DIRECTO = {
    2:  (CALZADO, "Zapatos de Vestir", True),       # VESTIR (mixto)
    3:  (DEPORTE, "Natación", False),
    4:  (DEPORTE, "Fútbol", False),                  # SET DE FOOTBAL
    5:  (CALZADO, "Zapatillas Lona", False),
    6:  (CALZADO, "Cueca", False),                   # RAMA CUECA
    7:  (CALZADO, "Confort y Ortopédico", True),     # SALUD Y ORTOPEDIA (mixto)
    8:  (ROPA, "Poleras y Camisetas", False),        # POLERA
    9:  (DEPORTE, "Fútbol", False),                  # RAMA FOOTBALL
    10: (ROPA, "Shorts", False),                     # SHORT Y PATAS
    11: (CALZADO, "Botas", False),
    12: (ACCESORIOS, "Bolsos y Mochilas", False),    # BOLSOS
    13: (CALZADO, "Mocasines", False),
    14: (CALZADO, "Sandalias y Chalas", False),      # CHALAS
    15: (DEPORTE, "Running", False),
    16: (DEPORTE, "Training y Fitness", False),      # RAMA TRAINNIG
    17: (DEPORTE, "Tenis", False),
    18: (ACCESORIOS, "Gorros", False),
    19: (DEPORTE, "Fútbol", False),                  # RAMA BABY FOTBALL
    20: (ROPA, "Trajes de Baño y Mallas", False),    # TRAJE DE BAÑO (encoding roto)
    21: (ROPA, "Poleras y Camisetas", False),        # CAMISETA
    22: (ROPA, "Pantalones y Buzos", False),         # BUZO- PANTALONES
    23: (ROPA, "Calcetines y Medias", False),        # MEDIAS
    24: (ROPA, "Calcetines y Medias", False),        # CALCETAS
    25: (DEPORTE, "Basketball", False),
    26: (CALZADO, "Botines", False),
    27: (ACCESORIOS, "Accesorios de Pelo", False),   # TRABA
    28: (CALZADO, "Zuecos", False),                  # PUNTILLA Y ZUECO
    29: (CALZADO, "Plataformas", False),
    30: (ROPA, "Trajes de Baño y Mallas", False),    # MALLA
    31: (ROPA, "Polerones y Chaquetas", False),      # CHAQUETAS Y CHALECOS
    32: (ROPA, "Polerones y Chaquetas", False),      # POLERON
    34: (CALZADO, "Bebé y Gateadores", False),       # GATEADORES
    35: (DEPORTE, "Boxeo", False),
    36: (DEPORTE, "Atletismo", False),
    37: (DEPORTE, "Voleyball", False),
    38: (DEPORTE, "Pesca y Caza", False),
    39: (DEPORTE, "Patinaje y Skate", False),
    40: (DEPORTE, "Pool y Billar", False),
    41: (DEPORTE, "Training y Fitness", False),      # RAMA FISICOCULTURISMO
    42: (DEPORTE, "Artes Marciales", False),
    43: (DEPORTE, "Gimnasia", False),                # GIMNASIA ARTISTICA
    44: (DEPORTE, "Bádminton", False),               # RAMA BALMINTON
    45: (DEPORTE, "Ping Pong", False),
    46: (CALZADO, "Zapatillas Urbanas", True),       # ZAPATON
    47: (ACCESORIOS, "Pulseras y Joyas", False),     # PULSERA
    48: (DEPORTE, "Outdoor y Camping", False),
    49: (OTROS, "Juegos de Salón", False),
    50: (DEPORTE, "Training y Fitness", False),      # RAMA FITNESS
    51: (DEPORTE, "Béisbol", False),
    52: (DEPORTE, "Gimnasia", False),                # CHICLES RITMICAS
    53: (DEPORTE, "Hockey", False),                  # RAMA HOKEY
    54: (OTROS, "Trofeos y Medallas", False),
    55: (ACCESORIOS, "Correas", False),
    57: (DEPORTE, "Outdoor y Camping", False),       # RAMA CAMPING
    58: (CALZADO, "Pantuflas", False),
    59: (CALZADO, "Alpargatas", False),
    60: (OTROS, "Implementos y Repuestos", False),   # BOMBIN Y AGUJA
    61: (DEPORTE, "Rugby", False),
    62: (DEPORTE, "Ciclismo", False),
    64: (CALZADO, "Ballerinas y Bajas", False),      # CHINITAS
    65: (CALZADO, "Ballerinas y Bajas", False),      # PLANO
    66: (ROPA, "Poleras y Camisetas", False),        # CAMISA
    67: (DEPORTE, "Golf", False),
    68: (OTROS, "Seguridad", False),
    69: (DEPORTE, "Handball", False),                # RAMA DE HANDBALL
    70: (DEPORTE, "Squash", False),
    71: (DEPORTE, "Handball", False),                # RAMA HANDBALL (merge)
    72: (OTROS, "Seguridad", False),                 # SEGUIRAD (merge)
    74: (ACCESORIOS, "Bolsos y Mochilas", False),    # BOL (merge)
    76: (ACCESORIOS, "Bolsos y Mochilas", False),    # BOLSOS Y MOCHILA (merge)
    77: (ROPA, "Trajes de Baño y Mallas", False),    # TRAJE DE BAÑO
}

# ── Categorías actuales que se deprecan al final (renombrar con prefijo, NO borrar
#    con DELETE porque on_delete=CASCADE arrastraría productos).
#    1=RAMA CASUAL y 63=SIN DEFINIR quedan vacías tras el pipeline.
CATEGORIAS_A_DEPRECAR = [1, 63, 73, 75, 78]   # + todas las planas viejas tras migrar

# ── Heurística (capa 2): qué ids default-ean a "Zapatillas Urbanas".
#    RAMA CASUAL (1) y Casual (83) son casi 100% sneakers atléticos/urbanos.
IDS_DEFAULT_ZAPATILLAS = {1, 83}

# ── Tokens de tipo en `descripcion` (UPPER, sin tildes-insensible se hace en el cmd).
#    Orden = prioridad: el primero que matchea gana. Lo específico va primero.
REGLAS_KEYWORD = [
    (("BOTIN",),                                  CALZADO, "Botines"),
    (("BOTA",),                                   CALZADO, "Botas"),
    (("SANDALIA", "CHALA", "HAWAI", "FLIP "),     CALZADO, "Sandalias y Chalas"),
    (("ZUECO",),                                  CALZADO, "Zuecos"),
    (("PANTUFLA",),                               CALZADO, "Pantuflas"),
    (("MOCASIN",),                                CALZADO, "Mocasines"),
    (("ALPARGATA",),                              CALZADO, "Alpargatas"),
    (("BALLERINA", "BALERINA"),                   CALZADO, "Ballerinas y Bajas"),
    (("CANILLERA", "ESPINILLERA", "RODILLERA",
      "MUÑEQUERA", "COQUILLA", "PROTECCION"),     ACCESORIOS, "Protecciones y Canilleras"),
    (("MOCHILA", "BOLSO", "MORRAL", "BOLSON", "BOLSA",
      "CARTERA", "RIÑONERA", "BALONERO"),         ACCESORIOS, "Bolsos y Mochilas"),
    (("SCRUNCHIE", "COLET", "CINTILLO"),          ACCESORIOS, "Accesorios de Pelo"),
    (("GORRO", "JOCKEY", "JOKEY", "VISERA"),      ACCESORIOS, "Gorros"),
    (("CALCETIN", "CALCETA", "SOQUETE"),          ROPA, "Calcetines y Medias"),
    (("HOODIE", "POLERON", "PARKA", "CHAQUETA",
      "CHALECO", "CANGURO"),                      ROPA, "Polerones y Chaquetas"),
    (("POLERA", "CAMISETA", "T-SHIRT"),           ROPA, "Poleras y Camisetas"),
    (("SHORT", "CALZA", "BERMUDA"),               ROPA, "Shorts"),
    (("PANTALON", "JOGGER", "LEGGING", "BUZO"),   ROPA, "Pantalones y Buzos"),
    (("BIKINI", "TRAJE DE BAÑO", "TRIKINI",
      "SUNGA"),                                   ROPA, "Trajes de Baño y Mallas"),
    (("GUANTE ARQUERO", "GUANTE DE ARQUERO",
      "RED FUTBOL", "RED DE FUTBOL"),             DEPORTE, "Fútbol"),
    (("GUANTE BOX", "SACO BOX"),                  DEPORTE, "Boxeo"),
    (("PATIN", "SKATE"),                          DEPORTE, "Patinaje y Skate"),
    (("TROFEO", "MEDALLA"),                       OTROS, "Trofeos y Medallas"),
    # Ambiguos (se marcan revisar=True en el comando):
    (("BALON", "PELOTA"),                         DEPORTE, "Fútbol"),
    (("CONO", "PETO", "ESCALERA"),                DEPORTE, "Training y Fitness"),
    # Genérico de calzado al final:
    (("ZAPATILLA", "ZAPATO"),                     CALZADO, "Zapatillas Urbanas"),
]

# Tokens cuyo match es poco confiable -> revisar=True aunque haya keyword.
KEYWORDS_AMBIGUOS = {"BALON", "PELOTA", "CONO", "PETO", "ESCALERA", "BOTA"}

# ── Marca -> tipo, para productos de equipamiento deportivo sin categoría útil.
#    Marcas de balones/equipo (ambiguas -> revisar=True en el comando).
REGLAS_MARCA = {
    "EVERLAST": (DEPORTE, "Boxeo"),
    "MOLTEN":   (DEPORTE, "Voleyball"),
    "WILSON":   (DEPORTE, "Tenis"),
    "MIKASA":   (DEPORTE, "Voleyball"),
    "SPALDING": (DEPORTE, "Basketball"),
    "PENALTY":  (DEPORTE, "Fútbol"),
    # Marcas de calzado: barren el residuo de "SIN DEFINIR" (cat 63), que no
    # entra al default de RAMA CASUAL. Quedan revisar=True por diseño.
    "SKECHERS": (CALZADO, "Zapatillas Urbanas"),
    "NIKE": (CALZADO, "Zapatillas Urbanas"),
    "NIKE PANAMA": (CALZADO, "Zapatillas Urbanas"),
    "NIKE TODODEPORTE": (CALZADO, "Zapatillas Urbanas"),
    "ADIDAS": (CALZADO, "Zapatillas Urbanas"),
    "REEBOK": (CALZADO, "Zapatillas Urbanas"),
    "REEBOK VALCAO": (CALZADO, "Zapatillas Urbanas"),
    "PUMA": (CALZADO, "Zapatillas Urbanas"),
    "FILA": (CALZADO, "Zapatillas Urbanas"),
    "NEW BALANCE": (CALZADO, "Zapatillas Urbanas"),
    "UNDERARMON": (CALZADO, "Zapatillas Urbanas"),
    "CONVERSE": (CALZADO, "Zapatillas Lona"),
}

# ── Descripciones que NO son productos vendibles -> OTROS/No-Producto + excluir_de_analitica.
#    Match por igualdad exacta (UPPER, strip) o prefijo. OJO: "SALDO" como sufijo de
#    un producto real ("CHALA SALDO") NO cae aquí porque la keyword de tipo va primero.
NO_PRODUCTO_EXACTO = {
    "SALDO", "SALDO3", "ENVIOS", "ENVIO", "TRANSPORTE", "TRANSPORTE MERCADERIA",
    "FLETE", "DESPACHO", "SERVICIO", "AJUSTE", "VARIOS", "SIN STOCK", "S/STOCK",
    "DIFER VISA", "COSTO ENVIO", "DIFERENCIA",
}
NO_PRODUCTO_PREFIJOS = ("ENVIO", "TRANSPORTE", "FLETE", "DESPACHO ", "COSTO ENVIO", "DIFER ")

# ── Descripciones genéricas SIN nombre de modelo: no se pueden sub-clasificar
#    por internet (no hay qué buscar). Quedan en "Zapatillas Urbanas" genérico.
GENERIC_DESC = {
    "ZAPATILLA", "ZAPATILLAS", "ZAPATO", "ZAPATOS", "CALZADO", "MODELO",
    "VARIOS", "SURTIDO", "SIN DESCRIPCION", "S/D", "ZAP", "",
}

# ── Normalización del sub-tipo que devuelve el clasificador de modelos
#    (conocimiento + web) a un nodo (departamento, tipo) del árbol.
#    Clave en minúsculas; el comando hace lower() del sub-tipo recibido.
SUBTIPO_A_NODO = {
    "urbana": (CALZADO, "Zapatillas Urbanas"),
    "urbano": (CALZADO, "Zapatillas Urbanas"),
    "lifestyle": (CALZADO, "Zapatillas Urbanas"),
    "casual": (CALZADO, "Zapatillas Urbanas"),
    "moda": (CALZADO, "Zapatillas Urbanas"),
    "sneaker": (CALZADO, "Zapatillas Urbanas"),
    "lona": (CALZADO, "Zapatillas Lona"),
    "canvas": (CALZADO, "Zapatillas Lona"),
    "running": (DEPORTE, "Running"),
    "trail running": (DEPORTE, "Running"),
    "basketball": (DEPORTE, "Basketball"),
    "basket": (DEPORTE, "Basketball"),
    "baloncesto": (DEPORTE, "Basketball"),
    "futbol": (DEPORTE, "Fútbol"),
    "football": (DEPORTE, "Fútbol"),
    "soccer": (DEPORTE, "Fútbol"),
    "futsal": (DEPORTE, "Fútbol"),
    "training": (DEPORTE, "Training y Fitness"),
    "gym": (DEPORTE, "Training y Fitness"),
    "crossfit": (DEPORTE, "Training y Fitness"),
    "fitness": (DEPORTE, "Training y Fitness"),
    "tenis": (DEPORTE, "Tenis"),
    "tennis": (DEPORTE, "Tenis"),
    "skate": (DEPORTE, "Patinaje y Skate"),
    "skateboarding": (DEPORTE, "Patinaje y Skate"),
    "outdoor": (DEPORTE, "Outdoor y Camping"),
    "trekking": (DEPORTE, "Outdoor y Camping"),
    "hiking": (DEPORTE, "Outdoor y Camping"),
    "trail": (DEPORTE, "Outdoor y Camping"),
    "boxeo": (DEPORTE, "Boxeo"),
    "boxing": (DEPORTE, "Boxeo"),
    "botin": (CALZADO, "Botines"),
    "bota": (CALZADO, "Botas"),
    "sandalia": (CALZADO, "Sandalias y Chalas"),
    "chala": (CALZADO, "Sandalias y Chalas"),
    "mocasin": (CALZADO, "Mocasines"),
    "ballerina": (CALZADO, "Ballerinas y Bajas"),
    "escolar": (CALZADO, "Escolar"),
    "colegial": (CALZADO, "Escolar"),
}

# Forma física esperada por departamento (para detectar mal-categorizados con la talla).
FORMA_ESPERADA = {
    CALZADO: "CALZADO",
    ROPA: "ROPA",
    # Deportes/Accesorios/Otros mezclan formas -> no se usa para flag de conflicto.
}
