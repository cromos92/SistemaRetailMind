"""
EJEMPLOS DE USO DEL MÓDULO GENERADOR DE TXT ACEPTA
===================================================

Este archivo contiene ejemplos prácticos de cómo usar el módulo
de generación de archivos TXT para Acepta en diferentes escenarios.

IMPORTANTE: Estos son ejemplos de referencia. Ajusta los datos según
tu caso de uso específico.
"""

from decimal import Decimal
from datetime import date, datetime
from django.utils import timezone


# ============================================================================
# EJEMPLO 1: FACTURA ELECTRÓNICA BÁSICA
# ============================================================================

def ejemplo_factura_basica():
    """Ejemplo más simple de una factura electrónica"""
    
    datos = {
        'documento': {
            'tipo_documento': 33,  # Factura Electrónica
            'folio': 12345,
            'fecha_emision': date(2025, 11, 5),
            'forma_pago': 1,  # Contado
        },
        'emisor': {
            'rut': '76.123.456-7',
            'razon_social': 'MI EMPRESA LTDA',
            'giro': 'VENTA AL POR MENOR',
        },
        'receptor': {
            'rut': '77.654.321-K',
            'razon_social': 'CLIENTE EJEMPLO S.A.',
        },
        'totales': {
            'monto_neto': Decimal('150000'),
            'tasa_iva': Decimal('19.00'),
            'iva': Decimal('28500'),
            'monto_total': Decimal('178500'),
        },
        'detalle': [
            {
                'nombre': 'PRODUCTO A',
                'cantidad': Decimal('10'),
                'unidad': 'UN',
                'precio_unitario': Decimal('15000'),
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('150000'),
            }
        ]
    }
    
    return datos


# ============================================================================
# EJEMPLO 2: FACTURA CON MÚLTIPLES PRODUCTOS Y DESCUENTOS
# ============================================================================

def ejemplo_factura_completa():
    """Factura con varios productos, descuentos y datos completos"""
    
    datos = {
        'documento': {
            'tipo_documento': 33,
            'folio': 12346,
            'fecha_emision': '2025-11-05',
            'forma_pago': 2,  # Crédito
            'fecha_vencimiento': '2025-12-05',  # 30 días
            'timestamp': datetime.now(),
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'EMPRESA RETAIL DEMO LTDA',
            'giro': 'VENTA AL POR MAYOR DE PRODUCTOS ALIMENTICIOS',
            'acteco': '462100',
            'sucursal': 'CASA MATRIZ',
            'codigo_sucursal': '001',
            'direccion': 'AV. PRINCIPAL 123',
            'comuna': 'SANTIAGO',
            'ciudad': 'SANTIAGO',
            'codigo_vendedor': 'VEND001',
            'telefono': '+56912345678',
        },
        'receptor': {
            'rut': '77654321-K',
            'codigo_interno': 'CLI001',
            'razon_social': 'SUPERMERCADO EJEMPLO S.A.',
            'giro': 'COMERCIO AL POR MENOR',
            'contacto': 'Juan Pérez - 56987654321',
            'direccion': 'CALLE COMERCIO 456',
            'comuna': 'PROVIDENCIA',
            'ciudad': 'SANTIAGO',
        },
        'totales': {
            'monto_neto': Decimal('485375'),
            'tasa_iva': Decimal('19.00'),
            'iva': Decimal('92221'),
            'monto_total': Decimal('577596'),
        },
        'detalle': [
            {
                'nombre': 'ARROZ GRADO 1',
                'descripcion': 'ARROZ GRADO 1 SACO 25 KG',
                'cantidad': Decimal('20'),
                'unidad': 'SACO',
                'precio_unitario': Decimal('15000'),
                'descuento_pct': Decimal('0'),
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('300000'),
            },
            {
                'nombre': 'ACEITE VEGETAL',
                'descripcion': 'ACEITE VEGETAL BOTELLA 1 LT',
                'cantidad': Decimal('50'),
                'unidad': 'UN',
                'precio_unitario': Decimal('2500'),
                'descuento_pct': Decimal('10.00'),
                'monto_descuento': Decimal('12500'),  # 50 * 2500 * 0.10
                'monto_item': Decimal('112500'),  # (50 * 2500) - 12500
            },
            {
                'nombre': 'AZUCAR',
                'descripcion': 'AZUCAR REFINADA SACO 50 KG',
                'cantidad': Decimal('10'),
                'unidad': 'SACO',
                'precio_unitario': Decimal('8500'),
                'descuento_pct': Decimal('5.00'),
                'monto_descuento': Decimal('4250'),
                'monto_item': Decimal('80750'),
            },
        ]
    }
    
    return datos


