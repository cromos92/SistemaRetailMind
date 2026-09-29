"""
Regresiones de la auditoría de Gestión de Inventarios (29-09-2026).

Cada test nombra el hallazgo que fija:
- N2  guarda anti doble aplicación (detalle releído bajo lock, lock de tarea)
- H5/N9 reconteos pendientes sin excluidas; finalizar desde BORRADOR con conteos
- H13 pertenencia en estado-ajustes
- H8  empresa de la toma = empresa de la sucursal
- N7  fechas en hora local en el listado
- N3  cancelar rechaza tomas con ajustes aplicados
- N4/N5 conteo negativo rechazado; SKU '4805622.0' casa
- H2  detección de columnas (Excel de la tienda), error si ambiguo, mapeo explícito
- H1/N1 corte futuro recortado; fecha_conteo explícita; escáner en vivo usa ahora
- H3  no contados en bloque (excluir / sin diferencia)
- H4  SKU con stock 0 se agrega a la toma (archivo y escáner); SKU duplicado = ambiguo
- H7  umbral de reconteo para stocks de 1-4 unidades
- H6  tarea huérfana: reanudar; command aplicar_ajustes_toma síncrono

Correr:  DATABASE_URL='sqlite://:memory:' python manage.py test app.tests.test_toma_inventario_correcciones
(en sqlite select_for_update es no-op: se verifica la GUARDA lógica, no el lock)
"""
import io
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.models import (
    LoteProducto, Movimientos_Producto, Producto, Producto_Talla,
    TareaAplicacionAjustes, TomaInventario, TomaInventarioDetalle,
)
from app.models.inventario import requiere_reconteo
from app.views_gestion_inventarios import (
    _aplicar_ajuste_individual, _detectar_indices_conteo, _ejecutar_ajustes_background,
    _iniciar_tarea_ajustes, _normalizar_sku, _tarea_huerfana, resolver_no_contados,
)
from .factories import (
    crear_empresa, crear_sucursal, crear_usuario, crear_empresa_user, otorgar_ver_pantalla,
)
from .test_toma_inventario import BaseTomaInventarioTest


class BaseCorrecciones(BaseTomaInventarioTest):

    def _toma(self, estado='BORRADOR', fecha_corte=None, **extra):
        return TomaInventario.objects.create(
            numero_inventario=TomaInventario.generar_numero_inventario(self.sucursal),
            nombre='Toma manual',
            sucursal=self.sucursal,
            empresa=self.empresa,
            tipo_inventario='COMPLETO',
            fecha_corte=fecha_corte or timezone.now(),
            estado=estado,
            creado_por=self.user,
            **extra,
        )

    def _detalle(self, toma, pt, stock_sistema, stock_fisico=None, contado=True, **extra):
        detalle = TomaInventarioDetalle(
            toma_inventario=toma,
            producto_talla=pt,
            sku=str(pt.sku),
            producto_nombre=pt.producto.articulo,
            talla_nombre=pt.talla or '',
            stock_sistema=stock_sistema,
            stock_sistema_ajustado=stock_sistema,
            costo_unitario_sistema=Decimal('10000'),
            precio_venta_sistema=Decimal('19990'),
            contado=contado,
            **extra,
        )
        if contado:
            detalle.stock_fisico = stock_fisico
            detalle.fecha_conteo = timezone.now()
        detalle.save()
        return detalle

    def _post(self, url_name, toma_id, payload=None):
        return self.client.post(
            reverse(url_name, args=[toma_id]),
            data=json.dumps(payload or {}), content_type='application/json',
        ).json()

    def _xlsx(self, filas, nombre='conteo.xlsx'):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        for fila in filas:
            ws.append(fila)
        buf = io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile(
            nombre, buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def _importar_archivo(self, toma_id, archivo, **campos):
        data = {'archivo': archivo}
        data.update(campos)
        return self.client.post(reverse('api_importar_conteo_pistola', args=[toma_id]), data).json()

    def _preview_archivo(self, toma_id, archivo, **campos):
        data = {'archivo': archivo}
        data.update(campos)
        return self.client.post(reverse('api_preview_conteo_pistola', args=[toma_id]), data).json()


# ---------------------------------------------------------------------------
# N2 — doble aplicación
# ---------------------------------------------------------------------------
class GuardaDobleAplicacionTest(BaseCorrecciones):

    def test_aplicar_dos_veces_el_mismo_detalle_no_duplica(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=8100001, stock=2)
        toma = self._toma(estado='APROBADO')
        detalle = self._detalle(toma, pt, stock_sistema=2, stock_fisico=3)
        # Dos objetos "stale" del mismo detalle, como dos workers que leyeron antes
        stale_1 = TomaInventarioDetalle.objects.get(pk=detalle.pk)
        stale_2 = TomaInventarioDetalle.objects.get(pk=detalle.pk)

        self.assertTrue(_aplicar_ajuste_individual(stale_1, toma, self.user))
        self.assertFalse(_aplicar_ajuste_individual(stale_2, toma, self.user))

        pt.refresh_from_db()
        self.assertEqual(pt.stock, 3)
        self.assertEqual(
            Movimientos_Producto.objects.filter(ProductoTalla=pt, concepto='AJUSTE_INVENTARIO_ENTRADA').count(), 1
        )
        self.assertEqual(LoteProducto.objects.filter(producto_talla=pt).count(), 1)
        self.assertTrue(stale_2.ajuste_aplicado)  # el objeto del segundo worker queda al día

    def test_worker_con_lista_stale_salta_lo_aplicado(self):
        _, pt1 = self._producto('ZAPATILLA A', sku=8100010, stock=5)
        _, pt2 = self._producto('ZAPATILLA B', sku=8100011, stock=5)
        toma = self._toma(estado='APROBADO')
        self._detalle(toma, pt1, stock_sistema=5, stock_fisico=6)
        self._detalle(toma, pt2, stock_sistema=5, stock_fisico=4)
        TareaAplicacionAjustes.objects.create(inventario=toma, estado='EN_PROCESO', iniciada_en=timezone.now())

        # Worker 2 leyó su lista de pendientes ANTES de que worker 1 aplicara
        lista_stale = list(
            toma.detalles.filter(contado=True, ajuste_aplicado=False, excluir_de_analisis=False).exclude(diferencia=0)
        )
        # Worker 1 corre completo
        _ejecutar_ajustes_background(toma.id, self.user.id, cerrar_conexion=False)
        # Worker 2 recorre su lista en memoria: todo debe saltarse
        resultados = [_aplicar_ajuste_individual(d, toma, self.user) for d in lista_stale]
        self.assertEqual(resultados, [False, False])

        pt1.refresh_from_db(); pt2.refresh_from_db()
        self.assertEqual((pt1.stock, pt2.stock), (6, 4))
        self.assertEqual(
            Movimientos_Producto.objects.filter(
                ProductoTalla__in=[pt1, pt2], concepto__startswith='AJUSTE_INVENTARIO'
            ).count(), 2
        )
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'COMPLETADO')

    def test_iniciar_tarea_dos_veces_solo_una_gana(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=8100020, stock=5)
        toma = self._toma(estado='APROBADO')
        self._detalle(toma, pt, stock_sistema=5, stock_fisico=6)

        tarea_a, iniciada_a = _iniciar_tarea_ajustes(toma, self.user)
        tarea_b, iniciada_b = _iniciar_tarea_ajustes(toma, self.user)
        self.assertTrue(iniciada_a)
        self.assertFalse(iniciada_b)
        self.assertEqual(tarea_a.pk, tarea_b.pk)
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'APLICANDO')

    def test_endpoint_aplicar_devuelve_already_running(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=8100030, stock=5)
        toma = self._toma(estado='APLICANDO')
        self._detalle(toma, pt, stock_sistema=5, stock_fisico=6)
        TareaAplicacionAjustes.objects.create(inventario=toma, estado='EN_PROCESO', iniciada_en=timezone.now())
        resp = self._post('api_aplicar_ajustes', toma.id)
        self.assertTrue(resp['success'])
        self.assertTrue(resp['already_running'])
        self.assertFalse(resp['huerfana'])


