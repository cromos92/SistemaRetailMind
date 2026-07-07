"""
diagnosticar_productos_duplicados — Detecta fichas de `Producto` DUPLICADAS:
dos o más productos que son el MISMO ítem (misma identidad de negocio) dentro
de una misma sucursal, y que existen por separado por un tipeo distinto del
código (mayúsculas, espacios, `\\xa0`, acentos) o por atributos equivalentes.

Identidad de un Producto (definición del negocio):
    articulo(NORMALIZADO) + marca + color + género + categoría, por sucursal.
    (atributo4 NO entra en la identidad.)

Es el COMPLEMENTO del fix de creación (que ya evita generar NUEVOS duplicados,
ver app/utils_producto_match.py): este comando encuentra los que YA existen en
producción para que puedas fusionarlos por el módulo web
"Existencias → Fusionar Duplicados" (transfiere stock talla a talla vía el
servicio de inventario, con kardex y lotes, de forma auditable).

Modo por defecto = SOLO LECTURA (resumen en consola + Excel). No modifica nada.
NO tiene `--aplicar`: la fusión se hace desde la UI (más segura y con etiquetas).

Uso:
    python manage.py diagnosticar_productos_duplicados
    python manage.py diagnosticar_productos_duplicados --sucursal 5 --top 800
    python manage.py diagnosticar_productos_duplicados --solo-tipeo   # solo colisiones por código distinto (las accionables)
    python manage.py diagnosticar_productos_duplicados --sin-excel
"""
import unicodedata

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from app.models import Producto
from app.utils_producto_match import normalizar_articulo


def _tiene_invisibles(texto):
    """True si el texto trae espacios no separables / de control (\\xa0, etc.)."""
    s = texto or ''
    for ch in s:
        if ch == '\xa0':
            return True
        if unicodedata.category(ch) in ('Cc', 'Cf', 'Zl', 'Zp'):
            return True
        # Espacios unicode distintos del ASCII simple
        if ch != ' ' and unicodedata.category(ch) == 'Zs':
            return True
    return False


