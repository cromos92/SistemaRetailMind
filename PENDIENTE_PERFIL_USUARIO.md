# 👤 Perfil de Usuario - Pendiente de Implementar

## ✅ **YA REALIZADO**

- ✅ Menú simplificado en navbar
  - Solo "Mi Perfil" y "Cerrar Sesión"
- ✅ URLs agregadas en `users/urls.py`
- ✅ Email configurado y funcionando
- ✅ Resetear contraseña desde gestión (envía email)

## ⏳ **PENDIENTE DE IMPLEMENTAR**

### **1. Vista `mi_perfil`**

**Crear en `users/views.py`:**

```python
@login_required
def mi_perfil(request):
    """Vista del perfil del usuario actual"""
    return render(request, 'users/mi_perfil.html', {
        'usuario': request.user
    })
```

### **2. Vista `actualizar_perfil`**

```python
@login_required
@require_POST
def actualizar_perfil(request):
    """Actualizar datos del perfil del usuario"""
    try:
        data = json.loads(request.body)
        usuario = request.user
        
        # Actualizar campos editables
        usuario.first_name = data.get('first_name', usuario.first_name)
        usuario.last_name = data.get('last_name', usuario.last_name)
        usuario.email = data.get('email', usuario.email)
        usuario.telefono = data.get('telefono', usuario.telefono)
        usuario.direccion = data.get('direccion', usuario.direccion)
        usuario.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Perfil actualizado exitosamente'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
```

### **3. Vista `cambiar_password`**

```python
@login_required
@require_POST
def cambiar_password(request):
    """Cambiar contraseña del usuario actual"""
    try:
        data = json.loads(request.body)
        usuario = request.user
        
        password_actual = data.get('password_actual')
        password_nueva = data.get('password_nueva')
        
        # Verificar contraseña actual
        if not usuario.check_password(password_actual):
            return JsonResponse({
                'success': False,
                'error': 'La contraseña actual es incorrecta'
            }, status=400)
        
        # Cambiar contraseña
        usuario.set_password(password_nueva)
        usuario.save()
        
        # Actualizar sesión para no cerrar sesión
        from django.contrib.auth import update_session_auth_hash
        update_session_auth_hash(request, usuario)
        
        return JsonResponse({
            'success': True,
            'message': 'Contraseña cambiada exitosamente'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
```

### **4. Vista `subir_foto_perfil`**

```python
@login_required
@require_POST
def subir_foto_perfil(request):
    """Subir foto de perfil del usuario"""
    try:
        if 'foto' not in request.FILES:
            return JsonResponse({
                'success': False,
                'error': 'No se proporcionó ninguna imagen'
            }, status=400)
        
        foto = request.FILES['foto']
        
        # Guardar foto (necesita configurar MEDIA)
        # ... lógica para guardar archivo
        
        return JsonResponse({
            'success': True,
            'message': 'Foto actualizada exitosamente'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
```

### **5. Template `mi_perfil.html`**

**Crear en `users/templates/users/mi_perfil.html`:**

