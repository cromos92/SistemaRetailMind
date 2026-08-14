# -*- coding: utf-8 -*-
"""
Marca TODOS los productos de una o más sucursales con excluir_de_analitica=True.

Complemento de vaciar_stock_sucursal: el vaciado deja el stock ACTUAL en 0,
pero el resumen de existencias con FECHA DE CORTE pasada reconstruye el stock
que había ese día (los ajustes del vaciado son de hoy), así que la sucursal
sigue apareciendo en vistas históricas. Este flag la saca de la analítica
completa —resumen-existencias (actual e histórico), dashboards, predicción de
compras— SIN tocar stock, kardex ni ventas.

SEGURIDAD:
- DRY-RUN POR DEFECTO: sin --aplicar solo muestra cuántos productos marcaría.
- Con --aplicar escribe snapshot (_exclusion_analitica_<ts>.json) con los ids
  que cambió (solo los que estaban en False → la reversión no pisa productos
  que ya estaban excluidos por otra razón).
- --revertir <snapshot.json> vuelve a False exactamente esos ids.

Uso:
    python manage.py excluir_sucursal_analitica --sucursal "EDEL FALLADOS" --sucursal NICK3
    python manage.py excluir_sucursal_analitica --sucursal "EDEL FALLADOS" --sucursal NICK3 --aplicar
    python manage.py excluir_sucursal_analitica --revertir _exclusion_analitica_20260814_180000.json
"""
import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import Producto, Sucursal


class Command(BaseCommand):
    help = 'Excluye de la analítica todos los productos de las sucursales indicadas (dry-run por defecto)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sucursal', action='append', default=[],
            help='Alias exacto o id de la sucursal (repetible)',
        )
        parser.add_argument(
            '--producto', action='append', default=[], type=int,
            help='Id de Producto a marcar dentro de esa sucursal (repetible). '
                 'Sin esto se toma la sucursal COMPLETA.',
        )
        parser.add_argument(
            '--incluir', action='store_true',
            help='Modo INVERSO: devuelve los productos a la analítica '
                 '(excluir_de_analitica=False) para que vuelvan a contar en los reportes.',
        )
        parser.add_argument('--aplicar', action='store_true',
                            help='Ejecuta de verdad (sin esto es dry-run)')
        parser.add_argument('--snapshot-dir', default='.',
                            help='Carpeta del snapshot de reversión (default: actual)')
        parser.add_argument('--revertir', default=None, metavar='SNAPSHOT.JSON',
                            help='Revierte una corrida anterior usando su snapshot')

    def handle(self, *args, **opts):
        if opts['revertir']:
            return self._revertir(opts['revertir'])

        if not opts['sucursal']:
            raise CommandError('Indica al menos una sucursal con --sucursal "ALIAS" (o su id)')

        sucursales = [self._resolver_sucursal(ref) for ref in opts['sucursal']]
        aplicar = opts['aplicar']
        productos_ids = opts['producto'] or None
        incluir = opts['incluir']

        # En modo --incluir se busca lo contrario: productos hoy EXCLUIDOS para
        # devolverlos a la analítica.
        estado_buscado = True if incluir else False
        estado_nuevo = not estado_buscado
        accion = 'devolución a la analítica' if incluir else 'exclusión de analítica'
        verbo = 'a devolver' if incluir else 'a marcar'

        alcance = f'SOLO productos {productos_ids}' if productos_ids else 'sucursal COMPLETA'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{'APLICANDO' if aplicar else 'DRY-RUN (nada se modifica)'} — {accion} ({alcance})"
        ))

        pendientes_por_suc = []
        for s in sucursales:
            productos = Producto.objects.filter(sucursal=s)
            if productos_ids:
                productos = productos.filter(id__in=productos_ids)
            total = productos.count()
            ya_en_destino = productos.filter(excluir_de_analitica=estado_nuevo).count()
            a_cambiar = productos.filter(excluir_de_analitica=estado_buscado)
            pendientes_por_suc.append((s, a_cambiar))
            self.stdout.write(
                f'  {s.alias} (id {s.id}): {total} productos en alcance, '
                f'{ya_en_destino} ya estaban así, {a_cambiar.count()} {verbo}'
            )
            for p in a_cambiar.values('id', 'articulo', 'descripcion')[:15]:
                self.stdout.write(
                    f"      · {p['id']:>7} {(p['articulo'] or '')[:24]:<24} "
                    f"{(p['descripcion'] or '')[:28]}"
                )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '\nDry-run. Para ejecutar de verdad agrega --aplicar'
            ))
            return

        ts = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        snapshot = {
            'fecha': timezone.localtime().isoformat(),
            'modo': 'incluir' if incluir else 'excluir',
            'estado_aplicado': estado_nuevo,
            'sucursales': [{'id': s.id, 'alias': s.alias} for s in sucursales],
            'productos_marcados': [],
        }

        with transaction.atomic():
            for s, a_cambiar in pendientes_por_suc:
                ids = list(a_cambiar.values_list('id', flat=True))
                snapshot['productos_marcados'].extend(ids)
                Producto.objects.filter(id__in=ids).update(excluir_de_analitica=estado_nuevo)

        prefijo = 'inclusion' if incluir else 'exclusion'
        ruta = os.path.join(opts['snapshot_dir'], f'_{prefijo}_analitica_{ts}.json')
        with open(ruta, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=1)

        destino = 'devueltos a la analítica' if incluir else 'excluidos de la analítica'
        self.stdout.write(self.style.SUCCESS(
            f"\nListo. {len(snapshot['productos_marcados'])} productos {destino}.\n"
            f"Snapshot de reversión: {ruta}\n"
            f"Para revertir: python manage.py excluir_sucursal_analitica --revertir {ruta}"
        ))

    def _resolver_sucursal(self, ref):
        ref = str(ref).strip()
        qs = Sucursal.objects.all()
        s = qs.filter(id=int(ref)).first() if ref.isdigit() else qs.filter(alias__iexact=ref).first()
        if s is None:
            parecidas = list(
                qs.filter(alias__icontains=ref.split()[0] if ref.split() else ref)
                .values_list('alias', flat=True)[:10]
            )
            raise CommandError(
                f'Sucursal "{ref}" no encontrada. '
                + (f'Parecidas: {", ".join(parecidas)}' if parecidas else 'Revisa el alias exacto.')
            )
        return s

    def _revertir(self, ruta):
        if not os.path.exists(ruta):
            raise CommandError(f'No existe el snapshot {ruta}')
        with open(ruta, encoding='utf-8') as f:
            snap = json.load(f)

        # `estado_aplicado` es lo que la corrida dejó grabado; revertir es ponerle
        # lo contrario. Los snapshots viejos no traen la clave y siempre fueron
        # exclusiones (True), así que ese es el default.
        estado_aplicado = snap.get('estado_aplicado', True)
        estado_previo = not estado_aplicado

        with transaction.atomic():
            restaurados = Producto.objects.filter(
                id__in=snap['productos_marcados']
            ).update(excluir_de_analitica=estado_previo)

        queda = 'excluidos de la analítica' if estado_previo else 'dentro de la analítica'
        self.stdout.write(self.style.SUCCESS(
            f"Revertido: {restaurados} productos quedan {queda} "
            f"(sucursales: {', '.join(s['alias'] for s in snap['sucursales'])})."
        ))
