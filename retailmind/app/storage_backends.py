"""
Storage de la evidencia fotográfica de requerimientos.

`MEDIA_ROOT` apunta al disco del contenedor, que es efímero: cada deploy borra
las fotos. Para el resto del sistema es molesto; para un requerimiento es
fatal, porque la foto ES la prueba del reclamo al proveedor.

Este módulo expone un callable que se pasa como `storage=` en el campo. Se
resuelve en tiempo de import del modelo:

- con `SPACES_ACCESS_KEY`/`SPACES_SECRET_KEY` definidas → DigitalOcean Spaces
- sin ellas (local, tests, CI) → el storage por defecto de Django

Por eso el alcance queda acotado a `FotoRequerimiento.imagen` y no se toca
`DEFAULT_FILE_STORAGE`: cotizaciones, evidencias de compras y fotos de
producto siguen exactamente donde están, con las mismas URLs.

**Importante para quien escriba código nuevo**: para leer estos archivos hay
que usar `foto.imagen.storage` / `foto.imagen.open()`, NUNCA `default_storage`
ni `foto.imagen.path`. Con Spaces activo `.path` lanza `NotImplementedError` y
`default_storage` apunta a otro backend, así que la lectura fallaría.
"""
import logging

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger('app')

_cache_storage = None


def _storage_local():
    """Storage del disco local (MEDIA_ROOT), servido por /media/."""
    from django.core.files.storage import FileSystemStorage
    return FileSystemStorage(location=settings.MEDIA_ROOT,
                             base_url=settings.MEDIA_URL)


def _construir_spaces_storage():
    """Storage de Spaces que SIGUE leyendo del disco local en la transición.

    Al activar Spaces, las fotos ya cargadas siguen viviendo en el disco del
    contenedor. Si el storage mirara únicamente el bucket, todas esas fotos
    aparecerían rotas de golpe (la ficha mostraba literalmente "Error").

    Por eso la lectura es **local primero**: si el archivo está en disco se
    sirve desde ahí —que es exactamente lo que pasaba antes— y si no está, se
    va al bucket. La escritura, en cambio, va SIEMPRE a Spaces. Así:

    - las fotos viejas se siguen viendo, sin migrar nada;
    - las nuevas nacen en el bucket y sobreviven al deploy;
    - cuando el deploy borre el disco, las que ya se subieron con
      `subir_fotos_requerimientos_spaces` se sirven solas desde el bucket.

    Además evita una llamada HEAD a S3 por cada foto que se muestra: el
    chequeo local es de disco.
    """
    from storages.backends.s3boto3 import S3Boto3Storage

    class EvidenciaSpacesStorage(S3Boto3Storage):

        def _respaldo_local(self):
            return _storage_local()

        def existe_en_bucket(self, name):
            """Solo el bucket, ignorando el respaldo local.

            `exists()` mira primero el disco, así que no sirve para saber si
            una foto YA se migró: el comando de subida se saltaría todas las
            que todavía están locales.
            """
            return S3Boto3Storage.exists(self, name)

        def exists(self, name):
            return self._respaldo_local().exists(name) or super().exists(name)

        def url(self, name, *args, **kwargs):
            local = self._respaldo_local()
            if local.exists(name):
                return local.url(name)
            return super().url(name, *args, **kwargs)

        def _open(self, name, mode='rb'):
            local = self._respaldo_local()
            if local.exists(name):
                return local.open(name, mode)
            return super()._open(name, mode)

        def size(self, name):
            local = self._respaldo_local()
            if local.exists(name):
                return local.size(name)
            return super().size(name)

        def delete(self, name):
            local = self._respaldo_local()
            if local.exists(name):
                local.delete(name)
            try:
                super().delete(name)
            except Exception:
                logger.warning('No se pudo borrar %s del Space', name)

    return EvidenciaSpacesStorage(
        bucket_name=settings.SPACES_BUCKET,
        endpoint_url=settings.SPACES_ENDPOINT,
        access_key=settings.SPACES_ACCESS_KEY,
        secret_key=settings.SPACES_SECRET_KEY,
        region_name=settings.SPACES_REGION,
        # Prefijo propio dentro del bucket compartido con el ecommerce.
        location=settings.SPACES_PREFIJO,
        default_acl=settings.SPACES_ACL,
        querystring_auth=(settings.SPACES_ACL != 'public-read'),
        querystring_expire=settings.SPACES_URL_EXPIRA_SEGUNDOS,
        # Nunca pisar un archivo existente: dos fotos distintas pueden llegar
        # con el mismo nombre desde dos celulares el mismo día.
        file_overwrite=False,
    )


def storage_evidencias():
    """Storage de la evidencia de requerimientos (Spaces si está configurado).

    Si Spaces está configurado pero la librería no está instalada, cae al
    storage por defecto y lo deja en el log: es preferible guardar la foto en
    disco efímero a rechazar la carga y perderla del todo.
    """
    global _cache_storage

    if not getattr(settings, 'SPACES_HABILITADO', False):
        return default_storage

    if _cache_storage is None:
        try:
            _cache_storage = _construir_spaces_storage()
        except ImportError:
            logger.error(
                'SPACES_* está configurado pero falta django-storages/boto3: '
                'las fotos de requerimientos seguirán en MEDIA_ROOT (efímero). '
                'Instalar con: pip install -r requirements.txt'
            )
            return default_storage
    return _cache_storage
