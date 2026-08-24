# -*- coding: utf-8 -*-
"""
FASE D de la auditoría de Reportes — cierre de F-06 (código muerto) y guardas
de la redundancia de los reportes de existencias.

Tres cosas se prueban aquí:

1. Las 5 FBV borradas de `app/views_modulo_existencias.py` ya NO existen, sus
   gemelas vivas siguen ruteadas desde `app/views.py`, y ninguna URL del
   proyecto resuelve al módulo huérfano. Si alguien vuelve a pegar una copia
   ahí (o a colgarla de una URL), estos tests fallan.

2. Las gemelas vivas de F-06 no quedaron huérfanas: se las invoca POR SU URL y
   contestan su validación propia (no 404, no 500, y sin escribir nada).

3. Los tres reportes de existencias vivos siguen respondiendo y siguen
   coincidiendo en unidades. Se fija además la ÚNICA divergencia estructural
   entre el legacy y el resumen: el legacy suma las filas con `stock < 0` y el
   resumen las filtra (`stock__gt=0`).

   Los tests de este archivo NO dependen de números de producción: arman su
   propio universo mínimo. Las cifras de prod se citan solo como contexto y
   son una FOTO del 2026-08-22 (la BD sigue vendiendo, así que los absolutos
   se mueven; lo que se mantiene es el delta). Medido ese día: el legacy sin
   filtro reportaba 109.677 u y el resumen 109.684 u, delta −7 u = 5 filas con
   stock negativo; con `estado_stock=con-stock` el legacy daba el mismo número
   que el resumen (delta 0).

QUÉ DISCRIMINA DE VERDAD EL CAMBIO DE ESTA FASE. Se corrió este mismo archivo
contra HEAD b32a3ed8 en un `git worktree` (2026-08-22): allí da 12 fallas
repartidas en estos 4 métodos, y en el árbol de trabajo da 16/16 OK.

  - test_las_cinco_fbv_muertas_ya_no_existen_en_el_modulo
  - test_el_trio_de_traspaso_no_tiene_ruta_ni_definicion_en_ninguna_parte
    (la aserción que discrimina es el escaneo del código fuente: en HEAD el
    trío tiene 3 definiciones en views_modulo_existencias.py, acá 0. Las otras
    dos aserciones —sin ruta, sin gemela— ya eran verdad antes y solas no
    probarían nada)
  - test_las_gemelas_de_f06_siguen_definidas_y_el_modulo_huerfano_no
  - test_el_modulo_ya_no_importa_los_modelos_de_traspaso

`test_no_se_borro_de_mas` NO discrimina (pasa igual en HEAD): es una guarda
hacia adelante, contra un borrado que se pase de largo.

Correr (BD desechable, NUNCA la de producción):

    $env:DATABASE_URL="sqlite:///C:/temp/tr5.sqlite3"; python manage.py test \
        app.tests.test_fase_d_deadcode --settings=test_settings_sqlite
"""
import ast
import json
from pathlib import Path

from django.test import Client, SimpleTestCase, TestCase
from django.urls import NoReverseMatch, get_resolver, resolve, reverse

from app import views as views_vivas
from app import views_modulo_existencias as modulo_huerfano
from app.models import Categoria, ModuloSistema, OpcionMenu, PermisoRol, Producto
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_sucursal,
    crear_usuario,
)

# Las 5 FBV eliminadas el 2026-08-22.
#   - Las dos primeras son F-06: copias muertas cuyas gemelas ruteadas viven en
#     views.py (urls.py:706 y :783).
#   - Las tres de traspaso eran las ÚNICAS definiciones de esos nombres en todo
#     el repo; sus rutas se borraron el 2026-07-28 porque movían stock sin
#     @login_required (comentario en urls.py:758).
FBV_BORRADAS_CON_GEMELA = ('crear_producto_desde_recepcion', 'crear_producto_manual')
FBV_BORRADAS_SIN_GEMELA = ('crear_traspaso', 'aprobar_traspaso', 'recibir_traspaso')

# Lo que SÍ debe seguir en el módulo (guarda contra un borrado de más).
FBV_QUE_SOBREVIVEN = (
    'obtener_productos_base', 'obtener_productos', 'obtener_productos_para_crear',
    'detalle_producto_para_crear', 'buscar_productos_existentes',
    'detalle_producto_para_copiar', 'tallas_producto', 'verMovimientosProducto',
    'obtener_movimientos_producto', 'crear_ajuste_inventario', 'ver_lotes_producto',
    'obtener_lotes_producto', 'crear_lote_manual', 'ajustar_lote',
    'dashboard_productos', 'obtener_datos_dashboard_productos',
    'filtrar_productos_dashboard', 'exportar_dashboard_productos',
    'exportar_productos_filtrado',
)


