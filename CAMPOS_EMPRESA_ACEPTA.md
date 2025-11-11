# ✅ CAMPOS AGREGADOS AL MODELO EMPRESA

## 🎯 NUEVOS CAMPOS PARA FACTURACIÓN ELECTRÓNICA

Se han agregado **3 campos opcionales** al modelo `Empresa` para facilitar la generación de DTEs con Acepta:

---

## 📋 CAMPOS AGREGADOS

### 1. **acteco** (Código de Actividad Económica)
```python
acteco = models.CharField(
    max_length=20, 
    blank=True, 
    null=True,
    verbose_name='Código Acteco',
    help_text='Código de actividad económica del SII'
)
```

**Uso:** Código de la actividad económica registrada en el SII (ej: 521000, 469000)

**Ejemplo:**
- 521000 = Comercio al por menor
- 469000 = Importación y exportación de calzado
- 726000 = Asesorías informáticas

### 2. **contacto1** (Contacto Principal)
```python
contacto1 = models.CharField(
    max_length=100, 
    blank=True, 
    null=True,
    verbose_name='Contacto 1',
    help_text='Teléfono o email de contacto principal'
)
```

**Uso:** Teléfono, email o dato de contacto principal

**Ejemplo:**
- +56912345678
- contacto@empresa.cl
- Juan Pérez - +56912345678

### 3. **contacto2** (Contacto Secundario)
```python
contacto2 = models.CharField(
    max_length=100, 
    blank=True, 
    null=True,
    verbose_name='Contacto 2',
    help_text='Teléfono o email de contacto secundario'
)
```

**Uso:** Teléfono, email o dato de contacto alternativo

**Ejemplo:**
- +56987654321
- ventas@empresa.cl
- María López - +56987654321

---

## 🔧 MIGRACIÓN REQUERIDA

Después de agregar estos campos, debes crear y aplicar la migración:

### 1. Crear migración
```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
..\venv\Scripts\python.exe manage.py makemigrations app
```

### 2. Aplicar migración
```bash
..\venv\Scripts\python.exe manage.py migrate app
```

---

## 💡 CÓMO USAR

### Desde el Admin de Django:

1. Ve a: `http://localhost:8000/admin/app/empresa/`
2. Edita tu empresa
3. Completa los nuevos campos:
   - **Acteco:** 521000
   - **Contacto 1:** +56912345678
   - **Contacto 2:** ventas@empresa.cl
4. Guardar

### Desde código Python:

```python
from app.models import Empresa

# Actualizar empresa existente
empresa = Empresa.objects.first()
empresa.acteco = '521000'
empresa.contacto1 = '+56912345678'
empresa.contacto2 = 'ventas@empresa.cl'
empresa.save()
```

### Al generar DTEs:

```python
# Los datos se toman automáticamente del modelo
datos_emisor = {
    'rut': empresa.rut,
    'razon_social': empresa.razon_social,
    'giro': empresa.giro,
    'acteco': empresa.acteco or '',  # ✅ Desde el modelo
    'direccion': empresa.direccion,
    'comuna': empresa.comuna,
    'ciudad': empresa.ciudad,
    'telefono': empresa.contacto1 or '',  # ✅ Desde el modelo
}
```

---

## 🎯 BENEFICIOS

### Antes:
```python
# Tenías que ingresar manualmente cada vez
datos_emisor = {
    'acteco': '521000',  # ❌ Hard-coded
    'telefono': '+56912345678'  # ❌ Hard-coded
}
```

### Ahora:
```python
# Se toma automáticamente de la empresa
datos_emisor = {
    'acteco': empresa.acteco or '',  # ✅ Desde BD
    'telefono': empresa.contacto1 or ''  # ✅ Desde BD
}
```

---

## 📊 CAMPOS DEL MODELO EMPRESA (Completo)

| Campo | Tipo | Obligatorio | Uso DTE |
|-------|------|-------------|---------|
| nombre | CharField(100) | ✅ Sí | - |
| rut | CharField(20) | ✅ Sí | ✅ Emisor |
| nombre_fantasia | CharField(255) | ✅ Sí | - |
| razon_social | CharField(255) | ✅ Sí | ✅ Emisor |
| giro | CharField(255) | ✅ Sí | ✅ Emisor |
| direccion | CharField(255) | ✅ Sí | ✅ Emisor |
| comuna | CharField(100) | ✅ Sí | ✅ Emisor |
| ciudad | CharField(100) | ✅ Sí | ✅ Emisor |
| **acteco** | **CharField(20)** | **❌ No** | **✅ Emisor** |
| **contacto1** | **CharField(100)** | **❌ No** | **✅ Emisor** |
| **contacto2** | **CharField(100)** | **❌ No** | **✅ Opcional** |
| esProveedor | BooleanField | ✅ Sí | - |
| correoVendedor | CharField(100) | ✅ Sí | - |
| correoIntercambio | CharField(100) | ✅ Sí | - |
| correoAdministrador | CharField(100) | ✅ Sí | - |

---

## 🚀 SIGUIENTE PASO

Crea la migración para agregar estos campos a la base de datos:

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
..\venv\Scripts\python.exe manage.py makemigrations app
..\venv\Scripts\python.exe manage.py migrate app
```

Luego podrás configurar estos datos en la empresa y se usarán automáticamente al generar DTEs.

---

**¿Quieres que cree también una vista para editar estos datos o es suficiente desde el admin?** 🔧
