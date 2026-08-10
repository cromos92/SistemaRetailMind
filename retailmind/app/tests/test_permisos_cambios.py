import json

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import (
    CambioDevolucion,
    CambioDevolucionDetalle,
    CodigoAutorizacionDinamico,
    LoteProducto,
    ModuloSistema,
    OpcionMenu,
    PermisoRol,
    PermisoTemporalCambio,
    RegistroAutorizacion,
    Ticket,
    Ticket_Productos,
)
from .factories import (
    crear_empresa,
    crear_empresa_user,
    crear_lote_fifo,
    crear_producto_con_talla,
    crear_sucursal,
    crear_usuario,
    crear_vendedor,
)


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class PermisosCambiosTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa Permisos')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='SUC-PERM')
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.vendedor.sucursales.add(self.sucursal)

        self.admin = crear_usuario(username='admin-cambios', rol='administrador')
        self.jefe = crear_usuario(username='jefe-cambios', rol='jefe_local')
        self.operador = crear_usuario(username='vendedor-cambios', rol='vendedor')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        crear_empresa_user(self.jefe, self.empresa, self.sucursal)
        crear_empresa_user(self.operador, self.empresa, self.sucursal)

        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas-test', defaults={'nombre': 'Ventas Test'}
        )
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='cambios_devoluciones',
            defaults={'modulo': modulo, 'nombre': 'Cambios y devoluciones'},
        )
        PermisoRol.objects.update_or_create(
            rol='vendedor',
            opcion_menu=opcion,
            defaults={'puede_ver': True, 'puede_eliminar': True},
        )

        self.ticket_original = Ticket.objects.create(
            correlativo=901,
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            subTotal=20000,
            total=20000,
            estado='PAGADO',
            responsable='test',
            cliente_nombre='Cliente Prueba',
        )
        self.cambio = CambioDevolucion.objects.create(
            ticket_original=self.ticket_original,
            sucursal=self.sucursal,
            numero_operacion='CD-PERM-001',
            tipo_operacion='CAMBIO_SIMPLE',
            estado='SOLICITADO',
            fecha_limite_cambio=timezone.localdate() + timezone.timedelta(days=10),
            monto_original=20000,
            monto_nuevo=20000,
            diferencia_monto=0,
            solicitado_por=self.jefe,
            motivo_principal='TALLA_INCORRECTA',
        )

        self.client = Client()
        self.client.force_login(self.jefe)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _codigo_admin(self, admin=None, codigo='123456'):
        return CodigoAutorizacionDinamico.objects.create(
            codigo=codigo,
            fecha_hora_inicio=timezone.now() - timezone.timedelta(minutes=1),
            fecha_hora_fin=timezone.now() + timezone.timedelta(minutes=30),
            generado_por=admin or self.admin,
        )

    def _post_cancelar(self, **extra):
        payload = {
            'cambio_id': self.cambio.id,
            'motivo': 'Cliente desiste de la solicitud',
        }
        payload.update(extra)
        return self.client.post(
            reverse('cancelar_cambio_devolucion'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _post_aprobar(self, codigo, vendedor=None):
        return self.client.post(
            reverse('aprobar_cambio_generar_ticket'),
            data=json.dumps({
                'cambio_id': self.cambio.id,
                'vendedor_id': (vendedor or self.vendedor).id,
                'codigo_autorizacion': codigo,
                'observaciones': 'Prueba de autorizacion',
            }),
            content_type='application/json',
        )

    def test_jefe_sin_autorizacion_temporal_no_puede_cancelar(self):
        response = self._post_cancelar()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'TEMP_AUTH_REQUIRED')
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'SOLICITADO')

    def test_codigo_navbar_admin_otorga_permiso_y_cancela(self):
        codigo = self._codigo_admin()
        response = self._post_cancelar(
            codigo_autorizacion=codigo.codigo,
            minutos_autorizacion=30,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

        self.cambio.refresh_from_db()
        codigo.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'CANCELADO')
        self.assertTrue(codigo.usado)
        self.assertTrue(PermisoTemporalCambio.objects.filter(
            usuario=self.jefe,
            accion=PermisoTemporalCambio.ACCION_CANCELAR,
            sucursal=self.sucursal,
        ).exists())
        self.assertTrue(RegistroAutorizacion.objects.filter(
            cambio_devolucion=self.cambio,
            usuario_autorizador=self.admin,
            exitoso=True,
        ).exists())

    def test_otro_perfil_con_permiso_requiere_autorizacion_temporal(self):
        self.client.force_login(self.operador)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        response_sin_codigo = self._post_cancelar()
        self.assertEqual(response_sin_codigo.status_code, 403)
        self.assertEqual(response_sin_codigo.json()['code'], 'TEMP_AUTH_REQUIRED')

        codigo = self._codigo_admin(codigo='444555')
        response_autorizado = self._post_cancelar(codigo_autorizacion=codigo.codigo)
        self.assertEqual(response_autorizado.status_code, 200)
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'CANCELADO')

    def test_administrador_cancela_sin_codigo(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        response = self._post_cancelar()
        self.assertEqual(response.status_code, 200)
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'CANCELADO')

    def test_codigo_administrador_otra_empresa_es_rechazado(self):
        otra_empresa = crear_empresa(nombre='Empresa Ajena', rut='77.000.000-1')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='SUC-AJENA')
        otro_admin = crear_usuario(username='admin-ajeno', rol='administrador')
        crear_empresa_user(otro_admin, otra_empresa, otra_sucursal)
        codigo = self._codigo_admin(admin=otro_admin, codigo='654321')

        response = self._post_cancelar(codigo_autorizacion=codigo.codigo)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'CROSS_COMPANY_AUTH')
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'SOLICITADO')

    def test_codigo_navbar_de_jefe_local_no_autoriza(self):
        codigo = self._codigo_admin(admin=self.jefe, codigo='222333')

        response = self._post_cancelar(codigo_autorizacion=codigo.codigo)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'INVALID_AUTHORIZER')
        codigo.refresh_from_db()
        self.cambio.refresh_from_db()
        self.assertFalse(codigo.usado)
        self.assertEqual(self.cambio.estado, 'SOLICITADO')

    def test_no_revertir_cambio_completado_y_pagado(self):
        ticket_nuevo = Ticket.objects.create(
            correlativo=902,
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            subTotal=0,
            total=0,
            estado='PAGADO',
            responsable='test',
        )
        self.cambio.estado = 'COMPLETADO'
        self.cambio.ticket_nuevo = ticket_nuevo
        self.cambio.save(update_fields=['estado', 'ticket_nuevo'])

        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        response = self.client.post(
            reverse('revertir_cambio_devolucion'),
            data=json.dumps({
                'cambio_id': self.cambio.id,
                'motivo': 'Error detectado después del pago',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'INVALID_STATE')
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'COMPLETADO')

    def test_aprobacion_exige_codigo_navbar(self):
        response = self.client.post(
            reverse('aprobar_cambio_generar_ticket'),
            data=json.dumps({
                'cambio_id': self.cambio.id,
                'vendedor_id': self.vendedor.id,
                'observaciones': '',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'AUTH_CODE_REQUIRED')

    def _codigo_de(self, usuario, codigo):
        return CodigoAutorizacionDinamico.objects.create(
            codigo=codigo,
            fecha_hora_inicio=timezone.now() - timezone.timedelta(minutes=1),
            fecha_hora_fin=timezone.now() + timezone.timedelta(minutes=30),
            generado_por=usuario,
        )

    def _marcar_fuera_de_plazo(self, autorizado_por=None):
        self.cambio.es_fuera_de_plazo = True
        self.cambio.dias_fuera_de_plazo = 5
        self.cambio.tipo_cambio_especial = 'FUERA_PLAZO'
        self.cambio.fecha_limite_cambio = timezone.localdate() - timezone.timedelta(days=5)
        self.cambio.autorizado_por_usuario = autorizado_por
        self.cambio.save(update_fields=[
            'es_fuera_de_plazo', 'dias_fuera_de_plazo', 'tipo_cambio_especial',
            'fecha_limite_cambio', 'autorizado_por_usuario',
        ])

    def test_fuera_de_plazo_ya_autorizado_no_pide_segundo_codigo_admin(self):
        """La excepción se firma al crear: aprobar no debe exigir otro código de admin.

        El código dinámico es de un solo uso, así que exigirlo dos veces obliga al
        administrador a emitir un segundo código para la misma operación.
        """
        self._marcar_fuera_de_plazo(autorizado_por=self.admin)
        codigo_jefe = self._codigo_de(self.jefe, '555111')

        response = self._post_aprobar(codigo_jefe.codigo)

        self.assertNotEqual(response.json().get('code'), 'ADMIN_REQUIRED')
        self.assertNotEqual(response.status_code, 403)

    def test_fuera_de_plazo_sin_autorizacion_previa_sigue_exigiendo_admin(self):
        """Sin firma de administrador (filas legacy) la excepción no se puede aprobar sola."""
        self._marcar_fuera_de_plazo(autorizado_por=None)
        codigo_jefe = self._codigo_de(self.jefe, '555222')

        response = self._post_aprobar(codigo_jefe.codigo)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'ADMIN_REQUIRED')
        codigo_jefe.refresh_from_db()
        self.assertFalse(codigo_jefe.usado)

    def test_fuera_de_plazo_autorizado_por_no_administrador_exige_admin(self):
        """La firma previa solo vale si viene de un administrador activo."""
        self._marcar_fuera_de_plazo(autorizado_por=self.jefe)
        codigo_jefe = self._codigo_de(self.jefe, '555444')

        response = self._post_aprobar(codigo_jefe.codigo)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'ADMIN_REQUIRED')

    def test_cambio_creado_en_plazo_no_exige_admin_por_vencer_esperando_aprobacion(self):
        """Vencer mientras espera aprobación no convierte el cambio en una excepción."""
        self.cambio.fecha_limite_cambio = timezone.localdate() - timezone.timedelta(days=2)
        self.cambio.save(update_fields=['fecha_limite_cambio'])
        codigo_jefe = self._codigo_de(self.jefe, '555333')

        response = self._post_aprobar(codigo_jefe.codigo)

        self.assertNotEqual(response.json().get('code'), 'ADMIN_REQUIRED')
        self.assertNotEqual(response.status_code, 403)

    def test_aprobacion_normal_acepta_codigo_jefe_local_de_otra_empresa(self):
        """El código de un jefe de local sirve para cambios normales de cualquier empresa."""
        otra_empresa = crear_empresa(nombre='Empresa Jefe Ajeno', rut='76.333.333-3')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='SUC-JEFE-AJENA')
        jefe_ajeno = crear_usuario(username='jefe-ajeno', rol='jefe_local')
        crear_empresa_user(jefe_ajeno, otra_empresa, otra_sucursal)
        codigo = self._codigo_de(jefe_ajeno, '777888')

        response = self._post_aprobar(codigo.codigo)

        self.assertNotEqual(response.status_code, 403)
        self.assertNotIn(
            response.json().get('code'),
            ('CROSS_COMPANY_AUTH', 'INVALID_AUTHORIZER', 'ADMIN_REQUIRED'),
        )
        registro = RegistroAutorizacion.objects.filter(
            cambio_devolucion=self.cambio,
            usuario_autorizador=jefe_ajeno,
            exitoso=True,
        ).first()
        if registro is not None:
            self.assertTrue(registro.es_cross_branch)
            self.assertTrue(registro.requiere_revision)

    def test_fuera_de_plazo_rechaza_codigo_admin_de_otra_empresa(self):
        """La excepción de plazo sigue exigiendo un administrador de la MISMA empresa."""
        self._marcar_fuera_de_plazo(autorizado_por=None)
        otra_empresa = crear_empresa(nombre='Empresa Admin Ajeno FP', rut='76.444.444-4')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='SUC-ADM-FP')
        admin_ajeno = crear_usuario(username='admin-ajeno-fp', rol='administrador')
        crear_empresa_user(admin_ajeno, otra_empresa, otra_sucursal)
        codigo = self._codigo_de(admin_ajeno, '888999')

        response = self._post_aprobar(codigo.codigo)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'CROSS_COMPANY_AUTH')
        codigo.refresh_from_db()
        self.cambio.refresh_from_db()
        self.assertFalse(codigo.usado)
        self.assertEqual(self.cambio.estado, 'SOLICITADO')

    def test_aprobacion_normal_rechaza_codigo_de_rol_no_supervisor(self):
        """Un código generado por un rol sin atribuciones (vendedor) no aprueba cambios."""
        codigo = self._codigo_de(self.operador, '999000')

        response = self._post_aprobar(codigo.codigo)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'INVALID_AUTHORIZER')
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    @override_settings(CSRF_FAILURE_VIEW='django.views.csrf.csrf_failure')
    def test_crear_cambio_rechaza_post_sin_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.cookies.update(self.client.cookies)

        response = csrf_client.post(
            reverse('crear_cambio_devolucion'),
            data=json.dumps({}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)

    def test_aprobacion_rechaza_vendedor_de_otra_empresa_sin_consumir_codigo(self):
        otra_empresa = crear_empresa(nombre='Empresa Vendedor Ajeno', rut='76.111.111-1')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='SUC-VEND-AJENA')
        vendedor_ajeno = crear_vendedor(
            nombre='Vendedor Ajeno',
            empresa=otra_empresa,
            codigo_vendedor='VAJENO',
            rut='11.111.111-1',
        )
        vendedor_ajeno.sucursales.add(otra_sucursal)
        codigo = self._codigo_admin(codigo='333444')

        response = self._post_aprobar(codigo.codigo, vendedor=vendedor_ajeno)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'SELLER_NOT_AVAILABLE')
        codigo.refresh_from_db()
        self.cambio.refresh_from_db()
        self.assertFalse(codigo.usado)
        self.assertEqual(self.cambio.estado, 'SOLICITADO')
        self.assertIsNone(self.cambio.ticket_nuevo_id)

    def test_validar_codigo_vendedor_usa_contexto_del_cambio(self):
        response_valido = self.client.post(
            reverse('validar_codigo_vendedor'),
            data=json.dumps({
                'codigo': self.vendedor.codigo_vendedor,
                'cambio_id': self.cambio.id,
            }),
            content_type='application/json',
        )
        self.assertEqual(response_valido.status_code, 200)
        self.assertEqual(response_valido.json()['vendedor']['id'], self.vendedor.id)

        otra_empresa = crear_empresa(nombre='Empresa Codigo Ajeno', rut='76.222.222-2')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='SUC-COD-AJENA')
        vendedor_ajeno = crear_vendedor(
            nombre='Codigo Ajeno',
            empresa=otra_empresa,
            codigo_vendedor='COD-AJENO',
            rut='22.222.222-2',
        )
        vendedor_ajeno.sucursales.add(otra_sucursal)
        response_ajeno = self.client.post(
            reverse('validar_codigo_vendedor'),
            data=json.dumps({
                'codigo': vendedor_ajeno.codigo_vendedor,
                'cambio_id': self.cambio.id,
            }),
            content_type='application/json',
        )
        self.assertEqual(response_ajeno.status_code, 404)
        self.assertEqual(response_ajeno.json()['code'], 'SELLER_NOT_AVAILABLE')

    def test_stock_repetido_se_valida_por_cantidad_total(self):
        _, producto_nuevo = crear_producto_con_talla(
            self.sucursal,
            articulo='Ultima Unidad',
            sku=880001,
            stock=1,
            precioventa=25000,
        )
        crear_lote_fifo(producto_nuevo, cantidad=1)
        for _ in range(2):
            CambioDevolucionDetalle.objects.create(
                cambio_devolucion=self.cambio,
                producto_original=None,
                cantidad_original=0,
                producto_nuevo=producto_nuevo,
                cantidad_nueva=1,
                precio_nuevo=25000,
                condicion_producto='PERFECTO',
                apto_para_venta=True,
                precio_original_unitario=0,
            )
        codigo = self._codigo_admin(codigo='555666')

        response = self._post_aprobar(codigo.codigo)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'STOCK_CHANGED')
        producto_nuevo.refresh_from_db()
        codigo.refresh_from_db()
        self.cambio.refresh_from_db()
        self.assertEqual(producto_nuevo.stock, 1)
        self.assertFalse(codigo.usado)
        self.assertEqual(self.cambio.estado, 'SOLICITADO')
        self.assertIsNone(self.cambio.ticket_nuevo_id)

    def test_reversion_bloqueada_si_producto_devuelto_ya_no_tiene_stock(self):
        _, producto_original = crear_producto_con_talla(
            self.sucursal,
            articulo='Producto Ya Vendido',
            sku=880002,
            stock=0,
            precioventa=20000,
        )
        linea_original = Ticket_Productos.objects.create(
            idTicket=self.ticket_original,
            ProductoTalla=producto_original,
            stock=1,
            precio=20000,
            precio_original=20000,
            subtotal=20000,
        )
        CambioDevolucionDetalle.objects.create(
            cambio_devolucion=self.cambio,
            producto_original=linea_original,
            cantidad_original=1,
            producto_nuevo=None,
            cantidad_nueva=0,
            precio_nuevo=0,
            condicion_producto='PERFECTO',
            apto_para_venta=True,
            precio_original_unitario=20000,
        )
        ticket_pendiente = Ticket.objects.create(
            correlativo=903,
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            subTotal=20000,
            total=20000,
            estado='PENDIENTE',
            responsable='test',
            modulo_origen='CAMBIO_DEVOLUCION',
        )
        self.cambio.estado = 'EJECUTADO_DEVOL_PENDIENTE'
        self.cambio.ticket_nuevo = ticket_pendiente
        self.cambio.save(update_fields=['estado', 'ticket_nuevo'])
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        response = self.client.post(
            reverse('revertir_cambio_devolucion'),
            data=json.dumps({
                'cambio_id': self.cambio.id,
                'motivo': 'Producto devuelto ya fue vendido',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'STOCK_REVERSAL_CONFLICT')
        producto_original.refresh_from_db()
        ticket_pendiente.refresh_from_db()
        self.cambio.refresh_from_db()
        self.assertEqual(producto_original.stock, 0)
        self.assertEqual(ticket_pendiente.estado, 'PENDIENTE')
        self.assertEqual(self.cambio.estado, 'EJECUTADO_DEVOL_PENDIENTE')

    def test_ejecucion_y_reversion_mantienen_stock_fifo_y_kardex(self):
        _, producto_original = crear_producto_con_talla(
            self.sucursal,
            articulo='Producto Devuelto',
            sku=880010,
            stock=0,
            costo=10000,
            precioventa=20000,
        )
        _, producto_nuevo = crear_producto_con_talla(
            self.sucursal,
            articulo='Producto Entregado',
            sku=880011,
            stock=2,
            costo=12000,
            precioventa=25000,
        )
        crear_lote_fifo(
            producto_nuevo,
            cantidad=2,
            costo_unitario=12000,
            precio_venta_unitario=25000,
        )
        linea_original = Ticket_Productos.objects.create(
            idTicket=self.ticket_original,
            ProductoTalla=producto_original,
            stock=1,
            precio=20000,
            precio_original=20000,
            subtotal=20000,
        )
        CambioDevolucionDetalle.objects.create(
            cambio_devolucion=self.cambio,
            producto_original=linea_original,
            cantidad_original=1,
            producto_nuevo=producto_nuevo,
            cantidad_nueva=1,
            precio_nuevo=25000,
            condicion_producto='PERFECTO',
            apto_para_venta=True,
            precio_original_unitario=20000,
        )
        self.cambio.tipo_operacion = 'CAMBIO_CON_DIFERENCIA'
        self.cambio.monto_original = 20000
        self.cambio.monto_nuevo = 25000
        self.cambio.diferencia_monto = 5000
        self.cambio.save(update_fields=[
            'tipo_operacion', 'monto_original', 'monto_nuevo', 'diferencia_monto'
        ])
        codigo = self._codigo_admin(codigo='777888')

        response_aprobar = self._post_aprobar(codigo.codigo)
        self.assertEqual(response_aprobar.status_code, 200, response_aprobar.content)

        producto_original.refresh_from_db()
        producto_nuevo.refresh_from_db()
        self.cambio.refresh_from_db()
        self.assertEqual(producto_original.stock, 1)
        self.assertEqual(producto_nuevo.stock, 1)
        self.assertEqual(
            sum(LoteProducto.objects.filter(
                producto_talla=producto_original,
                activo=True,
                agotado=False,
            ).values_list('cantidad_disponible', flat=True)),
            1,
        )
        self.assertEqual(
            sum(LoteProducto.objects.filter(
                producto_talla=producto_nuevo,
                activo=True,
                agotado=False,
            ).values_list('cantidad_disponible', flat=True)),
            1,
        )
        self.assertEqual(self.cambio.estado, 'EJECUTADO_COBRO_PENDIENTE')
        self.assertEqual(self.cambio.ticket_nuevo.estado, 'PENDIENTE')

        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        response_revertir = self.client.post(
            reverse('revertir_cambio_devolucion'),
            data=json.dumps({
                'cambio_id': self.cambio.id,
                'motivo': 'Prueba de rollback integral',
            }),
            content_type='application/json',
        )
        self.assertEqual(response_revertir.status_code, 200, response_revertir.content)

        producto_original.refresh_from_db()
        producto_nuevo.refresh_from_db()
        self.cambio.refresh_from_db()
        self.cambio.ticket_nuevo.refresh_from_db()
        self.assertEqual(producto_original.stock, 0)
        self.assertEqual(producto_nuevo.stock, 2)
        self.assertEqual(
            sum(LoteProducto.objects.filter(
                producto_talla=producto_original,
                activo=True,
                agotado=False,
            ).values_list('cantidad_disponible', flat=True)),
            0,
        )
        self.assertEqual(
            sum(LoteProducto.objects.filter(
                producto_talla=producto_nuevo,
                activo=True,
                agotado=False,
            ).values_list('cantidad_disponible', flat=True)),
            2,
        )
        self.assertEqual(self.cambio.estado, 'REVERTIDO')
        self.assertEqual(self.cambio.ticket_nuevo.estado, 'ANULADO')

    def test_flujos_legacy_no_permiten_saltar_autorizacion(self):
        for url_name, payload in (
            ('aprobar_cambio_devolucion', {
                'cambio_id': self.cambio.id,
                'accion': 'aprobar',
            }),
            ('ejecutar_cambio_devolucion', {
                'cambio_id': self.cambio.id,
            }),
            ('registrar_pago_diferencia', {
                'cambio_id': self.cambio.id,
                'metodo_pago': 'EFECTIVO',
                'monto': 0,
            }),
            ('completar_cambio_devolucion', {
                'cambio_id': self.cambio.id,
            }),
        ):
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse(url_name),
                    data=json.dumps(payload),
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 410)
                self.assertEqual(response.json()['code'], 'LEGACY_FLOW_DISABLED')
