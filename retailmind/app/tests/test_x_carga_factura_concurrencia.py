"""
CC-13: el agente "Cargar desde factura" no debe ingresar dos veces la misma
factura cuando llegan dos órdenes de carga a la vez (doble clic, dos pestañas,
dos sesiones del mismo PDF).

  - La vista pasa la sesión LEIDA → CARGANDO con un UPDATE condicional: solo
    un pedido lanza el hilo.
  - El aplicador bloquea el DTE (advisory lock por transacción) y vuelve a
    medir lo ingresado antes de cada línea: una corrida con un plan viejo no
    ingresa lo que otra ya ingresó. Lo propio de la corrida se mide igual que
    lo ingresado (antes/después de cada línea), así el mismo código en dos
    colores (ROJO y MULTI) no se salta a sí mismo.
  - Las correcciones del chat se aplican sobre la sesión fresca y bloqueada.
  - Un DTE que no sirve para ingresar stock de la bodega (otra empresa) se
    avisa en la vista previa y la carga no se lanza.

Ejecutar (BD de test, NO producción):
    python manage.py test app.tests.test_x_carga_factura_concurrencia
"""
from unittest import mock

from django.test import TestCase, override_settings

from app.models import (
    AtributoOpcion, CargaFacturaPdf, Dte, Movimientos_Producto, Producto, Producto_Talla,
)
from app.services.carga_factura import aplicador as svc_aplicador
from app.services.carga_factura import web as svc_web
from app.services.carga_factura.aplicador import aplicar_linea
from app.services.carga_factura.facturas import ErrorCarga
from app.tests.factories import crear_empresa, crear_empresa_user, crear_usuario
from app.tests import test_carga_factura_web as base


