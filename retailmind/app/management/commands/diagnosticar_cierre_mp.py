"""
Diagnóstico SOLO LECTURA del cierre de Mercado Pago que imprime la máquina Point.

No escribe NADA en la base de datos ni en Mercado Pago (la verificación contra
la API es opcional y es un GET).

    python manage.py diagnosticar_cierre_mp
    python manage.py diagnosticar_cierre_mp --sucursal PAO1
    python manage.py diagnosticar_cierre_mp --sucursal PAO1 --fecha 2026-09-20
    python manage.py diagnosticar_cierre_mp --sucursal PAO1 --verificar-mp
    python manage.py diagnosticar_cierre_mp --sucursal PAO1 --papel

Para qué sirve
--------------
El botón «Imprimir cierre en la máquina» arma el papel con DOS fuentes:

  1. Cobros Mercado Pago  -> de la CAJA elegida (MercadoPagoConfig).
  2. VENTA DEL DÍA        -> de la SUCURSAL de esa caja, con la misma
                             cuadratura del arqueo (`_calcular_cuadratura_data`).

Si la caja elegida es de otra tienda, el papel sale con la venta de esa OTRA
tienda. Este comando muestra, sin imprimir nada:

  - todas las cajas MP, con cuál resuelve cada sucursal y si le falta la
    principal (la causa de que el selector cayera en la caja de otra tienda);
  - la venta del día que el papel mostraría para la sucursal pedida, medio por
    medio, contra el total global;
  - con --verificar-mp, el control contra la API (ojo: el total «MP» puede
    incluir cobros de otras cajas de la MISMA cuenta que MP no deja atribuir).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import MercadoPagoConfig, Sucursal, TransaccionMercadoPago


def _plata(v):
    return f"${int(v or 0):,}".replace(',', '.')


class Command(BaseCommand):
    help = 'Diagnostica (solo lectura) qué imprimiría el cierre MP de una sucursal.'

    def add_arguments(self, parser):
        parser.add_argument('--sucursal', default=None,
                            help='Alias o id de la sucursal (ej. PAO1). Sin esto solo lista las cajas.')
        parser.add_argument('--fecha', default=None,
                            help='Fecha YYYY-MM-DD (default: hoy)')
        parser.add_argument('--verificar-mp', action='store_true',
                            help='Consulta la API de Mercado Pago (GET, solo lectura)')
        parser.add_argument('--papel', action='store_true',
                            help='Muestra el texto exacto del ticket térmico')

    # ---------------------------------------------------------------- cajas
    def _listar_cajas(self):
        from app.views_mercadopago import caja_mp_de_sucursal

        self.stdout.write(self.style.MIGRATE_HEADING('\n=== CAJAS MERCADO PAGO ==='))
        cajas = list(MercadoPagoConfig.objects.select_related(
            'sucursal', 'sucursal__empresa', 'cuenta', 'cuenta__empresa'
        ).order_by('sucursal__alias', 'nombre'))
        if not cajas:
            self.stdout.write('  (no hay cajas MP configuradas)')
            return
        self.stdout.write(f"  {'SUCURSAL':<10} {'CAJA':<22} {'HAB':<4} {'MAQUINA':<8} "
                          f"{'PPAL':<5} CUENTA")
        for c in cajas:
            cuenta = (c.cuenta.empresa.nombre if c.cuenta_id
                      else f'(auto: {c.sucursal.empresa.nombre})')
            self.stdout.write(
                f"  {(c.sucursal.alias or '?'):<10} {c.nombre[:22]:<22} "
                f"{'si' if c.habilitado else 'NO':<4} {'si' if c.device_id else 'no':<8} "
                f"{'*' if c.es_principal else '-':<5} {str(cuenta)[:30]}"
            )

        # Sucursales cuyo selector podía caer en la caja de otra tienda
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== QUÉ CAJA RESUELVE CADA SUCURSAL (regla del cierre) ==='))
        sucursales = Sucursal.objects.filter(
            id__in={c.sucursal_id for c in cajas}).order_by('alias')
        for suc in sucursales:
            resuelta = caja_mp_de_sucursal(suc.id, con_maquina=True)
            preselec = caja_mp_de_sucursal(suc.id)
            sin_ppal = not any(c.es_principal for c in cajas if c.sucursal_id == suc.id)
            detalle = (f"{resuelta.nombre} (id {resuelta.id})" if resuelta
                       else 'NINGUNA con máquina Point')
            self.stdout.write(f"  {str(suc.alias or suc.id):<10} imprime con: {detalle}")
            if preselec and (not resuelta or preselec.id != resuelta.id):
                self.stdout.write(f"  {'':<10} preselecciona: {preselec.nombre} (id {preselec.id})")
            if sin_ppal:
                self.stdout.write(self.style.WARNING(
                    f"  {'':<10} OJO: ninguna caja de esta sucursal esta marcada como "
                    f"principal. Antes del fix su selector caia en la PRIMERA caja de "
                    f"la lista (la de otra tienda) y el cierre salia con la venta de "
                    f"esa otra tienda."))

        # Cajas que comparten cuenta: el control contra la API las mezcla
        por_cuenta = {}
        for c in cajas:
            clave = c.cuenta_id or f'empresa:{c.sucursal.empresa_id}'
            por_cuenta.setdefault(clave, []).append(c)
        compartidas = {k: v for k, v in por_cuenta.items() if len(v) > 1}
        if compartidas:
            self.stdout.write(self.style.MIGRATE_HEADING(
                '\n=== CUENTAS MP COMPARTIDAS POR VARIAS CAJAS ==='))
            self.stdout.write(
                '  MP no deja filtrar pagos por terminal: un cobro sin identificar\n'
                '  puede sumar al bloque «CONTROL vs MERCADO PAGO» de cualquiera de\n'
                '  estas cajas y aparecer como FALTAN $X. El papel ya lo advierte.')
            for clave, grupo in compartidas.items():
                nombres = ', '.join(f"{c.sucursal.alias}/{c.nombre}" for c in grupo)
                self.stdout.write(f"  cuenta {clave}: {nombres}")

    # ----------------------------------------------------------- una fecha
    def _diagnosticar(self, sucursal, fecha, verificar, papel):
        from app.services import mercadopago_service as mp
        from app.views_mercadopago import caja_mp_de_sucursal
        from app.views_modulo_ventas import _calcular_cuadratura_data

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== CIERRE QUE SALDRIA PARA {sucursal.alias} EL {fecha} ==='))
        config = caja_mp_de_sucursal(sucursal.id, con_maquina=True)
        if not config:
            self.stdout.write(self.style.WARNING(
                '  Esta sucursal no tiene caja MP habilitada con maquina Point: '
                'el boton responde con ese error y no imprime.'))
        else:
            self.stdout.write(f"  Caja: {config.nombre} (id {config.id})  maquina {config.device_id}")

        # 1) Cobros MP de la caja
        if config:
            trxs = list(TransaccionMercadoPago.objects
                        .filter(config=config, creado_en__date=fecha,
                                tipo='VENTA', estado='APROBADA')
                        .exclude(correlativo_ticket__startswith='PRUEBA-'))
            total_mp = sum(t.monto for t in trxs)
            self.stdout.write(f"\n  COBROS MERCADO PAGO DE ESTA CAJA: {len(trxs)} por {_plata(total_mp)}")
            por_canal = {}
            for t in trxs:
                canal = t.canal or 'QR'
                por_canal.setdefault(canal, [0, 0])
                por_canal[canal][0] += 1
                por_canal[canal][1] += t.monto
            for canal, (n, monto) in sorted(por_canal.items()):
                self.stdout.write(f"    {canal:<8} {n:>4} cobros  {_plata(monto):>14}")

        # 2) Venta del día de la SUCURSAL (lo que más se nota en el papel)
        cua = _calcular_cuadratura_data(sucursal, fecha)
        medios = [
            ('EFECTIVO', cua.get('total_efectivo', 0)),
            ('TRANSBANK DEBITO', cua.get('total_tarjeta_debito', 0)),
            ('TRANSBANK CREDITO', cua.get('total_visa_mc_amex', 0)),
            ('MP POS DEBITO', cua.get('total_mercadopago_pos_debito', 0)),
            ('MP POS CREDITO', cua.get('total_mercadopago_pos_credito', 0)),
            ('MP POS QR/OTROS', cua.get('total_mercadopago_pos_otros', 0)),
            ('TRANSFERENCIA', cua.get('total_transferencia', 0)),
            ('TARJETA COMERCIAL', cua.get('total_tarjetas_comerciales', 0)),
            ('VENTA INTERNET', cua.get('total_venta_internet', 0)),
            ('GIFT CARD', cua.get('total_giftcard', 0)),
            ('CRED. TRABAJADOR', cua.get('total_credito_trabajador', 0)),
            ('CRED. EXTERNO', cua.get('total_credito_externo', 0)),
            ('CONVENIO', cua.get('total_convenio', 0)),
            ('ORDEN COMPRA', cua.get('total_orden_compra', 0)),
            ('CHEQUE', cua.get('total_cheque', 0)),
        ]
        self.stdout.write(f"\n  VENTA DEL DIA DE {sucursal.alias} (misma cuadratura del arqueo):")
        suma_medios = 0
        for nombre, monto in medios:
            monto = int(monto or 0)
            if not monto:
                continue
            suma_medios += monto
            self.stdout.write(f"    {nombre:<20} {_plata(monto):>14}")
        nc = int(cua.get('total_notas_credito', 0) or 0)
        if nc:
            self.stdout.write(f"    {'NOTAS DE CREDITO':<20} {('-' + _plata(nc)):>14}")
        self.stdout.write(f"    {'-' * 34}")
        self.stdout.write(f"    {'SUMA MEDIOS':<20} {_plata(suma_medios - nc):>14}")
        self.stdout.write(f"    {'TOTAL GLOBAL (papel)':<20} {_plata(cua.get('venta_total')):>14}")
        self.stdout.write(f"    {'  tickets':<20} {_plata(cua.get('total_tickets')):>14}"
                          f"   ({cua.get('cantidad_tickets')} tickets)")
        self.stdout.write(f"    {'  boletas elec.':<20} {_plata(cua.get('total_boletas_electronicas')):>14}")
        self.stdout.write(f"    {'  boletas papel':<20} {_plata(cua.get('total_boletas_papel')):>14}")
        self.stdout.write(f"    {'  facturas':<20} {_plata(cua.get('total_facturas')):>14}")
        diferencia = (suma_medios - nc) - int(cua.get('venta_total') or 0)
        if diferencia:
            self.stdout.write(self.style.WARNING(
                f"    Medios y documentos difieren en {_plata(abs(diferencia))}: es la misma "
                f"alerta «documentos vs medios» del Resumen de Caja, no un problema del papel."))

        # 2b) Tickets re-fechados: `Ticket.fecha` es auto_now, así que un
        # `ticket.save()` completo sobre una venta vieja la mueve al día de hoy
        # y la cuadratura (y el papel) la cuentan como venta de hoy.
        from app.models import Ticket
        refechados = [
            t for t in Ticket.objects.filter(sucursal=sucursal, fecha=fecha, estado='PAGADO')
            if t.created_at and timezone.localtime(t.created_at).date() != t.fecha
        ]
        if refechados:
            monto = sum(t.total or 0 for t in refechados)
            self.stdout.write(self.style.WARNING(
                f"\n  OJO: {len(refechados)} ticket(s) por {_plata(monto)} se CREARON otro "
                f"dia y quedaron con fecha {fecha}."))
            self.stdout.write(
                '  `Ticket.fecha` es auto_now: cualquier save() completo de un ticket\n'
                '  viejo lo re-fecha a hoy y su venta se suma al dia de hoy.')
            for t in sorted(refechados, key=lambda x: -(x.total or 0))[:10]:
                creado = timezone.localtime(t.created_at).strftime('%Y-%m-%d %H:%M')
                self.stdout.write(f"    ticket {t.correlativo:<10} {_plata(t.total):>12}"
                                  f"   creado {creado}")

        # 3) Control contra la API (opcional)
        if verificar and config:
            self.stdout.write('\n  Consultando la API de Mercado Pago (solo lectura)...')
            control = mp.conciliar_cierre_mp(config, fecha)
            if not control.get('ok'):
                self.stdout.write(self.style.ERROR(f"    No se pudo verificar: {control.get('error')}"))
            else:
                self.stdout.write(f"    {'MEDIO':<18} {'SISTEMA':>12} {'MP':>12}")
                for m in control['medios']:
                    self.stdout.write(f"    {m['etiqueta'][:18]:<18} {_plata(m['sistema']):>12} "
                                      f"{_plata(m['mp']):>12}{'  *' if m['diferencia'] else ''}")
                self.stdout.write(f"    {'TOTAL':<18} {_plata(control['sistema_total']):>12} "
                                  f"{_plata(control['mp_total']):>12}")
                if control.get('hay_sin_atribuir') and (control.get('cajas_en_la_cuenta') or 1) > 1:
                    self.stdout.write(self.style.WARNING(
                        f"    Hay cobros que MP no deja atribuir y la cuenta tiene "
                        f"{control['cajas_en_la_cuenta']} cajas: parte de esa diferencia "
                        f"puede ser de otra tienda."))
                for d in control.get('sin_registro', [])[:10]:
                    self.stdout.write(f"      sin registro: {d['hora']} {_plata(d['monto'])} "
                                      f"{d['medio']} pago {d['payment_id']}"
                                      f"{'' if d['atribuible'] else '  (SIN ATRIBUIR)'}")

        # 4) Papel exacto
        if papel and config:
            vacio = {'cobros': 0, 'monto': 0, 'devoluciones': 0,
                     'monto_devuelto': 0, 'comisiones': 0}
            caja = {'caja': config.nombre, 'sucursal': sucursal.alias,
                    'QR': dict(vacio), 'POINT': dict(vacio),
                    'medios': {}, 'total_neto': 0,
                    'dia_sucursal': [(n, int(m or 0)) for n, m in medios if int(m or 0)],
                    'dia_nc': nc, 'dia_total_global': int(cua.get('venta_total') or 0),
                    'responsable': 'DIAGNOSTICO'}
            texto = mp.contenido_cierre_terminal(caja, fecha)
            self.stdout.write(self.style.MIGRATE_HEADING('\n=== TEXTO DEL TICKET ==='))
            for renglon in (texto.replace('{center}', '').replace('{w}', '')
                            .replace('{s}', '').split('{br}')):
                self.stdout.write('  |' + renglon)

    # --------------------------------------------------------------- handle
    def handle(self, *args, **opts):
        fecha = opts['fecha'] or str(timezone.localdate())
        self._listar_cajas()

        clave = opts['sucursal']
        if not clave:
            self.stdout.write('\nPasa --sucursal PAO1 para ver que imprimiria esa tienda.')
            return
        sucursal = Sucursal.objects.filter(alias__iexact=clave).first()
        if sucursal is None and str(clave).isdigit():
            sucursal = Sucursal.objects.filter(id=int(clave)).first()
        if sucursal is None:
            self.stdout.write(self.style.ERROR(f'No existe la sucursal «{clave}».'))
            return
        self._diagnosticar(sucursal, fecha, opts['verificar_mp'], opts['papel'])
