"""
Unifica AtributoOpcion duplicadas: dos filas que son la MISMA marca escrita
distinto ('CHALADA' vs 'CHALADA .', 'ADIDAS' vs 'ADIDAS ', 'champion' vs
'Champion'). Deja una canónica y repunta todas las FK a ella.

Contexto: el catálogo se cargó desde varias fuentes (migración Laravel, creación
manual, sync de precios) y quedaron opciones gemelas. El efecto práctico es que
los dashboards, las predicciones y los filtros por marca parten la misma marca
en dos, y la guía de tallas de una no aplica a la otra.

Seguro por diseño:
  - DRY-RUN por defecto: sin --apply NO escribe nada.
  - Solo agrupa opciones que cuelgan del MISMO Productos_Atributos padre. Nunca
    fusiona 'AMBAR' (Marca) con 'AMBAR' (Color): son atributos distintos.
  - La canónica es la que más productos tiene (desempate: id más bajo). Se puede
    forzar otra con --canonica.
  - Repunta las FK por INTROSPECCIÓN (`_meta.related_objects`), no por lista
    hardcodeada, para que no se escape ninguna relación futura.
  - Respeta los unique_together que incluyen la FK. VelocidadHistorica y
    CurvaTalles tienen uno sobre `marca`: repuntar a ciegas reventaría con
    IntegrityError. Las filas de la perdedora que chocarían con una fila ya
    existente de la canónica se BORRAN (son benchmarks derivados que
    `calcular_predicciones` regenera), y se reportan aparte.
  - Deduplica ProductoAtributoValor después de repuntar (repuntar puede dejar
    dos filas idénticas producto+atributo+opcion).
  - Todo dentro de una transacción: o se aplica el grupo completo o nada.

Uso:
    python manage.py unificar_marcas_duplicadas                  # dry-run, atributo Marca
    python manage.py unificar_marcas_duplicadas --atributo Color # dry-run, otro atributo
    python manage.py unificar_marcas_duplicadas --todos          # dry-run, todos los atributos
    python manage.py unificar_marcas_duplicadas --solo CHALADA   # dry-run de un grupo
    python manage.py unificar_marcas_duplicadas --apply          # ESCRIBE
"""
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from app.models import AtributoOpcion, Producto


def clave(valor):
    """Canon de comparación: mayúsculas, solo alfanuméricos."""
    return re.sub(r'[^A-Z0-9]', '', (valor or '').upper())


