# ANÁLISIS COMPLETO DEL SISTEMA RETAILMIND
## Documentación Técnica para Migración de Datos

---

## ÍNDICE
1. [Resumen Ejecutivo](#resumen-ejecutivo)
2. [Arquitectura del Sistema](#arquitectura-del-sistema)
3. [Catálogo Completo de Modelos](#catálogo-completo-de-modelos)
4. [Análisis de Uso de Modelos](#análisis-de-uso-de-modelos)
5. [Diagrama de Relaciones](#diagrama-de-relaciones)
6. [Especificaciones Técnicas](#especificaciones-técnicas)

---

## RESUMEN EJECUTIVO

**Sistema:** RetailMind - Sistema de Gestión Retail Integral  
**Framework:** Django 4.x  
**Base de Datos:** PostgreSQL/SQLite  
**Total de Modelos:** 52 modelos principales + 3 modelos de gestión empresarial + 2 modelos de usuarios

### Módulos Principales
1. **Gestión Empresarial** - Empresas, Sucursales, Clientes, Proveedores
2. **Gestión de Usuarios** - Autenticación, permisos, roles
3. **Productos e Inventario** - Productos, tallas, categorías, atributos, FIFO
4. **Compras** - DTEs, recepciones, regularizaciones
5. **Ventas** - Tickets, POS, métodos de pago
6. **Documentos Tributarios** - Facturas, boletas, notas de crédito
7. **Movimientos de Inventario** - Traspasos, ajustes, movimientos
8. **Créditos a Trabajadores** - Préstamos, pagos, firmas
9. **POS Transbank** - Integración con terminales de pago
10. **Cambios y Devoluciones** - Gestión de cambios, devoluciones, pagos
11. **Cotizaciones** - Cotizaciones empresariales con historial
12. **Gestión de Precios** - Sistema de aprobación de cambios de precios
13. **Arqueo de Caja** - Cuadraturas, depósitos bancarios

---

## ARQUITECTURA DEL SISTEMA

### Estructura de Aplicaciones Django

```
retailmind/
├── users/                  # Gestión de usuarios
│   └── models.py          (2 modelos)
├── empresa_management/    # Gestión empresarial
│   └── models.py          (5 modelos)
└── app/                   # Aplicación principal
    └── models.py          (52 modelos)
```

---

## CATÁLOGO COMPLETO DE MODELOS

### 📦 MÓDULO: USERS (Gestión de Usuarios)

#### 1. Usuario (AbstractUser)
**Descripción:** Modelo de usuario personalizado con roles y permisos  
**Uso:** ✅ ACTIVO - Usado en todo el sistema

**Campos:**
- `rut`: CharField(12) - RUT único del usuario
- `telefono`: CharField(15) - Teléfono de contacto
- `direccion`: TextField - Dirección del usuario
- `fecha_nacimiento`: DateField - Fecha de nacimiento
- `empresa`: CharField(100) - Nombre de la empresa
- `cargo`: CharField(100) - Cargo del usuario
- `departamento`: CharField(100) - Departamento
- `rol`: CharField(50) - Rol del usuario (administrador, jefe_local, cajero, vendedor)
- `es_activo`: BooleanField - Usuario activo
- `fecha_creacion`: DateTimeField - Fecha de creación
- `fecha_ultimo_acceso`: DateTimeField - Último acceso
- `token_reset_password`: UUIDField - Token para reset de contraseña
- `fecha_token_reset`: DateTimeField - Fecha del token
- `puede_crear_usuarios`: BooleanField - Permiso para crear usuarios
- `puede_editar_usuarios`: BooleanField - Permiso para editar usuarios
- `puede_eliminar_usuarios`: BooleanField - Permiso para eliminar usuarios

**Relaciones:**
- Hereda de AbstractUser de Django (username, email, password, first_name, last_name, etc.)

**Métodos Importantes:**
- `validar_rut()`: Valida RUT chileno
- `generar_token_reset()`: Genera token para reset de contraseña
- `token_valido()`: Verifica si el token es válido
- `actualizar_ultimo_acceso()`: Actualiza fecha de último acceso

**Tipos de Datos Necesarios:**
- RUT validado formato chileno
- Roles: administrador, jefe_local, cajero, vendedor
- Email válido
- Password encriptado

---

#### 2. LogAcceso
**Descripción:** Registro de accesos de usuarios  
**Uso:** ⚠️ PARCIAL - Se usa para auditoría

**Campos:**
- `usuario`: FK(Usuario) - Usuario que accede
- `fecha_acceso`: DateTimeField - Fecha y hora del acceso
- `ip_address`: GenericIPAddressField - IP del acceso
- `user_agent`: TextField - User agent del navegador
- `exito`: BooleanField - Si el acceso fue exitoso

---

### 🏢 MÓDULO: EMPRESA_MANAGEMENT (Gestión Empresarial)

#### 3. Empresa
**Descripción:** Empresas del sistema (clientes, proveedores, propia empresa)  
**Uso:** ✅ ACTIVO - Modelo central del sistema

**Campos:**
- `nombre`: CharField(100) - Nombre de la empresa
- `rut`: CharField(20) - RUT con validación (formato: 12.345.678-9)
- `nombre_fantasia`: CharField(255) - Nombre de fantasía
- `razon_social`: CharField(255) - Razón social
- `giro`: CharField(255) - Giro comercial
- `direccion`: CharField(255) - Dirección
- `comuna`: CharField(100) - Comuna
- `ciudad`: CharField(100) - Ciudad
- `region`: CharField(100) - Región
- `codigo_postal`: CharField(10) - Código postal
- `telefono`: CharField(20) - Teléfono
- `email`: EmailField - Email
- `sitio_web`: URLField - Sitio web
- `tipo_empresa`: CharField(20) - CLIENTE, PROVEEDOR, CLIENTE_PROVEEDOR
- `esProveedor`: BooleanField - Si es proveedor (legacy)
- `correoVendedor`: CharField(100) - Email del vendedor
- `correoIntercambio`: CharField(100) - Email para intercambio
- `correoAdministrador`: CharField(100) - Email del administrador
- `fecha_creacion`: DateField - Fecha de creación
- `fecha_modificacion`: DateTimeField - Fecha de modificación
- `activo`: BooleanField - Si está activa
- `observaciones`: TextField - Observaciones
- `created_at`: DateTimeField - Timestamp de creación
- `updated_at`: DateTimeField - Timestamp de actualización
- `created_by`: FK(Usuario) - Usuario que creó
- `updated_by`: FK(Usuario) - Usuario que modificó

**Relaciones:**
- Relacionada con: Sucursales, Clientes, DTEs, Compras, Cotizaciones

**Validaciones:**
- RUT chileno válido con dígito verificador

---

#### 4. Sucursal
**Descripción:** Sucursales de las empresas  
**Uso:** ✅ ACTIVO - Usado en todo el sistema

**Campos:**
- `empresa`: FK(Empresa) - Empresa a la que pertenece
- `alias`: CharField(100) - Alias de la sucursal
- `nombre`: CharField(200) - Nombre completo
- `direccion`: CharField(255) - Dirección
- `comuna`: CharField(100) - Comuna
- `ciudad`: CharField(100) - Ciudad
- `telefono`: CharField(20) - Teléfono
- `email`: EmailField - Email
- `activa`: BooleanField - Si está activa
- `created_at`: DateTimeField - Fecha de creación
- `updated_at`: DateTimeField - Fecha de actualización

**Unique Together:** (empresa, alias)

---

#### 5. ContactoEmpresa
**Descripción:** Contactos de las empresas  
**Uso:** ⚠️ PARCIAL - Se usa ocasionalmente

**Campos:**
- `empresa`: FK(Empresa) - Empresa del contacto
- `nombre`: CharField(100) - Nombre del contacto
- `cargo`: CharField(100) - Cargo
- `email`: EmailField - Email
- `telefono`: CharField(20) - Teléfono
- `celular`: CharField(20) - Celular
- `tipo_contacto`: CharField(20) - PRINCIPAL, COMPRAS, VENTAS, ADMINISTRACION, FINANZAS, TECNICO, OTRO
- `activo`: BooleanField - Si está activo
- `created_at`: DateTimeField - Fecha de creación
- `updated_at`: DateTimeField - Fecha de actualización

---

#### 6. Cliente
**Descripción:** Clientes individuales (personas naturales)  
**Uso:** ✅ ACTIVO - Usado en ventas y tickets

**Campos:**
- `nombre`: CharField(100) - Nombre
- `apellido`: CharField(100) - Apellido
- `rut`: CharField(20) - RUT validado (opcional)
- `email`: EmailField - Email
- `telefono`: CharField(20) - Teléfono
- `celular`: CharField(20) - Celular
- `direccion`: CharField(255) - Dirección
- `comuna`: CharField(100) - Comuna
- `ciudad`: CharField(100) - Ciudad
- `fecha_nacimiento`: DateField - Fecha de nacimiento
- `genero`: CharField(10) - M, F, O
- `tipo_cliente`: CharField(20) - INDIVIDUAL, EMPRESARIAL, MAYORISTA, DISTRIBUIDOR
- `empresa`: FK(Empresa) - Empresa asociada (opcional)
- `activo`: BooleanField - Si está activo
- `observaciones`: TextField - Observaciones
- `created_at`: DateTimeField - Fecha de creación
- `updated_at`: DateTimeField - Fecha de actualización
- `created_by`: FK(Usuario) - Usuario que creó
- `updated_by`: FK(Usuario) - Usuario que modificó

**Property:** `nombre_completo` - Retorna nombre + apellido

---

#### 7. Proveedor
**Descripción:** Proveedores (extensión de Empresa)  
**Uso:** ✅ ACTIVO - Usado en compras

**Campos:**
- `empresa`: OneToOneField(Empresa) - Empresa base
- `codigo_proveedor`: CharField(50) - Código único
- `categoria`: CharField(100) - Categoría del proveedor
- `dias_credito`: IntegerField - Días de crédito (default: 30)
- `descuento_porcentaje`: DecimalField(5,2) - Descuento (%)
- `calificacion`: IntegerField - Calificación 1-5
- `observaciones_evaluacion`: TextField - Observaciones
- `activo`: BooleanField - Si está activo
- `created_at`: DateTimeField - Fecha de creación
- `updated_at`: DateTimeField - Fecha de actualización

---

#### 8. LogEmpresa
**Descripción:** Log de actividades de empresas  
**Uso:** ⚠️ PARCIAL - Auditoría

**Campos:**
- `empresa`: FK(Empresa) - Empresa
- `usuario`: FK(Usuario) - Usuario que realizó la acción
- `accion`: CharField(50) - Acción realizada
- `descripcion`: TextField - Descripción
- `datos_anteriores`: JSONField - Datos antes del cambio
- `datos_nuevos`: JSONField - Datos después del cambio
- `fecha`: DateTimeField - Fecha de la acción
- `ip_address`: GenericIPAddressField - IP del usuario
- `user_agent`: TextField - User agent

---

#### 9. LogCliente
**Descripción:** Log de actividades de clientes  
**Uso:** ⚠️ PARCIAL - Auditoría

**Campos:**
- Similar a LogEmpresa pero para clientes

---

### 👥 MÓDULO: APP - GESTIÓN DE USUARIOS Y PERMISOS

#### 10. EmpresaUser
**Descripción:** Relación entre usuarios, empresas y sucursales  
**Uso:** ✅ ACTIVO - Control de acceso

**Campos:**
- `empresa`: FK(Empresa) - Empresa
- `sucursal`: FK(Sucursal) - Sucursal (opcional)
- `user`: FK(Usuario) - Usuario
- `status`: BooleanField - Estado
- `active`: BooleanField - Activo
- `margenSobreprecio`: IntegerField - Margen de sobreprecio
- `margenPrecioVenta`: IntegerField - Margen de precio de venta

---

#### 11. Vendedor
**Descripción:** Vendedores del sistema  
**Uso:** ✅ ACTIVO - Usado en ventas, tickets, cotizaciones

**Campos:**
- `codigo_vendedor`: CharField(100) - Código único
- `rut`: CharField(100) - RUT
- `nombre`: CharField(100) - Nombre
- `comision`: DecimalField(5,2) - Comisión (%)
- `fecha_nacimiento`: DateField - Fecha de nacimiento
- `correo`: CharField(100) - Email

**Relaciones:**
- Relacionado con: Tickets, DTEs, Cotizaciones, Créditos

---

### 📄 MÓDULO: APP - DOCUMENTOS TRIBUTARIOS Y CORRELATIVOS

#### 12. Correlativo
**Descripción:** Correlativos para documentos tributarios  
**Uso:** ✅ ACTIVO - Crítico para emisión de documentos

**Campos:**
- `sucursal`: FK(Sucursal) - Sucursal
- `tipo_dte`: CharField(50) - Tipo de documento
- `inicio`: IntegerField - Número actual
- `termino`: IntegerField - Número final
- `fecha_actualizacion`: DateField - Fecha de actualización
- `alias`: CharField(100) - Alias
- `responsable`: CharField(50) - Responsable

**Properties:**
- `numero_actual`: Número actual
- `disponibles`: Números disponibles
- `consumidos`: Números consumidos
- `total_rango`: Total del rango
- `porcentaje_consumo`: Porcentaje consumido
- `estado`: agotado, critico, activo

**Métodos:**
- `puede_emitir()`: Verifica disponibilidad
- `obtener_siguiente_numero()`: Obtiene y actualiza

**Unique Together:** (sucursal, tipo_dte)

**Tipos de Documento:**
- FACTURA ELECTRONICA
- BOLETA ELECTRONICA
- GUIA (Guía de Despacho)
- NOTA DE PEDIDO
- NOTA DE CREDITO
- NOTA DE DEBITO
- FACTURA EXENTA
- COTIZACION
- COMPRA
- TICKET
- TRASPASO

---

#### 13. Dte
**Descripción:** Documentos Tributarios Electrónicos  
**Uso:** ✅ ACTIVO - Modelo central para facturación

**Campos:**
- `emisor`: FK(Empresa) - Empresa emisora
- `receptor`: FK(Empresa) - Empresa receptora
- `numero_documento`: IntegerField - Número del documento
- `tipo_documento`: CharField(20) - Tipo de documento (choices)
- `monto_con_iva`: DecimalField(12,2) - Monto con IVA
- `monto_neto`: DecimalField(12,2) - Monto neto
- `estado_pago`: CharField(20) - PENDIENTE, PAGADO, VENCIDO
- `estado_dte`: CharField(30) - Estado del DTE (EMITIDO, RECEPCIONADO_COMPLETO, etc.)
- `responsable`: CharField(100) - Responsable
- `fecha_emision`: DateField - Fecha de emisión
- `fecha_vencimiento`: DateField - Fecha de vencimiento
- `diasCredito`: IntegerField - Días de crédito
- `bultos`: IntegerField - Cantidad de bultos
- `unidades_productos`: IntegerField - Unidades totales
- `vendedor`: FK(Vendedor) - Vendedor (opcional)
- `descuento`: DecimalField(10,2) - Descuento
- `sucursal`: FK(Sucursal) - Sucursal
- `fecha_recepcion`: DateField - Fecha de recepción
- `hora`: TimeField - Hora
- `tipo_transaccion`: CharField(15) - COMPRA, VENTA, VENTA_PUBLICO, TRASPASO
- `referencias`: TextField - Referencias
- `es_nota_credito`: BooleanField - Si es nota de crédito
- `documento_afectado`: FK(Dte) - DTE original (para NC)
- `motivo_nc`: TextField - Motivo de la NC

**Métodos:**
- `es_misma_empresa_check()`: Verifica si emisor y receptor son iguales
- `requiere_nota_credito_check()`: Determina si requiere NC

**Estados de Pago:**
- PENDIENTE
- PAGADO
- VENCIDO

**Estados de DTE:**
- EMITIDO
- ACEPTADO
- RECEPCIONADO_COMPLETO
- RECEPCIONADO_PARCIAL
- EN_REGULARIZACION
- RECHAZADO
- ANULADO

---

#### 14. Dte_Detalle_Pago
**Descripción:** Pagos asociados a DTEs  
**Uso:** ✅ ACTIVO - Usado en compras

**Campos:**
- `dte`: FK(Dte) - DTE asociado
- `metodo_pago`: CharField(100) - Método de pago
- `tipo_tarjeta`: CharField(100) - Tipo de tarjeta
- `voucher`: CharField(50) - Voucher
- `monto`: IntegerField - Monto
- `notas`: TextField - Notas

---

#### 15. Dte_Productos
**Descripción:** Productos de un DTE  
**Uso:** ✅ ACTIVO - Detalle de DTEs

**Campos:**
- `dte`: FK(Dte) - DTE
- `productoTalla`: FK(Producto_Talla) - Producto talla
- `descripcion`: CharField(200) - Descripción
- `costo`: IntegerField - Costo
- `sobreprecio`: IntegerField - Sobreprecio
- `precio`: IntegerField - Precio
- `stock`: IntegerField - Cantidad
- `activo`: BooleanField - Activo

---

### 🏷️ MÓDULO: APP - PRODUCTOS Y ATRIBUTOS

#### 16. Productos_Atributos
**Descripción:** Definición de atributos de productos  
**Uso:** ✅ ACTIVO - Marca, Color, Género, etc.

**Campos:**
- `nombre`: CharField(100) - Nombre del atributo (ej: Marca, Color)
- `descripcion`: CharField(250) - Descripción
- `fecha_actualizacion`: DateField - Fecha de actualización

**Ejemplos:** Marca, Color, Género, Material

---

#### 17. AtributoOpcion
**Descripción:** Opciones de cada atributo  
**Uso:** ✅ ACTIVO - Valores de atributos

**Campos:**
- `atributo`: FK(Productos_Atributos) - Atributo padre
- `valor`: CharField(100) - Valor (ej: Nike, Adidas, Rojo, Azul)

**Ejemplos:**
- Atributo: Marca → Opciones: Nike, Adidas, Puma
- Atributo: Color → Opciones: Rojo, Azul, Negro
- Atributo: Género → Opciones: Hombre, Mujer, Unisex

---

#### 18. ProductoAtributoValor
**Descripción:** Relación entre productos y sus atributos  
**Uso:** ⚠️ PARCIAL - Sistema alternativo (no muy usado)

**Campos:**
- `producto`: FK(Producto) - Producto
- `atributo`: FK(Productos_Atributos) - Atributo
- `opcion`: FK(AtributoOpcion) - Opción seleccionada

---

#### 19. Categoria
**Descripción:** Categorías de productos (jerárquicas)  
**Uso:** ✅ ACTIVO - Clasificación de productos

**Campos:**
- `nombre`: CharField(100) - Nombre de la categoría
- `padre`: FK(Categoria) - Categoría padre (self-reference)

**Métodos:**
- `es_raiz()`: Verifica si es categoría raíz

**Ejemplo:**
- Calzado
  - Calzado Deportivo
  - Calzado Casual
- Ropa
  - Poleras
  - Pantalones

---

#### 20. GuiaTalla
**Descripción:** Guías de tallas por marca  
**Uso:** ✅ ACTIVO - Conversión de tallas

**Campos:**
- `marca`: FK(AtributoOpcion) - Marca
- `nombre`: CharField(100) - Nombre de la guía
- `orden`: IntegerField - Orden
- `fecha_creacion`: DateTimeField - Fecha de creación
- `productos`: ManyToMany(Producto) - Productos asociados

---

#### 21. GuiaTallaItem
**Descripción:** Items de una guía de tallas  
**Uso:** ✅ ACTIVO - Conversión de tallas

**Campos:**
- `guia`: FK(GuiaTalla) - Guía
- `cl`: CharField(20) - Talla CL
- `us`: CharField(20) - Talla US
- `eu`: CharField(20) - Talla EU
- `uk`: CharField(20) - Talla UK
- `br`: CharField(20) - Talla BR
- `cm`: CharField(20) - Centímetros
- `orden`: IntegerField - Orden

**Ejemplo:**
```
CL: 38 | US: 7 | EU: 39 | CM: 24.5
```

---

#### 22. GuiaTallaProducto
**Descripción:** Relación entre guía de tallas y productos  
**Uso:** ✅ ACTIVO - Intermediaria

**Campos:**
- `guia`: FK(GuiaTalla)
- `producto`: FK(Producto)
- `fecha_asociacion`: DateTimeField

**Unique Together:** (guia, producto)

---

#### 23. Producto
**Descripción:** Productos del sistema (producto base)  
**Uso:** ✅ ACTIVO - Modelo central de inventario

**Campos:**
- `articulo`: CharField(200) - Nombre del artículo
- `descripcion`: CharField(250) - Descripción
- `atributo1`: FK(AtributoOpcion) - Marca (generalmente)
- `atributo2`: FK(AtributoOpcion) - Color (generalmente)
- `atributo3`: FK(AtributoOpcion) - Género (generalmente)
- `atributo4`: FK(AtributoOpcion) - Otro atributo
- `categoria`: FK(Categoria) - Categoría
- `sucursal`: FK(Sucursal) - Sucursal
- `costo`: IntegerField - Costo
- `sobreprecio`: IntegerField - Sobreprecio
- `precioventa`: IntegerField - Precio de venta
- `precioSugerido`: IntegerField - Precio sugerido
- `tipo_talla`: CharField(5) - CL, US, EU, UK, BR, CM
- `guia_talla`: FK(GuiaTalla) - Guía de tallas asociada

**Nota:** Un producto es la combinación de artículo + atributos + sucursal

---

#### 24. Producto_Talla
**Descripción:** Variantes de un producto por talla (SKU)  
**Uso:** ✅ ACTIVO - Stock por talla

**Campos:**
- `producto`: FK(Producto) - Producto base
- `sku`: IntegerField - SKU único
- `stock`: IntegerField - Stock actual
- `talla`: CharField(50) - Talla

**Nota:** Este es el nivel donde se maneja el stock real

---

### 📦 MÓDULO: APP - COMPRAS Y RECEPCIONES

#### 25. Compras
**Descripción:** Compras a proveedores  
**Uso:** ✅ ACTIVO - Gestión de compras

**Campos:**
- `empresa`: FK(Empresa) - Proveedor
- `nombre`: CharField(200) - Nombre de la compra
- `correlativo`: IntegerField - Correlativo
- `responsable`: CharField(50) - Responsable
- `temporada`: CharField(50) - Temporada
- `fecha`: DateField - Fecha
- `fechaInicioTemporada`: DateField - Inicio de temporada
- `fechaTerminoTemporada`: DateField - Término de temporada

---

#### 26. Compras_Producto
**Descripción:** Productos de una compra  
**Uso:** ✅ ACTIVO - Detalle de compras

**Campos:**
- `compras`: FK(Compras) - Compra
- `nombre`: CharField(200) - Nombre
- `descripcion`: CharField(200) - Descripción
- `atributo1`: CharField(200) - Marca
- `atributo2`: CharField(200) - Color
- `atributo3`: CharField(200) - Género
- `atributo4`: CharField(200) - Otro
- `tipo_talla`: CharField(5) - Tipo de talla
- `costo`: IntegerField - Costo
- `precioSugerido`: IntegerField - Precio sugerido
- `fecha`: DateField - Fecha

---

#### 27. Compras_Producto_Talla
**Descripción:** Tallas de productos de compra  
**Uso:** ✅ ACTIVO - Stock por talla en compras

**Campos:**
- `compra_producto`: FK(Compras_Producto) - Producto de compra
- `stock`: IntegerField - Cantidad
- `talla`: CharField(50) - Talla

---

#### 28. Productos_Recepcionados
**Descripción:** Recepciones de productos (compras y traspasos)  
**Uso:** ✅ ACTIVO - Recepciones con problemas

**Campos:**
- `compra_producto_talla`: FK(Compras_Producto_Talla) - Para compras (legacy)
- `dte`: FK(Dte) - DTE de traspaso interno
- `dte_producto`: FK(Dte_Productos) - Producto del DTE
- `producto_talla`: FK(Producto_Talla) - Producto talla
- `stockArribado`: IntegerField - Cantidad recepcionada
- `cantidad_esperada`: IntegerField - Cantidad esperada
- `cantidad_danada`: IntegerField - Cantidad dañada
- `cantidad_faltante`: IntegerField - Cantidad faltante
- `estado`: CharField(30) - Estado de recepción
- `observaciones`: TextField - Observaciones
- `fecha`: DateField - Fecha
- `fecha_recepcion`: DateTimeField - Fecha de recepción
- `recepcionado_por`: CharField(100) - Usuario que recepcionó
- `fecha_regularizacion`: DateTimeField - Fecha de regularización
- `regularizado_por`: CharField(100) - Usuario que regularizó

**Estados:**
- PENDIENTE
- RECEPCIONADO_OK
- RECEPCIONADO_PARCIAL
- RECEPCIONADO_DANADO
- FALTANTE
- EN_REGULARIZACION
- EN_SOLICITUD_REGULARIZACION
- REGULARIZADO

**Properties:**
- `tiene_problemas`
- `esta_ok`
- `es_recepcion_traspaso`
- `es_recepcion_compra`

---

#### 29. Solicitud_Regularizacion
**Descripción:** Solicitudes de regularización entre empresas  
**Uso:** ✅ ACTIVO - Sistema de regularización

**Campos:**
- `numero_solicitud`: CharField(20) - Número único (ej: SOL-001)
- `fecha_solicitud`: DateTimeField - Fecha de solicitud
- `dte_original`: FK(Dte) - DTE con problema
- `producto_recepcionado`: FK(Productos_Recepcionados) - Producto con problema
- `sucursal_solicitante`: FK(Sucursal) - Receptor del DTE
- `sucursal_emisora`: FK(Sucursal) - Emisor del DTE
- `usuario_solicita`: CharField(100) - Usuario solicitante
- `tipo_problema`: CharField(50) - FALTANTE, DANADO, PARCIAL, INCORRECTO
- `cantidad_problema`: IntegerField - Cantidad con problema
- `descripcion_problema`: TextField - Descripción
- `evidencia_foto`: FileField - Foto de evidencia
- `tipo_solucion_solicitada`: CharField(50) - Tipo de solución
- `producto_cambio_solicitado`: FK(Producto_Talla) - Producto de cambio
- `cantidad_cambio_solicitada`: IntegerField - Cantidad solicitada
- `estado`: CharField(50) - Estado de la solicitud
- `fecha_revision`: DateTimeField - Fecha de revisión
- `usuario_revisa`: CharField(100) - Usuario que revisa
- `decision_emisor`: TextField - Decisión del emisor
- `tipo_solucion_aprobada`: CharField(50) - Solución aprobada
- `producto_cambio_aprobado`: FK(Producto_Talla) - Producto aprobado
- `cantidad_cambio_aprobada`: IntegerField - Cantidad aprobada
- `fecha_ejecucion`: DateTimeField - Fecha de ejecución
- `dte_solucion`: FK(Dte) - DTE de solución
- `nota_credito`: FK(Dte) - NC emitida
- `fecha_confirmacion`: DateTimeField - Fecha de confirmación
- `usuario_confirma`: CharField(100) - Usuario que confirma
- `conformidad`: BooleanField - Conformidad
- `observaciones_finales`: TextField - Observaciones finales

**Estados:**
- PENDIENTE
- EN_REVISION
- APROBADA
- RECHAZADA
- EJECUTADA
- COMPLETADA
- CANCELADA

**Tipos de Problema:**
- FALTANTE
- DANADO
- PARCIAL
- INCORRECTO

**Tipos de Solución:**
- NOTA_CREDITO
- REENVIO
- CAMBIO_PRODUCTO
- AJUSTE_CANTIDAD

---

### 🛒 MÓDULO: APP - VENTAS Y TICKETS

#### 30. Ticket
**Descripción:** Tickets de venta (ventas al público)  
**Uso:** ✅ ACTIVO - Modelo principal de ventas

**Campos:**
- `vendedor`: FK(Vendedor) - Vendedor
- `sucursal`: FK(Sucursal) - Sucursal
- `correlativo`: IntegerField - Correlativo
- `estado`: CharField(20) - PENDIENTE, PAGADO, ANULADO, DEVUELTO
- `subTotal`: IntegerField - Subtotal
- `descuento`: IntegerField - Descuento
- `total`: IntegerField - Total
- `fecha`: DateField - Fecha
- `hora`: TimeField - Hora
- `responsable`: CharField(50) - Responsable
- `cliente_nombre`: CharField(200) - Nombre del cliente
- `cliente_rut`: CharField(20) - RUT del cliente
- `cliente_email`: EmailField - Email
- `cliente_telefono`: CharField(20) - Teléfono
- `cliente_giro`: CharField(255) - Giro
- `cliente_comuna`: CharField(100) - Comuna
- `cliente_ciudad`: CharField(100) - Ciudad
- `cliente_direccion`: CharField(255) - Dirección
- `cliente_telefono_secundario`: CharField(20) - Teléfono secundario
- `cliente_email_facturacion`: EmailField - Email de facturación
- `metodo_pago`: CharField(50) - Método de pago principal
- `observaciones`: TextField - Observaciones
- `observaciones_adicionales`: TextField - Observaciones adicionales
- `created_at`: DateTimeField - Fecha de creación
- `updated_at`: DateTimeField - Fecha de actualización
- `modulo_origen`: CharField(20) - VENTA_PUBLICO, VENTA_MAYORISTA, POS

**Properties:**
- `total_pagado`: Total pagado
- `saldo_por_pagar`: Saldo pendiente

**Unique Together:** (sucursal, correlativo)

**Métodos de Pago:**
- EFECTIVO
- TARJETA_DEBITO
- TARJETA_CREDITO
- TRANSFERENCIA
- CHEQUE
- OTRO
- TBK_POS_INTEGRADO
- TBK_MANUAL
- TBK_DEBITO_POS
- TBK_CREDITO_POS
- TBK_PREPAGO_POS
- TARJETA_COMERCIAL
- VENTA_INTERNET
- ORDEN_COMPRA
- CREDITO_TRABAJADOR
- CREDITO_EXTERNO
- CONVENIO
- MULTIPLE

---

#### 31. Ticket_Productos
**Descripción:** Productos de un ticket  
**Uso:** ✅ ACTIVO - Detalle de tickets

**Campos:**
- `ProductoTalla`: FK(Producto_Talla) - Producto talla
- `idTicket`: FK(Ticket) - Ticket
- `stock`: IntegerField - Cantidad
- `precio`: IntegerField - Precio
- `descuento_unitario`: IntegerField - Descuento por unidad
- `subtotal`: IntegerField - Subtotal
- `precio_original`: IntegerField - Precio antes de descuentos
- `porcentaje_descuento`: DecimalField(5,2) - Porcentaje de descuento
- `costo_fifo`: IntegerField - Costo FIFO
- `lotes_utilizados`: TextField - JSON de lotes

**Unique Together:** (ProductoTalla, idTicket)

---

#### 32. TicketDetallePago
**Descripción:** Pagos de un ticket (pagos múltiples)  
**Uso:** ✅ ACTIVO - Pagos combinados

**Campos:**
- `ticket`: FK(Ticket) - Ticket
- `metodo_pago`: CharField(50) - Método de pago
- `tipo_tarjeta`: CharField(100) - Tipo de tarjeta
- `voucher`: CharField(100) - Voucher
- `monto`: IntegerField - Monto
- `notas`: TextField - Notas
- `creado_en`: DateTimeField - Fecha de creación
- `actualizado_en`: DateTimeField - Fecha de actualización

---

### 🔄 MÓDULO: APP - MOVIMIENTOS DE INVENTARIO

#### 33. Movimientos_Producto
**Descripción:** Movimientos de inventario (kardex)  
**Uso:** ✅ ACTIVO - Trazabilidad de inventario

**Campos:**
- `dte`: FK(Dte) - DTE asociado (opcional)
- `ticket`: FK(Ticket) - Ticket asociado (opcional)
- `ProductoTalla`: FK(Producto_Talla) - Producto talla
- `sucursal_origen`: FK(Sucursal) - Sucursal origen
- `sucursal_destino`: FK(Sucursal) - Sucursal destino
- `cantidad`: IntegerField - Cantidad (positiva/negativa)
- `costo`: IntegerField - Costo
- `sobreprecio`: IntegerField - Sobreprecio
- `precio`: IntegerField - Precio
- `fecha`: DateField - Fecha
- `hora`: TimeField - Hora
- `concepto`: CharField(50) - Concepto del movimiento
- `tipo_movimiento`: CharField(50) - INGRESO, EGRESO, TRASPASO, AJUSTE
- `estado`: CharField(20) - Estado
- `responsable`: CharField(50) - Responsable
- `aprobado_por`: CharField(50) - Aprobador
- `fecha_aprobacion`: DateTimeField - Fecha de aprobación
- `observaciones`: TextField - Observaciones
- `referencia_externa`: CharField(100) - Referencia externa
- `created_at`: DateTimeField - Fecha de creación
- `updated_at`: DateTimeField - Fecha de actualización

**Tipos de Movimiento:**
- INGRESO
- EGRESO
- TRASPASO
- AJUSTE
- DEVOLUCION
- PERDIDA
- DONACION

**Conceptos:**
- **Ingresos:** INGRESO_INICIAL, RECEPCION_COMPRA, DEVOLUCION_CLIENTE, TRASPASO_ENTRADA, REGULARIZACION_TRASPASO, AJUSTE_POSITIVO, DONACION_RECIBIDA
- **Egresos:** VENTA_PUBLICO, VENTA_MAYORISTA, TRASPASO_SALIDA, AJUSTE_NEGATIVO, PERDIDA_ROBO, PERDIDA_DETERIORO, DONACION_ENTREGADA, DEVOLUCION_PROVEEDOR
- **Traspasos:** TRASPASO_SUCURSAL, TRASPASO_BODEGA, TRASPASO_VITRINA, CAMBIO_PRODUCTO_SALIDA, CAMBIO_PRODUCTO_ENTRADA

**Estados:**
- PENDIENTE
- PENDIENTE_RECEPCION
- APROBADO
- RECHAZADO
- ANULADO
- COMPLETADO

---

#### 34. Traspaso
**Descripción:** Traspasos entre sucursales  
**Uso:** ✅ ACTIVO - Traspasos internos

**Campos:**
- `sucursal_origen`: FK(Sucursal) - Origen
- `sucursal_destino`: FK(Sucursal) - Destino
- `numero_traspaso`: IntegerField - Número
- `fecha_solicitud`: DateField - Fecha de solicitud
- `fecha_aprobacion`: DateField - Fecha de aprobación
- `fecha_recepcion`: DateField - Fecha de recepción
- `estado`: CharField(20) - PENDIENTE, APROBADO, EN_TRANSITO, RECIBIDO, RECHAZADO, ANULADO
- `solicitante`: CharField(50) - Solicitante
- `aprobador`: CharField(50) - Aprobador
- `receptor`: CharField(50) - Receptor
- `observaciones_solicitud`: TextField
- `observaciones_aprobacion`: TextField
- `observaciones_recepcion`: TextField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Unique Together:** (sucursal_origen, numero_traspaso)

---

#### 35. Traspaso_Detalle
**Descripción:** Detalle de productos en traspaso  
**Uso:** ✅ ACTIVO - Detalle de traspasos

**Campos:**
- `traspaso`: FK(Traspaso)
- `producto_talla`: FK(Producto_Talla)
- `cantidad_solicitada`: IntegerField
- `cantidad_aprobada`: IntegerField
- `cantidad_enviada`: IntegerField
- `cantidad_recibida`: IntegerField
- `costo`: IntegerField
- `precio_venta`: IntegerField
- `observaciones`: TextField

**Unique Together:** (traspaso, producto_talla)

---

#### 36. AjusteInventario
**Descripción:** Ajustes de inventario  
**Uso:** ✅ ACTIVO - Ajustes de stock

**Campos:**
- `sucursal`: FK(Sucursal)
- `numero_ajuste`: IntegerField
- `fecha_ajuste`: DateField
- `tipo_ajuste`: CharField(20) - POSITIVO, NEGATIVO, INVENTARIO_FISICO
- `estado`: CharField(20) - PENDIENTE, APROBADO, RECHAZADO, COMPLETADO
- `solicitante`: CharField(50)
- `aprobador`: CharField(50)
- `motivo`: TextField
- `observaciones`: TextField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Unique Together:** (sucursal, numero_ajuste)

---

#### 37. AjusteInventario_Detalle
**Descripción:** Detalle de ajustes de inventario  
**Uso:** ✅ ACTIVO - Detalle de ajustes

**Campos:**
- `ajuste`: FK(AjusteInventario)
- `producto_talla`: FK(Producto_Talla)
- `stock_sistema`: IntegerField
- `stock_fisico`: IntegerField
- `diferencia`: IntegerField
- `costo`: IntegerField
- `precio_venta`: IntegerField
- `observaciones`: TextField

**Unique Together:** (ajuste, producto_talla)

---

#### 38. LoteProducto
**Descripción:** Lotes FIFO para control de costos  
**Uso:** ✅ ACTIVO - Sistema FIFO

**Campos:**
- `producto_talla`: FK(Producto_Talla)
- `dte`: FK(Dte) - DTE origen
- `movimiento`: FK(Movimientos_Producto)
- `cantidad_inicial`: IntegerField
- `cantidad_disponible`: IntegerField
- `costo_unitario`: IntegerField
- `sobreprecio_unitario`: IntegerField
- `precio_venta_unitario`: IntegerField
- `fecha_ingreso`: DateTimeField
- `fecha_vencimiento`: DateField - Para perecederos
- `activo`: BooleanField
- `agotado`: BooleanField
- `observaciones`: TextField
- `numero_lote`: CharField(50) - Lote del proveedor
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Properties:**
- `valor_disponible`: Valor total del lote
- `porcentaje_consumido`: % consumido

---

### 💰 MÓDULO: APP - ARQUEO Y CUADRATURA

#### 39. ArqueoCaja
**Descripción:** Arqueos de caja diarios  
**Uso:** ✅ ACTIVO - Cuadratura de caja

**Campos:**
- `fecha_arqueo`: DateField
- `sucursal`: FK(Sucursal)
- `usuario_responsable`: FK(Usuario)

**Totales Teóricos (calculados):**
- `total_hites_teorico`: IntegerField
- `total_tarjetas_comerciales_teorico`: IntegerField
- `total_efectivo_teorico`: IntegerField
- `total_falabella_teorico`: IntegerField
- `total_paris_teorico`: IntegerField
- `total_ripley_teorico`: IntegerField
- `total_mercadopago_teorico`: IntegerField
- `total_klap_teorico`: IntegerField
- `total_venta_internet_teorico`: IntegerField
- `total_tarjeta_debito_teorico`: IntegerField
- `total_tarjeta_credito_teorico`: IntegerField
- `total_transbank_teorico`: IntegerField
- `total_transferencia_teorico`: IntegerField
- `total_cheque_teorico`: IntegerField
- `total_convenio_teorico`: IntegerField
- `total_credito_trabajador_teorico`: IntegerField
- `total_tickets_teorico`: IntegerField
- `total_boletas_electronicas_teorico`: IntegerField
- `total_facturas_teorico`: IntegerField
- `total_facturas_exentas_teorico`: IntegerField
- `total_notas_credito_teorico`: IntegerField
- `cantidad_tickets`: IntegerField
- `cantidad_boletas_electronicas`: IntegerField
- `cantidad_facturas`: IntegerField
- `cantidad_facturas_exentas`: IntegerField
- `venta_total_teorica`: IntegerField

**Conteo Físico (solo efectivo):**
- `billetes_20000`: IntegerField
- `billetes_10000`: IntegerField
- `billetes_5000`: IntegerField
- `billetes_2000`: IntegerField
- `billetes_1000`: IntegerField
- `monedas_500`: IntegerField
- `monedas_100`: IntegerField
- `monedas_50`: IntegerField
- `monedas_10`: IntegerField
- `monedas_5`: IntegerField
- `monedas_1`: IntegerField
- `total_efectivo_fisico`: IntegerField (calculado)

**Diferencias:**
- `diferencia_efectivo`: IntegerField (físico - teórico)
- `cierre_pos_fisico`: IntegerField
- `numero_lote_pos`: CharField(50)
- `diferencia_transbank`: IntegerField

**Control:**
- `estado`: CharField(20) - ABIERTO, CERRADO, CON_DIFERENCIAS, REVISADO
- `observaciones`: TextField
- `observaciones_diferencia`: TextField
- `supervisor_revision`: FK(Usuario)
- `fecha_revision`: DateTimeField
- `observaciones_supervisor`: TextField
- `fecha_creacion`: DateTimeField
- `fecha_cierre`: DateTimeField
- `fecha_actualizacion`: DateTimeField

**Properties:**
- `tiene_diferencias`
- `diferencia_absoluta`
- `tipo_diferencia`: SOBRANTE, FALTANTE, EXACTO
- `porcentaje_diferencia`
- `requiere_supervision`

**Unique Together:** (fecha_arqueo, sucursal)

---

#### 40. DepositoBancario
**Descripción:** Depósitos bancarios  
**Uso:** ✅ ACTIVO - Registro de depósitos

**Campos:**
- `arqueo`: FK(ArqueoCaja)
- `fecha_deposito`: DateField
- `monto`: IntegerField
- `banco`: CharField(20) - ESTADO, CHILE, SANTANDER, BCI, SCOTIABANK, ITAU, SECURITY, FALABELLA, RIPLEY, OTRO
- `numero_comprobante`: CharField(50)
- `observaciones`: TextField
- `registrado_por`: FK(Usuario)
- `fecha_registro`: DateTimeField

---

### 💳 MÓDULO: APP - CRÉDITOS A TRABAJADORES

#### 41. CreditoTrabajador
**Descripción:** Créditos otorgados a trabajadores  
**Uso:** ✅ ACTIVO - Préstamos a empleados

**Campos:**
- `trabajador`: FK(Vendedor)
- `empresa_origen`: FK(Empresa)
- `sucursal`: FK(Sucursal)
- `numero_credito`: CharField(50) - Único (formato: CR-2025-0001)
- `tipo_credito`: CharField(20) - ANTICIPO_SUELDO, PRESTAMO_EMPRESA, CREDITO_COMPRA, EMERGENCIA, OTRO
- `monto_solicitado`: DecimalField(12,2)
- `monto_aprobado`: DecimalField(12,2)
- `monto_pagado`: DecimalField(12,2)
- `fecha_solicitud`: DateTimeField
- `fecha_aprobacion`: DateTimeField
- `fecha_vencimiento`: DateField
- `fecha_primer_pago`: DateField
- `estado`: CharField(20) - PENDIENTE, APROBADO, ACTIVO, PAGADO, VENCIDO, CANCELADO, RECHAZADO
- `autorizado_por`: FK(Usuario)
- `solicitado_por`: FK(Usuario)
- `tasa_interes`: DecimalField(5,2) - % mensual
- `numero_cuotas`: IntegerField
- `valor_cuota`: DecimalField(12,2) - Calculado
- `motivo_solicitud`: TextField
- `observaciones_solicitud`: TextField
- `observaciones_aprobacion`: TextField
- `observaciones_rechazo`: TextField
- `requiere_aval`: BooleanField
- `aval_nombre`: CharField(200)
- `aval_rut`: CharField(20)
- `aval_telefono`: CharField(20)
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Properties:**
- `saldo_pendiente`
- `porcentaje_pagado`
- `esta_vencido`
- `dias_para_vencimiento`

**Métodos:**
- `aprobar_credito()`
- `rechazar_credito()`
- `activar_credito()`

---

#### 42. PagoCreditoTrabajador
**Descripción:** Pagos/abonos a créditos  
**Uso:** ✅ ACTIVO - Pagos de créditos

**Campos:**
- `credito`: FK(CreditoTrabajador)
- `numero_pago`: CharField(50) - Formato: CR-2025-0001-P01
- `monto_pago`: DecimalField(12,2)
- `fecha_pago`: DateField
- `metodo_pago`: CharField(50)
- `numero_cuota`: IntegerField
- `es_pago_total`: BooleanField
- `referencia_pago`: CharField(100)
- `registrado_por`: FK(Usuario)
- `observaciones`: TextField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

---

#### 43. FirmaCreditoTrabajador
**Descripción:** Firmas digitales de créditos  
**Uso:** ✅ ACTIVO - Firmas de documentos

**Campos:**
- `credito`: OneToOne(CreditoTrabajador)
- `firmado_por_trabajador`: BooleanField
- `fecha_firma_trabajador`: DateTimeField
- `firma_trabajador_data`: TextField - Datos de firma digital
- `firmado_por_autorizador`: BooleanField
- `fecha_firma_autorizador`: DateTimeField
- `firma_autorizador_data`: TextField
- `firmado_por_aval`: BooleanField
- `fecha_firma_aval`: DateTimeField
- `firma_aval_data`: TextField
- `ip_firma_trabajador`: GenericIPAddressField
- `ip_firma_autorizador`: GenericIPAddressField
- `ip_firma_aval`: GenericIPAddressField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Property:**
- `esta_completamente_firmado`

**Métodos:**
- `registrar_firma_trabajador()`
- `registrar_firma_autorizador()`
- `registrar_firma_aval()`

---

### 💳 MÓDULO: APP - POS TRANSBANK

#### 44. ConfiguracionPOS
**Descripción:** Configuración de terminales POS por sucursal  
**Uso:** ✅ ACTIVO - Integración Transbank

**Campos:**
- `sucursal`: FK(Sucursal)
- `nombre`: CharField(100)
- `tipo_pos`: CharField(20) - VERIFONE_VX520, INGENICO_3500, INGENICO_DESK, OTRO
- `puerto_conexion`: CharField(20) - COM1, /dev/ttyUSB0, etc.
- `velocidad_conexion`: IntegerField - Default: 115200
- `activo`: BooleanField
- `es_principal`: BooleanField
- `timeout_conexion`: IntegerField - Default: 30 seg
- `numero_serie`: CharField(50)
- `version_firmware`: CharField(20)
- `ultima_conexion`: DateTimeField
- `estado_conexion`: CharField(20) - CONECTADO, DESCONECTADO, DETECTADO, ERROR, NO_PROBADO
- `observaciones`: TextField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Unique Together:** (sucursal, nombre)

---

#### 45. TransaccionPOS
**Descripción:** Transacciones POS para auditoría  
**Uso:** ✅ ACTIVO - Registro de transacciones

**Campos:**
- `configuracion_pos`: FK(ConfiguracionPOS)
- `ticket`: FK(Ticket)
- `detalle_pago`: FK(TicketDetallePago)
- `ticket_pos`: CharField(50) - Único (formato: POS-1-20250101120000-001)
- `monto`: DecimalField(12,2)
- `tipo_transaccion`: CharField(20) - VENTA, ANULACION, DEVOLUCION
- `estado`: CharField(20) - INICIADA, ESPERANDO_TARJETA, PROCESANDO, APROBADA, RECHAZADA, ANULADA, ERROR, TIMEOUT
- `fecha_inicio`: DateTimeField
- `fecha_completada`: DateTimeField
- `codigo_respuesta`: CharField(10)
- `mensaje_respuesta`: CharField(200)
- `codigo_autorizacion`: CharField(20)
- `tipo_tarjeta`: CharField(20) - DEBITO, CREDITO, PREPAGO, DESCONOCIDO
- `ultimos_4_digitos`: CharField(4)
- `nombre_tarjeta`: CharField(50) - VISA, MASTERCARD, etc.
- `numero_operacion`: CharField(20)
- `numero_cuotas`: IntegerField
- `codigo_comercio`: CharField(20)
- `terminal_id`: CharField(20)
- `ip_origen`: GenericIPAddressField
- `usuario_operador`: FK(Usuario)
- `observaciones`: TextField
- `error_detalle`: TextField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Properties:**
- `duracion_transaccion`
- `es_exitosa`
- `puede_anular`

---

#### 46. LogPOS
**Descripción:** Log de comunicación con POS  
**Uso:** ✅ ACTIVO - Debugging

**Campos:**
- `configuracion_pos`: FK(ConfiguracionPOS)
- `transaccion_pos`: FK(TransaccionPOS)
- `tipo_evento`: CharField(20) - CONEXION, DESCONEXION, COMANDO_ENVIADO, RESPUESTA_RECIBIDA, ERROR, TIMEOUT, INFO
- `mensaje`: TextField
- `datos_tecnicos`: JSONField
- `timestamp`: DateTimeField

---

### 🔄 MÓDULO: APP - CAMBIOS Y DEVOLUCIONES

#### 47. CambioDevolucion
**Descripción:** Cambios y devoluciones de productos  
**Uso:** ✅ ACTIVO - Gestión de cambios

**Campos:**
- `ticket_original`: FK(Ticket)
- `ticket_nuevo`: FK(Ticket)
- `sucursal`: FK(Sucursal)
- `numero_operacion`: CharField(50) - Único (formato: CD-1-202501-0001)
- `tipo_operacion`: CharField(30) - CAMBIO_SIMPLE, CAMBIO_CON_DIFERENCIA, DEVOLUCION_TOTAL, DEVOLUCION_PARCIAL
- `estado`: CharField(20) - SOLICITADO, EN_PROCESO, APROBADO, COMPLETADO, RECHAZADO, CANCELADO
- `fecha_solicitud`: DateTimeField
- `fecha_aprobacion`: DateTimeField
- `fecha_completado`: DateTimeField
- `fecha_limite_cambio`: DateField - Default: 30 días
- `monto_original`: DecimalField(12,2)
- `monto_nuevo`: DecimalField(12,2)
- `diferencia_monto`: DecimalField(12,2)
- `solicitado_por`: FK(Usuario)
- `aprobado_por`: FK(Usuario)
- `motivo_principal`: CharField(30) - TALLA_INCORRECTA, COLOR_INCORRECTO, DEFECTO_PRODUCTO, NO_SATISFACE, etc.
- `observaciones_cliente`: TextField
- `observaciones_vendedor`: TextField
- `observaciones_aprobacion`: TextField
- `requiere_autorizacion`: BooleanField
- `autorizado_excepcion`: BooleanField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Properties:**
- `dias_desde_venta`
- `dentro_del_plazo`
- `puede_completar`
- `requiere_pago_adicional`
- `genera_devolucion`

**Métodos:**
- `aprobar_cambio()`
- `rechazar_cambio()`
- `completar_cambio()`

**Motivos:**
- TALLA_INCORRECTA
- COLOR_INCORRECTO
- DEFECTO_PRODUCTO
- NO_SATISFACE
- REGALO_NO_DESEADO
- CAMBIO_OPINION
- PRODUCTO_DAÑADO
- OTRO

---

#### 48. CambioDevolucionDetalle
**Descripción:** Detalle de productos en cambios  
**Uso:** ✅ ACTIVO - Detalle de cambios

**Campos:**
- `cambio_devolucion`: FK(CambioDevolucion)
- `producto_original`: FK(Ticket_Productos)
- `cantidad_original`: IntegerField
- `producto_nuevo`: FK(Producto_Talla)
- `cantidad_nueva`: IntegerField
- `precio_nuevo`: DecimalField(10,2)
- `condicion_producto`: CharField(20) - PERFECTO, BUENO, REGULAR, DAÑADO, NO_APTO
- `apto_para_venta`: BooleanField
- `precio_original_unitario`: DecimalField(10,2)
- `diferencia_unitaria`: DecimalField(10,2)
- `diferencia_total`: DecimalField(10,2)
- `observaciones`: TextField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Properties:**
- `es_cambio`
- `es_devolucion`
- `valor_original_total`
- `valor_nuevo_total`

---

#### 49. PagoCambioDevolucion
**Descripción:** Pagos asociados a cambios  
**Uso:** ✅ ACTIVO - Pagos de cambios

**Campos:**
- `cambio_devolucion`: FK(CambioDevolucion)
- `tipo_pago`: CharField(20) - PAGO_DIFERENCIA, DEVOLUCION_EFECTIVO, DEVOLUCION_TARJETA, CREDITO_TIENDA
- `metodo_pago`: CharField(50)
- `monto`: DecimalField(12,2)
- `referencia_pago`: CharField(100)
- `numero_autorizacion`: CharField(50)
- `procesado_por`: FK(Usuario)
- `fecha_pago`: DateTimeField
- `fecha_vencimiento_credito`: DateField
- `observaciones`: TextField
- `created_at`: DateTimeField

---

#### 50. HistorialCambioDevolucion
**Descripción:** Historial de acciones en cambios  
**Uso:** ✅ ACTIVO - Auditoría

**Campos:**
- `cambio_devolucion`: FK(CambioDevolucion)
- `accion`: CharField(50) - CREADO, APROBADO, RECHAZADO, COMPLETADO, CANCELADO, etc.
- `estado_anterior`: CharField(20)
- `estado_nuevo`: CharField(20)
- `usuario`: FK(Usuario)
- `descripcion`: TextField
- `datos_adicionales`: JSONField
- `timestamp`: DateTimeField

---

### 📋 MÓDULO: APP - COTIZACIONES

#### 51. Cotizacion (Legacy)
**Descripción:** Cotizaciones simples (sistema antiguo)  
**Uso:** ⚠️ LEGACY - Reemplazado por Cotizacion_Empresa

**Campos:**
- `correlativo`: IntegerField
- `vendedor`: FK(Vendedor)
- `empresa`: FK(Empresa)
- `sucursal`: FK(Sucursal)
- `estado`: CharField(50)
- `estadoPago`: CharField(50)
- `responsable`: CharField(50)
- `fechaCreacion`: DateField

---

#### 52. Cotizacion_Detalle (Legacy)
**Descripción:** Detalle de cotizaciones simples  
**Uso:** ⚠️ LEGACY

**Campos:**
- `cotizacion`: FK(Cotizacion)
- `descripcion`: CharField(100)
- `producto_talla`: FK(Producto_Talla)
- `stock`: IntegerField
- `costo`: IntegerField
- `sobreprecio`: IntegerField
- `precio`: IntegerField

---

#### 53. Cotizacion_Empresa
**Descripción:** Cotizaciones empresariales (sistema nuevo)  
**Uso:** ✅ ACTIVO - Cotizaciones formales

**Campos:**
- `sucursal`: FK(Sucursal)
- `cliente`: FK(Empresa)
- `vendedor`: FK(Vendedor)
- `usuario_creador`: FK(Usuario)
- `numero_cotizacion`: CharField(20) - Único
- `fecha_emision`: DateField
- `fecha_validez`: DateField
- `dias_validez`: IntegerField - Default: 30
- `descripcion`: TextField
- `observaciones`: TextField
- `subtotal`: DecimalField(12,2)
- `descuento`: DecimalField(12,2)
- `impuesto`: DecimalField(12,2) - IVA
- `total`: DecimalField(12,2)
- `estado`: CharField(20) - VIGENTE, VENCIDA, FACTURADA, ANULADA
- `facturada`: BooleanField
- `numero_factura`: CharField(20)
- `fecha_facturacion`: DateTimeField
- `archivo_pdf`: FileField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField
- `anulada_por`: FK(Usuario)
- `fecha_anulacion`: DateTimeField
- `motivo_anulacion`: TextField

**Properties:**
- `esta_vigente`
- `dias_restantes`
- `porcentaje_vigencia`

**Métodos:**
- `calcular_totales()`
- `anular()`
- `marcar_como_facturada()`

---

#### 54. Cotizacion_Empresa_Detalle
**Descripción:** Items de cotizaciones empresariales  
**Uso:** ✅ ACTIVO - Detalle de cotizaciones

**Campos:**
- `cotizacion`: FK(Cotizacion_Empresa)
- `producto_existente`: FK(Producto_Talla)
- `es_producto_pendiente`: BooleanField
- `nombre_producto_pendiente`: CharField(255)
- `descripcion_producto_pendiente`: TextField
- `sku_producto_pendiente`: CharField(100)
- `numero_linea`: IntegerField
- `descripcion`: TextField
- `cantidad`: IntegerField
- `precio_unitario`: DecimalField(12,2)
- `descuento_porcentaje`: DecimalField(5,2)
- `descuento_monto`: DecimalField(12,2)
- `subtotal`: DecimalField(12,2)
- `stock_disponible`: IntegerField
- `fecha_llegada_estimada`: DateField
- `observaciones`: TextField
- `created_at`: DateTimeField
- `updated_at`: DateTimeField

**Properties:**
- `tiene_stock_suficiente`
- `precio_total`
- `nombre_producto`
- `sku_producto`

---

#### 55. Historial_Cotizacion
**Descripción:** Historial de cambios en cotizaciones  
**Uso:** ✅ ACTIVO - Auditoría

**Campos:**
- `cotizacion`: FK(Cotizacion_Empresa)
- `usuario`: FK(Usuario)
- `accion`: CharField(50) - CREADA, MODIFICADA, ANULADA, FACTURADA, ENVIADA, etc.
- `descripcion`: TextField
- `datos_anteriores`: JSONField
- `datos_nuevos`: JSONField
- `timestamp`: DateTimeField
- `ip_address`: GenericIPAddressField

---

### 💲 MÓDULO: APP - GESTIÓN DE PRECIOS

#### 56. CambioPrecioPendiente
**Descripción:** Sistema de aprobación de cambios de precios  
**Uso:** ✅ ACTIVO - Workflow de precios

**Campos:**
- `producto_talla`: FK(Producto_Talla)
- `sucursal`: FK(Sucursal)
- `precio_anterior`: IntegerField
- `precio_nuevo`: IntegerField
- `diferencia`: IntegerField
- `porcentaje_cambio`: DecimalField(10,2)
- `tipo_cambio`: CharField(20) - INDIVIDUAL, MASIVO, SINCRONIZACION, RECOMENDACION
- `estado`: CharField(20) - PENDIENTE, REVISADO, APROBADO, RECHAZADO, APLICADO, CANCELADO
- `motivo`: TextField
- `recomendacion_sistema`: JSONField
- `creado_por`: FK(Usuario)
- `revisado_por`: FK(Usuario)
- `aprobado_por`: FK(Usuario)
- `fecha_creacion`: DateTimeField
- `fecha_revision`: DateTimeField
- `fecha_aprobacion`: DateTimeField
- `fecha_aplicacion`: DateTimeField
- `fecha_vencimiento`: DateTimeField
- `observaciones_revision`: TextField
- `observaciones_aprobacion`: TextField
- `notificado`: BooleanField
- `prioridad`: CharField(10) - BAJA, MEDIA, ALTA, URGENTE

**Properties:**
- `dias_pendiente`
- `esta_vencido`
- `requiere_atencion`

---

#### 57. NotificacionCambioPrecio
**Descripción:** Notificaciones de cambios de precios  
**Uso:** ✅ ACTIVO - Sistema de notificaciones

**Campos:**
- `cambio_precio`: FK(CambioPrecioPendiente)
- `usuario`: FK(Usuario)
- `tipo`: CharField(20) - NUEVA, REVISION, APROBACION, RECHAZO, APLICACION, VENCIMIENTO
- `mensaje`: TextField
- `leida`: BooleanField
- `fecha_creacion`: DateTimeField
- `fecha_lectura`: DateTimeField

**Método:**
- `marcar_leida()`

---

#### 58. HistorialCambioPrecio
**Descripción:** Historial completo de cambios de precios  
**Uso:** ✅ ACTIVO - Auditoría de precios

**Campos:**
- `producto`: FK(Producto)
- `precio_anterior`: IntegerField
- `precio_nuevo`: IntegerField
- `diferencia`: IntegerField
- `porcentaje_cambio`: DecimalField(10,2)
- `motivo`: TextField
- `tipo_cambio`: CharField(50) - MANUAL, RECOMENDACION, MASIVO, SINCRONIZACION, APROBACION
- `usuario`: FK(Usuario)
- `fecha_cambio`: DateTimeField
- `ip_address`: GenericIPAddressField
- `tallas_afectadas`: IntegerField
- `lotes_afectados`: IntegerField

**Property:**
- `hace_cuanto`

---

### ⚙️ MÓDULO: APP - PARÁMETROS

#### 59. ParametroGlobal
**Descripción:** Parámetros globales del sistema  
**Uso:** ✅ ACTIVO - Configuración global

**Campos:**
- `nombre`: CharField(100) - Único
- `valor_entero`: IntegerField
- `fecha_actualizacion`: DateTimeField

---

## ANÁLISIS DE USO DE MODELOS

### Modelos Críticos (100% de uso)
1. ✅ **Usuario** - Autenticación y permisos
2. ✅ **Empresa** - Empresas, clientes, proveedores
3. ✅ **Sucursal** - Sucursales y ubicaciones
4. ✅ **Producto** - Productos base
5. ✅ **Producto_Talla** - SKUs y stock
6. ✅ **Ticket** - Ventas al público
7. ✅ **Dte** - Documentos tributarios
8. ✅ **Movimientos_Producto** - Kardex
9. ✅ **Correlativo** - Numeración de documentos
10. ✅ **ArqueoCaja** - Cuadraturas

### Modelos Activos (80-99% de uso)
11. ✅ **Vendedor** - Vendedores
12. ✅ **Cliente** - Clientes individuales
13. ✅ **Productos_Atributos** - Atributos
14. ✅ **AtributoOpcion** - Valores de atributos
15. ✅ **Categoria** - Categorías
16. ✅ **GuiaTalla** - Guías de tallas
17. ✅ **LoteProducto** - FIFO
18. ✅ **CreditoTrabajador** - Créditos
19. ✅ **ConfiguracionPOS** - POS Transbank
20. ✅ **TransaccionPOS** - Transacciones POS
21. ✅ **CambioDevolucion** - Cambios y devoluciones
22. ✅ **Cotizacion_Empresa** - Cotizaciones
23. ✅ **CambioPrecioPendiente** - Aprobación de precios

### Modelos Parciales (20-79% de uso)
24. ⚠️ **ContactoEmpresa** - Se usa ocasionalmente
25. ⚠️ **LogAcceso** - Auditoría básica
26. ⚠️ **LogEmpresa** - Auditoría parcial
27. ⚠️ **ProductoAtributoValor** - Sistema alternativo

### Modelos Legacy (< 20% de uso)
28. ❌ **Cotizacion** - Reemplazado por Cotizacion_Empresa
29. ❌ **Cotizacion_Detalle** - Legacy

---

## DIAGRAMA DE RELACIONES

### Estructura Central

```
Usuario
├── EmpresaUser → Empresa → Sucursal
│                   ├── Clientes
│                   ├── Proveedores
│                   └── DTEs
│
├── Vendedor
│   ├── Tickets
│   ├── DTEs
│   ├── Cotizaciones
│   └── Créditos
│
└── Sucursal
    ├── Productos → Producto_Talla
    │               ├── Stock
    │               ├── Lotes (FIFO)
    │               └── Movimientos
    │
    ├── Tickets → Ticket_Productos
    │            └── TicketDetallePago
    │
    ├── DTEs → Dte_Productos
    │         └── Dte_Detalle_Pago
    │
    ├── Traspasos → Traspaso_Detalle
    ├── Ajustes → AjusteInventario_Detalle
    ├── ArqueoCaja → DepositoBancario
    ├── ConfiguracionPOS → TransaccionPOS
    └── CambioDevolucion → CambioDevolucionDetalle
```

### Flujo de Datos Principal

```
1. COMPRAS
   Empresa (Proveedor) → DTE → Dte_Productos → Recepción → Producto_Talla
   
2. VENTAS
   Cliente → Ticket → Ticket_Productos → Movimientos_Producto → Stock
   
3. INVENTARIO
   Producto → Producto_Talla → LoteProducto (FIFO) → Movimientos_Producto
   
4. TRASPASOS
   Sucursal Origen → Traspaso → Traspaso_Detalle → DTE → Recepción → Sucursal Destino
   
5. PRECIOS
   Producto → CambioPrecioPendiente → Aprobación → HistorialCambioPrecio
```

---

## ESPECIFICACIONES TÉCNICAS

### Tipos de Datos por Categoría

#### Identificadores
- `RUT`: CharField(20) - Formato: 12.345.678-9 o 12345678-9
- `SKU`: IntegerField - Único por producto-talla
- `Correlativo`: IntegerField - Numérico secuencial
- `Código`: CharField(50-100) - Alfanumérico

#### Montos y Precios
- `Precio/Costo`: IntegerField - Pesos chilenos sin decimales
- `Descuento`: IntegerField o DecimalField(12,2)
- `Total`: IntegerField o DecimalField(12,2)
- `Porcentaje`: DecimalField(5,2) - Max 999.99%

#### Fechas y Tiempos
- `Fecha`: DateField - YYYY-MM-DD
- `FechaHora`: DateTimeField - YYYY-MM-DD HH:MM:SS
- `Hora`: TimeField - HH:MM:SS

#### Textos
- `Nombre/Título`: CharField(100-255)
- `Descripción`: CharField(250) o TextField
- `Observaciones`: TextField
- `Email`: EmailField
- `Teléfono`: CharField(20)
- `Dirección`: CharField(255) o TextField

#### Booleanos
- `Activo/Estado`: BooleanField
- `Es_X`: BooleanField (flags)

#### Opciones (Choices)
- `Estado`: CharField(20-50) con choices
- `Tipo`: CharField(20-50) con choices
- `Método`: CharField(50) con choices

#### JSON y Archivos
- `Datos_JSON`: JSONField
- `Archivo`: FileField
- `IP`: GenericIPAddressField

---

### Validaciones Importantes

#### RUT Chileno
- Formato: 7-8 dígitos + guión + dígito verificador (0-9 o K)
- Algoritmo de validación del dígito verificador (módulo 11)

#### Correlativo
- Debe tener disponibilidad antes de emitir
- Auto-incremento con validación de rango

#### Stock
- No puede ser negativo
- Sistema FIFO para cálculo de costos

#### Precios
- Siempre en pesos chilenos (CLP)
- Sin decimales para precios de venta
- IVA 19% en Chile

---

### Índices y Performance

#### Índices Críticos
- `(sucursal, correlativo)` - Unique Together
- `(empresa, alias)` - Sucursales
- `numero_documento` - DTEs
- `sku` - Productos
- `fecha` - Para reportes
- `estado` - Para filtros

#### Índices Compuestos
- `(fecha, tipo_movimiento)` - Movimientos
- `(ProductoTalla, fecha)` - Kardex
- `(sucursal, fecha_arqueo)` - Arqueos
- `(estado, sucursal)` - Filtros comunes

---

### Consideraciones para Migración

#### Datos Esenciales (Prioridad 1)
1. Empresas y Sucursales
2. Usuarios y Vendedores
3. Productos (con atributos y categorías)
4. Stock actual (Producto_Talla)
5. Clientes

#### Datos Históricos (Prioridad 2)
6. DTEs históricos
7. Tickets históricos
8. Movimientos de inventario
9. Lotes FIFO

#### Datos Operacionales (Prioridad 3)
10. Cotizaciones vigentes
11. Traspasos pendientes
12. Créditos activos
13. Cambios/Devoluciones en proceso

#### Datos Opcionales (Prioridad 4)
14. Logs y auditoría
15. Historial de precios
16. Arqueos antiguos

---

### Relaciones Críticas a Preservar

1. **Producto → Producto_Talla → Stock**
   - Fundamental para inventario

2. **Empresa → Sucursal → Productos**
   - Estructura organizacional

3. **Usuario → EmpresaUser → Sucursal**
   - Control de acceso

4. **Ticket → Ticket_Productos → Producto_Talla**
   - Ventas y stock

5. **Dte → Dte_Productos → Producto_Talla**
   - Compras y stock

6. **Movimientos_Producto → Producto_Talla**
   - Kardex y trazabilidad

7. **LoteProducto → Producto_Talla**
   - Costos FIFO

---

## RESUMEN DE DATOS NECESARIOS PARA MIGRACIÓN

### PASO 1: Estructura Base
```json
{
  "empresas": {
    "campos_requeridos": ["nombre", "rut", "razon_social", "direccion", "tipo_empresa"],
    "validaciones": ["rut_chileno_valido"],
    "relacionados": ["sucursales", "clientes"]
  },
  "sucursales": {
    "campos_requeridos": ["empresa_id", "alias", "direccion"],
    "unique": ["empresa_id + alias"]
  },
  "usuarios": {
    "campos_requeridos": ["username", "email", "password", "rol"],
    "opcionales": ["rut", "telefono"],
    "relacion_empresas": ["EmpresaUser"]
  }
}
```

### PASO 2: Catálogo de Productos
```json
{
  "atributos": {
    "ejemplos": ["Marca", "Color", "Género", "Material"],
    "con_opciones": ["Nike", "Adidas", "Rojo", "Azul"]
  },
  "categorias": {
    "jerarquicas": true,
    "ejemplos": ["Calzado > Deportivo", "Ropa > Poleras"]
  },
  "productos": {
    "campos_requeridos": ["articulo", "atributo1", "atributo2", "sucursal_id", "costo", "precio_venta"],
    "opcionales": ["categoria", "guia_talla", "descripcion"]
  },
  "producto_tallas": {
    "campos_requeridos": ["producto_id", "sku", "talla", "stock"],
    "nota": "SKU debe ser único en todo el sistema"
  }
}
```

### PASO 3: Datos Transaccionales
```json
{
  "dtes": {
    "campos_requeridos": ["emisor_id", "receptor_id", "numero_documento", "tipo_documento", "fecha_emision", "monto_neto", "monto_con_iva"],
    "con_detalle": ["Dte_Productos"]
  },
  "tickets": {
    "campos_requeridos": ["sucursal_id", "vendedor_id", "correlativo", "total", "fecha"],
    "con_detalle": ["Ticket_Productos", "TicketDetallePago"]
  },
  "movimientos": {
    "campos_requeridos": ["ProductoTalla_id", "cantidad", "fecha", "concepto", "tipo_movimiento"],
    "para_kardex": true
  }
}
```

### PASO 4: Datos Operacionales Activos
```json
{
  "creditos_trabajadores": {
    "solo_activos": true,
    "estados": ["APROBADO", "ACTIVO"]
  },
  "cotizaciones": {
    "solo_vigentes": true,
    "con_detalle": true
  },
  "cambios_devoluciones": {
    "estados_pendientes": ["SOLICITADO", "EN_PROCESO", "APROBADO"]
  }
}
```

---

## ENDPOINTS DE MIGRACIÓN SUGERIDOS

### Para el Sistema Origen (Crear estos endpoints)

```python
# 1. Exportar estructura base
GET /api/migracion/empresas/
GET /api/migracion/sucursales/
GET /api/migracion/usuarios/

# 2. Exportar catálogo
GET /api/migracion/atributos/
GET /api/migracion/categorias/
GET /api/migracion/productos/
GET /api/migracion/productos-tallas/

# 3. Exportar transacciones
GET /api/migracion/dtes/?fecha_desde=YYYY-MM-DD
GET /api/migracion/tickets/?fecha_desde=YYYY-MM-DD
GET /api/migracion/movimientos/?fecha_desde=YYYY-MM-DD

# 4. Exportar operaciones activas
GET /api/migracion/creditos-activos/
GET /api/migracion/cotizaciones-vigentes/
GET /api/migracion/cambios-pendientes/

# 5. Validación y mapeo
POST /api/migracion/validar-datos/
GET /api/migracion/mapeo-campos/
```

### Para el Sistema Destino (RetailMind - Crear estos endpoints)

```python
# 1. Importar estructura base
POST /api/importacion/empresas/
POST /api/importacion/sucursales/
POST /api/importacion/usuarios/

# 2. Importar catálogo
POST /api/importacion/atributos/
POST /api/importacion/categorias/
POST /api/importacion/productos/
POST /api/importacion/stock-inicial/

# 3. Importar histórico
POST /api/importacion/dtes-historico/
POST /api/importacion/tickets-historico/
POST /api/importacion/movimientos-historico/

# 4. Importar operaciones activas
POST /api/importacion/creditos/
POST /api/importacion/cotizaciones/
POST /api/importacion/cambios/

# 5. Validación post-migración
GET /api/importacion/validar-integridad/
GET /api/importacion/reporte-migracion/
```

---

## FIN DEL ANÁLISIS

**Generado:** 2025-01-05  
**Sistema:** RetailMind v4.0  
**Modelos Analizados:** 59  
**Módulos:** 13  

**Próximos Pasos:**
1. Analizar el sistema origen con este documento como referencia
2. Identificar modelos equivalentes o similares
3. Crear mapeo de campos entre sistemas
4. Desarrollar endpoints de exportación en sistema origen
5. Desarrollar endpoints de importación en RetailMind
6. Validar integridad de datos migrados
7. Realizar pruebas de migración en ambiente de desarrollo
8. Ejecutar migración en producción con respaldo

