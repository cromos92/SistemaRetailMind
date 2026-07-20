# -*- coding: utf-8 -*-
"""Depreca las categorías VIEJAS planas que quedaron VACÍAS tras la
recategorización v1.2, renombrándolas con prefijo '_ZZ_' (NO las borra:
Producto.categoria es on_delete=CASCADE → borrar una categoría borraría sus
productos). Las '_ZZ_' quedan ocultas de los selects (ver categorias_existentes).

Categoría vieja = raíz (sin padre) que NO tiene hijas v1.2. Los 3 padres reales
(Calzado/Ropa/Accesorios) tienen hijas, así que quedan fuera automáticamente.
Solo se deprecan las que tienen 0 productos; las que aún tienen productos se
reportan (siguen pendientes de revisión) y NO se tocan.

DRY-RUN por defecto; --apply escribe.

Uso:
    python manage.py limpiar_categorias_viejas          # dry-run
    python manage.py limpiar_categorias_viejas --apply
    python manage.py limpiar_categorias_viejas --revertir   # quita el prefijo _ZZ_
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from app.models import Categoria

logger = logging.getLogger('app')
PREFIJO = '_ZZ_'
PADRES_V12 = {'calzado', 'ropa', 'accesorios'}


class Command(BaseCommand):
    help = "Depreca (renombra _ZZ_) las categorías viejas planas ya vacías (dry-run por defecto)"

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--revertir', action='store_true',
                            help='Quita el prefijo _ZZ_ (deshacer)')

    def handle(self, *args, **opts):
        apply_ = opts['apply']
        modo = 'APPLY' if apply_ else 'DRY-RUN'

        if opts['revertir']:
            qs = Categoria.objects.filter(nombre__startswith=PREFIJO)
            self.stdout.write(self.style.MIGRATE_HEADING(f"[{modo}] revertir _ZZ_ ({qs.count()})"))
            if apply_:
                with transaction.atomic():
                    for c in qs:
                        c.nombre = c.nombre[len(PREFIJO):]
                        c.save(update_fields=['nombre'])
            self.stdout.write(self.style.SUCCESS(f"{qs.count()} categorías {'revertidas' if apply_ else 'a revertir'}"))
            return

        # raíces sin hijas (categorías viejas planas), con conteo de productos
        raices = (Categoria.objects.filter(padre__isnull=True)
                  .annotate(n_prod=Count('categoria_productos'),
                            n_hijas=Count('subcategorias'))
                  .order_by('nombre'))
        vacias = []
        con_productos = []
        for c in raices:
            if c.n_hijas > 0:                       # es un padre v1.2 (tiene hijas)
                continue
            if c.nombre.lower() in PADRES_V12:       # por si acaso, no tocar padres
                continue
            if c.nombre.startswith(PREFIJO):         # ya deprecada
                continue
            if c.n_prod == 0:
                vacias.append(c)
            else:
                con_productos.append(c)

        self.stdout.write(self.style.MIGRATE_HEADING(f"[{modo}] limpiar_categorias_viejas"))
        self.stdout.write(f"  categorías viejas VACÍAS a deprecar: {len(vacias)}")
        for c in vacias:
            self.stdout.write(f"     _ZZ_ ← {c.nombre}")
        self.stdout.write(f"\n  categorías viejas que AÚN tienen productos (NO se tocan): {len(con_productos)}")
        for c in sorted(con_productos, key=lambda x: -x.n_prod):
            self.stdout.write(f"     {c.n_prod:6}  {c.nombre}")

        if apply_:
            with transaction.atomic():
                for c in vacias:
                    c.nombre = PREFIJO + c.nombre
                    c.save(update_fields=['nombre'])
            self.stdout.write(self.style.SUCCESS(f"\n  {len(vacias)} categorías deprecadas (renombradas _ZZ_)"))
        else:
            self.stdout.write(self.style.WARNING("\n  (dry-run: nada escrito; usa --apply)"))
        logger.info("limpiar_categorias_viejas %s: %d deprecadas, %d con productos",
                    modo, len(vacias), len(con_productos))
