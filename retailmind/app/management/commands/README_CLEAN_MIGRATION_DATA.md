# 🗑️ Comando de Limpieza de Datos de Migración

Este comando Django permite limpiar datos de migración de forma segura, respetando las dependencias de Foreign Keys y usando transacciones.

## 📋 Características

✅ **Orden correcto de eliminación**: Respeta las dependencias de Foreign Keys  
✅ **Transacciones atómicas**: Si algo falla, se revierte todo (rollback)  
✅ **Progreso detallado**: Muestra cantidad de registros y tiempo de eliminación  
✅ **Confirmación requerida**: Pide confirmación antes de eliminar (opcional)  
✅ **Protección de datos**: Solo elimina empresas donde `esProveedor=False`  
✅ **Estadísticas finales**: Muestra resumen completo y verificación  

## 🚀 Uso

### Uso básico (con confirmación)
```bash
python manage.py clean_migration_data
```

### Uso sin confirmación (modo forzado)
```bash
python manage.py clean_migration_data --force
```

## 📊 Orden de eliminación

El comando elimina datos en el siguiente orden (respetando dependencias FK):

1. **app_movimientos_producto** - Movimientos de productos
2. **app_dte_detalle_pago** - Detalles de pago de DTEs
3. **app_dte_productos** - Productos en DTEs (si existe)
4. **app_dte** - Documentos Tributarios Electrónicos
5. **app_ticket_productos** - Productos en tickets (si existe)
6. **app_ticket** - Tickets de venta
7. **app_producto_talla** - Variaciones de productos
8. **app_producto** - Productos
9. **app_atributopcion** - Opciones de atributos
10. **app_productos_atributos** - Atributos de productos
11. **app_categoria** - Categorías
12. **app_sucursal** - Sucursales
13. **app_empresa** - Empresas (solo donde `esProveedor=False`)

## ⚠️ Importante

- **Esta operación NO se puede deshacer**
- Se eliminarán **SOLO** las empresas donde `esProveedor=False`
- Las empresas principales (proveedores) se mantienen intactas
- Se usa una transacción atómica: si algo falla, TODO se revierte
- Se recomienda hacer un **backup de la base de datos** antes de ejecutar

## 🔒 Seguridad de Datos

**Este comando es 100% seguro para bases de datos externas:**

✅ **SOLO opera en la base de datos de Django** (configurada en `settings.py`)  
✅ **NUNCA toca MySQL** ni ninguna otra base de datos externa  
✅ **NO hace conexiones a Vicent** ni otros sistemas  
✅ **Solo usa Django ORM estándar** - No hay queries raw SQL a bases externas  

> **Nota**: A diferencia del comando `migrate_from_vicent.py` que SÍ conecta a MySQL para importar datos, este comando `clean_migration_data.py` SOLO elimina datos de tu base de datos Django local.

## 📖 Ejemplo de salida

