"""
Script temporal para verificar el estado del permiso 'cambiar_empresa'.
Uso: python manage.py shell < _check_permiso_cambiar_empresa.py
"""
from app.models import OpcionMenu, PermisoRol, PermisoUsuario

print("=" * 60)
print("OPCION 'cambiar_empresa'")
print("=" * 60)

op = OpcionMenu.objects.filter(codigo="cambiar_empresa").first()
print(f"  Existe: {op is not None}")

if op:
    print(f"  ID:       {op.id}")
    print(f"  Nombre:   {op.nombre}")
    print(f"  Modulo:   {op.modulo.nombre} (codigo={op.modulo.codigo})")
    print(f"  Activo:   {op.activo}")
    print(f"  url_name: {op.url_name}")

    print()
    print("=" * 60)
    print("PERMISOS POR ROL para esta opcion")
    print("=" * 60)
    permisos = PermisoRol.objects.filter(opcion_menu=op).order_by("rol")
    if not permisos.exists():
        print("  (ningun rol tiene permiso configurado)")
    else:
        for p in permisos:
            flags = []
            if p.puede_ver:
                flags.append("VER")
            if p.puede_crear:
                flags.append("CREAR")
            if p.puede_editar:
                flags.append("EDITAR")
            if p.puede_eliminar:
                flags.append("ELIMINAR")
            if p.puede_exportar:
                flags.append("EXPORTAR")
            if p.puede_aprobar:
                flags.append("APROBAR")
            flags_str = " | ".join(flags) if flags else "sin flags activos"
            print(f"  {p.rol:20s} -> {flags_str}")

    print()
    print("=" * 60)
    print("OVERRIDES POR USUARIO")
    print("=" * 60)
    overrides = PermisoUsuario.objects.filter(opcion_menu=op).select_related("usuario")
    if overrides.exists():
        for p in overrides:
            print(
                f"  {p.usuario.username:30s} | puede_ver={p.puede_ver}, "
                f"puede_ver_todas_sucursales={p.puede_ver_todas_sucursales}"
            )
    else:
        print("  (ningun usuario tiene override)")

print()
print("=" * 60)
print("TODAS LAS OPCIONES DEL MODULO 'usuario' (Mi Cuenta)")
print("=" * 60)
mis_opciones = OpcionMenu.objects.filter(modulo__codigo__in=["usuario", "mi_cuenta"]).order_by("orden")
if not mis_opciones.exists():
    mods = OpcionMenu.objects.values_list("modulo__codigo", flat=True).distinct()
    print(f"  No hay modulo 'usuario' o 'mi_cuenta'. Modulos disponibles: {list(mods)}")
for o in mis_opciones:
    print(f"  [{o.codigo}] {o.nombre}  (activo={o.activo})")
