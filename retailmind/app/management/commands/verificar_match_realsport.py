"""Diagnostica el match SKU(realsport.cl) ↔ articulo(RetailMind).

Toma una muestra de SKUs reales del ecommerce, los compara contra
``Producto.articulo`` aplicando distintas estrategias (exacto, case-insensitive,
trim, upper, lower) y reporta cuántos matchean con cada una. Sirve para
descubrir si hay normalización pendiente antes de sincronizar masivo.

Uso:
    python manage.py verificar_match_realsport --codigo realsport
    python manage.py verificar_match_realsport --codigo realsport --muestra 30
"""
from __future__ import annotations

import random

from django.core.management.base import BaseCommand, CommandError

from app.models import CredencialesEcommerce, Producto
from app.services.realsport_imagenes_service import (
    RealsportImagenesError,
    probar_conexion,
    traer_catalogo_portadas,
)


class Command(BaseCommand):
    help = 'Diagnostica el match SKU(ecommerce) ↔ articulo(RetailMind) con varias estrategias.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--codigo', required=True,
            help='codigo de CredencialesEcommerce a usar (ej: realsport).',
        )
        parser.add_argument(
            '--muestra', type=int, default=20,
            help='Cantidad de SKUs a mostrar (default 20).',
        )

    def handle(self, *args, **opts):
        codigo = opts['codigo']
        muestra_n = max(5, min(100, opts['muestra']))

        try:
            cred = CredencialesEcommerce.objects.get(codigo=codigo, activo=True)
        except CredencialesEcommerce.DoesNotExist:
            raise CommandError(f'No existe credencial activa con codigo={codigo}.')

        self.stdout.write(f'Credencial: {cred.nombre} ({cred.url_api})')
        self.stdout.write(f'Empresa propietaria: {cred.empresa.nombre} (RUT {cred.empresa.rut})')

        # 1. Health
        health = probar_conexion(cred)
        if not health['ok']:
            raise CommandError(
                f'Health falló: status={health["status"]}, detalle={health["detalle"]}'
            )
        self.stdout.write(self.style.SUCCESS('Health: OK'))
        self.stdout.write('')

        # 2. Traer SKUs del ecommerce (primera página)
        try:
            skus_ecommerce = []
            for pagina in traer_catalogo_portadas(cred, page_size=500):
                skus_ecommerce.extend(pagina.keys())
                if len(skus_ecommerce) >= 1500:
                    break
        except RealsportImagenesError as exc:
            raise CommandError(f'Endpoint falló: {exc}')

        if not skus_ecommerce:
            self.stdout.write(self.style.WARNING(
                'El ecommerce no devolvió ningún producto con foto.'
            ))
            return

        self.stdout.write(f'SKUs traídos del ecommerce: {len(skus_ecommerce)}')

        # Sample aleatorio
        random.shuffle(skus_ecommerce)
        muestra = skus_ecommerce[:muestra_n]

        # 3. Cargar articulos de RetailMind para comparar
        # Empresa propia primero, luego todos
        articulos_empresa = set(
            Producto.objects
            .filter(sucursal__empresa=cred.empresa)
            .values_list('articulo', flat=True)
            .distinct()
        )
        articulos_todos = set(
            Producto.objects.values_list('articulo', flat=True).distinct()
        )
        self.stdout.write(f'Articulos RetailMind empresa {cred.empresa.nombre}: {len(articulos_empresa)}')
        self.stdout.write(f'Articulos RetailMind TODAS las empresas: {len(articulos_todos)}')
        self.stdout.write('')

        # Pre-calcular maps de normalizaciones (para no iterar el set cada vez)
        articulos_lower = {a.lower(): a for a in articulos_todos if a}
        articulos_upper = {a.upper(): a for a in articulos_todos if a}
        articulos_trim = {a.strip(): a for a in articulos_todos if a}
        articulos_trim_lower = {a.strip().lower(): a for a in articulos_todos if a}

        # 4. Estadísticas globales sobre TODOS los SKUs traídos
        self.stdout.write(self.style.MIGRATE_HEADING(
            '=== Estadísticas sobre todos los SKUs traídos ==='
        ))
        total = len(skus_ecommerce)
        matches = self._estrategias_match(
            skus_ecommerce, articulos_empresa, articulos_todos,
            articulos_lower, articulos_upper, articulos_trim, articulos_trim_lower,
        )

        for nombre, count in matches.items():
            pct = (count / total) * 100 if total else 0
            estilo = self.style.SUCCESS if pct >= 80 else (self.style.WARNING if pct >= 30 else self.style.ERROR)
            self.stdout.write(estilo(
                f'  {nombre:35s}: {count:5d}/{total} ({pct:5.1f}%)'
            ))

        # 5. Side-by-side de la muestra
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'=== Muestra de {muestra_n} SKUs lado a lado ==='
        ))
        self.stdout.write(
            f'{"SKU ecommerce":40s} | {"match":15s} | {"articulo RetailMind"}'
        )
        self.stdout.write('-' * 100)

        for sku in muestra:
            tipo_match, articulo_real = self._clasificar(
                sku, articulos_empresa, articulos_todos,
                articulos_lower, articulos_upper, articulos_trim, articulos_trim_lower,
            )
            estilo = self.style.SUCCESS if 'exacto' in tipo_match else (
                self.style.WARNING if tipo_match != 'NO_MATCH' else self.style.ERROR
            )
            self.stdout.write(estilo(
                f'{repr(sku)[:40]:40s} | {tipo_match:15s} | {repr(articulo_real or "—")}'
            ))

        # 6. Ejemplos de articulos en RetailMind (para ver formato)
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(
            '=== Ejemplos de articulos en RetailMind (10 al azar) ==='
        ))
        sample_rm = random.sample(list(articulos_todos), min(10, len(articulos_todos)))
        for a in sample_rm:
            self.stdout.write(f'  {repr(a)}')

        # 7. Veredicto
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('=== Veredicto ==='))
        for nombre in ('exacto (empresa)', 'exacto (todas)', 'case-insensitive',
                       'trim', 'trim+case-insensitive'):
            pct = (matches[nombre] / total) * 100 if total else 0
            if pct >= 80:
                self.stdout.write(self.style.SUCCESS(
                    f'  Estrategia recomendada: {nombre}  ({pct:.1f}% match)'
                ))
                return
        self.stdout.write(self.style.ERROR(
            '  Ninguna estrategia simple supera el 80%. Hay transformación más '
            'compleja: revisar publisher.py de AllConected y los ejemplos de '
            'muestra arriba para descubrir el patrón.'
        ))

    def _estrategias_match(self, skus, art_empresa, art_todos,
                           art_lower, art_upper, art_trim, art_trim_lower):
        m = {
            'exacto (empresa)': 0,
            'exacto (todas)': 0,
            'case-insensitive': 0,
            'upper': 0,
            'trim': 0,
            'trim+case-insensitive': 0,
        }
        for sku in skus:
            if sku in art_empresa:
                m['exacto (empresa)'] += 1
            if sku in art_todos:
                m['exacto (todas)'] += 1
            if sku.lower() in art_lower:
                m['case-insensitive'] += 1
            if sku.upper() in art_upper:
                m['upper'] += 1
            if sku.strip() in art_trim:
                m['trim'] += 1
            if sku.strip().lower() in art_trim_lower:
                m['trim+case-insensitive'] += 1
        return m

    def _clasificar(self, sku, art_empresa, art_todos,
                    art_lower, art_upper, art_trim, art_trim_lower):
        if sku in art_empresa:
            return 'exacto-empresa', sku
        if sku in art_todos:
            return 'exacto-otra-emp', sku
        if sku.lower() in art_lower:
            return 'case-insens', art_lower[sku.lower()]
        if sku.strip() in art_trim:
            return 'trim', art_trim[sku.strip()]
        if sku.strip().lower() in art_trim_lower:
            return 'trim+case', art_trim_lower[sku.strip().lower()]
        return 'NO_MATCH', None
