# -*- coding: utf-8 -*-
"""
Consume los `PendienteDespacho` que ya se despacharon por otra vía (hallazgo
CC-08).

La cola de pendientes (compra consolidada en el CD → "hay que mandar X a la
tienda Y") solo se descuenta con un POST aparte que hace emisionDTE.html
después de emitir la guía, y solo si el usuario precargó los pendientes. Todo
despacho hecho por otra pantalla deja el pendiente vivo: la lista de Emisión
DTE ofrece reenviar mercadería que ya salió (riesgo de doble despacho) y esos
registros bloquean la eliminación del producto (PENDIENTES_DESPACHO).

Regla (conservadora):
- Solo se ASIGNA a pendientes en PENDIENTE / PARCIAL, agrupados por
  (producto_talla, destino).
- Se cruzan con los TRASPASO_SALIDA (EGRESO, estado COMPLETADO o
  PENDIENTE_RECEPCION) del MISMO Producto_Talla hacia la MISMA sucursal
  destino, con fecha >= fecha (local) de creación del pendiente.
- Antes de asignar, lo ya registrado como despachado (cantidad_despachada) de
  TODOS los pendientes del grupo (también los DESPACHADO y los ANULADO que
  alcanzaron a despacharse) se descuenta del pozo, cada uno desde su propia
  fecha de creación, el más antiguo primero. Un despacho que ya consumió un
  pendiente vía emisionDTE no se le vuelve a asignar a otro: sin esto, el
  pendiente vivo quedaba marcado como despachado sin que su mercadería
  saliera (desaparecía de Emisión DTE y la tienda nunca la recibía).
  Si un despacho previo no aparece en su ventana, esas unidades se descuentan
  igual del resto del pozo (se prefiere dejar un pendiente vivo de más).
- Se asigna en orden: pendiente más antiguo primero; nunca se asigna más que
  lo que queda por despachar de cada pendiente.

Uso:
    python manage.py traspaso_consumir_pendientes_despacho           # dry-run
    python manage.py traspaso_consumir_pendientes_despacho --apply
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from app.models import Movimientos_Producto, PendienteDespacho

ESTADOS_VIVOS = ('PENDIENTE', 'PARCIAL')


def _descontar(pozo, unidades, desde=None):
    """Descuenta `unidades` del pozo [[fecha, restante], ...] (en orden de
    fecha) usando solo despachos con fecha >= `desde` (None = cualquiera).
    Devuelve lo que no se pudo descontar."""
    for m in pozo:
        if unidades <= 0:
            break
        if m[1] <= 0 or (desde is not None and m[0] < desde):
            continue
        usa = min(unidades, m[1])
        m[1] -= usa
        unidades -= usa
    return unidades


class Command(BaseCommand):
    help = ('Marca como despachados los PendienteDespacho cubiertos por TRASPASO_SALIDA reales '
            '(dry-run por defecto; --apply para escribir).')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplica los cambios. Sin esta bandera solo simula.')
        parser.add_argument('--detalle', action='store_true',
                            help='Lista cada pendiente afectado.')

    def handle(self, *args, **opts):
        aplicar = opts['apply']
        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no escribe)'
        self.stdout.write(self.style.WARNING(f'== traspaso_consumir_pendientes_despacho — {modo} =='))

        pendientes = list(
            PendienteDespacho.objects
            .filter(estado__in=ESTADOS_VIVOS)
            .order_by('created_at', 'id')
        )
        total_uds = sum(max(0, (p.cantidad or 0) - (p.cantidad_despachada or 0)) for p in pendientes)
        self.stdout.write(f'Pendientes vivos: {len(pendientes)} ({total_uds} u por despachar)')
        if not pendientes:
            return

        grupos = defaultdict(list)
        for p in pendientes:
            grupos[(p.producto_talla_id, p.sucursal_destino_id)].append(p)

        pt_ids = {k[0] for k in grupos}

        # Pendientes YA cerrados (DESPACHADO) o anulados del mismo grupo: su
        # cantidad_despachada ya consumió despachos reales del pozo.
        cerrados = defaultdict(list)
        for p in (
            PendienteDespacho.objects
            .filter(producto_talla_id__in=pt_ids, cantidad_despachada__gt=0)
            .exclude(estado__in=ESTADOS_VIVOS)
            .order_by('created_at', 'id')
        ):
            clave = (p.producto_talla_id, p.sucursal_destino_id)
            if clave in grupos:
                cerrados[clave].append(p)

        movs = defaultdict(list)   # (pt, destino) -> [[fecha, cantidad_restante]]
        for row in (
            Movimientos_Producto.objects
            .filter(
                ProductoTalla_id__in=pt_ids,
                concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO',
                estado__in=['COMPLETADO', 'PENDIENTE_RECEPCION'],
                sucursal_destino__isnull=False,
            )
            .order_by('fecha', 'hora', 'id')
            .values('ProductoTalla_id', 'sucursal_destino_id', 'fecha', 'cantidad')
        ):
            clave = (row['ProductoTalla_id'], row['sucursal_destino_id'])
            if clave in grupos:
                movs[clave].append([row['fecha'], abs(int(row['cantidad'] or 0))])

        asignaciones = []   # (pendiente, unidades)
        for clave, lista in grupos.items():
            pozo = movs.get(clave, [])
            if not pozo:
                continue
            todos = sorted(lista + cerrados.get(clave, []), key=lambda p: (p.created_at, p.id))
            fecha_min = min(timezone.localtime(p.created_at).date() for p in todos)
            pozo = [m for m in pozo if m[0] >= fecha_min]
            # 1) Lo ya registrado como despachado (vivos y cerrados) consumió
            #    despachos reales posteriores a la creación de cada pendiente:
            #    se descuenta del pozo antes de asignar nada.
            no_ubicado = 0
            for p in todos:
                no_ubicado += _descontar(pozo, int(p.cantidad_despachada or 0),
                                         timezone.localtime(p.created_at).date())
            # Conservador: lo que no calzó en su ventana se descuenta igual.
            _descontar(pozo, no_ubicado)
            # 2) Asignación a los pendientes vivos, el más antiguo primero.
            for p in lista:
                restante = max(0, (p.cantidad or 0) - (p.cantidad_despachada or 0))
                if restante <= 0:
                    continue
                desde = timezone.localtime(p.created_at).date()
                asignado = restante - _descontar(pozo, restante, desde)
                if asignado > 0:
                    asignaciones.append((p, asignado))

        completos = sum(1 for p, a in asignaciones
                        if a >= max(0, (p.cantidad or 0) - (p.cantidad_despachada or 0)))
        uds = sum(a for _p, a in asignaciones)
        self.stdout.write(f'  Cubiertos por despachos reales: {len(asignaciones)} pendientes '
                          f'({completos} completos, {len(asignaciones) - completos} parciales), {uds} u')
        self.stdout.write(f'  Siguen pendientes sin despacho que los cubra: '
                          f'{len(pendientes) - len(asignaciones)}')
        if opts['detalle']:
            for p, a in asignaciones:
                self.stdout.write(
                    f'    pendiente {p.id} pt={p.producto_talla_id} -> suc {p.sucursal_destino_id}: '
                    f'{p.cantidad_despachada}+{a}/{p.cantidad} (creado {timezone.localtime(p.created_at):%Y-%m-%d})'
                )

        if not aplicar:
            self.stdout.write(self.style.NOTICE('Dry-run: no se escribió nada. Usa --apply para aplicar.'))
            return

        aplicados = 0
        with transaction.atomic():
            for p, a in asignaciones:
                bloqueado = PendienteDespacho.objects.select_for_update().get(id=p.id)
                if bloqueado.estado not in ESTADOS_VIVOS:
                    continue
                restante = max(0, (bloqueado.cantidad or 0) - (bloqueado.cantidad_despachada or 0))
                usa = min(a, restante)
                if usa <= 0:
                    continue
                bloqueado.cantidad_despachada = (bloqueado.cantidad_despachada or 0) + usa
                bloqueado.recomputar_estado()
                bloqueado.save(update_fields=['cantidad_despachada', 'estado', 'updated_at'])
                aplicados += 1
        self.stdout.write(self.style.SUCCESS(f'Actualizados: {aplicados} pendientes.'))
