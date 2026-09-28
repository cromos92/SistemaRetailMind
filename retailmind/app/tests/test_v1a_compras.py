"""
Unidad V1A — Compras: CRUD, listado, recepción de compra y proveedores
(views.py).

Cubre:
- B11-02 / B13-05 / B12-10: borrar un proveedor ya no arrastra en cascada sus
  DTE de compra (el proveedor es el EMISOR); permiso por método; empresas
  propias protegidas; 'DTEs' del listado = facturas de compra emitidas.
- B11-14: RUT canónico y duplicados detectados con el RUT normalizado.
- B14-01 / B4-04 / B1-02 / B2-02: eliminar_compra bloquea con recepciones
  (fuente de verdad: Productos_Recepcionados).
- B14-07 / B2-08 / B2-10 / B1-03 / B14-02 / B2-03: guardar_recepcion valida
  todo antes de escribir (tope, negativos, compra anulada, talla ajena,
  factura de otro emisor / no COMPRA / NC, reuso de factura con exceso) y
  registra quién y cuándo.
- B12-09 / B1-03: validar_factura_proveedor busca primero en el proveedor.
- B2-04: actualizar_recepciones_compra no toca recepciones ya creadas y no
  deja escrituras a medias.
- B12-08 / B2-11 / B2-02: pendientes de la compra desde las recepciones.
- B1-06: importar_csv_compra valida todo y es atómico.
- B1-10: actualizar_compra (histórica / cambio de proveedor).
- B15-05 / B1-13 / B1-14: listado por año de orden o de temporada, estado de
  temporada, parámetros inválidos.
- B12-01 / B1-08 / B1-09: recepcionar_compra (facturas una vez en la raíz, sin
  NC).
- Comandos compras_recalcular_avance y compras_revisar_recepciones_pendientes.

Ejecutar (BD de test aislada):
    python manage.py test app.tests.test_v1a_compras --keepdb
"""
import io
import json
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Dte, Empresa,
    Movimientos_Producto, Productos_Recepcionados, Vendedor,
)
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_sucursal,
    crear_usuario, otorgar_ver_pantalla,
)


def _rut(numero):
    """RUT válido 'NNNNNNNN-D' para un número dado."""
    suma, mult = 0, 2
    for d in reversed(str(numero)):
        suma += int(d) * mult
        mult = mult + 1 if mult < 7 else 2
    resto = 11 - (suma % 11)
    dv = '0' if resto == 11 else 'K' if resto == 10 else str(resto)
    return f'{numero}-{dv}'


class _BaseV1A(TestCase):
    ROL = 'administrador'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Nosotros V1A', rut=_rut(76410001))
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='V1A-SUC')
        cls.proveedor = crear_empresa(nombre='Proveedor V1A', rut=_rut(77410001), esProveedor=True)
        cls.otro_proveedor = crear_empresa(nombre='Otro Prov V1A', rut=_rut(77410002), esProveedor=True)
        cls.user = crear_usuario(username='v1a_admin', rol=cls.ROL, first_name='Ana', last_name='Compras')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)
        flags = dict(puede_crear=True, puede_editar=True, puede_eliminar=True)
        otorgar_ver_pantalla(cls.ROL, 'gestion_compras', **flags)
        otorgar_ver_pantalla(cls.ROL, 'gestion_dte_compras', **flags)

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s['idEmpresaActual'] = self.empresa.id
        s['idSucursalActual'] = self.sucursal.id
        s['nombreUsuario'] = 'Ana Compras'
        s.save()
        self.hoy = timezone.localdate()

    # ---------- helpers ----------
    def _json(self, method, url, data=None, client=None):
        c = client or self.client
        if method == 'get':
            return c.get(url)
        return getattr(c, method)(url, data=json.dumps(data or {}), content_type='application/json')

    def _dte(self, numero, emisor, tipo='FACTURA ELECTRONICA', tipo_transaccion='COMPRA',
             unidades=0, **extra):
        return Dte.objects.create(
            emisor=emisor, receptor=self.empresa, numero_documento=numero,
            tipo_documento=tipo, monto_con_iva=119000, monto_neto=100000, descuento=0,
            estado_pago='Pendiente', estado_dte='ACEPTADO', responsable='test',
            fecha_emision=self.hoy, fecha_vencimiento=self.hoy, diasCredito=0, bultos=0,
            unidades_productos=unidades, tipo_transaccion=tipo_transaccion,
            sucursal=self.sucursal, **extra,
        )

    def _compra(self, nombre='OC V1A', estado='ACTIVA', proveedor=None, **extra):
        return Compras.objects.create(
            empresa=proveedor or self.proveedor, nombre=nombre, correlativo=1,
            responsable='tester', temporada='Invierno', fecha=self.hoy, estado=estado, **extra,
        )

    def _linea(self, compra, nombre='ART-1', tallas=(('40', 5),), costo=1000):
        cp = Compras_Producto.objects.create(
            compras=compra, nombre=nombre, atributo1='MARCA', atributo2='NEGRO',
            atributo3='HOMBRE', atributo4='', costo=costo, precioSugerido=costo * 2,
        )
        return cp, [Compras_Producto_Talla.objects.create(compra_producto=cp, stock=s, talla=t)
                    for t, s in tallas]

    def _recepcion(self, cpt, cantidad, dte=None, producto_talla=None):
        return Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, stockArribado=cantidad, dte=dte,
            producto_talla=producto_talla,
        )


