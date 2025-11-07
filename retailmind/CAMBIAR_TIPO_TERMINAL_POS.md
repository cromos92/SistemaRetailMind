# 🔧 Cambiar Tipo de Terminal POS

## Problema Actual

Todos tus terminales aparecen como **"Verifone VX520"** pero probablemente tienes terminales **Ingenico**.

---

## ✅ Solución 1: Actualizar desde Línea de Comandos (RÁPIDO)

### Opción A: Actualizar TODOS los terminales a Ingenico

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind

python manage.py actualizar_tipo_pos
```

**Sigue las instrucciones interactivas:**

```
1. Selecciona opción: 1 (Actualizar todos)
2. Selecciona tipo nuevo: 2 (INGENICO_3500) o 3 (INGENICO_DESK)
3. Confirma: si
```

### Opción B: Actualizar terminal específico

```bash
python manage.py actualizar_tipo_pos
```

```
1. Selecciona opción: 2 (Actualizar uno específico)
2. Ingresa el ID del terminal (ej: 1, 2, 3...)
3. Selecciona tipo nuevo: 2 o 3
4. Confirma: si
```

---

## ✅ Solución 2: Actualizar desde Python Shell

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind

python manage.py shell
```

```python
from app.models import ConfiguracionPOS

# Ver todos los terminales
terminales = ConfiguracionPOS.objects.all()
for t in terminales:
    print(f"ID: {t.id} | {t.nombre} | Tipo: {t.tipo_pos} | Puerto: {t.puerto_conexion}")

# Actualizar TODOS a Ingenico 3500
ConfiguracionPOS.objects.all().update(tipo_pos='INGENICO_3500')
print("✅ Todos actualizados a INGENICO_3500")

# O actualizar TODOS a Ingenico DESK
ConfiguracionPOS.objects.all().update(tipo_pos='INGENICO_DESK')
print("✅ Todos actualizados a INGENICO_DESK")

# O actualizar uno específico (ID 1)
terminal = ConfiguracionPOS.objects.get(id=1)
terminal.tipo_pos = 'INGENICO_3500'
terminal.save()
print(f"✅ Terminal {terminal.nombre} actualizado")

# Salir
exit()
```

---

## ✅ Solución 3: Actualizar desde la Interfaz Web

1. **Ve a:** `http://localhost:8000/app/pos/transbank/`

2. **En la tarjeta de cada terminal:**
   - Haz clic en el botón **✏️ Editar**
   
3. **En el modal de edición:**
   - Cambia el campo **"Tipo de Terminal"**
   - Selecciona **"Ingenico 3500"** o **"Ingenico DESK"**
   
4. **Haz clic en "Guardar"**

---

## 📋 Tipos de Terminal Disponibles

| Código | Nombre | Cuándo Usar |
|--------|--------|-------------|
| `VERIFONE_VX520` | Verifone VX520 | Terminal Verifone modelo VX520 |
| `INGENICO_3500` | Ingenico 3500 | Terminal Ingenico modelo 3500 |
| `INGENICO_DESK` | Ingenico DESK | Terminal Ingenico modelo DESK/5000 |
| `OTRO` | Otro | Otro tipo de terminal |

---

## 🎯 ¿Qué Tipo Tengo?

### Identificar tu Terminal:

#### **Verifone VX520:**
- Terminal compacto negro
- Pantalla pequeña monocromática
- Teclado físico con números grandes
- Logo "Verifone" en el frente

#### **Ingenico 3500:**
- Terminal blanco/gris
- Pantalla táctil a color
- Más moderno y delgado
- Logo "Ingenico" en el frente

#### **Ingenico DESK:**
- Terminal tipo escritorio
- Pantalla táctil grande
- Similar al 3500 pero más grande
- Para uso en counter/mostrador

---

## 🚀 Comando Rápido para tu Caso

### Si TODOS tus terminales son Ingenico:

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind

# Para Ingenico 3500
python manage.py shell -c "from app.models import ConfiguracionPOS; ConfiguracionPOS.objects.all().update(tipo_pos='INGENICO_3500'); print('✅ Actualizados a Ingenico 3500')"

# O para Ingenico DESK
python manage.py shell -c "from app.models import ConfiguracionPOS; ConfiguracionPOS.objects.all().update(tipo_pos='INGENICO_DESK'); print('✅ Actualizados a Ingenico DESK')"
```

---

## 🔍 Verificar el Cambio

### Opción 1: Desde Python

```bash
python manage.py shell
```

```python
from app.models import ConfiguracionPOS

for t in ConfiguracionPOS.objects.all():
    print(f"{t.nombre}: {t.get_tipo_pos_display()}")
```

### Opción 2: Desde la Web

1. Ve a `http://localhost:8000/app/pos/transbank/`
2. Haz clic en **"Actualizar"** (🔄)
3. Verifica que ahora aparezcan como "Ingenico"

---

## ⚡ Script de Actualización Rápida

Copia este contenido en un archivo `actualizar_ingenico.py`:

```python
import os
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models import ConfiguracionPOS

print('='*60)
print('  🔧 ACTUALIZACIÓN A INGENICO')
print('='*60)

terminales = ConfiguracionPOS.objects.all()
print(f'\n📋 Terminales encontrados: {terminales.count()}\n')

for t in terminales:
    print(f'   • {t.nombre} - Tipo actual: {t.get_tipo_pos_display()}')

tipo = input('\n👉 ¿A qué tipo actualizar? (3500/desk): ').strip().lower()

if tipo == '3500':
    tipo_nuevo = 'INGENICO_3500'
elif tipo == 'desk':
    tipo_nuevo = 'INGENICO_DESK'
else:
    print('❌ Tipo inválido')
    exit()

confirmacion = input(f'\n⚠️  ¿Actualizar TODOS a {tipo_nuevo}? (si/no): ').strip().lower()

if confirmacion in ['si', 's', 'yes', 'y']:
    actualizados = terminales.update(tipo_pos=tipo_nuevo)
    print(f'\n✅ {actualizados} terminal(es) actualizados exitosamente')
    print('='*60)
else:
    print('\n❌ Actualización cancelada')
```

**Ejecutar:**

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python actualizar_ingenico.py
```

---

## 📝 Nota Importante

El **tipo de terminal NO afecta la funcionalidad** con el SDK de Transbank. Es solo información para:

- Identificación visual en la interfaz
- Reportes y estadísticas
- Documentación interna

**Todos los tipos usan el mismo protocolo** de comunicación del SDK oficial.

---

## 🎯 Recomendación

Para cambiar rápidamente TODOS tus terminales a Ingenico:

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind

python manage.py shell
```

```python
from app.models import ConfiguracionPOS

# Cambiar todos a Ingenico 3500
ConfiguracionPOS.objects.all().update(tipo_pos='INGENICO_3500')

# Verificar
for t in ConfiguracionPOS.objects.all():
    print(f"✅ {t.nombre}: {t.get_tipo_pos_display()}")

exit()
```

---

**¿Cuál es el modelo exacto de tus terminales POS?** Así puedo darte el comando exacto para actualizarlos.

Opciones:
- Ingenico 3500 (táctil, blanco/gris)
- Ingenico DESK/5000 (escritorio)
- Otro modelo

