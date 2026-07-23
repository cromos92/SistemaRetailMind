"""
Marca en estado VENCIDA las gift cards cuya fecha de vencimiento ya pasó.

El consumo ya rechaza dinámicamente las tarjetas vencidas (`esta_vencida`); este
comando persiste el estado para poder filtrarlas y reportarlas. No borra ni toca
el saldo: solo cambia el estado de ACTIVA/AGOTADA a VENCIDA.

Pensado para correr a diario por cron / Celery beat:
    python manage.py expirar_giftcards
    python manage.py expirar_giftcards --dry-run
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import GiftCard
from app.services import giftcard_service

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = 'Marca en estado VENCIDA las gift cards ya expiradas (ACTIVA/AGOTADA).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo reporta cuántas gift cards se marcarían, sin aplicar cambios.',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            hoy = timezone.localdate()
            n = GiftCard.objects.filter(
                estado__in=['ACTIVA', 'AGOTADA'],
                fecha_vencimiento__isnull=False,
                fecha_vencimiento__lt=hoy,
            ).count()
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] Se marcarían {n} gift cards como VENCIDAS.'
            ))
            return

        n = giftcard_service.marcar_vencidas()
        logger.info("expirar_giftcards: %s gift cards marcadas VENCIDA", n)
        self.stdout.write(self.style.SUCCESS(
            f'>> {n} gift cards marcadas como VENCIDAS.'
        ))
