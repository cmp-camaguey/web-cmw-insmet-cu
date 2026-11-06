"""Perfil `dev`: desarrollo local con `DEBUG=True`."""

import logging
import os

from .base import *  # noqa: F403
from .base import (  # noqa: F401  (alias sin guion bajo: `import *` no lo exporta)
    _CONTENT_SECURITY_POLICY_DIRECTIVES,
    apply_external_hostname,
    resolve_obs_local_only,
)

DEBUG = True

if 'EMAIL_BACKEND' not in os.environ:
    EMAIL_BACKEND = console_email_backend

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

DEBUG_TOOLBAR_ENABLED = True
INTERNAL_IPS = ['127.0.0.1', '::1']
DEBUG_TOOLBAR_CONFIG = {'IS_RUNNING_TESTS': False}

INSTALLED_APPS = [*INSTALLED_APPS, 'debug_toolbar']
MIDDLEWARE = [mw for mw in MIDDLEWARE if mw != whitenoise_middleware] + [
    'debug_toolbar.middleware.DebugToolbarMiddleware'
]

# Opt-in explicito para probar Postgres en local. `DB_ENGINE` por si solo NO
# cambia la base de datos de dev: un `.env` copiado de produccion haria que la
# laptop escribiera en la base real, que es justo lo que impide
# `test_sqlite_wins_over_db_engine_env`. Con `DEV_USE_DB_ENGINE=True` y
# `DB_ENGINE=postgresql` (mas DB_NAME/USER/PASS/HOST) dev usa el motor de
# verdad; sin el flag, sqlite como siempre.
DEV_USE_DB_ENGINE = os.getenv('DEV_USE_DB_ENGINE', 'False') == 'True'
DATABASES = get_database_config(is_production=IS_PRODUCTION, prefer_sqlite=not DEV_USE_DB_ENGINE)

if external_hostname := os.getenv('EXTERNAL_HOSTNAME', ''):
    apply_external_hostname(external_hostname, ['http://'])

OBS_LOCAL_ONLY_DEFAULT = True
OBS_LOCAL_ONLY = resolve_obs_local_only(OBS_LOCAL_ONLY_DEFAULT)

if os.getenv('LDAP_SERVER_URI'):
    logging.getLogger('ldap3_auth').setLevel(logging.DEBUG)