# =====================================================================
# Proveedores
# =====================================================================
class ProveedorEliminarTest(_BaseV1A):
    URL = '/app/gestionar_proveedor/{}/'

    def test_no_borra_proveedor_con_facturas_de_compra_emitidas(self):
        dte = self._dte(5001, self.proveedor)
        r = self._json('delete', self.URL.format(self.proveedor.id))
        self.assertEqual(r.status_code, 400)
        self.assertIn('emitido', r.json()['error'])
        self.assertTrue(Dte.objects.filter(id=dte.id).exists())
        self.assertTrue(Empresa.objects.filter(id=self.proveedor.id).exists())

    def test_vendedor_sin_permiso_no_puede_borrar(self):
        vend = crear_usuario(username='v1a_vend', rol='vendedor')
        crear_empresa_user(vend, self.empresa, self.sucursal)
        prov = crear_empresa(nombre='Prov limpio', rut=_rut(77410010), esProveedor=True)
        self.client.force_login(vend)
        r = self._json('delete', self.URL.format(prov.id))
        self.assertIn(r.status_code, (302, 403))
        self.assertTrue(Empresa.objects.filter(id=prov.id).exists())

    def test_borra_proveedor_sin_registros(self):
        prov = crear_empresa(nombre='Prov limpio 2', rut=_rut(77410011), esProveedor=True)
        r = self._json('delete', self.URL.format(prov.id))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Empresa.objects.filter(id=prov.id).exists())

    def test_rechaza_si_el_borrado_arrastraria_otros_registros(self):
        prov = crear_empresa(nombre='Prov con vendedor', rut=_rut(77410012), esProveedor=True)
        Vendedor.objects.create(nombre='V', empresa=prov, codigo_vendedor='VX', rut=_rut(12000001),
                                correo='v@x.cl', comision=0, activo=True)
        r = self._json('delete', self.URL.format(prov.id))
        self.assertEqual(r.status_code, 400)
        self.assertIn('registros asociados', r.json()['error'])
        self.assertTrue(Empresa.objects.filter(id=prov.id).exists())

    def test_empresa_propia_no_se_borra_ni_edita(self):
        self.empresa.esProveedor = True
        self.empresa.save()
        r = self._json('delete', self.URL.format(self.empresa.id))
        self.assertEqual(r.status_code, 400)
        r = self._json('put', self.URL.format(self.empresa.id), {'nombre': 'X'})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Empresa.objects.filter(id=self.empresa.id).exists())

    def test_listado_cuenta_facturas_emitidas(self):
        self._dte(5101, self.proveedor)
        self._dte(5102, self.proveedor)
        self._dte(5103, self.proveedor, tipo_transaccion='VENTA')
        r = self._json('get', '/app/listar_proveedores/?search=Proveedor V1A')
        fila = [d for d in r.json()['data'] if d['id'] == self.proveedor.id][0]
        self.assertEqual(fila['dtes_count'], 2)
        r = self._json('get', '/app/listar_proveedores/?page=0&page_size=abc')
        self.assertEqual(r.status_code, 200)