# ============================================================================
# EJEMPLO 3: BOLETA ELECTRÓNICA (CONSUMIDOR FINAL)
# ============================================================================

def ejemplo_boleta_consumidor_final():
    """Boleta para consumidor final genérico"""
    
    datos = {
        'documento': {
            'tipo_documento': 39,  # Boleta Electrónica
            'folio': 5678,
            'fecha_emision': date.today(),
            'forma_pago': 1,  # Contado
            'ind_servicio': 3,  # Factura de Servicios
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'MI NEGOCIO SPA',
            'giro': 'VENTA DE PRODUCTOS VARIOS',
        },
        'receptor': {
            'rut': '66666666-6',  # RUT genérico para consumidor final
            'razon_social': 'CONSUMIDOR FINAL',
        },
        'totales': {
            'monto_neto': Decimal('86000'),
            'tasa_iva': Decimal('19.00'),
            'iva': Decimal('16340'),
            'monto_total': Decimal('102340'),
        },
        'detalle': [
            {
                'nombre': 'SERVICIO DE INSTALACION',
                'cantidad': Decimal('1'),
                'unidad': 'UN',
                'precio_unitario': Decimal('50000'),
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('50000'),
            },
            {
                'nombre': 'PRODUCTO X',
                'cantidad': Decimal('3'),
                'unidad': 'UN',
                'precio_unitario': Decimal('12000'),
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('36000'),
            },
        ]
    }
    
    return datos


# ============================================================================
# EJEMPLO 4: GUÍA DE DESPACHO
# ============================================================================

def ejemplo_guia_despacho():
    """Guía de despacho con datos de transporte"""
    
    datos = {
        'documento': {
            'tipo_documento': 52,  # Guía de Despacho
            'folio': 789,
            'fecha_emision': '2025-11-05',
            'tipo_despacho': 2,  # Por cuenta del emisor al receptor
            'ind_traslado': 1,   # Operación constituye venta
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'DISTRIBUIDORA DEMO LTDA',
            'giro': 'DISTRIBUCION DE ALIMENTOS',
            'acteco': '462100',
            'direccion': 'BODEGA CENTRAL AV LOGISTICA 500',
            'comuna': 'QUILICURA',
            'ciudad': 'QUILICURA',
        },
        'receptor': {
            'rut': '77654321-K',
            'razon_social': 'SUPERMERCADO ABC S.A.',
            'direccion': 'CALLE COMPRAS 200',
            'comuna': 'LAS CONDES',
            'ciudad': 'LAS CONDES',
        },
        'transporte': {
            'patente': 'ABCD12',
            'rut_transportista': '12345678-9',
            'direccion_destino': 'CALLE COMPRAS 200',
            'comuna_destino': 'LAS CONDES',
            'ciudad_destino': 'LAS CONDES',
        },
        'totales': {
            'monto_neto': Decimal('0'),  # Las guías no llevan montos
            'tasa_iva': Decimal('0'),
            'iva': Decimal('0'),
            'monto_total': Decimal('0'),
        },
        'detalle': [
            {
                'nombre': 'CAJA PRODUCTO A 12 UNIDADES',
                'cantidad': Decimal('50'),
                'unidad': 'CAJA',
                'precio_unitario': Decimal('0'),  # Sin precio en guías
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('0'),
            },
            {
                'nombre': 'CAJA PRODUCTO B 24 UNIDADES',
                'cantidad': Decimal('30'),
                'unidad': 'CAJA',
                'precio_unitario': Decimal('0'),
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('0'),
            },
        ]
    }
    
    return datos


# ============================================================================
# EJEMPLO 5: NOTA DE CRÉDITO
# ============================================================================

