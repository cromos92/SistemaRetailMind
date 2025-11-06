# 🔄 Guía de Migración desde Vicent a RetailMind

## 📋 Índice

1. [Introducción](#introducción)
2. [Prerequisitos](#prerequisitos)
3. [Instalación](#instalación)
4. [Configuración](#configuración)
5. [Uso del Comando](#uso-del-comando)
6. [Estructura de Datos](#estructura-de-datos)
7. [Solución de Problemas](#solución-de-problemas)

---

## 🎯 Introducción

Este comando migra productos desde el sistema **Vicent** (MySQL) hacia **RetailMind** (PostgreSQL).

### ⚠️ IMPORTANTE: Concepto de Producto Padre

**En Vicent:**
- NO existe concepto de "producto padre"
- Solo hay variaciones en la tabla `talla`
- Cada registro = 1 variación (producto + talla + bodega + stock)

**En RetailMind:**
- Necesitamos CREAR productos padre
- Se agrupan variaciones por `codigo_asociado`
- Cada grupo → 1 producto padre + N variaciones

### 📊 Números Esperados

- **328,547** productos padre (agrupados por `codigo_asociado`)
- **584,027** variaciones (`ProductoTalla`, mapeo 1:1 desde tabla `talla`)
- **78** categorías
- **436** marcas
- **267** colores
- **13** bodegas
- **186** tallas

---

## 🔧 Prerequisitos

### 1. Software Requerido

- Python 3.8+
- PostgreSQL (RetailMind - destino)
- MySQL (Vicent - origen)
- Django 4.2+

### 2. Acceso a Bases de Datos

- ✅ Acceso de **LECTURA** a MySQL (Vicent)
- ✅ Acceso de **ESCRITURA** a PostgreSQL (RetailMind)

---

## 📦 Instalación

### 1. Instalar dependencias

```bash
pip install -r requirements.txt
```

El archivo `requirements.txt` ya incluye:
```
mysqlclient==2.2.0  # Driver para MySQL
```

### 2. Verificar instalación de MySQL

**Windows:**
```powershell
# Si tienes problemas instalando mysqlclient, usa:
pip install mysqlclient --only-binary :all:

# O descarga el wheel desde:
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#mysqlclient
```

**Linux/Mac:**
```bash
# Instalar dependencias del sistema
# Ubuntu/Debian:
sudo apt-get install python3-dev default-libmysqlclient-dev build-essential

# CentOS/RedHat:
sudo yum install python3-devel mysql-devel

# macOS:
brew install mysql
```

---

## ⚙️ Configuración

### 1. Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto:

```env
# PostgreSQL (RetailMind - ya configurado)
DATABASE_URL=postgresql://usuario:password@localhost:5432/retailmind

# MySQL (Vicent - nuevo)
MYSQL_PASSWORD=tu_password_mysql
MYSQL_HOST=localhost
MYSQL_PORT=3306
```

### 2. Configuración Automática

El comando **configura automáticamente** la conexión MySQL en `settings.py`:

```python
DATABASES = {
    'default': {
        # PostgreSQL (RetailMind)
        ...
    },
    'vicent_mysql': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'vicent_software',
        'USER': 'root',
        'PASSWORD': os.environ.get('MYSQL_PASSWORD', ''),
        'HOST': os.environ.get('MYSQL_HOST', 'localhost'),
        'PORT': os.environ.get('MYSQL_PORT', '3306'),
    }
}
```

### 3. Verificar Conexión

```bash
# El comando verificará la conexión automáticamente
python manage.py migrate_from_vicent --modo=test
```

---

## 🚀 Uso del Comando

### Sintaxis Básica

```bash
python manage.py migrate_from_vicent [opciones]
```

### Modos de Ejecución

#### 1️⃣ Modo TEST (Recomendado primero)

Migra estructura completa + solo 10 productos de prueba:

```bash
python manage.py migrate_from_vicent --modo=test
```

**¿Qué hace?**
- ✅ Migra TODA la estructura (categorías, atributos, bodegas)
- ✅ Migra solo 10 productos padre + sus variaciones
- ✅ Ideal para verificar que todo funciona

#### 2️⃣ Modo COMPLETO

Migra TODO (estructura + todos los productos):

```bash
python manage.py migrate_from_vicent --modo=completo
```

**¿Qué hace?**
- ✅ Migra toda la estructura
- ✅ Migra los 328,547 productos padre
- ✅ Migra las 584,027 variaciones
- ⚠️ **Puede tomar varias horas**

#### 3️⃣ Modo SOLO ESTRUCTURA

Solo migra categorías, atributos y bodegas:

```bash
python manage.py migrate_from_vicent --modo=solo-estructura
```

**¿Qué hace?**
- ✅ Migra categorías (78)
- ✅ Migra atributos y opciones (4 atributos, 721 opciones)
- ✅ Migra bodegas (13)
- ❌ NO migra productos

#### 4️⃣ Modo SOLO PRODUCTOS

Solo migra productos (requiere estructura previa):

```bash
python manage.py migrate_from_vicent --modo=solo-productos
```

**¿Qué hace?**
- ❌ NO migra estructura
- ✅ Migra productos padre + variaciones
- ⚠️ **Requiere que ya se haya ejecutado** `--modo=solo-estructura`

### Opciones Adicionales

#### Tamaño de Lote

Controla cuántos productos se procesan a la vez:

```bash
python manage.py migrate_from_vicent --modo=completo --batch-size=50
```

- Por defecto: `100`
- Menor valor = más lento pero menos memoria
- Mayor valor = más rápido pero más memoria

#### Especificar Empresa

```bash
python manage.py migrate_from_vicent --modo=completo --empresa-id=1
```

- Si no se especifica, usa la primera empresa o crea una por defecto

#### Especificar Sucursal

```bash
python manage.py migrate_from_vicent --modo=completo --sucursal-id=5
```

- Si no se especifica, usa la primera sucursal de la empresa o crea una por defecto

---

## 📊 Estructura de Datos

### Transformación de Datos

#### EJEMPLO REAL: Producto con 9 variaciones

**EN VICENT (9 filas en tabla `talla`):**

| codigo_asociado | articulo | descripcion | marca | familia | size | stock | alias |
|-----------------|----------|-------------|-------|---------|------|-------|-------|
| 4744021 | 45-1 | BOLSA CORPORATIVA | PAOLA | BOLSOS | 00 | 2500 | EDEL |
| 4744021 | 45-1 | BOLSA CORPORATIVA | PAOLA | BOLSOS | 00 | 3200 | NICK1 |
| 4744021 | 45-1 | BOLSA CORPORATIVA | PAOLA | BOLSOS | 00 | 4100 | NICK2 |
| ... | ... | ... | ... | ... | ... | ... | ... |

**EN RETAILMIND:**

**1 Producto Padre:**
```python
Producto(
    articulo='45-1',
    descripcion='BOLSA CORPORATIVA',
    categoria=Categoria('BOLSOS'),
    atributo1=OpcionAtributo('Marca: PAOLA'),
    atributo2=OpcionAtributo('Color: MULTI'),
    atributo3=OpcionAtributo('Género: UNISEX'),
    costo=990,  # Promedio
    precioventa=990,  # Promedio
    tiene_tallas=True
)
```

**9 Variaciones (ProductoTalla):**
```python
ProductoTalla(producto=producto, talla='00', stock=2500, sku=hash('4744021-EDEL-00'))
ProductoTalla(producto=producto, talla='00', stock=3200, sku=hash('4744021-NICK1-00'))
ProductoTalla(producto=producto, talla='00', stock=4100, sku=hash('4744021-NICK2-00'))
# ... 6 más
```

### Fases de Migración

#### FASE 1: Estructura Base

1. **Categorías** (78 únicas)
   ```sql
   SELECT DISTINCT familia FROM talla
   ```

2. **Atributo "Marca"** + 436 opciones
   ```sql
   SELECT DISTINCT marca FROM talla
   ```

3. **Atributo "Color"** + 267 opciones
   ```sql
   SELECT DISTINCT color FROM talla
   ```

4. **Atributo "Género"** + 7 opciones
   ```sql
   SELECT DISTINCT sexo FROM talla
   ```

5. **Atributo "Departamento"** + 11 opciones
   ```sql
   SELECT DISTINCT departamento FROM talla
   ```

6. **Bodegas** (13 únicas)
   ```sql
   SELECT DISTINCT alias FROM talla
   ```

#### FASE 2: Productos Padre (¡CREADOS! No existen en Vicent)

```sql
SELECT 
    codigo_asociado,
    MAX(articulo) as articulo,
    MAX(descripcion) as descripcion,
    MAX(marca) as marca,
    MAX(familia) as familia,
    AVG(costo) as costo_promedio,
    AVG(precioventapublico) as precio_promedio,
    COUNT(*) as cantidad_variaciones
FROM talla
GROUP BY codigo_asociado
```

→ **328,547 productos padre**

#### FASE 3: Variaciones (Mapeo 1:1)

```sql
SELECT 
    codigo_asociado,
    size,
    stock,
    costo,
    precioventapublico,
    alias
FROM talla
ORDER BY codigo_asociado, alias, size
```

→ **584,027 variaciones** (1 por cada fila original)

---

## 📈 Salida del Comando

### Ejemplo de Ejecución en Modo TEST

```
================================================================================
🚀 MIGRACIÓN VICENT → RETAILMIND
================================================================================

📋 Modo: test
📦 Tamaño de lote: 100

✅ Conexión MySQL establecida
📊 Total de variaciones en Vicent: 584,027

🏢 Empresa: Mi Empresa
🏪 Sucursal: Principal

================================================================================
FASE 1: ESTRUCTURA BASE
================================================================================

📁 1. Migrando Categorías...
   📊 Categorías únicas encontradas: 78
   ✅ [1/78] Categoría creada: BOLSOS
   ✅ [2/78] Categoría creada: ZAPATILLAS
   ...
   ✅ Categorías migradas: 78

🏷️  2. Migrando Atributos...

   🏷️  Procesando atributo: Marca
      ✅ Atributo creado: Marca
      📊 Opciones encontradas: 436
         ➕ [1/436] NIKE
         ➕ [2/436] ADIDAS
         ...
      ✅ Opciones creadas: 436/436

   🏷️  Procesando atributo: Color
      ✅ Atributo creado: Color
      📊 Opciones encontradas: 267
      ✅ Opciones creadas: 267/267

   🏷️  Procesando atributo: Género
      ✅ Atributo creado: Género
      📊 Opciones encontradas: 7
      ✅ Opciones creadas: 7/7

   🏷️  Procesando atributo: Departamento
      ✅ Atributo creado: Departamento
      📊 Opciones encontradas: 11
      ✅ Opciones creadas: 11/11

   ✅ Total atributos: 4
   ✅ Total opciones: 721

🏪 3. Migrando Bodegas...
   📊 Bodegas únicas encontradas: 13
   ✅ [1/13] Bodega creada: EDEL
   ✅ [2/13] Bodega creada: NICK1
   ...
   ✅ Bodegas migradas: 13

================================================================================
FASE 2 y 3: PRODUCTOS PADRE + VARIACIONES
================================================================================

📦 Productos padre a crear: 10
   (Agrupados desde variaciones por codigo_asociado)

📦 Procesando lote 1: productos 1 a 10...
   ✅ [1] 4744021 - BOLSA CORPORATIVA (9 variaciones)
   ✅ [2] 1234567 - ZAPATILLA DEPORTIVA (15 variaciones)
   ...

   ✅ Productos padre migrados: 10
   ✅ Variaciones migradas: 127

================================================================================
📊 RESUMEN DE MIGRACIÓN
================================================================================

📁 Categorías migradas: 78
🏷️  Atributos migrados: 4
🔖 Opciones de atributos: 721
🏪 Bodegas migradas: 13
📦 Productos padre creados: 10
📊 Variaciones migradas: 127

🎉 ¡Migración completada!

🔍 Verificación en base de datos:
   • Categorías: 78
   • Atributos: 4
   • Opciones atributos: 721
   • Sucursales/Bodegas: 13
   • Productos: 10
   • Variaciones: 127
```

---

## 🐛 Solución de Problemas

### Error: "No se puede conectar a MySQL"

**Síntoma:**
```
❌ Error al conectar con MySQL: (2003, "Can't connect to MySQL server")
```

**Soluciones:**
1. Verificar que MySQL esté corriendo:
   ```bash
   # Windows
   net start MySQL80
   
   # Linux
   sudo systemctl start mysql
   ```

2. Verificar credenciales en `.env`:
   ```env
   MYSQL_PASSWORD=tu_password_correcto
   MYSQL_HOST=localhost
   MYSQL_PORT=3306
   ```

3. Verificar que la base de datos existe:
   ```sql
   SHOW DATABASES LIKE 'vicent_software';
   ```

### Error: "No module named 'MySQLdb'"

**Síntoma:**
```
ModuleNotFoundError: No module named 'MySQLdb'
```

**Solución:**
```bash
pip install mysqlclient
```

**Si falla en Windows:**
```bash
# Descargar wheel desde:
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#mysqlclient

# Instalar wheel descargado:
pip install mysqlclient-2.2.0-cp311-cp311-win_amd64.whl
```

### Error: "Empresa no encontrada"

**Síntoma:**
```
❌ No existe empresa con ID 1
```

**Solución:**
```bash
# No especificar empresa_id, se creará automáticamente:
python manage.py migrate_from_vicent --modo=test

# O crear empresa manualmente en admin de Django
```

### Error: "Memoria insuficiente"

**Síntoma:**
El proceso se cuelga o se queda sin memoria.

**Solución:**
Reducir el tamaño del lote:
```bash
python manage.py migrate_from_vicent --modo=completo --batch-size=50
```

### Error: "Productos duplicados"

**Síntoma:**
```
IntegrityError: duplicate key value violates unique constraint
```

**Explicación:**
El comando usa `get_or_create`, por lo que **no debería** crear duplicados.

**Solución:**
1. Si ya migraste antes, los productos existentes se reutilizarán
2. Para migración limpia, puedes eliminar datos previos:
   ```python
   # ⚠️ CUIDADO: Esto elimina todos los productos
   python manage.py shell
   >>> from app.models import Producto, Producto_Talla
   >>> Producto_Talla.objects.all().delete()
   >>> Producto.objects.all().delete()
   ```

---

## 📝 Notas Importantes

### 1. **Solo Lectura en MySQL**
- El comando **SOLO LEE** de MySQL (Vicent)
- No modifica nada en el sistema origen
- Es seguro ejecutarlo en producción

### 2. **Transacciones Atómicas**
- Cada producto se migra en una transacción
- Si falla 1 producto, los demás continúan
- Los errores se registran en el log

### 3. **Caché Interno**
- El comando usa caché para optimizar búsquedas
- Categorías, atributos y opciones se cargan una sola vez
- Mejora significativamente el rendimiento

### 4. **SKU Generado**
- El SKU se genera como hash del string: `codigo_asociado-bodega-talla`
- Se convierte a entero para el campo `sku` de `ProductoTalla`
- Ejemplo: `hash('4744021-EDEL-00')` → `123456789`

### 5. **Tiempos Estimados**

| Modo | Registros | Tiempo Estimado |
|------|-----------|----------------|
| test | 10 productos | 1-2 minutos |
| solo-estructura | 812 registros | 2-5 minutos |
| completo | 328,547 productos | 4-8 horas* |

\* Depende del hardware y tamaño de lote

---

## 🔄 Estrategia Recomendada de Migración

### Paso 1: Prueba Local
```bash
# 1. Ejecutar en modo test
python manage.py migrate_from_vicent --modo=test

# 2. Verificar datos en admin de Django
# 3. Revisar logs por errores
```

### Paso 2: Migración por Fases
```bash
# 1. Migrar solo estructura
python manage.py migrate_from_vicent --modo=solo-estructura

# 2. Verificar estructura en admin
# 3. Migrar productos en lotes
python manage.py migrate_from_vicent --modo=solo-productos --batch-size=50
```

### Paso 3: Migración Completa
```bash
# Solo cuando estés seguro:
python manage.py migrate_from_vicent --modo=completo
```

### Paso 4: Verificación Post-Migración
```sql
-- Verificar totales
SELECT COUNT(*) FROM app_producto;  -- Debe ser ~328,547
SELECT COUNT(*) FROM app_producto_talla;  -- Debe ser ~584,027

-- Verificar un producto específico
SELECT p.*, pt.* 
FROM app_producto p
LEFT JOIN app_producto_talla pt ON pt.producto_id = p.id
WHERE p.articulo = '45-1'
LIMIT 20;
```

---

## 📞 Soporte

Si encuentras problemas:

1. Revisa los logs del comando
2. Verifica la sección de [Solución de Problemas](#solución-de-problemas)
3. Consulta la documentación de Django sobre bases de datos múltiples

---

## 📄 Licencia

Este comando es parte del sistema RetailMind.

---

**Última actualización:** Noviembre 2025

