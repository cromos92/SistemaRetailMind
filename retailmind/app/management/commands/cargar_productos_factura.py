"""
Carga los productos de una o varias facturas de proveedor por el MISMO camino
que el modal "Crear Producto Manual" de /app/verGestionProducto/.

Cada factura se transcribe a un JSON (ver compras/facturas/): folio, RUT del
proveedor y una línea por código con su curva de tallas US, costo neto
unitario y la clasificación (género, categoría v1.2, especialidades). Cada
línea queda asociada al DTE de SU factura (folio + RUT del emisor).

Por qué llama a la vista en vez de escribir directo: `crear_producto_manual`
deja el ingreso en cinco tablas (movimiento, lote FIFO, líneas del DTE,
recepción y la "Compra Manual"). Copiar esa lógica aquí sería una segunda
versión que se desincroniza con el tiempo. El comando solo arma el mismo POST
que el modal.

Reglas:
  - Precio de venta = costo × 1,85 si el costo es menor a $40.000, costo × 1,8
    desde $40.000 (--umbral-costo / --factor-bajo / --factor-alto), redondeado
    a ...990 como el modal. Sobreprecio de fichas nuevas = % de márgenes de la
    bodega (EmpresaUser).
  - Código NUEVO → se crea la ficha con tallas, stock, precios y especialidades.
    Género: el de la factura (W/WMNS = MUJER, M = HOMBRE); si no lo dice, el
    de los otros colores del mismo modelo ya cargados; si no hay, el del JSON.
  - Código que YA EXISTE → nunca se crea otra ficha: se usa la existente (con
    su identidad). Las tallas que ya tiene suman stock en su SKU; las que no
    tiene se agregan a esa misma ficha. Si la factura cambia precios, se
    pregunta por línea: [s] stock + costo + venta (también en esa variante de
    otras tiendas, con aviso) · [c] stock + costo, la venta sigue igual ·
    [t] solo stock (solo si el costo no cambia: la compra/DTE se registra con
    el costo que se envía) · [n] saltar. [c] y [t] no tocan otras tiendas.
  - Tallas: como elegir "Tipo Talla" + "Guía" en el modal. Ficha nueva → guía
    según "guias_talla" del JSON (INFANTIL si la talla trae C/Y, si no la del
    género) y la talla se escribe tal como está en la columna del tipo (US) de
    la guía; si una talla no está en la guía → ERROR. Ficha existente → su tipo
    y su guía (la talla de la factura se convierte por la guía si la tiene).
  - Si el código tiene varias fichas en la bodega con distinta identidad, la
    línea da ERROR hasta que el JSON diga en cuál entra ("ficha_id").

Seguro por diseño:
  - Sin --apply NO escribe nada: muestra la vista previa.
  - Con --apply primero valida TODAS las facturas; si alguna tiene errores no
    escribe nada. Después carga una por una, re-planificando cada una para que
    vea lo que crearon las anteriores (un código repetido en dos facturas se
    crea con la primera y en la segunda solo suma stock).
  - Cada línea va en su propia transacción: si la vista falla (o no alcanza a
    registrar la compra/DTE) se deshace ENTERA, no queda a medias.
  - Lo ya ingresado contra el DTE (en cualquier bodega) se detecta por
    unidades: completo → se salta; parcial o en otra bodega → ERROR.

Uso (desde retailmind/):
    python manage.py cargar_productos_factura "compras/facturas/EQUINOX_*.json"
    python manage.py cargar_productos_factura "compras/facturas/EQUINOX_*.json" --apply
    python manage.py cargar_productos_factura compras/facturas/EQUINOX_148763.json --solo HQ6034-001 --apply
"""
import glob
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.test import RequestFactory

from app.models import (
    AtributoOpcion, Categoria, Dte, Dte_Productos, EmpresaUser, GuiaTalla,
    Movimientos_Producto, Producto, Producto_Talla, Sucursal,
)
from app.utils_producto_match import normalizar_articulo, ordenar_por_reciente

# Talla numérica con sufijo opcional de la factura Nike: C (toddler), Y (youth).
_RE_TALLA = re.compile(r'^(\d+(?:[.,]\d+)?)\s*([CY]?)$')

# Talla única/código con cero adelante ('00', '0'): no es un número.
_RE_TALLA_CERO = re.compile(r'^0\d*$')

# Formato de talla que dejó la migración Laravel en fichas de calzado:
# '700' = 7,0 · '750' = 7,5 · '100' = 10 · '105' = 10,5. Solo los de 3
# dígitos: los de 2 ('40', '45') chocan con tallas europeas y no se tocan.
_RE_TALLA_LEGACY = re.compile(r'^(?:([2-9])([05])0|(1[0-3])([05]))$')

# Prefijo de la descripción que declara el género en la factura Nike.
_RE_GENERO_EXPLICITO = re.compile(r'^(W|WMNS|W MNS|M)\b')

# Atributo de género: los productos usan "Sexo" (id 3 en prod); "Género" es el
# atributo 4 que quedó vacío. Se prueba en ese orden.
_ATRIBUTOS_GENERO = ('Sexo', 'Género')

# Responsable que deja el modal en los movimientos (la sesión nunca trae
# 'nombreUsuario', así que la vista cae a 'Sistema').
_RESPONSABLE = 'Sistema'

# Movimientos que cuentan como "esta factura ya entró".
_CONCEPTOS_INGRESO = ('INGRESO_MANUAL', 'RECEPCION_COMPRA')


def _numero(valor):
    """Decimal → texto sin exponente ni ceros de más: 10 → '10', 7.50 → '7.5'."""
    return format(Decimal(valor).normalize(), 'f')


def talla_casa(talla):
    """Pasa una talla US de factura al formato con que están cargados los Nike.

    '7' → '7,0' · '7.5' → '7,5' · '10' → '10' · '11C' → '11' · '1.5Y' → '1,5'.
    Coma decimal, ',0' solo bajo 10 y sin el sufijo C/Y (así están las fichas
    JR/TD nativas de EDEL). Lo no numérico (S, M, L) y '00' quedan igual.
    """
    s = str(talla).strip().upper()
    m = _RE_TALLA.match(s)
    if not m or _RE_TALLA_CERO.match(s):
        return s
    valor = Decimal(m.group(1).replace(',', '.'))
    if valor == valor.to_integral_value():
        n = int(valor)
        return f'{n},0' if n < 10 else str(n)
    return _numero(valor).replace('.', ',')


