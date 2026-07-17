"""
Fase 2 de la re-categorización: clasifica `modelos.json` (marca+modelo del
residuo RAMA CASUAL/Casual/SIN DEFINIR) a (departamento, tipo) del árbol,
por CONOCIMIENTO de líneas de producto (GO RUN=running, PREDATOR=fútbol...).

Puro JSON — NO toca la base de datos. Genera `modelos_clasificados.json`
para consumir con:  python manage.py recategorizar_catalogo --modelos modelos_clasificados.json

Capas (la primera que matchea gana):
  1) LINEAS por marca : tokens de línea de modelo -> nodo (confianza alta)
  2) GENERICOS        : tokens de tipo válidos en cualquier marca (alta/media)
  3) MARCA_DEFAULT    : default por marca (confianza media)
Sin match -> se omite del JSON (el pipeline los deja en Zapatillas Urbanas
con revisar=Sí, que es el default correcto para el residuo RAMA CASUAL).

Valida cada nodo emitido contra la TAXONOMIA real de _data_recategorizacion.
"""
import json
import sys
import unicodedata
from collections import Counter

from app.management.commands._data_recategorizacion import TAXONOMIA

# ── Nodos (deben existir en TAXONOMIA; se valida abajo) ──
URB   = ("Calzado", "Zapatillas Urbanas")
LONA  = ("Calzado", "Zapatillas Lona")
BOTIN = ("Calzado", "Botines")
BOTA  = ("Calzado", "Botas")
CHALA = ("Calzado", "Sandalias y Chalas")
MOCA  = ("Calzado", "Mocasines")
VEST  = ("Calzado", "Zapatos de Vestir")
ESC   = ("Calzado", "Escolar")
BALLE = ("Calzado", "Ballerinas y Bajas")
PANT  = ("Calzado", "Pantuflas")
CONF  = ("Calzado", "Confort y Ortopédico")
BEBE  = ("Calzado", "Bebé y Gateadores")
ALPA  = ("Calzado", "Alpargatas")
RUN   = ("Deportes", "Running")
VOLEY = ("Deportes", "Voleyball")
POLER = ("Ropa", "Poleras y Camisetas")
CALCE = ("Ropa", "Calcetines y Medias")
BOLSO = ("Accesorios", "Bolsos y Mochilas")
FUT   = ("Deportes", "Fútbol")
BASQ  = ("Deportes", "Basketball")
TRAIN = ("Deportes", "Training y Fitness")
TEN   = ("Deportes", "Tenis")
SKT   = ("Deportes", "Patinaje y Skate")
OUT   = ("Deportes", "Outdoor y Camping")


