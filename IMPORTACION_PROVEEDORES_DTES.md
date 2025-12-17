# Sistema de Importación/Exportación de Proveedores y DTEs

## 📋 Descripción

Se ha implementado un sistema completo para **importar y exportar** proveedores y DTEs (Documentos Tributarios Electrónicos) de compras a la base de datos mediante archivos CSV o Excel.

## 🆕 **NUEVAS CARACTERÍSTICAS**

- ✅ **Exportación de datos actuales** a CSV o Excel
- ✅ **3 modos de importación** configurables
- ✅ **Flujo de trabajo bidireccional:** Exporta → Edita → Reimporta
- ✅ **Actualización masiva** de registros existentes

## ✨ Características Implementadas

### 1. Gestión de Proveedores

**URL de acceso:** `/app/importacion-proveedores/`

**Características de Importación:**
- ✅ Importación masiva desde archivos CSV o Excel (.csv, .xlsx, .xls)
- ✅ Validación de RUT chileno con dígito verificador
- ✅ **3 modos de importación:**
  - **Crear y Actualizar** (recomendado): Crea nuevos y actualiza existentes
  - **Solo Crear**: Solo crea nuevos, omite existentes
  - **Solo Actualizar**: Solo actualiza existentes, omite nuevos
- ✅ Vista previa de los datos antes de importar (primeras 10 filas)
- ✅ Drag & Drop para subir archivos
- ✅ Descarga de formato de ejemplo
- ✅ Reporte detallado de éxitos y errores

**Características de Exportación:**
- ✅ **Exportar proveedores actuales** a CSV
- ✅ **Exportar proveedores actuales** a Excel (.xlsx)
- ✅ Incluye todos los campos con formato
- ✅ Listo para editar y reimportar

**Campos soportados:**
- `rut` ⭐ (obligatorio) - Formato: **76123456-7** (sin puntos, solo guión)
- `nombre` ⭐ (obligatorio)
- `nombre_fantasia`
- `razon_social`
- `giro`
- `direccion`
- `comuna`
- `ciudad`
- `email` - Se usará para correoVendedor, correoIntercambio y correoAdministrador
- `telefono` - Se guardará en contacto1
- `acteco` - Código de actividad económica SII

**Formato CSV de ejemplo:**
```csv
rut,nombre,nombre_fantasia,razon_social,giro,direccion,comuna,ciudad,email,telefono,acteco
76123456-7,Empresa Ejemplo SPA,Ejemplo,Empresa Ejemplo Sociedad por Acciones,Comercio al por mayor,Av. Principal 123,Santiago,Santiago,contacto@ejemplo.cl,+56912345678,471010
```

### 2. Gestión de DTEs de Compras

**URL de acceso:** `/app/importacion-dtes/`

**Características de Importación:**
- ✅ Importación masiva desde archivos CSV o Excel
- ✅ Dos modos de búsqueda de proveedores:
  - Por **RUT** del proveedor
  - Por **ID** del proveedor en la base de datos
- ✅ Cálculo automático de IVA (19%) y total
- ✅ Validación de proveedores existentes
- ✅ Vista previa de datos
- ✅ Descarga de formato de ejemplo según modo seleccionado
- ✅ Reporte de éxitos y errores

**Características de Exportación:**
- ✅ **Exportar DTEs actuales** a CSV (por RUT o ID)
- ✅ **Exportar DTEs actuales** a Excel (.xlsx)
- ✅ Incluye información completa del DTE
- ✅ Mantiene referencia al proveedor
- ✅ Listo para editar y reimportar

**Campos soportados:**

**Modo RUT:**
- `rut_proveedor` ⭐ (obligatorio)
- `numero_documento` ⭐ (obligatorio)
- `tipo_documento` - Por defecto: 33 (Factura Electrónica)
- `fecha_emision` - Formato: YYYY-MM-DD o DD/MM/YYYY
- `monto_con_iva` ⭐ (obligatorio) - Monto total de la factura (con IVA incluido)
- `monto_neto` (alternativa) - Si prefieres usar el monto sin IVA
- `dias_credito` - Por defecto: 30
- `bultos`
- `unidades`
- `referencias`

**Notas importantes:**
- ✅ Puedes usar **monto_con_iva** (más común - el total de la factura)
- ✅ O puedes usar **monto_neto** (el subtotal sin IVA)
- ✅ El sistema calcula automáticamente el otro valor
- ✅ El campo `responsable` NO es necesario - se asigna automáticamente el usuario que importa

