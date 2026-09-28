"""
Corrige los DTE de COMPRA que dejó el importador XML (views_modulo_compras_xml)
antes de los arreglos de sep-2026:

  B11-12  Nota de DÉBITO (56) guardada con es_nota_credito=True → False.
  B11-08  FACTURA EXENTA (34) guardada con monto_neto=0 → monto_neto =
          monto_con_iva (en una exenta toda la base es exenta: IVA derivado 0,
          igual que emitir_dte_concepto).

Qué es "del importador XML": tipo_transaccion='COMPRA' + es_manual=True +
es_por_concepto=False + bultos=0. Hoy es_manual=True con COMPRA solo lo
escribe confirmar_xml_dte (el otro escritor de es_manual es VENTA_PUBLICO).
Las facturas 33 con líneas exentas NO se pueden corregir desde la BD (el monto
exento no quedó guardado): se listan para revisarlas con su XML.

El estado_pago lo normaliza dte_compra_normalizar_estado_pago (unidad V2).

Uso:
    python manage.py xml_dte_normalizar_importados            # dry-run
    python manage.py xml_dte_normalizar_importados --apply
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from app.models import Dte


def _importados():
    return Dte.objects.filter(tipo_transaccion='COMPRA', es_manual=True,
                              es_por_concepto=False, bultos=0)


class Command(BaseCommand):
    help = 'Corrige ND y exentas importadas por XML (dry-run por defecto; --apply para escribir).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Aplica los cambios.')
        parser.add_argument('--muestra', type=int, default=20, help='Filas de ejemplo a listar.')

    def handle(self, *args, **opts):
        aplicar, muestra = opts['apply'], opts['muestra']
        modo = 'APLICAR' if aplicar else 'DRY-RUN'
        base = _importados()

        nd = base.filter(tipo_documento='NOTA DE DEBITO', es_nota_credito=True)
        exentas = base.filter(tipo_documento='FACTURA EXENTA', monto_neto=0, monto_con_iva__gt=0)
        con_exento = base.filter(tipo_documento='FACTURA ELECTRONICA',
                                 referencias__icontains='exento')

        self.stdout.write(f'[{modo}] DTE de COMPRA importados por XML: {base.count()}')
        self.stdout.write(f'  Notas de débito marcadas como crédito: {nd.count()}')
        for d in nd.select_related('emisor').order_by('id')[:muestra]:
            self.stdout.write(f'    #{d.id} ND {d.numero_documento} {d.emisor.nombre} {d.fecha_emision}')
        self.stdout.write(f'  Facturas exentas con neto 0: {exentas.count()}')
        for d in exentas.select_related('emisor').order_by('id')[:muestra]:
            self.stdout.write(f'    #{d.id} FE {d.numero_documento} {d.emisor.nombre} '
                              f'total {int(d.monto_con_iva)} → neto {int(d.monto_con_iva)}')
        self.stdout.write(f'  (Informativo) facturas 33 con nota de exento ya corregida: '
                          f'{con_exento.count()}')

        if not aplicar:
            self.stdout.write(self.style.WARNING('Dry-run: no se escribió nada. Usa --apply.'))
            return
        with transaction.atomic():
            n_nd = nd.update(es_nota_credito=False)
            n_ex = exentas.update(monto_neto=F('monto_con_iva'))
        self.stdout.write(self.style.SUCCESS(
            f'Listo: {n_nd} nota(s) de débito y {n_ex} factura(s) exenta(s) corregidas.'))
