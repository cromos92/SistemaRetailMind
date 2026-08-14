"""
Consolida las familias CUECA / HUASO que existen bajo varios codigos gemelos
('3054', '30540', '305400', '3054-02', '3054 02') en UNA ficha por TRAMO DE
PRECIO, por color, por sucursal.

Por que por tramo y no una sola ficha: el precio de la cueca va por numeracion
(lista del proveedor, foto `retailmind/precio cueca.jpeg`) y `Producto.precioventa`
es unico por ficha. La convencion destino ya existe en el catalogo desde
mayo-2026: `3054-01/1` (blanco 35/40), `3054-01/2` (blanco 30/34).

Mecanica: RE-APUNTA las filas de `Producto_Talla` a la ficha destino de su tramo
(update de producto_id). La talla viaja completa: mismo SKU (el codigo de barras
fisico sigue funcionando en el POS), mismo kardex, mismos lotes FIFO. No hay
movimientos de stock ni re-etiquetado.

  *** POR QUE NO SE FUSIONAN LAS FILAS DE LA MISMA TALLA ***
  Al juntar codigos gemelos, la ficha destino queda con la misma talla repetida
  (una fila por ficha de origen, cada una con su SKU). Es DELIBERADO: hay 20
  relaciones apuntando a Producto_Talla y 14 son on_delete=CASCADE
  (Movimientos_Producto, LoteProducto, Ticket_Productos, Traspaso_Detalle,
  AjusteInventario_Detalle, Productos_Recepcionados, Cotizacion_Detalle...).
  Borrar una fila de talla para "limpiar la curva" destruiria ventas y kardex.
  Los listados agrupan por talla sumando stock, que es lo que importa vender.
  !! NO correr `diagnosticar_tallas_duplicadas --consolidar` sobre estas
  familias: ese comando SI borra las filas sobrantes y el destrozo es
  irreversible (el snapshot de aqui no lo cubre, las filas ya no existirian).

Decisiones de negocio (usuario, 12 y 13-ago-2026):
  - La familia 3054/3073 queda bajo ALQUIMIA (asi lo dice la lista de precios);
    hoy esta partida entre ALQUIMIA y UNISPORT.
  - LA LISTA DE PAPEL MANDA en los precios: la ficha destino queda con el precio
    del tramo y CADA cambio se reporta al final. (Antes el comando no tocaba
    precios, pero las fichas nuevas nacian con el precio de lista sin avisar:
    una rebaja silenciosa. Ahora es explicito y auditable.)
  - La talla 34 pertenece al tramo ALTO (la lista manda por sobre la
    segmentacion que se aplico en mayo-2026).
  - BACARAS SI es el mismo producto que UNISPORT/ALQUIMIA en 2918 y 9700.

Seguro por diseno:
  - DRY-RUN por defecto: sin --apply NO escribe nada (se simula dentro de una
    transaccion que se revierte).
  - La verificacion de conservacion corre en AMBOS modos, DENTRO de la
    transaccion de cada sucursal y sobre el conjunto EXACTO de ids de talla
    (no un regex): si no cuadra, esa sucursal se revierte sola.
  - Antes de escribir se guarda `_cueca_snapshot_<fecha>.json` con todo lo que
    se modifica, incluidos los ids de las fichas creadas, para que
    `revertir_consolidacion_cueca` pueda deshacerlo exactamente.
  - Las fichas fuente que quedan vacias se RENOMBRAN a un codigo muerto
    (`<base>-CONSOLIDADO-<id>`) ademas de marcarse `excluir_de_analitica`. Si
    conservaran su codigo original, la proxima recepcion aterrizaria en ellas
    (fichas_por_identidad no filtra por exclusion) y ese stock quedaria invisible.

Uso:
    python manage.py consolidar_cueca                        # dry-run, todas
    python manage.py consolidar_cueca --familia 3054         # dry-run, una familia
    python manage.py consolidar_cueca --sucursal PAO3        # dry-run, una sucursal
    python manage.py consolidar_cueca --apply                # ESCRIBE
"""
import json
import os
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from app.models import (AtributoOpcion, Categoria, Producto, Producto_Talla,
                        Sucursal)