**Modo ID:**
- `id_proveedor` ⭐ (obligatorio)
- (resto igual que modo RUT)

**Formatos CSV de ejemplo:**

Modo RUT (usando monto con IVA):
```csv
rut_proveedor,numero_documento,tipo_documento,fecha_emision,monto_con_iva,dias_credito,bultos,unidades,referencias
76123456-7,12345,33,2024-12-11,119000,30,2,50,Orden de Compra 001
77234567-8,12346,33,2024-12-10,297500,45,5,100,Orden de Compra 002
```

Modo ID (usando monto con IVA):
```csv
id_proveedor,numero_documento,tipo_documento,fecha_emision,monto_con_iva,dias_credito,bultos,unidades,referencias
1,12345,33,2024-12-11,119000,30,2,50,Orden de Compra 001
2,12346,33,2024-12-10,297500,45,5,100,Orden de Compra 002
```

**Nota:** También puedes usar `monto_neto` en lugar de `monto_con_iva` si tienes el monto sin IVA.

## 🚀 Cómo Usar

### Flujo Completo: Exportar → Editar → Importar

#### **Opción 1: Actualizar Proveedores Existentes**

1. Acceder a **Compras** > **Importar** > **Importar Proveedores**
2. Hacer clic en **"Exportar CSV"** o **"Exportar Excel"**
3. Se descargará un archivo con todos tus proveedores actuales
4. Editar el archivo (modificar datos, agregar nuevos proveedores)
5. Seleccionar modo **"Crear y Actualizar"**
6. Arrastrar el archivo editado a la zona de carga
7. Revisar vista previa y hacer clic en **"Importar Proveedores"**
8. ✅ Los proveedores existentes se actualizan, los nuevos se crean

#### **Opción 2: Solo Agregar Nuevos Proveedores**

1. Hacer clic en **"Descargar Formato"**
2. Completar solo con los nuevos proveedores
3. Seleccionar modo **"Solo Crear"**
4. Importar
5. ✅ Solo se crean los nuevos, los existentes se ignoran

#### **Opción 3: Solo Actualizar Datos Existentes**

1. Exportar proveedores actuales
2. Editar solo los que quieres actualizar (mantener el RUT)
3. Seleccionar modo **"Solo Actualizar"**
4. Importar
5. ✅ Solo se actualizan los existentes, se ignoran los nuevos

### Gestionar DTEs

#### **Exportar DTEs Actuales**

1. Acceder a **Compras** > **Importar** > **Importar DTEs**
2. Seleccionar modo de identificación (RUT o ID)
3. Hacer clic en **"Exportar Actuales"** → CSV o Excel
4. Se descarga archivo con todos los DTEs de compras
5. El archivo incluye:
   - ID del DTE
   - Identificador del proveedor (RUT o ID según selección)
   - Nombre del proveedor
   - Número de documento
   - Montos (neto, IVA, total)
   - Estados
   - Toda la información del DTE

#### **Importar/Actualizar DTEs**

1. Usar el archivo exportado o el formato de ejemplo
2. Editar o agregar DTEs
3. Cargar el archivo
4. ✅ Los DTEs se crean (duplicados por número+proveedor se rechazan)

## 📝 Recomendaciones

### Para Proveedores

1. **Validación de RUT:** El sistema valida automáticamente el dígito verificador del RUT chileno. Asegúrate de que el RUT esté en formato correcto: `76123456-7` (sin puntos, solo guión). También acepta `76.123.456-7` pero se recomienda sin puntos.

2. **Modos de Importación:**
   - **Crear y Actualizar** (recomendado): Úsalo para importaciones regulares. Actualiza lo que existe y crea lo que no.
   - **Solo Crear**: Úsalo cuando solo quieras agregar nuevos proveedores sin tocar los existentes.
   - **Solo Actualizar**: Úsalo para actualizar masivamente datos de proveedores existentes sin crear nuevos.

3. **Flujo de actualización masiva:**
   ```
   Exportar → Editar en Excel → Guardar → Importar con modo "Crear y Actualizar"
   ```

4. **Campos obligatorios:** RUT y Nombre son los únicos campos realmente obligatorios. Los demás se pueden dejar vacíos y se rellenarán con valores por defecto.

5. **Correos electrónicos:** Si solo proporcionas un campo `email`, se usará para los tres tipos de correo del proveedor (vendedor, intercambio, administrador).

