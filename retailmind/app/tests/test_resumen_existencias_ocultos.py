"""
Tests del reporte resumen-existencias sobre el stock oculto por
`Producto.excluir_de_analitica` y sobre el modal "Excluir Artículos del Análisis".

Caso real que motivó estos tests (IMP y PA00, ago-2026): sucursales cuyo stock
son casi puras bolsas marcadas como no-analíticas. IMP mostraba 10 unidades de
7.910 sin explicación y PA00 desaparecía entera del reporte.

Correr:
    python manage.py test app.tests.test_resumen_existencias_ocultos --settings=test_settings_sqlite
"""
import json

from django.test import TestCase
from django.urls import reverse

from app.models import (
    Categoria, ModuloSistema, OpcionMenu, PermisoRol, Producto, Producto_Talla,
)
from .factories import crear_empresa, crear_sucursal, crear_usuario, crear_empresa_user


class BaseResumenTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.tienda = crear_sucursal(self.empresa, alias='TIENDA')
        self.bodega = crear_sucursal(self.empresa, alias='IMP')      # 99% bolsas
        self.vacia_analitica = crear_sucursal(self.empresa, alias='PA00')  # 100% bolsas
        self.categoria = Categoria.objects.create(nombre='ZAPATILLA')

        self.user = crear_usuario(rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.tienda)

        # get_or_create: la migración 0150 siembra el módulo 'reportes'; con la
        # suite corriendo CON migraciones, un create() duplicado revienta.
        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='reportes', defaults={'nombre': 'Reportes'})
        for codigo in ('resumen_existencias',):
            opcion = OpcionMenu.objects.create(modulo=modulo, codigo=codigo, nombre=codigo)
            PermisoRol.objects.create(
                rol='administrador', opcion_menu=opcion,
                puede_ver=True, puede_crear=True, puede_editar=True,
                puede_eliminar=True, puede_exportar=True, puede_aprobar=True,
            )

        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.tienda.id
        session.save()

    def _producto(self, sucursal, articulo, stock, excluido=False, descripcion=None, sku=None):
        producto = Producto.objects.create(
            articulo=articulo,
            descripcion=descripcion if descripcion is not None else articulo,
            sucursal=sucursal, costo=1000, sobreprecio=0, precioventa=2000,
            categoria=self.categoria, excluir_de_analitica=excluido,
        )
        Producto_Talla.objects.create(
            producto=producto, sku=sku or (900000 + producto.id), stock=stock, talla='40',
        )
        return producto

    def _resumen(self, **params):
        return self.client.get(reverse('obtener_resumen_existencias'), params).json()

    def _modal(self, **params):
        return self.client.get(reverse('listar_articulos_para_excluir'), params).json()


class StockOcultoPorFlagTest(BaseResumenTest):
    """El stock marcado como no-analítico debe DECLARARSE, no desaparecer."""

    def test_sucursal_con_casi_todo_oculto_declara_las_unidades(self):
        self._producto(self.bodega, 'CAMISETA', stock=10)                       # visible
        self._producto(self.bodega, '45-1', stock=5700, excluido=True,
                       descripcion='BOLSA CORPORATIVA')
        self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True,
                       descripcion='PAPEL')

        data = self._resumen()
        self.assertTrue(data['success'], data.get('error'))
        fila = next(f for f in data['datos'] if f['sucursal'] == 'IMP')
        self.assertEqual(fila['total_pares'], 10)
        self.assertEqual(fila['ocultos_pares'], 7900)

    def test_sucursal_100_por_ciento_oculta_ya_no_desaparece(self):
        """PA00: antes se saltaba con `continue` y no dejaba rastro en ninguna parte."""
        self._producto(self.vacia_analitica, '45-1', stock=3840, excluido=True,
                       descripcion='BOLSA CORPORATIVA')
        self._producto(self.vacia_analitica, 'BOLSA GENERO', stock=500, excluido=True)
        self._producto(self.tienda, 'ZAPATILLA', stock=5)

        data = self._resumen()
        aliases = [f['sucursal'] for f in data['datos']]
        self.assertIn('PA00', aliases)

        fila = next(f for f in data['datos'] if f['sucursal'] == 'PA00')
        self.assertEqual(fila['total_pares'], 0)
        self.assertEqual(fila['ocultos_pares'], 4340)
        # No contamina el total del reporte
        self.assertEqual(data['total_general']['pares'], 5)
        self.assertIn('PA00', data['totales_ocultos_flag']['sucursales_sin_stock_visible'])

    def test_sucursal_genuinamente_vacia_sigue_sin_aparecer(self):
        crear_sucursal(self.empresa, alias='FANTASMA')
        self._producto(self.tienda, 'ZAPATILLA', stock=5)
        data = self._resumen()
        self.assertNotIn('FANTASMA', [f['sucursal'] for f in data['datos']])

    def test_totales_ocultos_globales(self):
        self._producto(self.tienda, 'ZAPATILLA', stock=5)
        self._producto(self.tienda, 'BOLSA', stock=100, excluido=True)
        self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True)

        data = self._resumen()
        self.assertEqual(data['totales_ocultos_flag']['pares'], 2300)
        self.assertEqual(data['totales_ocultos_flag']['sucursales'], 2)
        self.assertEqual(data['total_general']['pares'], 5)

    def test_historico_tambien_declara_los_ocultos(self):
        self._producto(self.tienda, 'ZAPATILLA', stock=5)
        self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True)
        data = self._resumen(fecha_corte='2026-01-01')
        self.assertTrue(data['es_historico'])
        fila = next(f for f in data['datos'] if f['sucursal'] == 'IMP')
        self.assertEqual(fila['ocultos_pares'], 2200)


