"""
Diagnóstico SOLO LECTURA de la edición de productos (verGestionProducto).

No escribe absolutamente nada en la base de datos.

    python manage.py diagnosticar_edicion_productos
    python manage.py diagnosticar_edicion_productos --limite 30

Responde tres preguntas antes/después de tocar la edición de productos:

  A) PERMISOS — qué roles pueden ENTRAR a Gestión de Productos pero no tienen
     `puede_editar`. Desde el fix, esos roles reciben 403 al guardar (antes
     cualquier usuario logueado podía reescribir el catálogo completo).

  B) CLAVE DE PROPAGACIÓN — cuántos códigos de artículo existen en más de una
     VARIANTE (mismo código, distinta marca/color/género/categoría). Cada uno
     de esos códigos era un caso donde la propagación vieja —que filtraba solo
     por `articulo`— reescribía atributos y precios de productos DISTINTOS.

  C) EDICIONES CON MERCADERÍA EN TRÁNSITO — fichas modificadas DESPUÉS de que
     se emitiera un traspaso que todavía no se recepciona. Son los casos en que
     la guía impresa y la pantalla de /app/recepcion-dte/ dicen cosas distintas.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count

from app.models import Movimientos_Producto, OpcionMenu, PermisoRol, Producto

CODIGO_OPCION = 'gestion_producto'
ESTADOS_DTE_MUERTOS = ['CANCELADO', 'ANULADO']


class Command(BaseCommand):
    help = 'Diagnostica (solo lectura) permisos, clave de propagacion y ediciones en transito de Gestion de Productos.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=15,
                            help='Cuantos ejemplos listar por seccion (default: 15)')

    def handle(self, *args, **opts):
        limite = opts['limite']
        self._seccion_permisos()
        self._seccion_clave_propagacion(limite)
        self._seccion_transito(limite)

    # ── A) PERMISOS ──────────────────────────────────────────────────────
    def _seccion_permisos(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n== A) Permisos de edicion sobre "%s" ==' % CODIGO_OPCION))

        opcion = OpcionMenu.objects.filter(codigo=CODIGO_OPCION, activo=True).first()
        if not opcion:
            self.stdout.write(self.style.ERROR(
                'No existe OpcionMenu activa con codigo="%s". PermisoRol.tiene_permiso '
                'devuelve False para TODOS: nadie podria editar. Corre '
                '"python manage.py inicializar_permisos" antes de desplegar.' % CODIGO_OPCION))
            return

        filas = PermisoRol.objects.filter(opcion_menu=opcion)
        if not filas.exists():
            self.stdout.write(self.style.ERROR(
                'La opcion existe pero NINGUN rol tiene fila de permiso: '
                'todos recibirian 403 al guardar.'))
            return

        for fila in filas.order_by('rol'):
            if fila.puede_editar:
                self.stdout.write(self.style.SUCCESS(
                    '  OK       rol=%-20s puede_ver=%s puede_editar=True'
                    % (fila.rol, fila.puede_ver)))
            elif fila.puede_ver:
                self.stdout.write(self.style.WARNING(
                    '  BLOQUEA  rol=%-20s puede_ver=True pero puede_editar=False '
                    '-> entra a la pantalla y recibe 403 al guardar' % fila.rol))
            else:
                self.stdout.write(
                    '  (sin acceso) rol=%-20s puede_ver=False' % fila.rol)

    # ── B) CLAVE DE PROPAGACIÓN ──────────────────────────────────────────
    def _seccion_clave_propagacion(self, limite):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n== B) Codigos con mas de una VARIANTE (alcance de la clave vieja) =='))

        # Identidad = codigo + marca + color + genero + categoria. Se agrupa por
        # codigo y se cuenta cuantas identidades distintas conviven bajo el.
        variantes_por_codigo = defaultdict(set)
        fichas_por_codigo = defaultdict(int)
        empresas_por_codigo = defaultdict(set)

        qs = (
            Producto.objects
            .values('articulo', 'atributo1_id', 'atributo2_id', 'atributo3_id',
                    'categoria_id', 'sucursal__empresa_id')
            .annotate(n=Count('id'))
        )
        for row in qs.iterator(chunk_size=2000):
            codigo = (row['articulo'] or '').strip().upper()
            if not codigo:
                continue
            identidad = (row['atributo1_id'], row['atributo2_id'],
                         row['atributo3_id'], row['categoria_id'])
            variantes_por_codigo[codigo].add(identidad)
            fichas_por_codigo[codigo] += row['n']
            if row['sucursal__empresa_id']:
                empresas_por_codigo[codigo].add(row['sucursal__empresa_id'])

        conflictivos = {c: v for c, v in variantes_por_codigo.items() if len(v) > 1}
        total_fichas_en_riesgo = sum(fichas_por_codigo[c] for c in conflictivos)
        multi_empresa = [c for c in conflictivos if len(empresas_por_codigo[c]) > 1]

        self.stdout.write('  Codigos distintos            : %s' % len(variantes_por_codigo))
        self.stdout.write(self.style.WARNING(
            '  Codigos con >1 variante      : %s  (la propagacion vieja los mezclaba)'
            % len(conflictivos)))
        self.stdout.write('  Fichas bajo esos codigos     : %s' % total_fichas_en_riesgo)
        self.stdout.write('  ...y ademas cruzan empresas  : %s' % len(multi_empresa))

        top = sorted(conflictivos.items(), key=lambda kv: len(kv[1]), reverse=True)[:limite]
        if top:
            self.stdout.write('\n  Peores casos (codigo -> variantes distintas / fichas):')
            for codigo, ident in top:
                self.stdout.write('    %-28s %2s variantes  %4s fichas  %s empresa(s)' % (
                    codigo[:28], len(ident), fichas_por_codigo[codigo],
                    len(empresas_por_codigo[codigo]) or 1))

    # ── C) EDICIONES CON MERCADERÍA EN TRÁNSITO ──────────────────────────
    def _seccion_transito(self, limite):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n== C) Fichas editadas con traspaso emitido y sin recepcionar =='))

        movs = (
            Movimientos_Producto.objects
            .filter(
                concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO',
                estado='COMPLETADO',
                dte__tipo_transaccion='TRASPASO',
                dte__fecha_recepcion__isnull=True,
            )
            .exclude(dte__estado_dte__in=ESTADOS_DTE_MUERTOS)
            .select_related('dte', 'ProductoTalla__producto', 'sucursal_destino')
        )

        total_docs = set()
        editados = []
        for mov in movs.iterator(chunk_size=1000):
            dte = mov.dte
            producto = mov.ProductoTalla.producto if mov.ProductoTalla else None
            if not dte or not producto:
                continue
            total_docs.add(dte.id)
            # `fecha_actualizacion` es auto_now: se mueve en CADA save del
            # producto (incluida la propagacion de precios), asi que esto es una
            # cota superior, no una prueba de que renombraron.
            if not producto.fecha_actualizacion or not dte.fecha_emision:
                continue
            if producto.fecha_actualizacion.date() > dte.fecha_emision:
                editados.append((dte, producto, mov))

        self.stdout.write('  Traspasos vivos sin recepcionar : %s' % len(total_docs))
        self.stdout.write(self.style.WARNING(
            '  Lineas con ficha tocada despues : %s' % len(editados)))

        for dte, producto, mov in editados[:limite]:
            self.stdout.write('    %s N%s  emitido %s  -> %s | %s | ficha guardada %s' % (
                dte.tipo_documento, dte.numero_documento, dte.fecha_emision,
                mov.sucursal_destino.alias if mov.sucursal_destino else '-',
                (producto.articulo or '')[:28],
                producto.fecha_actualizacion.strftime('%Y-%m-%d %H:%M'),
            ))

        if editados:
            self.stdout.write(
                '\n  Estas lineas aparecen marcadas como "ficha editada" en '
                '/app/recepcion-dte/ al abrir la recepcion.')
