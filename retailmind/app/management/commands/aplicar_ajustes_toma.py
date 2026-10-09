# -*- coding: utf-8 -*-
"""Aplica (o reanuda) los ajustes de stock de una toma de inventario APROBADA,
de forma SÍNCRONA en el proceso del comando.

Por qué existe. Desde la pantalla, «Aplicar ajustes» lanza un hilo daemon dentro
del worker de gunicorn. Un deploy de Railway, un OOM o un reinicio matan ese hilo
a mitad del bucle (una toma de tienda son miles de transacciones) y la toma queda
en APLICANDO con la tarea EN_PROCESO para siempre. Este comando hace el mismo
trabajo sin depender del worker web: se corre en la consola del servidor, se
puede cortar y volver a correr (cada detalle guarda lo que ya movió en
`diferencia_aplicada`, y se relee bajo lock antes de tocar stock, así que nunca
aplica dos veces; con «Ajustar stock ya» previo, aplica solo lo que falta).

Usa EXACTAMENTE el mismo camino que la pantalla:
  - `_iniciar_tarea_ajustes`: bloquea la fila de TareaAplicacionAjustes y pasa a
    EN_PROCESO con un UPDATE condicional (solo un llamador gana). Si la tarea ya
    está EN_PROCESO se detiene, salvo `--reanudar` y que esté huérfana (más de 30
    min sin avance).
  - `_ejecutar_ajustes_background`: aplica sobrantes (lote FIFO + kardex
    AJUSTE_INVENTARIO_ENTRADA) y faltantes (consumo FIFO + AJUSTE_INVENTARIO_SALIDA),
    con referencia_externa = número de la toma, y deja TomaInventarioLog.

Sin --aplicar solo muestra qué haría (cuántas líneas, unidades y $ a costo).

    python manage.py aplicar_ajustes_toma INV-6-20260929-001
    python manage.py aplicar_ajustes_toma 12 --aplicar
    python manage.py aplicar_ajustes_toma 12 --aplicar --reanudar --usuario javier
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from app.models import TareaAplicacionAjustes, TomaInventario
from app.views_gestion_inventarios import (
    _ejecutar_ajustes_background, _iniciar_tarea_ajustes, _tarea_huerfana,
)


class Command(BaseCommand):
    help = 'Aplica o reanuda, de forma síncrona, los ajustes de stock de una toma de inventario aprobada.'

    def add_arguments(self, parser):
        parser.add_argument('toma', help='Número de inventario (INV-6-20260929-001) o id numérico')
        parser.add_argument('--aplicar', action='store_true', help='Escribe de verdad (por defecto solo informa)')
        parser.add_argument('--reanudar', action='store_true',
                            help='Relanzar una tarea EN_PROCESO huérfana (> 30 min sin avance)')
        parser.add_argument('--usuario', default=None,
                            help='Username responsable del kardex (default: quien aprobó o creó la toma)')

    def handle(self, *args, **opts):
        ref = str(opts['toma']).strip()
        qs = TomaInventario.objects.select_related('sucursal')
        toma = qs.filter(id=int(ref)).first() if ref.isdigit() else qs.filter(numero_inventario=ref).first()
        if toma is None:
            raise CommandError(f'No existe la toma {ref}')

        if toma.estado not in ('APROBADO', 'APLICANDO'):
            raise CommandError(
                f'La toma {toma.numero_inventario} está en {toma.get_estado_display()}: '
                f'solo se aplican tomas APROBADAS (o APLICANDO huérfanas con --reanudar).'
            )

        usuario = self._usuario(toma, opts['usuario'])

        # Lo pendiente de cada línea (diferencia − lo que ya movió): incluye las
        # correcciones de líneas ajustadas antes con «Ajustar stock ya»
        pendientes = toma.lineas_por_ajustar()
        resumen = pendientes.aggregate(
            lineas=Count('id'),
            sobrantes=Coalesce(Sum('delta', filter=Q(delta__gt=0)), 0),
            faltantes=Coalesce(Sum('delta', filter=Q(delta__lt=0)), 0),
            valor=Coalesce(
                Sum(ExpressionWrapper(
                    F('delta') * F('costo_unitario_sistema'),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )),
                Value(0), output_field=DecimalField(max_digits=18, decimal_places=2),
            ),
        )
        aplicados = toma.detalles.filter(ajuste_aplicado=True).count()
        tarea = TareaAplicacionAjustes.objects.filter(inventario=toma).first()

        self.stdout.write(
            f'Toma {toma.numero_inventario} ({toma.sucursal.alias}) estado={toma.estado} '
            f'corte={timezone.localtime(toma.fecha_corte):%d/%m/%Y %H:%M}'
        )
        self.stdout.write(
            f'  ya aplicados: {aplicados}  |  pendientes: {resumen["lineas"]} líneas '
            f'(+{resumen["sobrantes"]} / {resumen["faltantes"]} u., ${float(resumen["valor"] or 0):,.0f} a costo)'
        )
        if tarea:
            self.stdout.write(
                f'  tarea: {tarea.estado} iniciada={tarea.iniciada_en} procesados={tarea.procesados}/{tarea.total} '
                f'huérfana={_tarea_huerfana(tarea, toma)}'
            )
        self.stdout.write(f'  responsable del kardex: {usuario}')

        if not opts['aplicar']:
            self.stdout.write(self.style.WARNING('Modo informe: agregue --aplicar para escribir.'))
            return

        tarea, iniciada = _iniciar_tarea_ajustes(toma, usuario, reanudar=opts['reanudar'])
        if not iniciada:
            if tarea.estado == 'EN_PROCESO' and not _tarea_huerfana(tarea, toma):
                raise CommandError(
                    'La tarea está EN_PROCESO y con avance reciente (un worker sigue vivo). '
                    'Espere o vuelva a intentar más tarde.'
                )
            raise CommandError(
                'La tarea está EN_PROCESO sin avance: use --reanudar para relanzarla.'
            )

        self.stdout.write('Aplicando ajustes (síncrono)...')
        _ejecutar_ajustes_background(toma.id, usuario.id, cerrar_conexion=False)

        tarea.refresh_from_db()
        toma.refresh_from_db()
        self.stdout.write(
            f'Resultado: tarea={tarea.estado} procesados={tarea.procesados}/{tarea.total} '
            f'errores={len(tarea.errores or [])} toma={toma.estado}'
        )
        for err in (tarea.errores or [])[:50]:
            self.stdout.write(self.style.ERROR(f'  - {err.get("sku", "?")}: {err.get("error")}'))
        if tarea.estado == 'COMPLETADO':
            self.stdout.write(self.style.SUCCESS('Listo.'))
        else:
            self.stdout.write(self.style.WARNING(
                'Quedaron líneas con error: la toma sigue APROBADA. Corrija (recontar) y vuelva a correr.'
            ))

    @staticmethod
    def _usuario(toma, username):
        User = get_user_model()
        if username:
            usuario = User.objects.filter(username=username).first()
            if usuario is None:
                raise CommandError(f'No existe el usuario {username}')
            return usuario
        usuario = toma.aprobado_por or toma.creado_por
        if usuario is None:
            raise CommandError('La toma no tiene aprobador ni creador: indique --usuario')
        return usuario