# Raíz del paquete `app` (…/retailmind/app), para escanear el código fuente.
RAIZ_APP = Path(__file__).resolve().parent.parent

# Se excluyen del escaneo: los propios tests (sus helpers usan otros nombres,
# p. ej. `crear_traspaso_emitido`), las migraciones y los .pyc.
_DIRS_EXCLUIDOS = {'tests', 'migrations', '__pycache__'}


def _modulos_del_paquete_app_que_definen(nombre):
    """Módulos de `app/` con un `def <nombre>` A NIVEL DE MÓDULO.

    Se resuelve por AST y no por grep: un comentario, un string o un método
    homónimo dentro de una clase no cuentan. Devuelve rutas relativas a `app/`.
    """
    aguja = 'def %s' % nombre
    encontrados = []
    for ruta in sorted(RAIZ_APP.rglob('*.py')):
        if _DIRS_EXCLUIDOS & set(ruta.relative_to(RAIZ_APP).parts):
            continue
        texto = ruta.read_text(encoding='utf-8', errors='replace')
        if aguja not in texto:          # prefiltro barato antes de parsear
            continue
        try:
            arbol = ast.parse(texto)
        except SyntaxError:             # pragma: no cover - no debería pasar
            continue
        for nodo in arbol.body:         # SOLO nivel de módulo
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == nombre:
                encontrados.append(ruta.relative_to(RAIZ_APP).as_posix())
                break
    return encontrados


def _modulo_real(callback):
    """Módulo donde vive la función que hay DEBAJO de los decoradores."""
    modulo = getattr(callback, '__module__', '')
    while hasattr(callback, '__wrapped__'):
        callback = callback.__wrapped__
        modulo = getattr(callback, '__module__', modulo)
    return modulo


def _modulos_ruteados():
    """Todos los módulos que aparecen como destino en el URLconf del proyecto."""
    encontrados = []

    def caminar(patrones):
        for patron in patrones:
            if hasattr(patron, 'url_patterns'):
                caminar(patron.url_patterns)
            else:
                encontrados.append((_modulo_real(patron.callback), str(patron.pattern)))

    caminar(get_resolver().url_patterns)
    return encontrados


class CodigoMuertoBorradoTest(SimpleTestCase):
    """F-06: las copias muertas se fueron y no se llevaron nada vivo por delante."""

    def test_las_cinco_fbv_muertas_ya_no_existen_en_el_modulo(self):
        for nombre in FBV_BORRADAS_CON_GEMELA + FBV_BORRADAS_SIN_GEMELA:
            with self.subTest(fbv=nombre):
                self.assertFalse(
                    hasattr(modulo_huerfano, nombre),
                    f'{nombre} volvió a aparecer en views_modulo_existencias.py; '
                    'es una copia muerta, la versión viva está en views.py',
                )

    def test_las_gemelas_vivas_siguen_en_views_py(self):
        for nombre in FBV_BORRADAS_CON_GEMELA:
            with self.subTest(fbv=nombre):
                self.assertTrue(callable(getattr(views_vivas, nombre, None)))

    def test_las_urls_de_las_gemelas_resuelven_a_views_py(self):
        for nombre, ruta in (
            ('crear_producto_desde_recepcion', '/app/crear_producto_desde_recepcion/'),
            ('crear_producto_manual', '/app/crear_producto_manual/'),
        ):
            with self.subTest(fbv=nombre):
                self.assertEqual(reverse(nombre), ruta)
                self.assertEqual(_modulo_real(resolve(ruta).func), 'app.views')

    def test_ninguna_url_del_proyecto_resuelve_al_modulo_huerfano(self):
        """El módulo no está importado por urls.py: 0 rutas deben caer ahí."""
        apuntan = [p for m, p in _modulos_ruteados() if m == 'app.views_modulo_existencias']
        self.assertEqual(apuntan, [], f'Hay rutas colgando del módulo huérfano: {apuntan}')

    def test_el_trio_de_traspaso_no_tiene_ruta_ni_definicion_en_ninguna_parte(self):
        """Se borraron sus rutas el 28-jul; ahora tampoco queda el cuerpo.

        Las dos primeras aserciones (sin ruta, sin gemela) YA eran verdad antes
        de esta fase y por sí solas no probarían nada. La que discrimina es la
        tercera: se escanea el código fuente del paquete `app` buscando la
        DEFINICIÓN. En HEAD (b32a3ed8) el escaneo devuelve
        `views_modulo_existencias.py` para los tres nombres; después del borrado
        debe devolver la lista vacía.
        """
        for nombre in FBV_BORRADAS_SIN_GEMELA:
            with self.subTest(fbv=nombre):
                # (1) sin ruta en el URLconf
                with self.assertRaises(NoReverseMatch):
                    reverse(nombre)
                # (2) sin gemela viva en views.py (nunca la hubo)
                self.assertFalse(hasattr(views_vivas, nombre))
                # (3) sin definición en NINGÚN módulo del paquete `app`
                definidores = _modulos_del_paquete_app_que_definen(nombre)
                self.assertEqual(
                    definidores, [],
                    f'`def {nombre}` reapareció en {definidores}. Movía stock sin '
                    '@login_required: sus rutas se borraron el 2026-07-28 (P0, '
                    'ver comentario en urls.py:758) y el cuerpo el 2026-08-22.',
                )

    def test_las_gemelas_de_f06_siguen_definidas_y_el_modulo_huerfano_no(self):
        """Dónde vive HOY cada gemela de F-06, medido sobre el código fuente.

        Discrimina el cambio: en HEAD, `views_modulo_existencias.py` aparecía en
        la lista de definidores de las dos; ahora no debe aparecer, y la copia
        viva (`views.py`, la que está ruteada) sí.
        """
        for nombre in FBV_BORRADAS_CON_GEMELA:
            with self.subTest(fbv=nombre):
                definidores = _modulos_del_paquete_app_que_definen(nombre)
                self.assertNotIn(
                    'views_modulo_existencias.py', definidores,
                    f'Volvió la copia muerta de {nombre}; la viva está en views.py',
                )
                self.assertIn(
                    'views.py', definidores,
                    f'Se perdió la copia VIVA de {nombre} (la que rutea urls.py)',
                )
                # La 3ª copia (views_modulo_productos.py) sigue en pie a
                # propósito: ese módulo SÍ está importado por otros y su
                # limpieza es una decisión pendiente del usuario, no de F-06.
                # No se asserta para no romper el test cuando se decida.

    def test_no_se_borro_de_mas(self):
        for nombre in FBV_QUE_SOBREVIVEN:
            with self.subTest(fbv=nombre):
                self.assertTrue(
                    callable(getattr(modulo_huerfano, nombre, None)),
                    f'{nombre} desapareció del módulo: el borrado se pasó de largo',
                )

    def test_el_modulo_ya_no_importa_los_modelos_de_traspaso(self):
        """Sus únicas consumidoras eran las 3 FBV borradas."""
        for simbolo in ('Traspaso', 'Traspaso_Detalle'):
            with self.subTest(simbolo=simbolo):
                self.assertFalse(hasattr(modulo_huerfano, simbolo))