6. **Campo ID en exportación:** Al exportar, el archivo incluye el campo `id`. Este campo es informativo y NO se usa en la importación (se usa el RUT como clave única).

### Para DTEs

1. **Proveedor debe existir:** Antes de importar DTEs, asegúrate de que los proveedores ya estén en el sistema. Puedes importarlos primero usando la función de importación de proveedores.

2. **Modo RUT vs ID:**
   - Usa **RUT** si tienes los documentos con el RUT del proveedor (más común) ⭐ Recomendado
   - Usa **ID** si ya conoces el ID interno del proveedor en tu base de datos
   - **Importante:** Al exportar, mantén el mismo modo que usaste para exportar

3. **Exportación y edición:**
   ```
   1. Selecciona modo (RUT o ID)
   2. Exporta DTEs actuales
   3. Edita en Excel (puedes modificar montos, fechas, etc.)
   4. Reimporta con el MISMO modo seleccionado
   ```

4. **Cálculo automático:** No necesitas calcular el IVA ni el total. El sistema lo hace automáticamente basándose en el `monto_neto`.

5. **Fechas:** Las fechas pueden estar en formato `YYYY-MM-DD` o `DD/MM/YYYY`. Si no se proporciona fecha de emisión, se usará la fecha actual.

6. **Tipos de documento comunes:**
   - `33`: Factura Electrónica
   - `34`: Factura Exenta Electrónica
   - `52`: Guía de Despacho Electrónica
   - `56`: Nota de Débito Electrónica
   - `61`: Nota de Crédito Electrónica

7. **Duplicados:** El sistema no permite duplicar DTEs con el mismo número de documento, tipo y proveedor.

8. **Actualización de DTEs:** Actualmente los DTEs solo se crean, no se actualizan. Si necesitas modificar un DTE, debes editarlo manualmente en el sistema.

## 🔧 APIs Disponibles

### Proveedores

**Importación:**
- `POST /app/api/importar-proveedores/` - Importar proveedores desde archivo
  - Parámetros: `archivo_proveedores` (file), `modo_actualizacion` (string)
  - Modos: `crear_y_actualizar`, `solo_crear`, `solo_actualizar`

**Exportación:**
- `GET /app/api/exportar-proveedores-actuales/` - Exportar proveedores a CSV
- `GET /app/api/exportar-proveedores-excel/` - Exportar proveedores a Excel

**Formatos:**
- `GET /app/api/descargar-formato-proveedores/` - Descargar formato CSV de ejemplo

### DTEs

**Importación:**
- `POST /app/api/importar-dtes/` - Importar DTEs desde archivo
  - Parámetros: `archivo_dtes` (file), `tipo_busqueda` (string)
  - Tipos: `rut`, `id`

**Exportación:**
- `GET /app/api/exportar-dtes-actuales/?tipo=rut` - Exportar DTEs a CSV (por RUT)
- `GET /app/api/exportar-dtes-actuales/?tipo=id` - Exportar DTEs a CSV (por ID)
- `GET /app/api/exportar-dtes-excel/?tipo=rut` - Exportar DTEs a Excel (por RUT)
- `GET /app/api/exportar-dtes-excel/?tipo=id` - Exportar DTEs a Excel (por ID)

**Formatos:**
- `GET /app/api/descargar-formato-dtes/?tipo=rut` - Descargar formato por RUT
- `GET /app/api/descargar-formato-dtes/?tipo=id` - Descargar formato por ID

## 🎯 Casos de Uso

### Caso 1: Migración inicial de proveedores

Si estás migrando desde otro sistema y tienes una lista de proveedores en Excel:

1. Exporta tus proveedores a CSV desde el sistema antiguo
2. Ajusta las columnas para que coincidan con el formato requerido
3. Selecciona modo **"Crear y Actualizar"**
4. Importa el archivo completo
5. Revisa el reporte y corrige los errores si los hay

### Caso 2: Actualización masiva de datos de proveedores ⭐ **NUEVO**

Si necesitas actualizar información de contacto de múltiples proveedores:

1. Exporta proveedores actuales (CSV o Excel)
2. Edita en Excel (cambiar correos, teléfonos, direcciones, etc.)
3. Guarda el archivo
4. Selecciona modo **"Crear y Actualizar"** o **"Solo Actualizar"**
5. Importa
6. ✅ Todos los cambios se aplican automáticamente

### Caso 3: Agregar proveedores nuevos sin tocar existentes

Si solo quieres agregar nuevos proveedores:

