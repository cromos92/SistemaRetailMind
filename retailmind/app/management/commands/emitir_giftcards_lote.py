"""
Emite un LOTE de gift cards digitales con código de sistema (sin titular:
se vincula al cliente en el primer canje) y genera un CSV con los códigos.

Pensado para campañas tipo "30 gift cards de $50.000 para REALSPORT":

    # Ver qué haría (default: NO emite nada)
    python manage.py emitir_giftcards_lote --cantidad 15 --monto 50000 \
        --sucursal NICK1 --empresa 76104936-4 --vigencia-dias 60 \
        --motivo PROMOCION --descripcion "LOTE REALSPORT NICK 2026-08"

    # Emitir de verdad
    python manage.py emitir_giftcards_lote ... --aplicar

Notas:
- `--empresa` acota el ámbito de canje a las sucursales de ESA empresa
  (acepta RUT o id). Sin `--empresa`, las tarjetas valen en toda la cadena.
- `--sucursal` es la sucursal de EMISIÓN (acepta alias, nombre o id); no
  restringe dónde se canjea — para eso está `--empresa`.
- Cada tarjeta queda con su fila EMISION en el ledger (via giftcard_service).
- El CSV con los códigos se escribe junto a manage.py:
  _giftcards_lote_<descripcion>_<timestamp>.csv
"""
import csv
import logging
import os

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from app.models import Empresa, GiftCard, Sucursal, MOTIVO_GIFTCARD_CHOICES
from app.services import giftcard_service

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = ('Emite un lote de gift cards digitales (código de sistema, sin titular) '
            'y genera un CSV con los códigos. Dry-run por defecto: usa --aplicar.')

    def add_arguments(self, parser):
        parser.add_argument('--cantidad', type=int, required=True,
                            help='Cuántas gift cards emitir (1 a 500).')
        parser.add_argument('--monto', type=int, required=True,
                            help='Monto de cada gift card en pesos.')
        parser.add_argument('--sucursal', required=True,
                            help='Sucursal de emisión (alias, nombre o id).')
        parser.add_argument('--empresa', default=None,
                            help='Ámbito de canje: RUT o id de la empresa. '
                                 'Omitir = válidas en todas las empresas.')
        parser.add_argument('--vigencia-dias', type=int, default=60,
                            help='Días de vigencia desde hoy (default 60).')
        parser.add_argument('--motivo', default='PROMOCION',
                            help='Motivo (PROMOCION/COMPENSACION/REGALO_CORP/VENTA/OTRO).')
        parser.add_argument('--descripcion', default='',
                            help='Etiqueta del lote (queda en cada tarjeta; sirve para filtrar).')
        parser.add_argument('--observaciones', default='',
                            help='Observaciones libres para cada tarjeta.')
        parser.add_argument('--aplicar', action='store_true',
                            help='Emite de verdad. Sin esta flag solo muestra el plan (dry-run).')

    def _resolver_sucursal(self, valor):
        qs = Sucursal.objects.all()
        if str(valor).isdigit():
            suc = qs.filter(id=int(valor)).first()
            if suc:
                return suc
        suc = qs.filter(Q(alias__iexact=valor) | Q(nombre__iexact=valor)).first()
        if not suc:
            raise CommandError(f'Sucursal no encontrada: {valor!r}')
        return suc

    def _resolver_empresa(self, valor):
        if valor is None:
            return None
        qs = Empresa.objects.all()
        if str(valor).isdigit():
            emp = qs.filter(id=int(valor)).first()
            if emp:
                return emp
        emp = qs.filter(rut__iexact=str(valor).strip()).first()
        if not emp:
            raise CommandError(f'Empresa no encontrada (RUT o id): {valor!r}')
        return emp

    def handle(self, *args, **options):
        cantidad = options['cantidad']
        monto = options['monto']
        if not 1 <= cantidad <= 500:
            raise CommandError('La cantidad debe estar entre 1 y 500.')
        if monto <= 0:
            raise CommandError('El monto debe ser mayor a 0.')
        motivo = (options['motivo'] or 'PROMOCION').upper()
        if motivo not in {c[0] for c in MOTIVO_GIFTCARD_CHOICES}:
            raise CommandError(f'Motivo inválido: {motivo}')
        vigencia_dias = options['vigencia_dias']
        if vigencia_dias <= 0:
            raise CommandError('La vigencia debe ser positiva (días).')

        sucursal = self._resolver_sucursal(options['sucursal'])
        empresa = self._resolver_empresa(options['empresa'])
        vencimiento = timezone.localdate() + timezone.timedelta(days=vigencia_dias)
        descripcion = (options['descripcion'] or '').strip()
        pasivo = cantidad * monto

        ambito_txt = (f'solo sucursales de {empresa.nombre} (RUT {empresa.rut})'
                      if empresa else 'TODAS las empresas de la cadena')
        self.stdout.write('')
        self.stdout.write(f'  Cantidad     : {cantidad} gift cards DIGITALES (sin titular)')
        self.stdout.write(f'  Monto c/u    : ${monto:,}'.replace(',', '.'))
        self.stdout.write(self.style.WARNING(
            f'  PASIVO TOTAL : ${pasivo:,}'.replace(',', '.')
            + '  (deuda que asume el negocio)'
        ))
        self.stdout.write(f'  Emisión      : {sucursal.alias} (id {sucursal.id})')
        self.stdout.write(f'  Ámbito canje : {ambito_txt}')
        self.stdout.write(f'  Vencimiento  : {vencimiento} ({vigencia_dias} días)')
        self.stdout.write(f'  Motivo       : {motivo}'
                          + (f' — "{descripcion}"' if descripcion else ''))
        self.stdout.write('')

        if not options['aplicar']:
            self.stdout.write(self.style.WARNING(
                '[DRY-RUN] No se emitió nada. Repite el comando con --aplicar para emitir.'
            ))
            return

        emitidas = []
        for i in range(cantidad):
            gc = giftcard_service.emitir(
                monto,
                sucursal=sucursal,
                empresa=empresa,
                vencimiento=vencimiento,
                tipo_tarjeta='DIGITAL',
                motivo=motivo,
                descripcion=descripcion,
                observaciones=options['observaciones'],
            )
            emitidas.append(gc)
            self.stdout.write(f'  {i + 1:>3}/{cantidad}  {gc.codigo}')

        # CSV con los códigos (para imprimir, repartir o enviar por correo).
        stamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        slug = ''.join(c if c.isalnum() else '_' for c in descripcion)[:40] or 'lote'
        ruta_csv = os.path.join(os.getcwd(), f'_giftcards_lote_{slug}_{stamp}.csv')
        with open(ruta_csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['codigo', 'monto', 'vencimiento', 'ambito', 'sucursal_emision',
                        'motivo', 'descripcion'])
            for gc in emitidas:
                w.writerow([gc.codigo, gc.saldo_actual, gc.fecha_vencimiento,
                            empresa.nombre if empresa else 'TODAS',
                            sucursal.alias, motivo, descripcion])

        logger.info(
            "emitir_giftcards_lote: %s GC de $%s emitidas (sucursal=%s empresa=%s)",
            cantidad, monto, sucursal.alias, empresa.rut if empresa else 'TODAS',
        )
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'>> {cantidad} gift cards emitidas. Pasivo creado: ${pasivo:,}'.replace(',', '.')
        ))
        self.stdout.write(self.style.SUCCESS(f'>> Códigos en: {ruta_csv}'))
        self.stdout.write(
            'Envío por correo: pantalla Gift Cards → Gestionar → "Enviar por correo" '
            '(queda registrado en la trazabilidad).'
        )