def fold(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(s.upper().split())


# ── Alias de marca (variantes de proveedor/typos -> marca canónica) ──
MARCA_ALIAS = {
    'NIKE PANAMA': 'NIKE', 'NIKE TODODEPORTE': 'NIKE', 'NIKE USA': 'NIKE',
    'NIKE VALCAO': 'NIKE',
    'UNDERARMON': 'UNDER ARMOUR', 'UNDER ARMON': 'UNDER ARMOUR',
    'BEIRO RIO': 'BEIRA RIO', 'PICADELLY': 'PICCADILLY',
    'CHAMPION': 'CHAMPION', 'ECKO UNLTDA': 'ECKO', 'ECKO UNLTD': 'ECKO',
    'REEBOK VALCAO': 'REEBOK', 'ADIDAS VALCAO': 'ADIDAS',
}

# ── Capa 1: líneas de modelo por marca (tokens buscados con `in`, en orden) ──
LINEAS = {
    'SKECHERS': [
        (('GO RUN', 'GORUN', 'RAZOR', 'MAX CUSHIONING'), RUN, 'alta'),
        (('GO TRAIL', 'HILLCREST', 'TERRAFORM'), OUT, 'alta'),
        (('ARCH FIT', 'GO WALK', 'GOWALK', 'GLIDE-STEP', 'GLIDE STEP', 'D LITES',
          "D'LITES", 'DLITES', 'D LITE', 'UNO', 'MICROSPEC', 'DYNAMIGHT', 'BOUNDER',
          'SUMMITS', 'FLEX APPEAL', 'SKECH', 'TRACK', 'GLIMMER', 'JOY', 'BOBS',
          'STAND ON AIR', 'STAND', 'FLEX', 'ENERGY', 'STAMINA', 'VIGOR',
          'TWINKLE', 'HYPNO', 'SOLAR FUEL', 'ULTRA'), URB, 'alta'),
        # Skechers residual = lifestyle/confort (LITE, EDGERIDE, HEART LIGHTS,
        # UNICORN, SHIMMER, FLASH, QUICK, TWISTY, SQUAD AIR, MILLION AIR...)
        ((' ',), URB, 'media'),
    ],
    'ADIDAS': [
        (('RUNFALCON', 'DURAMO', 'GALAXY', 'LITE RACER', 'FORTARUN', 'RESPONSE',
          'SUPERNOVA', 'ULTRABOOST', 'ULTRA BOOST', 'ADIZERO', 'EQ19', 'EQ21',
          'FLUIDFLOW', 'SHOWTHEWAY', 'RUNNER', 'FORTAGYM', 'PUREBOOST',
          'QUESTAR', 'STARTYOURRUN', 'CORERACER', 'SWIFT RUN',
          'FALCON ELITE', 'ENERGY CLOUD', 'FLUIDUP', 'NOVA'), RUN, 'alta'),
        (('TERREX', 'AX2', 'AX3', 'AX4', 'HIKER', 'DAROGA', 'KANADIA'), OUT, 'alta'),
        (('PREDATOR', 'COPA', 'NEMEZIZ', 'X SPEED', 'SPEEDFLOW', 'SPEEDPORTAL',
          'GOLETTO', 'DEPORTIVO'), FUT, 'alta'),
        (('ADILETTE', 'ADISSAGE', 'DURAMO SL SLIDE'), CHALA, 'alta'),
        (('SEELEY', 'BUSENITZ', 'MATCHBREAK'), SKT, 'alta'),
        (('DROPSET', 'TRAINER', 'AMPLIMOVE'), TRAIN, 'alta'),
        (('PRO NEXT', 'PRO MODEL', 'PRO BOUNCE', 'OWNTHEGAME', 'D.O.N',
          'DAME ', 'HARDEN', 'TRAE'), BASQ, 'alta'),
        (('SUPERSTAR', 'STAN SMITH', 'GAZELLE', 'FORUM', 'CONTINENTAL',
          'GRAND COURT', 'ADVANTAGE', 'BREAKNET', 'VL COURT', 'COURT',
          'TENSAUR', 'HOOPS', 'LK TRAINER', 'DAILY', 'VULC', 'NIZZA', 'SAMBA',
          'CAMPUS', 'OZELLE', 'OZWEEGO', 'RETROPY', 'FUKASA', 'PARK ST',
          'ZNSORED', 'KANTANA', 'RUN 60', 'RUN 70', 'RUN 80',
          'CLOUDFOAM', 'ZX ', 'VLNEO', 'ADRIA', 'TURNAROUND', 'STA FLUID',
          'FALCON'), URB, 'alta'),
        ((' ',), URB, 'media'),   # residual adidas = lifestyle
    ],
    'NIKE': [
        (('REVOLUTION', 'DOWNSHIFTER', 'QUEST', 'PEGASUS', 'ZOOM', 'FREE',
          'RUN SWIFT', 'RUNSWIFT', 'FLEX', 'WINFLO', 'VOMERO', 'STRUCTURE',
          'INTERACT', 'RUN', 'WAFFLE', 'RENEW'), RUN, 'alta'),
        (('MERCURIAL', 'TIEMPO', 'PHANTOM', 'BRAVATA', 'VAPOR', 'LEGEND',
          'MAGISTA', 'HYPERVENOM', 'FUTBOL'), FUT, 'alta'),
        (('TEAM HUSTLE', 'JORDAN', 'LEBRON', 'KYRIE', 'KD', 'GIANNIS',
          'PRECISION', 'RENEW ELEVATE', 'OVERPLAY', 'FLIGHT'), BASQ, 'alta'),
        (('METCON', 'LEGEND ESSENTIAL', 'MC TRAINER', 'TRAINING'), TRAIN, 'alta'),
        (('SB ', 'JANOSKI', 'STEFAN'), SKT, 'media'),
        (('AIR MAX', 'AIR FORCE', 'AIRMAX', 'CORTEZ', 'DUNK', 'BLAZER',
          'COURT VISION', 'COURT BOROUGH', 'COURT LEGACY', 'COURT ROYALE',
          'TANJUN', 'TANIUN', 'TAJUN', 'AIR', 'COURT', 'MD RUNNER', 'MD VALIANT',
          'PICO', 'STAR RUNNER', 'WEARALLDAY', 'EXPLORE', 'CITY REP',
          'SUKETO'), URB, 'alta'),
        ((' ',), URB, 'media'),   # residual nike = lifestyle
    ],
    'REEBOK': [
        (('REALFLEX', 'ENERGEN', 'RUNNER', 'LITE ', 'ZIG', 'FLOATRIDE',
          'SPEEDBREEZE', 'RUSH', 'YOURFLEX', 'TWISTFORM', 'SPEEDLUX',
          'PHEEHAN', 'TRIPLEHALL', 'HARMONY'), RUN, 'alta'),
        (('NANO', 'FLEXAGON', 'TRAINER', 'TRAINET'), TRAIN, 'alta'),
        (('ROYAL', 'CLASSIC', 'PRINCESS', 'F/S', 'CL ', 'COMPLETE', 'GLIDE',
          'COURT', 'EXOFIT', 'CLUB', 'REWIND', 'VECTOR', 'NPC'), URB, 'alta'),
        ((' ',), URB, 'media'),   # residual reebok = lifestyle
    ],
    'PUMA': [
        (('ANZARUN', 'FUN RACER', 'FLYER', 'VELOCITY', 'MAGNIFY', 'SOFTRIDE',
          'ELECTRIFY', 'TWITCH', 'RETALIATE', 'NRGY', 'RUN'), RUN, 'alta'),
        (('FUTURE', 'ULTRA', 'KING', 'MONARCH', 'TACTO', 'FUTBOL'), FUT, 'alta'),
        (('REBOUND', 'SMASH', 'CAVEN', 'SUEDE', 'BASKET', 'SHUFFLE', 'RICKIE',
          'COURTFLEX', 'MULTIFLEX', 'GRAVITON', 'ANZA', 'ST RUNNER',
          'CARINA', 'CALI', 'MAYZE', 'KARMEN'), URB, 'alta'),
    ],
    'CONVERSE': [
        (('CHUCK', 'TAYLOR', 'ALL STAR', 'ALLSTAR'), LONA, 'alta'),
        (('PRO BLAZE', 'STAR PLAYER', 'COURT', 'NET STAR'), URB, 'media'),
    ],
    'UNDER ARMOUR': [
        (('CHARGED', 'CHARGUED', 'ASSERT', 'SURGE', 'PURSUIT', 'ROGUE',
          'PHADE', 'VELOCITI'), RUN, 'alta'),
        (('ESSENTIAL', 'KICKSPRINT'), URB, 'media'),
    ],
    'FILA': [
        (('RACER', 'TRAZADO', 'RECOVERY', 'FXT', 'KR5', 'RUN'), RUN, 'media'),
        (('DISRUPTOR', 'RAY', 'EURO JOGGER', 'F13'), URB, 'alta'),
    ],
    'NEW BALANCE': [
        (('FRESH FOAM', 'ARISHI', 'ROAV', 'FUELCORE', '520', '411', 'DRFT'), RUN, 'alta'),
        (('574', '373', '500', '327', 'CT302', 'COURT', 'NEW BALANCE'), URB, 'media'),
    ],
    'ASICS': [
        (('GEL', 'JOLT', 'PATRIOT', 'CUMULUS', 'NIMBUS', 'KAYANO', 'CONTEND'), RUN, 'alta'),
    ],
    'LOTTO': [
        (('FUTBOL', 'FUTSAL', 'SOLISTA', 'MAESTRO'), FUT, 'alta'),
        (('RUN', 'SPEEDRIDE'), RUN, 'media'),
    ],
    'TOPPER': [
        (('FUTBOL', 'STINGER'), FUT, 'media'),
        (('RUN', 'LADY'), RUN, 'media'),
        ((' ',), URB, 'media'),   # PAUL/CUERO/GENERATION = urbanas clásicas
    ],
    'JORDAN': [((' ',), BASQ, 'media')],
    'AZALEIA': [((' ',), CHALA, 'media')],       # sandalias/confort mujer
    'MORMAII': [((' ',), CHALA, 'media')],       # surf
    'KELME': [(('FUTBOL', 'FUTSAL', 'INDOOR'), FUT, 'alta'), ((' ',), URB, 'media')],
    'CIRCA': [((' ', ), SKT, 'media')],          # marca 100% skate
    'DC': [((' ', ), SKT, 'media')],
    'VANS': [((' ', ), URB, 'alta')],
    'CAT': [(('BOTIN', 'BOTA'), BOTA, 'alta'), ((' ',), URB, 'media')],
    'CATERPILLAR': [(('BOTIN', 'BOTA'), BOTA, 'alta'), ((' ',), URB, 'media')],
    'HUSH PUPPIES': [((' ', ), CONF, 'media')],
    'PANAMA JACK': [((' ', ), BOTA, 'media')],   # línea PJACK = botas/botines outdoor
    'GRENDENE': [((' ', ), CHALA, 'alta')],
    'IPANEMA': [((' ', ), CHALA, 'alta')],
    'HAVAIANAS': [((' ', ), CHALA, 'alta')],
    'CROCS': [((' ', ), CHALA, 'media')],
    'BONNY FRANCO': [(('CHALA', 'SANDALIA'), CHALA, 'alta'), ((' ',), CHALA, 'media')],
    'FERRACINI': [(('VESTIR', 'MOCASIN'), VEST, 'alta'), ((' ',), VEST, 'media')],
    'PICCADILLY': [((' ', ), VEST, 'media')],    # confort-vestir dama
    'AGUXI': [(('DAMA', 'VESTIR'), VEST, 'media'), ((' ',), VEST, 'media')],
    'SOCCER': [((' ', ), FUT, 'alta')],          # marca de fútbol
    'AVIA': [((' ', ), TRAIN, 'media')],
    'EVERLAST': [((' ', ), TRAIN, 'media')],     # calzado Everlast = training (boxeo va por categoría vieja)
    'BUBBLE GUMMERS': [(('SANDALIA', 'CHALA', 'OCEAN'), CHALA, 'media'),
                       (('GATEADOR',), BEBE, 'alta'), ((' ',), URB, 'media')],
    'COLLOKY': [(('SANDALIA', 'CHALA'), CHALA, 'alta'),
                (('BOTIN', 'BOTA'), BOTA, 'media'), ((' ',), URB, 'media')],
    'BEIRA RIO': [(('SANDALIA', 'CHALA'), CHALA, 'alta'), ((' ',), URB, 'media')],
    'ACTVITTA': [((' ', ), URB, 'media')],
    'VIA MARTE': [((' ', ), URB, 'media')],
    'MOLECA': [((' ', ), URB, 'media')],
    'VIZZANO': [((' ', ), VEST, 'media')],
    'STYLO': [(('SANDALIA', 'CHALA'), CHALA, 'alta'), (('ZAPATON',), URB, 'media'),
              (('COLEGIAL', 'ESCOLAR'), ESC, 'alta'), ((' ',), URB, 'media')],
    'PLUMA': [(('COLEGIAL', 'ESCOLAR'), ESC, 'alta'), ((' ',), ESC, 'media')],
    'TEENER': [(('COLEGIAL', 'ESCOLAR'), ESC, 'alta'), ((' ',), URB, 'media')],
    'BATA': [(('COLEGIAL', 'ESCOLAR'), ESC, 'alta'), ((' ',), URB, 'media')],
    'POWER': [(('RUN',), RUN, 'media'), ((' ',), URB, 'media')],
    'NORTH STAR': [((' ', ), URB, 'media')],
    'CHAMPION': [((' ', ), URB, 'media')],
    'TBC': [((' ', ), URB, 'media')],            # infantil licencias (Spiderman...)
    'REIGO': [(('BTN', 'BOTIN'), BOTIN, 'alta'), ((' ',), URB, 'media')],
    # importadas genéricas (moda urbana):
    'HUALUNAOTE': [(('CHALA', 'SANDALIA'), CHALA, 'alta'), ((' ',), URB, 'media')],
    'IQUIQUE': [((' ', ), URB, 'media')],
    'BOTELI': [((' ', ), URB, 'media')],
    'VANKS': [((' ', ), URB, 'media')],
    'SKATER': [((' ', ), URB, 'media')],
    'AGTA': [((' ', ), URB, 'media')],
    'BY PASS': [(('MOCASIN',), MOCA, 'alta'), (('SANDALIA', 'CHALA'), CHALA, 'alta'),
                ((' ',), URB, 'media')],
    'PUMA_DEFAULT_MARKER': [],  # (placeholder, no usado)
    'ECKO': [((' ',), URB, 'media')],            # street/skate lifestyle
    'AND 1': [((' ',), BASQ, 'media')],          # marca de basketball
    'NEW WALK': [(('OUT DOOR', 'OUTDOOR'), OUT, 'media'), ((' ',), URB, 'media')],
    'PASSER': [(('DESCANSO',), PANT, 'media'), ((' ',), URB, 'media')],
    'BARBIE': [(('LONA',), LONA, 'alta'), ((' ',), URB, 'media')],
    'DISNEY': [((' ',), URB, 'media')],
    'CAMPER': [((' ',), URB, 'media')],
    '16HORAS': [((' ',), URB, 'media')],
    'ALQUIMIA': [((' ',), URB, 'media')],
    'SPALDING': [((' ',), URB, 'media')],        # los modelos residuales son calzado lifestyle
    'HIPPA': [((' ',), URB, 'media')],
    'OSS': [((' ',), URB, 'media')],
    'VERAZZI': [((' ',), URB, 'media')],
    'UNISPORT': [((' ',), URB, 'media')],
    'BACARAS': [((' ',), URB, 'media')],
    'UMBRO': [(('FUTBOL', 'FUTSAL', ' TF '), FUT, 'alta'), ((' ',), URB, 'media')],
    'OSIRIS': [((' ',), SKT, 'media')],          # marca de skate
    'SIR THOMAS': [((' ',), VEST, 'media')],     # vestir/casual hombre
    'GUANTE': [((' ',), VEST, 'media')],         # cuero clásico chileno
    'GARVIOLI': [((' ',), VEST, 'media')],
    'REEF': [((' ',), CHALA, 'media')],          # chalas/surf
    'IBERIA': [(('ALPARGATA',), ALPA, 'alta'), ((' ',), URB, 'media')],
    'SPORTZONE': [(('BALON',), FUT, 'media'), (('CALCETA', 'CALCETIN'), CALCE, 'alta'),
                  (('BOLSO',), BOLSO, 'alta'), ((' ',), URB, 'media')],
    'PACIFICO': [(('VOLEY',), VOLEY, 'alta'), (('MEDIA', 'CALCETA'), CALCE, 'media'),
                 ((' ',), URB, 'media')],
}
# líneas extra para marcas ya definidas
LINEAS['PUMA'].append(((' ',), URB, 'media'))            # residual puma = lifestyle
LINEAS['CONVERSE'].append(((' ',), LONA, 'media'))       # residual converse = lona
LINEAS['UNDER ARMOUR'].append(((' ',), URB, 'media'))
LINEAS['FILA'].append(((' ',), URB, 'media'))
LINEAS['NEW BALANCE'].append(((' ',), URB, 'media'))
LINEAS['LOTTO'].insert(0, ((' TF ', 'SPIDER', 'TACTO'), FUT, 'media'))
LINEAS['LOTTO'].append(((' ',), URB, 'media'))

# ── Capa 2: tokens genéricos válidos para cualquier marca (orden = prioridad) ──
GENERICOS = [
    (('GATEADOR',), BEBE, 'alta'),
    (('PANTUFLA', 'DESCANSO'), PANT, 'alta'),
    (('LONA',), LONA, 'alta'),
    (('OUT DOOR',), OUT, 'alta'),
    (('COLEGIAL', 'ESCOLAR'), ESC, 'alta'),
    (('VESTIR',), VEST, 'alta'),
    (('MOCASIN',), MOCA, 'alta'),
    (('BALLERINA', 'BALERINA', 'CHINITA'), BALLE, 'alta'),
    (('BOTIN', 'BTN'), BOTIN, 'alta'),
    (('BOTA',), BOTA, 'media'),
    (('CHALA', 'SANDALIA', 'HAWAIANA', 'SLIDE', 'FLIP FLOP'), CHALA, 'alta'),
    (('FUTBOL', 'FOOTBALL', 'SOCCER', 'FUTSAL', 'BABY FUT'), FUT, 'alta'),
    (('BASKET', 'BASQUET'), BASQ, 'alta'),
    (('TRAIL', 'TREKKING', 'TREK', 'HIKING', 'OUTDOOR', 'MONTANA'), OUT, 'alta'),
    (('SKATE',), SKT, 'alta'),
    (('TENIS DE MESA',), URB, 'media'),  # evitar falso "tenis"
    (('RUNNING', 'RUNNER', ' RUN', 'RUN ', 'RACER', 'JOGGER', 'MARATHON'), RUN, 'media'),
    (('TRAINER', 'TRAINING', 'CROSSFIT', ' GYM'), TRAIN, 'media'),
    (('ZAPATILLA', 'SNEAKER', 'URBAN', 'CASUAL', 'MODA', 'ZAPATON', 'ZPT',
      'LUCES'), URB, 'media'),
    (('ZAPATO DAMA', 'ZAPATO MUJER', 'TACO'), VEST, 'media'),
    (('ALPARGATA',), ALPA, 'alta'),
    (('VOLEY',), VOLEY, 'alta'),
    (('POLERA', 'CAMISETA'), POLER, 'alta'),
    (('CALCETA', 'CALCETIN', 'SOQUETE'), CALCE, 'alta'),
    (('BOLSO', 'MOCHILA'), BOLSO, 'alta'),
    (('BALON', 'PELOTA'), FUT, 'media'),
]


def clasificar(marca, modelo):
    m_fold = fold(marca)
    m_fold = MARCA_ALIAS.get(m_fold, m_fold)
    mod_fold = ' ' + fold(modelo) + ' '   # padding para tokens con espacio

    for toks, nodo, conf in LINEAS.get(m_fold, []):
        for t in toks:
            if t == ' ' or t in mod_fold:
                return nodo, conf, 'linea_marca'
    for toks, nodo, conf in GENERICOS:
        for t in toks:
            if t in mod_fold:
                return nodo, conf, 'token_generico'
    return None, None, None


def main():
    # Validar nodos contra la taxonomía real
    nodos_validos = {(dep, t) for dep, tipos in TAXONOMIA.items() for t in tipos}
    usados = set()
    for reglas in list(LINEAS.values()) + [GENERICOS]:
        for _, nodo, _ in reglas:
            usados.add(nodo)
    invalidos = usados - nodos_validos
    if invalidos:
        print('❌ Nodos fuera de la taxonomía:', invalidos)
        sys.exit(1)

    data = json.load(open('modelos.json', encoding='utf-8'))
    out, sin_resolver = [], []
    stats_nodo, stats_conf = Counter(), Counter()
    n_ok = n_total = 0

    for m in data:
        n_total += m['n']
        nodo, conf, fuente = clasificar(m['marca'], m['modelo'])
        if nodo:
            out.append({'marca': m['marca'], 'modelo': m['modelo'],
                        'dep': nodo[0], 'tipo': nodo[1],
                        'fuente': f'conocimiento:{fuente}', 'confianza': conf})
            stats_nodo[f'{nodo[0]} / {nodo[1]}'] += m['n']
            stats_conf[conf] += 1
            n_ok += m['n']
        else:
            sin_resolver.append(m)

    with open('modelos_clasificados.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f'Modelos clasificados : {len(out):,} / {len(data):,}  '
          f'({len(out)/len(data):.1%} de modelos, {n_ok/n_total:.1%} de filas)')
    print(f'Confianza            : alta={stats_conf["alta"]:,}  media={stats_conf["media"]:,}')
    print('\nDistribución (filas ponderadas):')
    for k, v in stats_nodo.most_common(20):
        print(f'  {k:<36} {v:>7,}')
    print(f'\nSIN RESOLVER: {len(sin_resolver):,} modelos '
          f'({sum(m["n"] for m in sin_resolver):,} filas) — quedan en Zapatillas Urbanas/revisar')
    sin_resolver.sort(key=lambda m: -m['n'])
    for m in sin_resolver[:40]:
        print(f'  {m["marca"][:20]:<20} | {m["modelo"][:44]:<44} | {m["n"]:>5}')


if __name__ == '__main__':
    main()