@override_settings(MEDIA_ROOT=base.MEDIA_TMP, ANTHROPIC_API_KEY='sk-ant-test')
class TestCargaFacturaConcurrencia(TestCase):
    """Mismo escenario que test_carga_factura_web (factura 555 de NIKE)."""

    @classmethod
    def setUpTestData(cls):
        base.TestAgenteCargaFactura.setUpTestData.__func__(cls)

    setUp = base.TestAgenteCargaFactura.setUp
    _subir = base.TestAgenteCargaFactura._subir

    def _cargar(self, sesion_id, cliente=None):
        return (cliente or self.client).post(
            f'/app/carga-factura/{sesion_id}/cargar/',
            data={'idx': 0, 'opciones': {}}, content_type='application/json')

    def _stock_cargado(self):
        producto = Producto.objects.filter(articulo='HQ6034-001', sucursal=self.sucursal)
        return sum(Producto_Talla.objects.filter(producto__in=producto)
                   .values_list('stock', flat=True))

    # ------------------------------------------------------------ vista

    def test_doble_clic_solo_lanza_una_carga(self):
        sesion_id = self._subir()
        with mock.patch.object(svc_web, 'iniciar_carga') as iniciar:
            primera = self._cargar(sesion_id)
            segunda = self._cargar(sesion_id)
        self.assertTrue(primera.json()['success'], primera.json())
        self.assertEqual(segunda.status_code, 400)
        self.assertIn('en curso', segunda.json()['error'])
        self.assertEqual(iniciar.call_count, 1)
        self.assertEqual(CargaFacturaPdf.objects.get(id=sesion_id).estado, 'CARGANDO')

    def test_pedido_que_pierde_la_carrera_no_lanza_otra_carga(self):
        """Los dos pedidos leyeron LEIDA; mientras este planificaba, el otro
        pasó la sesión a CARGANDO. El UPDATE condicional lo detecta."""
        sesion_id = self._subir()
        planificar = svc_web.planificar

        def planificar_y_perder(*args, **kwargs):
            salida = planificar(*args, **kwargs)
            CargaFacturaPdf.objects.filter(id=sesion_id).update(estado='CARGANDO')
            return salida

        with mock.patch.object(svc_web, 'planificar', side_effect=planificar_y_perder), \
                mock.patch.object(svc_web, 'iniciar_carga') as iniciar:
            resp = self._cargar(sesion_id)
        self.assertEqual(resp.status_code, 400)
        iniciar.assert_not_called()
        # No se ingresó nada ni se pisó el estado del otro pedido.
        self.assertEqual(self._stock_cargado(), 0)
        self.assertEqual(CargaFacturaPdf.objects.get(id=sesion_id).estado, 'CARGANDO')

    def test_sin_permiso_de_crear_no_lanza_la_carga(self):
        sesion_id = self._subir()
        vendedor = crear_usuario(username='vendedor_cf', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        cliente = self.client_class()
        cliente.force_login(vendedor)
        s = cliente.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        with mock.patch.object(svc_web, 'iniciar_carga') as iniciar:
            resp = self._cargar(sesion_id, cliente)
        self.assertEqual(resp.status_code, 403, resp.content)
        iniciar.assert_not_called()
        self.assertEqual(CargaFacturaPdf.objects.get(id=sesion_id).estado, 'LEIDA')

    # --------------------------------------------------------- aplicador

    def test_corrida_con_plan_viejo_no_ingresa_dos_veces(self):
        """Dos corridas planificaron la misma factura antes de que ninguna
        cargara (ambas ven la línea NUEVA). La segunda, ya con el DTE
        bloqueado, ve que la línea entró y no la vuelve a ingresar."""
        sesion_id = self._subir()
        data = dict(CargaFacturaPdf.objects.get(id=sesion_id).facturas[0])
        f1 = svc_web._factura(data, self.user)
        planes1 = svc_web._motor(data).planificar_factura(f1, {})
        f2 = svc_web._factura(data, self.user)
        planes2 = svc_web._motor(data).planificar_factura(f2, {})
        self.assertEqual(planes2[0]['estado'], 'NUEVO')
        self.assertEqual(planes2[0]['ingreso_previo'], 0)

        r1 = aplicar_linea(planes1[0], f1, self.user, 's')
        self.assertTrue(r1['ok'], r1)
        movimientos = Movimientos_Producto.objects.filter(dte=self.dte).count()

        r2 = aplicar_linea(planes2[0], f2, self.user, 's')
        self.assertFalse(r2['ok'])
        self.assertTrue(r2.get('ya_cargado'), r2)
        self.assertIn('otra carga', r2['error'])
        self.assertEqual(self._stock_cargado(), 5)
        self.assertEqual(Movimientos_Producto.objects.filter(dte=self.dte).count(), movimientos)
        self.assertEqual(Producto.objects.filter(articulo='HQ6034-001').count(), 1)

    def test_lo_ingresado_por_la_misma_corrida_no_cuenta_como_ajeno(self):
        """Dos líneas de la misma factura con el mismo código: la segunda no
        se salta por lo que acaba de cargar la primera."""
        clave = ('articulo', 'HQ6034-001')
        plan = {'ingreso_clave': clave, 'ingreso_previo': 0, 'unidades': 3}
        ingresado = {'articulo': {'HQ6034-001': {'EDEL': 5}}, 'color': {}}
        with mock.patch.object(svc_aplicador, 'ingresado_contra_dte', return_value=ingresado):
            propia = {'dte': self.dte, '_ingresado_en_corrida': {
                'articulo': {'HQ6034-001': {'EDEL': 5}}, 'color': {}}}
            self.assertEqual(svc_aplicador._ingreso_ajeno(plan, propia), 0)
            ajena = {'dte': self.dte}
            self.assertEqual(svc_aplicador._ingreso_ajeno(plan, ajena), 5)
            self.assertEqual(svc_aplicador._ingreso_ajeno(dict(plan, forzar=True), ajena), 0)

    def test_lo_propio_de_otro_color_se_descuenta_pero_lo_ajeno_no(self):
        """Clave ('articulo', art) mide todos los colores: descuenta lo que la
        corrida cargó en ROJO, pero sigue viendo las 3 u MULTI que puso otra
        carga. Clave ('color', art, ROJO) solo mira ROJO."""
        art = '12-REBI-1'
        ingresado = {'articulo': {art: {'EDEL': 5}},
                     'color': {(art, 'ROJO'): {'EDEL': 2}, (art, 'MULTI'): {'EDEL': 3}}}
        corrida = {'dte': self.dte, '_ingresado_en_corrida': {
            'articulo': {art: {'EDEL': 2}}, 'color': {(art, 'ROJO'): {'EDEL': 2}}}}
        multi = {'ingreso_clave': ('articulo', art), 'ingreso_previo': 0}
        rojo = {'ingreso_clave': ('color', art, 'ROJO'), 'ingreso_previo': 0}
        self.assertEqual(svc_aplicador._ingreso_ajeno(multi, corrida, ingresado), 3)
        self.assertEqual(svc_aplicador._ingreso_ajeno(rojo, corrida, ingresado), 0)

    # -------------------------------------------------------------- hilo

    def test_hilo_no_repite_una_factura_ya_cargada(self):
        sesion_id = self._subir()
        self.assertTrue(self._cargar(sesion_id).json()['success'])
        antes = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]
        self.assertEqual(antes['_estado'], 'CARGADA', antes.get('_resultado'))

        # Una segunda orden que llegara al hilo igual (p. ej. de otro proceso).
        svc_web.cargar_en_segundo_plano(sesion_id, 0, {}, self.user.id)
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        self.assertEqual(sesion.facturas[0]['_estado'], 'CARGADA')
        self.assertEqual(sesion.facturas[0]['_resultado'], antes['_resultado'])
        self.assertEqual(sesion.mensajes[-1]['tipo'], 'error')
        self.assertIn('ya se cargó', sesion.mensajes[-1]['texto'])
        self.assertEqual(sesion.estado, 'LEIDA')
        self.assertEqual(self._stock_cargado(), 5)

    def test_busqueda_en_internet_no_se_lanza_si_la_sesion_ya_no_esta_leida(self):
        sesion_id = self._subir()
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        # Otro pedido la tomó después de que esta instancia se leyera.
        CargaFacturaPdf.objects.filter(id=sesion_id).update(estado='CARGANDO')
        with mock.patch.object(svc_web, '_lanzar') as lanzar:
            with self.assertRaises(ErrorCarga):
                svc_web.iniciar_investigacion(sesion, [(0, 1)], self.user)
        lanzar.assert_not_called()
        self.assertEqual(CargaFacturaPdf.objects.get(id=sesion_id).estado, 'CARGANDO')


