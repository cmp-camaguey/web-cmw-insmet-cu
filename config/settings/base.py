"""Configuración compartida por los tres perfiles de `config.settings`.

Contiene lo que NO depende del perfil: `.env`, plantillas, apps, DRF, i18n,
estáticos, correo, LDAP, FTP, CSP y logging. Las decisiones de entorno (cookies
seguras, STORAGES, debug toolbar, WhiteNoise y base de datos) viven en `dev.py`,
`testing.py` y `production.py`.

REGLA ESTRUCTURAL (no negociable): este módulo NO ramifica sobre `DEBUG` ni
sobre `IS_PRODUCTION`, solo los lee como valores planos. Cada perfil hace
`from .base import *`, que ejecuta este archivo ANTES de que el perfil pueda
corregir nada: si aquí se calculara `DATABASES` con el `DEBUG` de `base`, el
perfil ya no podría imponer el suyo. Ver odd/tasks/split-config-settings.md.
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.contrib.messages import constants as messages
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse_lazy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / '.env')

IS_PRODUCTION = 'PRODUCTION' in os.environ

DEBUG = os.getenv('DEBUG', 'False') == 'True'

# ---------------------------------------------------------------------------
# URL canónica del sitio
# ---------------------------------------------------------------------------
# Los enlaces que viajan en un correo salen de acá y NUNCA del `request`: el
# destinatario no está en la red interna, y el host por el que el operador
# entró (`http://10.0.0.5:8000/`) no es el público.
#
# CONVENCIÓN: `SITE_URL` se expone CON barra final y las rutas se concatenan
# SIN barra inicial (`SITE_URL + reverse(...).lstrip('/')`). Así el host y la
# ruta quedan unidos por una sola barra, nunca por dos.
#
# El valor por defecto es el de desarrollo. En producción la variable es
# obligatoria y `config/settings/production.py` se niega a arrancar sin ella.
DEFAULT_SITE_URL = 'http://localhost:8000/'


def normalize_site_url(value, default=DEFAULT_SITE_URL):
    """Normaliza `SITE_URL`: sin espacios y con una sola barra final."""
    value = (value or '').strip()
    if not value:
        return default
    return value.rstrip('/') + '/'


SITE_URL = normalize_site_url(os.getenv('SITE_URL', ''))

# ---------------------------------------------------------------------------
# Cache backend
# ---------------------------------------------------------------------------
USE_REDIS_CACHE = os.getenv('USE_REDIS_CACHE', 'False') == 'True'

REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/1')


def build_caches(use_redis, redis_url):
    """Construye el dict CACHES de forma determinista y testeable."""
    caches = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        },
    }
    if use_redis:
        caches['default'] = {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': redis_url,
        }
    return caches


CACHES = build_caches(USE_REDIS_CACHE, REDIS_URL)


# ---------------------------------------------------------------------------
# Avisos operativos (cambio 026)
# ---------------------------------------------------------------------------
# Apagado: no se crean filas y el icono no se renderiza. La poda SIGUE
# corriendo a propósito: limpiar datos viejos no es una función visible que
# haya que apagar, y dejarla detenida solo hace crecer la tabla.
NOTIFICATIONS_ENABLED = os.getenv('NOTIFICATIONS_ENABLED', 'True') == 'True'
# Días que se conserva una notificación ya leída.
NOTIFICATIONS_RETENTION_READ = int(os.getenv('NOTIFICATIONS_RETENTION_READ', '30'))
# Días que se conserva una no leída. Un aviso de hace 90 días que nadie abrió
# ya no es accionable y solo infla el badge.
NOTIFICATIONS_RETENTION_UNREAD = int(os.getenv('NOTIFICATIONS_RETENTION_UNREAD', '90'))
# Entradas del desplegable del navbar. La página de historial no depende de esto.
NOTIFICATIONS_MAX_VISIBLE = int(os.getenv('NOTIFICATIONS_MAX_VISIBLE', '10'))
# Horas de antelación con que se avisa que un `Warning` está por vencer. Un día
# y medio alcanza para editar el aviso o preparar el siguiente sin sonar antes
# de tiempo: el aviso sigue vigente, solo está por terminar.
NOTIFICATIONS_WARNING_HORIZON_HOURS = int(os.getenv('NOTIFICATIONS_WARNING_HORIZON_HOURS', '48'))


def decrypt_secret_key(encrypted_secret_key, encryption_key):
    if not encrypted_secret_key or not encryption_key:
        return None

    cipher_suite = Fernet(encryption_key.encode())
    return cipher_suite.decrypt(encrypted_secret_key.encode()).decode()


def load_secret_key():
    """Arma `SECRET_KEY` desde el entorno, o se niega a arrancar."""
    encrypted = os.getenv('SECRET_KEY')
    encryption_key = os.getenv('ENCRYPTION_KEY')

    if not encrypted and not encryption_key:
        raise ImproperlyConfigured(
            'Falta el archivo .env: no hay SECRET_KEY ni ENCRYPTION_KEY. Generá el '
            'archivo con `python scripts/generate_env.py --development`, o con '
            '`--production` en el servidor.'
        )

    if not encryption_key:
        raise ImproperlyConfigured(
            'Falta ENCRYPTION_KEY: hay SECRET_KEY pero nada con qué descifrarla. En '
            'producción la clave de descifrado NO va en el .env, la carga systemd con '
            '`EnvironmentFile=-/etc/webcmp/encryption.env`. En el resto de los perfiles, '
            'regenerá el archivo con `python scripts/generate_env.py`.'
        )

    if not encrypted:
        raise ImproperlyConfigured(
            'Falta SECRET_KEY: hay ENCRYPTION_KEY pero ningún material cifrado que '
            'descifrar. Regenerá el archivo con `python scripts/generate_env.py`.'
        )

    try:
        return decrypt_secret_key(encrypted, encryption_key)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            'SECRET_KEY no descifra con ENCRYPTION_KEY: el par no corresponde. O el '
            'archivo de clave de descifrado es de otra instalación, o el .env se copió '
            'de otro servidor. `python scripts/generate_env.py` conserva las claves '
            'existentes si el par funciona y genera otras si no.'
        ) from exc


SECRET_KEY = load_secret_key()

# ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()
]
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv('CSRF_TRUSTED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(
        ','
    )
    if o.strip()
]


def apply_external_hostname(external_hostname, schemes):
    """Agrega el dominio de EXTERNAL_HOSTNAME a ALLOWED_HOSTS/CSRF_TRUSTED_ORIGINS."""
    domain = (
        external_hostname.replace('https://', '').replace('http://', '').split('/')[0].split(':')[0]
    )
    if domain not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(domain)
    for scheme in schemes:
        origin = f'{scheme}{domain}'
        if ':' in external_hostname.split('://')[-1]:
            origin += f':{external_hostname.split(":")[-1]}'
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)


# Application
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'csp',
    'drf_redesign',
    'rest_framework',
    'drf_spectacular',
    'drf_spectacular_sidecar',
    'apps.api.apps.ApiConfig',
    'apps.home.apps.HomeConfig',
    'apps.publications.apps.PublicationsConfig',
    'apps.core.apps.CoreConfig',
    'apps.user_auth.apps.UserAuthConfig',
    'apps.meteo.apps.MeteoConfig',
    'apps.commercial.apps.CommercialConfig',
    'apps.dashboard.apps.DashboardConfig',
]

whitenoise_middleware = 'whitenoise.middleware.WhiteNoiseMiddleware'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    whitenoise_middleware,
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'csp.middleware.CSPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.core.middleware.CheckUserProfileMiddleware',
    'apps.core.middleware.MaintenanceModeMiddleware',
]

# ---------------------------------------------------------------------------
# Content Security Policy (CSP) — django-csp
# ---------------------------------------------------------------------------
_CONTENT_SECURITY_POLICY_DIRECTIVES = {
    'default-src': ["'self'"],
    'base-uri': ["'self'"],
    'frame-ancestors': ["'self'"],
    'object-src': ["'self'"],
    'script-src': ["'self'", "'unsafe-inline'"],
    'style-src': ["'self'", "'unsafe-inline'"],
    'img-src': ["'self'", 'data:', 'https://cdn.jsdelivr.net'],
    'font-src': ["'self'"],
    'connect-src': ["'self'"],
}

CONTENT_SECURITY_POLICY = {'DIRECTIVES': _CONTENT_SECURITY_POLICY_DIRECTIVES}

if os.getenv('CSP_REPORT_ONLY', 'False') == 'True':
    CONTENT_SECURITY_POLICY = None
    CONTENT_SECURITY_POLICY_REPORT_ONLY = {
        'DIRECTIVES': _CONTENT_SECURITY_POLICY_DIRECTIVES,
    }

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

HOME_TEMPLATES = BASE_DIR / 'templates'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [HOME_TEMPLATES],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.menu_notifications',
                'apps.core.context_processors.site_branding',
            ],
        },
    },
]


# Database
def get_database_config():
    """Construye el dict DATABASES a partir de las variables de entorno.

    El motor es obligatorio en los tres perfiles. No hay respaldo silencioso: un
    perfil que no encuentre `DB_ENGINE` falla al arrancar, en vez de medir SQLite
    mientras producción corre PostgreSQL.
    """
    db_engine = os.getenv('DB_ENGINE', '').strip().lower()

    if not db_engine:
        raise ImproperlyConfigured(
            'Falta DB_ENGINE. Este proyecto solo corre sobre PostgreSQL: defina '
            'DB_ENGINE=postgresql y DB_NAME, DB_USER, DB_PASS y DB_HOST en el archivo '
            '.env. Ejecute `python scripts/generate_env.py --development` (o '
            '`--production`) para generarlo.'
        )

    if db_engine in {'sqlite', 'sqlite3'}:
        raise ImproperlyConfigured(
            'SQLite ya no se soporta: este proyecto solo corre sobre PostgreSQL. Borre '
            'DB_ENGINE=sqlite3 del archivo .env y defina DB_ENGINE=postgresql con '
            'DB_NAME, DB_USER, DB_PASS y DB_HOST. Ejecute '
            '`python scripts/generate_env.py --development` para regenerarlo. Los datos '
            'de una base SQLite existente no se convierten solos: hay que volcarlos '
            'aparte antes de borrar `db.sqlite3`.'
        )

    if not all(os.getenv(k) for k in ('DB_NAME', 'DB_USER', 'DB_HOST', 'DB_PASS')):
        raise ImproperlyConfigured(
            f'El motor "{db_engine}" necesita DB_NAME, DB_USER, DB_HOST y DB_PASS, y al menos '
            'uno falta o está vacío en el entorno. Defina las cuatro en el archivo .env.'
        )

    db_config = {
        'ENGINE': f'django.db.backends.{db_engine}',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASS'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT', '5432' if db_engine == 'postgresql' else '3306'),
        'CONN_MAX_AGE': 600,
    }

    ssl_mode = os.getenv('DB_SSL_MODE', 'prefer' if db_engine == 'postgresql' else 'PREFERRED')
    db_config['OPTIONS'] = {}

    if db_engine == 'postgresql':
        db_config['OPTIONS']['sslmode'] = ssl_mode
        if ssl_mode in ('verify-ca', 'verify-full') and (cert := os.getenv('DB_SSL_ROOT_CERT')):
            db_config['OPTIONS']['sslrootcert'] = cert

    elif db_engine == 'mysql':
        db_config['OPTIONS']['ssl_mode'] = ssl_mode.upper()
        if ssl_mode.upper() in ('VERIFY_CA', 'VERIFY_IDENTITY'):
            for k in ('ca', 'cert', 'key'):
                if v := os.getenv(f'DB_SSL_{k.upper()}'):
                    db_config['OPTIONS'].setdefault('ssl', {})[k] = v

    return {'default': db_config}


# El que arma DATABASES es el perfil

# Auth
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_REDIRECT_URL = reverse_lazy('home:index')
LOGOUT_REDIRECT_URL = reverse_lazy('home:index')
LOGIN_URL = reverse_lazy('user_auth:login')

GROUP_PERMISSION_EXCLUDED_APPS = [
    'auth',
    'user_auth',
    'admin',
    'contenttypes',
    'sessions',
    'messages',
    'staticfiles',
]
GROUP_PERMISSION_APP_MERGE = {
    'auth': 'user_auth',
}
GROUP_PERMISSION_MODEL_MERGE = {
    'Pronóstico': 'Pronóstico',
    'Pronóstico por Región': 'Pronóstico',
    'Día Extendido': 'Pronóstico',
    'Factura': 'Facturacion',
    'Item': 'Facturacion',
    'Correo': 'Lista de correo',
}

MESSAGE_TAGS = {
    messages.DEBUG: 'debug',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'danger',
}

# Internationalization
LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Havana'
USE_I18N = True
USE_TZ = True

TIME_INPUT_FORMATS = ['%H:%M:%S', '%H:%M:%S.%f', '%H:%M', '%I:%M %p', '%I:%M %P']
DATETIME_INPUT_FORMATS = [
    '%d/%m/%Y %H:%M:%S',
    '%d/%m/%Y %H:%M:%S.%f',
    '%d/%m/%Y %H:%M',
    '%d/%m/%y %H:%M:%S',
    '%d/%m/%y %H:%M:%S.%f',
    '%d/%m/%y %H:%M',
    '%d/%m/%Y %I:%M %p',
    '%d/%m/%y %I:%M %p',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%d %H:%M',
    '%Y-%m-%d',
]

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Los tests escriben en un MEDIA_ROOT temporal que se autolimpia
TEST_RUNNER = 'config.test_runner.IsolatedMediaRunner'

X_FRAME_OPTIONS = 'DENY'

SILENCED_SYSTEM_CHECKS = ['security.W008']

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.DjangoModelPermissionsOrAnonReadOnly'
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {'anon': '100/hour', 'user': '1000/hour'},
}

# FTP de observaciones (FileObs)
FTP_OBS_HOST = os.getenv('FTP_OBS_HOST')
FTP_OBS_USER = os.getenv('FTP_OBS_USER')
FTP_OBS_PASS = os.getenv('FTP_OBS_PASS')
FTP_OBS_PORT = os.getenv('FTP_OBS_PORT', '990')


def resolve_obs_local_only(default):
    """Resuelve OBS_LOCAL_ONLY a partir del default que impone el perfil."""
    return (
        default
        if 'OBS_LOCAL_ONLY' not in os.environ
        else os.getenv('OBS_LOCAL_ONLY', '0') in ('1', 'true', 'True', 'yes')
    )


SPECTACULAR_SETTINGS = {
    'TITLE': 'API Centro Meteorológico Camagüey',
    'DESCRIPTION': 'Documentación de la API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SWAGGER_UI_DIST': 'SIDECAR',
    'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    'REDOC_DIST': 'SIDECAR',
}

# CORS
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(
        ','
    )
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True
CORS_URLS_REGEX = r'^/api/.*$'

# Email
console_email_backend = 'django.core.mail.backends.console.EmailBackend'

silent_email_backends = (
    console_email_backend,
    'django.core.mail.backends.filebased.EmailBackend',
    'django.core.mail.backends.locmem.EmailBackend',
)
# Backends que aceptan el mensaje y lo entregan SIN verificar el certificado del
# servidor SMTP. No fallan y no se quejan: el correo sale, pero viaja por un
# intermediario en claro. En producción se rechazan (ver `production.py`).
insecure_email_backends = ('config.mail_backends.InsecureDevEmailBackend',)
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
# 465 es TLS implícito (SMTPS) y 587 es STARTTLS: son dos modos distintos, no dos
# variantes del mismo. `deploy/install.sh` escribe esta variable desde siempre,
# pero ningún settings la leía, así que elegir 465 dejaba a Django abriendo una
# conexión en claro contra un puerto que espera el handshake en el primer byte.
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False') == 'True'

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured(
        'EMAIL_USE_TLS y EMAIL_USE_SSL están activos a la vez, y Django los rechaza '
        'porque son modos excluyentes de cifrar la misma conexión. El puerto 465 usa '
        'TLS implícito (EMAIL_USE_SSL=True y EMAIL_USE_TLS=False); el 587 usa STARTTLS '
        '(EMAIL_USE_TLS=True y EMAIL_USE_SSL=False). Corrija el archivo .env.'
    )

EMAIL_HOST = os.getenv('EMAIL_HOST')
EMAIL_PORT = os.getenv('EMAIL_PORT')
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL')
EMAIL_FILE_PATH = os.getenv('EMAIL_FILE_PATH', BASE_DIR / 'tmp' / 'emails')

# LDAP
if os.getenv('LDAP_SERVER_URI'):
    AUTHENTICATION_BACKENDS = [
        'apps.user_auth.backends.LDAP3Backend',
        'django.contrib.auth.backends.ModelBackend',
    ]

    logger = logging.getLogger('ldap3_auth')
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {'format': '{levelname} {asctime} {module} {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'},
    },
    'loggers': {
        '': {'handlers': ['console'], 'level': 'INFO'},
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'weasyprint.progress': {'level': 'WARNING'},
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Actualizaciones desde el panel (change 025-update-web-deploy) ----------
# El chequeo lee el repo publico con `git ls-remote` sobre HTTPS: sin
# credenciales y sin escribir nada en el checkout del servidor.
UPDATE_REPO_URL = os.getenv(
    'UPDATE_REPO_URL', 'https://github.com/cmp-camaguey/web-cmw-insmet-cu.git'
)
UPDATE_BRANCH_REF = os.getenv('UPDATE_BRANCH_REF', 'refs/heads/main')
# `deploy.sh` escribe aca el commit que quedo sirviendo (una linea, 0644).
UPDATE_DEPLOYED_SHA_FILE = os.getenv('UPDATE_DEPLOYED_SHA_FILE', '/etc/webcmp/deployed')
# El usuario de despliegue escribe aca el estado del ultimo intento (JSON).
UPDATE_STATUS_FILE = os.getenv('UPDATE_STATUS_FILE', '/var/lib/webcmp/deploy-status.json')
# El panel escribe la peticion; una unidad systemd .path la recoge al aparecer.
UPDATE_REQUEST_FILE = os.getenv('UPDATE_REQUEST_FILE', '/var/lib/webcmp/deploy-request')
# Cuanto dura el resultado de `git ls-remote` en cache, y cuanto se espera al
# remoto. El panel es de administracion: 5 minutos de cache evitan que cada
# visita al dashboard abra una conexion, y 15 segundos acotan lo que puede
# bloquear una peticion cuando GitHub no responde.
#
# 15 y no 8: un servidor que sale por un proxy corporativo puede tardar en
# resolver o en que el proxy conteste, y con 8 s el panel reportaba "no
# respondio a tiempo" mientras la conexion directa tardaba medio segundo. El
# coste de subirlo es que una red caida bloquea la peticion 15 s en vez de 8;
# el coste de dejarlo bajo es un aviso falso que no se distingue de una caida.
UPDATE_CHECK_TTL = int(os.getenv('UPDATE_CHECK_TTL', '300'))
UPDATE_CHECK_TIMEOUT = int(os.getenv('UPDATE_CHECK_TIMEOUT', '15'))
# Que cambio, no solo cuanto: la API publica de GitHub resuelve el rango
# `deployed...latest` sin token, igual que `ls-remote`. Se consulta solo cuando
# hay actualizacion pendiente, y el resultado se cachea distinto que el fallo
# (ver `pending_commits`). 60 peticiones por hora sin autenticar: con 5 minutos
# de cache, un panel abierto todo el dia gasta 12.
UPDATE_REPO_API = os.getenv(
    'UPDATE_REPO_API', 'https://api.github.com/repos/cmp-camaguey/web-cmw-insmet-cu'
)
