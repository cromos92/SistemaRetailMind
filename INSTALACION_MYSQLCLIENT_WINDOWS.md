# 🪟 Instalación de mysqlclient en Windows

## ⚠️ Problema Común en Windows

Al ejecutar `pip install mysqlclient` en Windows, puedes encontrar errores como:

```
ERROR: Microsoft Visual C++ 14.0 or greater is required
error: command 'cl.exe' failed
```

## ✅ Soluciones (3 Métodos)

---

## 🥇 Método 1: Usar Wheel Precompilado (RECOMENDADO)

### Paso 1: Identificar tu versión de Python

```powershell
python --version
# Ejemplo: Python 3.11.5

python -c "import struct; print(struct.calcsize('P') * 8)"
# Resultado: 64 (para 64-bit) o 32 (para 32-bit)
```

### Paso 2: Descargar el Wheel Correcto

Ir a: https://www.lfd.uci.edu/~gohlke/pythonlibs/#mysqlclient

Descargar según tu versión:

| Python | Arquitectura | Archivo |
|--------|--------------|---------|
| 3.11 | 64-bit | `mysqlclient‑2.2.0‑cp311‑cp311‑win_amd64.whl` |
| 3.11 | 32-bit | `mysqlclient‑2.2.0‑cp311‑cp311‑win32.whl` |
| 3.10 | 64-bit | `mysqlclient‑2.2.0‑cp310‑cp310‑win_amd64.whl` |
| 3.10 | 32-bit | `mysqlclient‑2.2.0‑cp310‑cp310‑win32.whl` |
| 3.9 | 64-bit | `mysqlclient‑2.2.0‑cp39‑cp39‑win_amd64.whl` |
| 3.9 | 32-bit | `mysqlclient‑2.2.0‑cp39‑cp39‑win32.whl` |

### Paso 3: Instalar el Wheel

```powershell
# Navegar a la carpeta de descargas
cd C:\Users\TuUsuario\Downloads

# Instalar el wheel
pip install mysqlclient-2.2.0-cp311-cp311-win_amd64.whl
```

### Paso 4: Verificar Instalación

```powershell
python -c "import MySQLdb; print('✅ MySQLdb instalado correctamente')"
```

---

## 🥈 Método 2: Instalar MySQL Connector

Si el método 1 falla, puedes usar MySQL Connector en lugar de mysqlclient.

### Paso 1: Instalar MySQL Connector

```powershell
pip install mysql-connector-python
```

### Paso 2: Modificar settings.py

Cambiar el ENGINE en la configuración de MySQL:

```python
# En settings.py
DATABASES = {
    'vicent_mysql': {
        'ENGINE': 'mysql.connector.django',  # ← Cambiar esta línea
        'NAME': 'vicent_software',
        'USER': 'root',
        'PASSWORD': os.environ.get('MYSQL_PASSWORD', ''),
        'HOST': 'localhost',
        'PORT': '3306',
    }
}
```

### Paso 3: Verificar

```powershell
python manage.py migrate_from_vicent --modo=test
```

---

## 🥉 Método 3: Instalar Visual C++ Build Tools

Si necesitas compilar desde código fuente:

### Paso 1: Descargar Build Tools

Ir a: https://visualstudio.microsoft.com/visual-cpp-build-tools/

Descargar "Build Tools for Visual Studio 2022"

### Paso 2: Instalar Componentes Necesarios

Durante la instalación, seleccionar:
- ✅ Desktop development with C++
- ✅ MSVC v143 - VS 2022 C++ x64/x86 build tools
- ✅ Windows 10 SDK

### Paso 3: Instalar mysqlclient

```powershell
pip install mysqlclient
```

---

## 🔍 Verificación Completa

### 1. Verificar MySQL Instalado

```powershell
# Verificar servicio MySQL
net start | findstr MySQL

# Debería mostrar algo como:
# MySQL80
```

### 2. Verificar Conexión Manual

```powershell
# Conectar a MySQL
mysql -u root -p

# En MySQL shell:
mysql> SHOW DATABASES LIKE 'vicent%';
mysql> USE vicent_software;
mysql> SHOW TABLES LIKE 'talla';
mysql> SELECT COUNT(*) FROM talla;
```

### 3. Verificar Python puede conectar

```powershell
python
```

```python
# En Python shell:
import MySQLdb

# Conectar
conn = MySQLdb.connect(
    host='localhost',
    user='root',
    password='TU_PASSWORD',
    database='vicent_software'
)

# Probar query
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM talla")
print(cursor.fetchone())  # Debería mostrar: (584027,)

conn.close()
print("✅ Conexión exitosa!")
```

---

## 🔧 Solución de Problemas Específicos

### Error: "DLL load failed"

```
ImportError: DLL load failed while importing _mysql
```

**Solución:**

Instalar Visual C++ Redistributable:
- Descargar: https://aka.ms/vs/17/release/vc_redist.x64.exe
- Instalar
- Reiniciar computadora

