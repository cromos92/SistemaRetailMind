"""
Catálogo de permisos: qué controla cada casillero (Ver / Crear / Editar /
Eliminar / Exportar / Aprobar) en cada pantalla.

La pantalla /app/permisos/gestion/ lo usa para explicar cada opción y para
atenuar los casilleros que hoy no controlan nada. El test
`app/tests/test_permisos_catalogo.py` obliga a documentar cada permiso que el
código exige (lo detecta `escaner.py`), así el catálogo no se desactualiza.

Un módulo por área (`ventas.py`, `documentos.py`, `existencias_compras.py`,
`reportes.py`, `otros.py`), cada uno con un dict `CATALOGO`:

    'cuadratura_caja': {
        'pantalla': 'Cuadratura y Arqueo',
        'ruta': '/app/ventas/cuadratura-caja/',   # '' si es una acción dentro de otra pantalla
        'resumen': 'Para qué sirve la pantalla.',
        'permisos': {'puede_ver': '…', 'puede_crear': '…'},   # solo los casilleros que hacen algo
        'depende_de': ['dte_editar_fecha'],   # opcional
        'notas': 'Matices.',                  # opcional
    }
"""
import importlib
import logging

logger = logging.getLogger('app')

MODULOS = ('ventas', 'documentos', 'existencias_compras', 'reportes', 'otros')

TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')
ETIQUETAS = {
    'puede_ver': 'Ver', 'puede_crear': 'Crear', 'puede_editar': 'Editar',
    'puede_eliminar': 'Eliminar', 'puede_exportar': 'Exportar', 'puede_aprobar': 'Aprobar',
}
# Qué significa cada casillero cuando la pantalla no dice nada más específico.
GENERICO = {
    'puede_ver': 'Entrar a la pantalla y verla en el menú.',
    'puede_crear': 'Crear registros o ejecutar la acción principal.',
    'puede_editar': 'Modificar registros existentes.',
    'puede_eliminar': 'Eliminar registros.',
    'puede_exportar': 'Descargar Excel / PDF.',
    'puede_aprobar': 'Aprobar o autorizar operaciones.',
}


def _cargar():
    catalogo = {}
    for nombre in MODULOS:
        try:
            modulo = importlib.import_module(f'{__name__}.{nombre}')
        except ImportError as exc:  # el módulo todavía no existe: se sigue sin él
            logger.warning('Catálogo de permisos: no se pudo cargar %s (%s)', nombre, exc)
            continue
        for codigo, ficha in getattr(modulo, 'CATALOGO', {}).items():
            if codigo in catalogo:
                logger.warning('Catálogo de permisos: %s definido dos veces (%s)', codigo, nombre)
            ficha = dict(ficha)
            ficha.setdefault('permisos', {})
            ficha.setdefault('depende_de', [])
            ficha.setdefault('notas', '')
            ficha['modulo_catalogo'] = nombre
            catalogo[codigo] = ficha
    return catalogo


CATALOGO = _cargar()


def descripcion(codigo):
    """Ficha del catálogo lista para la pantalla, o None si el código no está documentado."""
    ficha = CATALOGO.get(codigo)
    if ficha is None:
        return None
    permisos = ficha.get('permisos') or {}
    return {
        'pantalla': ficha.get('pantalla', ''),
        'ruta': ficha.get('ruta', ''),
        'resumen': ficha.get('resumen', ''),
        'permisos': {t: permisos[t] for t in TIPOS if t in permisos},
        'sin_efecto': [t for t in TIPOS if t not in permisos],
        'depende_de': list(ficha.get('depende_de') or []),
        'notas': ficha.get('notas', ''),
    }


def texto_permiso(codigo, tipo):
    """Texto del casillero, o el genérico si la pantalla no lo documenta."""
    ficha = CATALOGO.get(codigo) or {}
    return (ficha.get('permisos') or {}).get(tipo) or GENERICO.get(tipo, tipo)


def codigos_documentados():
    return set(CATALOGO)
