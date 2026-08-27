"""
Diagnostico de un DTE "anulado" - COMANDO DE SOLO LECTURA.

NO escribe nada en la base de datos. Solo consulta y muestra un informe.
Se puede correr con total seguridad contra produccion.

Responde la pregunta: "elimine un documento y en gestion-DTE figura ANULADO,
se emitio la Nota de Credito o no?". Distingue los dos caminos que dejan un
DTE en `estado_dte='ANULADO'`:

  A) `eliminar_documento_venta` (boton "Eliminar" de Documentos de Ventas y de
     gestion-DTE): borrado logico INTERNO. Marca `descartado=True`, devuelve el
     stock con movimientos `referencia_externa='ELIMINACION_DTE_<folio>'` y
     anula el ticket. NO emite NC ni TXT: para el SII el folio sigue vigente.

  B) `anular_factura_dte` (boton "Anular / Nota de credito" de gestion-DTE):
     crea el DTE `NOTA DE CREDITO` con `documento_afectado` apuntando al
     original. Ese es el unico camino que si anula ante el SII.

Uso:
    python manage.py diagnosticar_dte_eliminado 259218
    python manage.py diagnosticar_dte_eliminado 259218 --sucursal NICK1
    python manage.py diagnosticar_dte_eliminado 259218,259219 --tipo "BOLETA ELECTRONICA"

Notas de alcance:
  - `numero_documento` NO es unico: el mismo folio puede existir en varias
    sucursales/empresas y en distintas series (boleta papel vs electronica).
    Por eso el comando lista TODOS los candidatos y no filtra por sucursal
    salvo que se lo pidas.
  - Los tickets se buscan por `sucursal + folio_dte`, que es el mismo criterio
    que usa el borrado logico para decidir a que venta devolverle el stock.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from app.models import (
    Dte,
    Dte_Productos,
    Movimientos_Producto,
    Sucursal,
    Ticket,
)

logger = logging.getLogger('app')

SEP = '=' * 78
SUB = '-' * 78


class Command(BaseCommand):
    help = (
        'Informa si un DTE marcado ANULADO tiene Nota de Credito emitida o si '
        'solo fue eliminado logicamente (solo lectura).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'folios',
            help='Folio o lista de folios separados por coma (ej: 259218 o 259218,259219)',
        )
        parser.add_argument(
            '--sucursal',
            default=None,
            help='Alias de sucursal para acotar (ej: NICK1). Por defecto muestra todas.',
        )
        parser.add_argument(
            '--tipo',
            default=None,
            help='Tipo de documento exacto para acotar (ej: "BOLETA ELECTRONICA").',
        )

    def handle(self, *args, **options):
        try:
            folios = [
                int(f.strip())
                for f in str(options['folios']).split(',')
                if f.strip()
            ]
        except ValueError:
            raise CommandError('Los folios deben ser numeros enteros.')
        if not folios:
            raise CommandError('Debe indicar al menos un folio.')

        sucursal_ids = None
        alias = options.get('sucursal')
        if alias:
            sucursal_ids = list(
                Sucursal.objects.filter(alias__iexact=alias.strip())
                .values_list('id', flat=True)
            )
            if not sucursal_ids:
                raise CommandError(f'No existe ninguna sucursal con alias "{alias}".')

        for folio in folios:
            self._informar_folio(folio, sucursal_ids, options.get('tipo'))

    # ------------------------------------------------------------------
    def _informar_folio(self, folio, sucursal_ids, tipo):
        self.stdout.write('')
        self.stdout.write(SEP)
        self.stdout.write(f'FOLIO {folio}')
        self.stdout.write(SEP)

        qs = Dte.objects.filter(numero_documento=folio)
        if sucursal_ids:
            qs = qs.filter(sucursal_id__in=sucursal_ids)
        if tipo:
            qs = qs.filter(tipo_documento__iexact=tipo.strip())
        documentos = list(
            qs.select_related('sucursal', 'emisor', 'receptor').order_by('id')
        )

        if not documentos:
            self.stdout.write(self.style.WARNING(
                'No hay ningun Dte con ese folio (con los filtros aplicados).'
            ))
            self._informar_tickets_sueltos(folio, sucursal_ids)
            return

        for dte in documentos:
            self._informar_dte(dte)

    # ------------------------------------------------------------------
    def _informar_dte(self, dte):
        suc = getattr(dte.sucursal, 'alias', None) or f'sucursal_id={dte.sucursal_id}'
        emisor = getattr(dte.emisor, 'nombre', None) or f'emisor_id={dte.emisor_id}'
        receptor = (
            getattr(dte.receptor, 'nombre', None)
            or (f'receptor_id={dte.receptor_id}' if dte.receptor_id else 'SIN RECEPTOR')
        )

        self.stdout.write('')
        self.stdout.write(SUB)
        self.stdout.write(
            f'DTE id={dte.id} | {dte.tipo_documento} #{dte.numero_documento} | {suc}'
        )
        self.stdout.write(SUB)
        self.stdout.write(f'  emisor              : {emisor}')
        self.stdout.write(f'  receptor            : {receptor}')
        self.stdout.write(f'  fecha_emision       : {dte.fecha_emision}')
        self.stdout.write(f'  monto_con_iva       : {dte.monto_con_iva}')
        self.stdout.write(f'  tipo_transaccion    : {dte.tipo_transaccion}')
        self.stdout.write(f'  estado_dte          : {dte.estado_dte}')
        self.stdout.write(f'  estado_pago         : {dte.estado_pago}')
        self.stdout.write(f'  es_nota_credito     : {dte.es_nota_credito}')
        self.stdout.write(f'  descartado          : {dte.descartado}')
        if dte.descartado:
            self.stdout.write(f'  descartado_por      : {dte.descartado_por}')
            self.stdout.write(f'  fecha_descarte      : {dte.fecha_descarte}')
            self.stdout.write(f'  motivo_descarte     : {dte.motivo_descarte}')

        # --- Notas de credito que apuntan a este documento -------------
        ncs = list(
            Dte.objects.filter(documento_afectado_id=dte.id)
            .order_by('id')
            .values(
                'id', 'tipo_documento', 'numero_documento', 'fecha_emision',
                'estado_dte', 'monto_con_iva', 'descartado', 'tipo_transaccion',
                'motivo_nc',
            )
        )
        self.stdout.write('')
        if ncs:
            self.stdout.write(self.style.SUCCESS(f'  NC asociadas: {len(ncs)}'))
            for nc in ncs:
                oculta = ' [OCULTA/descartada]' if nc['descartado'] else ''
                self.stdout.write(
                    f'    - NC id={nc["id"]} #{nc["numero_documento"]} '
                    f'{nc["fecha_emision"]} | {nc["estado_dte"]} | '
                    f'${nc["monto_con_iva"]} | {nc["tipo_transaccion"]}{oculta}'
                )
                if nc['motivo_nc']:
                    self.stdout.write(f'      motivo: {nc["motivo_nc"]}')
        else:
            self.stdout.write(self.style.ERROR(
                '  NC asociadas: NINGUNA (no se emitio Nota de Credito)'
            ))

        # --- Movimientos generados por el borrado logico ---------------
        referencia = f'ELIMINACION_DTE_{dte.numero_documento}'
        movs = list(
            Movimientos_Producto.objects
            .filter(referencia_externa=referencia)
            .order_by('id')
            .values(
                'id', 'ProductoTalla_id', 'cantidad', 'concepto',
                'tipo_movimiento', 'fecha', 'responsable',
            )
        )
        self.stdout.write('')
        if movs:
            unidades = sum(m['cantidad'] or 0 for m in movs)
            self.stdout.write(self.style.WARNING(
                f'  Stock devuelto por el borrado logico: {len(movs)} movimiento(s), '
                f'{unidades} unidad(es) - referencia "{referencia}"'
            ))
            for m in movs[:15]:
                self.stdout.write(
                    f'    - mov id={m["id"]} PT={m["ProductoTalla_id"]} '
                    f'cant={m["cantidad"]} {m["concepto"]}/{m["tipo_movimiento"]} '
                    f'{m["fecha"]} por {m["responsable"]}'
                )
            if len(movs) > 15:
                self.stdout.write(f'    ... y {len(movs) - 15} movimiento(s) mas')
        else:
            self.stdout.write(
                f'  Stock devuelto por el borrado logico: ninguno '
                f'(no hay movimientos con referencia "{referencia}")'
            )

        # --- Lineas del DTE --------------------------------------------
        lineas = Dte_Productos.objects.filter(dte=dte).count()
        self.stdout.write(f'  Lineas en Dte_Productos: {lineas}')

        # --- Tickets vinculados ----------------------------------------
        tickets = list(
            Ticket.objects
            .filter(sucursal_id=dte.sucursal_id, folio_dte=dte.numero_documento)
            .order_by('id')
            .values('id', 'correlativo', 'estado', 'tipo_dte', 'total', 'created_at')
        )
        self.stdout.write('')
        if tickets:
            self.stdout.write(f'  Tickets con ese folio en la sucursal: {len(tickets)}')
            for t in tickets:
                self.stdout.write(
                    f'    - Ticket id={t["id"]} #{t["correlativo"]} | {t["estado"]} | '
                    f'tipo_dte={t["tipo_dte"] or "-"} | ${t["total"]} | {t["created_at"]}'
                )
        else:
            self.stdout.write('  Tickets con ese folio en la sucursal: ninguno')

        self._veredicto(dte, ncs, movs, tickets)

    # ------------------------------------------------------------------
    def _veredicto(self, dte, ncs, movs, tickets):
        self.stdout.write('')
        self.stdout.write('  VEREDICTO')
        ncs_vigentes = [n for n in ncs if not n['descartado']]

        if ncs_vigentes:
            self.stdout.write(self.style.SUCCESS(
                '    Se emitio Nota de Credito: el documento esta anulado tambien '
                'ante el SII.'
            ))
        elif ncs:
            self.stdout.write(self.style.WARNING(
                '    Existe NC pero esta marcada como OCULTA (descartado=True): '
                'no figura en cuadratura ni en el listado. Revisa si el TXT se '
                'llego a enviar al SII.'
            ))
        elif dte.descartado or movs:
            self.stdout.write(self.style.ERROR(
                '    NO hay Nota de Credito. El documento fue ELIMINADO LOGICAMENTE '
                '(borrado interno del ERP). Para el SII el folio sigue vigente y '
                'entra al libro de ventas / F29 sin nada que lo reverse.'
            ))
            if movs:
                self.stdout.write(self.style.ERROR(
                    '    El stock YA volvio a bodega con el borrado: emitir ahora la '
                    'NC lo devolveria por segunda vez (doble ingreso).'
                ))
            if dte.estado_dte == 'ANULADO':
                self.stdout.write(self.style.ERROR(
                    '    Ademas `estado_dte=ANULADO` bloquea el boton de NC en '
                    'gestion-DTE (anular_factura_dte responde "El documento ya esta '
                    'anulado").'
                ))
        elif dte.estado_dte == 'ANULADO':
            self.stdout.write(self.style.WARNING(
                '    `estado_dte=ANULADO` sin NC y sin rastro de borrado logico: '
                'estado inconsistente, revisar a mano.'
            ))
        else:
            self.stdout.write('    Documento vigente, sin anulacion registrada.')

        tickets_anulados = [t for t in tickets if t['estado'] == 'ANULADO']
        if tickets_anulados:
            self.stdout.write(
                '    Ticket(s) anulado(s): '
                + ', '.join('#' + str(t['correlativo']) for t in tickets_anulados)
                + ' (fuera de la cuadratura).'
            )

    # ------------------------------------------------------------------
    def _informar_tickets_sueltos(self, folio, sucursal_ids):
        """Si no hay DTE, al menos mostrar si existe un ticket con ese folio."""
        qs = Ticket.objects.filter(folio_dte=folio)
        if sucursal_ids:
            qs = qs.filter(sucursal_id__in=sucursal_ids)
        tickets = list(qs.select_related('sucursal').order_by('id'))
        if not tickets:
            self.stdout.write('  Tampoco hay tickets con ese folio_dte.')
            return
        self.stdout.write(f'  Tickets con folio_dte={folio}: {len(tickets)}')
        for t in tickets:
            suc = getattr(t.sucursal, 'alias', None) or f'sucursal_id={t.sucursal_id}'
            self.stdout.write(
                f'    - Ticket id={t.id} #{t.correlativo} | {suc} | {t.estado} | '
                f'tipo_dte={t.tipo_dte or "-"} | ${t.total} | {t.created_at}'
            )
