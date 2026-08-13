"""
Revierte una corrida de `consolidar_cueca --apply` usando la SNAPSHOT que ese
comando guarda automaticamente antes de escribir (_cueca_snapshot_<fecha>.json).

Vuelve EXACTAMENTE al estado original:
  1. Cada `Producto_Talla` regresa a su ficha original (producto_id de la
     snapshot). El stock, el SKU y el kardex nunca se movieron — solo el
     puntero — asi que no hay nada mas que restaurar.
  2. Cada `Producto` recupera su articulo, descripcion, marca (atributo1) y
     su flag `excluir_de_analitica` originales.
  3. Las fichas CREADAS por la consolidacion (existen ahora, no estan en la
     snapshot) se BORRAN solo si quedaron sin tallas tras el paso 1; si por
     cualquier motivo aun tienen tallas, se reportan y NO se borran.

Es seguro correrlo aunque haya habido ventas despues de consolidar: las ventas
se registran contra la talla (SKU), y la talla vuelve con todo su historial a
la ficha original.

Lo UNICO que no revierte son cambios hechos por fuera de consolidar_cueca
(p. ej. precios corregidos a mano en Edicion Rapida despues de consolidar).

Uso:
    python manage.py revertir_consolidacion_cueca --snapshot _cueca_snapshot_X.json
    python manage.py revertir_consolidacion_cueca --snapshot _cueca_snapshot_X.json --apply
"""
import json

from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Producto, Producto_Talla


class Command(BaseCommand):
    help = ('Revierte consolidar_cueca a partir de su snapshot. '
            'Dry-run por defecto; --apply para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--snapshot', required=True,
                            help='Archivo _cueca_snapshot_*.json generado por '
                                 'consolidar_cueca --apply')
        parser.add_argument('--apply', action='store_true',
                            help='Escribe la reversa (sin esto solo reporta)')

    def handle(self, *args, **opts):
        aplicar = opts['apply']
        with open(opts['snapshot'], encoding='utf-8') as fh:
            snap = json.load(fh)
        self.stdout.write('Snapshot del %s: %d fichas, %d tallas   [%s]'
                          % (snap.get('fecha', '?'), len(snap['productos']),
                             len(snap['tallas']),
                             'APPLY' if aplicar else 'DRY-RUN'))

        ids_snap = {p['id'] for p in snap['productos']}

        with transaction.atomic():
            # 1) tallas de vuelta a su ficha original
            mov = 0
            for t in snap['tallas']:
                fila = Producto_Talla.objects.filter(id=t['id']).first()
                if fila is None:
                    self.stdout.write(self.style.WARNING(
                        '  talla id=%s ya no existe (¿borrada por fuera?)' % t['id']))
                    continue
                if fila.producto_id != t['producto_id']:
                    mov += 1
                    if aplicar:
                        Producto_Talla.objects.filter(id=t['id']) \
                            .update(producto_id=t['producto_id'])
            self.stdout.write('  tallas devueltas a su ficha original: %d' % mov)

            # 2) fichas: restaurar articulo/descripcion/marca/exclusion
            rest = 0
            for p in snap['productos']:
                fila = Producto.objects.filter(id=p['id']).values(
                    'articulo', 'descripcion', 'atributo1_id',
                    'excluir_de_analitica').first()
                if fila is None:
                    self.stdout.write(self.style.WARNING(
                        '  ficha id=%s ya no existe' % p['id']))
                    continue
                cambios = {}
                for campo in ('articulo', 'descripcion', 'atributo1_id',
                              'excluir_de_analitica'):
                    if fila[campo] != p[campo]:
                        cambios[campo] = p[campo]
                if cambios:
                    rest += 1
                    if aplicar:
                        Producto.objects.filter(id=p['id']).update(**cambios)
            self.stdout.write('  fichas restauradas (codigo/marca/exclusion): %d' % rest)

            # 3) fichas creadas por la consolidacion: borrar solo si quedaron vacias
            #    Se detectan por convencion de codigo (base-cc/n) + no estar en la
            #    snapshot. El borrado es seguro: sin tallas no hay stock, kardex
            #    ni ventas colgando (todo eso vive en Producto_Talla).
            creadas = Producto.objects.filter(
                articulo__iregex=r'^(3054|2918|3334)-\d{2}(/\d)?$'
            ).exclude(id__in=ids_snap)
            borradas, con_tallas = [], []
            for p in creadas:
                if Producto_Talla.objects.filter(producto_id=p.id).exists():
                    con_tallas.append(p.id)
                else:
                    borradas.append(p.id)
                    if aplicar:
                        p.delete()
            self.stdout.write('  fichas creadas por la consolidacion, borradas: %s'
                              % (borradas or '(ninguna)'))
            if con_tallas:
                self.stdout.write(self.style.ERROR(
                    '  !! fichas nuevas que AUN tienen tallas (NO se borran, '
                    'revisar a mano): %s' % con_tallas))

            if not aplicar:
                transaction.set_rollback(True)

        # verificacion post-reversa
        if aplicar:
            mal = 0
            for t in snap['tallas']:
                fila = Producto_Talla.objects.filter(id=t['id']) \
                    .values_list('producto_id', flat=True).first()
                if fila is not None and fila != t['producto_id']:
                    mal += 1
            if mal:
                self.stdout.write(self.style.ERROR(
                    'VERIFICACION: %d tallas NO quedaron en su ficha original' % mal))
            else:
                self.stdout.write(self.style.SUCCESS(
                    'VERIFICACION OK: todas las tallas de la snapshot estan en su '
                    'ficha original. Estado identico al previo a la consolidacion.'))
        else:
            self.stdout.write(self.style.WARNING(
                'DRY-RUN: nada quedo escrito. Ejecuta con --apply para revertir.'))