```
================================================================================
🗑️  LIMPIEZA DE DATOS DE MIGRACIÓN
================================================================================

📊 PASO 1: Contando registros...

Registros a eliminar por tabla:

  • Movimientos_Producto........................      1,234 registros
  • Dte_Detalle_Pago............................        567 registros
  • Dte_Productos...............................        890 registros
  • Dte.........................................        123 registros
  • Ticket_Productos............................      2,345 registros
  • Ticket......................................        456 registros
  • Producto_Talla..............................      5,678 registros
  • Producto....................................      1,234 registros
  • AtributoOpcion..............................         45 registros
  • Productos_Atributos.........................         12 registros
  • Categoria...................................          8 registros
  • Sucursal....................................          3 registros
  • Empresa.....................................          1 registros

  TOTAL.........................................     12,596 registros

⚠️  ADVERTENCIA: Esta acción NO se puede deshacer.
Se eliminarán un total de 12,596 registros.

¿Desea continuar? Escriba "SI" para confirmar: SI

================================================================================
🗑️  PASO 2: Eliminando datos...
================================================================================

  ✅ [1/13] Movimientos_Producto: 1,234 registros eliminados en 0.45s
  ✅ [2/13] Dte_Detalle_Pago: 567 registros eliminados en 0.23s
  ✅ [3/13] Dte_Productos: 890 registros eliminados en 0.34s
  ✅ [4/13] Dte: 123 registros eliminados en 0.12s
  ✅ [5/13] Ticket_Productos: 2,345 registros eliminados en 0.78s
  ✅ [6/13] Ticket: 456 registros eliminados en 0.19s
  ✅ [7/13] Producto_Talla: 5,678 registros eliminados en 1.23s
  ✅ [8/13] Producto: 1,234 registros eliminados en 0.56s
  ✅ [9/13] AtributoOpcion: 45 registros eliminados en 0.05s
  ✅ [10/13] Productos_Atributos: 12 registros eliminados en 0.02s
  ✅ [11/13] Categoria: 8 registros eliminados en 0.01s
  ✅ [12/13] Sucursal: 3 registros eliminados en 0.01s
  ✅ [13/13] Empresa (esProveedor=False): 1 registros eliminados en 0.01s

================================================================================
📊 RESUMEN DE ELIMINACIÓN
================================================================================

Registros eliminados por tabla:

  • Movimientos_Producto............................      1,234 registros (  0.45s)
  • Dte_Detalle_Pago................................        567 registros (  0.23s)
  • Dte_Productos...................................        890 registros (  0.34s)
  • Dte.............................................        123 registros (  0.12s)
  • Ticket_Productos................................      2,345 registros (  0.78s)
  • Ticket..........................................        456 registros (  0.19s)
  • Producto_Talla..................................      5,678 registros (  1.23s)
  • Producto........................................      1,234 registros (  0.56s)
  • AtributoOpcion..................................         45 registros (  0.05s)
  • Productos_Atributos.............................         12 registros (  0.02s)
  • Categoria.......................................          8 registros (  0.01s)
  • Sucursal........................................          3 registros (  0.01s)
  • Empresa (esProveedor=False).....................          1 registros (  0.01s)

  TOTAL ELIMINADO...................................     12,596 registros
  TIEMPO TOTAL......................................       4.00 segundos

================================================================================
✅ LIMPIEZA COMPLETADA EXITOSAMENTE
================================================================================

🔍 Verificación final:

  • Movimientos_Producto: 0
  • Dte_Detalle_Pago: 0
  • Dte_Productos: 0
  • Dte: 0
  • Ticket_Productos: 0
  • Ticket: 0
  • Producto_Talla: 0
  • Producto: 0
  • AtributoOpcion: 0
  • Productos_Atributos: 0
  • Categoria: 0
  • Sucursal: 0
  • Empresa (total): 5
  • Empresa (esProveedor=False): 0
  • Empresa (esProveedor=True): 5
```

## 🔧 Opciones del comando

| Opción | Descripción |
|--------|-------------|
| `--force` | Fuerza la eliminación sin pedir confirmación |

## 🛡️ Seguridad

- Usa `@transaction.atomic()` para asegurar que toda la operación es atómica
- Si ocurre algún error, se hace rollback automático
- Solo elimina empresas donde `esProveedor=False`
- Mantiene intactas las empresas proveedoras

## 📝 Notas técnicas

- El comando está ubicado en: `app/management/commands/clean_migration_data.py`
- Usa el framework de Django Management Commands
- Compatible con PostgreSQL, MySQL y SQLite
- Muestra tiempos individuales y totales de ejecución
- Verifica tablas opcionales (Dte_Productos, Ticket_Productos)

## 🐛 Troubleshooting

### Error: "no such table: app_xxx"
Algunas tablas pueden no existir en tu base de datos. El comando las detecta automáticamente y las omite.

### Error: "FOREIGN KEY constraint failed"
Esto no debería ocurrir gracias al orden de eliminación. Si ocurre, reporta el issue.

### Error: "This operation cannot be undone"
Esto es una advertencia, no un error. Confirma con "SI" para continuar.

## 📞 Soporte

Si encuentras algún problema o necesitas ayuda:
1. Revisa que tienes backup de tu base de datos
2. Verifica los logs de Django
3. Contacta al equipo de desarrollo

---

**Creado por**: RetailMind Team  
**Última actualización**: 2025-11-19

