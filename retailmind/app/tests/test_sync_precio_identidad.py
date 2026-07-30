"""
Tests de la clave de identidad usada por la SINCRONIZACIÓN DE PRECIOS.

Reproducen el incidente real del 25-07-2026 (caso "BOX ELITE"):
unos GUANTES EVERLAST ROJO UNISEX creados en EDEL reutilizando el código
009283623 de unas ZAPATILLAS EVERLAST ROJO UNISEX dejaron las zapatillas de
NICK2 a precio de guante ($109.990 → $44.990), porque el sync matcheaba solo
código + marca + color (sin género ni categoría).

Cubren:
1. La clave completa NO alcanza a un producto de otra categoría (el bug).
2. La clave completa SÍ alcanza al mismo producto en otra sucursal.
3. Las "casi-coincidencias" se reportan para poder avisar.
4. El endpoint `actualizar_precio` no pisa el precio del otro producto y lo
   devuelve en `no_sincronizadas`.
"""
import json

from django.test import TestCase, Client

from app.models import Producto, Categoria, Productos_Atributos, AtributoOpcion
from app.utils_producto_match import (
    qs_fichas_identidad_otras_sucursales,
    qs_fichas_codigo_otra_identidad,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla,
)

CODIGO = '009283623'


class SyncPrecioIdentidadTest(TestCase):
    """El sync debe distinguir productos que solo comparten código/marca/color."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.edel = crear_sucursal(self.empresa, alias='EDEL')
        self.nick2 = crear_sucursal(self.empresa, alias='NICK2')

        self.cat_zapatillas = Categoria.objects.create(nombre='Zapatillas')
        self.cat_guantes = Categoria.objects.create(nombre='Guantes')

        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_genero = Productos_Atributos.objects.create(nombre='Genero', descripcion='Genero')
        self.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='EVERLAST')
        self.color = AtributoOpcion.objects.create(atributo=attr_color, valor='ROJO')
        self.genero = AtributoOpcion.objects.create(atributo=attr_genero, valor='UNISEX')

        # Zapatillas ROJO en ambas sucursales (mismo producto, dos bodegas).
        self.zap_edel = self._crear_ficha(self.edel, self.cat_zapatillas, 109990, sku=4832116)
        self.zap_nick2 = self._crear_ficha(self.nick2, self.cat_zapatillas, 109990, sku=4832117)
        # GUANTES en EDEL reutilizando el MISMO código, marca, color y género.
        self.guantes_edel = self._crear_ficha(self.edel, self.cat_guantes, 44990, sku=4837256)

    def _crear_ficha(self, sucursal, categoria, precio, sku):
        _, talla = crear_producto_con_talla(
            sucursal, articulo=CODIGO, sku=sku, stock=5, precioventa=precio,
        )
        producto = talla.producto
        producto.categoria = categoria
        producto.atributo1 = self.marca
        producto.atributo2 = self.color
        producto.atributo3 = self.genero
        producto.precioventa = precio
        producto.save()
        return producto

    def _identidad(self, producto):
        return dict(
            articulo=producto.articulo,
            atributo1_id=producto.atributo1_id,
            atributo2_id=producto.atributo2_id,
            atributo3_id=producto.atributo3_id,
            categoria_id=producto.categoria_id,
        )

    # ------------------------------------------------------------------
    def test_guantes_no_alcanzan_a_las_zapatillas(self):
        """El bug original: crear/editar los guantes no debe tocar zapatillas."""
        alcanzadas = qs_fichas_identidad_otras_sucursales(
            excluir_sucursal_id=self.edel.id, **self._identidad(self.guantes_edel)
        )
        self.assertEqual(list(alcanzadas), [],
                         'Los guantes NO deben alcanzar a ninguna ficha de zapatillas')

    def test_zapatillas_si_alcanzan_su_par_en_otra_sucursal(self):
        """El sync legítimo (mismo producto en otra bodega) sigue funcionando."""
        alcanzadas = qs_fichas_identidad_otras_sucursales(
            excluir_sucursal_id=self.edel.id, **self._identidad(self.zap_edel)
        )
        self.assertEqual([p.id for p in alcanzadas], [self.zap_nick2.id])

    def test_casi_coincidencias_se_reportan(self):
        """Las fichas descartadas quedan visibles para poder avisar."""
        casi = qs_fichas_codigo_otra_identidad(
            excluir_sucursal_id=self.edel.id, **self._identidad(self.guantes_edel)
        )
        # Desde los guantes de EDEL: las zapatillas de NICK2 (otra sucursal).
        self.assertIn(self.zap_nick2.id, [p.id for p in casi])

    def test_clave_antigua_habria_pisado_el_precio(self):
        """Documenta el bug: la clave corta sí alcanzaba a las zapatillas."""
        clave_corta = Producto.objects.filter(
            articulo__iexact=CODIGO,
            atributo1=self.marca,
            atributo2=self.color,
        ).exclude(sucursal=self.edel)
        self.assertIn(self.zap_nick2.id, [p.id for p in clave_corta])


class ActualizarPrecioNoPisaOtroProductoTest(TestCase):
    """El endpoint de edición rápida no debe pisar precios de otro producto."""

    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.edel = crear_sucursal(self.empresa, alias='EDEL')
        self.nick2 = crear_sucursal(self.empresa, alias='NICK2')
        crear_empresa_user(self.user, self.empresa, self.edel)

        self.cat_zapatillas = Categoria.objects.create(nombre='Zapatillas')
        self.cat_guantes = Categoria.objects.create(nombre='Guantes')
        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_genero = Productos_Atributos.objects.create(nombre='Genero', descripcion='Genero')
        self.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='EVERLAST')
        self.color = AtributoOpcion.objects.create(atributo=attr_color, valor='ROJO')
        self.genero = AtributoOpcion.objects.create(atributo=attr_genero, valor='UNISEX')

        self.guantes = self._crear_ficha(self.edel, self.cat_guantes, 44990, 4837256)
        self.zapatillas = self._crear_ficha(self.nick2, self.cat_zapatillas, 109990, 4832117)

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.edel.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _crear_ficha(self, sucursal, categoria, precio, sku):
        _, talla = crear_producto_con_talla(
            sucursal, articulo=CODIGO, sku=sku, stock=5, precioventa=precio,
        )
        producto = talla.producto
        producto.categoria = categoria
        producto.atributo1 = self.marca
        producto.atributo2 = self.color
        producto.atributo3 = self.genero
        producto.precioventa = precio
        producto.save()
        return producto

    def test_cambiar_precio_guantes_no_toca_zapatillas(self):
        resp = self.client.post(
            '/app/gestion-precios/actualizar-precio/',
            data=json.dumps({
                'producto_id': self.guantes.id,
                'nuevo_precio': 39990,
                'sincronizar_sucursales': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])

        self.guantes.refresh_from_db()
        self.zapatillas.refresh_from_db()
        self.assertEqual(self.guantes.precioventa, 39990)
        self.assertEqual(self.zapatillas.precioventa, 109990,
                         'Las zapatillas de NICK2 NO deben cambiar de precio')
        self.assertEqual(data.get('productos_sincronizados', 0), 0)

        # Y el usuario recibe el aviso de la ficha no sincronizada.
        avisos = data.get('no_sincronizadas') or []
        self.assertTrue(any(a['sucursal'] == 'NICK2' for a in avisos), avisos)
