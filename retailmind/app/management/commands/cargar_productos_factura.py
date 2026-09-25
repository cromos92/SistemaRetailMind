"""
Carga los productos de una o varias facturas de proveedor por el MISMO camino
que el modal "Crear Producto Manual" de /app/verGestionProducto/.

Cada factura se transcribe a un JSON (ver compras/facturas/): folio, RUT del
proveedor y una línea por código con su curva de tallas, costo neto unitario y
la clasificación (género, categoría v1.2, especialidades). Cada línea queda
asociada al DTE de SU factura (folio + RUT del emisor).

La lógica vive en app/services/carga_factura/ (planificador + aplicador, con
reglas por marca en perfiles.py); este comando solo arma las opciones, imprime
la vista previa y pregunta en los productos que ya existen.

Reglas:
  - Precio de venta = regla del perfil de la marca (Nike: costo × 1,85 si el
    costo es menor a $40.000, × 1,8 desde $40.000; --umbral-costo /
    --factor-bajo / --factor-alto la pisan), redondeado a ...990 como el
    modal. Sobreprecio de fichas nuevas = % de márgenes de la bodega.
  - Código NUEVO → se crea la ficha con tallas, stock, precios y especialidades.
    Género: el de la factura (W/WMNS = MUJER, M = HOMBRE); si no lo dice, el
    de los otros colores del mismo modelo ya cargados; si no hay, el del JSON.
  - Existe o no = artículo + MARCA, nada más (color/género/categoría de la
    ficha pueden estar mal y sigue siendo el mismo producto). Código que YA
    EXISTE → nunca se crea otra ficha: se usa la existente (con su identidad).
    Las tallas que ya tiene suman stock en su SKU; las que no tiene se agregan
    a esa misma ficha. Si la factura cambia precios, se pregunta por línea:
    [s] stock + costo + venta (también en esa variante de otras tiendas, con
    aviso) · [c] stock + costo, la venta sigue igual · [t] solo stock (solo si
    el costo no cambia: la compra/DTE se registra con el costo que se envía) ·
    [n] saltar. [c] y [t] no tocan otras tiendas.
  - Tallas: como elegir "Tipo Talla" + "Guía" en el modal. Ficha nueva → guía
    según "guias_talla" del JSON o del perfil (INFANTIL si la talla trae C/Y,
    si no la del género) y la talla se escribe tal como está en la columna del
    tipo (US) de la guía; si una talla no está en la guía → ERROR. Ficha
    existente (y sus gemelas en otras bodegas) → pasa al MISMO formato: tipo
    US + guía, y sus tallas se renombran ('7,0'→'7', '700'→'7', '1,0'→'1Y')
    manteniendo el SKU, igual que el lápiz del modal; --sin-renombrar-tallas
    las deja como están.
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
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from app.models import Dte_Productos
from app.services.carga_factura import facturas as svc_facturas
from app.services.carga_factura.aplicador import RESPONSABLE, aplicar_linea
from app.services.carga_factura.facturas import ErrorCarga
from app.services.carga_factura.planificador import (
    PlanificadorCarga, estado_visible, opciones_existente,
)
from app.services.carga_factura.precios import fmt
from app.utils_producto_match import normalizar_articulo


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
        parser.add_argument('--umbral-costo', type=int, default=None,
                            help='Costo desde el que se usa --factor-alto (default: perfil de la marca, 40000)')
        parser.add_argument('--factor-bajo', type=Decimal, default=None,
                            help='Venta = costo × este factor si el costo es menor al umbral '
                                 '(default: perfil de la marca, 1.85)')
        parser.add_argument('--factor-alto', type=Decimal, default=None,
                            help='Venta = costo × este factor desde el umbral (default: perfil de la marca, 1.8)')
        parser.add_argument('--margen-sobreprecio', type=Decimal, default=None,
                            help='%% de sobreprecio de fichas nuevas (default: el de la bodega)')
        parser.add_argument('--si', action='store_true',
                            help='No preguntar: en los existentes aplica [s] (stock + costo + venta)')
        parser.add_argument('--confirmar-duplicados', action='store_true',
                            help='Cargar aunque el código tenga 2+ fichas iguales en la '
                                 'bodega (entra en la más reciente)')
        parser.add_argument('--sin-renombrar-tallas', dest='renombrar_tallas', action='store_false',
                            help='No pasar a US con letra (7 / 7.5 / 11C / 1.5Y) las tallas de las '
                                 'fichas que ya existen; dejarlas como están')
        parser.add_argument('--forzar', action='store_true',
                            help='Cargar aunque el DTE ya tenga ingreso de ese código (en esta u '
                                 'otra bodega). DUPLICA stock: solo si sabes por qué')

    # ---------------------------------------------------------------- motor

    @property
    def motor(self):
        """Planificador con las opciones de esta corrida (se rehace si cambian)."""
        if getattr(self, '_motor', None) is None or self._motor.opts is not self.opts:
            self._motor = PlanificadorCarga(self.opts)
        return self._motor

    def _resolver_usuario(self, username):
        return svc_facturas.resolver_usuario(username)

    def _margen_sobreprecio(self, user, sucursal):
        return svc_facturas.margen_sobreprecio(user, sucursal)

    def _planificar_factura(self, f, vistos):
        return self.motor.planificar_factura(f, vistos)

    # ---------------------------------------------------------------- flujo

    def handle(self, *args, **opts):
        self.opts = opts
        try:
            self._handle(opts)
        except ErrorCarga as exc:
            raise CommandError(str(exc))

    def _handle(self, opts):
        rutas = svc_facturas.archivos_de_patrones(opts['archivos'])
        if opts['dte_id'] and len(rutas) > 1:
            raise CommandError('--dte-id solo sirve con un archivo; para varios pon "dte_id" en cada JSON')

        user = self._resolver_usuario(opts['usuario'])
        facturas = [svc_facturas.leer_factura(r, user, dte_id=opts['dte_id'],
                                              margen=opts['margen_sobreprecio'])
                    for r in rutas]
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

    # ------------------------------------------------------------ reporte

    def _imprimir_factura(self, f, planes, user):
        self._imprimir_cabecera(f, user)
        for plan in planes:
            self._imprimir_plan(plan)
        self._imprimir_totales(f, planes)

    def _imprimir_cabecera(self, f, user):
        w, data, dte, sucursal = self.stdout.write, f['data'], f['dte'], f['sucursal']
        umbral, factor_bajo, factor_alto = self.motor.regla(f)
        w('')
        w(self.style.MIGRATE_HEADING(
            f'══ {dte.tipo_documento} N° {dte.numero_documento} · {dte.emisor.nombre} '
            f'({dte.emisor.rut}) · DTE id={dte.id} · emitida {dte.fecha_emision} · {f["ruta"].name}'))
        if 'FACTURA' not in str(dte.tipo_documento or '').upper() or getattr(dte, 'es_nota_credito', False):
            w(self.style.WARNING(f'  ! el DTE no es una factura ({dte.tipo_documento}); se usó por "dte_id"'))
        w(f'Bodega: {sucursal.alias} · usuario: {user.username} (responsable "{RESPONSABLE}") '
          f'· venta = costo × {factor_bajo} (< ${fmt(umbral)}) / × {factor_alto} '
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

    def _imprimir_plan(self, plan):
        w = self.stdout.write
        colores = {'NUEVO': self.style.SUCCESS, 'EXISTE': self.style.HTTP_INFO,
                   'EXISTE_OTRAS': self.style.HTTP_INFO, 'YA_CARGADO': self.style.WARNING,
                   'DUPLICADAS': self.style.ERROR, 'ERROR': self.style.ERROR}
        estado = estado_visible(plan)
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
            opciones = opciones_existente(plan)
            sentido = ('  BAJA la venta' if pv < v0 else '  sube la venta' if pv > v0 else '')
            w(f'      vigente ({donde}): costo {fmt(c0)} · venta {fmt(v0)}'
              f'  →  factura: costo {fmt(costo)} · venta {fmt(pv)}'
              + f' [{plan["fuente_pv"]}]'
              + ((sentido + f'  · te preguntará [{"/".join(opciones)}]') if opciones
                 else '  (sin cambios: solo suma stock)'))
            if plan['gemelas']:
                w('      mismo código en: '
                  + ', '.join(f'{a} (costo {fmt(c)}, venta {fmt(v)})'
                              + ('' if misma else ' [otra identidad: [s] no la toca]')
                              for a, c, v, misma in plan['gemelas'])
                  + ' — solo [s] les aplica el precio de la factura y avisa a esas tiendas')
        else:
            w(f'      costo {fmt(costo)} · sobreprecio {fmt(sobre)} · venta {fmt(pv)}'
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
        if plan.get('fichas_formato'):
            por_alias = {}
            for _pid, alias, _pt, viejo, nuevo in plan['renombres']:
                por_alias.setdefault(alias, []).append(f'{viejo}→{nuevo}')
            fichas_txt = ', '.join(f'{f.sucursal.alias} #{f.id}' for f in plan['fichas_formato'])
            w(self.style.WARNING(
                f'      ficha(s) {fichas_txt} pasan a tipo US con guía «{plan["guia"].nombre}»'))
            for alias, cambios in por_alias.items():
                w(self.style.WARNING(f'        {alias}: renombra ' + '  '.join(cambios)))
            if not por_alias:
                w('        (las tallas ya estaban en ese formato)')
            for alias, viejo, nuevo, stock in plan['conflictos']:
                w(self.style.WARNING(f'        ! {alias}: «{viejo}» no se renombra a «{nuevo}» porque esa '
                                     f'talla ya existe en la ficha (fila duplicada, stock {stock}); fusionar aparte'))
            for alias, t in plan['sin_resolver']:
                w(self.style.WARNING(f'        ! {alias}: «{t}» no calza con la guía (¿{t}C?): queda igual'))
        for a in plan['avisos']:
            w(self.style.WARNING(f'      ! {a}'))
        for e in plan['errores']:
            w(self.style.ERROR(f'      ✗ {e}'))

    def _imprimir_totales(self, f, planes):
        w, data, dte = self.stdout.write, f['data'], f['dte']
        completo = not self.opts.get('solo')
        unidades = sum(p['unidades'] for p in planes)
        neto = sum(int(p['linea'].get('costo') or 0) * p['unidades'] for p in planes)
        por_estado = {}
        for p in planes:
            e = estado_visible(p)
            por_estado[e] = por_estado.get(e, 0) + 1
        w(self.style.MIGRATE_HEADING(f'  Resumen factura {dte.numero_documento}: ')
          + ' · '.join(f'{k}: {v}' for k, v in sorted(por_estado.items())))
        w(f'  unidades: {unidades}' + (f' (factura: {data.get("total_unidades")})' if completo else '')
          + f' · neto líneas: ${fmt(neto)}'
          + (f' (factura: ${fmt(data.get("total_neto") or 0)} · DTE en sistema: '
             f'${fmt(dte.monto_neto or 0)})' if completo else ''))
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

    def _elegir_opcion(self, plan):
        """Opción para un código existente ('s', 'c', 't' o 'n'), preguntando si hace falta."""
        c0, _s0, v0 = plan['vigentes']
        c1, _s1, v1 = plan['factura']
        opciones = opciones_existente(plan)
        if opciones is None:
            return 't'  # la factura trae los mismos precios
        if self.opts['si']:
            return 's'
        gemelas = (' · también en ' + ', '.join(a for a, _c, _v, misma in plan['gemelas'] if misma)
                   if plan['gemelas'] else '')
        textos = {
            's': f'[s] stock + costo + venta{" (y en esas tiendas, con aviso)" if gemelas else ""}',
            'c': f'[c] stock + costo (venta sigue {fmt(v0)})',
            't': f'[t] solo stock (venta sigue {fmt(v0)})',
            'n': '[n] saltar',
        }
        baja = '  (la venta BAJA)' if v1 < v0 else ''
        return self._preguntar(
            f'[{plan["n"]:>2}] {plan["articulo"]} existe: +{plan["unidades"]} u, '
            f'costo {fmt(c0)}→{fmt(c1)}, venta {fmt(v0)}→{fmt(v1)}{baja}{gemelas}.\n'
            f'     ' + ' · '.join(textos[o] for o in opciones) + ': ',
            set(opciones), 'n')

    def _aplicar(self, f, planes, user):
        """Carga una factura. Devuelve False si el usuario cancela."""
        w, dte = self.stdout.write, f['dte']
        a_cargar = [p for p in planes if p['estado'] != 'YA_CARGADO']
        if not a_cargar:
            w(self.style.WARNING(f'Factura {dte.numero_documento}: nada que cargar.'))
            return True
        nuevos = sum(1 for p in a_cargar if p['vigentes'] is None)
        existentes = len(a_cargar) - nuevos
        w('')
        if not self.opts['si'] and self._preguntar(
                f'Factura {dte.numero_documento}: se crearán {nuevos} producto(s) y se cargará '
                f'stock en {existentes} existente(s) (se pregunta uno a uno si cambian precios). '
                f'¿Continuar? [s/N] ', {'s', 'n'}, 'n') != 's':
            w(self.style.WARNING('Cancelado: esta factura no se cargó.'))
            return False

        ok = fallidas = saltadas = unidades = 0
        w(self.style.MIGRATE_HEADING(f'Aplicando factura {dte.numero_documento}'))
        for plan in planes:
            if plan['estado'] == 'YA_CARGADO':
                w(f'[{plan["n"]:>2}] {plan["articulo"]:<12} saltada (ya cargada contra este DTE)')
                continue
            opcion = 's'
            if plan['vigentes'] is not None:
                opcion = self._elegir_opcion(plan)
                if opcion == 'n':
                    saltadas += 1
                    w(f'[{plan["n"]:>2}] {plan["articulo"]:<12} saltada')
                    continue

            resultado = aplicar_linea(plan, f, user, opcion)
            if not resultado['ok']:
                fallidas += 1
                w(self.style.ERROR(f'[{plan["n"]:>2}] {plan["articulo"]:<12} FALLÓ  {resultado["error"]} '
                                   f'(se deshizo: no quedó nada de esta línea)'))
                continue

            respuesta = resultado['respuesta']
            ok += 1
            cargadas = sum(t.get('stock_ingresado', 0) for t in respuesta.get('tallas_detalle', []))
            unidades += cargadas
            w(self.style.SUCCESS(f'[{plan["n"]:>2}] {plan["articulo"]:<12} OK  ')
              + f'producto #{respuesta.get("producto_id")} · +{cargadas} u · {respuesta.get("mensaje", "")}'
              + (f' · {len(plan["renombres"])} talla(s) renombradas a US' if plan.get('renombres') else ''))
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