class ProveedorRutTest(_BaseV1A):
    BASE = {'nombre': 'Nuevo', 'nombre_fantasia': 'N', 'razon_social': 'N SpA', 'giro': 'g',
            'direccion': 'd', 'comuna': 'c', 'ciudad': 'c', 'correoVendedor': 'a@b.cl'}

    def test_duplicado_con_otro_formato_se_rechaza(self):
        existente = _rut(77410001)  # RUT del proveedor base
        num, dv = existente.split('-')
        con_puntos = f'{int(num):,}'.replace(',', '.') + '-' + dv
        for variante in (con_puntos, existente.lower(), num + dv):
            r = self._json('post', '/app/crear_proveedor/', dict(self.BASE, rut=variante))
            self.assertEqual(r.status_code, 400, variante)

    def test_se_guarda_canonico(self):
        rut = _rut(77410099)
        num, dv = rut.split('-')
        r = self._json('post', '/app/crear_proveedor/', dict(self.BASE, rut=f'{int(num):,}'.replace(',', '.') + '-' + dv.lower()))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Empresa.objects.get(id=r.json()['proveedor']['id']).rut, rut.upper())


# =====================================================================
# eliminar_compra
# =====================================================================
class EliminarCompraTest(_BaseV1A):
    URL = '/app/eliminar_compra/'

    def test_bloquea_si_ya_ingreso_stock(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra, tallas=(('40', 5),))
        _prod, pt = crear_producto_con_talla(self.sucursal, articulo='ART-1', talla='40', sku=9900001)
        self._recepcion(cpt, 5, producto_talla=pt)
        r = self._json('post', self.URL, {'compra_id': compra.id, 'mode': 'check'})
        self.assertEqual(r.json()['info']['unidades_ingresadas'], 5)
        self.assertTrue(r.json()['info']['tiene_recepciones'])
        r = self._json('post', self.URL, {'compra_id': compra.id, 'mode': 'delete', 'force': True})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json()['blocked'])
        compra.refresh_from_db()
        self.assertEqual(compra.estado, 'ACTIVA')

    def test_bloquea_con_recepciones_pendientes(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra)
        self._recepcion(cpt, 3)
        r = self._json('post', self.URL, {'compra_id': compra.id, 'mode': 'delete', 'force': True})
        self.assertEqual(r.status_code, 400)
        compra.refresh_from_db()
        self.assertEqual(compra.estado, 'ACTIVA')

    def test_elimina_sin_recepciones(self):
        compra = self._compra()
        self._linea(compra)
        r = self._json('post', self.URL, {'compra_id': compra.id, 'mode': 'delete', 'force': True})
        self.assertEqual(r.status_code, 200, r.content)
        compra.refresh_from_db()
        self.assertEqual(compra.estado, 'ELIMINADA')

    def test_compra_inexistente_json_404(self):
        r = self._json('post', self.URL, {'compra_id': 999999, 'mode': 'check'})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()['success'])


