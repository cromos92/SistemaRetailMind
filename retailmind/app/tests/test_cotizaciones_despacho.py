"""
Tests del despacho diferido de cotizaciones con cuadratura por UNIDADES.

Regresión del bug donde un despacho parcial (facturado 5, despachado 2)
cerraba el ítem como "completado" y las unidades restantes quedaban sin
salida de stock para siempre. Cubre además el OK del Administrador
(validar_despacho_cotizacion) y la reversa que lo invalida.

Correr en BD local desechable:
    python manage.py test app.tests.test_cotizaciones_despacho
"""
import json

from django.test import TestCase, Client
from django.utils import timezone

from app.models import (
    Cotizacion_Empresa, Cotizacion_Empresa_Detalle, Cotizacion_Empresa_Detalle_SKU,
    Historial_Cotizacion, Dte, Dte_Productos, Movimientos_Producto,
    ModuloSistema, OpcionMenu, PermisoRol,
)

from .factories import (
    setup_entorno_completo, crear_usuario, crear_producto_con_talla,
    crear_empresa_user,
)


class DespachoDiferidoBase(TestCase):
    """Entorno común: cotización FACTURADA con un ítem de 5 uds sin SKU
    (despacho diferido) y su DTE con la línea pendiente."""

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.empresa = self.entorno['empresa']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.producto_talla = self.entorno['producto_talla']  # stock=10, costo=15000

        # Segundo SKU con costo distinto para probar el costo ponderado
        self.producto2, self.producto_talla2 = crear_producto_con_talla(
            self.sucursal, articulo='Zapatilla Test 2', talla='43',
            sku=1000002, stock=5, costo=25000,
        )

        hoy = timezone.localdate()
        self.cotizacion = Cotizacion_Empresa.objects.create(
            sucursal=self.sucursal,
            cliente=self.empresa,
            vendedor=self.vendedor,
            usuario_creador=self.user,
            numero_cotizacion='COT-TEST-0001',
            fecha_emision=hoy,
            fecha_validez=hoy,
            total=100000,
        )
        # Ítem SIN SKU: nació pendiente (despacho diferido), 5 unidades
        self.detalle = Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=self.cotizacion,
            numero_linea=1,
            descripcion='Producto por llegar',
            cantidad=5,
            precio_unitario=20000,
            subtotal=100000,
            es_producto_pendiente=True,
            nombre_producto_pendiente='Producto por llegar',
        )

        # DTE emitido con la línea pendiente (como deja registrar_pagos_ticket)
        self.dte = Dte.objects.create(
            emisor=self.empresa,
            receptor=self.empresa,
            numero_documento=777,
            tipo_documento='FACTURA ELECTRONICA',
            monto_con_iva=100000,
            monto_neto=84034,
            descuento=0,
            estado_pago='PAGADO',
            estado_dte='EMITIDO',
            responsable=self.user.username,
            fecha_emision=hoy,
            fecha_vencimiento=hoy,
            diasCredito=0,
            bultos=1,
            unidades_productos=5,
            tipo_transaccion='VENTA_PUBLICO',
            sucursal=self.sucursal,
            hora=timezone.localtime().time(),
        )
        self.linea_dte = Dte_Productos.objects.create(
            dte=self.dte,
            productoTalla=None,
            stock=5,
            costo=0,
            sobreprecio=0,
            precio=20000,
            precio_unitario=20000,
            monto_item=100000,
            descripcion='Producto por llegar',
            es_pendiente_despacho=True,
            cotizacion_detalle_id=self.detalle.id,
        )

        self.cotizacion.marcar_como_facturada('777', tiene_pendientes=True, dte=self.dte)

        # Cliente autenticado con sucursal en sesión
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        # Los endpoints de escritura de cotizaciones pasan por el middleware de
        # permisos: sin `gestion_cotizaciones.puede_ver` responden 403 antes de
        # entrar a la vista.
        self._dar_permiso_cotizaciones()

    # -- helpers --------------------------------------------------------

    def _asignar(self, producto_talla, cantidad):
        return self.client.post(
            '/app/api/cotizaciones/asignar-sku-pendiente/',
            data=json.dumps({
                'detalle_id': self.detalle.id,
                'producto_talla_id': producto_talla.id,
                'cantidad': cantidad,
            }),
            content_type='application/json',
        )

    def _validar(self):
        return self.client.post(
            '/app/api/cotizaciones/validar-despacho/',
            data=json.dumps({'cotizacion_id': self.cotizacion.id}),
            content_type='application/json',
        )

    def _dar_permiso_cotizaciones(self, rol=None, aprobar=False):
        """Concede `gestion_cotizaciones` al rol indicado.

        Los endpoints de escritura de cotizaciones están en `URL_PERMISO_MAP`
        (antes colgaban solo de @login_required y cualquier autenticado podía
        despachar stock por POST directo), así que sin este permiso el
        middleware responde 403 antes de llegar a la vista.
        """
        rol = rol or getattr(self.user, 'rol', 'vendedor')
        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='documentos', defaults={'nombre': 'Documentos', 'orden': 1})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='gestion_cotizaciones',
            defaults={'modulo': modulo, 'nombre': 'Gestion Cotizaciones', 'orden': 5})
        permiso, _ = PermisoRol.objects.get_or_create(rol=rol, opcion_menu=opcion)
        permiso.puede_ver = True
        if aprobar:
            permiso.puede_aprobar = True
        permiso.save()
        return permiso

    def _dar_permiso_aprobar(self, rol='vendedor'):
        return self._dar_permiso_cotizaciones(rol=rol, aprobar=True)