# ---------------------------------------------------------------------------
# Familias. `tramos` = (desde, hasta, sufijo, precio_lista) de talla ALTA a BAJA.
# precio_lista None -> el destino conserva su precio actual y se reporta aparte.
# ---------------------------------------------------------------------------
# ALQUIMIA ZAPATO MUJER, "ART. 3054 - 3073" en la lista. El tramo 34-41 figura
# impreso a 24.990 pero lleva la correccion a mano "21.000" al lado; el usuario
# confirmo el 13-ago-2026 que manda la correccion. Con eso 26-33 y 34-41 quedan
# al MISMO precio (21.990) — se mantienen como fichas separadas igual, para poder
# mover el precio de cada rango por su cuenta mas adelante.
PRECIOS_ALQUIMIA_MUJER = [(34, 41, '/1', 21990), (26, 33, '/2', 21990),
                          (22, 25, '/3', 19990)]

FAMILIAS = {
    '3054': {
        'regex': r'^3054([\s\-/].*)?$|^30540{1,2}$',
        'marcas_fuente': ['ALQUIMIA', 'UNISPORT'],
        'marca_destino': 'ALQUIMIA',
        'descripcion': 'ZAPATO CUECA',
        'tramos': PRECIOS_ALQUIMIA_MUJER,
    },
    '3073': {
        # misma lista de precios que 3054 ("ART. 3054 - 3073" en la foto)
        'regex': r'^3073([\s\-/].*)?$|^30730{1,3}$',
        'marcas_fuente': ['ALQUIMIA', 'UNISPORT'],
        'marca_destino': 'ALQUIMIA',
        'descripcion': 'ZAPATO CUECA',
        'tramos': PRECIOS_ALQUIMIA_MUJER,
    },
    '3334': {
        # incluye 2334-20: es la mitad 30-34 del mismo zapato charol (el 3
        # tipeado como 2). Verificado: ALQUIMIA, NEGRO CHAROL, 'ZAPATO CUECA',
        # $24.990, tallas 30-34, 64 uds — complementa las 35-44 de 3334.
        'regex': r'^3334([\s\-/].*)?$|^33340{1,2}$|^2334([\s\-/].*)?$',
        'marcas_fuente': ['ALQUIMIA', 'UNISPORT'],
        'marca_destino': 'ALQUIMIA',
        'descripcion': 'ZAPATO CUECA',
        # la lista no segmenta este articulo y hoy todo esta a 24.990
        'tramos': [(30, 44, '', 24990)],
    },
    '2918': {
        # El codigo 2918 lo usan 6 marcas y solo tres son esta bota:
        #   INCLUIDAS: UNISPORT (9 fichas, 251 uds), ALQUIMIA (5, 7 uds) y
        #   BACARAS (5, 6 uds) — las tres 'BOTA CUECA', mismo precio y costo,
        #   creadas ago-sep 2018, misma curva 22-38.
        #   EXCLUIDAS: COLLOKY (BALERINA), SKECHERS (SK PETRYTRAIL) y SORMANI
        #   (BOTA GA291-8), las tres con 0 stock y 0 ventas historicas.
        'regex': r'^2918([\s\-/].*)?$',
        'marcas_fuente': ['UNISPORT', 'ALQUIMIA', 'BACARAS'],
        'marca_destino': 'UNISPORT',
        'descripcion': 'BOTA CUECA',
        # Los CUATRO tramos tal cual la lista, aunque 22-25 y 26-29 compartan
        # precio: el negocio los pide separados para poder mover el precio de
        # cada rango por su cuenta y leer la curva por edad.
        'tramos': [(34, 38, '/1', 34990), (30, 33, '/2', 29990),
                   (26, 29, '/3', 26990), (22, 25, '/4', 26990)],
    },
    '1800': {
        # BOTIN HUASO 20-25 ("ART. 1800" en la foto). Tramo unico.
        # El codigo 1800 tambien lo usan NIKE, SKECHERS, SUNTA y DRIBLING con
        # productos ajenos: por eso el whitelist de marcas.
        'regex': r'^1800([\s\-/].*)?$',
        'marcas_fuente': ['UNISPORT', 'BACARAS'],
        'marca_destino': 'UNISPORT',
        'descripcion': 'BOTIN HUASO',
        'tramos': [(20, 25, '', 19990)],
    },
    '9700': {
        # BOTA CUECA 39-44 ("39-44 = 39.990", anotado a mano en la foto).
        # NO incluye 9702 (BACARAS, $34.990, 1 ud): precio distinto, se revisa aparte.
        'regex': r'^9700([\s\-/].*)?$',
        'marcas_fuente': ['ALQUIMIA', 'UNISPORT', 'BACARAS'],
        'marca_destino': 'ALQUIMIA',
        'descripcion': 'BOTA CUECA',
        'tramos': [(39, 44, '', 39990)],
    },
}