# ---------------------------------------------------------------------------
# H6 — tarea huérfana / reanudar / command
# ---------------------------------------------------------------------------
class TareaHuerfanaTest(BaseCorrecciones):

    def _toma_aplicando_huerfana(self, minutos=45):
        _, pt1 = self._producto('ZAPATILLA A', sku=8200001, stock=5)
        _, pt2 = self._producto('ZAPATILLA B', sku=8200002, stock=5)
        toma = self._toma(estado='APLICANDO')
        d1 = self._detalle(toma, pt1, stock_sistema=5, stock_fisico=6)
        d2 = self._detalle(toma, pt2, stock_sistema=5, stock_fisico=7)
        hace = timezone.now() - timedelta(minutes=minutos)
        # d1 ya se aplicó hace rato (el worker murió después)
        TomaInventarioDetalle.objects.filter(pk=d1.pk).update(ajuste_aplicado=True, fecha_ajuste=hace)
        tarea = TareaAplicacionAjustes.objects.create(
            inventario=toma, estado='EN_PROCESO', iniciada_en=hace, total=2, procesados=1,
        )
        return toma, tarea, pt1, pt2, d2

    def test_tarea_huerfana_se_puede_reanudar(self):
        toma, tarea, pt1, pt2, d2 = self._toma_aplicando_huerfana()
        self.assertTrue(_tarea_huerfana(tarea, toma))

        # Sin reanudar: already_running
        resp = self._post('api_aplicar_ajustes', toma.id)
        self.assertTrue(resp.get('already_running'))
        self.assertTrue(resp.get('huerfana'))

        with self.captureOnCommitCallbacks(execute=False):
            resp = self._post('api_aplicar_ajustes', toma.id, {'reanudar': True})
        self.assertTrue(resp['success'], resp)
        self.assertFalse(resp.get('already_running'))
        self.assertTrue(resp['reanudada'])
        # El worker se ejecuta acá de forma síncrona (sin thread)
        _ejecutar_ajustes_background(toma.id, self.user.id, cerrar_conexion=False)

        pt1.refresh_from_db(); pt2.refresh_from_db()
        self.assertEqual(pt1.stock, 5)   # d1 ya estaba aplicado: no se repite
        self.assertEqual(pt2.stock, 7)
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'COMPLETADO')
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, 'COMPLETADO')

    def test_reanudar_con_tarea_viva_es_rechazado(self):
        toma, tarea, pt1, pt2, d2 = self._toma_aplicando_huerfana(minutos=45)
        # Un detalle aplicado hace 1 minuto: el worker sigue vivo
        TomaInventarioDetalle.objects.filter(pk=d2.pk).update(
            ajuste_aplicado=True, fecha_ajuste=timezone.now() - timedelta(minutes=1)
        )
        self.assertFalse(_tarea_huerfana(tarea, toma))
        resp = self._post('api_aplicar_ajustes', toma.id, {'reanudar': True})
        self.assertTrue(resp.get('already_running'))
        self.assertFalse(resp.get('huerfana'))

    def test_estado_ajustes_informa_huerfana(self):
        toma, tarea, *_ = self._toma_aplicando_huerfana()
        resp = self.client.get(reverse('api_estado_tarea_ajustes', args=[toma.id])).json()
        self.assertTrue(resp['success'])
        self.assertTrue(resp['huerfana'])
        self.assertIsNotNone(resp['ultimo_ajuste_en'])

    def test_command_aplica_sincrono_y_es_idempotente(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=8200010, stock=5)
        toma = self._toma(estado='APROBADO')
        self._detalle(toma, pt, stock_sistema=5, stock_fisico=3)

        out = io.StringIO()
        call_command('aplicar_ajustes_toma', toma.numero_inventario, stdout=out)  # informe
        pt.refresh_from_db()
        self.assertEqual(pt.stock, 5)
        self.assertIn('Modo informe', out.getvalue())

        call_command('aplicar_ajustes_toma', str(toma.id), '--aplicar', stdout=out)
        pt.refresh_from_db()
        self.assertEqual(pt.stock, 3)
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'COMPLETADO')
        mov = Movimientos_Producto.objects.get(ProductoTalla=pt, concepto='AJUSTE_INVENTARIO_SALIDA')
        self.assertEqual(mov.referencia_externa, toma.numero_inventario)

    def test_command_reanuda_huerfana(self):
        toma, tarea, pt1, pt2, d2 = self._toma_aplicando_huerfana()
        out = io.StringIO()
        with self.assertRaises(Exception):
            call_command('aplicar_ajustes_toma', str(toma.id), '--aplicar', stdout=out)
        call_command('aplicar_ajustes_toma', str(toma.id), '--aplicar', '--reanudar', stdout=out)
        pt2.refresh_from_db()
        self.assertEqual(pt2.stock, 7)
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'COMPLETADO')


