"""
Ronda 2 de mejoras de Compras (unidad R2V1, 26/27-sep-2026): views.py —
Gestión Compras, paso 5 (crear desde recepción / manual), Lotes FIFO y
proveedores.

Cubre:
- A3-01 (2)   crear_lote_manual / ajustar_lote: permiso fino + alcance por
              empresa ANTES de escribir; errores genéricos (sin str(e)).
- B1-01/B4-05/B13-08  permisos finos de los escritores de Gestión Compras
              (puede_crear / puede_editar / puede_eliminar según la acción) y
              crear_proveedor con gestion_dte_compras/puede_crear.
- B6-04       revertir_producto_a_pendiente responde 409 con un traspaso de
              la talla todavía en viaje.
- B14-01/B2-03  compras ELIMINADA/CANCELADA fuera de "Crear productos" y
              rechazadas al crear; B14-05 enlace movimiento_ingreso sin DTE.
- B14-02 (2)  crear_producto_manual: 409 needs_confirmation por reuso de
              factura, confirmar_exceso_factura lo deja pasar.
- B4-12       agregar_producto_manual_a_compra sin DTE y sin huérfanos.
- B4-17 / B4-16  filtros de la recepción antes de paginar; unidades_por_crear.
- B15-01 / B14-02 (4) / B1-09 (2)  facturas_pendientes sin NC/ND y con monto,
              usado, pagado, tipo_documento y recibidas_uds.
- B14-04      responsable = usuario real (no 'Sistema').
- CC-15       escritores sin cobertura: distribuir tallas, eliminar pendientes
              masivo, guardar curva, guardar márgenes.

Ejecutar (BD de test aislada, NO producción):
    python manage.py test app.tests.test_r2v1_compras --keepdb --noinput
"""
import json

from django.test import TestCase
from django.utils import timezone

from app.models import (
    AtributoOpcion, Categoria, Compras, Compras_Producto, Compras_Producto_Talla,
    CurvaDistribucion, Dte, Dte_Detalle_Pago, Dte_Productos, Empresa, EmpresaUser,
    LoteProducto, ModuloSistema, Movimientos_Producto, OpcionMenu, PermisoRol, Producto,
    Producto_Talla, Productos_Atributos, Productos_Recepcionados,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)

TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')


def permiso(rol, codigo, **flags):
    """PermisoRol explícito: todos los flags en False salvo los pedidos."""
    modulo, _ = ModuloSistema.objects.get_or_create(codigo='r2v1_test', defaults={'nombre': 'R2V1'})
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo=codigo, defaults={'modulo': modulo, 'nombre': codigo, 'activo': True})
    if not opcion.activo:
        opcion.activo = True
        opcion.save(update_fields=['activo'])
    valores = {t: False for t in TIPOS}
    valores.update(flags)
    PermisoRol.objects.update_or_create(rol=rol, opcion_menu=opcion, defaults=valores)


def crear_dte(emisor, receptor, numero, tipo_transaccion='COMPRA',
              tipo_documento='FACTURA ELECTRONICA', **kwargs):
    hoy = timezone.localdate()
    datos = dict(
        emisor=emisor, receptor=receptor, numero_documento=numero,
        tipo_documento=tipo_documento, monto_neto=100000, monto_con_iva=119000,
        estado_pago='PENDIENTE', estado_dte='ACEPTADO', responsable='test',
        fecha_emision=hoy, fecha_vencimiento=hoy,
        diasCredito=30, bultos=1, unidades_productos=0,
        tipo_transaccion=tipo_transaccion,
    )
    datos.update(kwargs)
    return Dte.objects.create(**datos)


class BaseR2V1(TestCase):
    """Dos empresas del holding (A y B), un proveedor, un maestro y un
    jefe_local de la empresa A (sus permisos los fija cada test)."""

    _n = 0

    @classmethod
    def setUpTestData(cls):
        cls.emp_a = crear_empresa(nombre='R2 Empresa A', rut='76.910.100-1')
        cls.emp_b = crear_empresa(nombre='R2 Empresa B', rut='76.920.200-2')
        cls.suc_a = crear_sucursal(empresa=cls.emp_a, alias='R2-BOD-A')
        cls.suc_a2 = crear_sucursal(empresa=cls.emp_a, alias='R2-TIENDA-A')
        cls.suc_b = crear_sucursal(empresa=cls.emp_b, alias='R2-BOD-B')
        cls.proveedor = crear_empresa(nombre='R2 Proveedor', rut='77.930.300-3', esProveedor=True)
        cls.maestro = crear_usuario(username='r2v1_maestro', rol='maestro')
        crear_empresa_user(cls.maestro, cls.emp_a, cls.suc_a)
        cls.jefe = crear_usuario(username='r2v1_jefe', rol='jefe_local',
                                 first_name='Juana', last_name='Bodega')
        crear_empresa_user(cls.jefe, cls.emp_a, cls.suc_a)

    def entrar(self, user, sucursal=None, nombre_sesion=True):
        sucursal = sucursal or self.suc_a
        self.client.force_login(user)
        s = self.client.session
        s['idSucursalActual'] = sucursal.id
        s['idEmpresaActual'] = sucursal.empresa_id
        if nombre_sesion:
            s['nombreUsuario'] = user.username
        s.save()

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type='application/json',
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def compra(self, estado='ACTIVA', nombre='ART-1', tallas=(('40', 5),), sucursal_destino=None,
               costo=1000, fecha=None, **kwargs):
        BaseR2V1._n += 1
        compra = Compras.objects.create(
            empresa=self.proveedor, nombre=f'OC R2 {BaseR2V1._n}', correlativo=BaseR2V1._n,
            responsable='test', temporada='', estado=estado,
            fecha=fecha or timezone.localdate(), **kwargs)
        cp = Compras_Producto.objects.create(
            compras=compra, nombre=nombre, atributo1='M', atributo2='C', atributo3='G',
            atributo4='', costo=costo, precioSugerido=2000, sucursal_destino=sucursal_destino)
        cpts = [Compras_Producto_Talla.objects.create(compra_producto=cp, stock=s, talla=t)
                for t, s in tallas]
        return compra, cp, cpts

    def recepcion(self, cpt, cantidad, dte=None, pt=None, sucursal_destino=None):
        return Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, producto_talla=pt, stockArribado=cantidad,
            dte=dte, sucursal_destino=sucursal_destino)


