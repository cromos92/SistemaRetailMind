# 🚀 Inicio Rápido - Migración Vicent

## ⚡ Instalación en 3 Pasos

### 1️⃣ Instalar Dependencias

```bash
# Instalar mysqlclient para conectar con MySQL
pip install mysqlclient==2.2.0
```

**Si falla en Windows:**
```bash
# Descargar desde: https://www.lfd.uci.edu/~gohlke/pythonlibs/#mysqlclient
pip install mysqlclient-2.2.0-cp311-cp311-win_amd64.whl
```

### 2️⃣ Configurar Variables de Entorno

Crea archivo `.env` en la raíz:

```env
# MySQL (Vicent - solo lectura)
MYSQL_PASSWORD=tu_password_mysql
MYSQL_HOST=localhost
MYSQL_PORT=3306
```

### 3️⃣ Ejecutar Migración de Prueba

```bash
# Migra estructura + 10 productos de prueba
python manage.py migrate_from_vicent --modo=test
```

---

## 📊 ¿Qué Hace el Comando?

### En Vicent (MySQL):
- ❌ **NO** hay productos padre
- Solo variaciones en tabla `talla`
- **584,027 registros** (todas variaciones)

### En RetailMind (PostgreSQL):
- ✅ **CREA** productos padre
- Agrupa por `codigo_asociado`
- **328,547 productos** + **584,027 variaciones**

---

## 🎯 Modos de Ejecución

| Comando | ¿Qué hace? | Tiempo |
|---------|-----------|---------|
| `--modo=test` | Estructura + 10 productos | 2 min |
| `--modo=solo-estructura` | Solo categorías/atributos | 3 min |
| `--modo=solo-productos` | Solo productos (requiere estructura) | Variable |
| `--modo=completo` | TODO (estructura + productos) | 4-8 hrs |

---

## 📋 Flujo Recomendado

```bash
# 1. Prueba con 10 productos
python manage.py migrate_from_vicent --modo=test

# 2. Verifica en admin de Django
# http://localhost:8000/admin

# 3. Revisa la migración
python verificar_migracion.py

# 4. Si todo está OK, migra todo
python manage.py migrate_from_vicent --modo=completo
```

---

## 🔍 Verificar Migración

```bash
# Ejecutar script de verificación
python verificar_migracion.py
```

**Salida esperada:**
```
✅ Conexión MySQL establecida
📊 Total registros en tabla 'talla': 584,027
📦 Códigos asociados únicos: 328,547
🏪 Bodegas únicas: 13
📁 Familias únicas: 78
```

---

## ❓ Problemas Comunes

### "Can't connect to MySQL"

```bash
# Verificar que MySQL esté corriendo
# Windows:
net start MySQL80

# Linux:
sudo systemctl start mysql
```

### "No module named 'MySQLdb'"

```bash
pip install mysqlclient
```

### "Productos sin categoría"

Es normal, algunos productos en Vicent no tienen categoría asignada.

---

## 📚 Documentación Completa

Ver: [`GUIA_MIGRACION_VICENT.md`](GUIA_MIGRACION_VICENT.md)

---

## 🎉 ¡Listo!

Si la prueba funcionó, puedes ejecutar la migración completa:

```bash
python manage.py migrate_from_vicent --modo=completo
```

**Nota:** La migración completa puede tomar varias horas. Se recomienda ejecutarla en horario no laboral.

