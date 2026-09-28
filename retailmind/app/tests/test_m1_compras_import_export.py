"""
Unidad M1 (auditoría compras 2026-09): importaciones CSV/XLSX de proveedores
y DTE, exportaciones, descarte de DTE, KPI de pendientes y compensación
manual con factura emitida.

Cubre los arreglos de:
- B11-01 / B13-04: importar DTE no pisa VENTA / TRASPASO ni DTE de otra
  empresa; receptor siempre la empresa en sesión; actualizar exige editar.
- B11-04: exportar -> reimportar proveedores no cambia fichas; no se tocan
  empresas del grupo; se prefiere la ficha proveedor.
- B11-05: una fila con error de BD no revierte en silencio las demás.
- B11-06 / B11-13: código SII, fecha inválida, exenta, BOM, ';' y cp1252.
- B13-06 / B3-10: eliminarDTE nunca borra físicamente ni fuera de la empresa.
- B1-04 / B1-05 / B12-02 / CC-16: exportar compras sin ELIMINADA, con todas
  las recepciones, sin N+1 y sin fórmulas ejecutables.
- B3-05 / B12-07 / B15-09: universo y saldo del KPI de pendientes.
- B5-08: reuso del mismo número en la compensación manual.

Ejecutar (BD de test aislada):
    DATABASE_URL=postgres://postgres:admin@localhost:5432/retail_m1 \
    python manage.py test app.tests.test_m1_compras_import_export --keepdb --noinput
"""
import csv
import io
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Dte, Dte_Detalle_Pago,
    Empresa, LoteProducto, Movimientos_Producto, Productos_Recepcionados,
)
from app.views_modulo_compras import (
    METODO_COMPENSACION_EMITIDA, obtener_resumen_pendientes_anio,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo, crear_producto_con_talla,
    crear_sucursal, crear_usuario, otorgar_ver_pantalla,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def rut_valido(numero):
    """RUT 'NNNNNNNN-D' con dígito verificador correcto."""
    suma, mult = 0, 2
    for d in reversed(str(numero)):
        suma += int(d) * mult
        mult = mult + 1 if mult < 7 else 2
    dv = 11 - suma % 11
    dv = '0' if dv == 11 else 'K' if dv == 10 else str(dv)
    return f'{numero}-{dv}'


def _dte(emisor, receptor, numero, tipo_transaccion='COMPRA', monto=119000,
         tipo_documento='FACTURA ELECTRONICA', fecha=None, estado_pago='Pendiente', **extra):
    fecha = fecha or timezone.localdate()
    datos = dict(
        emisor=emisor, receptor=receptor, numero_documento=numero,
        tipo_documento=tipo_documento, monto_con_iva=monto,
        monto_neto=int(round(monto / 1.19)), descuento=0, estado_pago=estado_pago,
        estado_dte='EMITIDO', responsable='test', fecha_emision=fecha,
        fecha_vencimiento=fecha + timedelta(days=30), diasCredito=30, bultos=1,
        unidades_productos=5, tipo_transaccion=tipo_transaccion, referencias='ref original',
    )
    datos.update(extra)
    return Dte.objects.create(**datos)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class _BaseM1(TestCase):
    rol = 'administrador'

    def setUp(self):
        self.empresa = crear_empresa(nombre='Nosotros', rut='76.100.000-' + rut_valido(76100000)[-1],
                                     esProveedor=True)
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='M1-SUC')
        self.otra_empresa = crear_empresa(nombre='Otra del grupo', rut=rut_valido(76200000), esProveedor=True)
        crear_sucursal(empresa=self.otra_empresa, alias='M1-OTRA')
        self.proveedor = crear_empresa(
            nombre='Proveedor M1', rut=rut_valido(77300000), esProveedor=True,
            direccion='Av. Proveedor 100', contacto1='225551234', acteco='469000',
            correoVendedor='ventas@prov.cl', correoIntercambio='dte@prov.cl',
            correoAdministrador='admin@prov.cl',
        )
        self.user = crear_usuario(username='m1_admin', rol=self.rol)
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        otorgar_ver_pantalla(self.rol, 'gestion_dte_compras', puede_crear=True, puede_editar=True)
        otorgar_ver_pantalla(self.rol, 'gestion_compras')
        otorgar_ver_pantalla(self.rol, 'dte_compras_eliminar', puede_eliminar=True)
        self.client = self._cliente(self.user)

    def _cliente(self, user):
        c = Client()
        c.force_login(user)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        return c

    def _importar(self, url, campo, contenido, nombre='archivo.csv', client=None, **extra):
        from django.core.files.uploadedfile import SimpleUploadedFile
        if isinstance(contenido, str):
            contenido = contenido.encode('utf-8')
        r = (client or self.client).post(
            url, {campo: SimpleUploadedFile(nombre, contenido), **extra},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        return r, json.loads(r.content)

    def _importar_dtes(self, contenido, **extra):
        return self._importar('/app/api/importar-dtes/', 'archivo_dtes', contenido, **extra)

    def _importar_proveedores(self, contenido, **extra):
        return self._importar('/app/api/importar-proveedores/', 'archivo_proveedores', contenido, **extra)


class ImportarDtesTest(_BaseM1):
    CAB = 'rut_proveedor,numero_documento,tipo_documento,fecha_emision,monto_con_iva,referencias'

    def test_no_pisa_venta_ni_traspaso_ni_compra_de_otra_empresa(self):
        venta = _dte(self.proveedor, self.otra_empresa, 100, tipo_transaccion='VENTA')
        traspaso = _dte(self.proveedor, self.empresa, 101, tipo_transaccion='TRASPASO')
        ajena = _dte(self.proveedor, self.otra_empresa, 102)
        rut = self.proveedor.rut
        csv_txt = (f'{self.CAB}\n{rut},100,33,2020-01-01,1,x\n{rut},101,33,2020-01-01,1,x\n'
                   f'{rut},102,33,2020-01-01,1,x\n')
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(r.status_code, 200, js)
        self.assertEqual(js['dtes_actualizados'] + js['dtes_creados'], 0)
        self.assertEqual(len(js['errores']), 3)
        self.assertIn('VENTA', js['errores'][0])
        self.assertIn('TRASPASO', js['errores'][1])
        self.assertIn('otra empresa', js['errores'][2])
        for d in (venta, traspaso, ajena):
            antes = (d.monto_con_iva, d.fecha_emision, d.referencias)
            d.refresh_from_db()
            self.assertEqual((d.monto_con_iva, d.fecha_emision, d.referencias), antes)
        self.assertEqual(Dte.objects.filter(numero_documento__in=[100, 101, 102]).count(), 3)

    def test_receptor_del_archivo_se_ignora(self):
        csv_txt = (f'{self.CAB},receptor_id\n{self.proveedor.rut},555,FACTURA ELECTRONICA,2026-01-10,119000,x,'
                   f'{self.otra_empresa.id}\n')
        r, js = self._importar_dtes(csv_txt)
        self.assertEqual(js['dtes_creados'], 1, js)
        creado = Dte.objects.get(numero_documento=555)
        self.assertEqual(creado.receptor_id, self.empresa.id)
        self.assertEqual(creado.tipo_transaccion, 'COMPRA')
        self.assertEqual(creado.estado_pago.upper(), 'PENDIENTE')

    def test_actualizar_exige_permiso_de_edicion(self):
        otorgar_ver_pantalla('jefe_local', 'gestion_dte_compras', puede_crear=True, puede_editar=False)
        jefe = crear_usuario(username='m1_jefe', rol='jefe_local')
        crear_empresa_user(jefe, self.empresa, self.sucursal)
        c = self._cliente(jefe)
        csv_txt = f'{self.CAB}\n{self.proveedor.rut},556,33,2026-01-10,119000,x\n'
        r, js = self._importar('/app/api/importar-dtes/', 'archivo_dtes', csv_txt, client=c,
                               modo_actualizacion='crear_y_actualizar')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Dte.objects.filter(numero_documento=556).exists())
        r, js = self._importar('/app/api/importar-dtes/', 'archivo_dtes', csv_txt, client=c,
                               modo_actualizacion='solo_crear')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(js['dtes_creados'], 1)

    def test_sin_permiso_de_crear_no_importa(self):
        otorgar_ver_pantalla('vendedor', 'gestion_dte_compras', puede_crear=False)
        vend = crear_usuario(username='m1_vend', rol='vendedor')
        crear_empresa_user(vend, self.empresa, self.sucursal)
        csv_txt = f'{self.CAB}\n{self.proveedor.rut},557,33,2026-01-10,119000,x\n'
        r, _ = self._importar('/app/api/importar-dtes/', 'archivo_dtes', csv_txt, client=self._cliente(vend))
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Dte.objects.filter(numero_documento=557).exists())

    def test_actualizacion_parcial_no_toca_columnas_ausentes(self):
        d = _dte(self.proveedor, self.empresa, 600, fecha=date(2026, 3, 1))
        csv_txt = f'rut_proveedor,numero_documento,monto_con_iva\n{self.proveedor.rut},600,238000\n'
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(js['dtes_actualizados'], 1, js)
        d.refresh_from_db()
        self.assertEqual(d.monto_con_iva, Decimal('238000'))
        self.assertEqual(d.monto_neto, Decimal('200000'))
        self.assertEqual(d.fecha_emision, date(2026, 3, 1))   # no vino: no pasa a hoy
        self.assertEqual(d.bultos, 1)                          # no vino: no pasa a 0
        self.assertEqual(d.unidades_productos, 5)
        self.assertEqual(d.referencias, 'ref original')

    def test_dte_con_pagos_no_cambia_montos_ni_fecha(self):
        d = _dte(self.proveedor, self.empresa, 601, fecha=date(2026, 3, 1))
        Dte_Detalle_Pago.objects.create(dte=d, metodo_pago='Transferencia', voucher='v', monto=1000,
                                        fecha_pago=timezone.localdate())
        csv_txt = (f'rut_proveedor,numero_documento,fecha_emision,monto_con_iva,referencias,bultos\n'
                   f'{self.proveedor.rut},601,2020-01-01,1000,nueva,4\n')
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(js['dtes_actualizados'], 1, js)
        self.assertTrue(any('no se cambiaron montos' in e for e in js['errores']))
        d.refresh_from_db()
        self.assertEqual(d.monto_con_iva, Decimal('119000'))
        self.assertEqual(d.fecha_emision, date(2026, 3, 1))
        self.assertEqual(d.referencias, 'nueva')
        self.assertEqual(d.bultos, 4)

    def test_nc_aplicada_como_pago_no_cambia_montos(self):
        """Revisión B11-01/B3-10: la NC de compra aplicada como pago de otra
        factura (Dte_Detalle_Pago de la factura, voucher = folio de la NC) no
        tiene pagos propios; antes el import le cambiaba el monto y el pago de
        la factura quedaba con el valor viejo."""
        nc = _dte(self.proveedor, self.empresa, 618, monto=10169, monto_neto=Decimal('8545.38'),
                  tipo_documento='NOTA DE CREDITO', es_nota_credito=True)
        factura = _dte(self.proveedor, self.empresa, 619)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Nota de Crédito', voucher='618',
                                        monto=10169, fecha_pago=timezone.localdate())
        # Factura del mismo proveedor con el MISMO folio que la NC: no está
        # aplicada como nada, así que sí se actualiza.
        misma_folio = _dte(self.proveedor, self.empresa, 618, monto=50000)
        csv_txt = ('rut_proveedor,numero_documento,tipo_documento,monto_con_iva\n'
                   f'{self.proveedor.rut},618,NOTA DE CREDITO,1000\n'
                   f'{self.proveedor.rut},618,FACTURA ELECTRONICA,60000\n')
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(r.status_code, 200, js)
        self.assertTrue(any('aplicado como pago' in e for e in js['errores']), js)
        nc.refresh_from_db()
        self.assertEqual((nc.monto_con_iva, nc.monto_neto), (Decimal('10169'), Decimal('8545.38')))
        misma_folio.refresh_from_db()
        self.assertEqual(misma_folio.monto_con_iva, Decimal('60000'))

    def test_solo_total_sin_cambio_no_recalcula_el_neto(self):
        """Revisión B11-01: la plantilla trae solo monto_con_iva; si el total
        no cambió, el neto histórico con decimales no se reescribe."""
        d = _dte(self.proveedor, self.empresa, 960, monto=1325537, monto_neto=Decimal('1113896.64'))
        csv_txt = f'rut_proveedor,numero_documento,monto_con_iva\n{self.proveedor.rut},960,1325537\n'
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.assertEqual((js['dtes_actualizados'], js['dtes_sin_cambios']), (0, 1), js)
        d.refresh_from_db()
        self.assertEqual(d.monto_neto, Decimal('1113896.64'))
        # Si el total sí cambia, el neto se recalcula con él.
        csv_txt = f'rut_proveedor,numero_documento,monto_con_iva\n{self.proveedor.rut},960,119000\n'
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(js['dtes_actualizados'], 1, js)
        d.refresh_from_db()
        self.assertEqual((d.monto_con_iva, d.monto_neto), (Decimal('119000'), Decimal('100000')))

    def test_emisor_es_la_propia_empresa_se_rechaza(self):
        """Revisión B13-04: emisor = receptor no es una compra. Una factura de
        OTRA empresa del grupo sí es una compra real y se acepta."""
        csv_txt = (f'{self.CAB}\n{self.empresa.rut},987654,FACTURA ELECTRONICA,2026-06-01,119000,x\n'
                   f'{self.otra_empresa.rut},987655,33,2026-06-01,119000,x\n')
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='solo_crear')
        self.assertEqual(js['dtes_creados'], 1, js)
        self.assertFalse(Dte.objects.filter(numero_documento=987654).exists())
        self.assertIn('misma empresa', js['errores'][0])
        self.assertTrue(Dte.objects.filter(numero_documento=987655, emisor=self.otra_empresa,
                                           receptor=self.empresa, tipo_transaccion='COMPRA').exists())

    def test_codigo_sii_se_traduce_y_detecta_duplicado(self):
        _dte(self.proveedor, self.empresa, 280)
        csv_txt = (f'{self.CAB}\n{self.proveedor.rut},280,33,2026-01-10,1000,x\n'
                   f'{self.proveedor.rut},281,61,2026-01-10,11900,x\n'
                   f'{self.proveedor.rut},282,99,2026-01-10,11900,x\n')
        r, js = self._importar_dtes(csv_txt, modo_actualizacion='solo_crear')
        self.assertEqual(js['dtes_omitidos'], 1, js)
        self.assertEqual(js['dtes_creados'], 1)
        self.assertEqual(Dte.objects.filter(numero_documento=280).count(), 1)
        nc = Dte.objects.get(numero_documento=281)
        self.assertEqual(nc.tipo_documento, 'NOTA DE CREDITO')
        self.assertTrue(nc.es_nota_credito)
        self.assertFalse(Dte.objects.filter(numero_documento=282).exists())
        self.assertTrue(any('no reconocido' in e for e in js['errores']))

    def test_fecha_invalida_es_error_y_exenta_sin_iva(self):
        csv_txt = ('rut_proveedor,numero_documento,tipo_documento,fecha_emision,monto_con_iva,monto_neto\n'
                   f'{self.proveedor.rut},700,33,31/31/2026,119000,\n'
                   f'{self.proveedor.rut},701,33,,119000,\n'
                   f'{self.proveedor.rut},702,FACTURA EXENTA,2026-01-05,50000,\n'
                   f'{self.proveedor.rut},703,33,11/12/24,,100001\n'
                   f'{self.proveedor.rut},704,33,2026-01-05,"119.000",\n')
        r, js = self._importar_dtes(csv_txt)
        self.assertEqual(js['dtes_creados'], 3, js)
        self.assertFalse(Dte.objects.filter(numero_documento__in=[700, 701]).exists())
        exenta = Dte.objects.get(numero_documento=702)
        self.assertEqual((exenta.monto_neto, exenta.monto_con_iva), (Decimal('50000'), Decimal('50000')))
        d703 = Dte.objects.get(numero_documento=703)
        self.assertEqual(d703.fecha_emision, date(2024, 12, 11))
        self.assertEqual(d703.monto_con_iva, Decimal('119001'))   # 100001 + IVA redondeado a peso
        self.assertEqual(Dte.objects.get(numero_documento=704).monto_con_iva, Decimal('119000'))

    def test_bom_y_punto_y_coma(self):
        contenido = ('﻿rut_proveedor;numero_documento;fecha_emision;monto_con_iva\n'
                     f'{self.proveedor.rut};800;05-01-2026;119000\n').encode('utf-8')
        r, js = self._importar_dtes(contenido)
        self.assertEqual(js['dtes_creados'], 1, js)

    def test_xls_se_rechaza_con_instrucciones(self):
        r, js = self._importar_dtes(b'\xd0\xcf\x11\xe0', nombre='viejo.xls')
        self.assertFalse(js['success'])
        self.assertIn('.xlsx', js['error'])

    def test_error_de_bd_en_una_fila_no_revierte_las_demas(self):
        """B11-05: antes un DataError marcaba el atomic único para rollback y
        la respuesta decía 'N creados' con 0 guardados."""
        original = Dte.objects.create
        llamadas = {'n': 0}

        def create_que_falla_la_segunda(**kwargs):
            llamadas['n'] += 1
            if llamadas['n'] == 2:
                kwargs['tipo_documento'] = 'X' * 40   # varchar(20): DataError real de la BD
            return original(**kwargs)

        csv_txt = (f'{self.CAB}\n{self.proveedor.rut},901,33,2026-01-10,119000,a\n'
                   f'{self.proveedor.rut},902,33,2026-01-10,119000,b\n'
                   f'{self.proveedor.rut},903,33,2026-01-10,119000,c\n')
        with mock.patch.object(Dte.objects, 'create', side_effect=create_que_falla_la_segunda):
            r, js = self._importar_dtes(csv_txt)
        self.assertEqual(js['dtes_creados'], 2, js)
        self.assertEqual(len(js['errores']), 1)
        self.assertEqual(
            sorted(Dte.objects.filter(numero_documento__in=[901, 902, 903]).values_list('numero_documento', flat=True)),
            [901, 903],
        )

    def test_exportar_y_reimportar_no_cambia_nada(self):
        d = _dte(self.proveedor, self.empresa, 950, fecha=date(2026, 2, 1))
        Dte_Detalle_Pago.objects.create(dte=d, metodo_pago='Transferencia', voucher='v', monto=1,
                                        fecha_pago=timezone.localdate())
        _dte(self.proveedor, self.empresa, 951, fecha=date(2026, 2, 2), tipo_documento='FACTURA EXENTA',
             monto=50000, monto_neto=50000)
        antes = list(Dte.objects.order_by('id').values())
        exportado = self.client.get('/app/api/exportar-dtes-actuales/?tipo=rut').content
        r, js = self._importar_dtes(exportado, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(js['dtes_actualizados'], 0, js)
        self.assertEqual(js['dtes_sin_cambios'], 2, js)
        self.assertEqual(list(Dte.objects.order_by('id').values()), antes)


class ImportarProveedoresTest(_BaseM1):

    def test_exportar_excel_y_reimportar_no_cambia_fichas(self):
        # Ficha cliente gemela (id menor, mismo RUT) y la ficha proveedor.
        gemela = crear_empresa(nombre='Proveedor M1 como cliente', rut=self.proveedor.rut.replace('-', ''),
                               esProveedor=False, acteco='111111')
        gemela.id, self.proveedor.id  # noqa: B018 (solo para dejar claro el orden)
        campos = ['nombre', 'rut', 'nombre_fantasia', 'razon_social', 'giro', 'direccion', 'comuna',
                  'ciudad', 'esProveedor', 'correoVendedor', 'correoIntercambio',
                  'correoAdministrador', 'acteco', 'contacto1', 'contacto2']
        antes = {e['id']: e for e in Empresa.objects.values('id', *campos)}
        for url, nombre in (('/app/api/exportar-proveedores-excel/', 'p.xlsx'),
                            ('/app/api/exportar-proveedores-actuales/', 'p.csv')):
            contenido = self.client.get(url).content
            r, js = self._importar_proveedores(contenido, nombre=nombre, modo_actualizacion='crear_y_actualizar')
            self.assertEqual(r.status_code, 200, js)
            self.assertEqual(js['proveedores_actualizados'], 0, js)
            self.assertEqual(js['proveedores_creados'], 0, js)
            despues = {e['id']: e for e in Empresa.objects.values('id', *campos)}
            self.assertEqual(despues, antes, nombre)
            # Las empresas del grupo se informan, no se tocan.
            self.assertTrue(any('empresa del grupo' in e for e in js['errores']))

    def test_actualiza_solo_columnas_con_valor_y_prefiere_ficha_proveedor(self):
        gemela = crear_empresa(nombre='Gemela cliente', rut=self.proveedor.rut, esProveedor=False)
        csv_txt = f'rut,nombre,giro,direccion\n{self.proveedor.rut},Proveedor M1,Nuevo giro,\n'
        r, js = self._importar_proveedores(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(js['proveedores_actualizados'], 1, js)
        self.proveedor.refresh_from_db()
        gemela.refresh_from_db()
        self.assertEqual(self.proveedor.giro, 'Nuevo giro')
        self.assertEqual(self.proveedor.direccion, 'Av. Proveedor 100')
        self.assertEqual(self.proveedor.correoIntercambio, 'dte@prov.cl')
        self.assertEqual(self.proveedor.acteco, '469000')
        self.assertEqual(gemela.nombre, 'Gemela cliente')
        self.assertFalse(gemela.esProveedor)

    def test_no_modifica_empresas_del_grupo(self):
        csv_txt = f'rut,nombre,giro\n{self.otra_empresa.rut},EMPRESA X,GIRO X\n'
        r, js = self._importar_proveedores(csv_txt, modo_actualizacion='crear_y_actualizar')
        self.otra_empresa.refresh_from_db()
        self.assertEqual(self.otra_empresa.nombre, 'Otra del grupo')
        self.assertIn('empresa del grupo', js['errores'][0])

    def test_reimportar_proveedor_sin_correo_queda_sin_cambios(self):
        """Revisión B11-04: la ficha creada sin email (placeholder en los 3
        correos) aparecía 'actualizada' en cada reimportación."""
        rut = rut_valido(76123456)
        r, js = self._importar_proveedores(f'rut,nombre\n{rut},Sin Correo SPA\n', modo_actualizacion='solo_crear')
        self.assertEqual(js['proveedores_creados'], 1, js)
        # Otra ficha con un correo vacío y otro placeholder: el placeholder
        # del archivo no se copia a los vacíos.
        parcial = crear_empresa(nombre='Correo Parcial', rut=rut_valido(77234567), esProveedor=True,
                                correoVendedor='', correoIntercambio='sin@correo.com', correoAdministrador='')
        exportado = self.client.get('/app/api/exportar-proveedores-actuales/').content
        for _ in range(2):
            r, js = self._importar_proveedores(exportado, nombre='p.csv', modo_actualizacion='crear_y_actualizar')
            self.assertEqual(js['proveedores_actualizados'], 0, js)
        parcial.refresh_from_db()
        self.assertEqual((parcial.correoVendedor, parcial.correoAdministrador), ('', ''))
        # Un correo real sí se usa si la ficha no tiene ninguno.
        r, js = self._importar_proveedores(f'rut,nombre,email\n{rut},Sin Correo SPA,real@prov.cl\n',
                                           modo_actualizacion='crear_y_actualizar')
        self.assertEqual(js['proveedores_actualizados'], 1, js)
        self.assertEqual(Empresa.objects.get(rut=rut).correoIntercambio, 'real@prov.cl')

    def test_xlsx_con_dimension_mal_declarada(self):
        """Revisión B11-13: read_only corta la hoja según <dimension>; un
        exportador que escribe ref="A1" dejaba el archivo como solo encabezado."""
        import re
        import zipfile

        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['rut', 'nombre'])
        ws.append([rut_valido(76123456), 'Prueba Dim SPA'])
        original = io.BytesIO()
        wb.save(original)
        entrada = zipfile.ZipFile(io.BytesIO(original.getvalue()))
        salida_buf = io.BytesIO()
        with zipfile.ZipFile(salida_buf, 'w', zipfile.ZIP_DEFLATED) as salida:
            for item in entrada.infolist():
                datos = entrada.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    datos, n = re.subn(rb'<dimension ref="[^"]+"', b'<dimension ref="A1"', datos)
                    self.assertEqual(n, 1)
                salida.writestr(item, datos)
        r, js = self._importar_proveedores(salida_buf.getvalue(), nombre='dim.xlsx', modo_actualizacion='solo_crear')
        self.assertEqual(js['proveedores_creados'], 1, js)
        self.assertTrue(Empresa.objects.filter(nombre='Prueba Dim SPA').exists())

    def test_fila_larga_no_revierte_las_demas(self):
        csv_txt = (f'rut,nombre\n{rut_valido(76123456)},Uno M1\n{rut_valido(77234567)},{"X" * 120}\n'
                   f'{rut_valido(11111111)},Tres M1\n')
        r, js = self._importar_proveedores(csv_txt, modo_actualizacion='solo_crear')
        self.assertEqual(js['proveedores_creados'], 2, js)
        self.assertEqual(len(js['errores']), 1)
        self.assertEqual(Empresa.objects.filter(nombre__in=['Uno M1', 'Tres M1']).count(), 2)

    def test_formato_descargable_se_importa_tal_cual(self):
        contenido = self.client.get('/app/api/descargar-formato-proveedores/').content
        self.assertTrue(contenido.startswith(b'\xef\xbb\xbf'))
        r, js = self._importar_proveedores(contenido, modo_actualizacion='solo_crear')
        self.assertEqual(js['proveedores_creados'], 2, js)
        self.assertEqual(js['errores'], [])
        fmt_dtes = self.client.get('/app/api/descargar-formato-dtes/?tipo=rut').content
        r, js = self._importar_dtes_fmt(fmt_dtes)
        self.assertEqual(js['dtes_creados'], 2, js)
        self.assertEqual(set(Dte.objects.filter(numero_documento__in=[12345, 12346])
                             .values_list('tipo_documento', flat=True)), {'FACTURA ELECTRONICA'})

    def _importar_dtes_fmt(self, contenido):
        return self._importar('/app/api/importar-dtes/', 'archivo_dtes', contenido)

    def test_csv_excel_es_cl_cp1252_punto_y_coma(self):
        contenido = f'rut;nombre;dirección\n{rut_valido(76543210)};Compañía Ñandú;Av. Peñalolén 1\n'.encode('cp1252')
        r, js = self._importar_proveedores(contenido, modo_actualizacion='crear_y_actualizar')
        self.assertEqual(js['proveedores_creados'], 1, js)
        e = Empresa.objects.get(rut=rut_valido(76543210))
        self.assertEqual((e.nombre, e.direccion), ('Compañía Ñandú', 'Av. Peñalolén 1'))

    def test_actualizar_exige_permiso_de_edicion(self):
        otorgar_ver_pantalla('jefe_local', 'gestion_dte_compras', puede_crear=True, puede_editar=False)
        jefe = crear_usuario(username='m1_jefe_p', rol='jefe_local')
        crear_empresa_user(jefe, self.empresa, self.sucursal)
        csv_txt = f'rut,nombre,giro\n{self.proveedor.rut},Proveedor M1,OTRO\n'
        r, js = self._importar('/app/api/importar-proveedores/', 'archivo_proveedores', csv_txt,
                               client=self._cliente(jefe), modo_actualizacion='crear_y_actualizar')
        self.assertEqual(r.status_code, 403)
        self.proveedor.refresh_from_db()
        self.assertNotEqual(self.proveedor.giro, 'OTRO')


class EliminarDteTest(_BaseM1):

    def _delete(self, dte_id, **body):
        r = self.client.delete(f'/app/eliminarDTE/{dte_id}/', data=json.dumps(body),
                               content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        return r, json.loads(r.content)

    def test_forzar_no_borra_kardex_ni_lotes(self):
        d = _dte(self.proveedor, self.empresa, 1200)
        _producto, pt = crear_producto_con_talla(self.sucursal)
        lote = crear_lote_fifo(pt, dte=d)
        mov = Movimientos_Producto.objects.create(dte=d, ProductoTalla=pt, sucursal_destino=self.sucursal,
                                                  cantidad=2, concepto='INGRESO_INICIAL')
        r, js = self._delete(d.id, forzar=True)
        self.assertEqual(r.status_code, 200, js)
        self.assertTrue(js.get('soft_delete'))
        d.refresh_from_db()
        self.assertTrue(d.descartado)
        self.assertTrue(LoteProducto.objects.filter(id=lote.id).exists())
        self.assertTrue(Movimientos_Producto.objects.filter(id=mov.id).exists())
        self.assertIn('stock', js['message'])

    def test_dte_de_otra_empresa_404(self):
        ajena = _dte(self.proveedor, self.otra_empresa, 1201)
        r, js = self._delete(ajena.id)
        self.assertEqual(r.status_code, 404)
        ajena.refresh_from_db()
        self.assertFalse(ajena.descartado)

    def test_descarte_avisa_pagos(self):
        d = _dte(self.proveedor, self.empresa, 1202)
        Dte_Detalle_Pago.objects.create(dte=d, metodo_pago='Transferencia', voucher='v', monto=40000,
                                        fecha_pago=timezone.localdate())
        r, js = self._delete(d.id)
        self.assertEqual(r.status_code, 200, js)
        self.assertIn('pago', js['message'])
        self.assertTrue(Dte_Detalle_Pago.objects.filter(dte=d).exists())

    def test_descartar_nc_aplicada_avisa_la_factura_que_rebaja(self):
        """B3-10: la NC aplicada como pago de otra factura no tiene pagos
        propios; antes se descartaba sin aviso y la factura quedaba rebajada
        por un documento descartado."""
        nc = _dte(self.proveedor, self.empresa, 1718, monto=10169, monto_neto=8545,
                  tipo_documento='NOTA DE CREDITO', es_nota_credito=True)
        factura = _dte(self.proveedor, self.empresa, 1719)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Nota de Crédito', voucher='1718',
                                        monto=10169, fecha_pago=timezone.localdate())
        r, js = self._delete(nc.id)
        self.assertEqual(r.status_code, 200, js)
        self.assertTrue(any('aplicado como pago' in a and '1719' in a for a in js['avisos']), js)
        self.assertIn('10.169', js['message'])
        self.assertTrue(Dte_Detalle_Pago.objects.filter(dte=factura, voucher='1718').exists())

    def test_nc_de_otro_proveedor_con_el_mismo_folio_no_avisa(self):
        otro = crear_empresa(nombre='Otro prov', rut=rut_valido(77400000), esProveedor=True)
        nc = _dte(self.proveedor, self.empresa, 1720, monto=5000, tipo_documento='NOTA DE CREDITO',
                  es_nota_credito=True)
        factura_otro = _dte(otro, self.empresa, 1721)
        Dte_Detalle_Pago.objects.create(dte=factura_otro, metodo_pago='Nota de Crédito', voucher='1720',
                                        monto=5000, fecha_pago=timezone.localdate())
        r, js = self._delete(nc.id)
        self.assertEqual(r.status_code, 200, js)
        self.assertEqual(js['avisos'], [])


class ExportarComprasTest(_BaseM1):

    def _compra(self, nombre, estado='ACTIVA', tallas=((('38', 10),))):
        compra = Compras.objects.create(empresa=self.proveedor, nombre=nombre, correlativo=1, responsable='t',
                                        temporada='Invierno', fecha=date(2026, 3, 1), estado=estado)
        prod = Compras_Producto.objects.create(
            compras=compra, nombre=f'=HYPERLINK("http://x","{nombre}")', descripcion='d', atributo1='NIKE',
            atributo2='NEGRO', atributo3='HOMBRE', atributo4='', costo=1000, precioSugerido=2380)
        tallas_creadas = [Compras_Producto_Talla.objects.create(compra_producto=prod, stock=s, talla=t)
                          for t, s in tallas]
        return compra, tallas_creadas

    def test_csv_sin_eliminadas_suma_recepciones_y_neutraliza_formulas(self):
        compra, (talla,) = self._compra('Activa')
        self._compra('Borrada', estado='ELIMINADA')
        f1 = _dte(self.proveedor, self.empresa, 111)
        f2 = _dte(self.proveedor, self.empresa, 222)
        Productos_Recepcionados.objects.create(compra_producto_talla=talla, stockArribado=4, dte=f1)
        Productos_Recepcionados.objects.create(compra_producto_talla=talla, stockArribado=6, dte=f2)
        r = self.client.get('/app/api/exportar-compras-csv/?anio=2026')
        self.assertEqual(r.status_code, 200)
        filas = list(csv.reader(io.StringIO(r.content.decode('utf-8-sig'))))
        self.assertEqual(len(filas), 2, filas)   # encabezado + la compra ACTIVA
        fila = dict(zip(filas[0], filas[1]))
        self.assertEqual(fila['Recepcionado'], '10')
        self.assertEqual(fila['Factura DTE'], '111, 222')
        self.assertTrue(fila['Nombre Producto'].startswith("'="))

    def test_excel_resumen_cuadra_con_detalle_y_no_deja_formulas(self):
        import openpyxl
        compra, (talla,) = self._compra('Activa')
        self._compra('Borrada', estado='ELIMINADA')
        Productos_Recepcionados.objects.create(compra_producto_talla=talla, stockArribado=4)
        Productos_Recepcionados.objects.create(compra_producto_talla=talla, stockArribado=6)
        r = self.client.get('/app/api/exportar-compras-excel/?anio=2026')
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        resumen = list(wb['Resumen Compras'].iter_rows(values_only=True))
        detalle_ws = wb['Detalle Productos']
        detalle = list(detalle_ws.iter_rows(values_only=True))
        self.assertEqual(len(resumen), 2)
        self.assertEqual(len(detalle), 2)
        self.assertEqual(resumen[1][13], 10)
        self.assertEqual(detalle[1][12], 10)
        celda = detalle_ws.cell(row=2, column=4)
        self.assertEqual(celda.data_type, 's')
        self.assertTrue(str(celda.value).startswith('=HYPERLINK'))

    def test_consultas_no_crecen_con_las_tallas(self):
        self._compra('A', tallas=(('38', 1), ('39', 1)))

        def contar():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get('/app/api/exportar-compras-csv/?anio=2026')
            return len(ctx.captured_queries)

        contar()            # la primera petición paga cachés de sesión/permisos
        base = contar()
        self._compra('B', tallas=tuple((str(t), 1) for t in range(30, 45)))
        self.assertEqual(contar(), base)

    def test_anio_invalido(self):
        r = self.client.get('/app/api/exportar-compras-csv/?anio=abc')
        self.assertEqual(r.status_code, 400)


class ExportarProveedoresFormulasTest(_BaseM1):
    def test_csv_neutraliza_formulas(self):
        crear_empresa(nombre='=cmd|"/c calc"!A1', rut=rut_valido(76999999), esProveedor=True, contacto1='+56911112222')
        r = self.client.get('/app/api/exportar-proveedores-actuales/')
        texto = r.content.decode('utf-8-sig')
        self.assertIn("'=cmd", texto)
        self.assertIn("'+56911112222", texto)


class ResumenPendientesTest(TestCase):
    def setUp(self):
        self.proveedor = crear_empresa(nombre='Prov KPI', rut=rut_valido(77111111), esProveedor=True)
        self.nuestra = crear_empresa(nombre='Nosotros KPI', rut=rut_valido(76222222))
        self.user = crear_usuario(username='kpi-m1')
        self.hoy = timezone.localdate()

    def _resumen(self):
        req = RequestFactory().get('/app/api/resumen-pendientes-anio/')
        req.user = self.user
        req.session = {'idEmpresaActual': self.nuestra.id}
        return json.loads(obtener_resumen_pendientes_anio(req).content)

    def _d(self, numero, **kw):
        kw.setdefault('fecha', self.hoy)
        d = _dte(self.proveedor, self.nuestra, numero, **kw)
        d.fecha_vencimiento = self.hoy + timedelta(days=30)
        d.save(update_fields=['fecha_vencimiento'])
        return d

    def test_universo_y_saldo(self):
        self._d(1, monto=119000)                                           # cuenta 119.000
        self._d(2, monto=50000, tipo_documento='NOTA DE CREDITO', es_nota_credito=True)  # NC: fuera
        self._d(3, monto=70000, descartado=True)                           # descartado: fuera
        self._d(4, monto=80000, estado_dte='RECHAZADO')                    # rechazado: fuera
        self._d(5, monto=90000, tipo_documento='COTIZACION')               # cotización: fuera
        abonada = self._d(6, monto=100000, estado_pago='Abonado')          # cuenta 60.000
        Dte_Detalle_Pago.objects.create(dte=abonada, metodo_pago='Transferencia', voucher='a', monto=40000,
                                        fecha_pago=self.hoy)
        pagada_sin_marcar = self._d(7, monto=30000, estado_pago='PENDIENTE')  # saldo 0: fuera
        Dte_Detalle_Pago.objects.create(dte=pagada_sin_marcar, metodo_pago='Transferencia', voucher='b',
                                        monto=30000, fecha_pago=self.hoy)
        self._d(8, monto=20000, fecha=date(2025, 12, 15))                  # año anterior: cuenta
        self._d(9, monto=10000, fecha=date(2019, 5, 1))                    # legacy: fuera
        r = self._resumen()
        self.assertTrue(r['success'])
        self.assertEqual(r['cantidad_pendientes'], 3, r)
        self.assertEqual(int(r['monto_pendiente']), 119000 + 60000 + 20000)
        self.assertEqual(r['desde'], '2025-01-01')

    def test_consultas_constantes(self):
        for n in range(10):
            self._d(100 + n)
        with CaptureQueriesContext(connection) as ctx:
            self._resumen()
        self.assertLessEqual(len(ctx.captured_queries), 3)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class CompensacionManualReusoTest(_BaseM1):

    def _post(self, body):
        r = self.client.post('/app/asociar_documento_emitido_compensacion/', data=json.dumps(body),
                             content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        return r, json.loads(r.content)

    def test_reuso_del_mismo_numero_se_registra_con_advertencia(self):
        """Repartir una factura emitida manual entre varias compras es válido
        y la pantalla no tiene paso de confirmación: se registra (200) y el
        mensaje avisa lo ya aplicado. El número se compara normalizado."""
        f1 = _dte(self.proveedor, self.empresa, 3001, monto=100000)
        f2 = _dte(self.proveedor, self.empresa, 3002, monto=100000)
        f3 = _dte(self.proveedor, self.empresa, 3003, monto=100000)
        r, js = self._post({'dte_id': f1.id, 'modo': 'manual', 'numero': '777', 'monto': 50000})
        self.assertEqual(r.status_code, 200, js)
        self.assertNotIn('advertencia', js)
        r, js = self._post({'dte_id': f2.id, 'modo': 'manual', 'numero': '000777', 'monto': 30000})
        self.assertEqual(r.status_code, 200, js)
        self.assertIn('3001', js['advertencia'])
        self.assertIn('50.000', js['advertencia'])
        self.assertIn('Ojo', js['message'])
        pago = Dte_Detalle_Pago.objects.get(dte=f2, metodo_pago=METODO_COMPENSACION_EMITIDA)
        self.assertEqual(pago.voucher, '777')   # se guarda el folio canónico
        r, js = self._post({'dte_id': f3.id, 'modo': 'manual', 'numero': 'F-0777', 'monto': 10000})
        self.assertEqual(r.status_code, 200, js)
        self.assertIn('3001, 3002', js['advertencia'])
        self.assertIn('80.000', js['advertencia'])

    def test_numero_sin_digitos_se_rechaza(self):
        f1 = _dte(self.proveedor, self.empresa, 3011, monto=100000)
        r, js = self._post({'dte_id': f1.id, 'modo': 'manual', 'numero': 'ABC', 'monto': 5000})
        self.assertEqual(r.status_code, 400, js)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=f1).exists())

    def test_tope_total_de_la_factura_emitida(self):
        f1 = _dte(self.proveedor, self.empresa, 3101, monto=100000)
        f2 = _dte(self.proveedor, self.empresa, 3102, monto=100000)
        self._post({'dte_id': f1.id, 'modo': 'manual', 'numero': '778', 'monto': 50000})
        r, js = self._post({'dte_id': f2.id, 'modo': 'manual', 'numero': '0778', 'monto': 50000,
                            'monto_total_emitida': 60000})
        self.assertEqual(r.status_code, 400, js)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=f2).exists())

    def test_factura_emitida_existente_debe_usar_su_pestana(self):
        objetivo = _dte(self.proveedor, self.empresa, 3201, monto=100000)
        _dte(self.empresa, self.proveedor, 9901, tipo_transaccion='VENTA', monto=50000)
        for numero in ('9901', '009901', 'N° 9901'):
            r, js = self._post({'dte_id': objetivo.id, 'modo': 'manual', 'numero': numero, 'monto': 10000})
            self.assertEqual(r.status_code, 400, (numero, js))
            self.assertIn('existentes', js['error'])
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=objetivo).exists())