# ---------------------------------------------------------------------------
# H5 / N9 — reconteos pendientes y finalizar desde BORRADOR
# ---------------------------------------------------------------------------
class ReconteosYFinalizarTest(BaseCorrecciones):

    def test_linea_excluida_con_reconteo_no_bloquea_finalizar_ni_enviar(self):
        _, pt1 = self._producto('ZAPATILLA A', sku=8300001, stock=10)
        _, pt2 = self._producto('ZAPATILLA B', sku=8300002, stock=10)
        toma = self._toma(estado='EN_CONTEO')
        self._detalle(toma, pt1, stock_sistema=10, stock_fisico=10)
        d2 = self._detalle(toma, pt2, stock_sistema=10, stock_fisico=2)  # -8 → reconteo
        self.assertTrue(d2.reconteo_requerido)

        resp = self.client.post(
            reverse('api_excluir_detalle_inventario', args=[toma.id, d2.id]),
            data=json.dumps({'excluir': True}), content_type='application/json',
        ).json()
        self.assertTrue(resp['success'])
        d2.refresh_from_db()
        self.assertTrue(d2.excluir_de_analisis)
        self.assertFalse(d2.reconteo_requerido)
        self.assertEqual(toma.reconteos_pendientes().count(), 0)

        resp = self._post('api_finalizar_conteo', toma.id)
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['estado'], 'CONTEO_FINALIZADO')
        resp = self._post('api_enviar_aprobacion', toma.id)
        self.assertTrue(resp['success'], resp)

        # Al reincluir, save() vuelve a marcar el reconteo
        d2.excluir_de_analisis = False
        d2.save()
        self.assertTrue(d2.reconteo_requerido)

    def test_finalizar_desde_borrador_con_conteos(self):
        _, pt = self._producto('ZAPATILLA A', sku=8300010, stock=10)
        toma = self._toma(estado='BORRADOR')
        self._detalle(toma, pt, stock_sistema=10, stock_fisico=10)
        resp = self._post('api_finalizar_conteo', toma.id)
        self.assertTrue(resp['success'], resp)
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'CONTEO_FINALIZADO')

    def test_finalizar_devuelve_desglose_de_no_contados(self):
        _, pt1 = self._producto('ZAPATILLA A', sku=8300020, stock=3)
        _, pt2 = self._producto('ZAPATILLA B', sku=8300021, stock=0)
        toma = self._toma(estado='EN_CONTEO')
        self._detalle(toma, pt1, stock_sistema=3, contado=False)
        self._detalle(toma, pt2, stock_sistema=0, contado=False)
        resp = self._post('api_finalizar_conteo', toma.id)
        self.assertFalse(resp['success'])
        self.assertEqual(resp['pendientes'], 2)
        self.assertEqual(resp['pendientes_con_stock'], 1)
        self.assertEqual(resp['pendientes_sin_stock'], 1)
        self.assertEqual(resp['unidades_sin_contar'], 3)