def ejemplo_nota_credito():
    """Nota de crédito para anular una factura"""
    
    datos = {
        'documento': {
            'tipo_documento': 61,  # Nota de Crédito
            'folio': 234,
            'fecha_emision': '2025-11-05',
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'EMPRESA DEMO LTDA',
            'giro': 'VENTA AL POR MAYOR DE PRODUCTOS ALIMENTICIOS',
            'acteco': '462100',
        },
        'receptor': {
            'rut': '77654321-K',
            'razon_social': 'CLIENTE EJEMPLO S.A.',
        },
        'totales': {
            'monto_neto': Decimal('-192500'),    # NEGATIVO
            'tasa_iva': Decimal('19.00'),
            'iva': Decimal('-36575'),            # NEGATIVO
            'monto_total': Decimal('-229075'),   # NEGATIVO
        },
        'detalle': [
            {
                'nombre': 'PRODUCTO A - DEVOLUCION',
                'descripcion': 'Devolución por error en precio',
                'cantidad': Decimal('-10'),      # NEGATIVO
                'unidad': 'UN',
                'precio_unitario': Decimal('15000'),  # Precio positivo
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('-150000'),     # NEGATIVO
            },
            {
                'nombre': 'PRODUCTO B - DEVOLUCION',
                'cantidad': Decimal('-5'),
                'unidad': 'KG',
                'precio_unitario': Decimal('8500'),
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('-42500'),
            },
        ]
    }
    
    return datos


# ============================================================================
# EJEMPLO 6: FACTURA EXENTA
# ============================================================================

def ejemplo_factura_exenta():
    """Factura para productos exentos de IVA"""
    
    datos = {
        'documento': {
            'tipo_documento': 34,  # Factura Exenta
            'folio': 999,
            'fecha_emision': '2025-11-05',
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'FUNDACION EJEMPLO',
            'giro': 'SERVICIOS EDUCACIONALES',
            'acteco': '853100',
        },
        'receptor': {
            'rut': '77654321-K',
            'razon_social': 'INSTITUCION EDUCATIVA',
        },
        'totales': {
            'monto_neto': Decimal('0'),      # No hay productos afectos
            'monto_exento': Decimal('150000'),  # Productos exentos
            'tasa_iva': Decimal('0'),
            'iva': Decimal('0'),             # Sin IVA
            'monto_total': Decimal('150000'),
        },
        'detalle': [
            {
                'indicador_exencion': 1,  # Producto exento
                'nombre': 'SERVICIO EDUCATIVO',
                'cantidad': Decimal('10'),
                'unidad': 'UN',
                'precio_unitario': Decimal('15000'),
                'monto_descuento': Decimal('0'),
                'monto_item': Decimal('150000'),
            }
        ]
    }
    
    return datos


# ============================================================================
# EJEMPLO 7: USO EN UNA VISTA DJANGO
# ============================================================================

def ejemplo_vista_django():
    """
    Ejemplo de cómo usar el generador en una vista Django
    """
    
    codigo_ejemplo = '''
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from decimal import Decimal
import json

from .views_modulo_documentos import generar_txt_dte_acepta

@require_POST
@login_required
def generar_factura_txt(request):
    """Vista que genera una factura TXT"""
    
    try:
        # Obtener datos del request
        data = json.loads(request.body)
        
        # Construir estructura de datos
        datos = {
            'documento': {
                'tipo_documento': 33,
                'folio': data['folio'],
                'fecha_emision': data['fecha'],
                'forma_pago': data.get('forma_pago', 1),
            },
            'emisor': {
                'rut': request.user.empresa.rut,
                'razon_social': request.user.empresa.nombre,
                'giro': request.user.empresa.giro,
                # ... más datos del emisor
            },
            'receptor': {
                'rut': data['cliente_rut'],
                'razon_social': data['cliente_nombre'],
                # ... más datos del receptor
            },
            'totales': {
                'monto_neto': Decimal(data['subtotal']),
                'tasa_iva': Decimal('19.00'),
                'iva': Decimal(data['iva']),
                'monto_total': Decimal(data['total']),
            },
            'detalle': []
        }
        
        # Agregar productos
        for producto in data['productos']:
            datos['detalle'].append({
                'nombre': producto['nombre'],
                'cantidad': Decimal(producto['cantidad']),
                'unidad': producto.get('unidad', 'UN'),
                'precio_unitario': Decimal(producto['precio']),
                'monto_descuento': Decimal(producto.get('descuento', 0)),
                'monto_item': Decimal(producto['total']),
            })
        
        # Generar TXT
        contenido_txt = generar_txt_dte_acepta(datos)
        
        # Crear respuesta con archivo
        response = HttpResponse(contenido_txt, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="factura_{data["folio"]}.txt"'
        
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
    '''
    
    return codigo_ejemplo


