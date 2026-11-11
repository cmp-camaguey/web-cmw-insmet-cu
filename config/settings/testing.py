"""Perfil `testing`: sin `DEBUG` y sin `PRODUCTION`."""

import os

from ._testing_keys import inject_testing_key_pair  # noqa: E402  (tiene que preceder a base)

inject_testing_key_pair()

from .base import *  # noqa: E402, F403
from .base import (  # noqa: E402, F401  (alias sin guion bajo: `import *` no lo exporta)
    _CONTENT_SECURITY_POLICY_DIRECTIVES,
    apply_external_hostname,
    resolve_obs_local_only,
)

DEBUG = False

# Security
SECURE_SSL_REDIRECT = False  # Nginx termina TLS y redirige a HTTPS; Django no lo hace.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

DEBUG_TOOLBAR_ENABLED = False

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

MIDDLEWARE = [mw for mw in MIDDLEWARE if mw != whitenoise_middleware]

DATABASES = get_database_config()

if external_hostname := os.getenv('EXTERNAL_HOSTNAME', ''):
    apply_external_hostname(external_hostname, ['http://', 'https://'])

OBS_LOCAL_ONLY_DEFAULT = False
OBS_LOCAL_ONLY = resolve_obs_local_only(OBS_LOCAL_ONLY_DEFAULT)

# El monitoreo mide trafico real: en tests no hay trafico que medir y una fila
# INSERT por peticion descuadraria los `assertNumQueries` de la suite. Los tests
# del propio monitoreo lo activan con `override_settings(MONITORING_ENABLED=True)`.
MONITORING_ENABLED = False