def clave_talla(talla):
    """Clave de equivalencia: '7' ≡ '7,0' ≡ '7.0' ≡ '7C'. Respeta '00'."""
    s = str(talla or '').strip().upper()
    if _RE_TALLA_CERO.match(s):
        return s
    m = _RE_TALLA.match(s)
    if not m:
        return s
    return _numero(m.group(1).replace(',', '.'))


def clave_guia(talla):
    """Clave para buscar una talla US en una guía: número + 'C' si es de bebé.

    '7' ≡ '7.0' ≡ '7,0' · '11C' ≠ '11' (bebé vs adulto) · '1.5Y' ≡ '1.5' (las
    guías Nike escriben las juveniles sin la Y)."""
    s = str(talla or '').strip().upper()
    m = _RE_TALLA.match(s)
    if not m or _RE_TALLA_CERO.match(s):
        return (s, '')
    return (_numero(m.group(1).replace(',', '.')), 'C' if m.group(2) == 'C' else '')


def es_talla_legacy(talla):
    return bool(_RE_TALLA_LEGACY.match(str(talla or '').strip()))


def clave_talla_ficha(talla):
    """clave_talla() para una talla que YA está en una ficha: entiende además
    el formato legacy ('700' ≡ 7,0), para no crear un '7,0' duplicado en una
    ficha que ya tiene esa talla escrita a la antigua."""
    m = _RE_TALLA_LEGACY.match(str(talla or '').strip())
    if not m:
        return clave_talla(talla)
    entero, medio = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return _numero(f'{entero}.{medio}')


def _preferencia_talla(texto):
    """Orden para elegir entre varias filas de la MISMA talla en una ficha:
    formato de la casa ('7,0') > otro formato ('7') > legacy ('700') > con
    espacios (la vista no la encontraría)."""
    if texto != texto.strip():
        return 3
    if es_talla_legacy(texto):
        return 2
    return 0 if texto == talla_casa(texto) else 1