# =========================================================================
# B1-01 / B4-05 / B13-08 — permisos finos de los escritores de Compras
# =========================================================================
class PermisosEscritoresComprasTest(BaseR2V1):

    def setUp(self):
        # jefe_local VE la pantalla (pasa el middleware) pero no crea,
        # edita ni elimina.
        permiso('jefe_local', 'gestion_compras', puede_ver=True)

    def form_compra(self):
        hoy = timezone.localdate()
        return {
            'empresa': str(self.proveedor.id), 'nombre': 'OC PERMISOS',
            'fechaInicioTemporada': hoy.isoformat(), 'fechaTerminoTemporada': hoy.isoformat(),
            'fecha_compra': hoy.isoformat(), 'temporada_familia': 'VERANO',
            'temporada_anio': str(hoy.year),
        }

    def assert403(self, r):
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.assertIn('permiso', r.json().get('error', '').lower())

    def test_crear_compra_exige_puede_crear(self):
        self.entrar(self.jefe)
        antes = Compras.objects.count()
        r = self.client.post('/app/crear_compra/', self.form_compra(), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assert403(r)
        self.assertEqual(Compras.objects.count(), antes)
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_crear=True)
        r = self.client.post('/app/crear_compra/', self.form_compra(), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(Compras.objects.count(), antes + 1)

    def test_maestro_pasa_sin_filas(self):
        self.entrar(self.maestro)
        r = self.client.post('/app/crear_compra/', self.form_compra(), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_importar_csv_exige_puede_crear(self):
        compra, cp, _ = self.compra()
        self.entrar(self.jefe)
        r = self.post_json('/app/importar_csv_compra/', {
            'compra_id': compra.id, 'filas': [{'nombre': 'X', 'talla': '40', 'stock': 1, 'costo': 10}]})
        self.assert403(r)
        self.assertEqual(Compras_Producto.objects.filter(compras=compra).count(), 1)

    def test_guardar_recepcion_exige_puede_editar(self):
        compra, _, (cpt,) = self.compra()
        self.entrar(self.jefe)
        r = self.post_json('/app/guardar_recepcion/', {
            'compra_id': compra.id,
            'recepciones': [{'compra_producto_talla_id': cpt.id, 'recepcionado': 2}]})
        self.assert403(r)
        self.assertFalse(Productos_Recepcionados.objects.filter(compra_producto_talla=cpt).exists())
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_editar=True)
        r = self.post_json('/app/guardar_recepcion/', {
            'compra_id': compra.id,
            'recepciones': [{'compra_producto_talla_id': cpt.id, 'recepcionado': 2}]})
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_agregar_producto_manual_exige_puede_crear(self):
        compra, _, _ = self.compra()
        self.entrar(self.jefe)
        r = self.post_json('/app/agregar_producto_manual/', {
            'compra_id': compra.id, 'nombre': 'NUEVO', 'tallas': [{'talla': '40', 'stock': 1}]})
        self.assert403(r)
        self.assertFalse(Compras_Producto.objects.filter(compras=compra, nombre='NUEVO').exists())

    def test_distribuir_y_curva_exigen_permiso(self):
        compra, cp, _ = self.compra(tallas=())
        Compras_Producto_Talla.objects.create(
            compra_producto=cp, stock=4, talla='__TOTAL__', pendiente_distribuir=True)
        self.entrar(self.jefe)
        r = self.post_json('/app/api/distribuir-tallas-compra-producto/', {
            'compra_producto_id': cp.id, 'distribucion': [{'talla': '40', 'stock': 4}]})
        self.assert403(r)
        r = self.post_json('/app/api/curvas-distribucion/guardar/', {
            'nombre': 'CURVA X', 'items': [{'talla': '40', 'porcentaje': 100}]})
        self.assert403(r)
        self.assertFalse(CurvaDistribucion.objects.filter(nombre='CURVA X').exists())

    def test_editar_recepciones_sin_eliminar_no_borra(self):
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_editar=True)
        compra, _, (cpt,) = self.compra()
        rec = self.recepcion(cpt, 3)
        self.entrar(self.jefe)
        # Cambiar la cantidad: basta puede_editar.
        r = self.post_json('/app/actualizar_recepciones_compra/', {
            'compra_id': compra.id, 'cambios': [{'recepcion_id': rec.id, 'cantidad': 2}]})
        self.assertEqual(r.status_code, 200, r.content[:300])
        rec.refresh_from_db()
        self.assertEqual(rec.stockArribado, 2)
        # Eliminar (o dejar en 0): además puede_eliminar.
        for cambio in ({'recepcion_id': rec.id, 'eliminar': True},
                       {'recepcion_id': rec.id, 'cantidad': 0}):
            r = self.post_json('/app/actualizar_recepciones_compra/', {
                'compra_id': compra.id, 'cambios': [cambio]})
            self.assert403(r)
        self.assertTrue(Productos_Recepcionados.objects.filter(id=rec.id).exists())

    def test_eliminar_producto_compra_post_atomico_y_puede_eliminar(self):
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_crear=True, puede_editar=True)
        compra, cp, _ = self.compra()
        self.entrar(self.jefe)
        r = self.post_json('/app/eliminar_producto_compra/', {'compra_producto_id': cp.id})
        self.assert403(r)
        self.assertTrue(Compras_Producto.objects.filter(id=cp.id).exists())
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_eliminar=True)
        r = self.client.get('/app/eliminar_producto_compra/', {'compra_producto_id': cp.id})
        self.assertEqual(r.status_code, 405)
        r = self.post_json('/app/eliminar_producto_compra/', {'compra_producto_id': 99999999})
        self.assertEqual(r.status_code, 404, r.content[:300])
        r = self.post_json('/app/eliminar_producto_compra/', {'compra_producto_id': cp.id})
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertFalse(Compras_Producto.objects.filter(id=cp.id).exists())

    def test_eliminar_producto_con_recepciones_400(self):
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_eliminar=True)
        compra, cp, (cpt,) = self.compra()
        self.recepcion(cpt, 1)
        self.entrar(self.jefe)
        r = self.post_json('/app/eliminar_producto_compra/', {'compra_producto_id': cp.id})
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertTrue(Compras_Producto.objects.filter(id=cp.id).exists())

    def test_crear_proveedor_exige_puede_crear_de_documentos(self):
        permiso('jefe_local', 'gestion_dte_compras', puede_ver=True)
        self.entrar(self.jefe)
        datos = {
            'nombre': 'Proveedor Nuevo R2', 'rut': '76.086.428-5', 'nombre_fantasia': 'PN',
            'razon_social': 'PN SpA', 'giro': 'Comercio', 'direccion': 'Calle 1',
            'comuna': 'Santiago', 'ciudad': 'Santiago', 'correoVendedor': 'v@pn.cl',
        }
        r = self.post_json('/app/crear_proveedor/', datos)
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.assertFalse(Empresa.objects.filter(nombre='Proveedor Nuevo R2').exists())
        permiso('jefe_local', 'gestion_dte_compras', puede_ver=True, puede_crear=True)
        r = self.post_json('/app/crear_proveedor/', datos)
        self.assertEqual(r.status_code, 200, r.content[:300])
        emp = Empresa.objects.get(nombre='Proveedor Nuevo R2')
        self.assertTrue(emp.esProveedor)
        self.assertEqual(emp.rut, '76086428-5')


