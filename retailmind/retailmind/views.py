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
                empresa_user = EmpresaUser.objects.get(user=user, active=True)
                request.session['idEmpresaActual'] = empresa_user.empresa.id
                request.session['idSucursalActual'] = empresa_user.sucursal.id
                request.session['idDireccionSucursalActual'] = empresa_user.sucursal.direccion
                request.session['alias'] = empresa_user.sucursal.alias
                request.session['nombreEmpresaActual'] = empresa_user.empresa.nombre
                request.session['rutEmpresaActual'] = empresa_user.empresa.rut
            except EmpresaUser.DoesNotExist:
                messages.warning(request, 'No tienes una empresa activa asignada.')
                # Podés redirigir a una vista de selección de empresa o logout
                return redirect('seleccionar_empresa')  # o mostrar un mensaje, etc.

            return redirect('/app/home')
        else:
            messages.error(request, 'Credenciales incorrectas. Por favor, inténtalo de nuevo.')

    return render(request, 'registration/login.html')

def logout_view(request):
    logout(request) 
    return render(request, 'registration/logout.html')