class ContadorExcluidosTest(BaseResumenTest):
    """El aviso de exclusiones debe medir el delta REAL del total."""

    def test_excluir_producto_ya_oculto_no_infla_el_contador(self):
        """Antes anunciaba '2.200 unidades excluidas' con el total inmóvil."""
        self._producto(self.tienda, 'ZAPATILLA', stock=5)
        bolsa = self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True)

        antes = self._resumen()
        despues = self._resumen(excluir_articulos=str(bolsa.id))

        self.assertEqual(antes['total_general']['pares'], despues['total_general']['pares'])
        self.assertEqual(despues['totales_excluidos']['pares'], 0)

    def test_excluir_producto_visible_si_baja_el_total_y_lo_reporta(self):
        self._producto(self.tienda, 'ZAPATILLA', stock=5)
        otro = self._producto(self.tienda, 'POLERA', stock=30)

        antes = self._resumen()
        despues = self._resumen(excluir_articulos=str(otro.id))

        self.assertEqual(antes['total_general']['pares'], 35)
        self.assertEqual(despues['total_general']['pares'], 5)
        self.assertEqual(despues['totales_excluidos']['pares'], 30)


class ModalExclusionesTest(BaseResumenTest):
    """El modal debe listar siempre, decir la verdad y avisar cuando corta."""

    def test_con_exclusiones_guardadas_el_catalogo_sigue_listando(self):
        """El bug: `elif not ids_solicitados` suprimía el listado completo."""
        visible = self._producto(self.tienda, 'ZAPATILLA', stock=5)
        self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True)

        data = self._modal(ids=str(visible.id))
        self.assertTrue(data['success'])
        articulos = [i['articulo'] for i in data['items']]
        self.assertIn('BOLSA REAL', articulos)
        self.assertIn('ZAPATILLA', articulos)

    def test_filtrar_por_sucursal_con_exclusiones_de_otra_no_devuelve_vacio(self):
        """Síntoma exacto reportado: elegir IMP dejaba el panel en blanco."""
        excluido_en_otra = self._producto(self.tienda, 'ZAPATILLA', stock=5)
        self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True)

        data = self._modal(sucursal_id=self.bodega.id, ids=str(excluido_en_otra.id))
        self.assertTrue(data['success'])
        self.assertGreater(len(data['items']), 0)
        self.assertIn('BOLSA REAL', [i['articulo'] for i in data['items']])

    def test_los_ya_excluidos_se_devuelven_aunque_sean_de_otra_sucursal(self):
        """Deben poder quitarse aunque el filtro apunte a otra sucursal."""
        excluido_en_otra = self._producto(self.tienda, 'ZAPATILLA', stock=5)
        self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True)

        data = self._modal(sucursal_id=self.bodega.id, ids=str(excluido_en_otra.id))
        self.assertIn(excluido_en_otra.id, [i['id'] for i in data['items']])

    def test_marca_de_ficha_viaja_al_front(self):
        """Responde a '¿por qué tiene stock pero no me figura como excluido?'."""
        self._producto(self.bodega, 'BOLSA REAL', stock=2200, excluido=True)
        self._producto(self.tienda, 'ZAPATILLA', stock=5)

        data = self._modal()
        por_articulo = {i['articulo']: i for i in data['items']}
        self.assertTrue(por_articulo['BOLSA REAL']['excluido_en_ficha'])
        self.assertFalse(por_articulo['ZAPATILLA']['excluido_en_ficha'])

    def test_ordena_por_stock_descendente(self):
        """El corte de 50 debe llevarse lo que MÁS pesa, no lo alfabéticamente primero."""
        self._producto(self.tienda, 'ZZZ GRANDE', stock=9000)
        self._producto(self.tienda, 'AAA CHICO', stock=3)

        data = self._modal()
        self.assertEqual(data['items'][0]['articulo'], 'ZZZ GRANDE')

    def test_informa_el_total_real_y_el_truncado(self):
        for i in range(55):
            self._producto(self.tienda, f'BOLSA {i:03d}', stock=i + 1)

        data = self._modal(q='BOLSA')
        self.assertEqual(len(data['items']), 50)
        self.assertEqual(data['total_encontrados'], 55)
        self.assertTrue(data['truncado'])

    def test_sin_truncado_no_marca_la_bandera(self):
        self._producto(self.tienda, 'BOLSA UNICA', stock=5)
        data = self._modal(q='BOLSA')
        self.assertEqual(data['total_encontrados'], 1)
        self.assertFalse(data['truncado'])

    def test_busca_por_descripcion(self):
        """'45-1' se llama así pero su descripción es BOLSA CORPORATIVA."""
        self._producto(self.tienda, '45-1', stock=5700, descripcion='BOLSA CORPORATIVA')
        data = self._modal(q='bolsa')
        self.assertIn('45-1', [i['articulo'] for i in data['items']])

    def test_no_filtra_productos_de_otra_empresa(self):
        otra_empresa = crear_empresa(nombre='Otra SpA')
        otra_suc = crear_sucursal(otra_empresa, alias='AJENA')
        self._producto(otra_suc, 'BOLSA AJENA', stock=100)
        self._producto(self.tienda, 'BOLSA PROPIA', stock=5)

        data = self._modal(q='BOLSA')
        self.assertNotIn('BOLSA AJENA', [i['articulo'] for i in data['items']])
