"""
Tests del módulo Gestión de Inventarios (toma física) — /app/gestion-inventarios/

Cubre las preguntas operativas reales:
- Segmentación: ¿puedo tomar inventario solo de ZAPATILLA (categoría) o de una marca?
- Corte retroactivo: conteo físico tomado ANOCHE, toma creada HOY.
- Carga de pistola diferida: el archivo se sube HOY con ventas de la mañana en medio.
- Movimientos posteriores al conteo ya registrado (no deben alterar la diferencia).
- Aplicación de ajustes: sobrante crea lote FIFO + kardex, faltante no deja negativo.

Correr con settings desechables (NO toca la BD de producción del .env):
    python manage.py test app.tests.test_toma_inventario --settings=test_settings_sqlite
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Categoria, Movimientos_Producto, LoteProducto, Producto, Producto_Talla,
    Productos_Atributos, AtributoOpcion,
    TomaInventario, TomaInventarioDetalle,
    ModuloSistema, OpcionMenu, PermisoRol,
)
from app.views_gestion_inventarios import (
    _aplicar_ajuste_individual,
    _obtener_movimientos_desde_corte_batch,
)
from .factories import crear_empresa, crear_sucursal, crear_usuario, crear_empresa_user


class BaseTomaInventarioTest(TestCase):
    """Entorno común: usuario administrador con permiso 'gestion_inventarios'."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.user = crear_usuario(rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.categoria = Categoria.objects.create(nombre='ZAPATILLA')

        modulo = ModuloSistema.objects.create(codigo='existencias', nombre='Existencias')
        opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='gestion_inventarios', nombre='Gestión de Inventarios'
        )
        PermisoRol.objects.create(
            rol='administrador', opcion_menu=opcion,
            puede_ver=True, puede_crear=True, puede_editar=True,
            puede_eliminar=True, puede_exportar=True, puede_aprobar=True,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    # ---------- helpers ----------

    def _producto(self, articulo, sku, stock, categoria=None, marca=None,
                  talla='40', costo=10000, precio=19990):
        producto = Producto.objects.create(
            articulo=articulo,
            descripcion=articulo,
            sucursal=self.sucursal,
            costo=costo,
            sobreprecio=0,
            precioventa=precio,
            categoria=categoria or self.categoria,
            atributo1=marca,
        )
        pt = Producto_Talla.objects.create(producto=producto, sku=sku, stock=stock, talla=talla)
        return producto, pt

    def _crear_toma_endpoint(self, tipo='COMPLETO', fecha_corte=None, filtros=None,
                             nombre='Toma test'):
        payload = {'nombre': nombre, 'tipo_inventario': tipo, 'filtros': filtros or {}}
        if fecha_corte is not None:
            payload['fecha_corte'] = fecha_corte.strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(
            reverse('api_crear_inventario'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        return resp.json()

    def _importar_pistola(self, toma_id, contenido, nombre='pistola.csv'):
        archivo = SimpleUploadedFile(nombre, contenido.encode('utf-8'), content_type='text/csv')
        resp = self.client.post(
            reverse('api_importar_conteo_pistola', args=[toma_id]),
            {'archivo': archivo},
        )
        return resp.json()

    def _mov(self, pt, cantidad, momento_local, concepto='VENTA_PUBLICO'):
        return Movimientos_Producto.objects.create(
            ProductoTalla=pt,
            cantidad=cantidad,
            concepto=concepto,
            sucursal_origen=self.sucursal,
            sucursal_destino=self.sucursal,
            responsable='Test',
            fecha=momento_local.date(),
            hora=momento_local.time(),
        )


class SegmentacionTest(BaseTomaInventarioTest):
    """¿Puedo hacer un inventario SOLO de zapatillas o de una categoría específica?"""

    def test_por_categoria_solo_incluye_esa_categoria(self):
        cat_botin = Categoria.objects.create(nombre='BOTIN')
        _, pt_zap1 = self._producto('ZAPATILLA RUN', sku=3000001, stock=5)
        _, pt_zap2 = self._producto('ZAPATILLA URBANA', sku=3000002, stock=3)
        self._producto('BOTIN CUERO', sku=3000003, stock=4, categoria=cat_botin)

        data = self._crear_toma_endpoint(
            tipo='POR_CATEGORIA',
            filtros={'categorias': [self.categoria.id], 'solo_con_stock': True},
        )
        self.assertTrue(data['success'], data.get('error'))
        self.assertEqual(data['total_productos'], 2)

        detalles = TomaInventarioDetalle.objects.filter(toma_inventario_id=data['inventario_id'])
        self.assertEqual(
            {d.sku for d in detalles}, {str(pt_zap1.sku), str(pt_zap2.sku)}
        )
        self.assertTrue(all(d.categoria_nombre == 'ZAPATILLA' for d in detalles))

    def test_por_categoria_sin_seleccion_es_rechazado(self):
        self._producto('ZAPATILLA RUN', sku=3000010, stock=5)
        data = self._crear_toma_endpoint(tipo='POR_CATEGORIA', filtros={})
        self.assertFalse(data['success'])
        self.assertIn('segmentación', data['error'])

    def test_por_marca_filtra_por_atributo1(self):
        attr_marca = Productos_Atributos.objects.create(nombre='Marca')
        nike = AtributoOpcion.objects.create(atributo=attr_marca, valor='NIKE')
        adidas = AtributoOpcion.objects.create(atributo=attr_marca, valor='ADIDAS')
        _, pt_nike = self._producto('ZAPATILLA NIKE', sku=3000020, stock=5, marca=nike)
        self._producto('ZAPATILLA ADIDAS', sku=3000021, stock=5, marca=adidas)

        data = self._crear_toma_endpoint(tipo='POR_MARCA', filtros={'marcas': [nike.id]})
        self.assertTrue(data['success'], data.get('error'))
        self.assertEqual(data['total_productos'], 1)
        detalle = TomaInventarioDetalle.objects.get(toma_inventario_id=data['inventario_id'])
        self.assertEqual(detalle.sku, str(pt_nike.sku))
        self.assertEqual(detalle.marca_nombre, 'NIKE')

    def test_tipos_no_implementados_son_rechazados(self):
        self._producto('ZAPATILLA RUN', sku=3000030, stock=5)
        for tipo in ('SELECTIVO', 'CICLICO', 'ALEATORIO'):
            data = self._crear_toma_endpoint(tipo=tipo)
            self.assertFalse(data['success'], f'{tipo} debería rechazarse')
            self.assertIn('no está implementado', data['error'])

    def test_filtros_sin_resultados_no_crean_toma_vacia(self):
        cat_vacia = Categoria.objects.create(nombre='PANTUFLA')
        self._producto('ZAPATILLA RUN', sku=3000040, stock=5)
        data = self._crear_toma_endpoint(
            tipo='POR_CATEGORIA', filtros={'categorias': [cat_vacia.id]}
        )
        self.assertFalse(data['success'])
        self.assertEqual(TomaInventario.objects.count(), 0)

    def test_solo_con_stock_deja_fuera_stock_cero(self):
        """Default solo_con_stock=True: un SKU en 0 no entra a la toma, y si la
        pistola lo escanea (sobrante físico real) vuelve como no_encontrado."""
        _, pt_cero = self._producto('ZAPATILLA AGOTADA', sku=5000001, stock=0)
        self._producto('ZAPATILLA VIVA', sku=5000002, stock=5)

        data = self._crear_toma_endpoint()
        self.assertTrue(data['success'], data.get('error'))
        self.assertEqual(data['total_productos'], 1)

        resultado = self._importar_pistola(
            data['inventario_id'], 'sku;cantidad\n5000001;2\n'
        )
        self.assertEqual(resultado['no_encontrados'], [str(pt_cero.sku)])
        self.assertEqual(resultado['actualizados'], 0)

    def test_solo_con_stock_false_incluye_stock_cero(self):
        self._producto('ZAPATILLA AGOTADA', sku=5000010, stock=0)
        self._producto('ZAPATILLA VIVA', sku=5000011, stock=5)
        data = self._crear_toma_endpoint(filtros={'solo_con_stock': False})
        self.assertTrue(data['success'], data.get('error'))
        self.assertEqual(data['total_productos'], 2)


class CorteRetroactivoTest(BaseTomaInventarioTest):
    """Inventario físico tomado ANOCHE: la fecha de corte reconstruye ese stock."""

    def test_corte_retroactivo_reconstruye_stock_de_anoche(self):
        ahora = timezone.localtime()
        corte = ahora - timedelta(hours=3)      # "anoche" al cerrar
        venta = ahora - timedelta(hours=1)      # venta de esta mañana

        # Hoy el sistema tiene 8, pero anoche (antes de la venta de 2) tenía 10
        _, pt = self._producto('ZAPATILLA RUN', sku=2000001, stock=8)
        self._mov(pt, -2, venta)

        data = self._crear_toma_endpoint(fecha_corte=corte)
        self.assertTrue(data['success'], data.get('error'))
        detalle = TomaInventarioDetalle.objects.get(toma_inventario_id=data['inventario_id'])
        self.assertEqual(detalle.stock_sistema, 10)

    def test_movimiento_exactamente_en_el_corte_no_es_posterior(self):
        corte = timezone.make_aware(datetime(2026, 8, 12, 20, 0, 0))
        _, pt = self._producto('ZAPATILLA RUN', sku=2000010, stock=5)
        limite = corte  # 20:00:00 exacto → pertenece al período ANTES del corte
        despues = corte + timedelta(seconds=1)
        self._mov(pt, -1, timezone.localtime(limite))
        self._mov(pt, -1, timezone.localtime(despues))

        resultado = _obtener_movimientos_desde_corte_batch([pt.id], corte, self.sucursal.id)
        self.assertEqual(resultado.get(pt.id), -1)


class CargaPistolaDiferidaTest(BaseTomaInventarioTest):
    """Conteo con pistola anoche, archivo cargado HOY: ¿cómo toma los movimientos?"""

    def test_carga_antes_de_abrir_la_tienda_no_genera_diferencias(self):
        """Camino correcto: corte anoche + carga antes de cualquier venta → diff 0."""
        ahora = timezone.localtime()
        corte = ahora - timedelta(hours=3)
        _, pt = self._producto('ZAPATILLA RUN', sku=2100001, stock=10)

        data = self._crear_toma_endpoint(fecha_corte=corte)
        resultado = self._importar_pistola(data['inventario_id'], 'sku;cantidad\n2100001;10\n')
        self.assertEqual(resultado['actualizados'], 1)

        detalle = TomaInventarioDetalle.objects.get(toma_inventario_id=data['inventario_id'])
        self.assertEqual(detalle.stock_sistema, 10)
        self.assertEqual(detalle.stock_movimientos_post_corte, 0)
        self.assertEqual(detalle.stock_sistema_ajustado, 10)
        self.assertEqual(detalle.diferencia, 0)

    def test_carga_despues_de_venta_matutina_genera_falso_sobrante(self):
        """GAP DOCUMENTADO: la fecha de conteo es el momento de la CARGA, no del
        conteo físico. Si la tienda vendió antes de subir el archivo, la venta de
        la mañana aparece como sobrante (+2) aunque anoche todo cuadraba.

        Este test fija el comportamiento ACTUAL; si se implementa el campo
        "fecha real del conteo" (plan), debe actualizarse para esperar diff 0.
        """
        ahora = timezone.localtime()
        corte = ahora - timedelta(hours=3)      # anoche: se contaron 10 físicas
        venta = ahora - timedelta(hours=1)      # esta mañana se vendieron 2

        _, pt = self._producto('ZAPATILLA RUN', sku=2100010, stock=8)
        self._mov(pt, -2, venta)

        data = self._crear_toma_endpoint(fecha_corte=corte)
        detalle = TomaInventarioDetalle.objects.get(toma_inventario_id=data['inventario_id'])
        self.assertEqual(detalle.stock_sistema, 10)  # snapshot de anoche: correcto

        # Se sube HOY el archivo con lo contado ANOCHE (10 unidades)
        self._importar_pistola(data['inventario_id'], 'sku;cantidad\n2100010;10\n')
        detalle.refresh_from_db()

        self.assertEqual(detalle.stock_movimientos_post_corte, -2)
        self.assertEqual(detalle.stock_sistema_ajustado, 8)
        # Falso sobrante: si se aprueba y aplica, el sistema sumaría 2 unidades
        # que en realidad se vendieron esta mañana.
        self.assertEqual(detalle.diferencia, 2)

    def test_venta_posterior_al_conteo_registrado_no_altera_la_diferencia(self):
        """Una vez REGISTRADO el conteo, las ventas siguientes mueven por igual
        el stock físico y el del sistema: la diferencia guardada sigue válida y
        el ajuste aplicado después deja el stock correcto."""
        ahora = timezone.localtime()
        corte = ahora - timedelta(hours=3)
        _, pt = self._producto('ZAPATILLA RUN', sku=2100020, stock=10)

        data = self._crear_toma_endpoint(fecha_corte=corte)
        toma = TomaInventario.objects.get(id=data['inventario_id'])

        # Se cuentan 9 (falta 1 real)
        self._importar_pistola(toma.id, 'sku;cantidad\n2100020;9\n')
        detalle = toma.detalles.get()
        self.assertEqual(detalle.diferencia, -1)

        # DESPUÉS del conteo se venden 2 (el POS las descuenta normalmente)
        self._mov(pt, -2, timezone.localtime())
        pt.refresh_from_db()
        pt.stock = 8
        pt.save()

        detalle.refresh_from_db()
        self.assertEqual(detalle.diferencia, -1)  # sin cambios

        _aplicar_ajuste_individual(detalle, toma, self.user)
        pt.refresh_from_db()
        # Físico real: 9 contadas - 2 vendidas = 7. Sistema: 8 - 1 de ajuste = 7 ✔
        self.assertEqual(pt.stock, 7)
        mov = Movimientos_Producto.objects.filter(
            ProductoTalla=pt, concepto='AJUSTE_INVENTARIO_SALIDA'
        ).get()
        self.assertEqual(mov.cantidad, -1)
        self.assertEqual(mov.referencia_externa, toma.numero_inventario)


class ArchivoPistolaTest(BaseTomaInventarioTest):
    """Mecánica del archivo: acumulación, re-importación, SKUs desconocidos, estados."""

    def test_filas_duplicadas_en_un_archivo_se_suman(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=6000001, stock=10)
        data = self._crear_toma_endpoint()
        self._importar_pistola(data['inventario_id'], 'sku;cantidad\n6000001;3\n6000001;2\n')
        detalle = TomaInventarioDetalle.objects.get(toma_inventario_id=data['inventario_id'])
        self.assertEqual(detalle.stock_fisico, 5)

    def test_reimportar_reemplaza_el_conteo_no_lo_suma(self):
        """GOTCHA operativo: dos archivos del mismo SKU (p.ej. bodega y sala en
        archivos separados) NO se suman — el segundo pisa al primero."""
        self._producto('ZAPATILLA RUN', sku=6000010, stock=10)
        data = self._crear_toma_endpoint()
        self._importar_pistola(data['inventario_id'], 'sku;cantidad\n6000010;3\n')
        self._importar_pistola(data['inventario_id'], 'sku;cantidad\n6000010;4\n')
        detalle = TomaInventarioDetalle.objects.get(toma_inventario_id=data['inventario_id'])
        self.assertEqual(detalle.stock_fisico, 4)

    def test_sku_desconocido_se_reporta_como_no_encontrado(self):
        self._producto('ZAPATILLA RUN', sku=6000020, stock=10)
        data = self._crear_toma_endpoint()
        resultado = self._importar_pistola(
            data['inventario_id'], 'sku;cantidad\n6000020;10\n9999999;1\n'
        )
        self.assertEqual(resultado['actualizados'], 1)
        self.assertEqual(resultado['no_encontrados'], ['9999999'])

    def test_importar_bloqueado_fuera_de_conteo(self):
        self._producto('ZAPATILLA RUN', sku=6000030, stock=10)
        data = self._crear_toma_endpoint()
        toma = TomaInventario.objects.get(id=data['inventario_id'])
        toma.estado = 'PENDIENTE_APROBACION'
        toma.save()
        resultado = self._importar_pistola(toma.id, 'sku;cantidad\n6000030;10\n')
        self.assertFalse(resultado['success'])
        self.assertIn('ya no admite conteos', resultado['error'])


class AprobacionYAjustesTest(BaseTomaInventarioTest):
    """Cierre del ciclo: aprobar solo con todo contado; ajustes tocan stock+kardex+FIFO."""

    def _toma_manual(self, estado='BORRADOR'):
        return TomaInventario.objects.create(
            numero_inventario=TomaInventario.generar_numero_inventario(self.sucursal),
            nombre='Toma manual',
            sucursal=self.sucursal,
            empresa=self.empresa,
            tipo_inventario='COMPLETO',
            fecha_corte=timezone.now(),
            estado=estado,
            creado_por=self.user,
        )

    def _detalle(self, toma, pt, stock_sistema, stock_fisico=None, contado=True):
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
        )
        if contado:
            detalle.stock_fisico = stock_fisico
            detalle.fecha_conteo = timezone.now()
        detalle.save()
        return detalle

    def test_no_se_aprueba_con_lineas_sin_contar(self):
        _, pt1 = self._producto('ZAPATILLA A', sku=7000001, stock=5)
        _, pt2 = self._producto('ZAPATILLA B', sku=7000002, stock=5)
        toma = self._toma_manual(estado='PENDIENTE_APROBACION')
        self._detalle(toma, pt1, stock_sistema=5, stock_fisico=5)
        self._detalle(toma, pt2, stock_sistema=5, contado=False)

        resp = self.client.post(
            reverse('api_aprobar_inventario', args=[toma.id]),
            data=json.dumps({}), content_type='application/json',
        ).json()
        self.assertFalse(resp['success'])
        self.assertIn('sin contar', resp['error'])

    def test_sobrante_crea_lote_fifo_y_movimiento_trazable(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=7000010, stock=10)
        toma = self._toma_manual(estado='APROBADO')
        detalle = self._detalle(toma, pt, stock_sistema=10, stock_fisico=13)
        self.assertEqual(detalle.diferencia, 3)

        _aplicar_ajuste_individual(detalle, toma, self.user)

        pt.refresh_from_db()
        self.assertEqual(pt.stock, 13)
        mov = Movimientos_Producto.objects.filter(
            ProductoTalla=pt, concepto='AJUSTE_INVENTARIO_ENTRADA'
        ).get()
        self.assertEqual(mov.cantidad, 3)
        self.assertEqual(mov.referencia_externa, toma.numero_inventario)
        lote = LoteProducto.objects.get(producto_talla=pt)
        self.assertEqual(lote.cantidad_disponible, 3)
        self.assertEqual(lote.costo_unitario, Decimal('10000'))
        detalle.refresh_from_db()
        self.assertTrue(detalle.ajuste_aplicado)

    def test_faltante_descuenta_stock(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=7000020, stock=10)
        toma = self._toma_manual(estado='APROBADO')
        detalle = self._detalle(toma, pt, stock_sistema=10, stock_fisico=8)
        self.assertEqual(detalle.diferencia, -2)

        _aplicar_ajuste_individual(detalle, toma, self.user)

        pt.refresh_from_db()
        self.assertEqual(pt.stock, 8)
        mov = Movimientos_Producto.objects.filter(
            ProductoTalla=pt, concepto='AJUSTE_INVENTARIO_SALIDA'
        ).get()
        self.assertEqual(mov.cantidad, -2)

    def test_ajuste_no_deja_stock_negativo(self):
        _, pt = self._producto('ZAPATILLA RUN', sku=7000030, stock=1)
        toma = self._toma_manual(estado='APROBADO')
        detalle = self._detalle(toma, pt, stock_sistema=10, stock_fisico=7)
        self.assertEqual(detalle.diferencia, -3)

        with self.assertRaises(ValidationError):
            _aplicar_ajuste_individual(detalle, toma, self.user)

        pt.refresh_from_db()
        self.assertEqual(pt.stock, 1)
        detalle.refresh_from_db()
        self.assertFalse(detalle.ajuste_aplicado)
