# -*- coding: utf-8 -*-
"""
Management command para recalcular el cache denormalizado de depósitos en
todos los arqueos existentes (backfill inicial después de la migración
`0138_arqueocaja_cache_depositos`).

Uso:
    python manage.py recalcular_cache_arqueos
    python manage.py recalcular_cache_arqueos --sucursal 3
    python manage.py recalcular_cache_arqueos --batch-size 500
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import ArqueoCaja


class Command(BaseCommand):
    help = (
        'Recalcula los campos cache_* de ArqueoCaja a partir de los depositos '
        'existentes. Seguro de correr varias veces.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sucursal',
            type=int,
            default=None,
            help='Recalcular solo arqueos de una sucursal específica.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=200,
            help='Tamaño del batch para procesar (default: 200).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No guarda cambios; solo imprime cuántos arqueos se procesarían.',
        )

    def handle(self, *args, **options):
        sucursal_id = options.get('sucursal')
        batch_size = options['batch_size']
        dry_run = options['dry_run']

        qs = ArqueoCaja.objects.all().only(
            'id', 'fecha_arqueo', 'sucursal_id',
        )
        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)

        total = qs.count()
        self.stdout.write(self.style.NOTICE(
            f'Arqueos a procesar: {total} '
            f'(sucursal={sucursal_id or "todas"}, batch_size={batch_size}, '
            f'dry_run={dry_run})'
        ))

        if dry_run or total == 0:
            return

        procesados = 0
        with transaction.atomic():
            for arqueo in qs.iterator(chunk_size=batch_size):
                arqueo.recalcular_cache_depositos(save=True)
                procesados += 1
                if procesados % batch_size == 0:
                    self.stdout.write(f'  ... {procesados}/{total}')

        self.stdout.write(self.style.SUCCESS(
            f'Listo: {procesados} arqueos con cache recalculado.'
        ))