class Command(BaseCommand):
    help = ('Unifica opciones de atributo duplicadas (misma marca escrita distinto). '
            'Dry-run por defecto; --apply para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Escribe los cambios (sin esto solo reporta)')
        parser.add_argument('--atributo', default='Marca',
                            help="Nombre del Productos_Atributos padre (default: Marca)")
        parser.add_argument('--todos', action='store_true',
                            help='Procesa todos los atributos, no solo --atributo')
        parser.add_argument('--solo', default=None,
                            help='Procesa solo este grupo (valor normalizado, ej. CHALADA)')
        parser.add_argument('--canonica', type=int, default=None,
                            help='Fuerza el id de AtributoOpcion que queda como canónica '
                                 '(solo válido junto con --solo)')
        parser.add_argument('--permitir-duplicados', action='store_true',
                            dest='permitir_duplicados',
                            help='Fusiona aunque queden fichas de Producto con identidad '
                                 'duplicada (por defecto ese grupo se OMITE)')

    # ------------------------------------------------------------------ utils
    def _relaciones(self):
        """[(modelo, nombre_campo)] de todo lo que apunta a AtributoOpcion."""
        rels = []
        for rel in AtributoOpcion._meta.related_objects:
            if rel.many_to_many:
                continue
            rels.append((rel.related_model, rel.field.name))
        return rels

    def _detectar_grupos(self, opts):
        qs = AtributoOpcion.objects.select_related('atributo')
        if not opts['todos']:
            qs = qs.filter(atributo__nombre__iexact=opts['atributo'])

        # agrupar por (atributo_padre, clave normalizada)
        grupos = defaultdict(list)
        for o in qs:
            k = clave(o.valor)
            if not k:
                continue
            grupos[(o.atributo_id, k)].append(o)

        # conteo de productos por opcion (las 4 posiciones de atributo)
        uso = defaultdict(int)
        for campo in ('atributo1', 'atributo2', 'atributo3', 'atributo4'):
            for r in (Producto.objects.values(campo)
                      .annotate(n=Count('id')).exclude(**{campo: None})):
                uso[r[campo]] += r['n']

        salida = []
        for (atr_id, k), ops in sorted(grupos.items(), key=lambda x: x[0][1]):
            if len(ops) < 2:
                continue
            if opts['solo'] and k != clave(opts['solo']):
                continue
            ops.sort(key=lambda o: (-uso[o.id], o.id))
            salida.append((k, ops, uso))
        return salida

    def _conteos_fk(self, opcion, relaciones):
        """{'app.Modelo.campo': n} de filas que apuntan a esta opcion."""
        out = {}
        for modelo, campo in relaciones:
            n = modelo.objects.filter(**{campo: opcion}).count()
            if n:
                out['%s.%s' % (modelo.__name__, campo)] = n
        return out

    def _specs_unicos(self, modelo, campo):
        """unique_together / UniqueConstraint que incluyen `campo`.

        Devuelve la lista de los OTROS campos de cada spec: si esos campos
        coinciden entre una fila de la perdedora y una de la canónica, repuntar
        provocaría IntegrityError.
        """
        specs = []
        for t in (modelo._meta.unique_together or []):
            if campo in t:
                specs.append([f for f in t if f != campo])
        for c in (modelo._meta.constraints or []):
            campos = list(getattr(c, 'fields', []) or [])
            if campo in campos and not getattr(c, 'condition', None):
                specs.append([f for f in campos if f != campo])
        return specs

    def _colisiones_identidad(self, perdedora, canonica):
        """Fichas de Producto que quedarían con la MISMA identidad tras fusionar.

        La BD no lo impide (Producto no tiene unique_together), pero la identidad
        a nivel aplicación —articulo normalizado + atributo1..3 + categoría +
        sucursal, ver app/utils_producto_match.py— sí la usan la recepción y el
        sync de precios. Dos fichas con la misma identidad parten el stock y
        hacen que `buscar_producto_por_identidad` devuelva la más reciente, que
        tras la fusión puede no ser la que se venía usando.

        Devuelve [(articulo, sucursal_id, [ids])].
        """
        from app.utils_producto_match import normalizar_articulo

        campos = ('atributo1_id', 'atributo2_id', 'atributo3_id')
        afectados = list(Producto.objects.filter(
            Q(atributo1=perdedora) | Q(atributo2=perdedora) | Q(atributo3=perdedora)
        ).values('id', 'articulo', 'atributo1_id', 'atributo2_id', 'atributo3_id',
                 'categoria_id', 'sucursal_id'))
        if not afectados:
            return []

        # identidad que tendría cada ficha DESPUÉS de repuntar
        agrupadas = defaultdict(list)
        for p in afectados:
            nuevos = tuple(canonica.id if p[c] == perdedora.id else p[c] for c in campos)
            agrupadas[(normalizar_articulo(p['articulo']), nuevos,
                       p['categoria_id'], p['sucursal_id'])].append(p['id'])

        colisiones = []
        for (art, (a1, a2, a3), cat, suc), ids in agrupadas.items():
            otras = [q.id for q in Producto.objects.filter(
                atributo1_id=a1, atributo2_id=a2, atributo3_id=a3,
                categoria_id=cat, sucursal_id=suc).exclude(id__in=ids)
                .only('id', 'articulo')
                if normalizar_articulo(q.articulo) == art]
            if otras or len(ids) > 1:
                colisiones.append((art, suc, sorted(set(ids) | set(otras))))
        return colisiones

    def _repuntar(self, modelo, campo, perdedora, canonica, aplicar):
        """Mueve las FK de `perdedora` a `canonica`. -> (movidas, borradas)."""
        qs = modelo.objects.filter(**{campo: perdedora})
        specs = self._specs_unicos(modelo, campo)
        if not specs:
            n = qs.count()
            if aplicar and n:
                qs.update(**{campo: canonica})
            return n, 0

        # tuplas ya ocupadas por la canónica, por spec
        ocupadas = []
        for otros in specs:
            ocupadas.append({tuple(r[f] for f in otros)
                             for r in modelo.objects.filter(**{campo: canonica})
                             .values(*otros)})

        necesarios = sorted({f for s in specs for f in s} | {'id'})
        mover, colisionan = [], []
        for row in qs.values(*necesarios).order_by('id'):
            choca = any(tuple(row[f] for f in otros) in ocupadas[i]
                        for i, otros in enumerate(specs))
            if choca:
                colisionan.append(row['id'])
            else:
                mover.append(row['id'])
                # reservar, para que dos perdedoras no choquen entre sí
                for i, otros in enumerate(specs):
                    ocupadas[i].add(tuple(row[f] for f in otros))

        if aplicar:
            if colisionan:
                modelo.objects.filter(id__in=colisionan).delete()
            if mover:
                modelo.objects.filter(id__in=mover).update(**{campo: canonica})
        return len(mover), len(colisionan)

    # ------------------------------------------------------------------ main
    def handle(self, *args, **opts):
        aplicar = opts['apply']
        if opts['canonica'] and not opts['solo']:
            self.stderr.write(self.style.ERROR(
                '--canonica requiere --solo (para no ambiguar el grupo).'))
            return

        relaciones = self._relaciones()
        self.stdout.write('Relaciones que apuntan a AtributoOpcion (%d):' % len(relaciones))
        for modelo, campo in relaciones:
            self.stdout.write('   - %s.%s' % (modelo.__name__, campo))

        grupos = self._detectar_grupos(opts)
        if not grupos:
            self.stdout.write(self.style.WARNING('\nNo hay duplicados que unificar.'))
            return

        self.stdout.write('\n%s' % ('=' * 78))
        self.stdout.write('GRUPOS DUPLICADOS: %d   (modo: %s)'
                          % (len(grupos), 'APLICAR' if aplicar else 'DRY-RUN'))
        self.stdout.write('=' * 78)

        tot_mov = 0
        tot_borradas = 0
        tot_colision = 0
        tot_omitidos = 0
        for k, ops, uso in grupos:
            canonica = ops[0]
            if opts['canonica']:
                forzada = [o for o in ops if o.id == opts['canonica']]
                if not forzada:
                    self.stderr.write(self.style.ERROR(
                        'El id %s no pertenece al grupo %s.' % (opts['canonica'], k)))
                    continue
                canonica = forzada[0]
            perdedoras = [o for o in ops if o.id != canonica.id]

            self.stdout.write('\n[%s]  atributo=%s' % (k, canonica.atributo.nombre))
            self.stdout.write('   CANONICA  id=%-5s %-18r %d productos'
                              % (canonica.id, canonica.valor, uso[canonica.id]))
            colisiones_identidad = []
            for p in perdedoras:
                fks = self._conteos_fk(p, relaciones)
                detalle = ', '.join('%s=%d' % (a, b) for a, b in sorted(fks.items())) or 'sin referencias'
                self.stdout.write('   absorbe   id=%-5s %-18r %d productos  [%s]'
                                  % (p.id, p.valor, uso[p.id], detalle))
                choques = []
                for modelo, campo in relaciones:
                    mov, col = self._repuntar(modelo, campo, p, canonica, False)
                    tot_mov += mov
                    tot_colision += col
                    if col:
                        choques.append('%s.%s=%d' % (modelo.__name__, campo, col))
                if choques:
                    self.stdout.write(self.style.WARNING(
                        '             chocan por unique_together y se BORRAN: %s'
                        % ', '.join(choques)))
                ident = self._colisiones_identidad(p, canonica)
                if ident:
                    colisiones_identidad.append((canonica, p, ident))
                    self.stdout.write(self.style.ERROR(
                        '             !! %d fichas de Producto quedarian con IDENTIDAD '
                        'DUPLICADA:' % sum(len(x[2]) for x in ident)))
                    for art, suc, ids in ident[:6]:
                        self.stdout.write(self.style.ERROR(
                            '                articulo=%-22s sucursal=%-3s ids=%s'
                            % (art[:22], suc, ids)))
                    if len(ident) > 6:
                        self.stdout.write(self.style.ERROR(
                            '                ... y %d grupos mas' % (len(ident) - 6)))
                tot_borradas += 1

            if colisiones_identidad and not opts['permitir_duplicados']:
                self.stdout.write(self.style.ERROR(
                    '   GRUPO OMITIDO: fusionar partiria el stock entre fichas gemelas. '
                    'Resuelvelas en /app/existencias/fusion-duplicados/ o repite con '
                    '--permitir-duplicados si asumes el riesgo.'))
                for _c, _p, _ident in colisiones_identidad:
                    tot_mov -= sum(self._conteos_fk(_p, relaciones).values())
                    tot_borradas -= 1
                    tot_omitidos += 1
                colisiones_identidad = []
                continue

            if not aplicar:
                continue

            with transaction.atomic():
                for perdedora in perdedoras:
                    for modelo, campo in relaciones:
                        self._repuntar(modelo, campo, perdedora, canonica, True)
                    # RED DE SEGURIDAD: Producto.atributo1..4 es on_delete=CASCADE.
                    # Si quedara una sola referencia viva, borrar la opción
                    # arrastraría productos. Abortamos la transacción entera.
                    restantes = self._conteos_fk(perdedora, relaciones)
                    if restantes:
                        raise RuntimeError(
                            'ABORTADO: la opción id=%s todavía tiene referencias '
                            'tras repuntar (%s). No se borra nada.'
                            % (perdedora.id, restantes))
                    perdedora.delete()

                # dedupe de ProductoAtributoValor (repuntar pudo dejar gemelas)
                from app.models import ProductoAtributoValor
                vistos = set()
                borrar = []
                for pav in (ProductoAtributoValor.objects
                            .filter(opcion=canonica)
                            .values('id', 'producto_id', 'atributo_id', 'opcion_id')
                            .order_by('id')):
                    ident = (pav['producto_id'], pav['atributo_id'], pav['opcion_id'])
                    if ident in vistos:
                        borrar.append(pav['id'])
                    else:
                        vistos.add(ident)
                if borrar:
                    ProductoAtributoValor.objects.filter(id__in=borrar).delete()
                    self.stdout.write('   dedupe    %d ProductoAtributoValor gemelas borradas'
                                      % len(borrar))
            self.stdout.write(self.style.SUCCESS('   -> unificado en id=%s' % canonica.id))

        self.stdout.write('\n%s' % ('=' * 78))
        extra = (' %d filas derivadas chocaban por unique_together y se borran '
                 '(las regenera calcular_predicciones).' % tot_colision) if tot_colision else ''
        if tot_omitidos:
            extra += (' %d opciones NO se tocaron: dejarian fichas de Producto con '
                      'identidad duplicada.' % tot_omitidos)
        if aplicar:
            self.stdout.write(self.style.SUCCESS(
                'LISTO: %d referencias repuntadas, %d opciones duplicadas eliminadas.%s'
                % (tot_mov, tot_borradas, extra)))
        else:
            self.stdout.write(self.style.WARNING(
                'DRY-RUN: se repuntarian %d referencias y se borrarian %d opciones.%s '
                'Ejecuta con --apply para escribir.' % (tot_mov, tot_borradas, extra)))
