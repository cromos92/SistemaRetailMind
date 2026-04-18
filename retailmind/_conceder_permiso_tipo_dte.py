"""
Concede `puede_editar=True` sobre las opciones de "Editar Boleta Electrónica"
y "Editar Boleta Papel" para un rol o usuario específico.

Uso:
    python _conceder_permiso_tipo_dte.py --rol administracion
    python _conceder_permiso_tipo_dte.py --rol jefe_local
    python _conceder_permiso_tipo_dte.py --usuario jeanpierre.mosnich
    python _conceder_permiso_tipo_dte.py --usuario email@dominio.cl
    python _conceder_permiso_tipo_dte.py --revocar --rol jefe_local
"""
import os
import sys
import argparse
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.contrib.auth import get_user_model
from app.models import OpcionMenu, PermisoRol, PermisoUsuario

Usuario = get_user_model()

CODIGOS = [
    'dte_editar_tipo_boleta_electronica',
    'dte_editar_tipo_boleta_papel',
]


def conceder_a_rol(rol: str, conceder: bool):
    opciones = list(OpcionMenu.objects.filter(codigo__in=CODIGOS))
    if len(opciones) != len(CODIGOS):
        print("ERROR: no se encontraron todas las OpcionMenu. "
              "¿Está aplicada la migración 0140_permisos_edicion_dte?")
        sys.exit(1)
    for op in opciones:
        pr, _ = PermisoRol.objects.update_or_create(
            rol=rol,
            opcion_menu=op,
            defaults={'puede_ver': True, 'puede_editar': conceder},
        )
        accion = 'CONCEDIDO' if conceder else 'REVOCADO'
        print(f"  [{accion}] rol={rol} · {op.codigo}")


def conceder_a_usuario(ident: str, conceder: bool):
    qs = Usuario.objects.filter(username__iexact=ident)
    if not qs.exists():
        qs = Usuario.objects.filter(email__iexact=ident)
    user = qs.first()
    if not user:
        print(f"ERROR: no se encontró usuario con username/email='{ident}'")
        sys.exit(1)

    opciones = list(OpcionMenu.objects.filter(codigo__in=CODIGOS))
    for op in opciones:
        pu, _ = PermisoUsuario.objects.update_or_create(
            usuario=user,
            opcion_menu=op,
            defaults={'puede_ver': True, 'puede_editar': conceder},
        )
        accion = 'CONCEDIDO' if conceder else 'REVOCADO'
        print(f"  [{accion}] usuario={user.username} · {op.codigo}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--rol', help='Rol: administrador|administracion|jefe_local|cajero|vendedor')
    g.add_argument('--usuario', help='Username o email del usuario')
    ap.add_argument('--revocar', action='store_true', default=False,
                    help='En vez de conceder, revoca (puede_editar=False)')
    args = ap.parse_args()

    conceder = not args.revocar

    if args.rol:
        conceder_a_rol(args.rol, conceder)
    else:
        conceder_a_usuario(args.usuario, conceder)

    print("\nListo. Reingresa a la sesión si no aparece el selector.")


if __name__ == '__main__':
    main()
