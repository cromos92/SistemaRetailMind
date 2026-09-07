"""
Reasigna un pedido de ecommerce a otra sucursal.

Para cuando la tienda asignada no tiene el producto pero otra si. Deja el mismo
rastro que la reasignacion por UI: `sucursal`, historial REASIGNACION y la
metrica con `fue_reasignado`.

Reglas duras (no se pueden saltear):
  - Solo pedidos PENDIENTES. Uno ya FACTURADO no se mueve: el DTE ya salio con
    el RUT de la sucursal que lo emitio.
  - La sucursal destino DEBE ser de la MISMA empresa que la actual. Mover un
    pedido entre empresas cambiaria el RUT emisor del DTE.

Uso
---
    # Ver a donde se podria mover (que sucursales de la empresa tienen stock)
    python manage.py reasignar_sucursal_pedido_ecommerce --ticket RM-XXXXXXXX

    # Mover a una sucursal concreta
    python manage.py reasignar_sucursal_pedido_ecommerce \
        --ticket RM-XXXXXXXX --a-sucursal 5 --motivo SIN_STOCK --aplicar

    # Que elija sola la primera sucursal de la empresa que cubra TODO el pedido
    python manage.py reasignar_sucursal_pedido_ecommerce \
        --ticket RM-XXXXXXXX --auto --motivo SIN_STOCK --aplicar

    # Barrer todos los pedidos marcados SIN_STOCK y moverlos donde si hay
    python manage.py reasignar_sucursal_pedido_ecommerce --todos-sin-stock --auto --aplicar
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import (
    HistorialPedidoEcommerce,
    MetricaAsignacionPedido,
    PedidoEcommerce,
    Sucursal,
)


class Command(BaseCommand):
    help = 'Reasigna un pedido de ecommerce a otra sucursal de la MISMA empresa.'

    def add_arguments(self, parser):
        parser.add_argument('--ticket', type=str, help='numero_ticket_rm del pedido.')
        parser.add_argument('--pedido-id', type=int, help='id del PedidoEcommerce.')
        parser.add_argument(
            '--todos-sin-stock', action='store_true',
            help='Procesa TODOS los pedidos PENDIENTES en sub-estado SIN_STOCK.',
        )
        parser.add_argument('--a-sucursal', type=int, help='id de la sucursal destino.')
        parser.add_argument(
            '--auto', action='store_true',
            help='Elige la primera sucursal de la empresa (por id) que cubra todo el pedido.',
        )
        parser.add_argument(
            '--motivo', type=str, default='SIN_STOCK',
            choices=['SIN_STOCK', 'MANUAL', 'DISTRIBUCION_AUTO'],
            help='Motivo de la reasignacion (default: SIN_STOCK).',
        )
        parser.add_argument(
            '--usuario', type=str, default=None,
            help='username que queda en el historial (opcional).',
        )
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Escribe los cambios. Sin este flag el comando es dry-run.',
        )

    def handle(self, *args, **o):
        aplicar = o['aplicar']
        self.stdout.write(self.style.WARNING(
            f"== reasignar_sucursal_pedido_ecommerce - "
            f"{'APLICANDO' if aplicar else 'DRY-RUN (nada se escribe)'} =="
        ))

        pedidos = self._seleccionar(o)
        if pedidos is None:
            return
        if not pedidos:
            self.stdout.write(self.style.SUCCESS('No hay pedidos que procesar.'))
            return

        usuario = None
        if o['usuario']:
            usuario = get_user_model().objects.filter(username=o['usuario']).first()
            if usuario is None:
                self.stderr.write(self.style.ERROR(f"Usuario {o['usuario']} no existe."))
                return

        movidos = salteados = 0
        for pedido in pedidos:
            if self._procesar(pedido, o, usuario, aplicar):
                movidos += 1
            else:
                salteados += 1

        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            f"Total: {movidos} {'reasignados' if aplicar else 'a reasignar'}, {salteados} salteados."
        ))
        if not aplicar and movidos:
            self.stdout.write('Volve a correrlo con --aplicar para escribir los cambios.')

    # ── seleccion ──────────────────────────────────────────────────────────

    def _seleccionar(self, o):
        base = PedidoEcommerce.objects.select_related('sucursal', 'sucursal__empresa')
        if o['todos_sin_stock']:
            qs = base.filter(estado='PENDIENTE', sub_estado='SIN_STOCK').order_by('fecha_recepcion')
            self.stdout.write(f"Pedidos PENDIENTES en SIN_STOCK: {qs.count()}\n")
            return list(qs)
        if o['ticket']:
            p = base.filter(numero_ticket_rm=o['ticket'].strip()).first()
        elif o['pedido_id']:
            p = base.filter(id=o['pedido_id']).first()
        else:
            self.stderr.write(self.style.ERROR(
                'Indica --ticket, --pedido-id o --todos-sin-stock.'))
            return None
        if p is None:
            self.stderr.write(self.style.ERROR('Pedido no encontrado.'))
            return None
        return [p]

    # ── stock ──────────────────────────────────────────────────────────────

    def _cobertura_por_sucursal(self, pedido):
        """[(sucursal, cubre_todo, detalle)] para las sucursales de la empresa.

        Reusa `_validar_items_pedido` de views_ecommerce: es la MISMA funcion con
        la que la ingesta decide ASIGNADO vs RECIBIDO y con la que el picking
        marca SIN_STOCK. Si este comando usara su propia consulta de stock,
        podria decir "aca hay" donde la tienda ve un quiebre.
        """
        from app.views_ecommerce import _validar_items_pedido

        empresa = getattr(pedido.sucursal, 'empresa', None)
        if empresa is None:
            return []
        salida = []
        for suc in Sucursal.objects.filter(empresa=empresa).order_by('id'):
            items_val = _validar_items_pedido(pedido, sucursal=suc)
            cubre = bool(items_val) and all(iv['encontrado'] for iv in items_val)
            detalle = {
                (it.get('sku') if isinstance(it, dict) else None) or '?': iv.get('stock_disponible', 0)
                for it, iv in zip(pedido.items or [], items_val)
            }
            salida.append((suc, cubre, detalle))
        return salida

    # ── proceso ────────────────────────────────────────────────────────────

    def _procesar(self, pedido, o, usuario, aplicar):
        etiqueta = (f"{pedido.numero_ticket_rm} ({pedido.canal_origen} "
                    f"{pedido.numero_pedido_origen or pedido.numero_pedido_canal})")
        self.stdout.write(self.style.HTTP_INFO(f"\n-- {etiqueta}"))
        empresa = getattr(pedido.sucursal, 'empresa', None)
        self.stdout.write(
            f"   actual: sucursal {pedido.sucursal_id}/"
            f"{getattr(pedido.sucursal, 'nombre', None) or getattr(pedido.sucursal, 'alias', '')} "
            f"empresa={getattr(empresa, 'rut', '?')} estado={pedido.estado}/{pedido.sub_estado}"
        )

        if pedido.estado != 'PENDIENTE':
            self.stdout.write(self.style.ERROR(
                f"   ! estado {pedido.estado}: no se mueve (si ya se facturo, el DTE ya salio)."))
            return False

        cobertura = self._cobertura_por_sucursal(pedido)
        for suc, cubre, detalle in cobertura:
            marca = '<- actual' if suc.id == pedido.sucursal_id else ''
            self.stdout.write(
                f"      sucursal {suc.id:<4} {(suc.nombre or suc.alias or ''):<8} "
                f"cubre_todo={'SI' if cubre else 'no':<3} stock={detalle} {marca}"
            )

        destino = self._elegir_destino(pedido, o, cobertura)
        if destino is None:
            return False

        self.stdout.write(self.style.SUCCESS(
            f"   -> mover a sucursal {destino.id}/{destino.nombre or destino.alias}"
        ))
        if aplicar:
            self._mover(pedido, destino, o['motivo'], usuario)
        return True

    def _elegir_destino(self, pedido, o, cobertura):
        empresa = getattr(pedido.sucursal, 'empresa', None)

        if o['a_sucursal']:
            destino = Sucursal.objects.select_related('empresa').filter(id=o['a_sucursal']).first()
            if destino is None:
                self.stdout.write(self.style.ERROR('   ! sucursal destino no existe.'))
                return None
            # Guardarrail duro: nunca cruzar empresas (cambiaria el RUT del DTE).
            if getattr(destino, 'empresa_id', None) != getattr(empresa, 'id', None):
                self.stdout.write(self.style.ERROR(
                    f"   ! sucursal {destino.id} es de la empresa "
                    f"{getattr(destino.empresa, 'rut', '?')} y el pedido es de "
                    f"{getattr(empresa, 'rut', '?')}. NO se mueve entre empresas."
                ))
                return None
            if destino.id == pedido.sucursal_id:
                self.stdout.write('   = ya esta en esa sucursal.')
                return None
            return destino

        if not o['auto']:
            self.stdout.write('   (sin --a-sucursal ni --auto: solo se listo el stock)')
            return None

        for suc, cubre, _detalle in cobertura:
            if suc.id == pedido.sucursal_id:
                continue
            if cubre:
                return suc
        self.stdout.write(self.style.ERROR(
            '   ! ninguna otra sucursal de la empresa cubre el pedido completo.'))
        return None

    @transaction.atomic
    def _mover(self, pedido, destino, motivo, usuario):
        anterior = pedido.sucursal
        sub_anterior = pedido.sub_estado

        pedido.sucursal = destino
        # Vuelve a la cola de picking de la nueva tienda. SIN_STOCK solo puede
        # volver a ASIGNADO (ver SUB_ESTADOS_BLOQUEADOS_PICKING en el modelo).
        pedido.sub_estado = 'ASIGNADO'
        if motivo == 'SIN_STOCK':
            pedido.sin_stock_motivo = ''
        campos = ['sucursal', 'sub_estado']
        if motivo == 'SIN_STOCK':
            campos.append('sin_stock_motivo')
        pedido.save(update_fields=campos)

        HistorialPedidoEcommerce.objects.create(
            pedido=pedido,
            estado_anterior=pedido.estado,
            estado_nuevo=pedido.estado,
            sub_estado_anterior=sub_anterior,
            sub_estado_nuevo='ASIGNADO',
            sucursal_anterior=anterior,
            sucursal_nueva=destino,
            usuario=usuario,
            tipo_evento='REASIGNACION',
            motivo=(
                f'Reasignado de {anterior.nombre or anterior.alias} a '
                f'{destino.nombre or destino.alias} ({motivo})'
            ),
        )
        MetricaAsignacionPedido.objects.create(
            pedido=pedido,
            sucursal_asignada=destino,
            fue_reasignado=True,
            motivo_reasignacion=motivo,
            todos_items_con_stock=True,
            items_sin_stock=0,
        )
