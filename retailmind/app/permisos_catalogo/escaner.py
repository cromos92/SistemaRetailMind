"""
Escáner estático de USOS de permisos en el código.

Recorre vistas, plantillas, el mapa del middleware y los decoradores y devuelve,
para cada (código de opción, tipo de permiso), dónde se exige de verdad. Es lo
que alimenta el catálogo (`app/permisos_catalogo`) y el test que obliga a
documentar cada permiso que el código usa.

No toca la base de datos. Solo lee archivos.
"""
import re
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent   # .../retailmind
CARPETAS = ('app', 'users', 'empresa_management', 'assistant')
IGNORAR = ('migrations', 'tests', '__pycache__', 'static', 'permisos_catalogo', 'management')
# Solo define los decoradores y trae ejemplos en docstrings: no son usos reales.
ARCHIVOS_IGNORADOS = ('app/decorators.py',)

TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')

_COD = r"['\"]([a-z0-9_]+)['\"]"
_TIPO = r"['\"](puede_[a-z]+)['\"]"

# Cada patrón captura (codigo, tipo|None). Los que no traen tipo son puede_ver.
PATRONES_PY = [
    ('decorador', re.compile(r"@requiere_permiso\(\s*" + _COD + r"(?:\s*,\s*(?:tipo_permiso=)?" + _TIPO + r")?")),
    ('tiene_permiso', re.compile(
        r"tiene_permiso\(\s*[^,()]+,\s*(?:codigo_opcion=)?" + _COD + r"(?:\s*,\s*(?:tipo_permiso=)?" + _TIPO + r")?")),
    ('tupla', re.compile(r"\(\s*" + _COD + r"\s*,\s*" + _TIPO + r"\s*\)")),   # verificar_permisos_multiples / alguno_de
]
PATRON_ALGUNO = re.compile(r"@requiere_alguno_de_los_permisos\(([^)]*)\)")
PATRON_URL_MAP = re.compile(r"^\s*'(/[^']+)'\s*:\s*'([a-z0-9_]+)'\s*,", re.M)

PATRONES_HTML = [
    ('tag', re.compile(r"\{%\s*tiene_permiso\s+" + _COD + r"(?:\s+" + _TIPO + r")?")),
    ('tag', re.compile(r"\{%\s*puede_ver_opcion_tag\s+" + _COD)),
    ('filtro', re.compile(r"\|puede_ver_opcion:" + _COD)),
    ('filtro_crear', re.compile(r"\|puede_crear_en:" + _COD)),
    ('filtro_editar', re.compile(r"\|puede_editar_en:" + _COD)),
    ('filtro_eliminar', re.compile(r"\|puede_eliminar_en:" + _COD)),
    ('filtro_exportar', re.compile(r"\|puede_exportar_en:" + _COD)),
    ('filtro_aprobar', re.compile(r"\|puede_aprobar_en:" + _COD)),
]
TIPO_DE_FILTRO = {
    'filtro_crear': 'puede_crear', 'filtro_editar': 'puede_editar', 'filtro_eliminar': 'puede_eliminar',
    'filtro_exportar': 'puede_exportar', 'filtro_aprobar': 'puede_aprobar',
}

PATRON_DEF = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.M)
PATRON_PATH = re.compile(r"path\(\s*['\"]([^'\"]*)['\"]\s*,\s*(?:views\.)?([A-Za-z_][A-Za-z0-9_.]*)")


def _archivos():
    for carpeta in CARPETAS:
        base = RAIZ / carpeta
        if not base.exists():
            continue
        for ruta in base.rglob('*'):
            if ruta.suffix not in ('.py', '.html', '.js'):
                continue
            if any(parte in IGNORAR for parte in ruta.relative_to(RAIZ).parts[:-1]):
                continue
            yield ruta


def _linea_de(texto, pos):
    return texto.count('\n', 0, pos) + 1


def _funcion_siguiente(texto, pos):
    """Nombre de la función definida justo después de `pos` (para decoradores)."""
    m = PATRON_DEF.search(texto, pos)
    return m.group(1) if m else ''


def _funcion_contenedora(texto, pos):
    """Última `def` antes de `pos` (para chequeos inline)."""
    ultimo = ''
    for m in PATRON_DEF.finditer(texto, 0, pos):
        ultimo = m.group(1)
    return ultimo


