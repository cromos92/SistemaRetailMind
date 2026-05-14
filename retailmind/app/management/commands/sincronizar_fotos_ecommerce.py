"""Sincroniza URLs de portada desde cada CredencialesEcommerce activa.

Estrategia:

  * **Sin flags** (sync periódico): para cada credencial activa, pide al
    ecommerce SU catálogo completo de SKUs con foto (paginado) y hace
    upsert en ``FotoPortadaArticulo`` solo de los SKUs que existen como
    ``articulo`` en RetailMind. Eficiente: 1 request por página, no por
    articulo local.

  * **--articulo X**: modo lookup puntual. Pide al ecommerce solo ese SKU.

Uso típico (cron diario):
    python manage.py sincronizar_fotos_ecommerce

Sólo una credencial:
    python manage.py sincronizar_fotos_ecommerce --codigo realsport

Sólo un articulo (debug):
    python manage.py sincronizar_fotos_ecommerce --codigo realsport --articulo ZAP-123
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from app.models import CredencialesEcommerce
from app.services.realsport_imagenes_service import (
    RealsportImagenesError,
    sincronizar_articulos_puntuales,
    sincronizar_credencial,
)


class Command(BaseCommand):
    help = 'Sincroniza portadas desde los ecommerces externos configurados.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--codigo', default=None,
            help='Sólo procesa esta credencial (por codigo).',
        )
        parser.add_argument(
            '--articulo', default=None,
            help='Sólo procesa este articulo. Requiere --codigo.',
        )
        parser.add_argument(
            '--page-size', type=int, default=500,
            help='Tamaño de página al pedir el catálogo (default 500, max 1000).',
        )

    def handle(self, *args, **opts):
        codigo = opts.get('codigo')
        articulo_unico = opts.get('articulo')
        page_size = max(1, min(1000, opts['page_size']))

        if articulo_unico and not codigo:
            raise CommandError('--articulo requiere --codigo.')

        qs = CredencialesEcommerce.objects.filter(activo=True).order_by('-prioridad')
        if codigo:
            qs = qs.filter(codigo=codigo)

        credenciales = list(qs)
        if not credenciales:
            self.stdout.write(self.style.WARNING('No hay credenciales activas que procesar.'))
            return

        total_global = {'procesados': 0, 'con_foto': 0, 'sin_match_local': 0, 'paginas': 0}

        for cred in credenciales:
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'>>> {cred.nombre} ({cred.codigo}) — empresa {cred.empresa.nombre}'
            ))

            try:
                if articulo_unico:
                    self.stdout.write(f'  Modo lookup puntual: {articulo_unico}')
                    resultado = sincronizar_articulos_puntuales(
                        cred, [articulo_unico], lote=1,
                    )
                    self.stdout.write(self.style.SUCCESS(
                        f'  procesados={resultado["procesados"]}  '
                        f'con_foto={resultado["con_foto"]}  '
                        f'sin_foto={resultado["sin_foto"]}  '
                        f'errores={resultado["errores"]}'
                    ))
                else:
                    self.stdout.write(f'  Modo catálogo completo (page_size={page_size}). Tirando del ecommerce...')

                    def progreso(page, total, recibidos_en_pagina):
                        self.stdout.write(
                            f'  página {page}: total ecommerce={total}, '
                            f'recibidos={recibidos_en_pagina}'
                        )

                    resultado = sincronizar_credencial(
                        cred, page_size=page_size, on_page=progreso,
                    )
                    self.stdout.write(self.style.SUCCESS(
                        f'  paginas={resultado["paginas"]}  '
                        f'procesados={resultado["procesados"]}  '
                        f'con_foto={resultado["con_foto"]}  '
                        f'(exacto={resultado.get("match_exacto", 0)}, '
                        f'flexible={resultado.get("match_flexible", 0)}, '
                        f'talla={resultado.get("match_por_talla", 0)})  '
                        f'sin_match={resultado["sin_match_local"]}'
                    ))
                    for k in total_global:
                        total_global[k] += resultado.get(k, 0)

            except RealsportImagenesError as exc:
                self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))

        if not articulo_unico:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(
                f'TOTAL: paginas={total_global["paginas"]}  '
                f'procesados={total_global["procesados"]}  '
                f'con_foto={total_global["con_foto"]}  '
                f'sin_match_local={total_global["sin_match_local"]}'
            ))
