"""
Tests del command excluir_sucursal_analitica.

Correr:
    python manage.py test app.tests.test_excluir_sucursal_analitica --settings=test_settings_sqlite
"""
import os
import tempfile

from django.core.management import call_command
from django.test import TestCase

from app.models import Categoria, Producto
from .factories import crear_empresa, crear_sucursal


class ExcluirSucursalAnaliticaTest(TestCase):

    def setUp(self):
        self.empresa = crear_empresa()
        self.fallados = crear_sucursal(self.empresa, alias='EDEL FALLADOS')
        self.intacta = crear_sucursal(self.empresa, alias='EDEL')
        self.categoria = Categoria.objects.create(nombre='ZAPATILLA')
        self.tmpdir = tempfile.mkdtemp()

        self.p_normal = self._producto(self.fallados, 'ZAPATILLA A')
        self.p_ya_excluido = self._producto(self.fallados, 'ZAPATILLA B', excluir=True)
        self.p_edel = self._producto(self.intacta, 'ZAPATILLA C')

    def _producto(self, sucursal, articulo, excluir=False):
        return Producto.objects.create(
            articulo=articulo, descripcion=articulo, sucursal=sucursal,
            costo=10000, sobreprecio=0, precioventa=19990, categoria=self.categoria,
            excluir_de_analitica=excluir,
        )

    def _snapshot_path(self):
        archivos = [f for f in os.listdir(self.tmpdir) if f.startswith('_exclusion_analitica_')]
        self.assertEqual(len(archivos), 1)
        return os.path.join(self.tmpdir, archivos[0])

    def test_dry_run_no_modifica_nada(self):
        call_command('excluir_sucursal_analitica', '--sucursal', 'EDEL FALLADOS',
                     '--snapshot-dir', self.tmpdir)
        self.p_normal.refresh_from_db()
        self.assertFalse(self.p_normal.excluir_de_analitica)
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_aplicar_marca_solo_la_sucursal_pedida(self):
        call_command('excluir_sucursal_analitica', '--sucursal', 'EDEL FALLADOS',
                     '--aplicar', '--snapshot-dir', self.tmpdir)
        self.p_normal.refresh_from_db(); self.p_edel.refresh_from_db()
        self.assertTrue(self.p_normal.excluir_de_analitica)
        self.assertFalse(self.p_edel.excluir_de_analitica)  # EDEL intacta

    def test_revertir_no_toca_los_que_ya_estaban_excluidos(self):
        call_command('excluir_sucursal_analitica', '--sucursal', 'EDEL FALLADOS',
                     '--aplicar', '--snapshot-dir', self.tmpdir)
        call_command('excluir_sucursal_analitica', '--revertir', self._snapshot_path())

        self.p_normal.refresh_from_db(); self.p_ya_excluido.refresh_from_db()
        self.assertFalse(self.p_normal.excluir_de_analitica)   # restaurado
        self.assertTrue(self.p_ya_excluido.excluir_de_analitica)  # estaba excluido de antes