# =========================================================================
# CC-15 — escritores de Compras sin cobertura previa
# =========================================================================
class EscritoresComprasTest(BaseR2V1):

    def setUp(self):
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_crear=True,
                puede_editar=True, puede_eliminar=True)

    # --- agregar_producto_manual_a_compra (B4-12) ---
    def test_agregar_manual_sin_dte_crea_la_linea(self):
        compra, _, _ = self.compra()
        self.entrar(self.jefe)
        r = self.post_json('/app/agregar_producto_manual/', {
            'compra_id': compra.id, 'nombre': '  NUEVO   CODIGO ', 'atributo1': ' NIKE ',
            'costo': '1500', 'precioSugerido': '3000',
            'tallas': [{'talla': '40', 'stock': 2}, {'talla': '41', 'stock': '3'},
                       {'talla': '', 'stock': 9}, {'talla': '42', 'stock': 0}]})
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual((body['tallas_creadas'], body['dte_id']), (2, None))
        cp = Compras_Producto.objects.get(id=body['compra_producto_id'])
        self.assertEqual((cp.nombre, cp.atributo1, cp.costo), ('NUEVO CODIGO', 'NIKE', 1500))
        self.assertEqual(
            sorted(Compras_Producto_Talla.objects.filter(compra_producto=cp).values_list('talla', 'stock')),
            [('40', 2), ('41', 3)])

    def test_agregar_manual_sin_tallas_validas_no_deja_huerfano(self):
        compra, _, _ = self.compra()
        self.entrar(self.jefe)
        antes = Compras_Producto.objects.filter(compras=compra).count()
        r = self.post_json('/app/agregar_producto_manual/', {
            'compra_id': compra.id, 'nombre': 'VACIO',
            'tallas': [{'talla': '40', 'stock': 0}, {'talla': '', 'stock': 3}]})
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertEqual(Compras_Producto.objects.filter(compras=compra).count(), antes)

    def test_agregar_manual_valida_dte_si_viene_y_compra_cancelada(self):
        compra, _, _ = self.compra()
        venta = crear_dte(self.emp_a, self.proveedor, 55, tipo_transaccion='VENTA')
        self.entrar(self.jefe)
        r = self.post_json('/app/agregar_producto_manual/', {
            'compra_id': compra.id, 'nombre': 'CON DTE', 'dte_id': venta.id,
            'tallas': [{'talla': '40', 'stock': 1}]})
        self.assertEqual(r.status_code, 400, r.content[:300])
        factura = crear_dte(self.proveedor, self.emp_a, 56)
        r = self.post_json('/app/agregar_producto_manual/', {
            'compra_id': compra.id, 'nombre': 'CON DTE', 'dte_id': factura.id,
            'tallas': [{'talla': '40', 'stock': 1}]})
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()['dte_id'], factura.id)
        cancelada, _, _ = self.compra(estado='CANCELADA')
        r = self.post_json('/app/agregar_producto_manual/', {
            'compra_id': cancelada.id, 'nombre': 'X', 'tallas': [{'talla': '40', 'stock': 1}]})
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertFalse(Compras_Producto.objects.filter(compras=cancelada, nombre='X').exists())

    # --- distribuir_tallas_compra_producto ---
    def test_distribuir_reemplaza_la_fila_total(self):
        compra, cp, _ = self.compra(tallas=())
        Compras_Producto_Talla.objects.create(
            compra_producto=cp, stock=10, talla='__TOTAL__', pendiente_distribuir=True)
        self.entrar(self.jefe)
        url = '/app/api/distribuir-tallas-compra-producto/'
        r = self.post_json(url, {'compra_producto_id': cp.id,
                                 'distribucion': [{'talla': '38', 'stock': 4}, {'talla': 39, 'stock': 5}]})
        self.assertEqual(r.status_code, 400, r.content[:300])  # suma 9 != 10
        r = self.post_json(url, {'compra_producto_id': cp.id,
                                 'distribucion': [{'talla': '38', 'stock': 4}, {'talla': 39, 'stock': 6}]})
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(
            sorted(Compras_Producto_Talla.objects.filter(compra_producto=cp)
                   .values_list('talla', 'stock', 'pendiente_distribuir')),
            [('38', 4, False), ('39', 6, False)])
        # Segundo envío (doble clic): ya no hay fila total.
        r = self.post_json(url, {'compra_producto_id': cp.id,
                                 'distribucion': [{'talla': '38', 'stock': 10}]})
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertEqual(Compras_Producto_Talla.objects.filter(compra_producto=cp).count(), 2)

    def test_distribuir_rechaza_con_recepciones_o_compra_eliminada(self):
        compra, cp, _ = self.compra(tallas=())
        total = Compras_Producto_Talla.objects.create(
            compra_producto=cp, stock=2, talla='__TOTAL__', pendiente_distribuir=True)
        self.recepcion(total, 1)
        self.entrar(self.jefe)
        url = '/app/api/distribuir-tallas-compra-producto/'
        r = self.post_json(url, {'compra_producto_id': cp.id, 'distribucion': [{'talla': '40', 'stock': 2}]})
        self.assertEqual(r.status_code, 400, r.content[:300])
        eliminada, cp2, _ = self.compra(estado='ELIMINADA', tallas=())
        Compras_Producto_Talla.objects.create(
            compra_producto=cp2, stock=2, talla='__TOTAL__', pendiente_distribuir=True)
        r = self.post_json(url, {'compra_producto_id': cp2.id, 'distribucion': [{'talla': '40', 'stock': 2}]})
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertTrue(Compras_Producto_Talla.objects.filter(compra_producto=cp2, pendiente_distribuir=True).exists())
        r = self.post_json(url, {'compra_producto_id': cp.id, 'distribucion': 'x'})
        self.assertEqual(r.status_code, 400)

    # --- eliminar_pendientes_compra_masivo ---
    def test_eliminar_pendientes_masivo_respeta_recepciones(self):
        compra, cp, (c40, c41, c42) = self.compra(tallas=(('40', 1), ('41', 2), ('42', 3)))
        self.recepcion(c42, 1)
        self.entrar(self.jefe)
        r = self.post_json('/app/eliminar_pendientes_compra_masivo/', {'ids': [c40.id, c41.id, c42.id, c41.id]})
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body['eliminados'], 2)
        self.assertEqual(len(body['errores']), 1)
        self.assertEqual(list(Compras_Producto_Talla.objects.filter(compra_producto=cp).values_list('id', flat=True)),
                         [c42.id])
        r = self.post_json('/app/eliminar_pendientes_compra_masivo/', {'ids': ['abc']})
        self.assertEqual(r.status_code, 400)

    def test_eliminar_pendientes_masivo_exige_puede_eliminar(self):
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_editar=True)
        compra, cp, (c40,) = self.compra()
        self.entrar(self.jefe)
        r = self.post_json('/app/eliminar_pendientes_compra_masivo/', {'ids': [c40.id]})
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.assertTrue(Compras_Producto_Talla.objects.filter(id=c40.id).exists())

    # --- guardar_curva_distribucion ---
    def test_guardar_curva_crear_validar_y_editar(self):
        self.entrar(self.jefe)
        url = '/app/api/curvas-distribucion/guardar/'
        r = self.post_json(url, {'nombre': 'CURVA R2', 'items': [
            {'talla': '40', 'porcentaje': 40}, {'talla': '41', 'porcentaje': 50},
            {'talla': '', 'porcentaje': 10}]})
        self.assertEqual(r.status_code, 400, r.content[:300])  # la fila sin talla no cuenta: 90 %
        r = self.post_json(url, {'nombre': 'CURVA R2', 'items': [
            {'talla': '40', 'porcentaje': 40, 'orden': 'x'}, {'talla': 41, 'porcentaje': 60}]})
        self.assertEqual(r.status_code, 200, r.content[:300])
        curva = CurvaDistribucion.objects.get(nombre='CURVA R2')
        self.assertEqual(curva.creado_por, self.jefe.username)
        self.assertEqual(sorted(curva.items.values_list('talla', flat=True)), ['40', '41'])
        # Editar: exige puede_editar.
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_crear=True)
        r = self.post_json(url, {'id': curva.id, 'nombre': 'CURVA R2 BIS',
                                 'items': [{'talla': '40', 'porcentaje': 100}]})
        self.assertEqual(r.status_code, 403, r.content[:300])
        curva.refresh_from_db()
        self.assertEqual(curva.nombre, 'CURVA R2')
        permiso('jefe_local', 'gestion_compras', puede_ver=True, puede_editar=True)
        r = self.post_json(url, {'id': curva.id, 'nombre': 'CURVA R2 BIS',
                                 'items': [{'talla': '40', 'porcentaje': 100}]})
        self.assertEqual(r.status_code, 200, r.content[:300])
        curva.refresh_from_db()
        self.assertEqual((curva.nombre, curva.items.count()), ('CURVA R2 BIS', 1))
        r = self.post_json(url, {'nombre': 'MALA', 'items': 'x'})
        self.assertIn(r.status_code, (400, 403))
        self.assertFalse(CurvaDistribucion.objects.filter(nombre='MALA').exists())

    # --- recepcionar_compra (B4-17) y obtener_compras_por_anio (B4-16 b) ---
    def _compra_para_filtros(self):
        compra, cp_a, (a40,) = self.compra(nombre='AAA', tallas=(('40', 2),))
        cp_b = Compras_Producto.objects.create(
            compras=compra, nombre='BBB', atributo1='M', atributo2='C', atributo3='G',
            atributo4='', costo=1000, precioSugerido=2000, sucursal_destino=self.suc_a2)
        b40 = Compras_Producto_Talla.objects.create(compra_producto=cp_b, stock=4, talla='40')
        cp_c = Compras_Producto.objects.create(
            compras=compra, nombre='CCC', atributo1='M', atributo2='C', atributo3='G',
            atributo4='', costo=1000, precioSugerido=2000)
        Compras_Producto_Talla.objects.create(compra_producto=cp_c, stock=3, talla='40')
        self.recepcion(a40, 2, sucursal_destino=self.suc_a)   # completa
        self.recepcion(b40, 1)                                # parcial
        return compra

    def test_recepcion_filtra_antes_de_paginar(self):
        compra = self._compra_para_filtros()
        self.entrar(self.jefe)
        casos = {
            'parciales': ['BBB'], 'completos': ['AAA'], 'pendientes': ['CCC'],
            'rebajados': ['AAA', 'BBB'], 'parcial': ['BBB'], '': ['AAA', 'BBB', 'CCC'],
        }
        for vista in (True, False):
            for filtro, esperados in casos.items():
                vistos = []
                pagina = 1
                while True:
                    r = self.post_json('/app/compra/recepcionar/', {
                        'compra_id': compra.id, 'page': pagina, 'page_size': 1,
                        'vista_agrupada': vista, 'filtro_estado': filtro})
                    self.assertEqual(r.status_code, 200, r.content[:300])
                    body = r.json()
                    self.assertEqual(body['pagination']['total_count'], len(esperados), (vista, filtro))
                    vistos += [it['nombre'] for it in body['items']]
                    if not body['pagination']['has_next']:
                        break
                    pagina += 1
                self.assertEqual(sorted(vistos), esperados, (vista, filtro))
        # Sucursal: la de la talla ('__sin__' = sin sucursal).
        r = self.post_json('/app/compra/recepcionar/', {
            'compra_id': compra.id, 'filtro_sucursal': '__sin__'})
        self.assertEqual([it['nombre'] for it in r.json()['items']], ['CCC'])
        r = self.post_json('/app/compra/recepcionar/', {
            'compra_id': compra.id, 'filtro_sucursal': str(self.suc_a2.id)})
        self.assertEqual([it['nombre'] for it in r.json()['items']], ['BBB'])
        self.assertEqual(r.json()['filtros'], {'estado': '', 'sucursal': str(self.suc_a2.id)})
        # Sin filtros la respuesta no cambia de forma.
        r = self.post_json('/app/compra/recepcionar/', {'compra_id': compra.id})
        self.assertNotIn('filtros', r.json())

    def test_compras_por_anio_devuelve_unidades_por_crear(self):
        compra, _, (c40, c41) = self.compra(tallas=(('40', 5), ('41', 5)))
        self.recepcion(c40, 3)
        self.recepcion(c41, 2)
        pt = crear_producto_con_talla(self.suc_a, articulo='YA-CREADO', sku=990101)[1]
        self.recepcion(c41, 1, pt=pt)
        self.entrar(self.jefe)
        r = self.client.get('/app/obtener_compras/', {'anio': compra.fecha.year, 'search': compra.nombre})
        self.assertEqual(r.status_code, 200, r.content[:300])
        fila = next(c for c in r.json()['compras'] if c['id'] == compra.id)
        self.assertEqual((fila['pendientes_crear'], fila['unidades_por_crear']), (2, 5))


