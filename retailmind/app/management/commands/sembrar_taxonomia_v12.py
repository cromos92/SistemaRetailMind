# -*- coding: utf-8 -*-
"""
Siembra la taxonomía v1.2 SIN borrar nada:
  1. Padres e hijos en Categoria (usa el FK padre existente; reusa stubs
     Calzado/Ropa/Accesorios por nombre).
  2. Atributo "Especialidad" + sus 36 opciones (AtributoOpcion.valor = slug).
  3. Opción de género BEBÉ (no toca DAMA aquí; eso lo hace aplicar_recategorizacion_v12).

Idempotente (get_or_create en todo). Por defecto DRY-RUN: muestra qué crearía.

Uso:
    python manage.py sembrar_taxonomia_v12            # dry-run
    python manage.py sembrar_taxonomia_v12 --apply    # crea de verdad
"""
import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Categoria, Productos_Atributos, AtributoOpcion

from ._data_recategorizacion_v12 import (
    PADRES, SUBS, ESPECIALIDADES, NOMBRE_ATRIBUTO_ESPECIALIDAD, GENERO_CREAR,
)

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = "Siembra árbol de categorías v1.2 + atributo Especialidad (idempotente, dry-run por defecto)"

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Escribe en la BD (sin esto solo muestra el plan)')

    def handle(self, *args, **opts):
        apply_ = opts['apply']
        plan = []

        with transaction.atomic():
            # 1) Padres
            padres_obj = {}
            for slug, nombre in PADRES.items():
                obj = Categoria.objects.filter(nombre__iexact=nombre, padre__isnull=True).first()
                if obj is None:
                    plan.append(f"CREAR padre Categoria '{nombre}'")
                    if apply_:
                        obj = Categoria.objects.create(nombre=nombre, padre=None)
                padres_obj[slug] = obj

            # 2) Hijos
            for slug, (padre_slug, nombre) in SUBS.items():
                padre = padres_obj.get(padre_slug)
                qs = Categoria.objects.filter(nombre__iexact=nombre)
                if padre is not None:
                    qs = qs.filter(padre=padre)
                if not qs.exists():
                    plan.append(f"CREAR hijo Categoria '{nombre}' bajo '{PADRES[padre_slug]}' (slug {slug})")
                    if apply_ and padre is not None:
                        Categoria.objects.create(nombre=nombre, padre=padre)

            # 3) Atributo Especialidad + opciones
            attr = Productos_Atributos.objects.filter(
                nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD).first()
            if attr is None:
                plan.append(f"CREAR Productos_Atributos '{NOMBRE_ATRIBUTO_ESPECIALIDAD}'")
                if apply_:
                    attr = Productos_Atributos.objects.create(
                        nombre=NOMBRE_ATRIBUTO_ESPECIALIDAD,
                        descripcion='Deporte o uso del producto (multi-etiqueta, slug estable). '
                                    'Consumido por los menús del ecommerce.')
            for slug, (familia, label) in ESPECIALIDADES.items():
                if attr is not None and not AtributoOpcion.objects.filter(
                        atributo=attr, valor=slug).exists():
                    plan.append(f"CREAR AtributoOpcion Especialidad '{slug}' ({familia}: {label})")
                    if apply_:
                        AtributoOpcion.objects.create(atributo=attr, valor=slug)
                elif attr is None:
                    plan.append(f"CREAR AtributoOpcion Especialidad '{slug}' ({familia}: {label})")

            # 4) Género BEBÉ (busca el atributo de género por sus opciones conocidas)
            gen_attr = None
            op = AtributoOpcion.objects.filter(valor__in=['HOMBRE', 'MUJER', 'UNISEX']).first()
            if op:
                gen_attr = op.atributo
            for valor in GENERO_CREAR:
                if gen_attr and not AtributoOpcion.objects.filter(
                        atributo=gen_attr, valor__iexact=valor).exists():
                    plan.append(f"CREAR AtributoOpcion Género '{valor}'")
                    if apply_:
                        AtributoOpcion.objects.create(atributo=gen_attr, valor=valor)
                elif gen_attr is None:
                    plan.append(f"AVISO: no encontré el atributo Género para crear '{valor}'")

            if not apply_:
                transaction.set_rollback(True)

        modo = 'APPLY' if apply_ else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(f"[{modo}] sembrar_taxonomia_v12"))
        if not plan:
            self.stdout.write(self.style.SUCCESS("Nada que crear: la taxonomía ya está sembrada."))
        for p in plan:
            self.stdout.write(f"  - {p}")
        self.stdout.write(self.style.SUCCESS(
            f"{len(plan)} operaciones {'aplicadas' if apply_ else 'pendientes (usa --apply)'}"))
        logger.info("sembrar_taxonomia_v12 %s: %d operaciones", modo, len(plan))
