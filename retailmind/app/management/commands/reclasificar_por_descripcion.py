# -*- coding: utf-8 -*-
"""Reclasificador por DESCRIPCIÓN (la fuente más honesta de qué ES un producto).

Barre TODOS los productos y, cuando la descripción contiene una palabra de tipo
FÍSICO inequívoca (CAMISETA, BALON, ZAPATILLA, BOTIN, MOCHILA, ...), corrige la
categoría v1.2 si la actual está en otro departamento o quedó en una raíz vieja.
Overlay de especialidad 'futbol' para ropa/accesorios de fútbol (camisetas de
club, RAMA FOOTBALL, etc.).

Es 100% determinístico (0 costo de IA). DRY-RUN por defecto; --apply escribe
(categoría en TODAS las bodegas del artículo; especialidad aditiva).

Uso:
    python manage.py reclasificar_por_descripcion            # dry-run + reporte
    python manage.py reclasificar_por_descripcion --solo-mal # solo los que corrige
    python manage.py reclasificar_por_descripcion --apply
"""
import logging
import re
import unicodedata
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (
    Producto, Categoria, Productos_Atributos, AtributoOpcion, ProductoAtributoValor,
)

logger = logging.getLogger('app')


def fold(x):
    x = unicodedata.normalize('NFKD', str(x or ''))
    return ''.join(c for c in x if not unicodedata.combining(c)).upper().strip()


# Reglas ORDENADAS: (regex de palabra, cat_slug, sub_slug). Primera que matchea gana.
# Solo palabras de TIPO FÍSICO inequívoco (alta confianza).
REGLAS = [
    # --- Accesorios muy específicos primero (evita que "BALON FUTBOL" caiga en otra) ---
    (r'BALON(ES)?|PELOTA', 'accesorios', 'balones'),
    (r'CANILLERA|ESPINILLERA|RODILLERA|TOBILLERA|MUNEQUERA|CODERA|MENISQUERA|VENDA|BUCAL|PROTECTOR|CORAQUILLA|SUSPENSOR|\bCASCO\b|COQUILLA|ANTIPARRA', 'accesorios', 'protecciones'),
    (r'GUANTILLA|GUANTE', 'accesorios', 'guantes'),
    (r'MOCHILA|BOLSO|MORRAL|CARTERA|BANANO|BACKPACK|MALETA|BOLSA|CANGUR', 'accesorios', 'mochilas'),
    (r'JOCKEY|VISERA|GORRA|GORRO|BEANIE|PASAMONTA', 'accesorios', 'gorros'),
    (r'CALCETA|CALCETIN|SOQUETE|\bMEDIA(S)?\b|CANILLA', 'accesorios', 'medias'),
    (r'TROFEO|MEDALLA|\bCOPA\b|GALVANO', 'accesorios', 'trofeos'),
    (r'MANCUERNA|\bPESA(S)?\b|\bDISCO(S)?\b|\bBARRA\b|COLCHONETA|CUERDA|BANDA ELAST|KETTLE|SOGA|COMBA', 'accesorios', 'equipamiento'),
    (r'CINTURON|\bCORREA\b|CINTILLO|SCRUNCHIE|COLET|PULSERA|TRABA\b|PINCHE', 'accesorios', 'accvest'),
    # --- Ropa ---
    (r'CAMISETA|\bPOLERA\b|\bPOLO\b|\bJERSEY\b|\bJSY\b|CAMISA|PRIMERA CAPA|\bTEE\b', 'vestuario', 'poleras'),
    (r'\bSHORT(S)?\b|\bCALZA\b|BERMUDA|\bPATA\b', 'vestuario', 'shorts'),
    (r'POLERON|CHAQUETA|\bPARKA\b|HOODIE|CORTAVIENTO|\bCHALECO\b', 'vestuario', 'chaquetas'),
    (r'PANTALON|\bBUZO\b|JOGGER|LEGGING', 'vestuario', 'buzo'),
    (r'BIKINI|TRIKINI|\bSUNGA\b|TRAJE DE BA|\bZUNGA\b|MALLA DE BA', 'vestuario', 'tbano'),
    # --- Calzado ---
    (r'ZAPATILLA', 'calzado', 'zapatillas'),
    (r'\bBOTIN(ES)?\b|\bBTN\b', 'calzado', 'botines'),
    (r'\bBOTA(S)?\b', 'calzado', 'botas'),
    (r'SANDALIA|\bCHALA(S)?\b|HAWAIANA|\bTONGA\b|OJOTA', 'calzado', 'sandalias'),
    (r'MOCASIN', 'calzado', 'mocasin'),
    (r'PANTUFLA', 'calzado', 'pantuflas'),
    (r'BALLERINA|BALERINA|GUILLERMINA', 'calzado', 'ballerinas'),
    (r'ALPARGATA', 'calzado', 'alpargata'),
    (r'GATEADOR', 'calzado', 'gateadores'),
    # --- Pistas de descripción específicas de ESTE catálogo (baja prioridad,
    #     al final: solo aplican si nada físico matcheó antes) ---
    (r'\bMODA\b', 'calzado', 'zapatillas'),    # "MODA" = zapatilla urbana
    (r'\bVESTIR\b', 'calzado', 'zvestir'),     # "VESTIR" = zapato de vestir
]
REGLAS = [(re.compile(r'\b(' + pat + r')') if not pat.startswith(r'\b') else re.compile(pat), c, s)
          for pat, c, s in REGLAS]

