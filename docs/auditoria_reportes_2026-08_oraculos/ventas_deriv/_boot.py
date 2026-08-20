# -*- coding: utf-8 -*-
"""Boot comun: django.setup + helper invocar() readonly. Importar desde los scripts."""
import json
import os
import sys
import time

sys.path.insert(0, r'C:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import django  # noqa: E402
django.setup()

from django.conf import settings  # noqa: E402
from django.db import connection, reset_queries, transaction  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

settings.DEBUG = True

User = get_user_model()
ADMIN = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())


def invocar(path, params, suc=None, emp=None, user=None):
    mod, fn = path.rsplit('.', 1)
    view = getattr(__import__(mod, fromlist=[fn]), fn)
    rf = RequestFactory()
    req = rf.get('/_a', data=params)
    req.user = user or ADMIN
    req.session = {'idSucursalActual': suc, 'idEmpresaActual': emp}
    reset_queries()
    t0 = time.perf_counter()
    try:
        with transaction.atomic():
            resp = view(req)
            transaction.set_rollback(True)
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}',
                'ms': round((time.perf_counter() - t0) * 1000),
                'nq': len(connection.queries), 'json': None, 'status': None,
                'escrituras': []}
    out = {'status': resp.status_code,
           'ms': round((time.perf_counter() - t0) * 1000),
           'nq': len(connection.queries), 'error': None}
    out['escrituras'] = [q['sql'][:90] for q in connection.queries
                         if q['sql'].lstrip().upper().startswith(
                             ('INSERT', 'UPDATE', 'DELETE'))]
    out['resp'] = resp
    try:
        out['json'] = json.loads(resp.content)
    except Exception:
        out['json'] = None
    return out
