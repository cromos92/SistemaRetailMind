#!/usr/bin/env python
"""
Script de diagnóstico para verificar el funcionamiento del sistema de stock por sucursal.
Este script verifica que los stocks se calculen correctamente desde los movimientos.
"""

import os
import sys
import django

# Configurar Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Sum, Q
from app.models import Producto_Talla, Sucursal, Movimientos_Producto, Dte


def print_header(text):
    """Imprime un encabezado formateado"""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_section(text):
    """Imprime una sección formateada"""
    print(f"\n--- {text} ---")


def diagnosticar_stock_producto(sku, sucursal_alias=None):
    """
    Diagnostica el stock de un producto específico
    """
    try:
        producto = Producto_Talla.objects.get(sku=sku)
    except Producto_Talla.DoesNotExist:
        print(f"❌ Producto con SKU {sku} no encontrado")
        return
    
    print_header(f"DIAGNÓSTICO DE PRODUCTO SKU: {sku}")
    print(f"Producto: {producto.producto.articulo if producto.producto else 'N/A'}")
    print(f"Talla: {producto.talla}")
    print(f"Campo stock (DEPRECADO): {producto.stock}")
    
    # Stock total calculado
    stock_total = producto.stock_total()
    print(f"\nStock Total (calculado desde movimientos): {stock_total}")
    
    # Stock por sucursal
    print_section("Stock por Sucursal")
    sucursales = Sucursal.objects.all()
    
    for sucursal in sucursales:
        if sucursal_alias and sucursal.alias != sucursal_alias:
            continue
            
        stock_suc = producto.stock_sucursal(sucursal.id)
        
        if stock_suc > 0:
            print(f"  ✅ {sucursal.alias:15} → {stock_suc:5} unidades")
        else:
            print(f"  ⚪ {sucursal.alias:15} → {stock_suc:5} unidades")
    
    # Movimientos del producto
    print_section("Últimos 10 Movimientos")
    movimientos = Movimientos_Producto.objects.filter(
        ProductoTalla=producto
    ).select_related(
        'sucursal_origen', 'sucursal_destino', 'dte'
    ).order_by('-fecha', '-hora')[:10]
    
    if not movimientos:
        print("  ℹ️  No hay movimientos registrados")
    else:
        print(f"  {'Fecha':12} {'Tipo':8} {'Cantidad':>8} {'Origen':15} {'Destino':15} {'Estado':15}")
        print("  " + "-" * 85)
        
        for mov in movimientos:
            origen = mov.sucursal_origen.alias if mov.sucursal_origen else "-"
            destino = mov.sucursal_destino.alias if mov.sucursal_destino else "-"
            
            cantidad_str = f"{mov.cantidad:+5}"
            if mov.cantidad > 0:
                cantidad_str = f"✅ {cantidad_str}"
            else:
                cantidad_str = f"❌ {cantidad_str}"
            
            print(f"  {str(mov.fecha):12} {mov.tipo_movimiento:8} {cantidad_str} {origen:15} {destino:15} {mov.estado:15}")
    
    # Verificar consistencia
    print_section("Verificación de Consistencia")
    
    # Calcular ingresos y egresos totales
    ingresos = Movimientos_Producto.objects.filter(
        ProductoTalla=producto,
        tipo_movimiento='INGRESO',
        estado='COMPLETADO'
    ).aggregate(total=Sum('cantidad'))['total'] or 0
    
    egresos = Movimientos_Producto.objects.filter(
        ProductoTalla=producto,
        tipo_movimiento='EGRESO',
        estado='COMPLETADO'
    ).aggregate(total=Sum('cantidad'))['total'] or 0
    
    calculado = ingresos + egresos
    
    print(f"  Total Ingresos: {ingresos:5} unidades")
    print(f"  Total Egresos:  {egresos:5} unidades (negativos)")
    print(f"  Stock Calculado: {calculado:5} unidades")
    
    if calculado == stock_total:
        print(f"  ✅ Consistencia OK: stock_total() = {stock_total}")
    else:
        print(f"  ⚠️  Discrepancia: stock_total()={stock_total}, calculado={calculado}")


def diagnosticar_dtes_pendientes():
    """
    Muestra DTEs pendientes de recepción
    """
    print_header("DTES PENDIENTES DE RECEPCIÓN")
    
    sucursales = Sucursal.objects.all()
    
    for sucursal in sucursales:
        dtes_pendientes = Dte.objects.filter(
            tipo_transaccion='TRASPASO',
            estado_dte='EMITIDO',
            fecha_recepcion__isnull=True,
            dte_movimientos__concepto='TRASPASO_SALIDA',
            dte_movimientos__tipo_movimiento='EGRESO',
            dte_movimientos__estado='COMPLETADO',
            dte_movimientos__sucursal_destino_id=sucursal.id
        ).distinct()
        
        if dtes_pendientes.exists():
            print(f"\n📦 Sucursal Destino: {sucursal.alias}")
            print(f"   {'DTE':10} {'Fecha Emisión':15} {'Origen':15} {'Productos':10}")
            print("   " + "-" * 60)
            
            for dte in dtes_pendientes:
                origen = dte.sucursal.alias if dte.sucursal else "-"
                num_productos = dte.dte_productos.count()
                print(f"   {dte.numero_documento:10} {str(dte.fecha_emision):15} {origen:15} {num_productos:10}")
    
    # Si no hay DTEs pendientes
    if not Dte.objects.filter(
        tipo_transaccion='TRASPASO',
        estado_dte='EMITIDO',
        fecha_recepcion__isnull=True
    ).exists():
        print("\n✅ No hay DTEs pendientes de recepción")


