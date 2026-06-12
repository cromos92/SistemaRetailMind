"""
Auditoria no destructiva de menu, OpcionMenu y permisos por rol.

Uso:
    python manage.py auditar_menu_permisos
    python manage.py auditar_menu_permisos --strict
"""
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from app.models import OpcionMenu, PermisoRol


class Command(BaseCommand):
    help = 'Audita menu.html contra OpcionMenu, permisos por rol y url_name resolubles.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--menu-path',
            default=None,
            help='Ruta alternativa al template menu.html. Por defecto usa app/templates/layout/menu.html.',
        )
        parser.add_argument(
            '--strict',
            action='store_true',
            help='Retorna error si encuentra problemas criticos.',
        )

    def handle(self, *args, **options):
        menu_path = self._resolver_menu_path(options.get('menu_path'))
        menu_html = menu_path.read_text(encoding='utf-8')

        menu_codes = self._extraer_codigos_menu(menu_html)
        hrefs_hardcodeados = self._extraer_hrefs_hardcodeados(menu_html)
        opciones = list(
            OpcionMenu.objects
            .select_related('modulo', 'padre')
            .filter(activo=True)
            .order_by('modulo__orden', 'orden', 'codigo')
        )
        opciones_por_codigo = {op.codigo: op for op in opciones}

        problemas = []
        advertencias = []

        self.stdout.write('Auditoria menu/permisos')
        self.stdout.write(f'Menu: {menu_path}')
        self.stdout.write(f'Codigos usados en menu: {len(menu_codes)}')
        self.stdout.write(f'Hrefs hardcodeados internos: {len(hrefs_hardcodeados)}')
        self.stdout.write(f'Opciones activas en BD: {len(opciones)}')

        for href in hrefs_hardcodeados:
            advertencias.append(
                f"Menu contiene href interno hardcodeado; preferir {{% url %}} si existe ruta nombrada: {href}"
            )

        codigos_sin_bd = sorted(code for code in menu_codes if code not in opciones_por_codigo)
        for codigo in codigos_sin_bd:
            problemas.append(f"Menu usa codigo sin OpcionMenu activa: {codigo}")

        opciones_visibles_sin_menu = [
            op for op in opciones
            if op.codigo not in menu_codes and (op.url_name or op.url_path)
        ]
        for opcion in opciones_visibles_sin_menu:
            advertencias.append(
                f"Opcion activa con URL no referenciada en menu: {opcion.codigo} ({opcion.nombre})"
            )

        for opcion in opciones:
            if opcion.url_name:
                try:
                    reverse(opcion.url_name)
                except NoReverseMatch:
                    problemas.append(
                        f"url_name no resoluble para {opcion.codigo}: {opcion.url_name}"
                    )
            elif opcion.url_path:
                if not opcion.url_path.startswith('/'):
                    problemas.append(
                        f"url_path invalido para {opcion.codigo}: {opcion.url_path}"
                    )
            elif opcion.codigo in menu_codes and not opcion.es_submenu:
                advertencias.append(
                    f"Opcion de menu sin url_name/url_path: {opcion.codigo} ({opcion.nombre})"
                )

        roles = [rol for rol, _nombre in PermisoRol.ROLES_CHOICES]
        for rol in roles:
            permisos_ids = set(
                PermisoRol.objects
                .filter(rol=rol, opcion_menu__activo=True)
                .values_list('opcion_menu_id', flat=True)
            )
            faltantes = [op.codigo for op in opciones if op.id not in permisos_ids]
            if faltantes:
                advertencias.append(
                    f"Rol {rol} sin PermisoRol para {len(faltantes)} opcion(es): {', '.join(faltantes[:12])}"
                    + ('...' if len(faltantes) > 12 else '')
                )

        self._imprimir_seccion('Problemas criticos', problemas, self.style.ERROR)
        self._imprimir_seccion('Advertencias', advertencias, self.style.WARNING)

        if not problemas:
            self.stdout.write(self.style.SUCCESS('OK: no se encontraron problemas criticos.'))

        if options.get('strict') and problemas:
            raise CommandError(f'Auditoria fallida: {len(problemas)} problema(s) critico(s).')

    def _resolver_menu_path(self, menu_path):
        if menu_path:
            path = Path(menu_path)
        else:
            path = Path(__file__).resolve().parents[2] / 'templates' / 'layout' / 'menu.html'

        if not path.exists():
            raise CommandError(f'No existe el template de menu: {path}')

        return path

    def _extraer_codigos_menu(self, menu_html):
        patrones = [
            r"puede_ver_opcion:'([^']+)'",
            r'puede_ver_opcion:"([^"]+)"',
            r"puede_ver_opcion_tag\s+'([^']+)'",
            r'tiene_permiso\s+\'([^\']+)\'',
        ]
        codigos = set()
        for patron in patrones:
            codigos.update(re.findall(patron, menu_html))
        return codigos

    def _extraer_hrefs_hardcodeados(self, menu_html):
        hrefs = set()
        for href in re.findall(r'href=["\']([^"\']+)["\']', menu_html):
            if href.startswith(('/app/', '/empresa_management/')):
                hrefs.add(href)
        return sorted(hrefs)

    def _imprimir_seccion(self, titulo, items, style):
        self.stdout.write('')
        if not items:
            self.stdout.write(self.style.SUCCESS(f'{titulo}: 0'))
            return

        self.stdout.write(style(f'{titulo}: {len(items)}'))
        for item in items:
            self.stdout.write(f'  - {item}')