def _lectura_colores(colores, articulo='12-REBI-1', marca='CHALADA'):
    """Una factura con `articulo` repetido, una línea por color (None = sin
    color leído); la línea i trae 1+i unidades en su propia talla."""
    comun = {'precio_venta_a_mano': None, 'precio_venta_a_mano_alternativa': None,
             'reparto_a_mano': [], 'marca': marca, 'genero': 'HOMBRE',
             'categoria': 'Calzado > Zapatillas', 'especialidades': [],
             'confianza': 'alta', 'dudas': ''}
    lineas = []
    for i, color in enumerate(colores):
        u = 1 + i
        lineas.append(dict(comun, articulo=articulo, descripcion=f'REBI {color or ""}'.strip(),
                           color=color, tallas=[{'talla': str(39 + i), 'cantidad': u}],
                           cantidad=u, precio_unitario=10000, importe=10000 * u))
    factura = {'tipo_documento': 'FACTURA ELECTRONICA', 'folio': 555,
               'proveedor_nombre': 'Proveedor Test', 'proveedor_rut': '77.111.111-1',
               'fecha_emision': '2026-07-01', 'marca': marca,
               'total_unidades': sum(l['cantidad'] for l in lineas),
               'total_neto': sum(l['importe'] for l in lineas),
               'paginas': [1], 'lineas': lineas, 'observaciones': ''}
    return {'lecturas': [{'facturas': [factura]}], 'modo': 'escaneo'}