def diagnosticar_ultimos_dtes(limit=5):
    """
    Muestra los últimos DTEs emitidos
    """
    print_header(f"ÚLTIMOS {limit} DTES EMITIDOS")
    
    dtes = Dte.objects.filter(
        tipo_transaccion='TRASPASO'
    ).select_related(
        'sucursal', 'emisor', 'receptor'
    ).order_by('-fecha_emision', '-id')[:limit]
    
    if not dtes:
        print("ℹ️  No hay DTEs de traspaso registrados")
        return
    
    for dte in dtes:
        print(f"\n📄 DTE #{dte.numero_documento} - {dte.tipo_documento}")
        print(f"   Fecha: {dte.fecha_emision}")
        print(f"   Estado: {dte.estado_dte}")
        print(f"   Emisor: {dte.emisor.nombre if dte.emisor else 'N/A'}")
        print(f"   Receptor: {dte.receptor.nombre if dte.receptor else 'N/A'}")
        
        if dte.fecha_recepcion:
            print(f"   ✅ Recepcionado el: {dte.fecha_recepcion}")
        else:
            print(f"   ⏳ Pendiente de recepción")
        
        # Movimientos del DTE
        movimientos = dte.dte_movimientos.select_related(
            'sucursal_origen', 'sucursal_destino'
        ).all()
        
        if movimientos:
            print(f"   Movimientos:")
            for mov in movimientos:
                origen = mov.sucursal_origen.alias if mov.sucursal_origen else "-"
                destino = mov.sucursal_destino.alias if mov.sucursal_destino else "-"
                print(f"      {mov.tipo_movimiento:8} | {mov.concepto:20} | {origen:10} → {destino:10} | Estado: {mov.estado:12} | Cantidad: {mov.cantidad:+5}")


def menu_principal():
    """
    Menú interactivo principal
    """
    while True:
        print_header("DIAGNÓSTICO DE STOCK POR SUCURSAL")
        print("\nOpciones:")
        print("  1. Diagnosticar producto por SKU")
        print("  2. Ver DTEs pendientes de recepción")
        print("  3. Ver últimos DTEs emitidos")
        print("  4. Verificar stock de todas las sucursales")
        print("  0. Salir")
        
        opcion = input("\nSeleccione una opción: ").strip()
        
        if opcion == '1':
            sku = input("Ingrese el SKU del producto: ").strip()
            try:
                sku = int(sku)
                diagnosticar_stock_producto(sku)
            except ValueError:
                print("❌ SKU inválido. Debe ser un número.")
        
        elif opcion == '2':
            diagnosticar_dtes_pendientes()
        
        elif opcion == '3':
            limit = input("¿Cuántos DTEs desea ver? (por defecto 5): ").strip()
            try:
                limit = int(limit) if limit else 5
            except ValueError:
                limit = 5
            diagnosticar_ultimos_dtes(limit)
        
        elif opcion == '4':
            print_header("STOCK POR SUCURSAL - RESUMEN")
            
            sucursales = Sucursal.objects.all()
            
            for sucursal in sucursales:
                print(f"\n📍 {sucursal.alias} - {sucursal.empresa.nombre if sucursal.empresa else 'N/A'}")
                
                # Obtener productos con stock en esta sucursal
                productos_con_stock = []
                
                for pt in Producto_Talla.objects.select_related('producto').all()[:100]:  # Limitado a 100 para performance
                    stock = pt.stock_sucursal(sucursal.id)
                    if stock > 0:
                        productos_con_stock.append((pt, stock))
                
                if productos_con_stock:
                    print(f"   Total productos con stock: {len(productos_con_stock)}")
                    print(f"   {'SKU':12} {'Producto':40} {'Stock':>8}")
                    print("   " + "-" * 65)
                    
                    for pt, stock in productos_con_stock[:10]:  # Mostrar solo los primeros 10
                        nombre = pt.producto.articulo if pt.producto else 'N/A'
                        print(f"   {pt.sku:<12} {nombre[:40]:40} {stock:>8}")
                    
                    if len(productos_con_stock) > 10:
                        print(f"   ... y {len(productos_con_stock) - 10} más")
                else:
                    print("   ⚪ Sin productos con stock")
        
        elif opcion == '0':
            print("\n👋 ¡Hasta luego!")
            break
        
        else:
            print("❌ Opción inválida")
        
        input("\nPresione Enter para continuar...")


if __name__ == '__main__':
    try:
        menu_principal()
    except KeyboardInterrupt:
        print("\n\n👋 Diagnóstico interrumpido por el usuario")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()



