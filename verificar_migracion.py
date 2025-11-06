#!/usr/bin/env python
"""
Script para verificar el estado de la migración desde Vicent a RetailMind

Uso:
    python verificar_migracion.py
"""

import os
import sys
import django
from decimal import Decimal

# Configurar Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'retailmind'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db import connections
from app.models import (
    Categoria, Productos_Atributos, AtributoOpcion,
    Producto, Producto_Talla, Sucursal
)


def print_header(texto):
    """Imprime un encabezado formateado"""
    print("\n" + "=" * 80)
    print(f"  {texto}")
    print("=" * 80)


def verificar_conexion_mysql():
    """Verifica la conexión con MySQL (Vicent)"""
    print_header("🔌 VERIFICACIÓN DE CONEXIÓN MYSQL (VICENT)")
    
    try:
        with connections['vicent_mysql'].cursor() as cursor:
            # Verificar tabla talla
            cursor.execute("SELECT COUNT(*) FROM talla")
            total_talla = cursor.fetchone()[0]
            print(f"✅ Conexión exitosa")
            print(f"📊 Total registros en tabla 'talla': {total_talla:,}")
            
            # Códigos únicos (productos padre en RetailMind)
            cursor.execute("""
                SELECT COUNT(DISTINCT codigo_asociado) 
                FROM talla 
                WHERE codigo_asociado IS NOT NULL AND codigo_asociado != ''
            """)
            total_codigos = cursor.fetchone()[0]
            print(f"📦 Códigos asociados únicos: {total_codigos:,}")
            
            # Bodegas únicas
            cursor.execute("""
                SELECT COUNT(DISTINCT alias) 
                FROM talla 
                WHERE alias IS NOT NULL AND alias != ''
            """)
            total_bodegas = cursor.fetchone()[0]
            print(f"🏪 Bodegas únicas: {total_bodegas}")
            
            # Categorías únicas
            cursor.execute("""
                SELECT COUNT(DISTINCT familia) 
                FROM talla 
                WHERE familia IS NOT NULL AND familia != ''
            """)
            total_familias = cursor.fetchone()[0]
            print(f"📁 Familias únicas: {total_familias}")
            
            return True
            
    except Exception as e:
        print(f"❌ Error conectando a MySQL: {str(e)}")
        print("\n💡 Asegúrate de:")
        print("   1. Configurar MYSQL_PASSWORD en .env")
        print("   2. Que MySQL esté corriendo")
        print("   3. Que existe la base de datos 'vicent_software'")
        return False


def verificar_estructura_retailmind():
    """Verifica la estructura migrada en RetailMind"""
    print_header("📊 ESTRUCTURA EN RETAILMIND (POSTGRESQL)")
    
    # Categorías
    total_categorias = Categoria.objects.count()
    print(f"📁 Categorías: {total_categorias}")
    if total_categorias > 0:
        print("   Ejemplos:")
        for cat in Categoria.objects.all()[:5]:
            print(f"   - {cat.nombre}")
    
    # Atributos
    total_atributos = Productos_Atributos.objects.count()
    print(f"\n🏷️  Atributos: {total_atributos}")
    if total_atributos > 0:
        for atributo in Productos_Atributos.objects.all():
            opciones_count = atributo.opciones.count()
            print(f"   - {atributo.nombre}: {opciones_count} opciones")
            # Mostrar primeras 3 opciones
            for opcion in atributo.opciones.all()[:3]:
                print(f"      • {opcion.valor}")
            if opciones_count > 3:
                print(f"      • ... y {opciones_count - 3} más")
    
    # Bodegas/Sucursales
    total_sucursales = Sucursal.objects.count()
    print(f"\n🏪 Sucursales/Bodegas: {total_sucursales}")
    if total_sucursales > 0:
        print("   Listado:")
        for suc in Sucursal.objects.all()[:10]:
            print(f"   - {suc.alias} ({suc.empresa.nombre})")
    
    # Productos
    total_productos = Producto.objects.count()
    print(f"\n📦 Productos Padre: {total_productos:,}")
    
    # Variaciones
    total_variaciones = Producto_Talla.objects.count()
    print(f"📊 Variaciones (ProductoTalla): {total_variaciones:,}")


def verificar_productos_muestra():
    """Muestra una muestra de productos migrados"""
    print_header("🔍 MUESTRA DE PRODUCTOS MIGRADOS")
    
    productos = Producto.objects.all()[:5]
    
    if not productos:
        print("⚠️  No hay productos migrados aún")
        return
    
    for idx, producto in enumerate(productos, 1):
        print(f"\n📦 Producto {idx}:")
        print(f"   Artículo: {producto.articulo}")
        print(f"   Descripción: {producto.descripcion}")
        print(f"   Categoría: {producto.categoria.nombre if producto.categoria else 'N/A'}")
        print(f"   Marca: {producto.atributo1.valor if producto.atributo1 else 'N/A'}")
        print(f"   Color: {producto.atributo2.valor if producto.atributo2 else 'N/A'}")
        print(f"   Género: {producto.atributo3.valor if producto.atributo3 else 'N/A'}")
        print(f"   Precio Venta: ${producto.precioventa:,}")
        
        # Variaciones
        variaciones = producto.producto_talla.all()
        print(f"   Variaciones: {variaciones.count()}")
        for var in variaciones[:3]:
            print(f"      - Talla: {var.talla}, Stock: {var.stock}, SKU: {var.sku}")
        if variaciones.count() > 3:
            print(f"      ... y {variaciones.count() - 3} más")