class Command(BaseCommand):
    help = ('Detecta fichas de Producto duplicadas (misma identidad, distinto '
            'tipeo de código o atributos equivalentes) para fusionarlas por la UI.')

    def add_arguments(self, parser):
        parser.add_argument('--sucursal', type=int, default=None,
                            help='Limitar a una sucursal (id). Por defecto, todas.')
        parser.add_argument('--top', type=int, default=500,
                            help='Nº de grupos a detallar en el Excel (default 500).')
        parser.add_argument('--excel', default='productos_duplicados.xlsx',
                            help='Ruta del Excel de salida.')
        parser.add_argument('--sin-excel', action='store_true',
                            help='Solo resumen en consola, sin Excel.')
        parser.add_argument('--solo-tipeo', action='store_true',
                            help='Reportar SOLO los grupos donde el código crudo DIFIERE '
                                 '(colisiones por tipeo/\\xa0/acentos = las accionables).')
        parser.add_argument('--incluir-excluidos', action='store_true',
                            help='Incluir productos con excluir_de_analitica=True '
                                 '(por defecto se omiten: suelen ser el lado ya vaciado).')

    # -------------------------------------------------------------- handle
    def handle(self, *args, **opts):
        qs = (Producto.objects
              .select_related('atributo1', 'atributo2', 'atributo3', 'categoria', 'sucursal')
              .annotate(n_tallas=Count('producto_talla', distinct=True),
                        stock_total=Sum('producto_talla__stock')))
        if opts['sucursal']:
            qs = qs.filter(sucursal_id=opts['sucursal'])
        if not opts['incluir_excluidos']:
            qs = qs.filter(excluir_de_analitica=False)

        # Agrupar por identidad de negocio. Usamos los VALORES de atributo en
        # mayúsculas (no los IDs) para cazar también atributos equivalentes con
        # distinto AtributoOpcion (dato sucio conocido).
        grupos = {}
        total_productos = 0
        for p in qs.iterator():
            total_productos += 1
            key = (
                p.sucursal_id,
                normalizar_articulo(p.articulo),
                (p.atributo1.valor.upper().strip() if p.atributo1 and p.atributo1.valor else ''),
                (p.atributo2.valor.upper().strip() if p.atributo2 and p.atributo2.valor else ''),
                (p.atributo3.valor.upper().strip() if p.atributo3 and p.atributo3.valor else ''),
                p.categoria_id,
            )
            grupos.setdefault(key, []).append(p)

        # Quedarse con los grupos de 2+ fichas
        dups = {k: v for k, v in grupos.items() if len(v) > 1}

        # Clasificar cada grupo: ¿la colisión es por tipeo (códigos crudos
        # distintos) o son idénticos al byte? Y ¿hay invisibles \xa0?
        grupos_tipeo = 0
        grupos_invisibles = 0
        fichas_involucradas = 0
        detalle = []
        for key, prods in dups.items():
            fichas_involucradas += len(prods)
            crudos = {pr.articulo for pr in prods}
            por_tipeo = len(crudos) > 1
            con_invisible = any(_tiene_invisibles(pr.articulo) for pr in prods)
            if por_tipeo:
                grupos_tipeo += 1
            if con_invisible:
                grupos_invisibles += 1
            if opts['solo_tipeo'] and not por_tipeo:
                continue
            # score para ordenar: primero los accionables (tipeo/invisibles) y con más stock
            stock_grupo = sum(int(pr.stock_total or 0) for pr in prods)
            detalle.append({
                'key': key, 'prods': prods, 'por_tipeo': por_tipeo,
                'con_invisible': con_invisible, 'stock_grupo': stock_grupo,
            })

        detalle.sort(key=lambda g: (-int(g['por_tipeo']), -int(g['con_invisible']),
                                    -g['stock_grupo'], -len(g['prods'])))

        # ------------------------------------------------------- resumen
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== DIAGNÓSTICO DE PRODUCTOS DUPLICADOS ==='))
        self.stdout.write(f'Productos analizados               : {total_productos:,}')
        self.stdout.write(f'Grupos duplicados (misma identidad): {len(dups):,}')
        self.stdout.write(f'  · fichas involucradas            : {fichas_involucradas:,}')
        self.stdout.write(f'  · fichas a fusionar (todas −1/gr): {fichas_involucradas - len(dups):,}')
        self.stdout.write(self.style.WARNING(
            f'  · grupos por TIPEO de código     : {grupos_tipeo:,}  (accionables: mismo ítem, código distinto)'))
        self.stdout.write(
            f'  · grupos con espacio invisible   : {grupos_invisibles:,}  (\\xa0 u otros)')

        if not dups:
            self.stdout.write(self.style.SUCCESS('\nNo hay productos duplicados con ese criterio. 🎉'))
            return

        # Top en consola
        self.stdout.write('\nTop grupos (accionables primero):')
        for g in detalle[:15]:
            suc = g['prods'][0].sucursal.alias if g['prods'][0].sucursal else '—'
            _, art, marca, color, gen, _cat = g['key']
            flags = ('TIPEO ' if g['por_tipeo'] else '') + ('␣INVIS' if g['con_invisible'] else '')
            self.stdout.write(
                f"   [{suc}] {art:<20} {marca}/{color}/{gen}  "
                f"fichas={len(g['prods'])} stock={g['stock_grupo']}  {flags}")

        self.stdout.write(self.style.NOTICE(
            '\n➡  Fusiónalos desde la web: Existencias → «Fusionar Duplicados» '
            '(conserva 1 ficha y transfiere el stock talla a talla, con kardex y etiquetas).'))

        if not opts['sin_excel']:
            self._exportar_excel(detalle[:opts['top']], opts['excel'])

    # -------------------------------------------------------------- excel
    def _exportar_excel(self, detalle, ruta):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.stdout.write(self.style.ERROR('openpyxl no disponible; omito Excel.'))
            return

        wb = Workbook()
        ws = wb.active
        ws.title = 'Productos duplicados'
        ws.append(['Grupo', 'Sucursal', 'Articulo_normalizado', 'Marca', 'Color', 'Genero',
                   'Categoria', 'Producto_id', 'Articulo_crudo', 'Repr_crudo',
                   'Tiene_invisible', 'Por_tipeo', 'N_tallas', 'Stock', 'PrecioVenta',
                   'Excluido_analitica'])
        for i, g in enumerate(detalle, start=1):
            _, art, marca, color, gen, _cat = g['key']
            cat_nombre = g['prods'][0].categoria.nombre if g['prods'][0].categoria else ''
            # sugerir como "dueño" el de más stock (el que conviene conservar en A)
            prods_ord = sorted(g['prods'], key=lambda pr: (-int(pr.stock_total or 0),
                                                           -int(pr.n_tallas or 0), pr.id))
            for pr in prods_ord:
                ws.append([
                    i,
                    pr.sucursal.alias if pr.sucursal else '',
                    art, marca, color, gen, cat_nombre,
                    pr.id,
                    pr.articulo,
                    repr(pr.articulo),  # deja ver el \xa0 y espacios
                    'SI' if _tiene_invisibles(pr.articulo) else '',
                    'SI' if g['por_tipeo'] else '',
                    int(pr.n_tallas or 0),
                    int(pr.stock_total or 0),
                    pr.precioventa,
                    'SI' if pr.excluir_de_analitica else '',
                ])
        wb.save(ruta)
        self.stdout.write(self.style.SUCCESS(
            f'\nExcel generado: {ruta}  ({len(detalle)} grupos; una fila por ficha, '
            f'ordenadas por stock — la primera de cada grupo es la sugerida a conservar).'))
