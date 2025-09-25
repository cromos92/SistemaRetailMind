#!/usr/bin/env python
"""
Script para configurar la empresa y sucursal del usuario administrador
"""
import os
import sys
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.contrib.auth import get_user_model
from app.models import Empresa, Sucursal, EmpresaUser

Usuario = get_user_model()

def setup_admin_empresa():
    try:
        # Buscar el usuario admin
        admin_user = Usuario.objects.get(username='admin')
        print(f"Usuario encontrado: {admin_user.username}")
        
        # Crear empresa si no existe
        empresa, created = Empresa.objects.get_or_create(
            rut='76.123.456-7',
            defaults={
                'nombre': 'RetailMind',
                'nombre_fantasia': 'RetailMind Sistema',
                'razon_social': 'RetailMind SpA',
                'giro': 'Desarrollo de Software',
                'direccion': 'Av. Principal 123',
                'comuna': 'Santiago',
                'ciudad': 'Santiago',
                'correoVendedor': 'ventas@retailmind.com',
                'correoIntercambio': 'intercambio@retailmind.com',
                'correoAdministrador': 'admin@retailmind.com',
                'esProveedor': False
            }
        )
        
        if created:
            print(f"Empresa creada: {empresa.nombre}")
        else:
            print(f"Empresa existente: {empresa.nombre}")
        
        # Crear sucursal si no existe
        sucursal, created = Sucursal.objects.get_or_create(
            empresa=empresa,
            alias='Principal',
            defaults={
                'direccion': 'Av. Principal 123, Santiago'
            }
        )
        
        if created:
            print(f"Sucursal creada: {sucursal.alias}")
        else:
            print(f"Sucursal existente: {sucursal.alias}")
        
        # Crear EmpresaUser si no existe
        empresa_user, created = EmpresaUser.objects.get_or_create(
            user=admin_user,
            defaults={
                'empresa': empresa,
                'sucursal': sucursal,
                'status': True,
                'active': True,
                'margenSobreprecio': 30,
                'margenPrecioVenta': 50
            }
        )
        
        if created:
            print(f"EmpresaUser creado para: {admin_user.username}")
        else:
            print(f"EmpresaUser existente para: {admin_user.username}")
            # Actualizar si ya existe
            empresa_user.empresa = empresa
            empresa_user.sucursal = sucursal
            empresa_user.active = True
            empresa_user.save()
            print("EmpresaUser actualizado")
        
        print("\n✅ Configuración completada exitosamente!")
        print(f"Usuario: {admin_user.username}")
        print(f"Empresa: {empresa.nombre}")
        print(f"Sucursal: {sucursal.alias}")
        
    except Usuario.DoesNotExist:
        print("❌ Error: Usuario 'admin' no encontrado")
        print("Ejecuta primero: python manage.py crear_superusuario_retailmind")
    except Exception as e:
        print(f"❌ Error: {str(e)}")

if __name__ == '__main__':
    setup_admin_empresa()
