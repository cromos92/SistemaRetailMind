"""
Settings de TEST aislados — SQLite en memoria, esquema creado desde los
modelos actuales (migraciones deshabilitadas). NO toca la BD de producción
del .env; sirve para validar lógica de negocio con `manage.py test`.

Uso:
    python manage.py test app.tests.test_campanas_liquidacion \
        --settings=test_settings_sqlite
"""
from retailmind.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}


class _DisableMigrations:
    """Django crea el esquema directo desde los modelos (estilo syncdb),
    sin correr las 188 migraciones — así no se necesita la 0188 y el test
    es independiente del estado de migraciones."""
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _DisableMigrations()

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Storage simple (evita el manifest comprimido de whitenoise en test).
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

DEBUG = False
