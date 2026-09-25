"""
Planificación de la carga de una factura: para cada línea decide si el
producto es NUEVO, si YA EXISTE (y en qué ficha entra), cómo se escriben sus
tallas, qué precios lleva y qué errores o avisos hay. No escribe nada.

Reglas (las mismas del comando cargar_productos_factura):
  - Existe o no = artículo + MARCA, nada más (color/género/categoría de la
    ficha pueden estar mal y sigue siendo el mismo producto). Si existe, nunca
    se crea otra ficha: se usa la existente con su identidad.
  - Tallas como "Tipo Talla" + "Guía" del modal: ficha nueva → guía del perfil
    o del JSON; ficha existente (y sus gemelas) → pasa al mismo formato,
    renombrando tallas con el mismo SKU.
  - Lo ya ingresado contra el DTE se detecta por unidades, en todas las bodegas.
"""
from decimal import Decimal

from django.db.models import Count, Sum

from app.models import (
    AtributoOpcion, Categoria, GuiaTalla, Movimientos_Producto, Producto,
    Producto_Talla, Productos_Recepcionados,
)
from app.utils_producto_match import normalizar_articulo, ordenar_por_reciente

from .perfiles import clave_marca, perfil_para
from .precios import fmt, precio_por_regla, redondeo_js
from .tallas import (
    clave_guia, clave_talla_ficha, es_talla_legacy, mapa_guia, preferencia_talla,
    talla_casa,
)

# Atributo de género: los productos usan "Sexo" (id 3 en prod); "Género" es el
# atributo 4 que quedó vacío. Se prueba en ese orden.
ATRIBUTOS_GENERO = ('Sexo', 'Género')

# Movimientos que cuentan como "esta factura ya entró".
CONCEPTOS_INGRESO = ('INGRESO_MANUAL', 'RECEPCION_COMPRA')


def estado_visible(plan):
    return 'ERROR' if plan['errores'] and plan['estado'] != 'YA_CARGADO' else plan['estado']


def opciones_existente(plan):
    """Opciones que se ofrecen para un código existente.

    Devuelve None si la factura no cambia costo ni venta (solo se carga stock,
    sin preguntar). [t] "solo stock" solo si el costo no cambia: la vista
    registra la compra/DTE con el costo que se le envía, así que con el costo
    viejo el detalle del DTE quedaría distinto a la factura.
    """
    c0, _s0, v0 = plan['vigentes']
    c1, _s1, v1 = plan['factura']
    if c0 == c1 and v0 == v1:
        return None
    return ['s', 'c', 'n'] if c0 != c1 else ['s', 't', 'n']


def tallas_y_stock(ficha):
    agg = Producto_Talla.objects.filter(producto=ficha).aggregate(n=Count('id'), s=Sum('stock'))
    return int(agg['n'] or 0), int(agg['s'] or 0)


def ingresado_contra_dte(dte):
    """Unidades de cada código que YA entraron contra este DTE, por bodega.

    {'articulo': {articulo: {alias: unidades}}, 'color': {(articulo, COLOR):
    {alias: unidades}}}: por código y, para las marcas donde el color es
    parte de la identidad, por código + color de la ficha. Se miran todas las
    bodegas (la factura pudo recibirse en otra) y dos fuentes que SÍ
    significan que el stock entró: los movimientos de ingreso y las
    recepciones. Las líneas Dte_Productos a secas NO cuentan: el importador
    XML las crea con la cantidad facturada sin mover stock. Por bodega se
    toma la mayor de las dos para no contar dos veces lo que el modal
    registra en ambas.
    """
    lineas, movs = {}, {}
    for art, color, alias, u in (Productos_Recepcionados.objects.filter(dte=dte)
                                 .values_list('producto_talla__producto__articulo',
                                              'producto_talla__producto__atributo2__valor',
                                              'producto_talla__producto__sucursal__alias')
                                 .annotate(u=Sum('stockArribado'))):
        clave = (normalizar_articulo(art), str(color or '').strip().upper(), alias)
        lineas[clave] = lineas.get(clave, 0) + int(u or 0)
    for art, color, alias, u in (Movimientos_Producto.objects
                                 .filter(dte=dte, concepto__in=CONCEPTOS_INGRESO, cantidad__gt=0)
                                 .values_list('ProductoTalla__producto__articulo',
                                              'ProductoTalla__producto__atributo2__valor',
                                              'ProductoTalla__producto__sucursal__alias')
                                 .annotate(u=Sum('cantidad'))):
        clave = (normalizar_articulo(art), str(color or '').strip().upper(), alias)
        movs[clave] = movs.get(clave, 0) + int(u or 0)
    por_articulo, por_color = {}, {}
    for (art, color, alias) in set(lineas) | set(movs):
        u = max(lineas.get((art, color, alias), 0), movs.get((art, color, alias), 0))
        bodegas = por_articulo.setdefault(art, {})
        bodegas[alias] = bodegas.get(alias, 0) + u
        por_color.setdefault((art, color), {})[alias] = u
    return {'articulo': por_articulo, 'color': por_color}