# =========================================================================
# B15-01 / B14-02 (4) / B1-09 (2) — facturas_pendientes
# =========================================================================
class FacturasPendientesTest(BaseR2V1):

    def setUp(self):
        permiso('jefe_local', 'gestion_compras', puede_ver=True)

    def test_sin_nc_nd_y_con_datos_de_uso_y_pago(self):
        factura = crear_dte(self.proveedor, self.emp_a, 7101, unidades_productos=10)
        crear_dte(self.proveedor, self.emp_a, 7102, tipo_documento='NOTA DE CREDITO')
        crear_dte(self.proveedor, self.emp_a, 7103, tipo_documento='NOTA DE DEBITO')
        guia = crear_dte(self.proveedor, self.emp_a, 7104, tipo_documento='GUIA DE DESPACHO')
        compra, _, (cpt,) = self.compra(costo=1000)
        self.recepcion(cpt, 3, dte=factura)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='TRANSFERENCIA', monto=50000)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='CHEQUE', monto=10000)
        self.entrar(self.jefe)
        r = self.client.get('/app/facturas_pendientes/', {'proveedor_id': self.proveedor.id, 'meses': 12})
        self.assertEqual(r.status_code, 200, r.content[:300])
        filas = {f['id']: f for f in r.json()}
        self.assertEqual(set(filas), {factura.id, guia.id})
        f = filas[factura.id]
        # Claves históricas que lee gestionCompras.html.
        for clave in ('id', 'text', 'text_con_fecha', 'proveedor_nombre', 'fecha'):
            self.assertIn(clave, f)
        self.assertEqual(f['text'], '7101')
        self.assertEqual((f['monto'], f['usado'], f['recibidas_uds'], f['pagado']),
                         (119000.0, 3000.0, 3, 60000.0))
        self.assertEqual(f['tipo_documento'], 'FACTURA ELECTRONICA')
        self.assertEqual(filas[guia.id]['recibidas_uds'], 0)

    def test_sin_proveedor_lista_vacia(self):
        self.entrar(self.jefe)
        r = self.client.get('/app/facturas_pendientes/')
        self.assertEqual(r.json(), [])


