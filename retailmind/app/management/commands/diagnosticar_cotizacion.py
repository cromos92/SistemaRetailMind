"""
Radiografía READ-ONLY de una cotización: documento, ticket, movimientos de
stock, cobertura por ítem y veredicto de reapertura.

Para qué sirve
--------------
Antes de tocar una cotización facturada (eliminar su documento, reabrirla,
despachar o revertir un despacho) hay que saber exactamente qué respalda cada
unidad. Este comando junta en una sola salida los cinco lugares donde vive esa
información y que hoy hay que cruzar a mano:

  1. `Cotizacion_Empresa`      — estado, folio, DTE enlazado, cuadratura.
  2. `Dte` + `Dte_Productos`   — el documento, sus líneas y sus NC.
  3. `Ticket`                  — **chequeo de ambigüedad de folio**: `folio_dte`
     no es único (las series se numeran por tipo de documento), así que se
     verifica que exista UN solo ticket candidato antes de que alguien apriete
     "Eliminar" y devuelva el stock de otra venta.
  4. `Movimientos_Producto`    — qué salió con el ticket (VENTA_DIRECTA) vs. qué
     salió por despacho diferido (DESPACHO_COTIZACION) y su neto.
  5. `evaluar_reapertura()`    — si se puede reabrir y, si no, por qué.

NO modifica nada. Ni una escritura.

Uso:
    python manage.py diagnosticar_cotizacion --cotizacion COT-202608-0001
    python manage.py diagnosticar_cotizacion --cotizacion 123          # por id
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from app.models import (
    Cotizacion_Empresa, Dte, Dte_Productos, Movimientos_Producto,
    Ticket, Ticket_Productos,
)
from app.services.cotizacion_reapertura import evaluar_reapertura
from app.utils_ventas import tipo_ticket_contradice_dte, tipo_ticket_para_dte

SEP = '=' * 100


class Command(BaseCommand):
    help = (
        'Radiografía completa de una cotización (documento, ticket, movimientos, '
        'cobertura y veredicto de reapertura). Solo lectura.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--cotizacion', type=str, required=True,
            help='numero_cotizacion (ej. COT-202608-0001) o ID numérico.',
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        cot = self._resolver(options['cotizacion'])

        self._seccion(f'1) COTIZACION {cot.numero_cotizacion}  (id={cot.id})')
        self.stdout.write(f'  sucursal        : {cot.sucursal.alias} (id={cot.sucursal_id})')
        self.stdout.write(f'  cliente         : {cot.cliente.nombre} / {cot.cliente.rut}')
        self.stdout.write(f'  estado          : {cot.estado}   facturada={cot.facturada}')
        self.stdout.write(f'  numero_factura  : {cot.numero_factura!r}')
        self.stdout.write(f'  dte_id          : {cot.dte_id}')
        self.stdout.write(f'  emision/validez : {cot.fecha_emision} / {cot.fecha_validez}')
        self.stdout.write(
            f'  estado_despacho : {cot.estado_despacho}   '
            f'validado={cot.despacho_validado}'
        )
        self.stdout.write(f'  total           : ${int(cot.total):,}'.replace(',', '.'))
        self.stdout.write(
            f'  uds facturadas={cot.unidades_facturadas}  '
            f'despachadas={cot.unidades_despachadas}  '
            f'pendientes={cot.unidades_pendientes_despacho}'
        )

        dte = cot.dte
        self._seccion('2) DOCUMENTO TRIBUTARIO')
        if not dte:
            self.stdout.write(self.style.WARNING('  SIN DTE ENLAZADO (cotización zombi)'))
        else:
            self.stdout.write(f'  dte_id          : {dte.id}')
            self.stdout.write(
                f'  tipo / folio    : {dte.tipo_documento} #{dte.numero_documento}')
            self.stdout.write(
                f'  estado_dte      : {dte.estado_dte}   descartado={dte.descartado}')
            self.stdout.write(f'  tipo_transaccion: {dte.tipo_transaccion}')
            self.stdout.write(
                f'  sucursal        : {getattr(dte.sucursal, "alias", None)} '
                f'(id={dte.sucursal_id})')
            self.stdout.write(
                f'  fecha / monto   : {dte.fecha_emision} / '
                f'${int(dte.monto_con_iva or 0):,}'.replace(',', '.'))
            self.stdout.write(f'  estado_pago     : {dte.estado_pago}')
            self.stdout.write(
                f'  ticket esperado : tipo_dte={tipo_ticket_para_dte(dte.tipo_documento)!r}')

            self.stdout.write('\n  --- Lineas del DTE ---')
            for dp in Dte_Productos.objects.filter(dte=dte).select_related('productoTalla'):
                sku = dp.productoTalla.sku if dp.productoTalla else None
                self.stdout.write(
                    f'    id={dp.id} sku={sku} cant={dp.stock} '
                    f'precio={int(dp.precio or 0)} costo={int(dp.costo or 0)} '
                    f'pend_despacho={dp.es_pendiente_despacho} '
                    f'cot_detalle_id={dp.cotizacion_detalle_id} '
                    f'desc="{(dp.descripcion or "")[:36]}"'
                )

            self.stdout.write('\n  --- NC / documentos hijos ---')
            hijos = list(Dte.objects.filter(documento_afectado=dte))
            if not hijos:
                self.stdout.write('    (ninguno)')
            for h in hijos:
                self.stdout.write(
                    f'    {h.tipo_documento} #{h.numero_documento} '
                    f'estado={h.estado_dte} descartado={h.descartado} '
                    f'monto=${int(h.monto_con_iva or 0):,}'.replace(',', '.')
                )

        # --- Ticket: el chequeo que evita devolver el stock equivocado -----
        self._seccion('3) TICKET(S) VINCULADO(S) — chequeo de AMBIGUEDAD de folio')
        if not dte:
            self.stdout.write('  (sin DTE, no aplica)')
        else:
            candidatos = list(
                Ticket.objects
                .filter(sucursal_id=dte.sucursal_id, folio_dte=dte.numero_documento)
                .order_by('id')
            )
            self.stdout.write(
                f'  tickets con (sucursal={dte.sucursal_id}, '
                f'folio_dte={dte.numero_documento}): {len(candidatos)}'
            )
            for t in candidatos:
                # Solo se marca cuando el tipo CONTRADICE. El default del modelo
                # ('TICKET' = "sin DTE") es neutro y lo trae el 100% de los
                # tickets con folio en producción: marcarlo sería ruido.
                marca = ''
                if tipo_ticket_contradice_dte(t.tipo_dte, dte.tipo_documento):
                    marca = '   <-- TIPO CONTRADICE AL DTE'
                elif t.tipo_dte and t.tipo_dte not in ('TICKET',):
                    marca = f'   (tipo neutro: {t.tipo_dte})'
                self.stdout.write(
                    f'    Ticket id={t.id} corr={t.correlativo} '
                    f'tipo_dte={t.tipo_dte!r} estado={t.estado} '
                    f'total=${int(t.total or 0):,}'.replace(',', '.') + f' fecha={t.fecha}{marca}'
                )
                for tp in (Ticket_Productos.objects
                           .filter(idTicket=t).select_related('ProductoTalla')):
                    sku = tp.ProductoTalla.sku if tp.ProductoTalla else None
                    self.stdout.write(
                        f'        sku={sku} cant={tp.stock} precio={int(tp.precio or 0)}')
            if len(candidatos) > 1:
                self.stdout.write(self.style.ERROR(
                    '  >>> AMBIGUO: el código sin el fix devolvería el stock de un '
                    'ticket que podría no ser este.'
                ))
            elif len(candidatos) == 1:
                self.stdout.write(self.style.SUCCESS('  >>> OK: un solo ticket candidato.'))
            else:
                self.stdout.write(self.style.WARNING(
                    '  >>> SIN TICKET: el stock se devolvería por Dte_Productos.'))

        # --- Movimientos ---------------------------------------------------
        self._seccion('4) MOVIMIENTOS DE STOCK')
        self.stdout.write('  a) Despacho diferido (referencia_externa = numero_cotizacion):')
        qs_desp = Movimientos_Producto.objects.filter(
            referencia_externa=cot.numero_cotizacion)
        if not qs_desp.exists():
            self.stdout.write('     (ninguno) → nada salió por despacho diferido')
        for m in qs_desp.select_related('ProductoTalla'):
            self.stdout.write(
                f'     id={m.id} {m.concepto} {m.tipo_movimiento} cant={m.cantidad} '
                f'sku={getattr(m.ProductoTalla, "sku", None)} fecha={m.fecha}'
            )
        neto = (qs_desp.filter(concepto='DESPACHO_COTIZACION')
                .aggregate(t=Sum('cantidad'))['t'] or 0)
        self.stdout.write(
            f'     NETO DESPACHO_COTIZACION = {neto} → unidades AFUERA sin revertir: '
            f'{max(0, -int(neto))}'
        )

        if dte:
            self.stdout.write('\n  b) Ligados al DTE:')
            movs_dte = list(Movimientos_Producto.objects.filter(dte=dte)
                            .select_related('ProductoTalla'))
            if not movs_dte:
                self.stdout.write('     (ninguno)')
            for m in movs_dte:
                self.stdout.write(
                    f'     id={m.id} {m.concepto} {m.tipo_movimiento} cant={m.cantidad} '
                    f'sku={getattr(m.ProductoTalla, "sku", None)} ref={m.referencia_externa!r}'
                )

            self.stdout.write('\n  c) Ligados al/los ticket(s):')
            hubo = False
            for t in Ticket.objects.filter(
                    sucursal_id=dte.sucursal_id, folio_dte=dte.numero_documento):
                for m in (Movimientos_Producto.objects.filter(ticket=t)
                          .select_related('ProductoTalla')):
                    hubo = True
                    self.stdout.write(
                        f'     ticket={t.correlativo} id={m.id} {m.concepto} '
                        f'{m.tipo_movimiento} cant={m.cantidad} '
                        f'sku={getattr(m.ProductoTalla, "sku", None)} '
                        f'ref={m.referencia_externa!r}'
                    )
            if not hubo:
                self.stdout.write('     (ninguno)')

            ref_elim = f'ELIMINACION_DTE_{dte.numero_documento}'
            self.stdout.write(
                f'\n  d) Reversas previas ({ref_elim}): '
                f'{Movimientos_Producto.objects.filter(referencia_externa=ref_elim).count()}'
            )

        # --- Items ---------------------------------------------------------
        self._seccion('5) ITEMS: cobertura y saldo por UNIDADES')
        for it in sorted(cot.items.all(), key=lambda i: i.numero_linea or 0):
            filas = list(it.skus_asociados.all())
            self.stdout.write(
                f'  [{it.numero_linea}] "{it.descripcion[:44]}"  '
                f'cant={it.cantidad} precio={int(it.precio_unitario)}'
            )
            self.stdout.write(
                f'       es_producto_pendiente={it.es_producto_pendiente} '
                f'sku_asignado_post_factura={it.sku_asignado_post_factura} '
                f'producto_existente_id={it.producto_existente_id}'
            )
            self.stdout.write(f'       sku_esperado={it.sku_producto_pendiente!r}')
            if not filas:
                self.stdout.write('       skus_asociados: (ninguno)')
            for f in filas:
                pt = f.producto_talla
                self.stdout.write(
                    f'       sku_rel id={f.id} sku={getattr(pt, "sku", None)} '
                    f'cant={f.cantidad} post_factura={f.asignado_post_factura} '
                    f'stock_actual={pt.stock if pt else None}'
                )
            self.stdout.write(
                f'       => cubiertas_al_facturar={it.unidades_cubiertas_al_facturar} '
                f'despachadas_post={it.unidades_despachadas_post_factura} '
                f'PENDIENTES={it.unidades_pendientes_despacho}'
            )

        # --- Veredicto -----------------------------------------------------
        self._seccion('6) VEREDICTO DE REAPERTURA')
        ev = evaluar_reapertura(cot)
        if ev['ok']:
            self.stdout.write(self.style.SUCCESS('  SE PUEDE REABRIR'))
        else:
            self.stdout.write(self.style.ERROR('  NO se puede reabrir todavía:'))
        for b in ev['bloqueos']:
            self.stdout.write(self.style.ERROR(f'    ✗ {b}'))
        for a in ev['avisos']:
            self.stdout.write(self.style.NOTICE(f'    · {a}'))

        # --- Contexto de la serie de folios --------------------------------
        if dte:
            self._seccion('7) SERIE DE FOLIOS (contexto: qué hueco dejaría eliminarlo)')
            cerca = (Dte.objects
                     .filter(sucursal_id=dte.sucursal_id,
                             tipo_documento=dte.tipo_documento,
                             numero_documento__gte=(dte.numero_documento or 0) - 3,
                             numero_documento__lte=(dte.numero_documento or 0) + 3)
                     .order_by('numero_documento'))
            for d in cerca:
                marca = '   <== ESTA' if d.id == dte.id else ''
                self.stdout.write(
                    f'  #{d.numero_documento} id={d.id} {d.fecha_emision} '
                    f'estado={d.estado_dte} descartado={d.descartado} '
                    f'monto=${int(d.monto_con_iva or 0):,}'.replace(',', '.') + marca
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('FIN — nada fue modificado.'))

    # ------------------------------------------------------------------
    def _seccion(self, titulo):
        self.stdout.write('')
        self.stdout.write(SEP)
        self.stdout.write(self.style.MIGRATE_HEADING(titulo))
        self.stdout.write(SEP)

    def _resolver(self, referencia):
        referencia = referencia.strip()
        base = (Cotizacion_Empresa.objects
                .select_related('dte', 'dte__sucursal', 'sucursal', 'cliente', 'vendedor')
                .prefetch_related('items__skus_asociados__producto_talla__producto'))
        cot = base.filter(numero_cotizacion__iexact=referencia).first()
        if cot:
            return cot
        if referencia.isdigit():
            cot = base.filter(pk=int(referencia)).first()
            if cot:
                return cot
        raise CommandError(f'No existe la cotización "{referencia}".')