# Overlay fútbol: ropa/accesorio de fútbol → especialidad 'futbol'
RE_FUTBOL = re.compile(r'FUTBOL|FOOTBALL|\bFUTSAL\b|ARQUERO|SELECCION|MUNDIAL')
SUB_PADRE = {'calzado': 'Calzado', 'vestuario': 'Ropa', 'accesorios': 'Accesorios'}
SUB_NOMBRE = {
    'zapatillas': 'Zapatillas', 'botines': 'Botines', 'botas': 'Botas',
    'sandalias': 'Sandalias y Chalas', 'mocasin': 'Mocasines', 'pantuflas': 'Pantuflas',
    'ballerinas': 'Ballerinas', 'alpargata': 'Alpargatas', 'gateadores': 'Gateadores',
    'zvestir': 'Zapatos de Vestir',
    'poleras': 'Poleras y Camisetas', 'shorts': 'Shorts', 'buzo': 'Buzos y Pantalones',
    'chaquetas': 'Chaquetas y Polerones', 'tbano': 'Trajes de Baño',
    'balones': 'Balones', 'protecciones': 'Protecciones', 'guantes': 'Guantes',
    'mochilas': 'Bolsos y Mochilas', 'gorros': 'Gorros', 'medias': 'Medias y Calcetines',
    'trofeos': 'Trofeos y Medallas', 'equipamiento': 'Equipamiento Fitness',
    'accvest': 'Accesorios de Vestuario',
}