# ---------------------------------------------------------------------------
# H13 / H8 / N7 / N3
# ---------------------------------------------------------------------------
class PertenenciaEmpresaFechasCancelarTest(BaseCorrecciones):

    def test_estado_ajustes_403_para_otra_empresa(self):
        otra_empresa = crear_empresa(nombre='Otra')
        otra_sucursal = crear_sucursal(otra_empresa, alias='OTRA')
        toma = TomaInventario.objects.create(
            numero_inventario='INV-X-1', nombre='Ajena', sucursal=otra_sucursal, empresa=otra_empresa,
            tipo_inventario='COMPLETO', fecha_corte=timezone.now(), estado='APLICANDO',
        )
        TareaAplicacionAjustes.objects.create(inventario=toma, estado='EN_PROCESO')
        # Usuario NO administrador de la empresa base (con permiso de pantalla,
        # para que sea la VISTA y no el middleware la que rechace)
        vendedor = crear_usuario(username='vendedor1', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        otorgar_ver_pantalla('vendedor', 'gestion_inventarios', puede_editar=True)
        self.client.force_login(vendedor)
        resp = self.client.get(reverse('api_estado_tarea_ajustes', args=[toma.id]))
        self.assertEqual(resp.status_code, 403)

    def test_toma_toma_la_empresa_de_la_sucursal(self):
        # El usuario tiene como PRIMERA EmpresaUser otra empresa: antes se usaba esa
        otra_empresa = crear_empresa(nombre='Primera del usuario')
        otra_sucursal = crear_sucursal(otra_empresa, alias='PRIM')
        from app.models import EmpresaUser
        EmpresaUser.objects.filter(user=self.user).delete()
        crear_empresa_user(self.user, otra_empresa, otra_sucursal)
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self._producto('ZAPATILLA A', sku=8400001, stock=3)
        data = self._crear_toma_endpoint()
        self.assertTrue(data['success'], data.get('error'))
        toma = TomaInventario.objects.get(id=data['inventario_id'])
        self.assertEqual(toma.empresa_id, self.sucursal.empresa_id)
        self.assertEqual(toma.empresa_id, self.empresa.id)

    def test_listado_fecha_corte_en_hora_local(self):
        self._producto('ZAPATILLA A', sku=8400010, stock=3)
        corte = timezone.make_aware(datetime(2026, 1, 15, 21, 27))
        data = self._crear_toma_endpoint(fecha_corte=corte)
        self.assertTrue(data['success'], data.get('error'))
        resp = self.client.get(reverse('api_obtener_inventarios')).json()
        self.assertEqual(resp['inventarios'][0]['fecha_corte'], '15/01/2026 21:27')

    def test_no_se_cancela_con_ajustes_aplicados(self):
        _, pt1 = self._producto('ZAPATILLA A', sku=8400020, stock=5)
        _, pt2 = self._producto('ZAPATILLA B', sku=8400021, stock=5)
        toma = self._toma(estado='APROBADO')
        self._detalle(toma, pt1, stock_sistema=5, stock_fisico=6, ajuste_aplicado=True)
        self._detalle(toma, pt2, stock_sistema=5, stock_fisico=6)
        resp = self._post('api_cancelar_inventario', toma.id, {'motivo': 'prueba'})
        self.assertFalse(resp['success'])
        self.assertIn('ya aplicó 1 ajuste', resp['error'])
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'APROBADO')

        # Sin ajustes aplicados sí se cancela
        toma2 = self._toma(estado='EN_CONTEO')
        resp = self._post('api_cancelar_inventario', toma2.id, {'motivo': 'toma basura'})
        self.assertTrue(resp['success'])


# ---------------------------------------------------------------------------
# N4 / N5 — validación de cantidades y SKU
# ---------------------------------------------------------------------------
class ValidacionConteoTest(BaseCorrecciones):

    def test_conteo_negativo_rechazado(self):
        _, pt = self._producto('ZAPATILLA A', sku=8500001, stock=4)
        toma = self._toma(estado='EN_CONTEO')
        d = self._detalle(toma, pt, stock_sistema=4, contado=False)

        resp = self._post('api_registrar_conteo', toma.id, {'conteos': [{'detalle_id': d.id, 'stock_fisico': -5}]})
        self.assertTrue(resp['success'])
        self.assertEqual(resp['conteos_realizados'], 0)
        self.assertIn('negativa', resp['errores'][0])
        d.refresh_from_db()
        self.assertFalse(d.contado)

        resultado = self._importar_pistola(toma.id, 'sku;cantidad\n8500001;-3\n')
        self.assertFalse(resultado['success'])  # única fila y es inválida
        d.refresh_from_db()
        self.assertFalse(d.contado)

        # Reconteo negativo
        d.stock_fisico = 0; d.contado = True; d.fecha_conteo = timezone.now(); d.save()
        self.assertTrue(d.reconteo_requerido)
        resp = self._post('api_registrar_reconteo', toma.id, {'reconteos': [{'detalle_id': d.id, 'stock_reconteo': -1}]})
        self.assertEqual(resp['reconteos_realizados'], 0)
        d.refresh_from_db()
        self.assertTrue(d.reconteo_requerido)

    def test_sku_con_decimal_en_csv_casa(self):
        _, pt = self._producto('ZAPATILLA A', sku=4805622, stock=4)
        toma = self._toma(estado='EN_CONTEO')
        d = self._detalle(toma, pt, stock_sistema=4, contado=False)
        resultado = self._importar_pistola(toma.id, 'sku,cantidad\n4805622.0,4\n')
        self.assertEqual(resultado['actualizados'], 1)
        self.assertEqual(resultado['no_encontrados'], [])
        d.refresh_from_db()
        self.assertEqual(d.stock_fisico, 4)
        self.assertEqual(_normalizar_sku(' 4805622,0 '), '4805622')
        self.assertEqual(_normalizar_sku('ABC-1'), 'ABC-1')