# =====================================================================
# guardar_recepcion
# =====================================================================
class GuardarRecepcionTest(_BaseV1A):
    URL = '/app/guardar_recepcion/'

    def setUp(self):
        super().setUp()
        self.compra = self._compra()
        self.cp, (self.cpt,) = self._linea(self.compra, tallas=(('40', 5),))
        self.factura = self._dte(7001, self.proveedor, unidades=5)

    def _post(self, recs, compra=None, **extra):
        body = dict(compra_id=(compra or self.compra).id, recepciones=recs, sucursal_destino_id=None, **extra)
        return self._json('post', self.URL, body)

    def _total(self, cpt=None):
        return sum(Productos_Recepcionados.objects.filter(
            compra_producto_talla=cpt or self.cpt).values_list('stockArribado', flat=True))

    def test_recepcion_normal_registra_quien_y_cuando(self):
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 3, 'factura_id': self.factura.id}])
        self.assertEqual(r.status_code, 200, r.content)
        rec = Productos_Recepcionados.objects.get(compra_producto_talla=self.cpt)
        self.assertEqual(rec.stockArribado, 3)
        self.assertIsNotNone(rec.fecha_recepcion)
        self.assertEqual(rec.recepcionado_por, 'Ana Compras')
        self.assertEqual(rec.cantidad_esperada, 0)
        # segunda parcial con la misma factura: suma sobre la fila pendiente
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 2, 'factura_id': self.factura.id}])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Productos_Recepcionados.objects.filter(compra_producto_talla=self.cpt).count(), 1)
        self.assertEqual(self._total(), 5)

    def test_exceso_negativo_y_no_escribe(self):
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 50}])
        self.assertEqual(r.status_code, 400)
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': -2}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._total(), 0)

    def test_validacion_previa_no_deja_escrituras_a_medias(self):
        _cp2, (cpt2,) = self._linea(self.compra, nombre='ART-2', tallas=(('41', 1),))
        r = self._post([
            {'compra_producto_talla_id': self.cpt.id, 'recepcionado': 2},
            {'compra_producto_talla_id': cpt2.id, 'recepcionado': 9},
        ])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._total(), 0)

    def test_compra_eliminada_y_talla_ajena(self):
        eliminada = self._compra(nombre='OC borrada', estado='ELIMINADA')
        _cp, (cpt_e,) = self._linea(eliminada)
        r = self._post([{'compra_producto_talla_id': cpt_e.id, 'recepcionado': 1}], compra=eliminada)
        self.assertEqual(r.status_code, 400)
        r = self._post([{'compra_producto_talla_id': cpt_e.id, 'recepcionado': 1}])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Productos_Recepcionados.objects.filter(compra_producto_talla=cpt_e).exists())

    def test_factura_invalida(self):
        otra = self._dte(7002, self.otro_proveedor)
        venta = self._dte(7003, self.empresa, tipo_transaccion='VENTA')
        nc = self._dte(7004, self.proveedor, tipo='NOTA DE CREDITO')
        for dte in (otra, venta, nc):
            r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 1, 'factura_id': dte.id}])
            self.assertEqual(r.status_code, 400, dte.tipo_documento)
        self.assertEqual(self._total(), 0)

    def test_factura_de_ficha_con_mismo_rut_es_valida(self):
        ficha2 = crear_empresa(nombre='Proveedor V1A (ficha 2)', rut=self.proveedor.rut.replace('-', ''),
                               esProveedor=True)
        f = self._dte(7005, ficha2)
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 1, 'factura_id': f.id}])
        self.assertEqual(r.status_code, 200, r.content)

    def test_filas_agrupadas_se_reparten_segun_pendiente(self):
        _cp, (a,) = self._linea(self.compra, nombre='ART-G', tallas=(('38', 1),))
        _cp, (b,) = self._linea(self.compra, nombre='ART-G', tallas=(('38', 3),))
        self._recepcion(a, 1)
        # UI: pendiente del grupo = 3; reparte parejo 2 + 1 sin mirar cada línea
        r = self._post([
            {'compra_producto_talla_id': a.id, 'recepcionado': 2},
            {'compra_producto_talla_id': b.id, 'recepcionado': 1},
        ])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self._total(a), 1)
        self.assertEqual(self._total(b), 3)

    def test_reuso_de_factura_por_sobre_lo_declarado_pide_confirmacion(self):
        otra_compra = self._compra(nombre='OC duplicada')
        _cp, (cpt_o,) = self._linea(otra_compra, tallas=(('40', 5),))
        self._recepcion(cpt_o, 5, dte=self.factura)  # factura declara 5 u
        rec = [{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 2, 'factura_id': self.factura.id}]
        r = self._post(rec)
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()['needs_confirmation'])
        self.assertEqual(self._total(), 0)
        r = self._post(rec, confirmar_exceso_factura=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._total(), 2)

    def test_sumar_sobre_recepcion_ya_creada_crea_fila_pendiente(self):
        _prod, pt = crear_producto_con_talla(self.sucursal, articulo='ART-1', talla='40', sku=9900002)
        creada = self._recepcion(self.cpt, 2, dte=self.factura, producto_talla=pt)
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 1, 'factura_id': self.factura.id}])
        self.assertEqual(r.status_code, 200, r.content)
        creada.refresh_from_db()
        self.assertEqual(creada.stockArribado, 2)
        nueva = Productos_Recepcionados.objects.get(compra_producto_talla=self.cpt, producto_talla__isnull=True)
        self.assertEqual(nueva.stockArribado, 1)