class PlanificadorCarga:
    """Planifica facturas con las opciones de la carga.

    opts (todas opcionales): solo (lista de artículos), confirmar_duplicados,
    forzar, renombrar_tallas (default True), umbral_costo / factor_bajo /
    factor_alto (None = los del perfil de la marca).
    """

    def __init__(self, opts=None):
        self.opts = opts if opts is not None else {}
        self._cache_guias = {}

    # ----------------------------------------------------------- opciones

    def _opt(self, clave, defecto=None):
        valor = self.opts.get(clave)
        return defecto if valor is None else valor

    def perfil(self, f):
        if f.get('perfil') is None:
            f['perfil'] = perfil_para(f['data'].get('marca'))
        return f['perfil']

    def regla(self, f):
        """(umbral, factor_bajo, factor_alto) de la factura: opciones o perfil."""
        p = self.perfil(f)
        return (self._opt('umbral_costo', p.umbral_costo),
                self._opt('factor_bajo', p.factor_bajo),
                self._opt('factor_alto', p.factor_alto))

    # ----------------------------------------------------------- factura

    def planificar_factura(self, f, vistos):
        """Planes de las líneas de una factura.

        `vistos` acumula los códigos de las facturas anteriores de esta misma
        corrida: en la vista previa sirve para avisar que un código NUEVO que
        se repite se creará con la primera factura.
        """
        data = f['data']
        perfil = self.perfil(f)
        solo = self.opts.get('solo')
        pedidos = {normalizar_articulo(a) for a in solo} if solo else None
        ingresado = ingresado_contra_dte(f['dte'])
        comunes = {'marca': data.get('marca'), 'color': data.get('color'),
                   'fuente_pv': data.get('fuente_precioventa') or 'fijado en el JSON',
                   'tipo_talla': (data.get('tipo_talla') or perfil.tipo_talla or 'CL').upper(),
                   'guias': {str(k).upper(): v
                             for k, v in (data.get('guias_talla') or perfil.guias or {}).items()},
                   'perfil': perfil, 'regla': self.regla(f)}
        planes = []
        for n, linea in enumerate(data['lineas'], start=1):
            articulo = normalizar_articulo(linea['articulo'])
            if pedidos is not None and articulo not in pedidos:
                continue
            plan = self.planificar_linea(n, linea, comunes, f['sucursal'], ingresado, f['margen'])
            previa = vistos.get(articulo)
            if previa and plan['estado'] == 'NUEVO':
                plan['avisos'].append(f'también viene en la factura {previa}: se crea con esa '
                                      f'y aquí solo suma stock')
            vistos.setdefault(articulo, f['dte'].numero_documento)
            planes.append(plan)
        return planes

    # ----------------------------------------------------------- catálogo

    def opcion(self, atributos, valor):
        for nombre in atributos:
            op = AtributoOpcion.objects.filter(
                atributo__nombre__iexact=nombre, valor__iexact=str(valor).strip(),
            ).order_by('id').first()
            if op:
                return op
        return None

    def categoria(self, ruta):
        partes = [p.strip() for p in str(ruta).split('>') if p.strip()]
        if not partes:
            return None
        qs = Categoria.objects.filter(nombre__iexact=partes[-1])
        if len(partes) > 1:
            qs = qs.filter(padre__nombre__iexact=partes[-2])
        cats = list(qs[:2])
        return cats[0] if len(cats) == 1 else None

    def nombre_guia(self, guias, genero, tallas_fact, perfil):
        """Guía que corresponde a la línea.

        Tallas de niño (sufijo del perfil, C o Y en Nike) o género NIÑO/NIÑA →
        INFANTIL; si no, la del género (HOMBRE / MUJER / UNISEX)."""
        if not guias:
            return None
        valor_genero = str(getattr(genero, 'valor', '') or '').upper()
        es_nino = (any(str(t).strip().upper()[-1:] in perfil.sufijos_nino for t in tallas_fact)
                   or valor_genero in ('NIÑO', 'NIÑA', 'NINO', 'NINA'))
        if es_nino:
            return guias.get('INFANTIL')
        return guias.get(valor_genero) or guias.get('DEFAULT')

    def guias_de_marca(self, marca):
        if marca is None:
            return []
        clave = str(marca.valor).strip().upper()
        if clave not in self._cache_guias:
            self._cache_guias[clave] = list(GuiaTalla.objects.filter(
                marca__valor__iexact=marca.valor).order_by('id'))
        return self._cache_guias[clave]

    def genero_del_modelo(self, articulo, marca, separador):
        """Género con que están cargados los OTROS colores del mismo modelo.

        El código es modelo<separador>color ('DV4342-002'): se buscan los otros
        colores del modelo ('DV4342-*') de la misma marca en todas las bodegas.
        Cuenta un voto por código (la ficha más reciente de cada uno), no por
        ficha, para que un código repetido en varias bodegas no pese más.
        Devuelve (AtributoOpcion, detalle) solo si hay un género claramente
        mayoritario.
        """
        if not separador or separador not in articulo:
            return None
        modelo = articulo.rsplit(separador, 1)[0]
        vistos, votos, genero_por_id = set(), {}, {}
        for f in ordenar_por_reciente(
                Producto.objects.filter(articulo__istartswith=f'{modelo}{separador}', atributo1=marca)
                .exclude(atributo3__isnull=True).select_related('atributo3')):
            codigo = normalizar_articulo(f.articulo)
            if codigo == articulo or codigo in vistos or codigo.rsplit(separador, 1)[0] != modelo:
                continue
            vistos.add(codigo)
            votos.setdefault(f.atributo3_id, []).append(codigo)
            genero_por_id[f.atributo3_id] = f.atributo3
        if not votos:
            return None
        ranking = sorted(votos.items(), key=lambda kv: len(kv[1]), reverse=True)
        if len(ranking) > 1 and len(ranking[0][1]) == len(ranking[1][1]):
            return None  # empate: no hay una forma clara, se deja la del JSON
        gid, codigos = ranking[0]
        otros = '; '.join(f'{genero_por_id[g].valor} {len(c)}' for g, c in ranking[1:])
        detalle = f'{", ".join(sorted(codigos)[:4])}' + (f' · también {otros}' if otros else '')
        return genero_por_id[gid], detalle

    # -------------------------------------------------------------- línea

    def planificar_linea(self, n, linea, comunes, sucursal, ingresado, margen_sobre):
        perfil = comunes['perfil']
        umbral, factor_bajo, factor_alto = comunes['regla']
        confirmar_duplicados = bool(self.opts.get('confirmar_duplicados'))
        forzar = bool(self.opts.get('forzar'))
        renombrar_tallas = self._opt('renombrar_tallas', True)

        articulo = normalizar_articulo(linea['articulo'])
        plan = {
            'n': n, 'articulo': articulo, 'linea': linea, 'estado': 'NUEVO',
            'errores': [], 'avisos': [], 'destino': None, 'referencia': None,
            'gemelas': [], 'especialidades': [],
        }
        err, avisar = plan['errores'].append, plan['avisos'].append

        # --- cuadre de la línea contra la factura
        tallas_fact = linea.get('tallas') or {}
        unidades = sum(int(c) for c in tallas_fact.values())
        plan['unidades'] = unidades
        costo = int(linea.get('costo') or 0)
        if not tallas_fact:
            err('sin tallas')
        if linea.get('cantidad') is not None and unidades != int(linea['cantidad']):
            err(f'las tallas suman {unidades} pero la factura dice {linea["cantidad"]}')
        if costo <= 0:
            err('costo en 0')
        # Tolerancia de $1 por unidad: con descuento por línea el costo neto
        # unitario viene redondeado (importe ÷ cantidad).
        if (linea.get('importe') is not None
                and abs(costo * unidades - int(linea['importe'])) > max(2, unidades)):
            avisar(f'costo × unidades = {fmt(costo * unidades)} ≠ importe {fmt(linea["importe"])}')

        # --- identidad pedida en el JSON
        marca = self.opcion(['Marca'], linea.get('marca') or comunes['marca'])
        color = self.opcion(['Color'], linea.get('color') or comunes['color'])
        genero = self.opcion(ATRIBUTOS_GENERO, linea.get('genero') or '')
        categoria = self.categoria(linea.get('categoria') or '')
        genero_json = genero   # el de la factura (la ficha existente puede pisar `genero`)

        # --- fichas que ya existen con este código
        # Identidad del producto para decidir si EXISTE = artículo + marca.
        # Color, género y categoría NO cuentan: una ficha puede estar mal
        # creada (otro género, otra categoría) y sigue siendo el mismo
        # producto; crear otra sería duplicarlo. Las fichas del mismo código
        # con OTRA marca se informan pero no se usan (salvo "ficha_id").
        # Prefiltro por contención (no iexact): hay fichas legacy con espacios
        # o NBSP en el código que iexact no ve; el filtro fino es normalizar.
        token = articulo.split(' ')[0]
        mismo_codigo = [f for f in ordenar_por_reciente(
                            Producto.objects.filter(articulo__icontains=token)
                            .select_related('sucursal', 'atributo1', 'atributo2',
                                            'atributo3', 'categoria', 'guia_talla'))
                        if normalizar_articulo(f.articulo) == articulo]
        # Misma marca = mismo nombre canónico, no mismo id: la marca puede
        # estar duplicada como opción ('NIKE' / 'Nike' / 'NIKE ').
        clave = clave_marca(getattr(marca, 'valor', ''))
        fichas = [f for f in mismo_codigo
                  if marca is not None and clave_marca(getattr(f.atributo1, 'valor', '')) == clave]
        otra_marca = [f for f in mismo_codigo if f not in fichas]
        # Marcas cuyo código NO lleva el color (perfil.identidad_color): con el
        # color conocido solo cuentan las fichas de ese color; las de otros
        # colores se informan y, si no hay ninguna del color, es variante
        # NUEVA. Con el color por defecto (desconocido) se cae a la regla por
        # código, que pide elegir ficha si hay varias.
        otros_colores = []
        color_conocido = (perfil.identidad_color and color is not None
                          and str(color.valor).strip().upper() != str(perfil.color_defecto).upper())
        if color_conocido and fichas:
            otros_colores = [f for f in fichas if f.atributo2_id != color.id]
            fichas = [f for f in fichas if f.atributo2_id == color.id]
        locales = [f for f in fichas if f.sucursal_id == sucursal.id]
        otras = [f for f in fichas if f.sucursal_id != sucursal.id]
        # Todas las fichas del código en la bodega (cualquier marca): son las
        # que se pueden elegir como "ficha_id" en la vista previa.
        plan['candidatas'] = [f for f in mismo_codigo if f.sucursal_id == sucursal.id]
        pedida = (getattr(marca, 'id', None), getattr(color, 'id', None),
                  getattr(genero, 'id', None), getattr(categoria, 'id', None))

        def identidad(f):
            return (f.atributo1_id, f.atributo2_id, f.atributo3_id, f.categoria_id)

        def describir(f):
            return (f'{getattr(f.atributo1, "valor", "-")}/{getattr(f.atributo2, "valor", "-")}/'
                    f'{getattr(f.atributo3, "valor", "-")}/{getattr(f.categoria, "nombre", "-")}')

        def resumen_ficha(f):
            n_tallas, stock = tallas_y_stock(f)
            return (f'#{f.id} {f.sucursal.alias} «{f.descripcion}» {describir(f)} '
                    f'({n_tallas} tallas, stock {stock})')

        nombre_marca = str(getattr(marca, 'valor', '') or '').upper()
        for f in otra_marca:
            valor = str(getattr(f.atributo1, 'valor', '') or '').upper()
            if nombre_marca and nombre_marca in valor and f.sucursal_id == sucursal.id:
                avisar(f'mismo código con marca «{f.atributo1.valor}» en {sucursal.alias}: '
                       f'{resumen_ficha(f)} — si es el mismo producto mal creado, pon '
                       f'"ficha_id": {f.id} en el JSON')
            elif nombre_marca and nombre_marca in valor:
                avisar(f'mismo código con marca «{f.atributo1.valor}» en otra bodega: '
                       f'{resumen_ficha(f)} — no se usa (revisar esa ficha aparte)')
            else:
                avisar(f'mismo código pero de otra marca ({valor}): {resumen_ficha(f)} — no se usa')

        ficha_id = linea.get('ficha_id')
        if ficha_id:
            # Elegida a mano en el JSON (cualquier ficha del código en la bodega).
            elegida = next((f for f in mismo_codigo
                            if f.id == int(ficha_id) and f.sucursal_id == sucursal.id), None)
            if elegida is None:
                hay = "; ".join(resumen_ficha(f) for f in mismo_codigo if f.sucursal_id == sucursal.id)
                err(f'ficha_id {ficha_id} no es una ficha de {articulo} en {sucursal.alias} '
                    f'(hay: {hay or "ninguna"})')
            else:
                plan['destino'] = plan['referencia'] = elegida
                plan['estado'] = 'EXISTE'
                descartadas = [f for f in locales if f.id != elegida.id]
                if descartadas:
                    avisar(f'entra en #{elegida.id} (elegida en el JSON); no se tocan: '
                           + '; '.join(resumen_ficha(f) for f in descartadas))
        elif len(locales) == 1:
            plan['destino'] = plan['referencia'] = locales[0]
            plan['estado'] = 'EXISTE'
            if identidad(locales[0]) != pedida:
                avisar(f'ya existe en {sucursal.alias} como {describir(locales[0])} '
                       f'(el JSON decía {getattr(color, "valor", "-")}/{getattr(genero, "valor", "-")}/'
                       f'{getattr(categoria, "nombre", "-")}): se usa esa ficha tal como está')
        elif len(locales) > 1:
            # Mismo código + marca dos o más veces en la bodega = el mismo
            # producto creado varias veces. Hay que decir en cuál entra.
            iguales = len({identidad(f) for f in locales}) == 1
            if confirmar_duplicados and iguales:
                plan['destino'] = plan['referencia'] = locales[0]
                plan['estado'] = 'EXISTE'
                avisar(f'{len(locales)} fichas iguales en {sucursal.alias}: entra en la más reciente '
                       f'#{locales[0].id}; no se tocan: '
                       + '; '.join(resumen_ficha(f) for f in locales[1:]))
            else:
                plan['estado'] = 'DUPLICADAS'
                err(f'{len(locales)} fichas de este código en {sucursal.alias}: '
                    + '; '.join(resumen_ficha(f) for f in locales)
                    + (' — pon "ficha_id" en el JSON (o --confirmar-duplicados para la más reciente)'
                       if iguales else
                       ' — son fichas DISTINTAS (color/género/categoría): pon "ficha_id" en el JSON'))
        elif otras:
            # Solo en otras bodegas: la ficha nueva copia la identidad de la
            # más reciente, para que sea la misma variante y sincronice.
            plan['referencia'] = otras[0]
            plan['estado'] = 'EXISTE_OTRAS'
            distintas = {identidad(f) for f in otras}
            if identidad(otras[0]) != pedida:
                avisar(f'existe en {otras[0].sucursal.alias} como {describir(otras[0])}: '
                       f'se crea con esa identidad')
            if len(distintas) > 1:
                avisar('en otras bodegas está con identidades distintas ('
                       + '; '.join(f'{f.sucursal.alias} #{f.id} {describir(f)}' for f in otras)
                       + f'): se copia la de la más reciente (#{otras[0].id})')

        ref = plan['referencia']
        re_genero = perfil.re_genero_explicito
        if ref is None and otros_colores:
            en_bodega = [f for f in otros_colores if f.sucursal_id == sucursal.id]
            avisar(f'mismo código en otros colores ({", ".join(sorted({getattr(f.atributo2, "valor", "-") for f in otros_colores}))}'
                   f'{" en " + sucursal.alias if en_bodega else " en otras bodegas"}): se crea la '
                   f'variante {color.valor}; si en realidad es una de esas, elige la ficha')
        if ref is not None:
            # La ficha existente manda: no se crea un gemelo con otra identidad.
            marca, color, genero, categoria = ref.atributo1, ref.atributo2, ref.atributo3, ref.categoria
            # Mismo código+marca (y color, si es parte de la identidad) en
            # otras tiendas. [s] solo les cambia el precio a las de la MISMA
            # identidad (así busca la vista).
            plan['gemelas'] = [(f.sucursal.alias, int(f.costo or 0), int(f.precioventa or 0),
                                identidad(f) == identidad(ref))
                               for f in mismo_codigo
                               if f.id != ref.id and f.sucursal_id != sucursal.id
                               and f.atributo1_id == ref.atributo1_id
                               and (not perfil.identidad_color or f.atributo2_id == ref.atributo2_id)]
        elif (not fichas and marca is not None and not linea.get('genero_fijo')
              and not (re_genero and re_genero.match(str(linea.get('descripcion') or '').upper()))):
            # Código nuevo sin género declarado en la factura: el género se
            # toma de cómo están cargados los otros colores del mismo modelo,
            # para que el modelo no quede repartido entre HOMBRE y UNISEX.
            del_modelo = self.genero_del_modelo(articulo, marca, perfil.separador_modelo)
            if del_modelo is not None:
                opcion, detalle = del_modelo
                if genero is None or opcion.id != genero.id:
                    avisar(f'género {opcion.valor} (no {getattr(genero, "valor", "-")}): así están '
                           f'cargados otros colores del modelo ({detalle})')
                    genero = opcion

        for nombre, obj, valor in (('marca', marca, linea.get('marca') or comunes['marca']),
                                   ('color', color, linea.get('color') or comunes['color']),
                                   ('género', genero, linea.get('genero')),
                                   ('categoría', categoria, linea.get('categoria'))):
            if obj is None and ref is not None:
                err(f'la ficha #{ref.id} de {ref.sucursal.alias} no tiene {nombre}: complétala en '
                    f'Gestión de Productos antes de cargar (la vista no puede sumar stock a una '
                    f'ficha sin {nombre})')
            elif obj is None:
                err(f'{nombre} {valor!r} no existe (o es ambigua) en el sistema')
        plan['marca'], plan['color'], plan['genero'], plan['categoria'] = marca, color, genero, categoria

        # --- ¿ya entró contra este DTE? (por unidades, en cualquier bodega).
        # Si el color es parte de la identidad se mira solo ese color: dos
        # colores del mismo código son dos líneas distintas de la factura.
        color_ingreso = str(getattr(color, 'valor', '') or '').strip().upper()
        if (perfil.identidad_color and color_ingreso
                and color_ingreso != str(perfil.color_defecto).upper()):
            ya = ingresado['color'].get((articulo, color_ingreso), {})
        else:
            ya = ingresado['articulo'].get(articulo, {})
        if ya:
            total = sum(ya.values())
            detalle = ', '.join(f'{a}: {u} u' for a, u in sorted(ya.items()))
            fuera = [a for a in ya if a != sucursal.alias]
            if forzar:
                avisar(f'este DTE ya tiene ingreso de este código ({detalle}): se carga igual '
                       f'por --forzar')
            elif fuera:
                err(f'este DTE ya tiene ingreso de este código en otra bodega ({detalle}): '
                    f'revisa antes de cargarlo en {sucursal.alias}')
            elif total >= unidades:
                plan['estado'] = 'YA_CARGADO'
            else:
                err(f'carga PARCIAL contra este DTE: ya entraron {total} de {unidades} u '
                    f'({detalle}); completa las tallas que faltan desde el modal')

        # --- tallas: las que la ficha ya tiene suman stock en SU fila (mismo
        # SKU); las que no tiene se agregan a la MISMA ficha (la vista las crea
        # con SKU nuevo). Nunca se crea otra ficha por tener tallas nuevas.
        #
        # Guía de talla (como elegir "Tipo Talla" + "Guía" en el modal): la
        # ficha queda asociada a la guía y cada talla se escribe tal como está
        # en la columna del tipo (US) de esa guía. Ficha existente → la suya.
        destino = plan['destino']
        plan['renombres'] = []        # (producto id, alias, Producto_Talla id, viejo, nuevo)
        plan['fichas_formato'] = []   # fichas que pasan a tipo US + guía
        plan['sin_resolver'] = []     # (alias, talla) que no calzan con la guía
        plan['conflictos'] = []       # (alias, viejo, nuevo, stock) destino ya ocupado

        # Guía que corresponde a la LÍNEA: por la factura (C/Y → INFANTIL) o
        # por el género del JSON; de la marca de la ficha si ya existe.
        marca_guia = ref.atributo1 if ref is not None else marca
        nombre_guia = linea.get('guia') or self.nombre_guia(comunes['guias'], genero_json, tallas_fact, perfil)
        guia_linea = None
        if nombre_guia and comunes['tipo_talla'] == 'US' and marca_guia is not None:
            guias = self.guias_de_marca(marca_guia)
            elegidas = [g for g in guias if g.nombre.strip().upper() == nombre_guia.strip().upper()]
            if elegidas:
                guia_linea = elegidas[0]
                if len(elegidas) > 1:
                    avisar(f'hay {len(elegidas)} guías «{nombre_guia}»: se usa la #{elegidas[0].id}')
            elif destino is None or renombrar_tallas:
                err(f'no existe la guía de talla «{nombre_guia}» para {marca_guia.valor} '
                    f'(hay: {", ".join(g.nombre for g in guias) or "ninguna"})')

        # Fichas existentes (esta y las gemelas de otras bodegas) pasan al
        # mismo formato que las nuevas: tipo US, guía, y cada talla escrita
        # como la guía (7 / 7.5 / 11C / 1.5Y). Misma regla que el lápiz del
        # modal (api_editar_talla_producto_global): si en una ficha ya existe
        # la talla destino, esa fila no se toca y se informa.
        renombrar = (renombrar_tallas and guia_linea is not None and ref is not None)
        if renombrar:
            objetivo = [f for f in mismo_codigo
                        if f.atributo1_id == marca_guia.id
                        and ((destino is not None and f.id == destino.id) or f.sucursal_id != sucursal.id)]
            self.planificar_renombres(plan, objetivo, guia_linea)
            plan['tipo_talla'], plan['guia'] = 'US', guia_linea
        elif destino is not None:
            plan['tipo_talla'] = destino.tipo_talla or 'CL'
            plan['guia'] = destino.guia_talla
        elif ref is not None and ref.guia_talla_id:
            plan['tipo_talla'], plan['guia'] = ref.tipo_talla or 'US', ref.guia_talla
        else:
            plan['tipo_talla'], plan['guia'] = comunes['tipo_talla'], guia_linea
        mapa = mapa_guia(plan['guia'], plan['tipo_talla']) if plan['guia'] else None

        def texto_talla(t_fact):
            """Cómo se escribe la talla en la ficha: la de la guía si hay."""
            if mapa is None:
                return talla_casa(t_fact)
            texto = mapa.get(clave_guia(t_fact))
            if texto is None:
                if destino is None:
                    err(f'la talla {t_fact} no está en la guía «{plan["guia"].nombre}» '
                        f'(columna {plan["tipo_talla"]}); corre revisar_guias_talla')
                return talla_casa(t_fact)
            # Ficha nueva en US: la talla de niño va con su letra (11C, 1.5Y),
            # igual que en la factura. Si la guía no la tiene, está por ajustar.
            sufijo = str(t_fact).strip().upper()[-1:]
            if (destino is None and plan['tipo_talla'] == 'US' and sufijo in perfil.sufijos_nino
                    and not texto.upper().endswith(sufijo)):
                err(f'la guía «{plan["guia"].nombre}» escribe la talla {t_fact} como «{texto}» '
                    f'(sin la {sufijo}); corre primero revisar_guias_talla --apply')
            return texto

        existentes = {}   # clave → (texto que se envía, SKU que recibe, [todas las filas])
        if destino is not None:
            renombrado = {pt_id: nuevo for pid, _a, pt_id, _v, nuevo in plan['renombres'] if pid == destino.id}
            por_clave = {}
            for pt_id, t, sku, stock in (Producto_Talla.objects.filter(producto=destino)
                                         .order_by('id').values_list('id', 'talla', 'sku', 'stock')):
                t = renombrado.get(pt_id, t)
                por_clave.setdefault(clave_talla_ficha(t), []).append((t, sku, stock))
            for clave_t, filas in por_clave.items():
                # min() es estable: a igual preferencia gana la de menor id,
                # que es la que la vista encuentra (.first() por pk).
                texto = min(filas, key=lambda fila: preferencia_talla(fila[0]))[0]
                sku = next(s for t, s, _st in filas if t == texto)
                existentes[clave_t] = (texto, sku, filas)
        tallas = []
        for t_fact, cant in tallas_fact.items():
            objetivo = texto_talla(t_fact)
            clave_t = clave_talla_ficha(objetivo)
            if clave_t in existentes:
                texto, sku, filas = existentes[clave_t]
                # Si una fila ya está escrita como la guía (p.ej. «8.5» junto a
                # un «8,5» que no se pudo renombrar), el stock entra en esa.
                exacta = next(((t, s) for t, s, _st in filas if t == objetivo), None)
                if exacta is not None:
                    texto, sku = exacta
                if texto != texto.strip():
                    err(f'la talla «{texto}» de la ficha tiene espacios: la vista crearía otra '
                        f'{texto.strip()} con SKU nuevo; corrígela en la ficha antes de cargar')
                if len(filas) > 1:
                    avisar(f'la talla {talla_casa(t_fact)} está {len(filas)} veces en la ficha ('
                           + ', '.join(f'«{t}» SKU {s} stock {st}' for t, s, st in filas)
                           + f'): el stock entra en «{texto}» SKU {sku}')
                tallas.append((str(t_fact), texto, int(cant), True))
            else:
                tallas.append((str(t_fact), objetivo, int(cant), False))
        finales = [t[1] for t in tallas]
        repetidas = {t for t in finales if finales.count(t) > 1}
        if repetidas:
            avisar(f'tallas que quedan iguales al convertir (se suman): {", ".join(sorted(repetidas))}')
        plan['tallas'] = tallas

        # --- precios: los de la factura (costo y venta por regla) y, si el
        # código existe, los vigentes.
        pv_factura = (int(linea['precioventa']) if linea.get('precioventa')
                      else precio_por_regla(costo, umbral, factor_bajo, factor_alto))
        plan['fuente_pv'] = (comunes['fuente_pv'] if linea.get('precioventa') else
                             f'regla ×{factor_bajo} / ×{factor_alto}')
        if linea.get('_precio_duda'):
            avisar(f'precio de venta dudoso al leerlo: {linea["_precio_duda"]}')
        if ref is not None:
            # Sobreprecio: se conserva el de la ficha (solo cambian costo y venta).
            sobre_factura = int(ref.sobreprecio or 0)
            plan['vigentes'] = (int(ref.costo or 0), int(ref.sobreprecio or 0), int(ref.precioventa or 0))
        else:
            sobre_factura = (int(linea['sobreprecio']) if linea.get('sobreprecio')
                             else redondeo_js(Decimal(costo) * margen_sobre / 100))
            plan['vigentes'] = None
        plan['factura'] = (costo, sobre_factura, pv_factura)
        if pv_factura <= costo:
            err(f'precio de venta {fmt(pv_factura)} no supera el costo {fmt(costo)}')

        # --- especialidades: solo para fichas nuevas en la bodega. En una
        # existente, la vista BORRA las que no vengan en la lista; no se tocan.
        if plan['destino'] is None:
            for slug in linea.get('especialidades') or []:
                op = self.opcion(['Especialidad'], slug)
                if op is None:
                    err(f'especialidad {slug!r} no existe')
                else:
                    plan['especialidades'].append(op)
        return plan

    def planificar_renombres(self, plan, fichas, guia):
        """Qué tallas de `fichas` cambian de texto para quedar como la guía (US).

        Talla legacy '700' → 7; '7,0' → 7; '3.5Y'/'11C' se mantienen; '1,0' en
        guía INFANTIL → 1Y. Lo que no calza con la guía (p.ej. un «11» en una
        ficha de niño: ¿11C?) se deja igual y se informa."""
        mapa = mapa_guia(guia, 'US')
        for f in fichas:
            filas = list(Producto_Talla.objects.filter(producto=f)
                         .order_by('id').values_list('id', 'talla', 'stock'))
            textos = {t for _i, t, _s in filas}
            for pt_id, t, stock in filas:
                if es_talla_legacy(t):
                    key = (clave_talla_ficha(t), '')
                else:
                    key = clave_guia(t)
                nuevo = mapa.get(key)
                if nuevo is None:
                    plan['sin_resolver'].append((f.sucursal.alias, t))
                    continue
                if nuevo == t:
                    continue
                if nuevo in textos:
                    plan['conflictos'].append((f.sucursal.alias, t, nuevo, int(stock or 0)))
                    continue
                plan['renombres'].append((f.id, f.sucursal.alias, pt_id, t, nuevo))
                textos.add(nuevo)
            plan['fichas_formato'].append(f)