def comparar_totales():
    """Compara totales entre Vicent y RetailMind"""
    print_header("📈 COMPARACIÓN VICENT ↔ RETAILMIND")
    
    try:
        # Obtener totales de Vicent
        with connections['vicent_mysql'].cursor() as cursor:
            # Total variaciones
            cursor.execute("SELECT COUNT(*) FROM talla")
            vicent_variaciones = cursor.fetchone()[0]
            
            # Total códigos únicos
            cursor.execute("""
                SELECT COUNT(DISTINCT codigo_asociado) 
                FROM talla 
                WHERE codigo_asociado IS NOT NULL AND codigo_asociado != ''
            """)
            vicent_productos = cursor.fetchone()[0]
        
        # Obtener totales de RetailMind
        retailmind_productos = Producto.objects.count()
        retailmind_variaciones = Producto_Talla.objects.count()
        
        # Mostrar comparación
        print("\n🏷️  PRODUCTOS PADRE:")
        print(f"   Vicent (esperado):    {vicent_productos:,}")
        print(f"   RetailMind (migrado): {retailmind_productos:,}")
        
        if retailmind_productos > 0:
            porcentaje_productos = (retailmind_productos / vicent_productos) * 100
            print(f"   Progreso: {porcentaje_productos:.1f}%")
            
            if porcentaje_productos >= 100:
                print("   ✅ ¡Migración de productos completa!")
            else:
                print(f"   ⏳ Faltan {vicent_productos - retailmind_productos:,} productos")
        
        print("\n📊 VARIACIONES:")
        print(f"   Vicent (esperado):    {vicent_variaciones:,}")
        print(f"   RetailMind (migrado): {retailmind_variaciones:,}")
        
        if retailmind_variaciones > 0:
            porcentaje_variaciones = (retailmind_variaciones / vicent_variaciones) * 100
            print(f"   Progreso: {porcentaje_variaciones:.1f}%")
            
            if porcentaje_variaciones >= 100:
                print("   ✅ ¡Migración de variaciones completa!")
            else:
                print(f"   ⏳ Faltan {vicent_variaciones - retailmind_variaciones:,} variaciones")
        
    except Exception as e:
        print(f"❌ Error comparando totales: {str(e)}")


def verificar_integridad():
    """Verifica la integridad de los datos migrados"""
    print_header("🔍 VERIFICACIÓN DE INTEGRIDAD")
    
    # Productos sin categoría
    sin_categoria = Producto.objects.filter(categoria__isnull=True).count()
    if sin_categoria > 0:
        print(f"⚠️  Productos sin categoría: {sin_categoria}")
    else:
        print(f"✅ Todos los productos tienen categoría")
    
    # Productos sin variaciones
    from django.db.models import Count
    sin_variaciones = Producto.objects.annotate(
        num_variaciones=Count('producto_talla')
    ).filter(num_variaciones=0).count()
    
    if sin_variaciones > 0:
        print(f"⚠️  Productos sin variaciones: {sin_variaciones}")
    else:
        print(f"✅ Todos los productos tienen variaciones")
    
    # Variaciones sin stock
    sin_stock = Producto_Talla.objects.filter(stock=0).count()
    total_variaciones = Producto_Talla.objects.count()
    if total_variaciones > 0:
        porcentaje_sin_stock = (sin_stock / total_variaciones) * 100
        print(f"📊 Variaciones sin stock: {sin_stock:,} ({porcentaje_sin_stock:.1f}%)")
    
    # Productos sin atributos
    sin_marca = Producto.objects.filter(atributo1__isnull=True).count()
    sin_color = Producto.objects.filter(atributo2__isnull=True).count()
    sin_genero = Producto.objects.filter(atributo3__isnull=True).count()
    
    total_productos = Producto.objects.count()
    if total_productos > 0:
        print(f"\n📊 Productos sin atributos:")
        print(f"   Sin Marca: {sin_marca} ({(sin_marca/total_productos)*100:.1f}%)")
        print(f"   Sin Color: {sin_color} ({(sin_color/total_productos)*100:.1f}%)")
        print(f"   Sin Género: {sin_genero} ({(sin_genero/total_productos)*100:.1f}%)")


def main():
    """Función principal"""
    print("\n")
    print("🔄 " + "=" * 76)
    print("🔄  VERIFICACIÓN DE MIGRACIÓN VICENT → RETAILMIND")
    print("🔄 " + "=" * 76)
    
    # 1. Verificar conexión MySQL
    mysql_ok = verificar_conexion_mysql()
    
    # 2. Verificar estructura en RetailMind
    verificar_estructura_retailmind()
    
    # 3. Mostrar muestra de productos
    verificar_productos_muestra()
    
    # 4. Comparar totales
    if mysql_ok:
        comparar_totales()
    
    # 5. Verificar integridad
    verificar_integridad()
    
    print_header("✅ VERIFICACIÓN COMPLETADA")
    print()


if __name__ == '__main__':
    main()