```html
{% load static %}
{% include '../../app/templates/layout/header.html' %}
{% include '../../app/templates/layout/menu.html' %}

<div class="page-content">
    <div class="container-fluid">
        
        <div class="row">
            <div class="col-lg-12">
                <div class="card">
                    <div class="card-header">
                        <h4 class="card-title mb-0">
                            <i class="ri-user-settings-line me-2"></i>
                            Mi Perfil
                        </h4>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <!-- Foto de perfil -->
                            <div class="col-md-3 text-center">
                                <img src="{% static 'images/users/user-dummy-img.jpg' %}" 
                                     class="rounded-circle img-thumbnail mb-3" 
                                     width="150" 
                                     id="fotoPerfil">
                                <button class="btn btn-primary btn-sm" onclick="cambiarFoto()">
                                    <i class="ri-image-edit-line me-1"></i>
                                    Cambiar Foto
                                </button>
                            </div>
                            
                            <!-- Información del usuario -->
                            <div class="col-md-9">
                                <h5 class="mb-3">Información Personal</h5>
                                
                                <!-- Formulario -->
                                <form id="formPerfil">
                                    <div class="row">
                                        <div class="col-md-6 mb-3">
                                            <label class="form-label">Nombre</label>
                                            <input type="text" class="form-control" 
                                                   name="first_name" 
                                                   value="{{ user.first_name }}">
                                        </div>
                                        <div class="col-md-6 mb-3">
                                            <label class="form-label">Apellido</label>
                                            <input type="text" class="form-control" 
                                                   name="last_name" 
                                                   value="{{ user.last_name }}">
                                        </div>
                                        <div class="col-md-6 mb-3">
                                            <label class="form-label">Email</label>
                                            <input type="email" class="form-control" 
                                                   name="email" 
                                                   value="{{ user.email }}">
                                        </div>
                                        <div class="col-md-6 mb-3">
                                            <label class="form-label">Teléfono</label>
                                            <input type="text" class="form-control" 
                                                   name="telefono" 
                                                   value="{{ user.telefono|default:'' }}">
                                        </div>
                                    </div>
                                    
                                    <button type="submit" class="btn btn-success">
                                        <i class="ri-save-line me-1"></i>
                                        Guardar Cambios
                                    </button>
                                </form>
                                
                                <hr class="my-4">
                                
                                <!-- Cambiar contraseña -->
                                <h5 class="mb-3">Cambiar Contraseña</h5>
                                <button class="btn btn-warning" data-bs-toggle="modal" data-bs-target="#modalCambiarPassword">
                                    <i class="ri-lock-password-line me-1"></i>
                                    Cambiar Contraseña
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
    </div>
</div>

<!-- Modal Cambiar Contraseña -->
<div class="modal fade" id="modalCambiarPassword" tabindex="-1">
    <div class="modal-dialog">
        <div class="modal-content">
            <div class="modal-header">
                <h5 class="modal-title">Cambiar Contraseña</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <form id="formCambiarPassword">
                    <div class="mb-3">
                        <label class="form-label">Contraseña Actual</label>
                        <input type="password" class="form-control" name="password_actual" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Nueva Contraseña</label>
                        <input type="password" class="form-control" name="password_nueva" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Confirmar Nueva Contraseña</label>
                        <input type="password" class="form-control" name="password_confirmar" required>
                    </div>
                </form>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
                <button type="submit" form="formCambiarPassword" class="btn btn-primary">Cambiar</button>
            </div>
        </div>
    </div>
</div>

<script>
// JavaScript para formularios
$('#formPerfil').on('submit', function(e) {
    e.preventDefault();
    const data = {
        first_name: $('input[name="first_name"]').val(),
        last_name: $('input[name="last_name"]').val(),
        email: $('input[name="email"]').val(),
        telefono: $('input[name="telefono"]').val()
    };
    
    $.ajax({
        url: '{% url "users:actualizar_perfil" %}',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(data),
        headers: {'X-CSRFToken': getCookie('csrftoken')},
        success: function(response) {
            Swal.fire('Éxito', response.message, 'success');
        },
        error: function(xhr) {
            Swal.fire('Error', xhr.responseJSON?.error || 'Error al actualizar', 'error');
        }
    });
});

$('#formCambiarPassword').on('submit', function(e) {
    e.preventDefault();
    const nueva = $('input[name="password_nueva"]').val();
    const confirmar = $('input[name="password_confirmar"]').val();
    
    if (nueva !== confirmar) {
        Swal.fire('Error', 'Las contraseñas no coinciden', 'error');
        return;
    }
    
    const data = {
        password_actual: $('input[name="password_actual"]').val(),
        password_nueva: nueva
    };
    
    $.ajax({
        url: '{% url "users:cambiar_password" %}',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(data),
        headers: {'X-CSRFToken': getCookie('csrftoken')},
        success: function(response) {
            Swal.fire('Éxito', response.message, 'success');
            $('#modalCambiarPassword').modal('hide');
            $('#formCambiarPassword')[0].reset();
        },
        error: function(xhr) {
            Swal.fire('Error', xhr.responseJSON?.error || 'Error al cambiar contraseña', 'error');
        }
    });
});

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}
</script>

{% include '../../app/templates/layout/footer.html' %}
```

---

## 🎯 **Para Implementar**

1. **Copiar el código de las vistas a `users/views.py`**
2. **Crear el template `users/templates/users/mi_perfil.html`**
3. **Probar accediendo a** `http://localhost:8000/users/mi-perfil/`

## 📸 **Para la Foto de Perfil**

Necesitarás:
- Configurar manejo de archivos multimedia
- Usar Pillow para redimensionar imágenes
- Guardar en carpeta `media/profile_photos/`

## ✨ **Lo que tendrás**

**Menú simplificado:**
```
┌────────────────────────────┐
│ Bienvenido Javier!         │
├────────────────────────────┤
│ 👤 Mi Perfil               │
├────────────────────────────┤
│ 🚪 Cerrar Sesión           │
└────────────────────────────┘
```

**Página Mi Perfil:**
- Editar nombre, apellido, email, teléfono
- Cambiar contraseña (modal)
- Subir foto de perfil
- Todo con AJAX

---

**¿Quieres que implemente las vistas y el template completo ahora o lo dejamos para la próxima sesión?** 😊