# =====================================================================
# validar_factura_proveedor
# =====================================================================
class ValidarFacturaProveedorTest(_BaseV1A):
    URL = '/app/validar_factura_proveedor/'

    def test_folio_compartido_devuelve_el_del_proveedor(self):
        propia = self._dte(8001, self.proveedor)
        ajena = self._dte(8001, self.otro_proveedor, tipo='NOTA DE CREDITO')
        ajena.fecha_emision = self.hoy + timedelta(days=5)
        ajena.save()
        r = self.client.get(self.URL, {'numero': '8001', 'proveedor_id': self.proveedor.id})
        d = r.json()
        self.assertTrue(d['existe'])
        self.assertTrue(d['pertenece_proveedor'])
        self.assertEqual(d['factura']['id'], propia.id)

    def test_folio_de_otro_emisor_o_venta_no_entrega_id(self):
        self._dte(8002, self.otro_proveedor)
        d = self.client.get(self.URL, {'numero': '8002', 'proveedor_id': self.proveedor.id}).json()
        self.assertFalse(d['existe'])
        self.assertIn('coincidencia', d)
        self.assertNotIn('factura', d)
        self._dte(8003, self.empresa, tipo_transaccion='VENTA')
        d = self.client.get(self.URL, {'numero': '8003', 'proveedor_id': self.proveedor.id}).json()
        self.assertFalse(d['existe'])
        self.assertNotIn('factura', d)


