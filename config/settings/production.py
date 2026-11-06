"""Perfil `production`: `PRODUCTION` presente en el entorno.

Lo elige el dispatcher solo si `PRODUCTION` está en `os.environ`. La exporta
`Environment=PRODUCTION=1` en `deploy/systemd/webcmp.service`, y no hay otro
lugar del que sale: por eso el perfil de producción NO se puede alcanzar por
otra vía, y no depende de `DEBUG` para ninguna de sus decisiones.
"""

import os

from .base import *  # noqa: F403
from .base import (  # noqa: F401  (alias sin guion bajo: `import *` no lo exporta)
    _CONTENT_SECURITY_POLICY_DIRECTIVES,
    apply_external_hostname,
    insecure_email_backends,
    resolve_obs_local_only,
    silent_email_backends,
)

DEBUG = False


if EMAIL_BACKEND.strip() in silent_email_backends:
    raise ImproperlyConfigured(
        f'Producción no puede enviar correo por {EMAIL_BACKEND.strip()}: ese backend '
        'acepta los mensajes y los descarta, así que se perderían en silencio. Defina '
        'un servidor SMTP real (EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, '
        'EMAIL_HOST_PASSWORD) y regenere el archivo con '
        '`python scripts/generate_env.py --production`.'
    )

if EMAIL_BACKEND.strip() in insecure_email_backends:
    raise ImproperlyConfigured(
        f'Producción no puede enviar correo por {EMAIL_BACKEND.strip()}: ese backend no '
        'verifica el certificado del servidor SMTP, así que el mensaje viaja por un '
        'intermediario en claro. Defina un backend SMTP real (EMAIL_BACKEND, EMAIL_HOST, '
        'EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD) y regenere el archivo con '
        '`python scripts/generate_env.py --production`.'
    )

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
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

if whitenoise_middleware not in MIDDLEWARE:
    MIDDLEWARE = [*MIDDLEWARE[:1], whitenoise_middleware, *MIDDLEWARE[1:]]


DATABASES = get_database_config()

if not os.getenv('SITE_URL', '').strip():
    raise ImproperlyConfigured(
        'Falta SITE_URL: los enlaces de los correos (factura, aviso, reporte del tiempo '
        'y restablecimiento de contraseña) necesitan la URL pública del sitio, y esa URL '
        'no se puede adivinar: un enlace a un host interno no lo puede abrir el '
        'destinatario. Defina SITE_URL en el archivo .env (por ejemplo '
        'SITE_URL=https://cmw.insmet.cu/) y regenere el archivo con '
        '`python scripts/generate_env.py --production`.'
    )

if external_hostname := os.getenv('EXTERNAL_HOSTNAME', ''):
    apply_external_hostname(external_hostname, ['http://', 'https://'])

OBS_LOCAL_ONLY_DEFAULT = False
OBS_LOCAL_ONLY = resolve_obs_local_only(OBS_LOCAL_ONLY_DEFAULT)
