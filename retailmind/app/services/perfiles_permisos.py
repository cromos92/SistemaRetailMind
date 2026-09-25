"""
Política de perfiles: qué permisos tiene cada rol.

Definida por el dueño (25-sep-2026):

  Maestro         todo (no se configura: pasa siempre).
  Administrador   todo, menos: menú Fidelización, emitir NC a clientes (Gestión
                  DTE / Cambios), eliminar documentos de venta, devolver plata
                  por Mercado Pago. En Consulta Documentos solo cambia el medio /
                  tipo de tarjeta (no fecha, N°, vendedor ni folio). Único que
                  aprueba devoluciones por garantía.
  Jefe            igual que Administrador, pero sin Ajuste de Stock, sin cambiar
                  el medio de pago y sin aprobar garantías.
  Administración  como Jefe de Local, pero puede cambiarse de tienda (y eso se
                  ajusta por usuario en Permisos).
  Jefe Local / Cajero / Vendedor
                  lo que ya tienen, sin editar ni eliminar documentos de venta,
                  sin aprobar garantías y sin devoluciones MP.

La aplica `python manage.py configurar_perfiles` (vista previa por defecto).
`inicializar_permisos` la usa para SEMBRAR el rol Jefe en instalaciones nuevas
(solo crea filas que faltan). La jerarquía (quién asigna o edita a quién) vive
en `app.models.permisos.NIVEL_ROL`.
"""
from django.db import transaction
from django.db.models import Max, Q

from app.models import (
    OpcionMenu, PermisoRol, PermisoUsuario, ROL_JEFE, ROL_MAESTRO,
    CODIGO_DEVOLUCION_MP, CODIGO_NC_CLIENTES,
)

TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')
TODO = {t: True for t in TIPOS}
NADA = {t: False for t in TIPOS}


def _solo(*tipos):
    return {t: (t in tipos) for t in TIPOS}


FIDELIZACION = ('giftcards_listado', 'giftcards_emitir', 'fidelizacion_cuentas',
                'fidelizacion_programa', 'fidelizacion_reporte', 'fidelizacion_cupones')
# Consulta Documentos: campos del modal Editar que NADIE (salvo el Maestro) toca.
CAMPOS_DOCUMENTO = ('dte_editar_fecha', 'dte_editar_numero', 'dte_editar_vendedor', 'dte_editar_folio')
TIPOS_DOCUMENTO = ('dte_editar_tipo_boleta_electronica', 'dte_editar_tipo_boleta_papel',
                   'dte_editar_tipo_factura_electronica', 'dte_editar_tipo_factura_exenta')

# Lo que ningún rol (salvo el Maestro) puede hacer con documentos de venta.
RESTRICCION_DOCUMENTOS = {
    **{c: NADA for c in CAMPOS_DOCUMENTO},
    'dte_eliminar_documento': NADA,
    CODIGO_DEVOLUCION_MP: NADA,
    'devolucion_garantia': {'puede_aprobar': False},
}
# Además, sin cambiar el medio de pago (solo el Administrador lo conserva).
SIN_MEDIO_PAGO = {'dte_editar_pago': NADA, **{c: NADA for c in TIPOS_DOCUMENTO}}

AJUSTES_ADMINISTRADOR = {
    **{c: NADA for c in FIDELIZACION},
    CODIGO_NC_CLIENTES: NADA,
    'dte_eliminar_documento': NADA,
    CODIGO_DEVOLUCION_MP: NADA,
    **{c: NADA for c in CAMPOS_DOCUMENTO},
    # Cambiar el medio / tipo de tarjeta exige el permiso del campo Y el del
    # tipo de documento (puede_editar_campo_dte). Cambiar boleta E↔papel exige
    # además Editar N°, que queda apagado.
    'dte_editar_pago': _solo('puede_ver', 'puede_editar'),
    **{c: _solo('puede_ver', 'puede_editar') for c in TIPOS_DOCUMENTO},
    'devolucion_garantia': TODO,
}

PERFILES = {
    'administrador': {'base': 'TODO', 'ajustes': AJUSTES_ADMINISTRADOR},
    ROL_JEFE: {'base': 'TODO', 'ajustes': {
        **AJUSTES_ADMINISTRADOR,
        'ajuste_stock_rapido': NADA,
        **SIN_MEDIO_PAGO,
        'devolucion_garantia': {**TODO, 'puede_aprobar': False},
    }},
    'administracion': {'base': ('COPIA', 'jefe_local'), 'ajustes': {
        'cambiar_empresa': _solo('puede_ver'),
        **RESTRICCION_DOCUMENTOS,
        **SIN_MEDIO_PAGO,
    }},
    'jefe_local': {'base': 'ACTUAL', 'ajustes': {**RESTRICCION_DOCUMENTOS, **SIN_MEDIO_PAGO}},
    'cajero': {'base': 'ACTUAL', 'ajustes': {**RESTRICCION_DOCUMENTOS, **SIN_MEDIO_PAGO}},
    'vendedor': {'base': 'ACTUAL', 'ajustes': {**RESTRICCION_DOCUMENTOS, **SIN_MEDIO_PAGO}},
}
ORDEN = ('administrador', ROL_JEFE, 'administracion', 'jefe_local', 'cajero', 'vendedor')


