# -*- coding: utf-8 -*-
"""
Muestra qué órdenes de este sistema sigue teniendo ENCOLADAS Mercado Pago en
la máquina Point de una sucursal y, con --apply, cancela las que la API deja.

CONTEXTO (PAO3, 21-09-2026): la máquina rechazaba todo cobro nuevo con
`already_queued_order_on_terminal` estando en reposo. Mercado Pago admite UNA
operación encolada por terminal: una orden vieja que nadie terminó ni canceló
—aunque el sistema la tenga cerrada como ERROR/EXPIRADA/CANCELADA, o la haya
mandado OTRA caja que después perdió la máquina— sigue ocupando esa ranura.
Este comando pregunta a MP orden por orden (GET /v1/orders/{id}) cuáles
siguen vivas en ESA máquina.

QUÉ HACE:
  * Sin --apply: informe (tabla legible). La única escritura posible es
    reflejar como APROBADA una orden que MP reporta PAGADA y el sistema no
    tenía como aprobada (plata real: revisar en Dineros / huérfanos).
  * Con --apply: cancela en MP las que están en status `created` y van a esta
    máquina (POST /v1/orders/{id}/cancel) y las marca CANCELADA local. Las que
    ya están en la pantalla del terminal (at_terminal/processing) MP no las
    deja cancelar por API: se cancelan EN la máquina (Actualizar en la
    pantalla de reposo o botón rojo). JAMÁS crea órdenes ni cobra.

USO:
    # Ver qué tiene encolado la máquina de PAO3 (no escribe nada)
    python manage.py liberar_terminal_mp --sucursal PAO3

    # Mirar más atrás (default 10 días, tope 30)
    python manage.py liberar_terminal_mp --sucursal PAO3 --dias 20

    # Cancelar lo cancelable
    python manage.py liberar_terminal_mp --sucursal PAO3 --apply

    # Una caja puntual (id de MercadoPagoConfig) en vez de la principal
    python manage.py liberar_terminal_mp --config 7 --apply
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from app.models import MercadoPagoConfig, Sucursal
from app.services import mercadopago_service as mp

logger = logging.getLogger('app')


def _plata(v):
    return f"${int(v or 0):,}".replace(',', '.')


class Command(BaseCommand):
    help = ('Lista (y con --apply cancela) las órdenes que Mercado Pago sigue '
            'teniendo encoladas en la máquina Point de una sucursal')

    def add_arguments(self, parser):
        parser.add_argument('--sucursal', type=str, default=None,
                            help='Alias o id de la sucursal (usa su caja principal habilitada con máquina)')
        parser.add_argument('--config', type=int, default=None,
                            help='Id de una MercadoPagoConfig puntual (alternativa a --sucursal)')
        parser.add_argument('--dias', type=int, default=10,
                            help='Días hacia atrás a revisar (default 10, tope 30)')
        parser.add_argument('--apply', action='store_true',
                            help='CANCELA en Mercado Pago las órdenes cancelables (sin esto es dry-run)')

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        aplicar = options['apply']
        dias = min(max(int(options['dias'] or 10), 1), 30)
        config = self._resolver_config(options)
        etiqueta = 'APLICAR' if aplicar else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{etiqueta}] Liberar maquina Point {config.device_id} '
            f'({config.sucursal.alias} · {config.nombre}, caja {config.id}), ultimos {dias} dias'))

        try:
            informe = mp.liberar_terminal(config, dias=dias, aplicar=aplicar, usuario=None)
        except mp.MercadoPagoError as e:
            raise CommandError(e.mensaje)

        self._imprimir(informe, aplicar)

    # ------------------------------------------------------------------
    def _resolver_config(self, options):
        if options['config']:
            config = (MercadoPagoConfig.objects.select_related('sucursal')
                      .filter(id=options['config']).first())
            if not config:
                raise CommandError(f"No existe la caja MercadoPagoConfig {options['config']}")
        elif options['sucursal']:
            sucursal = self._resolver_sucursal(options['sucursal'])
            config = (MercadoPagoConfig.objects.select_related('sucursal')
                      .filter(sucursal=sucursal, habilitado=True)
                      .exclude(device_id='')
                      .order_by('-es_principal', 'id').first())
            if not config:
                raise CommandError(
                    f'{sucursal.alias} no tiene una caja Mercado Pago habilitada con máquina Point. '
                    'Usa --config <id> para una caja puntual.')
        else:
            raise CommandError('Falta --sucursal <alias> o --config <id>')
        if not config.device_id:
            raise CommandError(f'La caja {config.id} ({config.sucursal.alias} · {config.nombre}) '
                               'no tiene máquina Point asociada.')
        return config

    def _resolver_sucursal(self, valor):
        sucursal = None
        if str(valor).isdigit():
            sucursal = Sucursal.objects.filter(id=int(valor)).first()
        if not sucursal:
            sucursal = Sucursal.objects.filter(alias__iexact=str(valor)).first()
        if not sucursal:
            raise CommandError(f"Sucursal '{valor}' no encontrada (usa alias o id)")
        return sucursal

    # ------------------------------------------------------------------
    def _fila(self, o):
        maquina = '' if o.get('terminal_coincide') else ' (maquina no confirmada)'
        return (f"  {o['creado_en']}  {o['sucursal']} · {o['caja']:<18} ticket {o['correlativo'] or '-':<14} "
                f"{_plata(o['monto']):>12}  MP: {o['status_mp']:<16} sistema: {o['estado_local']:<10} "
                f"orden {o['order_id']}{maquina}")

    def _imprimir(self, informe, aplicar):
        encontradas = informe['encontradas']
        self.stdout.write(f"Ordenes consultadas: {informe.get('consultadas', 0)}")
        if not encontradas:
            self.stdout.write(self.style.SUCCESS(
                '\nMercado Pago no reporta ordenes de este sistema encoladas en esa maquina. '
                'Puede ser una impresion de cierre o algo creado fuera del sistema: en la maquina, '
                'apretar "Actualizar" en la pantalla de reposo y cancelar lo que baje; si no baja '
                'nada, apagarla y encenderla.'))
        else:
            self.stdout.write(self.style.WARNING(f'\nEncoladas en la maquina: {len(encontradas)}'))
            for o in encontradas:
                marca = 'CANCELABLE  ' if o['cancelable'] else 'EN PANTALLA '
                self.stdout.write(f'{marca}{self._fila(o)}')

        if informe['canceladas']:
            self.stdout.write(self.style.SUCCESS(f"\nCanceladas en Mercado Pago: {len(informe['canceladas'])}"))
            for o in informe['canceladas']:
                self.stdout.write(self._fila(o))
        if informe['no_cancelables']:
            self.stdout.write(self.style.WARNING(
                f"\nNo cancelables desde el sistema: {len(informe['no_cancelables'])} "
                '(estan en la pantalla del terminal: "Actualizar" en la pantalla de reposo o boton rojo EN la maquina)'))
            for o in informe['no_cancelables']:
                self.stdout.write(self._fila(o) + (f"  -> {o['motivo']}" if o.get('motivo') else ''))
        if informe['pagadas_detectadas']:
            self.stdout.write(self.style.WARNING(
                f"\nPAGADAS en Mercado Pago que el sistema no tenia aprobadas: {len(informe['pagadas_detectadas'])} "
                '(se marcaron APROBADA; revisar en Dineros Mercado Pago / cobros huerfanos)'))
            for o in informe['pagadas_detectadas']:
                self.stdout.write(self._fila(o) + f"  (estaba {o.get('estado_anterior', '?')})")
        if informe['errores']:
            self.stdout.write(self.style.ERROR(f"\nCon error: {len(informe['errores'])}"))
            for e in informe['errores']:
                self.stdout.write(f"  {e.get('order_id') or '-'}: {e.get('error')}")

        cancelables = [o for o in encontradas if o['cancelable']]
        if cancelables and not aplicar:
            self.stdout.write(f'\nPara cancelar las {len(cancelables)} cancelable(s), repetir el comando con --apply')
