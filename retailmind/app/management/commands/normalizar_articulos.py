"""
Normaliza Producto.articulo al canon de identidad: MAYÚSCULAS, sin espacios
en los extremos ni dobles, sin acentos (el mismo canon que usa
buscar_producto_por_identidad y que ahora aplican las vistas de creación).

Contexto: las vistas de creación (manual y recepción) guardaban el código tal
cual se tipeó, por lo que quedaron códigos en minúscula/mixtos en el catálogo.
El matching de identidad ya era insensible a mayúsculas (no generó duplicados
nuevos), pero varias queries exactas y la consistencia visual se ven afectadas.

Seguro por diseño:
  - DRY-RUN por defecto: sin --apply NO escribe nada.
  - NO toca SKUs, ventas, movimientos ni tallas (el SKU es secuencial global,
    no deriva del texto del código).
  - Si al normalizar el código chocaría con OTRO producto de la misma
    identidad (código normalizado + marca + color + género + categoría +
    sucursal), NO se renombra: se reporta como COLISIÓN para resolver en
    /app/existencias/fusion-duplicados/.

Uso:
    python manage.py normalizar_articulos                  # dry-run global
    python manage.py normalizar_articulos --sucursal 5     # dry-run una sucursal
    python manage.py normalizar_articulos --apply          # escribe los renombres
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Producto
from app.utils_producto_match import normalizar_articulo


class Command(BaseCommand):
    help = ('Normaliza los códigos de artículo (mayúsculas/espacios/acentos). '
            'Dry-run por defecto; --apply para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Escribe los cambios (sin esto solo reporta)')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='Limitar a una sucursal (id)')
        parser.add_argument('--max-detalle', type=int, default=300,
                            help='Máximo de renombres a listar en detalle (default 300)')

    def handle(self, *args, **opts):
        qs = Producto.objects.all()
        if opts['sucursal']:
            qs = qs.filter(sucursal_id=opts['sucursal'])
        qs = qs.select_related('sucursal').only(
            'id', 'articulo', 'atributo1_id', 'atributo2_id', 'atributo3_id',
            'categoria_id', 'sucursal_id', 'sucursal__alias',
        )

        # Mapa de identidades: (código normalizado, marca, color, género,
        # categoría, sucursal) → ids que la ocupan
        ocupadas = {}
        pendientes = []  # (producto, codigo_normalizado, clave_identidad)
        total = 0
        for p in qs.iterator(chunk_size=2000):
            total += 1
            norm = normalizar_articulo(p.articulo)
            clave = (norm, p.atributo1_id, p.atributo2_id, p.atributo3_id,
                     p.categoria_id, p.sucursal_id)
            ocupadas.setdefault(clave, []).append(p.id)
            if (p.articulo or '') != norm:
                pendientes.append((p, norm, clave))

        renombrar, colisiones = [], []
        for p, norm, clave in pendientes:
            otros = [i for i in ocupadas.get(clave, []) if i != p.id]
            (colisiones if otros else renombrar).append((p, norm, otros))

        alias = lambda p: (p.sucursal.alias if p.sucursal_id and p.sucursal else '-')

        self.stdout.write('')
        self.stdout.write(f'Productos revisados:              {total}')
        self.stdout.write(f'Con código fuera de canon:        {len(pendientes)}')
        self.stdout.write(f'  → renombrables sin conflicto:   {len(renombrar)}')
        self.stdout.write(f'  → COLISIONES (no se renombran): {len(colisiones)}')
        self.stdout.write('')

        max_det = opts['max_detalle']
        for p, norm, _ in renombrar[:max_det]:
            self.stdout.write(f'  RENOMBRAR [{alias(p):>5}] id={p.id:<8} "{p.articulo}" -> "{norm}"')
        if len(renombrar) > max_det:
            self.stdout.write(f'  ... y {len(renombrar) - max_det} más (sube --max-detalle para verlos)')

        for p, norm, otros in colisiones:
            self.stdout.write(self.style.WARNING(
                f'  COLISION  [{alias(p):>5}] id={p.id:<8} "{p.articulo}" -> "{norm}" '
                f'ya usado por id(s) {otros} con la MISMA identidad — '
                f'resolver en Fusión de Duplicados'))

        if not opts['apply']:
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE(
                'DRY-RUN: no se escribió nada. Ejecuta con --apply para renombrar.'))
            return

        with transaction.atomic():
            n = 0
            for p, norm, _ in renombrar:
                Producto.objects.filter(id=p.id).update(articulo=norm)
                n += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'✅ {n} códigos normalizados. Colisiones omitidas: {len(colisiones)} '
            f'(esas fichas requieren fusión, no renombre).'))