# =========================================================================
# A3-01 (2) + B14-04 — Lotes FIFO
# =========================================================================
class LotesManualesTest(BaseR2V1):

    def setUp(self):
        permiso('jefe_local', 'gestion_producto', puede_ver=True, puede_crear=True, puede_editar=True)
        _, self.pt_a = crear_producto_con_talla(self.suc_a, articulo='LOTE-R2-A', sku=990201, stock=3)
        _, self.pt_b = crear_producto_con_talla(self.suc_b, articulo='LOTE-R2-B', sku=990202, stock=3)
        self.lote_a = crear_lote_fifo(self.pt_a, cantidad=3, costo_unitario=1000)
        self.lote_b = crear_lote_fifo(self.pt_b, cantidad=3, costo_unitario=2000)

    def form_lote(self, pt, **extra):
        datos = {'producto_talla_id': pt.id, 'cantidad': 4, 'costo_unitario': 1000,
                 'precio_venta_unitario': 2000}
        datos.update(extra)
        return datos

    def test_crear_lote_propio_firma_con_el_usuario_real(self):
        self.entrar(self.jefe, nombre_sesion=False)
        r = self.client.post('/app/crear_lote_manual/', self.form_lote(self.pt_a),
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, 7)
        mov = Movimientos_Producto.objects.get(ProductoTalla=self.pt_a, concepto='AJUSTE_POSITIVO')
        self.assertEqual(mov.responsable, 'Juana Bodega')

    def test_crear_lote_ajeno_403_sin_escribir(self):
        self.entrar(self.jefe)
        lotes = LoteProducto.objects.filter(producto_talla=self.pt_b).count()
        r = self.client.post('/app/crear_lote_manual/', self.form_lote(self.pt_b),
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.pt_b.refresh_from_db()
        self.assertEqual(self.pt_b.stock, 3)
        self.assertEqual(LoteProducto.objects.filter(producto_talla=self.pt_b).count(), lotes)

    def test_crear_lote_exige_puede_crear_y_valida_entrada(self):
        self.entrar(self.jefe)
        r = self.client.post('/app/crear_lote_manual/', self.form_lote(self.pt_a, cantidad='abc'),
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 400, r.content[:300])
        r = self.client.post('/app/crear_lote_manual/', self.form_lote(self.pt_a, cantidad=-2),
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 400, r.content[:300])
        r = self.client.post('/app/crear_lote_manual/', self.form_lote(self.pt_a, producto_talla_id=99999999),
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 404, r.content[:300])
        permiso('jefe_local', 'gestion_producto', puede_ver=True)
        r = self.client.post('/app/crear_lote_manual/', self.form_lote(self.pt_a),
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, 3)

    def test_ajustar_lote_propio_y_ajeno(self):
        self.entrar(self.jefe, nombre_sesion=False)
        r = self.client.post(f'/app/ajustar_lote/{self.lote_b.id}/', {'cantidad_disponible': 0},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.lote_b.refresh_from_db()
        self.assertEqual(self.lote_b.cantidad_disponible, 3)
        r = self.client.post(f'/app/ajustar_lote/{self.lote_a.id}/', {'cantidad_disponible': 1},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.lote_a.refresh_from_db()
        self.pt_a.refresh_from_db()
        self.assertEqual((self.lote_a.cantidad_disponible, self.pt_a.stock), (1, 1))
        mov = Movimientos_Producto.objects.get(ProductoTalla=self.pt_a, concepto='AJUSTE_NEGATIVO')
        self.assertEqual((mov.cantidad, mov.responsable), (-2, 'Juana Bodega'))
        r = self.client.post('/app/ajustar_lote/99999999/', {'cantidad_disponible': 1},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 404)
        r = self.client.post(f'/app/ajustar_lote/{self.lote_a.id}/', {'cantidad_disponible': 'x'},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 400)

    def test_ajustar_lote_exige_puede_editar(self):
        permiso('jefe_local', 'gestion_producto', puede_ver=True, puede_crear=True)
        self.entrar(self.jefe)
        r = self.client.post(f'/app/ajustar_lote/{self.lote_a.id}/', {'cantidad_disponible': 0},
                             HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.lote_a.refresh_from_db()
        self.assertEqual(self.lote_a.cantidad_disponible, 3)


# =========================================================================
# Paso 5: compras anuladas, enlace sin DTE, reuso de factura, traspaso en viaje
# =========================================================================
class Paso5R2V1Test(BaseR2V1):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cat = Categoria.objects.create(nombre='R2 Zapatillas')
        am = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        ac = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        ag = Productos_Atributos.objects.create(nombre='Género', descripcion='Género')
        cls.marca = AtributoOpcion.objects.create(atributo=am, valor='R2MARCA')
        cls.color = AtributoOpcion.objects.create(atributo=ac, valor='R2NEGRO')
        cls.color2 = AtributoOpcion.objects.create(atributo=ac, valor='R2BLANCO')
        cls.genero = AtributoOpcion.objects.create(atributo=ag, valor='R2HOMBRE')

    def setUp(self):
        permiso('jefe_local', 'gestion_producto', puede_ver=True, puede_crear=True,
                puede_editar=True, puede_eliminar=True)

    def form_recepcion(self, cp, stocks, articulo='R2-REC', **extra):
        data = {
            'producto_compra_id': str(cp.id), 'articulo': articulo, 'descripcion': 'x',
            'atributo1': str(self.marca.id), 'categoria': str(self.cat.id),
            'costo': '1000', 'sobreprecio': '0', 'precioventa': '2000',
            'tipo_talla': 'CL', 'sucursales_destino_ids[]': [str(self.suc_a.id)],
        }
        for talla, stock in stocks.items():
            data[f'sku_{talla}'] = ''
            data[f'stock_{talla}'] = str(stock)
        data.update(extra)
        return data

    # --- B14-01 / B2-03: compras anuladas ---
    def test_compra_eliminada_no_se_ofrece_ni_se_crea(self):
        activa, cp_act, (c_act,) = self.compra(nombre='R2-ACT', sucursal_destino=self.suc_a)
        self.recepcion(c_act, 2, sucursal_destino=self.suc_a)
        for estado in ('ELIMINADA', 'CANCELADA'):
            anulada, cp_an, (c_an,) = self.compra(estado=estado, nombre=f'R2-{estado}',
                                                   sucursal_destino=self.suc_a)
            self.recepcion(c_an, 2, sucursal_destino=self.suc_a)
            self.entrar(self.jefe)
            r = self.client.get('/app/productos_para_crear/', {'estado': 'no_creado'})
            self.assertEqual(r.status_code, 200, r.content[:300])
            ids = {p['producto_id'] for p in r.json()['data']}
            self.assertIn(cp_act.id, ids)
            self.assertNotIn(cp_an.id, ids)
            r = self.client.get(f'/app/detalle_producto_para_crear/{cp_an.id}/')
            self.assertEqual(r.status_code, 400, r.content[:300])
            r = self.client.get(f'/app/obtener_recepciones_producto/{cp_an.id}/')
            self.assertEqual(r.status_code, 400, r.content[:300])
            r = self.client.post('/app/crear_producto_desde_recepcion/',
                                 self.form_recepcion(cp_an, {'40': 2}, articulo=f'R2-{estado}'))
            self.assertEqual(r.status_code, 400, r.content[:300])
            self.assertFalse(Producto.objects.filter(articulo=f'R2-{estado}').exists())
        # La activa sigue intacta y su detalle responde.
        r = self.client.get(f'/app/detalle_producto_para_crear/{cp_act.id}/')
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_lo_ya_creado_de_una_compra_eliminada_sigue_como_historial(self):
        anulada, cp_an, (c_an,) = self.compra(estado='ELIMINADA', nombre='R2-HIST', sucursal_destino=self.suc_a)
        _, pt = crear_producto_con_talla(self.suc_a, articulo='R2-HIST', sku=990301, stock=2)
        self.recepcion(c_an, 2, pt=pt, sucursal_destino=self.suc_a)
        self.entrar(self.jefe)
        r = self.client.get('/app/productos_para_crear/', {'estado': 'creado'})
        self.assertIn(cp_an.id, {p['producto_id'] for p in r.json()['data']})

    # --- B14-05: enlace del movimiento de ingreso sin factura ---
    def test_crear_desde_recepcion_sin_dte_enlaza_movimiento_ingreso(self):
        _, cp, (cpt,) = self.compra(nombre='R2-SINDTE', sucursal_destino=self.suc_a)
        rec = self.recepcion(cpt, 3, sucursal_destino=self.suc_a)
        self.entrar(self.jefe, nombre_sesion=False)
        r = self.client.post('/app/crear_producto_desde_recepcion/',
                             self.form_recepcion(cp, {'40': 3}, articulo='R2-SINDTE'))
        self.assertEqual(r.status_code, 200, r.content[:300])
        pt = Producto_Talla.objects.get(producto__articulo='R2-SINDTE')
        ingreso = Movimientos_Producto.objects.get(ProductoTalla=pt, concepto='INGRESO_INICIAL')
        rec.refresh_from_db()
        self.assertEqual((rec.producto_talla_id, rec.movimiento_ingreso_id), (pt.id, ingreso.id))
        self.assertIsNone(ingreso.dte_id)
        # B14-04: firmado por el usuario real, no 'Sistema'.
        self.assertEqual(ingreso.responsable, 'Juana Bodega')

    # --- B14-02 (2): reuso de factura en Crear Manual ---
    def payload_manual(self, dte, articulo='R2-MAN', color=None, stock='3', **extra):
        datos = {
            'es_manual': 'true', 'proveedor': str(self.proveedor.id), 'dte_manual': str(dte.id),
            'articulo': articulo, 'descripcion': 'x', 'atributo1': str(self.marca.id),
            'atributo2': str((color or self.color).id), 'atributo3': str(self.genero.id),
            'categoria': str(self.cat.id), 'tipo_talla': 'CL', 'costo': '1000',
            'sobreprecio': '0', 'precioventa': '2000',
            'talla[]': ['40'], 'stock[]': [stock], 'sku[]': [''],
        }
        datos.update(extra)
        return datos

    def post_manual(self, datos):
        # Como el modal (jQuery $.ajax manda X-Requested-With).
        return self.client.post('/app/crear_producto_manual/', datos,
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_manual_sobre_lo_declarado_pide_confirmacion(self):
        dte = crear_dte(self.proveedor, self.emp_a, 7201, unidades_productos=5)
        _, _, (cpt,) = self.compra(nombre='OTRO-ART')
        self.recepcion(cpt, 4, dte=dte)  # ya recepcionadas en otra compra
        self.entrar(self.jefe)
        r = self.post_manual(self.payload_manual(dte))
        self.assertEqual(r.status_code, 409, r.content[:300])
        body = r.json()
        self.assertTrue(body['needs_confirmation'])
        self.assertEqual(body['factura_reutilizada']['ya_ingresadas'], 4)
        self.assertFalse(Producto.objects.filter(articulo='R2-MAN').exists())
        # Dentro de lo declarado (1 u): pasa sin confirmar.
        r = self.post_manual(self.payload_manual(dte, stock='1'))
        self.assertEqual(r.status_code, 200, r.content[:300])
        # Confirmado: pasa aunque se exceda.
        r = self.post_manual(self.payload_manual(dte, articulo='R2-MAN2', confirmar_exceso_factura='true'))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(r.json()['success'], r.content[:300])

    def test_manual_sin_unidades_declaradas_frena_solo_el_mismo_articulo(self):
        dte = crear_dte(self.proveedor, self.emp_a, 7202, unidades_productos=0)
        self.entrar(self.jefe)
        r = self.post_manual(self.payload_manual(dte))
        self.assertEqual(r.status_code, 200, r.content[:300])
        # Otro artículo de la misma factura: no se frena.
        r = self.post_manual(self.payload_manual(dte, articulo='R2-OTRO'))
        self.assertEqual(r.status_code, 200, r.content[:300])
        # Mismo artículo, otro color: tampoco.
        r = self.post_manual(self.payload_manual(dte, color=self.color2))
        self.assertEqual(r.status_code, 200, r.content[:300])
        # Mismo artículo y color otra vez: doble ingreso → 409.
        r = self.post_manual(self.payload_manual(dte))
        self.assertEqual(r.status_code, 409, r.content[:300])
        pt = Producto_Talla.objects.get(producto__articulo='R2-MAN', producto__atributo2=self.color)
        self.assertEqual(pt.stock, 3)

    def test_manual_del_agente_no_se_frena(self):
        # El agente/comando de carga por factura llama la vista con
        # RequestFactory (sin cabecera AJAX) y tiene su propio control de
        # doble carga por DTE: no recibe el 409 del modal.
        dte = crear_dte(self.proveedor, self.emp_a, 7203, unidades_productos=2)
        _, _, (cpt,) = self.compra(nombre='OTRO-ART')
        self.recepcion(cpt, 2, dte=dte)
        self.entrar(self.jefe)
        r = self.client.post('/app/crear_producto_manual/', self.payload_manual(dte))
        self.assertEqual(r.status_code, 200, r.content[:300])

    # --- B6-04: traspaso en viaje ---
    def _talla_creada(self, sku):
        _, pt = crear_producto_con_talla(self.suc_a, articulo=f'R2-TR-{sku}', sku=sku, stock=0)
        dte_compra = crear_dte(self.proveedor, self.emp_a, sku)
        Movimientos_Producto.objects.create(
            ProductoTalla=pt, concepto='INGRESO_INICIAL', cantidad=5, dte=dte_compra,
            sucursal_origen=self.suc_a, sucursal_destino=self.suc_a)
        Producto_Talla.objects.filter(id=pt.id).update(stock=5)
        _, _, (cpt,) = self.compra(nombre=f'R2-TR-{sku}')
        self.recepcion(cpt, 5, dte=dte_compra, pt=pt)
        pt.refresh_from_db()
        return pt

    def _traspaso(self, numero, recibido=False, estado='EMITIDO'):
        return crear_dte(self.emp_a, self.emp_a, numero, tipo_transaccion='TRASPASO',
                         tipo_documento='GUIA DE DESPACHO', estado_dte=estado,
                         fecha_recepcion=timezone.localdate() if recibido else None)

    def _salida(self, pt, dte, cantidad=2):
        Movimientos_Producto.objects.create(
            ProductoTalla=pt, concepto='TRASPASO_SALIDA', cantidad=-cantidad, dte=dte,
            sucursal_origen=self.suc_a, sucursal_destino=self.suc_a2)

    def test_revertir_con_traspaso_en_viaje_409(self):
        pt = self._talla_creada(990401)
        guia = self._traspaso(8801)
        self._salida(pt, guia)
        self.entrar(self.jefe)
        r = self.post_json('/app/api/producto/revertir-a-pendiente/',
                           {'producto_talla_id': pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 409, r.content[:300])
        self.assertEqual(r.json()['dtes_en_viaje'], [guia.id])
        self.assertTrue(Producto_Talla.objects.filter(id=pt.id).exists())
        self.assertTrue(Productos_Recepcionados.objects.filter(producto_talla=pt).exists())

    def test_revertir_con_linea_en_traspaso_del_flujo_actual_409(self):
        pt = self._talla_creada(990402)
        otra = self._talla_creada(990403)
        guia = self._traspaso(8802)
        self._salida(otra, guia)  # el DTE es del flujo actual
        Dte_Productos.objects.create(dte=guia, productoTalla=pt, descripcion='x', costo=1000,
                                     sobreprecio=0, precio=2000, precio_unitario=1000,
                                     monto_item=2000, stock=2)
        self.entrar(self.jefe)
        r = self.post_json('/app/api/producto/revertir-a-pendiente/',
                           {'producto_talla_id': pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 409, r.content[:300])

    def test_revertir_no_frena_por_traspaso_recibido_legacy_o_cancelado(self):
        pt = self._talla_creada(990404)
        recibido = self._traspaso(8803, recibido=True)
        self._salida(pt, recibido)
        cancelado = self._traspaso(8804, estado='CANCELADO')
        self._salida(pt, cancelado)
        # Guía migrada (sin movimientos) sin fecha_recepcion: no cuenta.
        legacy = self._traspaso(8805)
        Dte_Productos.objects.create(dte=legacy, productoTalla=pt, descripcion='x', costo=1000,
                                     sobreprecio=0, precio=2000, precio_unitario=1000,
                                     monto_item=2000, stock=1)
        self.entrar(self.jefe)
        r = self.post_json('/app/api/producto/revertir-a-pendiente/',
                           {'producto_talla_id': pt.id, 'motivo': 'x'})
        # Tiene historia (traspasos): no se borra, se revierte el stock.
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()['resumen']['tipo'], 'total_sin_borrar')
        self.assertTrue(Producto_Talla.objects.filter(id=pt.id).exists())


# =========================================================================
# CC-15 — guardar_margenes_usuario
# =========================================================================
class GuardarMargenesTest(BaseR2V1):

    def test_guarda_en_la_ficha_de_la_sucursal_activa(self):
        self.entrar(self.jefe)
        r = self.client.post('/app/guardar_margenes_usuario/',
                             {'margenSobreprecio': '30', 'margenPrecioVenta': '45'})
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(r.json()['success'])
        self.assertFalse(r.json()['created'])
        eu = EmpresaUser.objects.get(user=self.jefe, empresa=self.emp_a, sucursal=self.suc_a)
        self.assertEqual((eu.margenSobreprecio, eu.margenPrecioVenta, eu.status, eu.active),
                         (30, 45, True, True))

    def test_valores_invalidos_no_guardan(self):
        self.entrar(self.jefe)
        for datos in ({'margenSobreprecio': 'abc', 'margenPrecioVenta': '1'},
                      {'margenSobreprecio': '-5', 'margenPrecioVenta': '1'}):
            r = self.client.post('/app/guardar_margenes_usuario/', datos)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()['success'])
        eu = EmpresaUser.objects.get(user=self.jefe, empresa=self.emp_a, sucursal=self.suc_a)
        self.assertIsNone(eu.margenSobreprecio)

    def test_sin_sucursal_en_sesion_no_crea_fichas(self):
        # Directo a la vista (el middleware completa la sesión en una request
        # real): sin empresa/sucursal no se crea una EmpresaUser a ciegas.
        from importlib import import_module
        from django.conf import settings
        from django.test import RequestFactory
        from app.views import guardar_margenes_usuario
        request = RequestFactory().post('/app/guardar_margenes_usuario/',
                                        {'margenSobreprecio': '30', 'margenPrecioVenta': '45'})
        request.user = self.jefe
        request.session = import_module(settings.SESSION_ENGINE).SessionStore()
        antes = EmpresaUser.objects.filter(user=self.jefe).count()
        r = guardar_margenes_usuario(request)
        self.assertFalse(json.loads(r.content)['success'])
        self.assertEqual(EmpresaUser.objects.filter(user=self.jefe).count(), antes)
