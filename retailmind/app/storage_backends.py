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


def _construir_spaces_storage():
    """Instancia de S3Boto3Storage apuntando a la carpeta de RetailMind."""
    from storages.backends.s3boto3 import S3Boto3Storage

    return S3Boto3Storage(
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
