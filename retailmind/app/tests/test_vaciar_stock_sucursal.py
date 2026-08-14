"""
Tests del command vaciar_stock_sucursal (dejar sucursales completas en stock 0).

Correr:
    python manage.py test app.tests.test_vaciar_stock_sucursal --settings=test_settings_sqlite
"""
import json
import os
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from app.models import LoteProducto, Movimientos_Producto, Producto, Producto_Talla, Categoria
from .factories import crear_empresa, crear_sucursal


class VaciarStockSucursalTest(TestCase):

    def setUp(self):
        self.empresa = crear_empresa()
        self.fallados = crear_sucursal(self.empresa, alias='EDEL FALLADOS')
        self.intacta = crear_sucursal(self.empresa, alias='EDEL')
        self.categoria = Categoria.objects.create(nombre='ZAPATILLA')
        self.tmpdir = tempfile.mkdtemp()

        # En FALLADOS: stock positivo con lote, stock negativo, y lote con drift
        self.pt_pos = self._pt(self.fallados, sku=8000001, stock=5)
        self.lote_pos = self._lote(self.pt_pos, disponible=5)
        self.pt_neg = self._pt(self.fallados, sku=8000002, stock=-3)
        self.pt_drift = self._pt(self.fallados, sku=8000003, stock=0)
        self.lote_drift = self._lote(self.pt_drift, disponible=4)  # lote vivo sin stock plano

        # En EDEL (no se toca)
        self.pt_edel = self._pt(self.intacta, sku=8000010, stock=7)

    def _pt(self, sucursal, sku, stock):
        producto = Producto.objects.create(
            articulo=f'ZAPATILLA {sku}', descripcion='test', sucursal=sucursal,
            costo=10000, sobreprecio=0, precioventa=19990, categoria=self.categoria,
        )
        return Producto_Talla.objects.create(producto=producto, sku=sku, stock=stock, talla='40')

    def _lote(self, pt, disponible):
        return LoteProducto.objects.create(
            producto_talla=pt, cantidad_inicial=disponible, cantidad_disponible=disponible,
            costo_unitario=10000, sobreprecio_unitario=0, precio_venta_unitario=19990,
            activo=True, agotado=False,
        )

    def _snapshot_path(self):
        archivos = [f for f in os.listdir(self.tmpdir) if f.startswith('_vaciado_stock_')]
        self.assertEqual(len(archivos), 1)
        return os.path.join(self.tmpdir, archivos[0])

    # ------------------------------------------------------------------ #

    def test_dry_run_no_modifica_nada(self):
        call_command('vaciar_stock_sucursal', '--sucursal', 'EDEL FALLADOS',
                     '--snapshot-dir', self.tmpdir)
        self.pt_pos.refresh_from_db()
        self.assertEqual(self.pt_pos.stock, 5)
        self.assertEqual(Movimientos_Producto.objects.count(), 0)
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_alias_exacto_no_matchea_edel(self):
        """"EDEL" y "EDEL FALLADOS" son sucursales distintas: el alias es exacto."""
        with self.assertRaises(CommandError):
            call_command('vaciar_stock_sucursal', '--sucursal', 'EDEL FALLADO',
                         '--snapshot-dir', self.tmpdir)

    def test_aplicar_deja_todo_en_cero_y_no_toca_otras_sucursales(self):
        call_command('vaciar_stock_sucursal', '--sucursal', 'EDEL FALLADOS',
                     '--aplicar', '--snapshot-dir', self.tmpdir)

        self.pt_pos.refresh_from_db(); self.pt_neg.refresh_from_db()
        self.pt_drift.refresh_from_db(); self.pt_edel.refresh_from_db()
        self.assertEqual(self.pt_pos.stock, 0)
        self.assertEqual(self.pt_neg.stock, 0)   # negativo también queda en 0
        self.assertEqual(self.pt_drift.stock, 0)
        self.assertEqual(self.pt_edel.stock, 7)  # EDEL intacta

        # Kardex trazable
        salida = Movimientos_Producto.objects.get(
            ProductoTalla=self.pt_pos, concepto='AJUSTE_INVENTARIO_SALIDA')
        self.assertEqual(salida.cantidad, -5)
        self.assertTrue(salida.referencia_externa.startswith('VACIADO-'))
        entrada = Movimientos_Producto.objects.get(
            ProductoTalla=self.pt_neg, concepto='AJUSTE_INVENTARIO_ENTRADA')
        self.assertEqual(entrada.cantidad, 3)

        # Lotes agotados (incluido el lote con drift sin stock plano)
        self.lote_pos.refresh_from_db(); self.lote_drift.refresh_from_db()
        self.assertEqual(self.lote_pos.cantidad_disponible, 0)
        self.assertEqual(self.lote_drift.cantidad_disponible, 0)
        self.assertTrue(self.lote_drift.agotado)

        # Snapshot escrito y completo
        with open(self._snapshot_path(), encoding='utf-8') as f:
            snap = json.load(f)
        self.assertEqual({t['pt_id'] for t in snap['tallas']}, {self.pt_pos.id, self.pt_neg.id})
        self.assertEqual({l['id'] for l in snap['lotes']}, {self.lote_pos.id, self.lote_drift.id})
        self.assertGreater(len(snap['movimientos_creados']), 0)

    def test_revertir_restaura_stock_lotes_y_borra_movimientos(self):
        call_command('vaciar_stock_sucursal', '--sucursal', 'EDEL FALLADOS',
                     '--aplicar', '--snapshot-dir', self.tmpdir)
        movs_creados = Movimientos_Producto.objects.count()
        self.assertGreater(movs_creados, 0)

        call_command('vaciar_stock_sucursal', '--revertir', self._snapshot_path())

        self.pt_pos.refresh_from_db(); self.pt_neg.refresh_from_db()
        self.assertEqual(self.pt_pos.stock, 5)
        self.assertEqual(self.pt_neg.stock, -3)
        self.lote_pos.refresh_from_db(); self.lote_drift.refresh_from_db()
        self.assertEqual(self.lote_pos.cantidad_disponible, 5)
        self.assertEqual(self.lote_drift.cantidad_disponible, 4)
        self.assertFalse(self.lote_drift.agotado)
        self.assertEqual(Movimientos_Producto.objects.count(), 0)

    def test_revertir_se_niega_si_el_stock_cambio_despues(self):
        call_command('vaciar_stock_sucursal', '--sucursal', 'EDEL FALLADOS',
                     '--aplicar', '--snapshot-dir', self.tmpdir)

        # Después del vaciado entra mercadería nueva a esa talla
        self.pt_pos.refresh_from_db()
        self.pt_pos.stock = 2
        self.pt_pos.save()

        with self.assertRaises(CommandError):
            call_command('vaciar_stock_sucursal', '--revertir', self._snapshot_path())

        # Nada se revirtió (transacción completa abortada)
        self.pt_pos.refresh_from_db(); self.pt_neg.refresh_from_db()
        self.assertEqual(self.pt_pos.stock, 2)
        self.assertEqual(self.pt_neg.stock, 0)