def _redondeo_js(valor):
    """Math.round de JS para positivos."""
    return int(Decimal(valor).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def redondear_990(valor):
    """redondearPrecio990() del modal: baja al millar y suma 990."""
    return valor if valor < 1000 else (valor // 1000) * 1000 + 990


def _fmt(n):
    return f'{int(n):,}'.replace(',', '.')


def _variantes_rut(rut):
    """'77402098-5' → {'77402098-5', '77.402.098-5'} (el RUT se guarda de ambas formas)."""
    limpio = re.sub(r'[^0-9Kk]', '', str(rut))
    cuerpo, dv = limpio[:-1], limpio[-1].upper()
    return {str(rut).strip(), f'{cuerpo}-{dv}', f'{cuerpo}{dv}',
            f'{int(cuerpo):,}'.replace(',', '.') + f'-{dv}', f'{cuerpo}-{dv.lower()}'}


class _Revertir(Exception):
    """Deshace la transacción de una línea cuya carga no quedó completa."""


class Command(BaseCommand):
    help = ('Crea/suma los productos de una o varias facturas (JSON) por el mismo '
            'camino que el modal Crear Producto Manual. Vista previa por defecto; '
            '--apply escribe.')

    def add_arguments(self, parser):
        parser.add_argument('archivos', nargs='+',
                            help='JSON de las facturas (ver compras/facturas/; acepta comodines)')
        parser.add_argument('--apply', action='store_true',
                            help='Escribe (sin esto solo muestra la vista previa)')
        parser.add_argument('--usuario', default=None,
                            help="username del ingreso (default: 'sistema' o el primer superusuario)")
        parser.add_argument('--solo', nargs='+', default=None, metavar='ARTICULO',
                            help='Procesar solo estos códigos (para probar con uno)')
        parser.add_argument('--dte-id', type=int, default=None,
                            help='Id del DTE si el folio calza con más de uno (solo con un '
                                 'archivo; para varios pon "dte_id" en cada JSON)')
        parser.add_argument('--umbral-costo', type=int, default=40000,
                            help='Costo desde el que se usa --factor-alto (default 40000)')
        parser.add_argument('--factor-bajo', type=Decimal, default=Decimal('1.85'),
                            help='Venta = costo × este factor si el costo es menor al umbral (default 1.85)')
        parser.add_argument('--factor-alto', type=Decimal, default=Decimal('1.8'),
                            help='Venta = costo × este factor desde el umbral (default 1.8)')
        parser.add_argument('--margen-sobreprecio', type=Decimal, default=None,
                            help='%% de sobreprecio de fichas nuevas (default: el de la bodega)')
        parser.add_argument('--si', action='store_true',
                            help='No preguntar: en los existentes aplica [s] (stock + costo + venta)')
        parser.add_argument('--confirmar-duplicados', action='store_true',
                            help='Cargar aunque el código tenga 2+ fichas iguales en la '
                                 'bodega (entra en la más reciente)')
        parser.add_argument('--forzar', action='store_true',
                            help='Cargar aunque el DTE ya tenga ingreso de ese código (en esta u '
                                 'otra bodega). DUPLICA stock: solo si sabes por qué')

    # ------------------------------------------------------------------ setup

    def handle(self, *args, **opts):
        self.opts = opts
        rutas = []
        for patron in opts['archivos']:
            encontrados = sorted(glob.glob(patron)) if any(c in patron for c in '*?[') else [patron]
            if not encontrados:
                raise CommandError(f'Ningún archivo calza con {patron}')
            rutas.extend(encontrados)
        if opts['dte_id'] and len(rutas) > 1:
            raise CommandError('--dte-id solo sirve con un archivo; para varios pon "dte_id" en cada JSON')

        user = self._resolver_usuario(opts['usuario'])
        facturas = [self._cargar_factura(Path(r), user) for r in rutas]
        if opts['solo']:
            pedidos = {normalizar_articulo(a) for a in opts['solo']}
            presentes = {normalizar_articulo(l['articulo']) for f in facturas for l in f['data']['lineas']}
            faltan = pedidos - presentes
            if faltan:
                raise CommandError(f'No están en los JSON: {", ".join(sorted(faltan))}')

        if not opts['apply']:
            vistos = {}
            for f in facturas:
                planes = self._planificar_factura(f, vistos)
                self._imprimir_factura(f, planes, user)
            self.stdout.write(self.style.WARNING(
                '\nVISTA PREVIA: no se escribió nada. Para cargar, repite con --apply '
                '(te preguntará antes de tocar productos que ya existen).'))
            return

        # Validación completa antes de escribir la primera línea.
        vistos, con_error = {}, []
        for f in facturas:
            planes = self._planificar_factura(f, vistos)
            if any(p['errores'] for p in planes):
                con_error.append((f, planes))
        if con_error:
            for f, planes in con_error:
                self._imprimir_factura(f, planes, user)
            raise CommandError(
                'Hay líneas con ERROR (arriba). Corrige el JSON o excluye esas '
                'líneas con --solo; no se escribió nada.')

        for f in facturas:
            planes = self._planificar_factura(f, {})  # ve lo que crearon las anteriores
            self._imprimir_factura(f, planes, user)
            if any(p['errores'] for p in planes):
                raise CommandError(f'La factura {f["dte"].numero_documento} quedó con errores '
                                   f'tras cargar las anteriores; se detiene aquí.')
            if not self._aplicar(f, planes, user):
                self.stdout.write(self.style.WARNING('Se detiene: las facturas siguientes no se cargaron.'))
                return

    def _cargar_factura(self, ruta, user):
        if not ruta.exists():
            raise CommandError(f'No existe el archivo {ruta}')
        with ruta.open(encoding='utf-8') as fh:
            data = json.load(fh)
        sucursal = Sucursal.objects.select_related('empresa').filter(
            alias__iexact=data['sucursal']).first()
        if sucursal is None:
            raise CommandError(f'{ruta.name}: no existe la sucursal {data["sucursal"]!r}')
        dte = self._resolver_dte(data, self.opts['dte_id'] or data.get('dte_id'), ruta.name)
        margen = (self.opts['margen_sobreprecio'] if self.opts['margen_sobreprecio'] is not None
                  else self._margen_sobreprecio(user, sucursal))
        return {'ruta': ruta, 'data': data, 'sucursal': sucursal, 'dte': dte, 'margen': margen}

    def _resolver_usuario(self, username):
        User = get_user_model()
        if username:
            user = User.objects.filter(username=username).first()
            if user is None:
                raise CommandError(f'No existe el usuario {username!r}')
            return user
        user = (User.objects.filter(username__iexact='sistema', is_active=True).first()
                or User.objects.filter(is_superuser=True, is_active=True).order_by('id').first())
        if user is None:
            raise CommandError('No hay usuario "sistema" ni superusuario activo: usa --usuario')
        return user

    def _resolver_dte(self, data, dte_id, nombre):
        qs = Dte.objects.select_related('emisor', 'receptor')
        if dte_id:
            dte = qs.filter(id=dte_id).first()
            if dte is None:
                raise CommandError(f'{nombre}: no existe el DTE id={dte_id}')
            return dte
        candidatos = list(qs.filter(numero_documento=data['folio'],
                                    emisor__rut__in=_variantes_rut(data['proveedor_rut'])))
        compras = [d for d in candidatos if d.tipo_transaccion == 'COMPRA']
        dtes = compras or candidatos
        if not dtes:
            raise CommandError(
                f'{nombre}: no está en el sistema la factura {data["folio"]} del RUT '
                f'{data["proveedor_rut"]}. Si la creaste con otro proveedor, pon su '
                f'"dte_id" en el JSON.')
        if len(dtes) > 1:
            detalle = ', '.join(f'id={d.id} ({d.emisor.nombre}, {d.fecha_emision}, '
                                f'{d.tipo_transaccion})' for d in dtes)
            raise CommandError(f'{nombre}: el folio {data["folio"]} calza con varios DTE: '
                               f'{detalle}. Pon el correcto como "dte_id" en el JSON.')
        return dtes[0]

    def _margen_sobreprecio(self, user, sucursal):
        """% de sobreprecio que usaría el modal en esa bodega (/app/margenes_usuario/).

        Si el usuario del comando no tiene márgenes ahí, se toma el de
        cualquier usuario de la bodega que sí los tenga configurados.
        """
        eu = (EmpresaUser.objects.filter(user=user, sucursal=sucursal, status=True)
              .exclude(margenSobreprecio__isnull=True).first()
              or EmpresaUser.objects.filter(sucursal=sucursal, status=True, margenSobreprecio__gt=0)
              .order_by('id').first())
        return Decimal(str(eu.margenSobreprecio)) if eu else Decimal('10')

    def _ingresado_contra_dte(self, dte):
        """Unidades de cada código que YA entraron contra este DTE, por bodega.

        {articulo: {alias bodega: unidades}}. Se miran todas las bodegas (la
        factura pudo recibirse en otra) y dos fuentes: las líneas del DTE y
        los movimientos de ingreso. Por bodega se toma la mayor de las dos
        para no contar dos veces lo que el modal registra en ambas.
        """
        lineas, movs = {}, {}
        for art, alias, u in (Dte_Productos.objects.filter(dte=dte)
                              .values_list('productoTalla__producto__articulo',
                                           'productoTalla__producto__sucursal__alias')
                              .annotate(u=Sum('stock'))):
            clave = (normalizar_articulo(art), alias)
            lineas[clave] = lineas.get(clave, 0) + int(u or 0)
        for art, alias, u in (Movimientos_Producto.objects
                              .filter(dte=dte, concepto__in=_CONCEPTOS_INGRESO, cantidad__gt=0)
                              .values_list('ProductoTalla__producto__articulo',
                                           'ProductoTalla__producto__sucursal__alias')
                              .annotate(u=Sum('cantidad'))):
            clave = (normalizar_articulo(art), alias)
            movs[clave] = movs.get(clave, 0) + int(u or 0)
        resultado = {}
        for (art, alias) in set(lineas) | set(movs):
            resultado.setdefault(art, {})[alias] = max(lineas.get((art, alias), 0),
                                                       movs.get((art, alias), 0))
        return resultado

    def _precio_venta(self, costo):
        factor = self.opts['factor_bajo'] if costo < self.opts['umbral_costo'] else self.opts['factor_alto']
        return redondear_990(_redondeo_js(Decimal(costo) * factor))

    # --------------------------------------------------------------- plan

    def _planificar_factura(self, f, vistos):
        """Planes de las líneas de una factura.

        `vistos` acumula los códigos de las facturas anteriores de esta misma
        corrida: en la vista previa sirve para avisar que un código NUEVO que
        se repite se creará con la primera factura.
        """
        data = f['data']
        pedidos = ({normalizar_articulo(a) for a in self.opts['solo']}
                   if self.opts['solo'] else None)
        ingresado = self._ingresado_contra_dte(f['dte'])
        comunes = {'marca': data.get('marca'), 'color': data.get('color'),
                   'fuente_pv': data.get('fuente_precioventa') or 'fijado en el JSON',
                   'tipo_talla': (data.get('tipo_talla') or 'CL').upper(),
                   'guias': {str(k).upper(): v for k, v in (data.get('guias_talla') or {}).items()}}
        planes = []
        for n, linea in enumerate(data['lineas'], start=1):
            articulo = normalizar_articulo(linea['articulo'])
            if pedidos is not None and articulo not in pedidos:
                continue
            plan = self._planificar(n, linea, comunes, f['sucursal'],
                                    ingresado.get(articulo, {}), f['margen'])
            previa = vistos.get(articulo)
            if previa and plan['estado'] == 'NUEVO':
                plan['avisos'].append(f'también viene en la factura {previa}: se crea con esa '
                                      f'y aquí solo suma stock')
            vistos.setdefault(articulo, f['dte'].numero_documento)
            planes.append(plan)
        return planes

    def _opcion(self, atributos, valor):
        for nombre in atributos:
            op = AtributoOpcion.objects.filter(
                atributo__nombre__iexact=nombre, valor__iexact=str(valor).strip(),
            ).order_by('id').first()
            if op:
                return op
        return None

    def _categoria(self, ruta):
        partes = [p.strip() for p in str(ruta).split('>') if p.strip()]
        if not partes:
            return None
        qs = Categoria.objects.filter(nombre__iexact=partes[-1])
        if len(partes) > 1:
            qs = qs.filter(padre__nombre__iexact=partes[-2])
        cats = list(qs[:2])
        return cats[0] if len(cats) == 1 else None

    def _nombre_guia(self, guias, genero, tallas_fact):
        """Guía que corresponde a la línea según el JSON ("guias_talla").

        Tallas de niño (sufijo C o Y en la factura) o género NIÑO/NIÑA →
        INFANTIL; si no, la del género (HOMBRE / MUJER / UNISEX)."""
        if not guias:
            return None
        valor_genero = str(getattr(genero, 'valor', '') or '').upper()
        es_nino = (any(str(t).strip().upper()[-1:] in ('C', 'Y') for t in tallas_fact)
                   or valor_genero in ('NIÑO', 'NIÑA', 'NINO', 'NINA'))
        if es_nino:
            return guias.get('INFANTIL')
        return guias.get(valor_genero) or guias.get('DEFAULT')

    def _guias_de_marca(self, marca):
        if marca is None:
            return []
        if not hasattr(self, '_cache_guias'):
            self._cache_guias = {}
        clave = str(marca.valor).strip().upper()
        if clave not in self._cache_guias:
            self._cache_guias[clave] = list(GuiaTalla.objects.filter(
                marca__valor__iexact=marca.valor).order_by('id'))
        return self._cache_guias[clave]

    def _mapa_guia(self, guia, tipo_talla):
        """{clave de la talla US: texto en la columna del tipo} de una guía.

        La factura trae tallas US; si la ficha es de otro tipo (CL, EU…) se
        convierte por la fila de la guía, igual que el modal al cambiar el
        tipo de talla."""
        columna = str(tipo_talla or 'US').lower()
        if columna not in ('cl', 'us', 'eu', 'uk', 'br', 'cm'):
            columna = 'us'
        mapa = {}
        for item in guia.items.order_by('orden', 'id'):
            us = (item.us or '').strip()
            texto = (getattr(item, columna) or '').strip()
            if us and texto:
                mapa.setdefault(clave_guia(us), texto)
        return mapa

    def _genero_del_modelo(self, articulo, marca):
        """Género con que están cargados los OTROS colores del mismo modelo.

        Nike codifica modelo-color ('DV4342-002'): se buscan los otros colores
        del modelo ('DV4342-*') de la misma marca en todas las bodegas. Cuenta
        un voto por código (la ficha más reciente de cada uno), no por ficha,
        para que un código repetido en varias bodegas no pese más. Devuelve
        (AtributoOpcion, detalle) solo si hay un género claramente mayoritario.
        """
        if '-' not in articulo:
            return None
        modelo = articulo.rsplit('-', 1)[0]
        vistos, votos, genero_por_id = set(), {}, {}
        for f in ordenar_por_reciente(
                Producto.objects.filter(articulo__istartswith=f'{modelo}-', atributo1=marca)
                .exclude(atributo3__isnull=True).select_related('atributo3')):
            codigo = normalizar_articulo(f.articulo)
            if codigo == articulo or codigo in vistos or codigo.rsplit('-', 1)[0] != modelo:
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

    def _planificar(self, n, linea, comunes, sucursal, ingresado, margen_sobre):
        opts = self.opts
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
        if linea.get('importe') is not None and costo * unidades != int(linea['importe']):
            avisar(f'costo × unidades = {_fmt(costo * unidades)} ≠ importe {_fmt(linea["importe"])}')

        # --- identidad pedida en el JSON
        marca = self._opcion(['Marca'], linea.get('marca') or comunes['marca'])
        color = self._opcion(['Color'], linea.get('color') or comunes['color'])
        genero = self._opcion(_ATRIBUTOS_GENERO, linea.get('genero') or '')
        categoria = self._categoria(linea.get('categoria') or '')

        # --- fichas que ya existen con este código
        fichas = [f for f in ordenar_por_reciente(
                      Producto.objects.filter(articulo__iexact=articulo)
                      .select_related('sucursal', 'atributo1', 'atributo2',
                                      'atributo3', 'categoria', 'guia_talla'))
                  if normalizar_articulo(f.articulo) == articulo]
        locales = [f for f in fichas if f.sucursal_id == sucursal.id]
        otras = [f for f in fichas if f.sucursal_id != sucursal.id]
        pedida = (getattr(marca, 'id', None), getattr(color, 'id', None),
                  getattr(genero, 'id', None), getattr(categoria, 'id', None))

        def identidad(f):
            return (f.atributo1_id, f.atributo2_id, f.atributo3_id, f.categoria_id)

        def describir(f):
            return (f'{getattr(f.atributo1, "valor", "-")}/{getattr(f.atributo2, "valor", "-")}/'
                    f'{getattr(f.atributo3, "valor", "-")}/{getattr(f.categoria, "nombre", "-")}')

        def agrupar(lista):
            grupos = {}
            for f in lista:
                grupos.setdefault(identidad(f), []).append(f)
            return grupos

        ficha_id = linea.get('ficha_id')
        if ficha_id:
            # Elegida a mano en el JSON: el código tiene varias fichas en la
            # bodega con distinta identidad y hay que decir en cuál entra.
            elegida = next((f for f in locales if f.id == int(ficha_id)), None)
            if elegida is None:
                err(f'ficha_id {ficha_id} no es una ficha de {articulo} en {sucursal.alias} '
                    f'(hay: {", ".join("#" + str(f.id) + " " + describir(f) for f in locales) or "ninguna"})')
            else:
                plan['destino'] = plan['referencia'] = elegida
                plan['estado'] = 'EXISTE'
                descartadas = [f for f in locales if f.id != elegida.id]
                if descartadas:
                    avisar(f'entra en #{elegida.id} (elegida en el JSON); no se tocan: '
                           + '; '.join(f'#{f.id} {describir(f)}' for f in descartadas))
        elif locales:
            grupos = agrupar(locales)
            if pedida in grupos:
                elegida = pedida
            elif len(grupos) == 1:
                elegida = next(iter(grupos))
                avisar(f'ya existe en {sucursal.alias} como {describir(grupos[elegida][0])}: '
                       f'se usa esa ficha')
            else:
                elegida = None
                err(f'existe en {sucursal.alias} con varias identidades: '
                    + '; '.join(f'#{g[0].id} {describir(g[0])}' for g in grupos.values())
                    + ' — pon "ficha_id" en el JSON con la correcta')
            if elegida:
                iguales = grupos[elegida]
                plan['destino'] = plan['referencia'] = iguales[0]
                plan['estado'] = 'EXISTE'
                if len(iguales) > 1:
                    if opts['confirmar_duplicados']:
                        avisar(f'{len(iguales)} fichas iguales en {sucursal.alias}: '
                               f'entra en la más reciente #{iguales[0].id}')
                    else:
                        plan['estado'] = 'DUPLICADAS'
                        err(f'{len(iguales)} fichas iguales en {sucursal.alias} '
                            f'({", ".join("#" + str(f.id) for f in iguales)}): '
                            f'usa --confirmar-duplicados o fusiónalas')
        elif otras:
            grupos = agrupar(otras)
            if pedida in grupos:
                plan['referencia'] = grupos[pedida][0]
            elif len(grupos) == 1:
                plan['referencia'] = next(iter(grupos.values()))[0]
                avisar(f'existe en {plan["referencia"].sucursal.alias} como '
                       f'{describir(plan["referencia"])}: se usa esa identidad')
            else:
                avisar('existe en otras bodegas con identidades distintas ('
                       + '; '.join(f'{g[0].sucursal.alias} {describir(g[0])}' for g in grupos.values())
                       + '): se crea con la del JSON')
            if plan['referencia'] is not None:
                plan['estado'] = 'EXISTE_OTRAS'

        ref = plan['referencia']
        if ref is not None:
            # La ficha existente manda: no se crea un gemelo con otra identidad.
            marca, color, genero, categoria = ref.atributo1, ref.atributo2, ref.atributo3, ref.categoria
            # Misma variante en otras tiendas: solo [s] les cambia el precio.
            plan['gemelas'] = [(f.sucursal.alias, int(f.costo or 0), int(f.precioventa or 0))
                               for f in otras if identidad(f) == identidad(ref) and f.id != ref.id]
        elif (not fichas and marca is not None and not linea.get('genero_fijo')
              and not _RE_GENERO_EXPLICITO.match(str(linea.get('descripcion') or '').upper())):
            # Código nuevo sin W/M en la factura: el género se toma de cómo
            # están cargados los otros colores del mismo modelo, para que el
            # modelo no quede repartido entre HOMBRE y UNISEX.
            del_modelo = self._genero_del_modelo(articulo, marca)
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
            if obj is None:
                err(f'{nombre} {valor!r} no existe (o es ambigua) en el sistema')
        plan['marca'], plan['color'], plan['genero'], plan['categoria'] = marca, color, genero, categoria

        # --- ¿ya entró contra este DTE? (por unidades, en cualquier bodega)
        if ingresado:
            total = sum(ingresado.values())
            detalle = ', '.join(f'{a}: {u} u' for a, u in sorted(ingresado.items()))
            fuera = [a for a in ingresado if a != sucursal.alias]
            if opts['forzar']:
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
        if destino is not None:
            plan['tipo_talla'] = destino.tipo_talla or 'CL'
            plan['guia'] = destino.guia_talla
        elif ref is not None and ref.guia_talla_id:
            plan['tipo_talla'], plan['guia'] = ref.tipo_talla or 'US', ref.guia_talla
        else:
            plan['tipo_talla'], plan['guia'] = comunes['tipo_talla'], None
            nombre_guia = linea.get('guia') or self._nombre_guia(comunes['guias'], genero, tallas_fact)
            if nombre_guia:
                guias = self._guias_de_marca(marca)
                elegidas = [g for g in guias if g.nombre.strip().upper() == nombre_guia.strip().upper()]
                if not elegidas:
                    err(f'no existe la guía de talla «{nombre_guia}» para '
                        f'{getattr(marca, "valor", "-")} (hay: '
                        f'{", ".join(g.nombre for g in guias) or "ninguna"})')
                else:
                    plan['guia'] = elegidas[0]
                    if len(elegidas) > 1:
                        avisar(f'hay {len(elegidas)} guías «{nombre_guia}»: se usa la #{elegidas[0].id}')
        mapa_guia = self._mapa_guia(plan['guia'], plan['tipo_talla']) if plan['guia'] else None

        def texto_talla(t_fact):
            """Cómo se escribe la talla en la ficha: la de la guía si hay."""
            if mapa_guia is None:
                return talla_casa(t_fact)
            texto = mapa_guia.get(clave_guia(t_fact))
            if texto is None:
                if destino is None:
                    err(f'la talla {t_fact} no está en la guía «{plan["guia"].nombre}» '
                        f'(columna {plan["tipo_talla"]}); agrégala a la guía o corrige el JSON')
                return talla_casa(t_fact)
            return texto

        existentes = {}   # clave → (texto que se envía, SKU que recibe, [todas las filas])
        if destino is not None:
            por_clave = {}
            for t, sku, stock in (Producto_Talla.objects.filter(producto=destino)
                                  .order_by('id').values_list('talla', 'sku', 'stock')):
                por_clave.setdefault(clave_talla_ficha(t), []).append((t, sku, stock))
            for clave, filas in por_clave.items():
                # min() es estable: a igual preferencia gana la de menor id,
                # que es la que la vista encuentra (.first() por pk).
                texto = min(filas, key=lambda fila: _preferencia_talla(fila[0]))[0]
                sku = next(s for t, s, _st in filas if t == texto)
                existentes[clave] = (texto, sku, filas)
        tallas = []
        for t_fact, cant in tallas_fact.items():
            objetivo = texto_talla(t_fact)
            clave = clave_talla_ficha(objetivo)
            if clave in existentes:
                texto, sku, filas = existentes[clave]
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
        pv_factura = int(linea['precioventa']) if linea.get('precioventa') else self._precio_venta(costo)
        plan['fuente_pv'] = (comunes['fuente_pv'] if linea.get('precioventa') else
                             f'regla ×{opts["factor_bajo"]} / ×{opts["factor_alto"]}')
        if linea.get('_precio_duda'):
            avisar(f'precio de venta dudoso al leerlo: {linea["_precio_duda"]}')
        if ref is not None:
            # Sobreprecio: se conserva el de la ficha (solo cambian costo y venta).
            sobre_factura = int(ref.sobreprecio or 0)
            plan['vigentes'] = (int(ref.costo or 0), int(ref.sobreprecio or 0), int(ref.precioventa or 0))
        else:
            sobre_factura = (int(linea['sobreprecio']) if linea.get('sobreprecio')
                             else _redondeo_js(Decimal(costo) * margen_sobre / 100))
            plan['vigentes'] = None
        plan['factura'] = (costo, sobre_factura, pv_factura)
        if pv_factura <= costo:
            err(f'precio de venta {_fmt(pv_factura)} no supera el costo {_fmt(costo)}')

        # --- especialidades: solo para fichas nuevas en la bodega. En una
        # existente, la vista BORRA las que no vengan en la lista; no se tocan.
        if plan['destino'] is None:
            for slug in linea.get('especialidades') or []:
                op = self._opcion(['Especialidad'], slug)
                if op is None:
                    err(f'especialidad {slug!r} no existe')
                else:
                    plan['especialidades'].append(op)
        return plan

    def _opciones_existente(self, plan):
        """Opciones que se ofrecen para un código existente.

        Devuelve None si la factura no cambia costo ni venta (solo se carga
        stock, sin preguntar). [t] "solo stock" solo si el costo no cambia:
        la vista registra la compra/DTE con el costo que se le envía, así que
        con el costo viejo el detalle del DTE quedaría distinto a la factura.
        """
        c0, _s0, v0 = plan['vigentes']
        c1, _s1, v1 = plan['factura']
        if c0 == c1 and v0 == v1:
            return None
        return ['s', 'c', 'n'] if c0 != c1 else ['s', 't', 'n']

    # ------------------------------------------------------------ reporte

    def _imprimir_factura(self, f, planes, user):
        self._imprimir_cabecera(f, user)
        for plan in planes:
            self._imprimir_plan(plan)
        self._imprimir_totales(f, planes)

    def _imprimir_cabecera(self, f, user):
        w, data, dte, sucursal = self.stdout.write, f['data'], f['dte'], f['sucursal']
        o = self.opts
        w('')
        w(self.style.MIGRATE_HEADING(
            f'══ Factura {dte.numero_documento} · {dte.emisor.nombre} ({dte.emisor.rut}) '
            f'· DTE id={dte.id} · emitida {dte.fecha_emision} · {f["ruta"].name}'))
        w(f'Bodega: {sucursal.alias} · usuario: {user.username} (responsable "{_RESPONSABLE}") '
          f'· venta = costo × {o["factor_bajo"]} (< ${_fmt(o["umbral_costo"])}) / × {o["factor_alto"]} '
          f'→ ...990 · sobreprecio nuevos: {f["margen"]}%')
        if dte.tipo_transaccion != 'COMPRA':
            w(self.style.WARNING(f'  ! el DTE está como {dte.tipo_transaccion}, no COMPRA'))
        if dte.receptor_id and dte.receptor_id != sucursal.empresa_id:
            w(self.style.WARNING(f'  ! el receptor del DTE no es la empresa de {sucursal.alias}'))
        if str(dte.fecha_emision) != str(data.get('fecha_emision', dte.fecha_emision)):
            w(self.style.WARNING(f'  ! fecha del JSON {data.get("fecha_emision")} ≠ DTE {dte.fecha_emision}'))
        if getattr(dte, 'descartado', False):
            w(self.style.WARNING('  ! el DTE está DESCARTADO'))
        n_lineas = Dte_Productos.objects.filter(dte=dte).count()
        if n_lineas:
            w(self.style.WARNING(f'  ! el DTE ya tiene {n_lineas} línea(s) de detalle'))
        w('')

    def _estado_visible(self, plan):
        return 'ERROR' if plan['errores'] and plan['estado'] != 'YA_CARGADO' else plan['estado']

    def _imprimir_plan(self, plan):
        w = self.stdout.write
        colores = {'NUEVO': self.style.SUCCESS, 'EXISTE': self.style.HTTP_INFO,
                   'EXISTE_OTRAS': self.style.HTTP_INFO, 'YA_CARGADO': self.style.WARNING,
                   'DUPLICADAS': self.style.ERROR, 'ERROR': self.style.ERROR}
        estado = self._estado_visible(plan)
        l = plan['linea']
        ident = ' · '.join(filter(None, [
            getattr(plan['categoria'], 'nombre', None), getattr(plan['genero'], 'valor', None),
            getattr(plan['color'], 'valor', None), getattr(plan['marca'], 'valor', None)]))
        esp = ', '.join(o.valor for o in plan['especialidades'])
        w(f'[{plan["n"]:>2}] {plan["articulo"]:<12} ' + colores.get(estado, str)(f'{estado:<12}')
          + f' {plan["unidades"]:>3} u  {l.get("descripcion", "")}')
        costo, sobre, pv = plan['factura']
        if plan['vigentes'] is not None:
            c0, _s0, v0 = plan['vigentes']
            ref = plan['referencia']
            donde = f'ficha #{ref.id} {ref.sucursal.alias} «{ref.descripcion}»'
            opciones = self._opciones_existente(plan)
            sentido = ('  BAJA la venta' if pv < v0 else '  sube la venta' if pv > v0 else '')
            w(f'      vigente ({donde}): costo {_fmt(c0)} · venta {_fmt(v0)}'
              f'  →  factura: costo {_fmt(costo)} · venta {_fmt(pv)}'
              + f' [{plan["fuente_pv"]}]'
              + ((sentido + f'  · te preguntará [{"/".join(opciones)}]') if opciones
                 else '  (sin cambios: solo suma stock)'))
            if plan['gemelas']:
                w('      misma variante en: '
                  + ', '.join(f'{a} (costo {_fmt(c)}, venta {_fmt(v)})' for a, c, v in plan['gemelas'])
                  + ' — solo [s] les aplica el precio de la factura y avisa a esas tiendas')
        else:
            w(f'      costo {_fmt(costo)} · sobreprecio {_fmt(sobre)} · venta {_fmt(pv)}'
              f'  [{plan["fuente_pv"]}]')
        w(f'      {ident}' + (f' · esp: {esp}' if esp else ''))
        guia_txt = f'guía «{plan["guia"].nombre}»' if plan.get('guia') else 'sin guía'
        if plan['destino'] is None:
            w(f'      tallas {plan["tipo_talla"]} ({guia_txt}): '
              + '  '.join(f'{final}×{cant}' for _f, final, cant, _e in plan['tallas']))
        else:
            suman = [f'{final}×{cant}' for _f, final, cant, existe in plan['tallas'] if existe]
            nuevas = [f'{final}×{cant}' for _f, final, cant, existe in plan['tallas'] if not existe]
            w(f'      ficha: tipo talla {plan["tipo_talla"]}, {guia_txt}')
            w('      suma stock en tallas que ya tiene: ' + ('  '.join(suman) or '—'))
            w('      tallas nuevas que se agregan a la ficha: ' + ('  '.join(nuevas) or '—'))
        for a in plan['avisos']:
            w(self.style.WARNING(f'      ! {a}'))
        for e in plan['errores']:
            w(self.style.ERROR(f'      ✗ {e}'))

    def _imprimir_totales(self, f, planes):
        w, data, dte = self.stdout.write, f['data'], f['dte']
        completo = not self.opts['solo']
        unidades = sum(p['unidades'] for p in planes)
        neto = sum(int(p['linea'].get('costo') or 0) * p['unidades'] for p in planes)
        por_estado = {}
        for p in planes:
            e = self._estado_visible(p)
            por_estado[e] = por_estado.get(e, 0) + 1
        w(self.style.MIGRATE_HEADING(f'  Resumen factura {dte.numero_documento}: ')
          + ' · '.join(f'{k}: {v}' for k, v in sorted(por_estado.items())))
        w(f'  unidades: {unidades}' + (f' (factura: {data.get("total_unidades")})' if completo else '')
          + f' · neto líneas: ${_fmt(neto)}'
          + (f' (factura: ${_fmt(data.get("total_neto") or 0)} · DTE en sistema: '
             f'${_fmt(dte.monto_neto or 0)})' if completo else ''))
        if completo:
            if data.get('total_unidades') and unidades != int(data['total_unidades']):
                w(self.style.ERROR('  ✗ las unidades no cuadran con la factura'))
            if data.get('total_neto') and neto != int(data['total_neto']):
                w(self.style.ERROR('  ✗ el neto no cuadra con la factura'))
            if dte.monto_neto and abs(Decimal(dte.monto_neto) - neto) >= 1:
                w(self.style.WARNING('  ! el neto no cuadra con el DTE registrado en el sistema'))

    # ------------------------------------------------------------- escritura

    def _preguntar(self, texto, opciones, default):
        try:
            r = input(texto).strip().lower()
        except EOFError:
            return default
        return r if r in opciones else default

    def _aplicar(self, f, planes, user):
        """Carga una factura. Devuelve False si el usuario cancela."""
        from app.views import crear_producto_manual

        w, dte, sucursal = self.stdout.write, f['dte'], f['sucursal']
        sin_preguntar = self.opts['si']
        a_cargar = [p for p in planes if p['estado'] != 'YA_CARGADO']
        if not a_cargar:
            w(self.style.WARNING(f'Factura {dte.numero_documento}: nada que cargar.'))
            return True
        nuevos = sum(1 for p in a_cargar if p['vigentes'] is None)
        existentes = len(a_cargar) - nuevos
        w('')
        if not sin_preguntar and self._preguntar(
                f'Factura {dte.numero_documento}: se crearán {nuevos} producto(s) y se cargará '
                f'stock en {existentes} existente(s) (se pregunta uno a uno si cambian precios). '
                f'¿Continuar? [s/N] ', {'s', 'n'}, 'n') != 's':
            w(self.style.WARNING('Cancelado: esta factura no se cargó.'))
            return False

        motor_sesion = import_module(settings.SESSION_ENGINE)
        factory = RequestFactory()
        ok = fallidas = saltadas = unidades = 0
        w(self.style.MIGRATE_HEADING(f'Aplicando factura {dte.numero_documento}'))
        for plan in planes:
            if plan['estado'] == 'YA_CARGADO':
                w(f'[{plan["n"]:>2}] {plan["articulo"]:<12} saltada (ya cargada contra este DTE)')
                continue

            # Precios que se envían, si se actualiza la ficha y si se alinean
            # las fichas gemelas de otras tiendas (solo con [s]).
            precios, actualizar, sincronizar = plan['factura'], False, False
            if plan['vigentes'] is not None:
                c0, s0, v0 = plan['vigentes']
                c1, _s1, v1 = plan['factura']
                opciones = self._opciones_existente(plan)
                if opciones is None:
                    opcion = 't'  # la factura trae los mismos precios
                elif sin_preguntar:
                    opcion = 's'
                else:
                    gemelas = (' · también en ' + ', '.join(a for a, _c, _v in plan['gemelas'])
                               if plan['gemelas'] else '')
                    textos = {
                        's': f'[s] stock + costo + venta{" (y en esas tiendas, con aviso)" if gemelas else ""}',
                        'c': f'[c] stock + costo (venta sigue {_fmt(v0)})',
                        't': f'[t] solo stock (venta sigue {_fmt(v0)})',
                        'n': '[n] saltar',
                    }
                    baja = '  (la venta BAJA)' if v1 < v0 else ''
                    opcion = self._preguntar(
                        f'[{plan["n"]:>2}] {plan["articulo"]} existe: +{plan["unidades"]} u, '
                        f'costo {_fmt(c0)}→{_fmt(c1)}, venta {_fmt(v0)}→{_fmt(v1)}{baja}{gemelas}.\n'
                        f'     ' + ' · '.join(textos[o] for o in opciones) + ': ',
                        set(opciones), 'n')
                if opcion == 'n':
                    saltadas += 1
                    w(f'[{plan["n"]:>2}] {plan["articulo"]:<12} saltada')
                    continue
                if opcion == 't':
                    precios = plan['vigentes']
                elif opcion == 'c':
                    precios = (c1, s0, v0)
                    actualizar = True
                else:  # 's'
                    actualizar, sincronizar = True, True
                actualizar = actualizar and plan['destino'] is not None

            destino = plan['destino']
            payload = {
                'es_manual': 'true',
                'proveedor': str(dte.emisor_id),
                'dte_manual': str(dte.id),
                'articulo': plan['articulo'],
                # En una ficha existente se conserva su descripción (la vista la
                # pisa cuando actualiza precios).
                'descripcion': destino.descripcion if destino else plan['linea'].get('descripcion', ''),
                'atributo1': str(plan['marca'].id),
                'atributo2': str(plan['color'].id),
                'atributo3': str(plan['genero'].id),
                'categoria': str(plan['categoria'].id),
                'tipo_talla': plan['tipo_talla'],
                'guia_talla': str(plan['guia'].id) if plan['guia'] else '',
                'costo': str(precios[0]),
                'sobreprecio': str(precios[1]),
                'precioventa': str(precios[2]),
                'actualizar_precios': 'true' if actualizar else 'false',
                'sincronizar_otras_bodegas': 'true' if sincronizar else 'false',
                'aplicar_todas_bodegas': 'false',
                'talla[]': [t[1] for t in plan['tallas']],
                'stock[]': [str(t[2]) for t in plan['tallas']],
                'sku[]': ['' for _ in plan['tallas']],
                'especialidad[]': [str(o.id) for o in plan['especialidades']],
            }
            if destino is not None:
                payload['producto_id_destino'] = str(destino.id)
                payload['confirmar_duplicado'] = 'true'  # ya validado en el plan

            request = factory.post('/app/crear_producto_manual/', data=payload)
            request.user = user
            request.session = motor_sesion.SessionStore()
            request.session['idSucursalActual'] = sucursal.id
            request.session['idEmpresaActual'] = sucursal.empresa_id
            request.session['nombreUsuario'] = _RESPONSABLE

            # Todo o nada por línea: la vista no es atómica y, si falla a
            # mitad (o no alcanza a registrar la compra/DTE, cosa que ella
            # misma se traga), quedaría stock sin factura o tallas a medias.
            try:
                with transaction.atomic():
                    respuesta = json.loads(crear_producto_manual(request).content)
                    if not respuesta.get('success'):
                        raise _Revertir(respuesta.get('error') or 'la vista respondió error')
                    if not respuesta.get('compra_id'):
                        raise _Revertir('no se pudo registrar la línea en la compra/DTE')
                    # Un error de BD que la vista capturó sin savepoint deja la
                    # transacción marcada: al salir se desharía en silencio y
                    # aquí se informaría OK.
                    if transaction.get_rollback():
                        raise _Revertir('la vista tuvo un error de base de datos a mitad de camino')
            except _Revertir as exc:
                fallidas += 1
                w(self.style.ERROR(f'[{plan["n"]:>2}] {plan["articulo"]:<12} FALLÓ  {exc} '
                                   f'(se deshizo: no quedó nada de esta línea)'))
                continue
            except Exception as exc:
                fallidas += 1
                w(self.style.ERROR(f'[{plan["n"]:>2}] {plan["articulo"]:<12} FALLÓ  '
                                   f'{type(exc).__name__}: {exc} (se deshizo: no quedó nada de esta línea)'))
                continue

            ok += 1
            cargadas = sum(t.get('stock_ingresado', 0) for t in respuesta.get('tallas_detalle', []))
            unidades += cargadas
            w(self.style.SUCCESS(f'[{plan["n"]:>2}] {plan["articulo"]:<12} OK  ')
              + f'producto #{respuesta.get("producto_id")} · +{cargadas} u · {respuesta.get("mensaje", "")}')
            w('      ' + '  '.join(
                f'{t["talla"]}: sku {t["sku"]} → {t["stock_final"]}'
                for t in respuesta.get('tallas_detalle', [])))

        resumen = f'Factura {dte.numero_documento}: {ok} línea(s) cargadas, {unidades} unidades'
        if saltadas:
            resumen += f' · {saltadas} saltada(s) por ti'
        w((self.style.ERROR if fallidas else self.style.SUCCESS)(
            resumen + (f' · {fallidas} fallida(s): no quedó nada de ellas; corrige y vuelve a '
                       f'correr (las cargadas se saltan solas)' if fallidas else '')))
        return True