1. Descarga el formato de ejemplo
2. Completa solo con los nuevos proveedores
3. Selecciona modo **"Solo Crear"**
4. Importa
5. ✅ Se crean solo los nuevos, los existentes se ignoran

### Caso 4: Importación regular de DTEs

Si recibes facturas regularmente de proveedores:

1. Mantén una planilla Excel con los DTEs recibidos
2. Periódicamente (semanal/mensual) importa los nuevos DTEs
3. El sistema rechazará los duplicados automáticamente

### Caso 5: Revisión y corrección de DTEs ⭐ **NUEVO**

Si necesitas revisar tus DTEs:

1. Exporta DTEs actuales a Excel
2. Revisa los datos (montos, fechas, referencias)
3. Identifica errores o inconsistencias
4. Si necesitas corregir: edita manualmente en el sistema
5. Usa el Excel exportado como referencia/respaldo

## ⚠️ Errores Comunes y Soluciones

| Error | Causa | Solución |
|-------|-------|----------|
| "RUT no válido" | El dígito verificador no coincide | Verifica que el RUT esté correcto |
| "Proveedor no encontrado" | El proveedor no existe en la DB | Primero importa los proveedores |
| "DTE ya existe" | DTE duplicado | Verifica el número de documento |
| "Campo requerido" | Falta información obligatoria | Completa el campo indicado |
| "Formato de archivo no válido" | Extensión incorrecta | Usa .csv, .xlsx o .xls |

## 🔍 Validaciones Implementadas

### Proveedores
- ✅ Validación de formato RUT
- ✅ Validación de dígito verificador RUT
- ✅ Campos obligatorios: RUT, Nombre
- ✅ Detección de duplicados por RUT

### DTEs
- ✅ Validación de proveedor existente
- ✅ Validación de campos obligatorios
- ✅ Detección de DTEs duplicados
- ✅ Validación de formatos de fecha
- ✅ Validación de montos numéricos
- ✅ Cálculo correcto de IVA 19%

## 📊 Ejemplo de Uso Completo

```bash
# 1. Preparar archivo de proveedores
# proveedores.csv:
rut,nombre,email
76123456-7,Distribuidora ABC SPA,abc@empresa.cl
77234567-8,Comercial XYZ Ltda,xyz@empresa.cl

# 2. Importar proveedores
# Resultado: 2 proveedores creados

# 3. Preparar archivo de DTEs
# dtes.csv (usando monto con IVA):
rut_proveedor,numero_documento,tipo_documento,monto_con_iva,fecha_emision,dias_credito,bultos,unidades,referencias
76123456-7,1001,33,595000,2024-12-01,30,2,50,OC-001
77234567-8,2002,33,892500,2024-12-05,30,3,75,OC-002

# 4. Importar DTEs
# Resultado: 2 DTEs importados
# Responsable: Usuario que importó (automático)
# Monto neto calculado automáticamente: 500000 y 750000
# IVA calculado: 95000 y 142500
# Total guardado: 595000 y 892500
```

## 🆘 Soporte

Si tienes problemas con la importación/exportación:

1. **Exportación no funciona:** Verifica que tengas proveedores/DTEs en el sistema
2. **Error en importación:** Verifica que el archivo tenga el formato correcto
3. **Proveedores no se actualizan:** Asegúrate de seleccionar el modo correcto
4. **DTEs duplicados:** Revisa número de documento + tipo + proveedor
5. Consulta el reporte de errores que muestra el sistema
6. Descarga el formato de ejemplo y compáralo con tu archivo

## ✅ Funcionalidades Implementadas

- [x] Importación masiva de proveedores
- [x] Exportación de proveedores actuales (CSV y Excel)
- [x] 3 modos de importación configurables
- [x] Validación de RUT chileno
- [x] Importación masiva de DTEs
- [x] Exportación de DTEs actuales (CSV y Excel)
- [x] Vista previa antes de importar
- [x] Drag & Drop para archivos
- [x] Reportes detallados de errores
- [x] Formato de ejemplo descargable

## 🔄 Actualizaciones Futuras Sugeridas

- [ ] Modo de actualización para DTEs (actualmente solo se crean)
- [ ] Importación de productos del DTE junto con el DTE
- [ ] Validación de archivos XML del SII
- [ ] Importación desde APIs de servicios de facturación
- [ ] Programación de importaciones automáticas
- [ ] Notificaciones por email de importaciones completadas
- [ ] Historial de importaciones con rollback
- [ ] Exportación con filtros (por fecha, proveedor, estado, etc.)