# ---------------------------------------------------------------------------
# H2 — detección de columnas
# ---------------------------------------------------------------------------
class DeteccionColumnasTest(BaseCorrecciones):

    ENCABEZADO_TIENDA = ['Sucursal', 'SKU', 'Artículo', 'Talla', 'Stock sistema', 'Diferencia', 'Físico contado']

    def _toma_con(self, sku, stock):
        _, pt = self._producto('ZAPATILLA A', sku=sku, stock=stock)
        toma = self._toma(estado='EN_CONTEO')
        d = self._detalle(toma, pt, stock_sistema=stock, contado=False)
        return toma, d

    def test_excel_tienda_sku_sistema_fisico_toma_columna_fisico(self):
        toma, d = self._toma_con(4827948, 4)
        archivo = self._xlsx([self.ENCABEZADO_TIENDA, ['NICK1', 4827948, 'ZAPATILLA A', '40', 4, 1, 5]])
        resultado = self._importar_archivo(toma.id, archivo)
        self.assertTrue(resultado['success'], resultado)
        self.assertEqual(resultado['actualizados'], 1)
        d.refresh_from_db()
        self.assertEqual(d.stock_fisico, 5)   # NO 4 (Stock sistema)
        self.assertEqual(d.diferencia, 1)

    def test_detector_prioriza_fisico_y_descarta_sistema(self):
        sku_idx, cant_idx, con_enc, enc, error = _detectar_indices_conteo([self.ENCABEZADO_TIENDA])
        self.assertIsNone(error)
        self.assertEqual((sku_idx, cant_idx, con_enc), (1, 6, True))
        # Solo «Stock sistema» y nada más → error, no adivina
        _, cant_idx, _, _, error = _detectar_indices_conteo([['SKU', 'Artículo', 'Stock sistema']])
        self.assertIsNone(cant_idx)
        self.assertIn('No se identificó', error)
        # Sin encabezado: sku,cantidad
        self.assertEqual(_detectar_indices_conteo([['4827948', '3']])[:3], (0, 1, False))
        # 'sku,cantidad' clásico sigue funcionando
        self.assertEqual(_detectar_indices_conteo([['sku', 'cantidad']])[:2], (0, 1))
        self.assertEqual(_detectar_indices_conteo([['SKU', 'CANTIDAD']])[:2], (0, 1))

    def test_encabezado_ambiguo_devuelve_error(self):
        toma, d = self._toma_con(4827949, 4)
        archivo = self._xlsx([['SKU', 'Conteo zona 1', 'Conteo zona 2'], [4827949, 2, 3]])
        resultado = self._importar_archivo(toma.id, archivo)
        self.assertFalse(resultado['success'])
        self.assertIn('Varias columnas', resultado['error'])
        self.assertEqual(resultado['encabezados'], ['SKU', 'Conteo zona 1', 'Conteo zona 2'])
        d.refresh_from_db()
        self.assertFalse(d.contado)
        toma.refresh_from_db()
        self.assertEqual(toma.estado, 'EN_CONTEO')

    def test_mapeo_explicito_de_columnas(self):
        toma, d = self._toma_con(4827950, 4)
        archivo = self._xlsx([['SKU', 'Conteo zona 1', 'Conteo zona 2'], [4827950, 2, 3]])
        resultado = self._importar_archivo(toma.id, archivo, sku_col='0', cantidad_col='Conteo zona 2')
        self.assertTrue(resultado['success'], resultado)
        d.refresh_from_db()
        self.assertEqual(d.stock_fisico, 3)

    def test_preview_devuelve_columnas_y_advierte_si_todo_coincide(self):
        skus = [4827960 + i for i in range(6)]
        toma = self._toma(estado='EN_CONTEO')
        for s in skus:
            _, pt = self._producto(f'ZAP {s}', sku=s, stock=4)
            self._detalle(toma, pt, stock_sistema=4, contado=False)
        filas = [['SKU', 'Stock', 'Físico contado']] + [[s, 4, 4] for s in skus]
        resp = self._preview_archivo(toma.id, self._xlsx(filas))
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['columnas']['sku_col'], 0)
        self.assertEqual(resp['columnas']['cantidad_col'], 2)
        self.assertEqual(resp['columnas']['encabezados'], ['SKU', 'Stock', 'Físico contado'])
        self.assertEqual(resp['en_toma'], 6)
        self.assertIsNotNone(resp['advertencia'])
        self.assertTrue(all(p['en_toma'] for p in resp['preview']))
        # Forzando la columna «Stock» el preview también la respeta
        resp = self._preview_archivo(toma.id, self._xlsx(filas), cantidad_col='1')
        self.assertEqual(resp['columnas']['cantidad_col'], 1)

    def test_preview_exige_pertenencia(self):
        otra_empresa = crear_empresa(nombre='Otra')
        otra_sucursal = crear_sucursal(otra_empresa, alias='OTRA')
        toma = TomaInventario.objects.create(
            numero_inventario='INV-Y-1', nombre='Ajena', sucursal=otra_sucursal, empresa=otra_empresa,
            tipo_inventario='COMPLETO', fecha_corte=timezone.now(), estado='EN_CONTEO',
        )
        vendedor = crear_usuario(username='vendedor2', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        otorgar_ver_pantalla('vendedor', 'gestion_inventarios', puede_editar=True)
        self.client.force_login(vendedor)
        resp = self.client.post(
            reverse('api_preview_conteo_pistola', args=[toma.id]),
            {'archivo': self._xlsx([['sku', 'cantidad'], [1, 1]])},
        )
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# H1 / N1 — fechas de corte y de conteo
# ---------------------------------------------------------------------------
class FechasCorteYConteoTest(BaseCorrecciones):

    def test_corte_futuro_se_recorta_a_ahora(self):
        _, pt = self._producto('ZAPATILLA A', sku=8600001, stock=4)
        futuro = timezone.localtime() + timedelta(hours=3)   # lo que mandaba el modal en UTC
        data = self._crear_toma_endpoint(fecha_corte=futuro)
        self.assertTrue(data['success'], data.get('error'))
        self.assertTrue(data['corte_recortado'])
        toma = TomaInventario.objects.get(id=data['inventario_id'])
        self.assertLessEqual(toma.fecha_corte, timezone.now())
        self.assertGreater(toma.fecha_corte, timezone.now() - timedelta(minutes=5))
        log = toma.logs.get(tipo_accion='CREACION')
        self.assertTrue(log.datos_adicionales['corte_recortado'])

        # Una venta después de crear la toma sí entra al post-corte (el kardex
        # guarda fecha/hora a resolución de segundo: se espera un segundo)
        time.sleep(1.1)
        self._mov(pt, -1, timezone.localtime())
        resp = self._post('api_registrar_conteo', toma.id, {
            'conteos': [{'detalle_id': toma.detalles.get().id, 'stock_fisico': 3}]
        })
        self.assertTrue(resp['success'], resp)
        d = toma.detalles.get()
        self.assertEqual(d.stock_movimientos_post_corte, -1)
        self.assertEqual(d.diferencia, 0)

    def test_registrar_con_fecha_conteo_explicita(self):
        ahora = timezone.localtime()
        corte = ahora - timedelta(hours=3)
        _, pt = self._producto('ZAPATILLA A', sku=8600010, stock=8)
        self._mov(pt, -2, ahora - timedelta(hours=1))   # venta esta mañana
        toma = self._toma(estado='EN_CONTEO', fecha_corte=corte)
        d = self._detalle(toma, pt, stock_sistema=10, contado=False)

        # Contado anoche (a las corte+10min), cargado ahora
        fecha_conteo = (corte + timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M')
        resp = self._post('api_registrar_conteo', toma.id, {
            'conteos': [{'detalle_id': d.id, 'stock_fisico': 10}], 'fecha_conteo': fecha_conteo,
        })
        self.assertTrue(resp['success'], resp)
        d.refresh_from_db()
        self.assertEqual(d.stock_movimientos_post_corte, 0)
        self.assertEqual(d.diferencia, 0)

    def test_escaner_en_vivo_usa_now(self):
        ahora = timezone.localtime()
        corte = ahora - timedelta(hours=3)
        _, pt = self._producto('ZAPATILLA A', sku=8600020, stock=8)
        self._mov(pt, -2, ahora - timedelta(hours=1))
        toma = self._toma(estado='EN_CONTEO', fecha_corte=corte)
        d = self._detalle(toma, pt, stock_sistema=10, contado=False)
        resp = self._post('api_registrar_conteo', toma.id, {'conteos': [{'detalle_id': d.id, 'stock_fisico': 8}]})
        self.assertTrue(resp['success'], resp)
        d.refresh_from_db()
        self.assertEqual(d.stock_movimientos_post_corte, -2)
        self.assertEqual(d.stock_sistema_ajustado, 8)
        self.assertEqual(d.diferencia, 0)

    def test_fecha_conteo_fuera_de_rango_rechazada(self):
        corte = timezone.localtime() - timedelta(hours=3)
        _, pt = self._producto('ZAPATILLA A', sku=8600030, stock=8)
        toma = self._toma(estado='EN_CONTEO', fecha_corte=corte)
        d = self._detalle(toma, pt, stock_sistema=8, contado=False)
        antes = (corte - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        resp = self._post('api_registrar_conteo', toma.id, {
            'conteos': [{'detalle_id': d.id, 'stock_fisico': 8}], 'fecha_conteo': antes,
        })
        self.assertFalse(resp['success'])
        self.assertIn('anterior a la fecha de corte', resp['error'])
        futuro = (timezone.localtime() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        resp = self._post('api_registrar_conteo', toma.id, {
            'conteos': [{'detalle_id': d.id, 'stock_fisico': 8}], 'fecha_conteo': futuro,
        })
        self.assertFalse(resp['success'])
        self.assertIn('futura', resp['error'])
        d.refresh_from_db()
        self.assertFalse(d.contado)


# ---------------------------------------------------------------------------
# H3 — no contados en bloque
# ---------------------------------------------------------------------------
class NoContadosEnBloqueTest(BaseCorrecciones):

    def _request(self, payload):
        rf = RequestFactory()
        request = rf.post('/x/', data=json.dumps(payload), content_type='application/json')
        request.user = self.user
        request.session = self.client.session
        return request

    def _toma_parcial(self):
        _, pt1 = self._producto('ZAPATILLA A', sku=8700001, stock=3)
        _, pt2 = self._producto('ZAPATILLA B', sku=8700002, stock=0)
        _, pt3 = self._producto('ZAPATILLA C', sku=8700003, stock=5)
        toma = self._toma(estado='EN_CONTEO')
        d1 = self._detalle(toma, pt1, stock_sistema=3, contado=False)
        d2 = self._detalle(toma, pt2, stock_sistema=0, contado=False)
        d3 = self._detalle(toma, pt3, stock_sistema=5, stock_fisico=5)
        # las líneas sin contar tienen costo 10.000 → 3 u. = $30.000
        return toma, d1, d2, d3, pt1

    def test_previsualizar_no_escribe(self):
        toma, d1, d2, d3, _ = self._toma_parcial()
        resp = json.loads(resolver_no_contados(
            self._request({'accion': 'excluir', 'previsualizar': True}), toma.id
        ).content)
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['resultado']['lineas'], 2)
        self.assertEqual(resp['resultado']['unidades'], 3)
        self.assertEqual(resp['resultado']['valor_costo'], 30000.0)
        d1.refresh_from_db()
        self.assertFalse(d1.excluir_de_analisis)

    def test_excluir_no_contados_en_bloque(self):
        toma, d1, d2, d3, _ = self._toma_parcial()
        resp = json.loads(resolver_no_contados(
            self._request({'accion': 'excluir', 'solo_stock_cero': True}), toma.id
        ).content)
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['resultado']['lineas'], 1)
        d1.refresh_from_db(); d2.refresh_from_db()
        self.assertFalse(d1.excluir_de_analisis)
        self.assertTrue(d2.excluir_de_analisis)

        resp = json.loads(resolver_no_contados(self._request({'accion': 'excluir'}), toma.id).content)
        self.assertEqual(resp['resultado']['lineas'], 1)
        d1.refresh_from_db()
        self.assertTrue(d1.excluir_de_analisis)
        # Ahora finaliza
        resp = self._post('api_finalizar_conteo', toma.id)
        self.assertTrue(resp['success'], resp)

    def test_sin_diferencia_marca_contado_igual_al_sistema(self):
        toma, d1, d2, d3, pt1 = self._toma_parcial()
        # Venta después del corte: el «igual al sistema» debe ser 3-1 = 2
        time.sleep(1.1)
        self._mov(pt1, -1, timezone.localtime())
        resp = json.loads(resolver_no_contados(self._request({'accion': 'sin_diferencia'}), toma.id).content)
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['resultado']['lineas'], 2)
        d1.refresh_from_db(); d2.refresh_from_db()
        self.assertTrue(d1.contado and d2.contado)
        self.assertEqual(d1.stock_movimientos_post_corte, -1)
        self.assertEqual(d1.stock_sistema_ajustado, 2)
        self.assertEqual(d1.stock_fisico, 2)
        self.assertEqual((d1.diferencia, d2.diferencia), (0, 0))
        self.assertFalse(d1.reconteo_requerido)
        toma.refresh_from_db()
        self.assertEqual(toma.total_productos_contados, 3)
        self.assertEqual(float(toma.progreso_conteo), 100.0)

    def test_accion_invalida(self):
        toma, *_ = self._toma_parcial()
        resp = json.loads(resolver_no_contados(self._request({'accion': 'poner_en_cero'}), toma.id).content)
        self.assertFalse(resp['success'])


# ---------------------------------------------------------------------------
# H4 — SKUs fuera de la toma
# ---------------------------------------------------------------------------
class SkuFueraDeTomaTest(BaseCorrecciones):

    def test_escaner_agrega_sku_sin_linea(self):
        _, pt_cero = self._producto('ZAPATILLA AGOTADA', sku=8800001, stock=0)
        _, pt = self._producto('ZAPATILLA VIVA', sku=8800002, stock=5)
        data = self._crear_toma_endpoint()
        toma = TomaInventario.objects.get(id=data['inventario_id'])
        self.assertEqual(toma.detalles.count(), 1)

        resp = self._post('api_registrar_conteo', toma.id, {'conteos': [{'sku': '8800001', 'stock_fisico': 1}]})
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['agregados'], ['8800001'])
        self.assertEqual(resp['conteos_realizados'], 1)
        d = toma.detalles.get(producto_talla=pt_cero)
        self.assertEqual((d.stock_sistema, d.stock_fisico, d.diferencia), (0, 1, 1))
        self.assertTrue(d.contado)

        # SKU inexistente en la sucursal → error por línea
        resp = self._post('api_registrar_conteo', toma.id, {'conteos': [{'sku': '9999999', 'stock_fisico': 1}]})
        self.assertEqual(resp['conteos_realizados'], 0)
        self.assertIn('no existe en esta sucursal', resp['errores'][0])

    def test_sku_de_otra_sucursal_no_se_agrega(self):
        otra_sucursal = crear_sucursal(self.empresa, alias='OTRA')
        producto = Producto.objects.create(
            articulo='AJENA', descripcion='AJENA', sucursal=otra_sucursal, costo=1, sobreprecio=0,
            precioventa=1, categoria=self.categoria,
        )
        Producto_Talla.objects.create(producto=producto, sku=8800010, stock=3, talla='40')
        self._producto('ZAPATILLA VIVA', sku=8800011, stock=5)
        data = self._crear_toma_endpoint()
        resultado = self._importar_pistola(data['inventario_id'], 'sku;cantidad\n8800010;1\n')
        self.assertEqual(resultado['no_encontrados'], ['8800010'])
        self.assertEqual(resultado['agregados'], [])

    def test_sku_duplicado_en_sucursal_es_ambiguo(self):
        _, pt_a = self._producto('ZAPATILLA A', sku=8800020, stock=0)
        _, pt_b = self._producto('ZAPATILLA A bis', sku=8800020, stock=0, talla='41')
        self._producto('ZAPATILLA VIVA', sku=8800021, stock=5)
        data = self._crear_toma_endpoint()
        resultado = self._importar_pistola(data['inventario_id'], 'sku;cantidad\n8800020;1\n')
        self.assertEqual(resultado['ambiguos'], ['8800020'])
        self.assertEqual(resultado['agregados'], [])
        self.assertTrue(any('ambiguo' in e for e in resultado['errores']))
        self.assertEqual(
            TomaInventarioDetalle.objects.filter(toma_inventario_id=data['inventario_id']).count(), 1
        )

    def test_importar_reporta_sobreescritos(self):
        self._producto('ZAPATILLA A', sku=8800030, stock=5)
        data = self._crear_toma_endpoint()
        self._importar_pistola(data['inventario_id'], 'sku;cantidad\n8800030;3\n')
        resultado = self._importar_pistola(data['inventario_id'], 'sku;cantidad\n8800030;4\n')
        self.assertEqual(resultado['sobreescritos'], [{'sku': '8800030', 'anterior': 3, 'nuevo': 4}])


