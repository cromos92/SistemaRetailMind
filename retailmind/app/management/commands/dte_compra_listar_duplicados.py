"""
Lista DTE de COMPRA cargados más de una vez con la misma identidad SII
(RUT emisor + tipo + folio), sin mirar la fecha (hallazgos B3-11 / B14-10).

La regla vieja de crearDteCompras solo bloqueaba folio + fecha + proveedor, así
que el mismo documento se pudo cargar dos veces con otra fecha (en la copia
local: NC 775124 y 775125 de PUMA). Desde sep-2026 la vista usa la identidad
SII; este comando es de SOLO LECTURA: informa los grupos para que el usuario
decida qué copia descartar (soft-delete desde la pantalla). No borra nada.

Uso:
    python manage.py dte_compra_listar_duplicados
    python manage.py dte_compra_listar_duplicados --incluir-descartados
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from app.models import Dte, Dte_Detalle_Pago
from app.utils_folio_dte import normalizar_rut


class Command(BaseCommand):
    help = 'Lista DTE de COMPRA duplicados por (RUT emisor, tipo, folio). Solo lectura.'

    def add_arguments(self, parser):
        parser.add_argument('--incluir-descartados', action='store_true',
                            help='Considera también los DTE descartados.')

    def handle(self, *args, **opts):
        qs = Dte.objects.filter(tipo_transaccion='COMPRA').exclude(tipo_documento='COTIZACION')
        if not opts['incluir_descartados']:
            qs = qs.filter(descartado=False)

        grupos = defaultdict(list)
        for d in qs.select_related('emisor').only(
            'id', 'numero_documento', 'tipo_documento', 'fecha_emision', 'monto_con_iva',
            'estado_pago', 'descartado', 'receptor_id', 'emisor__id', 'emisor__rut', 'emisor__nombre',
        ).iterator():
            rut = normalizar_rut(getattr(d.emisor, 'rut', '')) or f'id{d.emisor_id}'
            grupos[(rut, d.tipo_documento, d.numero_documento)].append(d)

        duplicados = {k: v for k, v in grupos.items() if len(v) > 1}
        ids = [d.id for v in duplicados.values() for d in v]
        pagos = dict(
            Dte_Detalle_Pago.objects.filter(dte_id__in=ids)
            .values('dte_id').annotate(t=Sum('monto')).values_list('dte_id', 't')
        )
        # NC aplicadas como pago en otra factura (voucher = folio, mismo emisor)
        aplicadas = set(
            Dte_Detalle_Pago.objects.filter(metodo_pago='Nota de Crédito', dte__tipo_transaccion='COMPRA')
            .values_list('dte__emisor_id', 'voucher')
        )

        self.stdout.write(f'Grupos duplicados (RUT emisor, tipo, folio): {len(duplicados)}')
        for (rut, tipo, folio), docs in sorted(duplicados.items(), key=lambda x: (x[0][1], x[0][2])):
            self.stdout.write(f'- {tipo} N°{folio} RUT {rut} ({docs[0].emisor.nombre if docs[0].emisor else ""}): {len(docs)} cargas')
            for d in sorted(docs, key=lambda x: x.id):
                extra = ''
                if tipo == 'NOTA DE CREDITO' and (d.emisor_id, str(d.numero_documento)) in aplicadas:
                    extra = ' [folio aplicado como NC en una factura]'
                self.stdout.write(
                    f'    DTE #{d.id} emisión {d.fecha_emision} monto {d.monto_con_iva} '
                    f'estado_pago={d.estado_pago} pagos={pagos.get(d.id, 0)} '
                    f'receptor={d.receptor_id} descartado={d.descartado}{extra}'
                )
        if duplicados:
            self.stdout.write(self.style.WARNING(
                'Revisar en el SII la fecha real y descartar (soft-delete) la copia sobrante '
                'desde Gestión Documentos Compras. Este comando no modifica nada.'
            ))
