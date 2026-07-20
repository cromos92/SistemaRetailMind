# -*- coding: utf-8 -*-
"""Marca excluir_de_analitica=True los PSEUDO-ARTÍCULOS que no son productos
reales (DIFER VISA, COSTO ENVIO, SALDO, LOTE, diferencias, 0...). Estos inflan
los reportes con 'stock' falso (ej. DIFER VISA tenía 32.577). No cambia categoría
ni nada más. DRY-RUN por defecto; --apply escribe.

Detecta por descripción/artículo con patrones conservadores (palabra exacta).

Uso:
    python manage.py marcar_no_productos_analitica
    python manage.py marcar_no_productos_analitica --apply
"""
import logging
import re
import unicodedata
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Producto

logger = logging.getLogger('app')


def fold(x):
    x = unicodedata.normalize('NFKD', str(x or ''))
    return ''.join(c for c in x if not unicodedata.combining(c)).upper().strip()


# Patrones de NO-PRODUCTO (palabra/frase exacta en descripción o artículo)
RE_NOPROD = re.compile(
    r'^\s*0\s*$|DIFER|DIFERENCIA|COSTO ENVIO|\bENVIO\b|\bENVIOS\b|\bFLETE\b|'
    r'\bSALDO\b|\bSALDOS\b|\bVISA\b|TRANSBANK|\bABONO\b|\bAJUSTE\b|REDONDEO|'
    r'\bGIFT\b|GIFTCARD|GIFT CARD|\bVALE\b|PROPINA|\bLOTE\b|\bVARIOS\b|SIN NOMBRE|'
    r'\bBOLETA\b|\bFACTURA\b|\bTEST\b|PRUEBA'
)


class Command(BaseCommand):
    help = "Marca excluir_de_analitica los pseudo-artículos (VISA/ENVIO/SALDO/...) (dry-run por defecto)"

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        apply_ = opts['apply']
        modo = 'APPLY' if apply_ else 'DRY-RUN'

        # Candidatos: productos aún NO excluidos cuya desc o artículo matchea
        qs = Producto.objects.filter(excluir_de_analitica=False).values(
            'id', 'articulo', 'descripcion')
        ids = []
        muestras = []
        por_termino = Counter()
        for p in qs.iterator(chunk_size=5000):
            d = fold(p['descripcion'])
            a = fold(p['articulo'])
            m = RE_NOPROD.search(d) or RE_NOPROD.search(a)
            if m:
                ids.append(p['id'])
                por_termino[m.group(0).strip()] += 1
                if len(muestras) < 25:
                    muestras.append(f"{(p['descripcion'] or '')[:26]:26} [{(p['articulo'] or '')[:16]}]")

        self.stdout.write(self.style.MIGRATE_HEADING(f"[{modo}] marcar_no_productos_analitica"))
        self.stdout.write(f"  productos a EXCLUIR de analítica: {len(ids)}")
        self.stdout.write("  por término (top):")
        for k, v in por_termino.most_common(15):
            self.stdout.write(f"     {v:5}  {k}")
        self.stdout.write("  muestra:")
        for e in muestras:
            self.stdout.write(f"     {e}")

        if apply_:
            n = 0
            with transaction.atomic():
                for i in range(0, len(ids), 1000):
                    n += Producto.objects.filter(id__in=ids[i:i+1000]).update(excluir_de_analitica=True)
            self.stdout.write(self.style.SUCCESS(f"\n  {n} productos marcados excluir_de_analitica=True"))
        else:
            self.stdout.write(self.style.WARNING("\n  (dry-run: nada escrito; usa --apply)"))
        logger.info("marcar_no_productos_analitica %s: %d productos", modo, len(ids))