COLOR_CODIGO = {'NEGRO': '02', 'BLANCO': '01', 'CHAROL': '20', 'CARAMELO': '16',
                'CAFE': '03'}
# Sinonimos verificados en prod: el mismo color escrito distinto por flujos
# distintos. 2918: NEGRO == BLACK. 3334: CHAROL == NEGRO CHAROL (5 fichas,
# 4 uds, 5 ventas 2026). ESTAMPADO NO se mapea: en 3054 la unica ficha
# ESTAMPADO (id 31919) es 'VESTIR 22/25' de nino, $12.990, talla 13, 0 ventas
# en 6 anios — no es cueca. Sin codigo de color, el comando la deja intacta.
NORMALIZA_COLOR = {
    'BLACK': 'NEGRO',
    'WHITE': 'BLANCO',
    'NEGRO CHAROL': 'CHAROL',
    'CHAROL NEGRO': 'CHAROL',
}
# Ficha que recoge las tallas fuera de todo tramo de la lista (ej. la '00').
SUFIJO_SIN_TRAMO = '/0'


def _talla_num(talla):
    s = re.sub(r'\D', '', str(talla or ''))
    return int(s) if s else None


# El codigo solo no basta para decidir identidad: hay fichas con el codigo de la
# cueca pegado a otro producto ('PULCERA 22/25' en 3073, 'VESTIR 22/25' en 3054,
# 'BALERINA' de COLLOKY en 2918). El filtro de color atrapaba algunas (ESTAMPADO)
# pero no las que son BLANCO o NEGRO. Este es el segundo cerrojo: la descripcion
# tiene que hablar de cueca/huaso/danza.
RE_DESC_CUECA = re.compile(r'CUECA|HUASO|DANZA', re.I)