@override_settings(MEDIA_ROOT=base.MEDIA_TMP, ANTHROPIC_API_KEY='sk-ant-test')
class TestMismaCorridaVariosColores(TestCase):
    """Marca con identidad_color=True (todas menos NIKE): el mismo código en
    una línea con color y otra con el color por defecto (MULTI). Sin ninguna
    concurrencia, todas las líneas deben entrar."""

    @classmethod
    def setUpTestData(cls):
        base.TestAgenteCargaFactura.setUpTestData.__func__(cls)
        AtributoOpcion.objects.create(atributo=cls.marca.atributo, valor='CHALADA')
        AtributoOpcion.objects.create(atributo=cls.color.atributo, valor='ROJO')

    setUp = base.TestAgenteCargaFactura.setUp
    _subir = base.TestAgenteCargaFactura._subir
    _planificar = base.TestAgenteCargaFactura._planificar

    def _cargar_todo(self, colores):
        sesion_id = self._subir(_lectura_colores(colores), marca='CHALADA')
        item = self._planificar(sesion_id)['facturas'][0]
        self.assertIsNone(item['error'], item)
        self.assertEqual(item['totales']['bloqueantes'], 0, item['planes'])
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/cargar/',
                                data={'idx': 0, 'opciones': {}}, content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.json())
        factura = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]
        resultado = factura['_resultado']
        self.assertEqual([l['estado'] for l in resultado['lineas']], ['OK'] * len(colores),
                         resultado['lineas'])
        self.assertEqual(factura['_estado'], 'CARGADA')
        esperadas = sum(1 + i for i in range(len(colores)))
        stock = sum(Producto_Talla.objects.filter(producto__articulo='12-REBI-1')
                    .values_list('stock', flat=True))
        self.assertEqual(stock, esperadas)
        return factura

    def test_rojo_y_luego_multi(self):
        self._cargar_todo(['ROJO', 'MULTI'])

    def test_multi_y_luego_rojo(self):
        self._cargar_todo(['MULTI', 'ROJO'])

    def test_rojo_y_luego_sin_color(self):
        self._cargar_todo(['ROJO', None])

    def test_multi_rojo_multi(self):
        self._cargar_todo(['MULTI', 'ROJO', 'MULTI'])

    def test_otra_carga_de_multi_se_detecta_aunque_la_corrida_cargue_rojo(self):
        """Otra carga metió MULTI después de planificar: la línea MULTI de
        esta corrida se salta aunque antes haya cargado ROJO del mismo código."""
        sesion_id = self._subir(_lectura_colores(['ROJO', 'MULTI']), marca='CHALADA')
        data = dict(CargaFacturaPdf.objects.get(id=sesion_id).facturas[0])
        f1 = svc_web._factura(data, self.user)
        planes1 = svc_web._motor(data).planificar_factura(f1, {})
        f2 = svc_web._factura(data, self.user)
        planes2 = svc_web._motor(data).planificar_factura(f2, {})
        # La otra corrida carga solo la línea MULTI.
        self.assertTrue(aplicar_linea(planes2[1], f2, self.user, 's')['ok'])
        r_rojo = aplicar_linea(planes1[0], f1, self.user, 's')
        self.assertTrue(r_rojo['ok'], r_rojo)
        r_multi = aplicar_linea(planes1[1], f1, self.user, 's')
        self.assertTrue(r_multi.get('ya_cargado'), r_multi)
        stock = sum(Producto_Talla.objects.filter(producto__articulo='12-REBI-1')
                    .values_list('stock', flat=True))
        self.assertEqual(stock, 1 + 2)