# =====================================================================
# actualizar_recepciones_compra
# =====================================================================
class ActualizarRecepcionesCompraTest(_BaseV1A):
    URL = '/app/actualizar_recepciones_compra/'

    def test_no_toca_creadas_ni_crea_movimientos(self):
        compra = self._compra()
        _cp, (cpt, cpt2) = self._linea(compra, tallas=(('40', 5), ('41', 5)))
        _prod, pt = crear_producto_con_talla(self.sucursal, articulo='ART-1', talla='40', sku=9900003)
        creada = self._recepcion(cpt, 2, producto_talla=pt)
        pendiente = self._recepcion(cpt2, 2)
        movs = Movimientos_Producto.objects.count()
        r = self._json('post', self.URL, {'compra_id': compra.id, 'cambios': [
            {'recepcion_id': pendiente.id, 'cantidad': 4},
            {'recepcion_id': creada.id, 'cantidad': 3},
        ]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['omitidas'], 1)
        creada.refresh_from_db()
        pendiente.refresh_from_db()
        self.assertEqual(creada.stockArribado, 2)
        self.assertEqual(pendiente.stockArribado, 4)
        self.assertEqual(Movimientos_Producto.objects.count(), movs)

    def test_exceso_revierte_todo(self):
        compra = self._compra()
        _cp, (cpt, cpt2) = self._linea(compra, tallas=(('40', 5), ('41', 2)))
        r1 = self._recepcion(cpt, 1)
        r2 = self._recepcion(cpt2, 1)
        r = self._json('post', self.URL, {'compra_id': compra.id, 'cambios': [
            {'recepcion_id': r1.id, 'cantidad': 4},
            {'recepcion_id': r2.id, 'cantidad': 9},
        ]})
        self.assertEqual(r.status_code, 400)
        r1.refresh_from_db()
        self.assertEqual(r1.stockArribado, 1)


# =====================================================================
# pendientes / listado / recepcionar / importar / actualizar_compra
# =====================================================================
class PendientesYListadoTest(_BaseV1A):

    def test_pendientes_desde_recepciones(self):
        compra = self._compra()
        _cp, (a, b) = self._linea(compra, tallas=(('40', 5), ('41', 2)))
        self._recepcion(a, 3)
        self._recepcion(b, 2)
        d = self._json('get', f'/app/obtener_pendientes_compra/{compra.id}/').json()
        self.assertEqual([p['id'] for p in d['pendientes']], [a.id])
        self.assertEqual(d['pendientes'][0]['pendiente'], 2)
        self.assertEqual(d['pendientes'][0]['unidades_recibidas'], 3)
        self.assertFalse(d['pendientes'][0]['puede_eliminar'])

    def test_listado_incluye_temporada_y_valida_anio(self):
        oc = self._compra(nombre='OC temporada siguiente', temporada_anio=self.hoy.year + 1)
        oc.fecha = self.hoy.replace(year=self.hoy.year - 0)
        oc.save()
        d = self._json('get', f'/app/obtener_compras/?anio={self.hoy.year + 1}').json()
        fila = [c for c in d['compras'] if c['id'] == oc.id]
        self.assertEqual(len(fila), 1)
        self.assertEqual(fila[0]['estado_temporada'], 'sin_fechas')
        self.assertEqual(self._json('get', '/app/obtener_compras/?anio=abc').status_code, 400)
        self.assertEqual(self._json('get', f'/app/obtener_compras/?anio={self.hoy.year}&page=0&page_size=0').status_code, 200)

    def test_recepcionar_facturas_en_la_raiz_sin_nc(self):
        compra = self._compra()
        self._linea(compra, tallas=(('40', 5), ('41', 5)))
        f = self._dte(9001, self.proveedor)
        nc = self._dte(9002, self.proveedor, tipo='NOTA DE CREDITO')
        r = self._json('post', '/app/compra/recepcionar/', {'compra_id': compra.id, 'page': 1, 'page_size': 50,
                                                            'search': '', 'vista_agrupada': True})
        d = r.json()
        ids = [x['id'] for x in d['facturas_proveedor']]
        self.assertIn(f.id, ids)
        self.assertNotIn(nc.id, ids)
        talla = d['items'][0]['tallas'][0]
        self.assertNotIn('facturas', talla)
        self.assertIn('pendiente_por_id', talla)
        self.assertEqual(self._json('post', '/app/compra/recepcionar/', {'compra_id': 999999}).status_code, 404)


class ImportarCsvCompraTest(_BaseV1A):
    URL = '/app/importar_csv_compra/'

    def _fila(self, **kw):
        base = dict(nombre='ART-CSV', descripcion='d', atributo1='MARCA', atributo2='NEGRO',
                    atributo3='HOMBRE', atributo4='', costo=1000, precioSugerido=2000, stock=5,
                    talla='40', sucursal='')
        base.update(kw)
        return base

    def test_errores_no_importan_nada(self):
        compra = self._compra()
        r = self._json('post', self.URL, {'compra_id': compra.id, 'filas': [
            self._fila(), self._fila(stock=-7), self._fila(nombre='x' * 210),
            self._fila(talla='__TOTAL__'),
        ]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(r.json()['errores']), 3)
        self.assertFalse(Compras_Producto.objects.filter(compras=compra).exists())

    def test_mezcla_con_y_sin_talla_es_error(self):
        compra = self._compra()
        r = self._json('post', self.URL, {'compra_id': compra.id, 'filas': [
            self._fila(talla='40'), self._fila(talla='', stock=60)]})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Compras_Producto.objects.filter(compras=compra).exists())

    def test_importa_valido_y_rechaza_compra_eliminada(self):
        compra = self._compra()
        r = self._json('post', self.URL, {'compra_id': compra.id, 'filas': [
            self._fila(talla='40'), self._fila(talla='41', stock=0), self._fila(nombre='ART-TOT', talla='', stock=12)]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['filas_omitidas_sin_stock'], 1)
        tallas = set(Compras_Producto_Talla.objects.filter(compra_producto__compras=compra)
                     .values_list('talla', 'stock', 'pendiente_distribuir'))
        self.assertEqual(tallas, {('40', 5, False), ('__TOTAL__', 12, True)})
        eliminada = self._compra(nombre='OC borrada', estado='ELIMINADA')
        r = self._json('post', self.URL, {'compra_id': eliminada.id, 'filas': [self._fila()]})
        self.assertEqual(r.status_code, 400)


class ActualizarCompraTest(_BaseV1A):

    def _payload(self, compra, **kw):
        d = {'empresa': compra.empresa_id, 'nombre': compra.nombre, 'fecha': compra.fecha.isoformat(),
             'es_historica': compra.es_historica}
        d.update(kw)
        return d

    def test_no_marca_historica_ni_cambia_proveedor_con_recepciones(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra)
        f = self._dte(9101, self.proveedor)
        self._recepcion(cpt, 2, dte=f)
        url = f'/app/actualizar_compra/{compra.id}/'
        r = self._json('post', url, self._payload(compra, es_historica=True))
        self.assertEqual(r.status_code, 400)
        r = self._json('post', url, self._payload(compra, empresa=self.otro_proveedor.id))
        self.assertEqual(r.status_code, 400)
        compra.refresh_from_db()
        self.assertFalse(compra.es_historica)
        self.assertEqual(compra.empresa_id, self.proveedor.id)

    def test_sin_recepciones_se_puede_cambiar(self):
        compra = self._compra()
        r = self._json('post', f'/app/actualizar_compra/{compra.id}/',
                       self._payload(compra, empresa=self.otro_proveedor.id))
        self.assertEqual(r.status_code, 200, r.content)


# =====================================================================
# Comandos de reparación
# =====================================================================
class ComandosComprasTest(_BaseV1A):

    def test_recalcular_avance_dry_run_y_apply(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra, tallas=(('40', 5),))
        self._recepcion(cpt, 3)
        out = io.StringIO()
        call_command('compras_recalcular_avance', '--compra', str(compra.id), stdout=out)
        cpt.refresh_from_db()
        self.assertEqual(cpt.unidades_recibidas, 0)
        self.assertIn('DRY-RUN', out.getvalue())
        call_command('compras_recalcular_avance', '--compra', str(compra.id), '--apply', stdout=io.StringIO())
        cpt.refresh_from_db()
        self.assertEqual(cpt.unidades_recibidas, 3)
        self.assertEqual(cpt.estado_item, 'recibido_parcial')

    def test_revisar_pendientes_solo_borra_anuladas_con_apply(self):
        viva = self._compra()
        _cp, (cpt_v,) = self._linea(viva)
        rec_viva = self._recepcion(cpt_v, 2)
        borrada = self._compra(nombre='OC borrada', estado='ELIMINADA')
        _cp, (cpt_b,) = self._linea(borrada)
        rec_borrada = self._recepcion(cpt_b, 2)
        out = io.StringIO()
        call_command('compras_revisar_recepciones_pendientes', stdout=out)
        self.assertTrue(Productos_Recepcionados.objects.filter(id=rec_borrada.id).exists())
        self.assertIn('anulada', out.getvalue())
        call_command('compras_revisar_recepciones_pendientes', '--apply', stdout=io.StringIO())
        self.assertFalse(Productos_Recepcionados.objects.filter(id=rec_borrada.id).exists())
        self.assertTrue(Productos_Recepcionados.objects.filter(id=rec_viva.id).exists())