def rutas_por_vista():
    """{nombre_de_vista: [ruta_url, ...]} leído de app/urls.py y users/urls.py."""
    rutas = defaultdict(list)
    for archivo, prefijo in (('app/urls.py', '/app/'), ('users/urls.py', '/users/')):
        p = RAIZ / archivo
        if not p.exists():
            continue
        for m in PATRON_PATH.finditer(p.read_text(encoding='utf-8', errors='replace')):
            ruta, vista = m.group(1), m.group(2).split('.')[-1]
            rutas[vista].append(prefijo + ruta)
    return rutas


def escanear():
    """Devuelve {(codigo, tipo): [ {archivo, linea, via, funcion, rutas, contexto}, ... ]}."""
    usos = defaultdict(list)
    rutas = rutas_por_vista()

    def registrar(codigo, tipo, archivo, texto, pos, via, funcion=''):
        if codigo.startswith('puede_'):
            return  # `tiene_permiso(user, variable, 'puede_x')`: el código va en una variable
        tipo = tipo or 'puede_ver'
        linea = _linea_de(texto, pos)
        contexto = texto[texto.rfind('\n', 0, pos) + 1:texto.find('\n', pos)].strip()
        usos[(codigo, tipo)].append({
            'archivo': str(archivo.relative_to(RAIZ)).replace('\\', '/'),
            'linea': linea,
            'via': via,
            'funcion': funcion,
            'rutas': rutas.get(funcion, []) if funcion else [],
            'contexto': contexto[:160],
        })

    for ruta in _archivos():
        texto = ruta.read_text(encoding='utf-8', errors='replace')
        rel = str(ruta.relative_to(RAIZ)).replace('\\', '/')
        if rel in ARCHIVOS_IGNORADOS:
            continue

        if ruta.suffix == '.py':
            if rel.endswith('middleware_permisos.py'):
                for m in PATRON_URL_MAP.finditer(texto):
                    registrar(m.group(2), 'puede_ver', ruta, texto, m.start(), 'middleware:' + m.group(1))
                continue
            for via, patron in PATRONES_PY:
                for m in patron.finditer(texto):
                    if via == 'tupla':
                        # `@requiere_permiso('x', 'puede_y')` también calza como tupla: ya lo contó el decorador.
                        linea_txt = texto[texto.rfind('\n', 0, m.start()) + 1:texto.find('\n', m.start())]
                        if 'requiere_permiso(' in linea_txt or 'tiene_permiso(' in linea_txt:
                            continue
                    if via == 'decorador':
                        funcion = _funcion_siguiente(texto, m.end())
                    else:
                        funcion = _funcion_contenedora(texto, m.start())
                    registrar(m.group(1), m.group(2), ruta, texto, m.start(), via, funcion)
            for m in PATRON_ALGUNO.finditer(texto):
                funcion = _funcion_siguiente(texto, m.end())
                args = m.group(1)
                for t in re.finditer(r"\(\s*" + _COD + r"\s*,\s*" + _TIPO + r"\s*\)", args):
                    registrar(t.group(1), t.group(2), ruta, texto, m.start(), 'alguno_de', funcion)
                sin_tupla = re.sub(r"\([^)]*\)", '', args)
                for t in re.finditer(_COD, sin_tupla):
                    registrar(t.group(1), 'puede_ver', ruta, texto, m.start(), 'alguno_de', funcion)
        else:
            for via, patron in PATRONES_HTML:
                for m in patron.finditer(texto):
                    tipo = TIPO_DE_FILTRO.get(via) or (m.group(2) if m.re.groups > 1 else None)
                    registrar(m.group(1), tipo, ruta, texto, m.start(), via)
    return dict(usos)


def resumen_por_codigo(usos=None):
    """{codigo: {tipo: [usos...]}} ordenado, cómodo para revisar y documentar."""
    usos = usos if usos is not None else escanear()
    por_codigo = defaultdict(dict)
    for (codigo, tipo), lista in usos.items():
        por_codigo[codigo][tipo] = lista
    return {c: por_codigo[c] for c in sorted(por_codigo)}


if __name__ == '__main__':
    import json
    import sys
    json.dump(resumen_por_codigo(), sys.stdout, ensure_ascii=False, indent=1)