### Error: "Can't find libmysql.dll"

**Solución 1:** Agregar MySQL bin a PATH

```powershell
# Agregar a PATH
$env:Path += ";C:\Program Files\MySQL\MySQL Server 8.0\bin"
```

**Solución 2:** Copiar DLL

```powershell
# Ubicar libmysql.dll
# Normalmente en: C:\Program Files\MySQL\MySQL Server 8.0\lib

# Copiar a carpeta de Python
copy "C:\Program Files\MySQL\MySQL Server 8.0\lib\libmysql.dll" "C:\Python311\DLLs\"
```

### Error: "Authentication plugin 'caching_sha2_password' cannot be loaded"

**Solución:** Cambiar método de autenticación en MySQL

```sql
-- En MySQL shell:
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'tu_password';
FLUSH PRIVILEGES;
```

---

## 📝 Script de Instalación Automática

Crea un archivo `instalar_mysql_python.ps1`:

```powershell
# Script de instalación automática
Write-Host "🔧 Instalando mysqlclient para Windows..." -ForegroundColor Green

# Detectar versión de Python
$pythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$architecture = python -c "import struct; print(struct.calcsize('P') * 8)"

Write-Host "📊 Python $pythonVersion ($architecture-bit)" -ForegroundColor Cyan

# Mapeo de versiones a wheel
$wheels = @{
    "3.11-64" = "https://github.com/PyMySQL/mysqlclient/releases/download/v2.2.0/mysqlclient-2.2.0-cp311-cp311-win_amd64.whl"
    "3.10-64" = "https://github.com/PyMySQL/mysqlclient/releases/download/v2.2.0/mysqlclient-2.2.0-cp310-cp310-win_amd64.whl"
    "3.9-64"  = "https://github.com/PyMySQL/mysqlclient/releases/download/v2.2.0/mysqlclient-2.2.0-cp39-cp39-win_amd64.whl"
}

$key = "$pythonVersion-$architecture"

if ($wheels.ContainsKey($key)) {
    $wheelUrl = $wheels[$key]
    Write-Host "📦 Descargando wheel..." -ForegroundColor Yellow
    
    $wheelFile = "$env:TEMP\mysqlclient.whl"
    Invoke-WebRequest -Uri $wheelUrl -OutFile $wheelFile
    
    Write-Host "📥 Instalando..." -ForegroundColor Yellow
    pip install $wheelFile
    
    Remove-Item $wheelFile
    
    Write-Host "✅ Instalación completada!" -ForegroundColor Green
    
    # Verificar
    python -c "import MySQLdb; print('✅ MySQLdb funciona correctamente')"
} else {
    Write-Host "❌ No hay wheel para Python $pythonVersion $architecture-bit" -ForegroundColor Red
    Write-Host "💡 Usa el Método 2 (MySQL Connector) en su lugar" -ForegroundColor Yellow
}
```

Ejecutar:

```powershell
powershell -ExecutionPolicy Bypass -File instalar_mysql_python.ps1
```

---

## ✅ Checklist Final

Antes de ejecutar la migración, verifica:

- [ ] Python instalado (3.9, 3.10 o 3.11)
- [ ] MySQL Server instalado y corriendo
- [ ] mysqlclient o mysql-connector-python instalado
- [ ] Variable `MYSQL_PASSWORD` en `.env`
- [ ] Conexión manual a MySQL funciona
- [ ] Python puede importar MySQLdb

---

## 🧪 Test Rápido

```powershell
# Test de conexión completo
python -c "
import os
os.environ['MYSQL_PASSWORD'] = 'TU_PASSWORD'

import MySQLdb

try:
    conn = MySQLdb.connect(
        host='localhost',
        user='root',
        password=os.environ.get('MYSQL_PASSWORD'),
        database='vicent_software'
    )
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM talla')
    total = cursor.fetchone()[0]
    print(f'✅ Conexión exitosa!')
    print(f'📊 Total registros en talla: {total:,}')
    conn.close()
except Exception as e:
    print(f'❌ Error: {e}')
"
```

---

## 🎯 ¿Todo Listo?

Si el test anterior funciona, ya puedes ejecutar:

```powershell
python manage.py migrate_from_vicent --modo=test
```

---

## 📞 Ayuda Adicional

### Recursos

- MySQL Connector: https://dev.mysql.com/doc/connector-python/en/
- mysqlclient wheels: https://www.lfd.uci.edu/~gohlke/pythonlibs/#mysqlclient
- Visual C++ Build Tools: https://visualstudio.microsoft.com/downloads/

### Comandos Útiles

```powershell
# Ver servicios MySQL
net start | findstr MySQL

# Iniciar MySQL
net start MySQL80

# Detener MySQL
net stop MySQL80

# Ver versión Python
python --version

# Ver packages instalados
pip list | findstr -i mysql

# Desinstalar y reinstalar
pip uninstall mysqlclient -y
pip install mysqlclient
```

---

**Última actualización:** Noviembre 2025