def _limite(rol):
    v = PermisoRol.objects.filter(rol=rol).aggregate(m=Max('limite_descuento_porcentaje'))['m']
    return v if v is not None else 0


def objetivo(rol):
    """{codigo: flags} que debe tener `rol` según la política. Opciones sin
    entrada (base ACTUAL) se dejan como están."""
    perfil = PERFILES[rol]
    base = perfil['base']
    activas = {o.codigo: o for o in OpcionMenu.objects.filter(activo=True)}
    meta = {}
    if base == 'TODO':
        meta = {c: dict(TODO) for c in activas}
    elif isinstance(base, tuple) and base[0] == 'COPIA':
        for p in PermisoRol.objects.filter(rol=base[1], opcion_menu__activo=True).select_related('opcion_menu'):
            meta[p.opcion_menu.codigo] = {t: getattr(p, t) for t in TIPOS}
    # base 'ACTUAL': se parte de lo que hay.
    for codigo, flags in perfil['ajustes'].items():
        if codigo not in activas:
            continue
        if len(flags) == len(TIPOS):
            meta[codigo] = dict(flags)
        else:
            actual = meta.get(codigo)
            if actual is None:
                fila = PermisoRol.objects.filter(rol=rol, opcion_menu=activas[codigo]).first()
                actual = {t: getattr(fila, t) for t in TIPOS} if fila else dict(NADA)
            meta[codigo] = {**actual, **flags}
    return meta, activas


def aplicar(rol, escribir=False, solo_faltantes=False):
    """Lleva `rol` a la política. Devuelve la lista de cambios (texto).

    escribir=False    → solo calcula (vista previa).
    solo_faltantes    → crea filas que no existen y NO toca las existentes
                        (uso del seeder: nunca vuelve a encender algo apagado).
    """
    if rol == ROL_MAESTRO or rol not in PERFILES:
        return []
    meta, activas = objetivo(rol)
    existentes = {p.opcion_menu_id: p for p in PermisoRol.objects.filter(rol=rol)}
    limite = _limite(rol)
    if not existentes and PERFILES[rol]['base'] == 'TODO':
        limite = _limite('administrador')
    elif isinstance(PERFILES[rol]['base'], tuple):
        limite = _limite(PERFILES[rol]['base'][1])

    cambios = []
    with transaction.atomic():
        for codigo, flags in meta.items():
            opcion = activas[codigo]
            fila = existentes.get(opcion.id)
            if fila is None:
                if not any(flags.values()):
                    continue  # sin fila = sin acceso: no hace falta crearla apagada
                cambios.append(f'{rol}: {codigo} nueva → ' + _resumen(flags))
                if escribir:
                    PermisoRol.objects.create(rol=rol, opcion_menu=opcion,
                                              limite_descuento_porcentaje=limite, **flags)
                continue
            if solo_faltantes:
                continue
            diff = {t: v for t, v in flags.items() if getattr(fila, t) != v}
            if not diff:
                continue
            cambios.append(f'{rol}: {codigo} ' + ', '.join(
                f"{t.replace('puede_', '')} {'✓' if v else '✗'}" for t, v in diff.items()))
            if escribir:
                for t, v in diff.items():
                    setattr(fila, t, v)
                fila.save(update_fields=list(diff))

        if not solo_faltantes:
            cambios += _neutralizar_overrides(rol, meta, activas, escribir)
        if not escribir:
            transaction.set_rollback(True)
    return cambios


def _neutralizar_overrides(rol, meta, activas, escribir):
    """Un permiso individual en True le devolvería al usuario lo que la política
    le quita al rol: se pasa a «usar rol». Solo para opciones totalmente apagadas."""
    cambios = []
    apagadas = [activas[c].id for c, f in meta.items() if not any(f.values())]
    if not apagadas:
        return cambios
    overrides = PermisoUsuario.objects.filter(
        usuario__rol=rol, opcion_menu_id__in=apagadas,
    ).filter(Q(puede_ver=True) | Q(puede_crear=True) | Q(puede_editar=True)
             | Q(puede_eliminar=True) | Q(puede_exportar=True) | Q(puede_aprobar=True)
             ).select_related('usuario', 'opcion_menu')
    for ov in overrides:
        cambios.append(f'{rol}: override de {ov.usuario.username} en {ov.opcion_menu.codigo} → usar rol')
        if escribir:
            for t in TIPOS:
                if getattr(ov, t) is True:
                    setattr(ov, t, None)
            ov.save(update_fields=list(TIPOS))
    return cambios


def _resumen(flags):
    on = [t.replace('puede_', '') for t, v in flags.items() if v]
    return ', '.join(on) if on else 'sin acceso'