# ---------------------------------------------------------------------------
# H7 — umbral de reconteo
# ---------------------------------------------------------------------------
class UmbralReconteoTest(BaseCorrecciones):

    def test_regla_umbral(self):
        # 1 unidad sobre base 1-4: lo habitual, sin reconteo
        for base in (1, 2, 3, 4):
            self.assertFalse(requiere_reconteo(1, base), base)
            self.assertFalse(requiere_reconteo(-1, base), base)
        # 2 unidades: reconteo solo con base <= 2 (toda la línea), no con base 3-4
        self.assertTrue(requiere_reconteo(-2, 2))
        self.assertTrue(requiere_reconteo(2, 1))
        self.assertTrue(requiere_reconteo(2, 0))
        self.assertFalse(requiere_reconteo(2, 3))
        self.assertFalse(requiere_reconteo(-2, 4))
        # 3 o más: siempre
        self.assertTrue(requiere_reconteo(3, 10))
        self.assertTrue(requiere_reconteo(-3, 100))
        # Porcentaje solo desde base 20: 2 sobre 15 (13 %) no; 3 sobre 20 (15 %) sí; 2 sobre 25 (8 %) no
        self.assertFalse(requiere_reconteo(2, 15))
        self.assertTrue(requiere_reconteo(3, 20))
        self.assertFalse(requiere_reconteo(2, 25))
        self.assertFalse(requiere_reconteo(0, 5))

    def test_umbral_no_exige_reconteo_por_1_unidad_en_base_2(self):
        _, pt = self._producto('ZAPATILLA A', sku=8900001, stock=2)
        toma = self._toma(estado='EN_CONTEO')
        d = self._detalle(toma, pt, stock_sistema=2, stock_fisico=3)
        self.assertEqual(d.diferencia, 1)
        self.assertFalse(d.reconteo_requerido)
        d2 = self._detalle(toma, self._producto('ZAPATILLA B', sku=8900002, stock=4)[1], stock_sistema=4, stock_fisico=3)
        self.assertFalse(d2.reconteo_requerido)
        d3 = self._detalle(toma, self._producto('ZAPATILLA C', sku=8900003, stock=2)[1], stock_sistema=2, stock_fisico=0)
        self.assertTrue(d3.reconteo_requerido)
        # Flujo completo sin reconteos: finalizar → CONTEO_FINALIZADO
        d3.excluir_de_analisis = True
        d3.save()
        resp = self._post('api_finalizar_conteo', toma.id)
        self.assertEqual(resp['estado'], 'CONTEO_FINALIZADO')