class Command(BaseCommand):
    help = "Corrige la categoría v1.2 usando la palabra de tipo de la descripción (dry-run por defecto)"

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--solo-mal', action='store_true',
                            help='Reporta/aplica solo donde difiere de la categoría actual')

    def _tipo_por_desc(self, desc):
        d = fold(desc)
        for rx, cat, sub in REGLAS:
            if rx.search(d):
                return cat, sub
        return None, None

    def handle(self, *args, **opts):
        apply_ = opts['apply']
        # Mapa sub_slug -> objeto Categoria hija v1.2
        cat_obj = {}
        for slug, nombre in SUB_NOMBRE.items():
            padre = SUB_PADRE[[c for c, subs in
                               {'calzado': ['zapatillas','botines','botas','sandalias','mocasin','pantuflas','ballerinas','alpargata','gateadores','zvestir'],
                                'vestuario': ['poleras','shorts','buzo','chaquetas','tbano'],
                                'accesorios': ['balones','protecciones','guantes','mochilas','gorros','medias','trofeos','equipamiento','accvest']}.items()
                               if slug in subs][0]]
            obj = Categoria.objects.filter(nombre__iexact=nombre, padre__nombre__iexact=padre).first()
            if obj is None:
                raise CommandError(f"Falta la categoría '{nombre}' — corre sembrar_taxonomia_v12 --apply")
            cat_obj[slug] = obj

        esp_attr = Productos_Atributos.objects.filter(nombre__iexact='Especialidad').first()
        futbol_op = AtributoOpcion.objects.filter(atributo=esp_attr, valor='futbol').first() if esp_attr else None

        # Recorremos por artículo (una descisión por artículo, todas las bodegas)
        qs = (Producto.objects.select_related('categoria', 'categoria__padre')
              .values('articulo', 'descripcion', 'categoria_id',
                      'categoria__nombre', 'categoria__padre__nombre'))
        por_art = {}
        for p in qs.iterator(chunk_size=5000):
            a = (p['articulo'] or '').strip()
            if a and a not in por_art:
                por_art[a] = p

        n_match = n_corrige = n_futbol = 0
        cambios = Counter()
        ejemplos = []
        destino_por_art = {}
        for a, p in por_art.items():
            cat, sub = self._tipo_por_desc(p['descripcion'])
            if not sub:
                continue
            n_match += 1
            actual_sub = fold(p['categoria__nombre'])
            actual_padre = fold(p['categoria__padre__nombre'])
            destino = cat_obj[sub]
            # ¿corrige? si la categoría actual NO es esa hija (otro depto, raíz vieja, o sub distinta)
            difiere = (p['categoria_id'] != destino.id)
            es_futbol = bool(RE_FUTBOL.search(fold(p['descripcion']))) and cat != 'calzado'
            if difiere:
                n_corrige += 1
                cambios[f"{SUB_PADRE[cat]} › {SUB_NOMBRE[sub]}"] += 1
                if len(ejemplos) < 20:
                    ejemplos.append(f"{(p['descripcion'] or '')[:24]:24} | {p['categoria__nombre']} -> {SUB_PADRE[cat]}/{SUB_NOMBRE[sub]}")
            if es_futbol:
                n_futbol += 1
            if difiere or es_futbol:
                destino_por_art[a] = (destino if difiere else None, es_futbol)

        modo = 'APPLY' if apply_ else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(f"[{modo}] reclasificar_por_descripcion"))
        self.stdout.write(f"  artículos con palabra de tipo : {n_match}")
        self.stdout.write(f"  que se CORRIGEN (difieren)     : {n_corrige}")
        self.stdout.write(f"  overlay especialidad fútbol    : {n_futbol}")
        self.stdout.write("  correcciones por destino (top):")
        for k, v in cambios.most_common(15):
            self.stdout.write(f"     {v:5}  {k}")
        self.stdout.write("  ejemplos:")
        for e in ejemplos:
            self.stdout.write(f"     {e}")

        if apply_:
            n_prod = n_esp = 0
            with transaction.atomic():
                for a, (destino, es_futbol) in destino_por_art.items():
                    prods = list(Producto.objects.filter(articulo__iexact=a))
                    for pr in prods:
                        if destino is not None and pr.categoria_id != destino.id:
                            pr.categoria = destino
                            pr.save(update_fields=['categoria', 'fecha_actualizacion'])
                            n_prod += 1
                        if es_futbol and futbol_op is not None:
                            _, creado = ProductoAtributoValor.objects.get_or_create(
                                producto=pr, atributo=esp_attr, opcion=futbol_op)
                            if creado:
                                n_esp += 1
            self.stdout.write(self.style.SUCCESS(
                f"  ESCRITO: {n_prod} productos recategorizados, {n_esp} etiquetas fútbol"))
        else:
            self.stdout.write(self.style.WARNING("  (dry-run: nada escrito; usa --apply)"))
        logger.info("reclasificar_por_descripcion %s: match=%d corrige=%d futbol=%d",
                    modo, n_match, n_corrige, n_futbol)
