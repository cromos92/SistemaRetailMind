from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib.auth import get_user_model
from app.models import EmpresaUser
def login_view(request): 
    if request.method == 'POST':
        email = request.POST['email'].lower()
        password = request.POST['password-input']
        User = get_user_model()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            messages.error(request, 'No existe un usuario con ese correo electrónico.')
            return render(request, 'registration/login.html')

        user = authenticate(request, username=user.username, password=password)
        if user is not None:
            login(request, user)

            try:
                # Buscar todas las empresas activas del usuario
                empresas_activas = EmpresaUser.objects.filter(user=user, active=True)
                
                if empresas_activas.exists():
                    # Si hay múltiples empresas activas, tomar la primera (puedes cambiar esta lógica)
                    # Alternativa: ordenar por fecha de creación o permitir al usuario elegir
                    empresa_user = empresas_activas.first()
                    
                    request.session['idEmpresaActual'] = empresa_user.empresa.id
                    request.session['idSucursalActual'] = empresa_user.sucursal.id if empresa_user.sucursal else None
                    request.session['idDireccionSucursalActual'] = empresa_user.sucursal.direccion if empresa_user.sucursal else ''
                    request.session['alias'] = empresa_user.sucursal.alias if empresa_user.sucursal else ''
                    request.session['nombreEmpresaActual'] = empresa_user.empresa.nombre
                    request.session['rutEmpresaActual'] = empresa_user.empresa.rut
                    
                    # Si hay múltiples empresas, mostrar mensaje informativo
                    if empresas_activas.count() > 1:
                        messages.info(request, f'Tienes acceso a {empresas_activas.count()} empresas. Actualmente trabajando con: {empresa_user.empresa.nombre}')
                    
                    return redirect('verHome')
                else:
                    messages.warning(request, 'No tienes una empresa activa asignada. Contacta al administrador.')
                    return redirect('verHome')
            except Exception as e:
                messages.error(request, f'Error al acceder a la empresa: {str(e)}')
                return redirect('verHome')
        else:
            messages.error(request, 'Credenciales incorrectas. Por favor, inténtalo de nuevo.')

    return render(request, 'registration/login.html')

def logout_view(request):
    logout(request) 
    return render(request, 'registration/logout.html')

