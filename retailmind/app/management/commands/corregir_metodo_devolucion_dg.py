# -*- coding: utf-8 -*-
"""
Corrige el MEDIO por el que se devolvió la plata de una Devolución de Dinero
ya aprobada (NC emitida), cuando se registró uno y se pagó por otro.

CASO QUE LO ORIGINA (28-09-2026): PAO1, DG-2-202609-0002 / NC 5155 por $9.990
quedó como TRANSFERENCIA (con datos bancarios de relleno) pero la plata se
devolvió por Mercado Pago. El Resumen de Caja restaba $9.990 de las
transferencias («Otros medios» -$9.990) y dejaba el MP POS entero.

QUÉ HACE (service.cambiar_metodo_devolucion):
  * cambia el pago de la NC (método, medio y voucher): la cuadratura resta del
    medio correcto. La NC tributaria (folio, montos, líneas, TXT) NO se toca:
    el medio de pago no va al SII;
  * con Mercado Pago deja el N° de operación en el voucher (se ve y se busca
    en Consulta de Documentos y en el comprobante) y anota la devolución en el
    libro de cobros MP (la venta queda DEVUELTA si se devolvió entera);
  * anota la corrección en las observaciones de la devolución;
  * con --recalcular-arqueo re-snapshotea los teóricos del arqueo de ese día,
    si ya existe (si no existe no hace falta: la cuadratura se calcula en vivo).

Sin --apply no escribe nada (con Mercado Pago puede CONSULTAR la API de MP para
mostrar el N° de operación, sin guardarlo).

USO:
    python manage.py corregir_metodo_devolucion_dg DG-2-202609-0002 --metodo MERCADO_PAGO --usuario javier
    python manage.py corregir_metodo_devolucion_dg DG-2-202609-0002 --metodo MERCADO_PAGO --usuario javier --apply --recalcular-arqueo
    # N° de operación a mano (si MP no lo entrega o es otro):
    python manage.py corregir_metodo_devolucion_dg DG-2-202609-0002 --metodo MP --operacion-mp 177422093000 --usuario javier --apply
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import ArqueoCaja, DevolucionGarantia
from app.services import devolucion_garantia_service as service
from app.services.devolucion_garantia_service import DevolucionGarantiaError

ALIAS_METODO = {
    'MP': 'MERCADO_PAGO', 'MERCADOPAGO': 'MERCADO_PAGO', 'MERCADO_PAGO': 'MERCADO_PAGO',
    'EFECTIVO': 'EFECTIVO_CAJA', 'EFECTIVO_CAJA': 'EFECTIVO_CAJA',
    'TRANSFERENCIA': 'TRANSFERENCIA_BANCARIA', 'TRANSFERENCIA_BANCARIA': 'TRANSFERENCIA_BANCARIA',
}

# Clave de `_calcular_cuadratura_data` de la que resta cada método.
BUCKET_CUADRATURA = {
    'EFECTIVO_CAJA': ('total_efectivo', 'Efectivo en caja'),
    'TRANSFERENCIA_BANCARIA': ('total_transferencia', 'Transferencia'),
    'MERCADO_PAGO': ('total_mercadopago_pos', 'Mercado Pago POS'),
}


def _plata(v):
    return f'${int(v or 0):,}'.replace(',', '.')


class Command(BaseCommand):
    help = ('Corrige el medio (efectivo / transferencia / Mercado Pago) de una Devolución de '
            'Dinero ya aprobada, para que la caja la reste del medio correcto')

    def add_arguments(self, parser):
        parser.add_argument('devolucion', help='N° de operación (DG-2-202609-0002) o id de la devolución')
        parser.add_argument('--metodo', required=True,
                            help='MERCADO_PAGO (o MP) | EFECTIVO_CAJA (o EFECTIVO) | TRANSFERENCIA_BANCARIA (o TRANSFERENCIA)')
        parser.add_argument('--usuario', required=True, help='username de quien corrige (queda en la bitácora)')
        parser.add_argument('--operacion-mp', default='', dest='operacion_mp',
                            help='N° de operación de Mercado Pago (si no, el del cobro original)')
        parser.add_argument('--motivo', default='', help='Texto libre que queda en las observaciones')
        parser.add_argument('--apply', action='store_true', help='ESCRIBE los cambios (sin esto es dry-run)')
        parser.add_argument('--recalcular-arqueo', action='store_true', dest='recalcular_arqueo',
                            help='Re-snapshotea los teóricos del arqueo del día de imputación, si existe')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        from app.views_modulo_ventas import _calcular_cuadratura_data

        aplicar = options['apply']
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"[{'APLICAR' if aplicar else 'DRY-RUN'}] Corregir método de una Devolución de Dinero"))

        metodo = ALIAS_METODO.get(str(options['metodo']).strip().upper())
        if not metodo:
            raise CommandError(f"Método desconocido: {options['metodo']}")
        usuario = get_user_model().objects.filter(username=options['usuario']).first()
        if usuario is None:
            raise CommandError(f"No existe el usuario {options['usuario']}")

        ident = str(options['devolucion']).strip()
        qs = DevolucionGarantia.objects.select_related('dte_original', 'nota_credito', 'sucursal', 'receptor')
        dev = qs.filter(numero_operacion=ident).first() or (
            qs.filter(id=int(ident)).first() if ident.isdigit() else None)
        if dev is None:
            raise CommandError(f'No existe la devolución {ident}')
        nc = dev.nota_credito
        if dev.estado != 'NC_GENERADA' or nc is None:
            raise CommandError(f'{dev.numero_operacion} está {dev.get_estado_display()} (sin NC): no hay nada que corregir')

        pagos_nc = list(nc.dte_asociado.all())
        fecha = dev.fecha_imputacion_caja or next(
            (p.fecha_pago for p in pagos_nc if p.fecha_pago), None) or nc.fecha_emision
        monto = int(nc.monto_con_iva or 0)
        self.stdout.write(
            f'\n{dev.numero_operacion} · {dev.sucursal.alias} · cliente {dev.receptor.nombre} '
            f'({dev.receptor.rut}) {dev.receptor.correoVendedor or ""}'
        )
        self.stdout.write(
            f'  Documento {dev.dte_original.get_tipo_documento_display()} #{dev.dte_original.numero_documento} '
            f'→ NC #{nc.numero_documento} por {_plata(monto)}, imputada el {fecha:%d-%m-%Y}'
        )
        self.stdout.write(f'  Método actual: {dev.get_metodo_devolucion_display() or "-"}')
        for p in pagos_nc:
            self.stdout.write(
                f'    pago NC: {p.metodo_pago} {_plata(p.monto)} tipo={p.tipo_tarjeta or "-"} '
                f'voucher={p.voucher or "-"} fecha_pago={p.fecha_pago or "-"}'
            )

        # --- Mercado Pago: cobro original y su N° de operación ---
        numero = str(options['operacion_mp'] or '').strip()
        if metodo == 'MERCADO_PAGO':
            numero = self._numero_mp(dev, numero, guardar=aplicar)

        # --- Efecto en la caja de ese día ---
        anterior = dev.metodo_devolucion
        cuadratura = _calcular_cuadratura_data(dev.sucursal, fecha.strftime('%Y-%m-%d'))
        self.stdout.write(f'\nCaja de {dev.sucursal.alias} el {fecha:%d-%m-%Y} (en vivo):')
        for met, (clave, etiqueta) in BUCKET_CUADRATURA.items():
            actual = int(cuadratura.get(clave) or 0)
            despues = actual
            if met == anterior and met != metodo:
                despues += monto      # la NC deja de restar de acá
            if met == metodo and met != anterior:
                despues -= monto      # y pasa a restar de acá
            marca = '' if despues == actual else '   <-- cambia'
            self.stdout.write(f'  {etiqueta:<18} {_plata(actual):>12} -> {_plata(despues):>12}{marca}')
        self.stdout.write(f"  (VENTA TOTAL {_plata(cuadratura.get('venta_total'))} no cambia)")

        arqueo = ArqueoCaja.objects.filter(sucursal=dev.sucursal, fecha_arqueo=fecha).first()
        if arqueo is None:
            self.stdout.write('  Sin arqueo guardado ese día: el Resumen de Caja lo toma en vivo.')
        else:
            self.stdout.write(
                f'  Arqueo #{arqueo.id} en estado {arqueo.get_estado_display()}: sus teóricos son una '
                f'foto; ' + ('se recalculan (--recalcular-arqueo).' if options['recalcular_arqueo']
                             else 'agregue --recalcular-arqueo (o «Actualizar Teórico» en Revisión de Arqueos).'))

        if not aplicar:
            self.stdout.write(self.style.WARNING('\nDRY-RUN: no se escribió nada. Repita con --apply.'))
            return

        try:
            with transaction.atomic():
                res = service.cambiar_metodo_devolucion(
                    devolucion_id=dev.id, usuario=usuario, metodo_nuevo=metodo,
                    numero_operacion_mp=numero, motivo=options['motivo'],
                )
                cambios_arqueo = None
                if arqueo is not None and options['recalcular_arqueo']:
                    from app.views_modulo_ventas import _recalcular_teoricos_arqueo
                    cambios_arqueo = _recalcular_teoricos_arqueo(
                        arqueo, usuario=usuario, registrar_bitacora=True,
                        razon=f'corrección de método de {dev.numero_operacion} (NC {nc.numero_documento})',
                    )
        except DevolucionGarantiaError as e:
            raise CommandError(str(e))

        self.stdout.write(self.style.SUCCESS(
            f"\nOK: {dev.numero_operacion} ahora es {res['nuevo']}"
            + (f" (N° operación MP {res['numero_operacion_mp']})" if res['numero_operacion_mp'] else '')))
        self.stdout.write(f"  pago NC antes:   {res['pago_antes']}")
        self.stdout.write(f"  pago NC después: {res['pago_despues']}")
        if cambios_arqueo is not None:
            for campo, c in (cambios_arqueo.get('cambios') or {}).items():
                self.stdout.write(f"  arqueo {campo}: {_plata(c['antes'])} -> {_plata(c['despues'])}")
            if not cambios_arqueo.get('hay_cambios'):
                self.stdout.write('  arqueo: sin cambios en los teóricos')

    # ------------------------------------------------------------------
    def _numero_mp(self, dev, numero, guardar):
        """Muestra el cobro MP original y resuelve el N° de operación: el dado
        por parámetro, el que ya tiene el sistema o, si falta, el que responda
        la API de MP (en dry-run solo se consulta, no se guarda)."""
        from app.services.mercadopago_service import completar_numero_operacion

        dte = dev.dte_original
        mp = service.pago_mercadopago_dte(dte)
        if not mp['es_mp']:
            raise CommandError(
                f'El documento #{dte.numero_documento} no se cobró con Mercado Pago: '
                f'no se puede registrar la devolución por esa vía.')
        self.stdout.write(
            f"\n  Cobro Mercado Pago: {_plata(mp['monto_mp'])} {mp['medio']} ({mp['metodo_pago']})"
            + (f", ya devuelto por MP {_plata(mp['monto_devuelto'])}" if mp['monto_devuelto'] else ''))
        pagos_mp = [p for p in dte.dte_asociado.all() if service._es_metodo_mp(p.metodo_pago)]
        encontrado = mp['numero_operacion']
        for trx in service._transacciones_mp_venta(dte, pagos_mp):
            n = trx.payment_id_mp
            origen = 'guardado en el sistema'
            if not n:
                n = completar_numero_operacion(trx, guardar=guardar)
                origen = 'consultado a Mercado Pago' if n else 'MP no lo entregó'
            self.stdout.write(
                f'    cobro {trx.external_reference} {_plata(trx.monto)} {trx.estado}: '
                f'N° {n or "-"} ({origen})')
            encontrado = encontrado or n
        if numero:
            self.stdout.write(f'  N° de operación indicado a mano: {numero}')
            return numero
        if not encontrado:
            raise CommandError(
                'No se conoce el N° de operación de Mercado Pago: cópielo de la app o el panel de '
                'MP y páselo con --operacion-mp')
        self.stdout.write(f'  N° de operación a registrar: {encontrado}')
        return encontrado
