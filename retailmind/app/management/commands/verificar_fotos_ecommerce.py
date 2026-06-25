"""Verifica que las portadas sincronizadas desde los ecommerces "realmente se
pasaron": cobertura del catálogo + liveness HTTP de cada ``url_foto``.

A diferencia de ``sincronizar_fotos_ecommerce`` (que dice cuántos SKU matchearon),
este comando comprueba que las URLs devuelvan una imagen viva (200 + image/*) y
cuánto del catálogo, por sucursal, termina mostrando foto.

Uso:
    # Una integración, muestra rápida de 50 URLs:
    python manage.py verificar_fotos_ecommerce --codigo realsport --muestra 50

    # Solo conteo de cobertura (sin tocar el CDN):
    python manage.py verificar_fotos_ecommerce --codigo realsport --solo-cobertura

    # Todas las integraciones de las empresas asignadas a un usuario:
    python manage.py verificar_fotos_ecommerce --usuario jav.teb@gmail.com

    # Todas las integraciones activas (barrido completo):
    python manage.py verificar_fotos_ecommerce
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from app.models import CredencialesEcommerce
from app.services.verificacion_fotos_service import (
    VerificacionFotosError,
    persistir_resultado,
    verificar_credencial,
)


class Command(BaseCommand):
    help = 'Verifica cobertura y liveness de las portadas de los ecommerces.'

    def add_arguments(self, parser):
        parser.add_argument('--codigo', default=None,
                            help='Sólo esta credencial (por codigo).')
        parser.add_argument('--empresa', type=int, default=None,
                            help='Sólo credenciales de esta empresa (id).')
        parser.add_argument('--usuario', default=None,
                            help='Credenciales de las empresas asignadas a este usuario (id o email).')
        parser.add_argument('--muestra', type=int, default=None,
                            help='Verificar sólo una muestra aleatoria de N URLs.')
        parser.add_argument('--solo-cobertura', action='store_true',
                            help='Salta el chequeo HTTP; solo cuenta cobertura.')
        parser.add_argument('--workers', type=int, default=12,
                            help='Threads para los chequeos HTTP (default 12).')
        parser.add_argument('--timeout', type=int, default=10,
                            help='Timeout por request en segundos (default 10).')

    def _empresas_de_usuario(self, valor):
        from app.utils_permisos import obtener_empresas_usuario
        User = get_user_model()
        usuario = None
        if str(valor).isdigit():
            usuario = User.objects.filter(pk=int(valor)).first()
        if usuario is None:
            usuario = User.objects.filter(email__iexact=str(valor)).first()
        if usuario is None:
            raise CommandError(f'Usuario no encontrado: {valor}')
        return list(obtener_empresas_usuario(usuario).values_list('id', flat=True))

    def handle(self, *args, **opts):
        codigo = opts.get('codigo')
        empresa_id = opts.get('empresa')
        usuario = opts.get('usuario')
        muestra = opts.get('muestra')
        solo_cobertura = opts.get('solo_cobertura')
        workers = max(1, min(32, opts['workers']))
        timeout = max(1, opts['timeout'])

        qs = CredencialesEcommerce.objects.filter(activo=True).select_related('empresa').order_by('-prioridad')
        if codigo:
            qs = qs.filter(codigo=codigo)
        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)
        if usuario:
            qs = qs.filter(empresa_id__in=self._empresas_de_usuario(usuario))

        credenciales = list(qs)
        if not credenciales:
            self.stdout.write(self.style.WARNING('No hay credenciales activas que verificar.'))
            return

        for cred in credenciales:
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'>>> {cred.nombre} ({cred.codigo}) — empresa {cred.empresa.nombre}'
            ))

            def progreso(done, total):
                self.stdout.write(f'  urls verificadas: {done}/{total}')

            try:
                resultado = verificar_credencial(
                    cred, muestra=muestra, solo_cobertura=solo_cobertura,
                    workers=workers, timeout=timeout, on_progress=progreso,
                )
            except VerificacionFotosError as exc:
                self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))
                continue

            persistir_resultado(cred, resultado)
            self._imprimir(resultado)

    def _imprimir(self, resultado):
        cob = resultado['cobertura']
        self.stdout.write(self.style.SUCCESS(
            f'  Cobertura empresa: {cob["con_foto"]}/{cob["articulos"]} articulos con foto '
            f'({cob["sin_foto"]} sin foto)'
        ))
        for s in cob['por_sucursal']:
            self.stdout.write(
                f'    - {s["sucursal"]}: {s["con_foto"]}/{s["articulos"]} con foto'
            )

        u = resultado['urls']
        if u['verificadas']:
            c = u['counters']
            marca = ' (muestra)' if u['muestra'] else ''
            self.stdout.write(self.style.SUCCESS(
                f'  URLs ({u["verificadas"]}/{u["total_urls"]} verificadas{marca}): '
                f'{c["ok"]} ok, {c["http_404"]} 404, {c["no_imagen"]} no-img, '
                f'{c["http_otro"]} otro, {c["error_red"]} red'
            ))
            muertas = u['muertas_ejemplos']
            if muertas:
                self.stdout.write(self.style.WARNING(f'  Ejemplos de URLs con problema ({len(muertas)}):'))
                for m in muertas[:15]:
                    self.stdout.write(
                        f'    [{m["motivo"]}] {m["articulo"]} → {m["url"]}'
                    )
        else:
            self.stdout.write('  (liveness omitido)')
