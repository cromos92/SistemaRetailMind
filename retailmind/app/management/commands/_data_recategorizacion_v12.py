# -*- coding: utf-8 -*-
"""
Datos de la taxonomía v1.2 (árbol físico + especialidades transversales).

NO es un management command (prefijo `_`). Lo consumen:
  - sembrar_taxonomia_v12.py
  - aplicar_recategorizacion_v12.py

Diferencia con _data_recategorizacion.py (pipeline anterior):
  - El árbol Categoria SOLO lleva el tipo físico (calzado/ropa/accesorios → sub).
  - El deporte/uso va como ESPECIALIDADES múltiples en ProductoAtributoValor
    (atributo "Especialidad"), que es lo que consumen los megamenús de
    realsport.cl y calzadospaola.cl.

Fuente de decisiones: docs/recategorizacion_erp_v1.2.xlsx y el proyecto
«re mapeo» (sesión Cowork 14/15-07-2026).
"""

# ── Padres del árbol. Reusan los stubs existentes por nombre:
#    Calzado=79, Ropa=80 (funciona como "vestuario"), Accesorios=81.
PADRES = {
    "calzado": "Calzado",
    "vestuario": "Ropa",
    "accesorios": "Accesorios",
}

# ── Hijos: slug (columna sub_v12 del Excel) -> (padre_slug, nombre Categoria)
SUBS = {
    # calzado
    "zapatillas":   ("calzado", "Zapatillas"),
    "botines":      ("calzado", "Botines"),
    "botas":        ("calzado", "Botas"),
    "sandalias":    ("calzado", "Sandalias y Chalas"),
    "mocasin":      ("calzado", "Mocasines"),
    "zvestir":      ("calzado", "Zapatos de Vestir"),
    "alpargata":    ("calzado", "Alpargatas"),
    "pantuflas":    ("calzado", "Pantuflas"),
    "tacos":        ("calzado", "Tacos"),
    "ballerinas":   ("calzado", "Ballerinas"),
    "plataformas":  ("calzado", "Plataformas"),
    "gateadores":   ("calzado", "Gateadores"),
    "danza":        ("calzado", "Danza"),
    # vestuario (padre "Ropa")
    "poleras":      ("vestuario", "Poleras y Camisetas"),
    "shorts":       ("vestuario", "Shorts"),
    "buzo":         ("vestuario", "Buzos y Pantalones"),
    "chaquetas":    ("vestuario", "Chaquetas y Polerones"),
    "tbano":        ("vestuario", "Trajes de Baño"),
    "mallas":       ("vestuario", "Mallas"),
    # accesorios
    "mochilas":     ("accesorios", "Bolsos y Mochilas"),
    "gorros":       ("accesorios", "Gorros"),
    "medias":       ("accesorios", "Medias y Calcetines"),
    "accvest":      ("accesorios", "Accesorios de Vestuario"),
    "trofeos":      ("accesorios", "Trofeos y Medallas"),
    "protecciones": ("accesorios", "Protecciones"),
    "balones":      ("accesorios", "Balones"),
    "guantes":      ("accesorios", "Guantes"),
    "equipamiento": ("accesorios", "Equipamiento Fitness"),
    "accdep":       ("accesorios", "Accesorios Deportivos"),
}

# ── Especialidades: slug -> (familia, etiqueta legible)
#    Se guardan como AtributoOpcion.valor = slug (estable para APIs/menús).
ESPECIALIDADES = {
    "pasto":     ("Fútbol", "Fútbol pasto natural (FG/MG)"),
    "baby":      ("Fútbol", "Baby fútbol / sintético (TF)"),
    "sala":      ("Fútbol", "Fútsal / sala"),
    "futbol":    ("Fútbol", "Fútbol genérico (accesorios/ropa, no botín)"),
    "running":   ("Performance", "Running"),
    "atletismo": ("Performance", "Atletismo"),
    "basket":    ("Performance", "Basketball"),
    "training":  ("Performance", "Training / Fitness"),
    "tenis":     ("Performance", "Tenis / Pádel / Squash"),
    "voley":     ("Performance", "Voleibol"),
    "handball":  ("Performance", "Handball"),
    "rugby":     ("Performance", "Rugby"),
    "boxeo":     ("Performance", "Boxeo"),
    "artesm":    ("Performance", "Artes marciales"),
    "natacion":  ("Performance", "Natación"),
    "ciclismo":  ("Performance", "Ciclismo"),
    "pingpong":  ("Performance", "Ping pong"),
    "badminton": ("Performance", "Bádminton"),
    "beisbol":   ("Performance", "Béisbol"),
    "hockey":    ("Performance", "Hockey"),
    "pool":      ("Performance", "Pool / Billar"),
    "skate":     ("Performance", "Skate / Patinaje"),
    "recre":     ("Performance", "Recreativos"),
    "golf":      ("Performance", "Golf"),
    "trekking":  ("Outdoor", "Trekking / Outdoor"),
    "camping":   ("Outdoor", "Camping"),
    "pesca":     ("Outdoor", "Pesca y caza"),
    "oficial":   ("Escolar", "Escolar oficial"),
    "blanco":    ("Escolar", "Escolar deportivo blanco"),
    "negro":     ("Escolar", "Escolar deportivo negro"),
    "urbano":    ("Urbano", "Urbano (uso diario)"),
    "cueca":     ("Otros", "Cueca / Danza"),
    "vestir":    ("Otros", "Formal vestir"),
    "fiesta":    ("Otros", "Formal fiesta"),
    "descanso":  ("Otros", "Descanso"),
    "gim":       ("Otros", "Gimnasia"),
    "seguridad": ("Otros", "Seguridad / Trabajo"),
}

NOMBRE_ATRIBUTO_ESPECIALIDAD = "Especialidad"

# ── Género: migración DAMA -> MUJER y creación de BEBÉ.
#    IDs verificados en producción (AtributoOpcion del atributo Género, id=3):
#    706=MUJER 707=HOMBRE 708=NIÑO 709=UNISEX 710=NIÑA 711=DAMA 712=JUVENIL
GENERO_MIGRAR = {"DAMA": "MUJER"}   # por valor, con verificación en runtime
GENERO_CREAR = ["BEBÉ"]