class DespachoParcialTest(DespachoDiferidoBase):

    def test_despacho_parcial_mantiene_item_pendiente(self):
        """Facturado 5, despachar 2 → el ítem sigue pendiente con saldo 3,
        el stock y los movimientos reflejan SOLO lo despachado, y la línea
        del DTE sigue esperando (regresión del cierre en falso)."""
        resp = self._asignar(self.producto_talla, 2)
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertFalse(data['item_completado'])
        self.assertEqual(data['saldo_pendiente_item'], 3)

        self.detalle.refresh_from_db()
        self.assertTrue(self.detalle.es_producto_pendiente)
        self.assertFalse(self.detalle.sku_asignado_post_factura)
        self.assertEqual(self.detalle.unidades_despachadas_post_factura, 2)
        self.assertEqual(self.detalle.unidades_pendientes_despacho, 3)

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 8)

        mov = Movimientos_Producto.objects.filter(
            dte=self.dte, concepto='DESPACHO_COTIZACION').first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.cantidad, -2)

        # La línea del DTE NO se completa hasta despachar todo
        self.linea_dte.refresh_from_db()
        self.assertTrue(self.linea_dte.es_pendiente_despacho)
        self.assertIsNone(self.linea_dte.productoTalla)

        self.cotizacion.refresh_from_db()
        self.assertEqual(
            self.cotizacion.estado_despacho, Cotizacion_Empresa.DESPACHO_PARCIAL)

    def test_completar_despacho_cierra_item_y_completa_dte(self):
        """2 uds SKU1 (costo 15000) + 3 uds SKU2 (costo 25000) → ítem cerrado,
        estado COMPLETADO, línea DTE con SKU principal (mayor cantidad) y
        costo promedio ponderado (2*15000+3*25000)/5 = 21000."""
        self._asignar(self.producto_talla, 2)
        resp = self._asignar(self.producto_talla2, 3)
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertTrue(data['item_completado'])
        self.assertTrue(data['despacho_completado'])

        self.detalle.refresh_from_db()
        self.assertFalse(self.detalle.es_producto_pendiente)
        self.assertTrue(self.detalle.sku_asignado_post_factura)
        self.assertEqual(self.detalle.unidades_pendientes_despacho, 0)

        self.linea_dte.refresh_from_db()
        self.assertFalse(self.linea_dte.es_pendiente_despacho)
        self.assertEqual(self.linea_dte.productoTalla_id, self.producto_talla2.id)
        self.assertEqual(self.linea_dte.costo, 21000)

        self.cotizacion.refresh_from_db()
        self.assertEqual(
            self.cotizacion.estado_despacho, Cotizacion_Empresa.DESPACHO_COMPLETADO)
        self.assertEqual(self.cotizacion.unidades_facturadas, 5)
        self.assertEqual(self.cotizacion.unidades_despachadas, 5)

    def test_sobre_despacho_rechazado(self):
        """Con saldo 3 no se pueden despachar 4 (antes se podía re-despachar
        el total completo cuantas veces se quisiera... o nada)."""
        self._asignar(self.producto_talla, 2)
        resp = self._asignar(self.producto_talla2, 4)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('entre 1 y 3', resp.json()['error'])

        # Nada cambió con el intento inválido
        self.detalle.refresh_from_db()
        self.assertEqual(self.detalle.unidades_pendientes_despacho, 3)

    def test_item_con_sku_al_facturar_no_se_puede_redespachar(self):
        """Un ítem que salió con el ticket (no nació pendiente) no acepta
        despachos post-factura: su stock ya salió."""
        detalle_con_sku = Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=self.cotizacion,
            numero_linea=2,
            descripcion='Item con SKU',
            cantidad=2,
            precio_unitario=10000,
            subtotal=20000,
            es_producto_pendiente=False,
            producto_existente=self.producto_talla,
        )
        resp = self.client.post(
            '/app/api/cotizaciones/asignar-sku-pendiente/',
            data=json.dumps({
                'detalle_id': detalle_con_sku.id,
                'producto_talla_id': self.producto_talla.id,
                'cantidad': 1,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)


class ValidacionDespachoTest(DespachoDiferidoBase):

    def test_validar_sin_permiso_403(self):
        resp = self._validar()
        self.assertEqual(resp.status_code, 403)

    def test_validar_con_descuadre_400(self):
        """Con permiso pero unidades pendientes, la validación se rechaza."""
        self._dar_permiso_aprobar()
        self._asignar(self.producto_talla, 2)  # quedan 3 pendientes
        resp = self._validar()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no cierra', resp.json()['error'])

    def test_validar_con_cuadratura_ok(self):
        self._dar_permiso_aprobar()
        self._asignar(self.producto_talla, 5)
        resp = self._validar()
        data = resp.json()
        self.assertTrue(data['success'], data)

        self.cotizacion.refresh_from_db()
        self.assertTrue(self.cotizacion.despacho_validado)
        self.assertEqual(self.cotizacion.despacho_validado_por_id, self.user.id)
        self.assertIsNotNone(self.cotizacion.fecha_validacion_despacho)
        self.assertTrue(
            Historial_Cotizacion.objects.filter(
                cotizacion=self.cotizacion, accion='DESPACHO_VALIDADO').exists()
        )

        # Doble validación rechazada
        resp2 = self._validar()
        self.assertEqual(resp2.status_code, 400)

    def test_revertir_invalida_el_ok_y_reintegra_stock(self):
        """La reversa de un despacho ya validado limpia el OK del admin,
        reabre el ítem y reintegra el stock."""
        self._dar_permiso_aprobar()
        self._asignar(self.producto_talla, 5)
        self._validar()

        # revertir exige rol administrador
        admin = crear_usuario(username='admin_rev', rol='administrador')
        crear_empresa_user(admin, self.empresa, self.sucursal)
        # El permiso del middleware es por ROL: hay que concederlo también para
        # 'administrador', no solo para el rol del usuario de la base.
        self._dar_permiso_cotizaciones(rol='administrador')
        self.client.force_login(admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        resp = self.client.post(
            '/app/api/cotizaciones/revertir-sku-despachado/',
            data=json.dumps({'detalle_id': self.detalle.id, 'motivo': 'SKU equivocado'}),
            content_type='application/json',
        )
        data = resp.json()
        self.assertTrue(data['success'], data)

        self.cotizacion.refresh_from_db()
        self.assertFalse(self.cotizacion.despacho_validado)
        self.assertIsNone(self.cotizacion.despacho_validado_por)

        self.detalle.refresh_from_db()
        self.assertTrue(self.detalle.es_producto_pendiente)
        self.assertEqual(self.detalle.unidades_pendientes_despacho, 5)
        self.assertFalse(
            Cotizacion_Empresa_Detalle_SKU.objects.filter(
                detalle=self.detalle, asignado_post_factura=True).exists()
        )

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 10)


class ListadoCuadraturaTest(DespachoDiferidoBase):

    def test_listar_expone_cuadratura(self):
        self._asignar(self.producto_talla, 2)
        resp = self.client.get('/app/api/cotizaciones/')
        data = resp.json()
        self.assertTrue(data['success'], data)
        cot = next(
            c for c in data['cotizaciones']
            if c['numero_cotizacion'] == 'COT-TEST-0001'
        )
        self.assertEqual(cot['unidades_facturadas'], 5)
        self.assertEqual(cot['unidades_despachadas'], 2)
        self.assertEqual(cot['unidades_pendientes'], 3)
        self.assertTrue(cot['tiene_despacho_pendiente'])
        self.assertFalse(cot['despacho_validado'])
        self.assertFalse(cot['puede_validar_despacho'])