class Command(BaseCommand):
    help = ('Consolida las familias cueca/huaso en una ficha por tramo de precio. '
            'Dry-run por defecto; --apply para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Escribe los cambios (sin esto solo reporta)')
        parser.add_argument('--familia', choices=sorted(FAMILIAS), default=None,
                            help='Limitar a una familia')
        parser.add_argument('--sucursal', default=None,
                            help='Limitar a una sucursal (alias, ej. PAO3)')
        parser.add_argument('--realinear-lotes', action='store_true',
                            dest='realinear_lotes',
                            help='SOLO realinea los lotes FIFO activos al precio de '
                                 'su ficha (reparacion de corridas anteriores). No '
                                 'mueve tallas ni cambia precios de producto.')

    # ------------------------------------------------------------------ main
    def handle(self, *args, **opts):
        aplicar = opts['apply']
        self.alias = {s.id: (s.alias or s.nombreSucursal or '?')
                      for s in Sucursal.objects.all()}
        self.precios = []          # precio de la ficha destino vs lista
        self.repreciadas = []      # tallas que cambian de precio AL MOVERSE
        self.categorias = []       # fichas destino que cambian de categoria
        self.plan = []
        self.creadas_ids = []      # ids de fichas creadas -> van al snapshot
        self.incompletas = []      # (familia, sucursal) que abortaron
        tot = defaultdict(int)

        familias = {k: v for k, v in FAMILIAS.items()
                    if not opts['familia'] or k == opts['familia']}

        if opts['realinear_lotes']:
            self._realinear(familias, aplicar)
            return

        if aplicar:
            ruta_snap = self._snapshot(familias)
            self.stdout.write(self.style.SUCCESS(
                'SNAPSHOT del estado original -> %s\n' % ruta_snap))

        for base, cfg in familias.items():
            marca_destino = AtributoOpcion.objects.filter(
                atributo__nombre__iexact='Marca',
                valor__iexact=cfg['marca_destino']).first()
            if not marca_destino:
                self.stderr.write(self.style.ERROR(
                    'No existe la marca destino %r' % cfg['marca_destino']))
                continue

            # `-CONSOLIDADO-` vuelve a casar con el regex de su propia familia
            # (`^2918([\s\-/].*)?$` acepta `2918-CONSOLIDADO-28707`): hay que
            # excluirlo para que una segunda corrida no reprocese lo ya cerrado.
            todas = list(Producto.objects.filter(
                articulo__iregex=cfg['regex'],
                atributo1__valor__in=cfg['marcas_fuente'])
                .exclude(articulo__contains='-CONSOLIDADO-')
                .select_related('atributo2'))
            # segundo cerrojo: la descripcion tiene que ser de cueca/huaso
            prods = [p for p in todas if RE_DESC_CUECA.search(p.descripcion or '')]
            ajenas = [p for p in todas if p not in prods]
            if ajenas:
                self.stdout.write(self.style.WARNING(
                    '  %d ficha(s) con codigo %s pero descripcion ajena -> FUERA: %s'
                    % (len(ajenas), base,
                       ', '.join('%s#%s(%r)' % (p.articulo, p.id, (p.descripcion or '')[:18])
                                 for p in ajenas))))
                tot['ajenas'] += len(ajenas)
            if not prods:
                continue

            por_suc = defaultdict(list)
            for p in prods:
                por_suc[p.sucursal_id].append(p)

            self.stdout.write('\n' + '=' * 92)
            self.stdout.write('FAMILIA %s -> marca %s   (%d fichas, %d sucursales)   [%s]'
                              % (base, cfg['marca_destino'], len(prods), len(por_suc),
                                 'APPLY' if aplicar else 'DRY-RUN'))
            self.stdout.write('=' * 92)

            for suc_id, fichas in sorted(por_suc.items()):
                nombre_suc = self.alias.get(suc_id, str(suc_id))
                if opts['sucursal'] and nombre_suc.upper() != opts['sucursal'].upper():
                    continue
                try:
                    with transaction.atomic():
                        self._consolidar(base, cfg, marca_destino, suc_id,
                                         nombre_suc, fichas, tot)
                        if not aplicar:
                            transaction.set_rollback(True)
                except Exception as e:
                    self.incompletas.append((base, nombre_suc))
                    self.stderr.write(self.style.ERROR(
                        '  %s/%s ABORTADA y revertida: %s' % (base, nombre_suc, e)))

        self._resumen(aplicar, tot)

    # ------------------------------------------------------------------ reparacion
    def _realinear(self, familias, aplicar):
        """Repara lotes FIFO que quedaron con el precio anterior a una corrida.

        Solo toca lotes cuyo `precio_venta_unitario` difiere del `precioventa`
        de su ficha. NO se usa para campanas: si hay una campana activa el lote
        lleva a proposito otro precio, asi que se listan aparte para revisarlos
        a mano en vez de pisarlos.
        """
        from app.models import LoteProducto
        alias = self.alias
        total = 0
        self.stdout.write('REALINEACION DE LOTES FIFO   [%s]\n'
                          % ('APPLY' if aplicar else 'DRY-RUN'))
        for base, cfg in familias.items():
            fichas = list(Producto.objects.filter(
                articulo__iregex=cfg['regex'],
                atributo1__valor__in=cfg['marcas_fuente'])
                .exclude(articulo__contains='-CONSOLIDADO-'))
            for p in fichas:
                lotes = LoteProducto.objects.filter(
                    producto_talla__producto_id=p.id, cantidad_disponible__gt=0,
                    activo=True).exclude(precio_venta_unitario=p.precioventa)
                n = lotes.count()
                if not n:
                    continue
                uds = lotes.aggregate(s=Sum('cantidad_disponible'))['s'] or 0
                precios = sorted({l.precio_venta_unitario for l in lotes})
                self.stdout.write(
                    '  %-6s %-14s id=%-7s ficha $%-8s <- lotes $%-22s %d lotes, %d uds'
                    % (alias.get(p.sucursal_id, '?'), p.articulo, p.id,
                       '{:,}'.format(p.precioventa),
                       ', '.join('{:,}'.format(x) for x in precios[:3]), n, uds))
                if aplicar:
                    with transaction.atomic():
                        lotes.update(precio_venta_unitario=p.precioventa)
                total += n
        self.stdout.write('\n%s: %d lotes %s'
                          % ('LISTO' if aplicar else 'DRY-RUN', total,
                             'realineados' if aplicar else 'se realinearian'))
        if not aplicar:
            self.stdout.write(self.style.WARNING(
                'Nada quedo escrito. Agrega --apply para reparar.'))

    # ------------------------------------------------------------------ snapshot
    def _snapshot(self, familias):
        snap = {'fecha': timezone.localtime().isoformat(), 'productos': [],
                'tallas': [], 'creadas': []}
        ids = set()
        for base, cfg in familias.items():
            for p in Producto.objects.filter(
                    articulo__iregex=cfg['regex'],
                    atributo1__valor__in=cfg['marcas_fuente']).values(
                    'id', 'articulo', 'descripcion', 'atributo1_id', 'atributo2_id',
                    'excluir_de_analitica', 'sucursal_id', 'precioventa'):
                if p['id'] not in ids:
                    ids.add(p['id'])
                    snap['productos'].append(p)
        for t in Producto_Talla.objects.filter(producto_id__in=ids).values('id', 'producto_id'):
            snap['tallas'].append(t)
        self._snap = snap
        self._ruta_snap = os.path.join(
            os.getcwd(),
            '_cueca_snapshot_%s.json' % timezone.localtime().strftime('%Y%m%d_%H%M%S'))
        self._guardar_snap()
        return self._ruta_snap

    def _guardar_snap(self):
        if getattr(self, '_snap', None) is None:
            return
        self._snap['creadas'] = self.creadas_ids
        with open(self._ruta_snap, 'w', encoding='utf-8') as fh:
            json.dump(self._snap, fh, ensure_ascii=False, indent=1)

    # ------------------------------------------------------------------ destino
    def _elegir_destino(self, fichas_color, tallas_color, pertenece, precio_lista):
        """Ficha existente pura del grupo, o None. Prioriza precio de lista ya
        correcto, luego la mas reciente ('el ultimo creado'), luego mas tallas."""
        por_ficha = defaultdict(list)
        for t in tallas_color:
            por_ficha[t.producto_id].append(t)
        cands = []
        for p in fichas_color:
            propias = por_ficha.get(p.id, [])
            if propias and all(pertenece(t) for t in propias):
                cands.append(((p.precioventa or 0) == (precio_lista or 0),
                              (p.fecha_creacion is not None, p.fecha_creacion, p.id),
                              len(propias), p))
        if not cands:
            return None
        cands.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return cands[0][3]

    # ------------------------------------------------------------------ core
    def _consolidar(self, base, cfg, marca_destino, suc_id, nombre_suc, fichas, tot):
        ids_fichas = [p.id for p in fichas]
        tallas = list(Producto_Talla.objects.filter(producto_id__in=ids_fichas))
        if not tallas:
            return
        # conjunto EXACTO para verificar conservacion (no un regex)
        ids_talla = {t.id for t in tallas}
        stock_antes = sum(t.stock or 0 for t in tallas)

        self.stdout.write('\n  [%s]  %d fichas, %d filas de talla, %d unidades'
                          % (nombre_suc, len(fichas), len(tallas), stock_antes))

        destinos_tocados = set()

        def color_de(p):
            v = (p.atributo2.valor if p.atributo2 else '?').strip().upper()
            return NORMALIZA_COLOR.get(v, v)

        por_color = defaultdict(list)
        for p in fichas:
            por_color[color_de(p)].append(p)

        for color, fichas_color in sorted(por_color.items()):
            cc = COLOR_CODIGO.get(color)
            if cc is None:
                self.stdout.write(self.style.WARNING(
                    '    color %r sin codigo -> fichas %s NO se tocan'
                    % (color, [p.id for p in fichas_color])))
                tot['sin_color'] += len(fichas_color)
                continue
            ids_color = {p.id for p in fichas_color}
            tallas_color = [t for t in tallas if t.producto_id in ids_color]

            def _en(t, d, h):
                n = _talla_num(t.talla)
                return n is not None and d <= n <= h

            def _sin(t):
                n = _talla_num(t.talla)
                return n is None or not any(d <= n <= h for d, h, _s, _p in cfg['tramos'])

            grupos = [(d, h, s, pr, (lambda t, d=d, h=h: _en(t, d, h)))
                      for (d, h, s, pr) in cfg['tramos']]

            # Tallas fuera de todo tramo de la lista ('00', '13'): son residuo de
            # la migracion legacy — numeros que no existen en la curva real y que
            # aparecen pegados dentro de fichas de otro producto. Decision del
            # negocio (13-ago-2026): van al TRAMO MAS PEQUENO, no a una ficha
            # aparte. Son 5 filas en total (2 unidades reales, el resto sin stock).
            sin_rows = [t for t in tallas_color if _sin(t)]
            if sin_rows:
                ids_sin = {t.id for t in sin_rows}
                d0, h0, s0, pr0, pert0 = grupos[-1]
                grupos[-1] = (d0, h0, s0, pr0,
                              (lambda t, f=pert0, ids=ids_sin: f(t) or t.id in ids))
                self.stdout.write(
                    '    talla(s) fuera de lista (%s, %d uds) -> al tramo %d-%d'
                    % (', '.join(sorted({str(t.talla) for t in sin_rows})),
                       sum(t.stock or 0 for t in sin_rows), d0, h0))
                tot['sin_tramo'] += len(sin_rows)

            for (desde, hasta, suf, precio_lista, pertenece) in grupos:
                del_grupo = [t for t in tallas_color if pertenece(t)]
                if not del_grupo:
                    continue
                codigo = '%s-%s%s' % (base, cc, suf)
                desc = ('%s %d/%d' % (cfg['descripcion'], desde, hasta)
                        if desde is not None else '%s OTRAS TALLAS' % cfg['descripcion'])
                destino = self._elegir_destino(fichas_color, tallas_color,
                                               pertenece, precio_lista)
                creada = False
                if destino is None:
                    plant = max(fichas_color, key=lambda p: (p.fecha_creacion is not None,
                                                             p.fecha_creacion, p.id))
                    destino = Producto(
                        articulo=codigo, descripcion=desc, atributo1=marca_destino,
                        atributo2=plant.atributo2, atributo3=plant.atributo3,
                        atributo4=plant.atributo4, categoria=plant.categoria,
                        sucursal_id=suc_id, costo=plant.costo or 0,
                        sobreprecio=plant.sobreprecio or 0,
                        # nace con el precio de la PLANTILLA; si la lista dice otra
                        # cosa se ajusta abajo y queda reportado (nunca en silencio)
                        precioventa=plant.precioventa or 0,
                        tipo_talla=plant.tipo_talla, guia_talla=plant.guia_talla,
                        temporada=plant.temporada)
                    destino.save()
                    self.creadas_ids.append(destino.id)
                    creada = True
                    tot['creadas'] += 1

                cambios = []
                if destino.articulo.strip().upper() != codigo:
                    cambios += ['articulo', 'descripcion']
                    destino.articulo, destino.descripcion = codigo, desc
                    tot['renombradas'] += 1
                if destino.atributo1_id != marca_destino.id:
                    cambios.append('atributo1')
                    destino.atributo1 = marca_destino
                # CATEGORIA: la ficha destino puede estar categorizada distinto
                # que la mayoria del grupo (9700: la sobreviviente decia 'Zapatos
                # de Vestir' y arrastraba 86 uds de 'Botas'). Gana la categoria
                # con mas unidades del grupo, y el cambio se reporta.
                uds_cat = defaultdict(int)
                for t in del_grupo:
                    f = next((p for p in fichas_color if p.id == t.producto_id), None)
                    if f is not None and f.categoria_id:
                        uds_cat[f.categoria_id] += (t.stock or 0)
                if uds_cat:
                    cat_may = max(uds_cat, key=uds_cat.get)
                    if destino.categoria_id != cat_may and uds_cat[cat_may] > 0:
                        self.categorias.append(
                            (base, nombre_suc, codigo, destino.id,
                             str(destino.categoria) if destino.categoria else '(sin)',
                             str(Categoria.objects.filter(id=cat_may).first() or '?'),
                             uds_cat[cat_may]))
                        cambios.append('categoria')
                        destino.categoria_id = cat_may
                        tot['categorias'] += 1
                # LA LISTA MANDA: se aplica y se reporta SIEMPRE que cambie
                precio_antes = destino.precioventa or 0
                if precio_lista and (destino.precioventa or 0) != precio_lista:
                    self.precios.append((base, nombre_suc, codigo,
                                         '%d-%d' % (desde, hasta), color,
                                         destino.precioventa or 0, precio_lista,
                                         'NUEVA' if creada else 'existente'))
                    cambios.append('precioventa')
                    destino.precioventa = precio_lista
                    tot['precios'] += 1
                if cambios:
                    destino.save(update_fields=list(dict.fromkeys(cambios)))
                    if 'precioventa' in cambios:
                        # Invariante del proyecto (app/services/historial_precios.py):
                        # todo flujo que pise precioventa debe dejar rastro en
                        # HistorialCambioPrecio. La replica a los lotes FIFO se hace
                        # al FINAL de la sucursal (ver _alinear_lotes): si se hiciera
                        # aqui, las tallas que llegan DESPUES traerian sus lotes con
                        # el precio viejo y quedarian desalineados.
                        from app.services.historial_precios import registrar_cambios_precio
                        registrar_cambios_precio(
                            destino, {'precioventa': precio_antes},
                            motivo='Consolidacion cueca %s (%s)' % (base, codigo),
                            tipo_cambio='SINCRONIZACION')
                        destinos_tocados.add(destino.id)

                mover = [t for t in del_grupo if t.producto_id != destino.id]
                uds = sum(t.stock or 0 for t in mover)

                # --- REPRECIACION POR ORIGEN ---
                # Mover una talla la hace pasar al precio de la ficha destino.
                # Hay que reportarlo ANTES de mutar t.producto_id, agrupando por
                # ficha de origen: si no, la rebaja/alza queda invisible (el
                # reporte del destino solo ve su propio precio contra la lista).
                fuentes_ids = sorted({t.producto_id for t in mover})
                por_fuente = defaultdict(lambda: [0, 0])
                for t in mover:
                    por_fuente[t.producto_id][0] += 1
                    por_fuente[t.producto_id][1] += t.stock or 0
                for fid, (n_t, n_u) in sorted(por_fuente.items()):
                    fuente = next((p for p in fichas_color if p.id == fid), None)
                    p_ant = (fuente.precioventa or 0) if fuente else 0
                    if p_ant and p_ant != (destino.precioventa or 0):
                        self.repreciadas.append(
                            (base, nombre_suc, color, fuente.articulo if fuente else '?',
                             fid, codigo, destino.id, n_t, n_u, p_ant,
                             destino.precioventa or 0))
                        tot['sku_repreciados'] += n_t
                        tot['uds_repreciadas'] += n_u

                for t in mover:
                    Producto_Talla.objects.filter(id=t.id).update(producto_id=destino.id)
                    t.producto_id = destino.id
                tot['tallas'] += len(mover)
                tot['unidades'] += uds
                if desde is None:
                    tot['sin_tramo'] += len(mover)

                # cuantas tallas quedan repetidas en el destino (efecto conocido)
                cuenta = defaultdict(int)
                for t in del_grupo:
                    cuenta[str(t.talla).strip()] += 1
                rep = sum(v - 1 for v in cuenta.values() if v > 1)
                tot['repetidas'] += rep

                self.stdout.write(
                    '    %-14s %-8s %-10s -> id=%-7s%s: %d tallas, %d uds%s'
                    % (codigo, color[:8],
                       ('tramo %d-%d' % (desde, hasta)) if desde is not None else 'SIN TRAMO',
                       destino.id, ' NUEVA' if creada else '', len(mover), uds,
                       ('  [%d filas de talla repetida]' % rep) if rep else ''))
                self.plan.append({
                    'familia': base, 'sucursal': nombre_suc, 'color': color,
                    'tramo': ('%d-%d' % (desde, hasta)) if desde is not None else 'sin tramo',
                    'codigo_destino': codigo, 'destino_id': destino.id,
                    'creado': creada, 'tallas_movidas': len(mover), 'unidades': uds,
                    'repetidas': rep,
                    'fuentes': fuentes_ids,
                    'precio_destino': destino.precioventa or 0,
                    'precio_lista': precio_lista or 0})

        # --- LOTES FIFO: alinear al FINAL, cuando todas las tallas ya llegaron ---
        # Cada talla trae sus lotes con el precio de su ficha de origen. Si el
        # destino cambio de precio, esos lotes entrantes quedan desalineados y
        # una campana posterior revive el precio viejo desde el lote.
        from app.models import LoteProducto
        for did in sorted(destinos_tocados):
            pv = Producto.objects.filter(id=did).values_list('precioventa', flat=True).first()
            n = LoteProducto.objects.filter(
                producto_talla__producto_id=did, cantidad_disponible__gt=0,
                activo=True).exclude(precio_venta_unitario=pv).update(
                precio_venta_unitario=pv)
            tot['lotes'] += n
            if n:
                self.stdout.write('    lotes FIFO realineados en ficha %s: %d' % (did, n))

        # --- fuentes vaciadas: renombrar a codigo muerto + excluir ---
        vacias = [p for p in fichas
                  if not Producto_Talla.objects.filter(producto_id=p.id).exists()]
        for p in vacias:
            nuevo = '%s-CONSOLIDADO-%s' % (base, p.id)
            Producto.objects.filter(id=p.id).update(
                articulo=nuevo, excluir_de_analitica=True)
            tot['vaciadas'] += 1
        if vacias:
            self.stdout.write('    vaciadas -> renombradas a <base>-CONSOLIDADO-<id> '
                              'y excluidas: %s' % [p.id for p in vacias])

        # --- CONSERVACION: conjunto exacto, dentro de la transaccion ---
        qs = Producto_Talla.objects.filter(id__in=ids_talla)
        n_desp = qs.count()
        s_desp = qs.aggregate(s=Sum('stock'))['s'] or 0
        if n_desp != len(ids_talla) or s_desp != stock_antes:
            raise RuntimeError(
                'conservacion rota: tallas %d->%d, stock %d->%d'
                % (len(ids_talla), n_desp, stock_antes, s_desp))
        self.stdout.write('    conservacion OK: %d tallas / %d unidades intactas'
                          % (n_desp, s_desp))

    # ------------------------------------------------------------------ salida
    def _resumen(self, aplicar, tot):
        if aplicar:
            self._guardar_snap()
        ruta_plan = os.path.join(os.getcwd(), '_cueca_plan.json')
        with open(ruta_plan, 'w', encoding='utf-8') as fh:
            json.dump({'generado': timezone.localtime().isoformat(),
                       'modo': 'APPLY' if aplicar else 'DRY-RUN',
                       'plan': self.plan, 'precios': self.precios,
                       'repreciadas': self.repreciadas,
                       'creadas': self.creadas_ids,
                       'incompletas': self.incompletas}, fh, ensure_ascii=False, indent=1)

        self.stdout.write('\n' + '=' * 92)
        self.stdout.write(
            'RESUMEN: %d tallas re-apuntadas (%d uds) · %d fichas creadas · '
            '%d renombradas · %d vaciadas · %d cambios de precio · '
            '%d filas de talla repetida · %d tallas sin tramo'
            % (tot['tallas'], tot['unidades'], tot['creadas'], tot['renombradas'],
               tot['vaciadas'], tot['precios'], tot['repetidas'], tot['sin_tramo']))
        if tot['sin_color']:
            self.stdout.write(self.style.WARNING(
                '%d fichas quedaron fuera por color sin codigo conocido.' % tot['sin_color']))
        if self.incompletas:
            self.stdout.write(self.style.ERROR(
                'INCOMPLETAS (revertidas, revisar): %s' % self.incompletas))

        if self.precios:
            self.stdout.write('\nCAMBIOS DE PRECIO APLICADOS (la lista manda):')
            self.stdout.write('%-7s %-7s %-14s %-8s %-9s %10s %10s %s'
                              % ('FAM', 'SUC', 'FICHA', 'TRAMO', 'COLOR',
                                 'antes', 'lista', 'ficha'))
            for f in self.precios:
                self.stdout.write('%-7s %-7s %-14s %-8s %-9s %10s %10s %s'
                                  % (f[0], f[1], f[2], f[3], f[4][:9],
                                     '{:,}'.format(f[5]), '{:,}'.format(f[6]), f[7]))
        if self.categorias:
            self.stdout.write(self.style.WARNING(
                '\nCAMBIOS DE CATEGORIA (la ficha destino adopta la del grupo):'))
            for f in self.categorias:
                self.stdout.write('   %-6s %-6s %-14s id=%-7s %-22s -> %-22s (%d uds)'
                                  % (f[0], f[1], f[2], f[3], f[4][:22], f[5][:22], f[6]))
        if self.repreciadas:
            uds = sum(f[8] for f in self.repreciadas)
            self.stdout.write(self.style.WARNING(
                '\nTALLAS QUE CAMBIAN DE PRECIO AL MOVERSE  (%d filas, %d unidades)'
                % (sum(f[7] for f in self.repreciadas), uds)))
            self.stdout.write('Al pasar a la ficha destino, estas unidades quedan a '
                              'su precio. Es el efecto que hay que revisar:')
            self.stdout.write('%-6s %-6s %-9s %-12s %-14s %5s %5s %10s %10s %s'
                              % ('FAM', 'SUC', 'COLOR', 'DESDE', 'HACIA',
                                 'tall', 'uds', 'antes', 'despues', 'dif'))
            for f in sorted(self.repreciadas, key=lambda x: (-abs(x[10] - x[9]), x[0])):
                dif = f[10] - f[9]
                self.stdout.write('%-6s %-6s %-9s %-12s %-14s %5d %5d %10s %10s %+d'
                                  % (f[0], f[1], f[2][:9], '%s#%s' % (f[3][:7], f[4]),
                                     f[5], f[7], f[8],
                                     '{:,}'.format(f[9]), '{:,}'.format(f[10]), dif))
        self.stdout.write('\nPlan -> %s' % ruta_plan)
        if not aplicar:
            self.stdout.write(self.style.WARNING(
                'DRY-RUN: nada quedo escrito. Ejecuta con --apply para consolidar.'))