class GemelasVivasDeF06RespondenTest(TestCase):
    """El borrado no dejó huérfanas las 2 URLs vivas de F-06.

    No basta con que `resolve()` apunte a `app.views`: acá se PEGA a la URL y
    se comprueba que la vista corre y devuelve SU PROPIA validación (no un 404
    de ruta rota ni un 500). Se llama con el request más pobre posible, el que
    aborta antes de escribir, y se verifica que efectivamente no escribió.
    """

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA')
        self.user = crear_usuario(rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.client.force_login(self.user)

    def test_crear_producto_desde_recepcion_corta_por_falta_de_sucursal(self):
        """Sin sucursal en sesión la vista viva corta en 400 y no toca la BD.

        Se usa un usuario SIN `EmpresaUser`: no alcanza con vaciar la sesión,
        porque `middleware_permisos._corregir_sesion_sin_sucursal` la vuelve a
        llenar en cada request cuando el usuario tiene una asignada.
        """
        cliente = Client()
        cliente.force_login(crear_usuario(username='sin_sucursal', rol='vendedor'))
        antes = Producto.objects.count()
        respuesta = cliente.post(reverse('crear_producto_desde_recepcion'), {})
        self.assertEqual(respuesta.status_code, 400)
        datos = json.loads(respuesta.content)
        self.assertFalse(datos['success'])
        self.assertIn('sucursal', datos['error'].lower())
        self.assertEqual(Producto.objects.count(), antes)

    def test_crear_producto_manual_corta_por_campos_obligatorios(self):
        """POST vacío: la vista viva devuelve su validación, no un 500."""
        antes = Producto.objects.count()
        respuesta = self.client.post(reverse('crear_producto_manual'), {})
        self.assertEqual(respuesta.status_code, 200)
        datos = json.loads(respuesta.content)
        self.assertFalse(datos['success'])
        self.assertIn('obligatorios', datos['error'].lower())
        self.assertEqual(Producto.objects.count(), antes)

    def test_crear_producto_manual_sigue_siendo_solo_post(self):
        """La viva es @require_POST; la copia borrada aceptaba GET y renderizaba
        un template que ya no existe en el módulo huérfano."""
        self.assertEqual(
            self.client.get(reverse('crear_producto_manual')).status_code, 405)


class ReportesExistenciasVivosTest(TestCase):
    """Los 3 reportes que SÍ están ruteados siguen respondiendo y cuadran."""

    CODIGOS = ('reporte_existencias', 'reporte_existencias_sucursal', 'resumen_existencias')

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA')
        Categoria.objects.get_or_create(nombre='Calzado')

        self.user = crear_usuario(rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.sucursal)

        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='reportes', defaults={'nombre': 'Reportes'})
        for codigo in self.CODIGOS:
            opcion, _ = OpcionMenu.objects.get_or_create(
                modulo=modulo, codigo=codigo, defaults={'nombre': codigo})
            PermisoRol.objects.get_or_create(
                rol='administrador', opcion_menu=opcion,
                defaults=dict(puede_ver=True, puede_crear=True, puede_editar=True,
                              puede_eliminar=True, puede_exportar=True, puede_aprobar=True),
            )

        # 10 unidades vivas + una fila con stock negativo. El patrón existe en
        # prod: 5 filas / -7 unidades en el alcance del admin (foto del
        # 2026-08-22; el conteo se mueve con las ventas, el mecanismo no).
        crear_producto_con_talla(self.sucursal, articulo='VIVO', sku=7000001, stock=10)
        crear_producto_con_talla(self.sucursal, articulo='NEGATIVO', sku=7000002, stock=-3)

        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.sucursal.id
        sesion.save()

    def _json(self, nombre_url, **params):
        respuesta = self.client.get(reverse(nombre_url), params)
        self.assertEqual(respuesta.status_code, 200)
        datos = json.loads(respuesta.content)
        self.assertTrue(datos.get('success'), datos.get('error'))
        return datos

    def test_legacy_reportes_existencias_responde(self):
        datos = self._json('obtener_existencias_reporte', pagina=1, por_pagina=50)
        self.assertEqual(datos['paginacion']['total_registros'], 2)
        # Aporte propio del legacy: costo FIFO por fila (los otros dos no lo tienen).
        self.assertIn('costo_fifo', datos['existencias'][0])

    def test_existencias_sucursal_responde_y_exige_sucursal(self):
        datos = self._json('obtener_reporte_existencias_sucursal', sucursal_id=self.sucursal.id)
        self.assertEqual(datos['resumen']['stock_total'], 10)
        # Aporte propio: "Recibido hist." y KPIs de salud.
        self.assertIn('stock_inicial', datos['datos'][0])
        self.assertIn('pct_stock_viejo', datos['resumen'])

        sin_sucursal = self.client.get(reverse('obtener_reporte_existencias_sucursal'))
        self.assertFalse(json.loads(sin_sucursal.content)['success'])

    def test_resumen_existencias_responde(self):
        datos = self._json('obtener_resumen_existencias')
        fila = next(f for f in datos['datos'] if f['sucursal_id'] == self.sucursal.id)
        self.assertEqual(fila['total_pares'], 10)
        # Aporte propio: declara el stock que la ficha oculta del análisis.
        self.assertIn('ocultos_pares', fila)

    def test_los_tres_coinciden_en_unidades_cuando_se_pide_solo_con_stock(self):
        """Con el mismo universo (stock > 0) los tres dan el MISMO número."""
        legacy = self._json('obtener_existencias_reporte', pagina=1, por_pagina=50,
                            estado_stock='con-stock')
        sucursal = self._json('obtener_reporte_existencias_sucursal',
                              sucursal_id=self.sucursal.id)
        resumen = self._json('obtener_resumen_existencias')
        fila = next(f for f in resumen['datos'] if f['sucursal_id'] == self.sucursal.id)

        self.assertEqual(legacy['kpis_globales']['total_stock'], 10)
        self.assertEqual(sucursal['resumen']['stock_total'], 10)
        self.assertEqual(fila['total_pares'], 10)

    def test_el_legacy_sin_filtro_suma_el_stock_negativo_y_el_resumen_no(self):
        """Única divergencia estructural entre ambos.

        En prod, foto del 2026-08-22: −7 u en el holding, −1 u en NICK2. Acá se
        prueba el MECANISMO con un universo propio, no esas cifras.
        """
        legacy = self._json('obtener_existencias_reporte', pagina=1, por_pagina=50)
        resumen = self._json('obtener_resumen_existencias')
        fila = next(f for f in resumen['datos'] if f['sucursal_id'] == self.sucursal.id)

        self.assertEqual(legacy['kpis_globales']['total_stock'], 7)   # 10 + (-3)
        self.assertEqual(fila['total_pares'], 10)                     # solo stock > 0
        self.assertEqual(fila['total_pares'] - legacy['kpis_globales']['total_stock'], 3)
