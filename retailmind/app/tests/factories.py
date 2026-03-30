"""
Factories para crear objetos de prueba de forma consistente.
Centraliza la creación de datos de test para evitar duplicación.
"""
from django.contrib.auth import get_user_model
from app.models import (
    Empresa, Sucursal, Vendedor, EmpresaUser, Producto, Producto_Talla,
    Ticket, Ticket_Productos, Dte, Correlativo, Movimientos_Producto,
    Productos_Atributos, AtributoOpcion, Categoria, LoteProducto,
)

Usuario = get_user_model()


def crear_usuario(username='testuser', password='TestPass123!', rol='vendedor', **kwargs):
    defaults = {
        'email': f'{username}@test.com',
        'first_name': 'Test',
        'last_name': 'User',
        'es_activo': True,
        'rol': rol,
    }
    defaults.update(kwargs)
    user = Usuario.objects.create_user(username=username, password=password, **defaults)
    return user


def crear_empresa(nombre='Empresa Test', **kwargs):
    defaults = {
        'nombre': nombre,
        'rut': '76.000.000-0',
        'nombre_fantasia': nombre,
        'razon_social': f'{nombre} SpA',
        'giro': 'Venta al por menor',
        'direccion': 'Calle Test 123',
        'comuna': 'Santiago',
        'ciudad': 'Santiago',
        'esProveedor': False,
        'correoVendedor': 'vendedor@test.com',
        'correoIntercambio': 'intercambio@test.com',
        'correoAdministrador': 'admin@test.com',
    }
    defaults.update(kwargs)
    return Empresa.objects.create(**defaults)


def crear_sucursal(empresa=None, alias='SUC-TEST', **kwargs):
    if empresa is None:
        empresa = crear_empresa()
    defaults = {
        'direccion': 'Av. Test 456',
        'tipo_sucursal': 'VENDEDORA',
        'es_centro_distribucion': False,
    }
    defaults.update(kwargs)
    return Sucursal.objects.create(empresa=empresa, alias=alias, **defaults)


def crear_vendedor(nombre='Vendedor Test', empresa=None, **kwargs):
    defaults = {
        'codigo_vendedor': 'V001',
        'rut': '12.345.678-9',
        'correo': 'vendedor@test.com',
        'comision': 5.00,
        'activo': True,
    }
    defaults.update(kwargs)
    vendedor = Vendedor.objects.create(nombre=nombre, empresa=empresa, **defaults)
    return vendedor


def crear_empresa_user(user, empresa, sucursal, **kwargs):
    defaults = {
        'status': True,
        'active': True,
    }
    defaults.update(kwargs)
    return EmpresaUser.objects.create(
        user=user, empresa=empresa, sucursal=sucursal, **defaults
    )


def crear_producto_con_talla(sucursal, articulo='Zapatilla Test', talla='42',
                              sku=1000001, stock=10, costo=15000,
                              sobreprecio=5000, precioventa=20000, **kwargs):
    """Crea un Producto + Producto_Talla listos para usar en tests."""
    categoria = Categoria.objects.first()
    if not categoria:
        categoria = Categoria.objects.create(nombre='Calzado')

    producto = Producto.objects.create(
        articulo=articulo,
        descripcion=f'{articulo} - Descripción',
        sucursal=sucursal,
        costo=costo,
        sobreprecio=sobreprecio,
        precioventa=precioventa,
        categoria=categoria,
        **kwargs,
    )
    producto_talla = Producto_Talla.objects.create(
        producto=producto,
        sku=sku,
        stock=stock,
        talla=talla,
    )
    return producto, producto_talla


def crear_correlativo(sucursal, tipo_dte='TICKET', inicio=1, termino=999999):
    return Correlativo.objects.create(
        sucursal=sucursal,
        tipo_dte=tipo_dte,
        inicio=inicio,
        termino=termino,
        alias=f'{tipo_dte}_{sucursal.alias}',
        responsable='Sistema',
    )


def crear_lote_fifo(producto_talla, cantidad=10, costo_unitario=15000, **kwargs):
    defaults = {
        'sobreprecio_unitario': 5000,
        'precio_venta_unitario': 20000,
        'activo': True,
        'agotado': False,
    }
    defaults.update(kwargs)
    return LoteProducto.objects.create(
        producto_talla=producto_talla,
        cantidad_inicial=cantidad,
        cantidad_disponible=cantidad,
        costo_unitario=costo_unitario,
        **defaults,
    )


def setup_entorno_completo():
    """
    Crea un entorno completo para tests de integración:
    usuario + empresa + sucursal + vendedor + empresa_user + producto con stock + correlativo + lote FIFO.
    Retorna dict con todas las entidades.
    """
    user = crear_usuario()
    empresa = crear_empresa()
    sucursal = crear_sucursal(empresa=empresa)
    vendedor = crear_vendedor(empresa=empresa)
    vendedor.sucursales.add(sucursal)
    empresa_user = crear_empresa_user(user, empresa, sucursal)
    producto, producto_talla = crear_producto_con_talla(sucursal)
    correlativo = crear_correlativo(sucursal, tipo_dte='TICKET')
    lote = crear_lote_fifo(producto_talla)

    return {
        'user': user,
        'empresa': empresa,
        'sucursal': sucursal,
        'vendedor': vendedor,
        'empresa_user': empresa_user,
        'producto': producto,
        'producto_talla': producto_talla,
        'correlativo': correlativo,
        'lote': lote,
    }