@override_settings(MEDIA_ROOT=base.MEDIA_TMP, ANTHROPIC_API_KEY='sk-ant-test')
class TestCorreccionConSesionRancia(TestCase):
    """El chat lee la sesión, espera a Claude y recién después corrige: la
    corrección se valida y se guarda sobre la fila fresca y bloqueada."""

    @classmethod
    def setUpTestData(cls):
        base.TestAgenteCargaFactura.setUpTestData.__func__(cls)

    setUp = base.TestAgenteCargaFactura.setUp
    _subir = base.TestAgenteCargaFactura._subir

    def test_no_pisa_una_carga_que_termino_mientras_tanto(self):
        sesion_id = self._subir()
        rancia = CargaFacturaPdf.objects.get(id=sesion_id)
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/cargar/',
                                data={'idx': 0, 'opciones': {}}, content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.json())
        cargada = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]
        self.assertEqual(cargada['_estado'], 'CARGADA')

        with self.assertRaises(ErrorCarga) as ctx:
            svc_web.aplicar_correcciones(rancia, [{'idx': 0, 'lineas': [{'descripcion': 'OTRA'}]}])
        self.assertIn('ya se cargó', str(ctx.exception))
        despues = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]
        self.assertEqual(despues['_estado'], 'CARGADA')
        self.assertEqual(despues['_resultado'], cargada['_resultado'])

    def test_no_corrige_si_la_sesion_ya_no_esta_leida(self):
        sesion_id = self._subir()
        rancia = CargaFacturaPdf.objects.get(id=sesion_id)
        CargaFacturaPdf.objects.filter(id=sesion_id).update(estado='CARGANDO')
        with self.assertRaises(ErrorCarga):
            svc_web.aplicar_correcciones(rancia, [{'idx': 0, 'marca': 'OTRA'}])
        self.assertEqual(CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]['marca'], 'NIKE')

    def test_corrige_sobre_lo_fresco_y_conserva_lo_que_otro_escribio(self):
        sesion_id = self._subir()
        rancia = CargaFacturaPdf.objects.get(id=sesion_id)
        # Otro hilo dejó algo en la factura después de que el chat la leyera.
        fresca = dict(rancia.facturas[0], _que_escribio_otro=True)
        svc_web._guardar_factura(sesion_id, 0, fresca)
        self.assertTrue(svc_web.aplicar_correcciones(
            rancia, [{'idx': 0, 'lineas': [{'descripcion': 'CORREGIDA'}]}]))
        guardada = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]
        self.assertTrue(guardada.get('_que_escribio_otro'))
        self.assertEqual(guardada['lineas'][0]['descripcion'], 'CORREGIDA')
        # El objeto del llamador queda con lo guardado.
        self.assertEqual(rancia.facturas[0]['lineas'][0]['descripcion'], 'CORREGIDA')


@override_settings(MEDIA_ROOT=base.MEDIA_TMP, ANTHROPIC_API_KEY='sk-ant-test')
class TestDteDeOtraEmpresa(TestCase):
    """La factura (folio + RUT) está registrada a otra empresa receptora:
    crear_producto_manual rechazaría cada línea (CC-03). La vista previa lo
    muestra y la carga no se lanza."""

    @classmethod
    def setUpTestData(cls):
        base.TestAgenteCargaFactura.setUpTestData.__func__(cls)
        otra = crear_empresa(nombre='Otra del holding', rut='76.999.999-9')
        Dte.objects.filter(id=cls.dte.id).update(receptor=otra)

    setUp = base.TestAgenteCargaFactura.setUp
    _subir = base.TestAgenteCargaFactura._subir
    _planificar = base.TestAgenteCargaFactura._planificar

    def test_previa_avisa_y_no_se_lanza_la_carga(self):
        sesion_id = self._subir()
        item = self._planificar(sesion_id)['facturas'][0]
        self.assertIn('otra empresa', item['error'] or '')
        self.assertTrue(any(c['id'] == self.dte.id for c in item['candidatos_dte']))
        with mock.patch.object(svc_web, 'iniciar_carga') as iniciar:
            resp = self.client.post(f'/app/carga-factura/{sesion_id}/cargar/',
                                    data={'idx': 0, 'opciones': {}},
                                    content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('otra empresa', resp.json()['error'])
        iniciar.assert_not_called()
        self.assertEqual(CargaFacturaPdf.objects.get(id=sesion_id).estado, 'LEIDA')
        self.assertFalse(Producto.objects.filter(articulo='HQ6034-001').exists())
