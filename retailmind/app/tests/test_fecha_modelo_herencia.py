"""
Tests de la fecha de alta por MODELO (09/2026):

  * señal `heredar_fecha_creacion_del_modelo`: una ficha nueva de un modelo que
    ya existe hereda la fecha más antigua de sus hermanas (solo baja).
  * comando `corregir_fecha_creacion_productos`: excluye el saldo sintético de
    la migración, hereda por modelo, es idempotente, dry-run no escribe y
    --apply deja respaldo CSV.
"""
import csv
import os
import tempfile
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO
from app.models import Movimientos_Producto, Producto
from .factories import crear_empresa, crear_sucursal, crear_producto_con_talla


def _fijar_fecha(producto, dias_atras):
    """update() salta auto_now_add y la señal: simula data histórica."""
    Producto.objects.filter(pk=producto.pk).update(
        fecha_creacion=timezone.now() - timedelta(days=dias_atras))
    producto.refresh_from_db()


class HerenciaFechaSenalTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(rut='76.111.222-3')
        self.suc1 = crear_sucursal(empresa=self.empresa, alias='SUC-1')
        self.suc2 = crear_sucursal(empresa=self.empresa, alias='SUC-2')
        self.vieja, _ = crear_producto_con_talla(self.suc1, articulo='MOD-1', sku=7770001)
        _fijar_fecha(self.vieja, 800)

    def test_ficha_nueva_del_mismo_modelo_hereda(self):
        nueva, _ = crear_producto_con_talla(self.suc2, articulo='MOD-1', sku=7770002)
        nueva.refresh_from_db()
        self.assertEqual(nueva.fecha_creacion, self.vieja.fecha_creacion)

    def test_variante_de_mayusculas_y_espacios_tambien_hereda(self):
        nueva, _ = crear_producto_con_talla(self.suc2, articulo='  mod-1 ', sku=7770003)
        nueva.refresh_from_db()
        self.assertEqual(nueva.fecha_creacion, self.vieja.fecha_creacion)

    def test_modelo_distinto_conserva_su_fecha(self):
        antes = timezone.now()
        otra, _ = crear_producto_con_talla(self.suc2, articulo='MOD-2', sku=7770004)
        otra.refresh_from_db()
        self.assertGreaterEqual(otra.fecha_creacion, antes)

    def test_otra_empresa_del_holding_tambien_cuenta(self):
        empresa2 = crear_empresa(rut='76.999.888-7', nombre='Bodega')
        suc_edel = crear_sucursal(empresa=empresa2, alias='EDEL')
        nueva, _ = crear_producto_con_talla(suc_edel, articulo='MOD-1', sku=7770005)
        nueva.refresh_from_db()
        self.assertEqual(nueva.fecha_creacion, self.vieja.fecha_creacion)

    def test_hermana_sin_fecha_no_rompe_ni_hereda(self):
        Producto.objects.filter(pk=self.vieja.pk).update(fecha_creacion=None)
        antes = timezone.now()
        nueva, _ = crear_producto_con_talla(self.suc2, articulo='MOD-1', sku=7770006)
        nueva.refresh_from_db()
        self.assertGreaterEqual(nueva.fecha_creacion, antes)

    def test_editar_una_ficha_existente_no_toca_la_fecha(self):
        nueva, _ = crear_producto_con_talla(self.suc2, articulo='MOD-1', sku=7770007)
        _fijar_fecha(nueva, 10)   # más nueva que la hermana
        nueva.precioventa = 99990
        nueva.save()
        nueva.refresh_from_db()
        # post_save con created=False: no hereda.
        self.assertGreater(nueva.fecha_creacion, self.vieja.fecha_creacion)


class CorregirFechaCreacionComandoTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(rut='76.111.222-3')
        self.suc1 = crear_sucursal(empresa=self.empresa, alias='SUC-1')
        self.suc2 = crear_sucursal(empresa=self.empresa, alias='SUC-2')
        self.tmp = tempfile.mkdtemp()

    def _mov(self, pt, dias_atras, ref=None, concepto='INGRESO_INICIAL'):
        return Movimientos_Producto.objects.create(
            ProductoTalla=pt, tipo_movimiento='INGRESO', concepto=concepto,
            cantidad=1, fecha=timezone.localdate() - timedelta(days=dias_atras),
            referencia_externa=ref)

    def _correr(self, *args):
        out = StringIO()
        call_command('corregir_fecha_creacion_productos', *args, stdout=out)
        return out.getvalue()

    def test_baja_a_min_de_movimientos_reales(self):
        prod, pt = crear_producto_con_talla(self.suc1, articulo='A-1', sku=8880001)
        self._mov(pt, 500)
        self._mov(pt, 100)
        self._correr('--apply', '--backup-dir', self.tmp)
        prod.refresh_from_db()
        self.assertEqual(timezone.localtime(prod.fecha_creacion).date(),
                         timezone.localdate() - timedelta(days=500))

    def test_excluye_saldo_sintetico_de_la_migracion(self):
        prod, pt = crear_producto_con_talla(self.suc1, articulo='A-2', sku=8880002)
        _fijar_fecha(prod, 5)
        self._mov(pt, 250, ref=REF_SALDO_INICIAL_SINTETICO)   # 2026-01-22 en prod
        self._correr('--apply', '--backup-dir', self.tmp)
        prod.refresh_from_db()
        # No baja a la fecha del saldo sintético: se queda como estaba.
        self.assertEqual(timezone.localtime(prod.fecha_creacion).date(),
                         timezone.localdate() - timedelta(days=5))

    def test_herencia_por_modelo_repara_ficha_recreada(self):
        vieja, pt_v = crear_producto_con_talla(self.suc1, articulo='A-3', sku=8880003)
        _fijar_fecha(vieja, 700)
        recreada, pt_r = crear_producto_con_talla(self.suc2, articulo='A-3', sku=8880004)
        _fijar_fecha(recreada, 0)        # simula ficha creada hoy sin la señal
        self._mov(pt_r, 0)               # su único movimiento es de hoy

        salida = self._correr('--apply', '--backup-dir', self.tmp)
        recreada.refresh_from_db()
        self.assertEqual(timezone.localtime(recreada.fecha_creacion).date(),
                         timezone.localdate() - timedelta(days=700))
        self.assertIn('[modelo]', salida)

    def test_sin_herencia_no_toca_la_recreada(self):
        vieja, _ = crear_producto_con_talla(self.suc1, articulo='A-4', sku=8880005)
        _fijar_fecha(vieja, 700)
        recreada, pt_r = crear_producto_con_talla(self.suc2, articulo='A-4', sku=8880006)
        _fijar_fecha(recreada, 0)
        self._mov(pt_r, 0)
        self._correr('--apply', '--sin-herencia', '--backup-dir', self.tmp)
        recreada.refresh_from_db()
        self.assertEqual(timezone.localtime(recreada.fecha_creacion).date(), timezone.localdate())

    def test_nunca_sube_una_fecha(self):
        prod, pt = crear_producto_con_talla(self.suc1, articulo='A-5', sku=8880007)
        _fijar_fecha(prod, 900)
        self._mov(pt, 30)
        self._correr('--apply', '--backup-dir', self.tmp)
        prod.refresh_from_db()
        self.assertEqual(timezone.localtime(prod.fecha_creacion).date(),
                         timezone.localdate() - timedelta(days=900))

    def test_dry_run_no_escribe(self):
        prod, pt = crear_producto_con_talla(self.suc1, articulo='A-6', sku=8880008)
        self._mov(pt, 400)
        salida = self._correr()
        prod.refresh_from_db()
        self.assertEqual(timezone.localtime(prod.fecha_creacion).date(), timezone.localdate())
        self.assertIn('DRY-RUN', salida)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_apply_es_idempotente_y_deja_respaldo(self):
        prod, pt = crear_producto_con_talla(self.suc1, articulo='A-7', sku=8880009)
        self._mov(pt, 400)
        self._correr('--apply', '--backup-dir', self.tmp)
        archivos = os.listdir(self.tmp)
        self.assertEqual(len(archivos), 1)
        with open(os.path.join(self.tmp, archivos[0]), encoding='utf-8') as fh:
            filas = list(csv.DictReader(fh))
        self.assertEqual(len(filas), 1)
        self.assertEqual(int(filas[0]['producto_id']), prod.id)
        self.assertEqual(filas[0]['fuente'], 'movimientos')

        salida2 = self._correr('--apply', '--backup-dir', self.tmp)
        self.assertIn('Total candidatos            : 0', salida2)
        self.assertEqual(len(os.listdir(self.tmp)), 1)   # sin cambios, sin respaldo nuevo