# ============================================================================
# EJEMPLO 8: CÁLCULO AUTOMÁTICO DE TOTALES
# ============================================================================

def calcular_totales_automaticos(productos, descuento_global=0):
    """
    Función auxiliar para calcular totales automáticamente
    
    Args:
        productos: Lista de productos con cantidad y precio_unitario
        descuento_global: Descuento global a aplicar
        
    Returns:
        dict con totales calculados
    """
    
    subtotal = Decimal('0')
    
    for producto in productos:
        cantidad = Decimal(str(producto['cantidad']))
        precio = Decimal(str(producto['precio_unitario']))
        descuento_item = Decimal(str(producto.get('descuento_unitario', 0)))
        
        monto_item = cantidad * (precio - descuento_item)
        subtotal += monto_item
        
        # Actualizar producto con monto calculado
        producto['monto_item'] = monto_item
        producto['monto_descuento'] = descuento_item * cantidad
    
    monto_neto = subtotal - Decimal(str(descuento_global))
    iva = (monto_neto * Decimal('0.19')).quantize(Decimal('1'))
    total = monto_neto + iva
    
    return {
        'monto_neto': monto_neto,
        'tasa_iva': Decimal('19.00'),
        'iva': iva,
        'monto_total': total,
    }


# ============================================================================
# EJEMPLO 9: USO COMPLETO CON CÁLCULOS AUTOMÁTICOS
# ============================================================================

def ejemplo_con_calculos_automaticos():
    """Ejemplo que calcula totales automáticamente"""
    
    # Definir productos
    productos = [
        {
            'nombre': 'PRODUCTO A',
            'cantidad': 10,
            'unidad': 'UN',
            'precio_unitario': 15000,
            'descuento_unitario': 0,
        },
        {
            'nombre': 'PRODUCTO B',
            'cantidad': 5,
            'unidad': 'KG',
            'precio_unitario': 8500,
            'descuento_unitario': 425,  # 5% de descuento
        }
    ]
    
    # Calcular totales
    totales = calcular_totales_automaticos(productos)
    
    # Construir datos completos
    datos = {
        'documento': {
            'tipo_documento': 33,
            'folio': 12347,
            'fecha_emision': date.today(),
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'MI EMPRESA',
            'giro': 'COMERCIO',
        },
        'receptor': {
            'rut': '77654321-K',
            'razon_social': 'MI CLIENTE',
        },
        'totales': totales,
        'detalle': productos,
    }
    
    return datos


# ============================================================================
# FUNCIÓN PRINCIPAL DE DEMOSTRACIÓN
# ============================================================================

if __name__ == '__main__':
    """
    Ejecuta ejemplos de demostración
    
    Para usar en tu proyecto Django:
    
    from retailmind.app.views_modulo_documentos import generar_txt_dte_acepta
    
    # Elegir un ejemplo
    datos = ejemplo_factura_basica()
    # o
    datos = ejemplo_factura_completa()
    # etc...
    
    # Generar TXT
    contenido_txt = generar_txt_dte_acepta(datos)
    
    # Guardar en archivo
    with open('factura.txt', 'w', encoding='utf-8') as f:
        f.write(contenido_txt)
    """
    
    print("=" * 70)
    print("EJEMPLOS DE USO DEL GENERADOR DE TXT ACEPTA")
    print("=" * 70)
    print()
    print("Ejemplos disponibles:")
    print("1. ejemplo_factura_basica()")
    print("2. ejemplo_factura_completa()")
    print("3. ejemplo_boleta_consumidor_final()")
    print("4. ejemplo_guia_despacho()")
    print("5. ejemplo_nota_credito()")
    print("6. ejemplo_factura_exenta()")
    print("7. ejemplo_con_calculos_automaticos()")
    print()
    print("Para usar en tu código:")
    print()
    print("from ejemplos_uso_generador_txt import ejemplo_factura_basica")
    print("from retailmind.app.views_modulo_documentos import generar_txt_dte_acepta")
    print()
    print("datos = ejemplo_factura_basica()")
    print("contenido_txt = generar_txt_dte_acepta(datos)")
    print()
    print("=" * 70)

