"""Alerta de cotizaciones FACTURADAS con despacho pendiente. READ-ONLY.

Lista las cotizaciones facturadas que todavía tienen unidades sin salida de
stock (despacho diferido) y marca como ATRASADAS las que llevan más de
`--dias` días desde la factura: el cliente pagó y no ha recibido.

Opcionalmente envía el resumen por correo (`--email a@b.cl,c@d.cl`). Pensado
para correr una vez al día (cron / scheduler de Railway).

Uso:
    python manage.py alertar_despachos_pendientes
    python manage.py alertar_despachos_pendientes --dias 5 --solo-atrasadas
    python manage.py alertar_despachos_pendientes --email bodega@empresa.cl
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import Cotizacion_Empresa

logger = logging.getLogger('app')


def cotizaciones_con_despacho_pendiente(dias_alerta=7, sucursal_id=None):
    """Lista de dicts (una por cotización con unidades pendientes), atrasadas primero."""
    qs = (
        Cotizacion_Empresa.objects
        .filter(facturada=True,
                estado_despacho__in=(Cotizacion_Empresa.DESPACHO_PENDIENTE,
                                     Cotizacion_Empresa.DESPACHO_PARCIAL))
        .select_related('sucursal', 'cliente', 'vendedor', 'dte')
        .prefetch_related('items__skus_asociados')
    )
    if sucursal_id:
        qs = qs.filter(sucursal_id=sucursal_id)
    hoy = timezone.localdate()
    filas = []
    for cot in qs:
        pendientes = sum(item.unidades_pendientes_despacho for item in cot.items.all())
        if pendientes <= 0:
            continue
        dias = ((hoy - timezone.localtime(cot.fecha_facturacion).date()).days
                if cot.fecha_facturacion else None)
        filas.append({
            'numero': cot.numero_cotizacion,
            'sucursal': cot.sucursal.alias if cot.sucursal_id else '',
            'cliente': cot.cliente.nombre if cot.cliente_id else '',
            'vendedor': cot.vendedor.nombre if cot.vendedor_id else '',
            'documento': (f'{cot.dte.tipo_documento} #{cot.dte.numero_documento}'
                          if cot.dte_id else (cot.numero_factura or '')),
            'unidades_pendientes': pendientes,
            'dias': dias,
            'atrasada': dias is not None and dias > dias_alerta,
        })
    filas.sort(key=lambda f: (not f['atrasada'], -(f['dias'] or 0)))
    return filas


class Command(BaseCommand):
    help = 'Lista (y opcionalmente envía por correo) las cotizaciones facturadas con despacho pendiente'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=7,
                            help='Días desde la factura para marcar ATRASADA (default 7)')
        parser.add_argument('--solo-atrasadas', action='store_true')
        parser.add_argument('--sucursal', type=int, default=None, help='ID de sucursal')
        parser.add_argument('--email', type=str, default='',
                            help='Destinatarios separados por coma')

    def handle(self, *args, **options):
        dias = options['dias']
        filas = cotizaciones_con_despacho_pendiente(dias, options['sucursal'])
        if options['solo_atrasadas']:
            filas = [f for f in filas if f['atrasada']]

        if not filas:
            self.stdout.write(self.style.SUCCESS('✅ Sin cotizaciones con despacho pendiente'))
            return

        atrasadas = sum(1 for f in filas if f['atrasada'])
        lineas = [
            f"Despachos pendientes de cotizaciones facturadas ({timezone.localdate():%d-%m-%Y})",
            f"Total: {len(filas)} · Atrasadas (+{dias} días): {atrasadas}",
            '',
        ]
        for f in filas:
            marca = 'ATRASADA ' if f['atrasada'] else ''
            lineas.append(
                f"{marca}{f['numero']} [{f['sucursal']}] {f['cliente']} · {f['documento']} · "
                f"{f['unidades_pendientes']} ud(s) pendientes · "
                f"{f['dias'] if f['dias'] is not None else '?'} días · vendedor {f['vendedor'] or '-'}"
            )
        texto = '\n'.join(lineas)
        self.stdout.write(texto)

        destinatarios = [d.strip() for d in (options['email'] or '').split(',') if d.strip()]
        if destinatarios:
            try:
                send_mail(
                    subject=f'Despachos pendientes: {atrasadas} atrasada(s) de {len(filas)}',
                    message=texto + '\n\nRevisar en /app/cotizaciones/ → filtro «Despacho atrasado».',
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    recipient_list=destinatarios,
                    fail_silently=False,
                )
                self.stdout.write(self.style.SUCCESS(f'Correo enviado a {", ".join(destinatarios)}'))
            except Exception as e:  # noqa: BLE001 — el listado ya salió por consola
                logger.exception("alertar_despachos_pendientes: no se pudo enviar el correo")
                self.stderr.write(f'No se pudo enviar el correo: {e}')
