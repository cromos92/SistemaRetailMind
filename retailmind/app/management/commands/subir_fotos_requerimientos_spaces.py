"""
Sube a Spaces las fotos de requerimientos que hoy viven en el disco local.

Contexto: hasta la migración 0211 la evidencia se guardaba en `MEDIA_ROOT`, que
en el contenedor es efímero. Este comando copia lo que TODAVÍA exista en disco
al Space, para no perder los requerimientos ya cargados.

Es aditivo: solo SUBE archivos, nunca borra el original ni toca la BD (la ruta
guardada es la misma en ambos storages). Se puede correr las veces que sea: lo
que ya está arriba se omite.

Uso:
    python manage.py subir_fotos_requerimientos_spaces              # dry-run
    python manage.py subir_fotos_requerimientos_spaces --aplicar
    python manage.py subir_fotos_requerimientos_spaces --aplicar --desde 2026-01-01
"""
from datetime import datetime

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.models import FotoRequerimiento
from app.storage_backends import storage_evidencias


class Command(BaseCommand):
    help = 'Sube al Space las fotos de requerimientos que estén en el disco local'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Sube de verdad (por defecto solo informa)')
        parser.add_argument('--desde', type=str, default=None,
                            help='Solo fotos subidas desde esta fecha (YYYY-MM-DD)')
        parser.add_argument('--limite', type=int, default=None,
                            help='Tope de archivos a procesar')

    def handle(self, *args, **opciones):
        if not getattr(settings, 'SPACES_HABILITADO', False):
            raise CommandError(
                'SPACES_ACCESS_KEY / SPACES_SECRET_KEY no están definidas: '
                'no hay a dónde subir. Configúralas antes de correr esto.'
            )

        remoto = storage_evidencias()
        if isinstance(remoto, FileSystemStorage):
            raise CommandError(
                'El storage de evidencias resolvió a disco local. '
                'Falta instalar django-storages/boto3 (pip install -r requirements.txt).'
            )

        local = FileSystemStorage(location=settings.MEDIA_ROOT)
        aplicar = opciones['aplicar']

        fotos = FotoRequerimiento.objects.select_related('requerimiento').order_by('id')
        if opciones['desde']:
            try:
                desde = timezone.make_aware(
                    datetime.strptime(opciones['desde'], '%Y-%m-%d'))
            except ValueError:
                raise CommandError('--desde debe tener formato YYYY-MM-DD')
            fotos = fotos.filter(fecha_subida__gte=desde)
        if opciones['limite']:
            fotos = fotos[:opciones['limite']]

        subidas = omitidas_ya_estan = perdidas = errores = 0
        bytes_subidos = 0

        for foto in fotos:
            nombre = foto.imagen.name if foto.imagen else ''
            if not nombre:
                continue

            if not local.exists(nombre):
                # El archivo ya no está en disco: se perdió en algún deploy.
                perdidas += 1
                self.stdout.write(self.style.WARNING(
                    f'  PERDIDA  {foto.requerimiento.numero_requerimiento}  {nombre}'))
                continue

            if remoto.exists(nombre):
                omitidas_ya_estan += 1
                continue

            tamano = local.size(nombre)
            if not aplicar:
                subidas += 1
                bytes_subidos += tamano
                self.stdout.write(
                    f'  [dry-run] subiría {nombre} ({tamano / 1024:.0f} KB)')
                continue

            try:
                with local.open(nombre, 'rb') as fh:
                    guardado = remoto.save(nombre, fh)
                subidas += 1
                bytes_subidos += tamano
                if guardado != nombre:
                    # `file_overwrite=False` renombra si ya existía: se avisa
                    # porque la BD seguiría apuntando al nombre original.
                    self.stdout.write(self.style.WARNING(
                        f'  OJO: se guardó como {guardado} (esperado {nombre})'))
            except Exception as e:
                errores += 1
                self.stdout.write(self.style.ERROR(f'  ERROR {nombre}: {e}'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"SUBIDAS" if aplicar else "A SUBIR (dry-run)"}: {subidas} '
            f'({bytes_subidos / 1024 / 1024:.1f} MB)'))
        self.stdout.write(f'Ya estaban en el Space: {omitidas_ya_estan}')
        self.stdout.write(f'Sin archivo en disco (perdidas): {perdidas}')
        if errores:
            self.stdout.write(self.style.ERROR(f'Errores: {errores}'))
        if not aplicar and subidas:
            self.stdout.write('')
            self.stdout.write('Volver a correr con --aplicar para subirlas.')
