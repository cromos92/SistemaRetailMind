# -*- coding: utf-8 -*-
"""
Management command para medir rendimiento de endpoints clave del módulo
ventas: mide número de queries a la DB, tiempo total y tamaño de respuesta.

No requiere django-silk — usa `connection.queries` con DEBUG=True temporal.

Uso:
    python manage.py benchmark_ventas
    python manage.py benchmark_ventas --sucursal 3
    python manage.py benchmark_ventas --username admin
"""
from __future__ import annotations

import time
import json

from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import connection, reset_queries
from django.test import RequestFactory

from app.models import Sucursal


ENDPOINTS = [
    # (nombre, import_path, method, params/body, kwargs_url)
    ('listar_documentos_ventas',
     'app.views_modulo_ventas.listar_documentos_ventas',
     'GET', {'page': '1', 'per_page': '20'}, {}),
    ('listar_arqueos',
     'app.views_modulo_ventas.listar_arqueos',
     'GET', {'page': '1', 'per_page': '20'}, {}),
    ('listar_cuadraturas',
     'app.views_modulo_ventas.listar_cuadraturas',
     'GET', {'page': '1', 'per_page': '20'}, {}),
    ('listar_cambios_devoluciones',
     'app.views_modulo_ventas.listar_cambios_devoluciones',
     'GET', {'page': '1', 'per_page': '20'}, {}),
    ('dashboard_stats',
     'app.views_modulo_ventas.dashboard_stats',
     'GET', {}, {}),
    ('obtener_indicadores_globales_ventas',
     'app.views_modulo_ventas.obtener_indicadores_globales_ventas',
     'GET', {}, {}),
    ('obtener_ventas_por_vendedor',
     'app.views_modulo_ventas.obtener_ventas_por_vendedor',
     'GET', {}, {}),
    ('obtener_ventas_por_sucursal',
     'app.views_modulo_ventas.obtener_ventas_por_sucursal',
     'GET', {}, {}),
]


def _import_view(path: str):
    module_name, func_name = path.rsplit('.', 1)
    mod = __import__(module_name, fromlist=[func_name])
    return getattr(mod, func_name)


class Command(BaseCommand):
    help = 'Mide nº de queries y tiempo de endpoints clave del módulo ventas.'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default=None,
                            help='Usuario a simular (default: superusuario)')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='ID de sucursal para la sesión')
        parser.add_argument('--runs', type=int, default=1,
                            help='Número de ejecuciones por endpoint (default: 1)')

    def handle(self, *args, **options):
        User = get_user_model()
        username = options.get('username')
        sucursal_id = options.get('sucursal')
        runs = options['runs']

        # Obtener usuario
        if username:
            user = User.objects.filter(username=username).first()
        else:
            user = User.objects.filter(is_superuser=True).first()
        if user is None:
            self.stdout.write(self.style.ERROR('No hay usuario disponible para el benchmark.'))
            return

        # Obtener sucursal
        if sucursal_id is None:
            suc = Sucursal.objects.first()
            if suc:
                sucursal_id = suc.id
        if sucursal_id is None:
            self.stdout.write(self.style.ERROR('No hay sucursal disponible.'))
            return

        # Activar DEBUG para tener connection.queries
        settings.DEBUG = True

        factory = RequestFactory()
        header = f'{"Endpoint":<45} {"Runs":>5} {"Avg ms":>10} {"Avg qs":>8} {"Min ms":>10} {"Max ms":>10} {"Resp KB":>8}'
        self.stdout.write(self.style.NOTICE(header))
        self.stdout.write('-' * len(header))

        for name, path, method, params, url_kwargs in ENDPOINTS:
            try:
                view = _import_view(path)
            except Exception as e:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f'{name}: import error: {e}'))
                continue

            tiempos = []
            counts_qs = []
            resp_size = 0

            for _ in range(runs):
                reset_queries()
                request = factory.get('/benchmark', data=params) if method == 'GET' \
                    else factory.post('/benchmark', data=params)
                request.user = user
                # Simular session con sucursal
                request.session = {'idSucursalActual': sucursal_id}

                t0 = time.perf_counter()
                try:
                    response = view(request, **url_kwargs)
                except Exception as e:  # noqa: BLE001
                    self.stdout.write(self.style.ERROR(f'{name}: runtime error: {e}'))
                    break
                elapsed_ms = (time.perf_counter() - t0) * 1000
                tiempos.append(elapsed_ms)
                counts_qs.append(len(connection.queries))
                try:
                    resp_size = max(resp_size, len(response.content))
                except AttributeError:
                    resp_size = 0
            else:
                avg_ms = sum(tiempos) / len(tiempos)
                avg_qs = sum(counts_qs) / len(counts_qs)
                self.stdout.write(
                    f'{name:<45} {runs:>5} {avg_ms:>10.1f} {avg_qs:>8.1f} '
                    f'{min(tiempos):>10.1f} {max(tiempos):>10.1f} {resp_size/1024:>8.1f}'
                )

        self.stdout.write('-' * len(header))
        self.stdout.write(self.style.SUCCESS('Benchmark finalizado.'))
