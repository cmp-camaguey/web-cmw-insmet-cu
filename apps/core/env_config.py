"""Catálogo y armado de la configuración del entorno que muestra el panel.

Tres decisiones que explican la forma de este módulo:

1. **Se leen valores efectivos, no el archivo.** `config/settings/base.py` carga
   el `.env` al arrancar y de ahí deriva `DATABASES`, `CACHES`,
   `AUTHENTICATION_BACKENDS` y compañía. Mostrar el archivo crudo mentiría: hay
   claves que llegan por systemd (`ENCRYPTION_KEY` en producción), claves que
   ningún settings lee (`LOG_LEVEL`, `LOG_FILE`) y valores que no son 1:1 con
   una variable. La página informa lo que el proceso está usando de verdad.
2. **La lista blanca excluye lo que impide arrancar.** Base de datos, hosts
   permitidos, CSRF/CORS, `DEBUG`/`PRODUCTION`, `SECRET_KEY`/`ENCRYPTION_KEY` y
   `EMAIL_BACKEND` quedan de solo lectura: un error ahí deja el sitio sin
   arrancar, sin sesión o sin correo, y no se puede corregir desde un panel que
   no levanta. El resto de Correo, LDAP y Actualizaciones sí es editable, con
   validación de las combinaciones que Django rechaza al iniciar.
3. **Los secretos no se renderizan nunca.** Ni truncados, ni con longitud. Solo
   se informa si están configurados, y los campos de contraseña se muestran
   siempre vacíos: dejarlos en blanco significa "no cambiar".
"""

from __future__ import annotations

import os
import platform
import urllib.parse
from dataclasses import dataclass

import django
from django.conf import settings


@dataclass(frozen=True)
class EnvField:
    """Un parámetro administrable desde el panel."""

    key: str
    label: str
    kind: str  # 'bool' | 'int' | 'url' | 'text' | 'password'
    group: str
    help_text: str = ''
    min_value: int | None = None
    max_value: int | None = None
    required: bool = True
    default: str = ''
    # Texto de ejemplo del input. Vacío en los checkboxes, donde no aplica.
    placeholder: str = ''
    # Ancho en la grilla del panel: 'half' (media columna) o 'full' (fila
    # completa). Los valores largos —URLs, DN, remitentes— van a lo ancho para
    # que se lean sin desplazarse; los números y los interruptores comparten
    # fila. Se declara acá para que la plantilla no tenga una lista paralela.
    width: str = 'half'
    # De dónde sale el valor en ejecución. Casi todo son atributos de
    # `settings`; LDAP se lee del entorno en cada autenticación.
    source: str = 'settings'
    # Campo de contraseña: nunca se precarga y dejarlo vacío no borra nada.
    secret: bool = False


# Orden en que se muestran. Un parámetro nuevo se agrega acá y aparece en el
# formulario, en la plantilla y en los tests sin tocar nada más.
EDITABLE_FIELDS = (
    EnvField(
        key='SITE_URL',
        label='URL pública del sitio',
        kind='url',
        group='app',
        width='full',
        placeholder='Ej.: https://cmp.camaguey.cu/',
        help_text=('Es la que viaja en los enlaces de los correos. Se normaliza con barra final.'),
    ),
    EnvField(
        key='EMERGENCY_BANNER_ENABLED',
        label='Franja de aviso de emergencia',
        kind='bool',
        group='notifications',
        help_text=('Muestra el aviso vigente en el portal público, sin sesión y sin JavaScript.'),
    ),
    EnvField(
        key='NOTIFICATIONS_ENABLED',
        label='Avisos operativos',
        kind='bool',
        group='notifications',
        help_text='Apagado no crea avisos nuevos. La poda de los viejos sigue corriendo.',
    ),
    EnvField(
        key='NOTIFICATIONS_MAX_VISIBLE',
        label='Entradas visibles en el navbar',
        kind='int',
        group='notifications',
        placeholder='Ej.: 10',
        min_value=1,
        max_value=100,
        default='10',
    ),
    EnvField(
        key='NOTIFICATIONS_WARNING_HORIZON_HOURS',
        label='Antelación del aviso de vencimiento (horas)',
        kind='int',
        group='notifications',
        placeholder='Ej.: 48',
        min_value=1,
        max_value=720,
        default='48',
    ),
    EnvField(
        key='NOTIFICATIONS_RETENTION_READ',
        label='Retención de avisos leídos (días)',
        kind='int',
        group='notifications',
        placeholder='Ej.: 30',
        min_value=1,
        max_value=3650,
        default='30',
    ),
    EnvField(
        key='NOTIFICATIONS_RETENTION_UNREAD',
        label='Retención de avisos no leídos (días)',
        kind='int',
        group='notifications',
        placeholder='Ej.: 90',
        min_value=1,
        max_value=3650,
        default='90',
    ),
    EnvField(
        key='INVOICE_PENDING_REMINDER_DAYS',
        label='Recordatorio de factura pendiente (días)',
        kind='int',
        group='notifications',
        width='full',
        placeholder='Ej.: 7',
        min_value=1,
        max_value=365,
        default='7',
    ),
    EnvField(
        key='EMAIL_HOST',
        label='Servidor SMTP',
        kind='text',
        group='email',
        width='full',
        placeholder='Ej.: smtp.cmp.cu',
        required=False,
        help_text='Nombre o IP del servidor de correo saliente.',
    ),
    EnvField(
        key='EMAIL_PORT',
        label='Puerto SMTP',
        kind='int',
        group='email',
        width='full',
        placeholder='Ej.: 587',
        min_value=1,
        max_value=65535,
        required=False,
        help_text='El 587 usa STARTTLS y el 465 usa TLS implícito.',
    ),
    EnvField(
        key='EMAIL_HOST_USER',
        label='Usuario SMTP',
        kind='text',
        group='email',
        width='full',
        placeholder='Ej.: avisos@cmp.cu',
        required=False,
    ),
    EnvField(
        key='EMAIL_USE_TLS',
        label='STARTTLS',
        kind='bool',
        group='email',
        help_text='Cifra la conexión después del saludo. Es el modo del puerto 587.',
    ),
    EnvField(
        key='EMAIL_USE_SSL',
        label='TLS implícito',
        kind='bool',
        group='email',
        help_text='Cifra desde el primer byte. Es el modo del puerto 465.',
    ),
    EnvField(
        key='EMAIL_HOST_PASSWORD',
        label='Contraseña SMTP',
        kind='password',
        group='email',
        width='full',
        placeholder='Ej.: contraseña nueva',
        required=False,
        secret=True,
    ),
    EnvField(
        key='DEFAULT_FROM_EMAIL',
        label='Remitente por defecto',
        kind='text',
        group='email',
        width='full',
        placeholder='Ej.: Centro Meteorológico Camagüey <avisos@cmp.cu>',
        required=False,
        help_text='Lo que ve el destinatario, por ejemplo Centro Meteorológico <avisos@cmp.cu>.',
    ),
    EnvField(
        key='LDAP_ENABLED',
        label='Autenticación LDAP',
        kind='bool',
        group='ldap',
        source='settings',
        help_text=(
            'Apagado, solo se puede iniciar sesión con usuarios locales. '
            'El cambio se aplica al reiniciar.'
        ),
    ),
    EnvField(
        key='LDAP_START_TLS',
        label='STARTTLS en LDAP',
        kind='bool',
        group='ldap',
        source='environ',
        default='True',
        help_text='Cifra la conexión con el directorio después del saludo.',
    ),
    EnvField(
        key='LDAP_SERVER_URI',
        label='Servidor LDAP',
        kind='text',
        group='ldap',
        width='full',
        placeholder='Ej.: ldap://dc.cmp.cu:389',
        required=False,
        source='environ',
        help_text='Host del directorio, con esquema y puerto.',
    ),
    EnvField(
        key='LDAP_BIND_DN',
        label='DN de bind',
        kind='text',
        group='ldap',
        width='full',
        placeholder='Ej.: cn=admin,dc=cmp,dc=cu',
        required=False,
        source='environ',
        help_text='Cuenta con la que se consulta el directorio.',
    ),
    EnvField(
        key='LDAP_BIND_PASSWORD',
        label='Contraseña de bind',
        kind='password',
        group='ldap',
        width='full',
        placeholder='Ej.: contraseña nueva',
        required=False,
        source='environ',
        secret=True,
    ),
    EnvField(
        key='LDAP_USER_SEARCH_BASE',
        label='Base de búsqueda de usuarios',
        kind='text',
        group='ldap',
        width='full',
        placeholder='Ej.: ou=users,dc=cmp,dc=cu',
        required=False,
        source='environ',
    ),
    EnvField(
        key='LDAP_GROUP_SEARCH_BASE',
        label='Base de búsqueda de grupos',
        kind='text',
        group='ldap',
        width='full',
        placeholder='Ej.: ou=groups,dc=cmp,dc=cu',
        required=False,
        source='environ',
    ),
    EnvField(
        key='LDAP_STAFF_GROUP',
        label='Grupo de staff',
        kind='text',
        group='ldap',
        width='full',
        placeholder='Ej.: cn=staff,ou=groups,dc=cmp,dc=cu',
        required=False,
        source='environ',
    ),
    EnvField(
        key='LDAP_SUPERUSER_GROUP',
        label='Grupo de superusuarios',
        kind='text',
        group='ldap',
        width='full',
        placeholder='Ej.: cn=superuser,ou=groups,dc=cmp,dc=cu',
        required=False,
        source='environ',
    ),
    EnvField(
        key='UPDATE_REPO_URL',
        label='Repositorio de actualizaciones',
        kind='text',
        group='updates',
        width='full',
        placeholder='Ej.: https://github.com/cmp-camaguey/webcmp.git',
        help_text='Origen que consulta el panel para detectar versiones nuevas.',
    ),
    EnvField(
        key='UPDATE_REPO_API',
        label='API del repositorio',
        kind='url',
        group='updates',
        width='full',
        placeholder='Ej.: https://api.github.com/repos/cmp-camaguey/webcmp',
        help_text='Endpoint que lista los commits pendientes de desplegar.',
    ),
    EnvField(
        key='UPDATE_BRANCH_REF',
        label='Rama desplegada',
        kind='text',
        group='updates',
        width='full',
        placeholder='Ej.: refs/heads/main',
        help_text='Qué rama se considera la versión publicada.',
    ),
    EnvField(
        key='UPDATE_CHECK_TTL',
        label='Caché del chequeo (segundos)',
        kind='int',
        group='updates',
        placeholder='Ej.: 300',
        min_value=0,
        max_value=86400,
        default='300',
    ),
    EnvField(
        key='UPDATE_CHECK_TIMEOUT',
        label='Timeout del chequeo (segundos)',
        kind='int',
        group='updates',
        placeholder='Ej.: 15',
        min_value=1,
        max_value=120,
        default='15',
    ),
    EnvField(
        key='OBS_AUTOMATIC',
        label='Observaciones automáticas por FTP',
        kind='bool',
        group='ftp',
        width='full',
        default='True',
        help_text=(
            'Activado, el mapa toma las observaciones del FTP. Desactivado, usa '
            'solo las que se capturan desde la sección Observaciones del panel.'
        ),
    ),
    EnvField(
        key='FTP_OBS_HOST',
        label='Host FTP de observaciones',
        kind='text',
        group='ftp',
        width='full',
        placeholder='Ej.: ftp.cmp.cu',
        required=False,
    ),
    EnvField(
        key='FTP_OBS_PORT',
        label='Puerto FTP de observaciones',
        kind='int',
        group='ftp',
        width='full',
        placeholder='Ej.: 990',
        min_value=1,
        max_value=65535,
        default='990',
    ),
    EnvField(
        key='FTP_OBS_USER',
        label='Usuario FTP de observaciones',
        kind='text',
        group='ftp',
        width='full',
        placeholder='Ej.: observaciones',
        required=False,
    ),
    EnvField(
        key='FTP_OBS_PASS',
        label='Contraseña FTP de observaciones',
        kind='password',
        group='ftp',
        width='full',
        placeholder='Ej.: contraseña nueva',
        required=False,
        secret=True,
    ),
)

_SPEC_BY_KEY = {spec.key: spec for spec in EDITABLE_FIELDS}

# Títulos de las tarjetas que son solo lectura de punta a punta.
GROUP_TITLES = {
    'app': 'Aplicación',
    'domain': 'Dominio y orígenes',
    'database': 'Base de datos',
    'cache': 'Caché',
}

# Títulos de las secciones editables. `app` se llama distinto acá porque la
# tarjeta de solo lectura "Aplicación" ya existe y repetir el nombre confunde.
EDITABLE_GROUP_TITLES = {
    'app': 'Sitio',
    'notifications': 'Avisos operativos',
    'email': 'Correo',
    'ldap': 'LDAP',
    'updates': 'Actualizaciones del panel',
    'ftp': 'Observaciones (FTP)',
}

# Secretos que NO se administran desde el panel. Los de correo, LDAP y FTP sí
# son editables, así que no van acá: su estado se muestra junto al campo.
SECRET_LABELS = (
    ('SECRET_KEY', 'Clave secreta de Django'),
    ('ENCRYPTION_KEY', 'Clave de descifrado'),
    ('DB_PASS', 'Contraseña de la base de datos'),
)


def build_environment(settings_obj=settings, environ=None, file_values=None) -> dict:
    """Todo lo que la plantilla necesita, en una sola llamada."""
    environ = os.environ if environ is None else environ
    file_values = {} if file_values is None else file_values

    effective = {
        spec.key: effective_value(spec.key, settings_obj, environ) for spec in EDITABLE_FIELDS
    }
    pending = {
        spec.key: (
            spec.key in file_values
            and not same_value(spec, file_values[spec.key], effective[spec.key])
        )
        for spec in EDITABLE_FIELDS
    }

    groups = []
    for key, items in _readonly_groups(settings_obj, environ).items():
        if not items:
            continue
        title = GROUP_TITLES[key]
        groups.append({'key': key, 'title': title, 'items': items})

    return {
        'groups': groups,
        'secrets': secret_status(settings_obj, environ),
        'effective': effective,
        'pending': pending,
        'section_notes': _section_notes(settings_obj, environ),
    }


def effective_value(key: str, settings_obj=settings, environ=None) -> str:
    """Valor en ejecución de un parámetro editable, como texto."""
    spec = _SPEC_BY_KEY.get(key)
    if spec is not None and spec.source == 'environ':
        value = (os.environ if environ is None else environ).get(key)
    else:
        value = getattr(settings_obj, key, None)

    if isinstance(value, bool):
        return 'True' if value else 'False'
    if value is None or value == '':
        return spec.default if spec is not None else ''
    return str(value)


def same_value(spec: EnvField, file_value, effective) -> bool:
    """¿El valor del archivo y el efectivo significan lo mismo?"""
    if file_value is None:
        return False
    if spec.kind == 'bool':
        return as_bool(file_value) == as_bool(effective)

    left = str(file_value).strip()
    right = str(effective).strip()
    if left == right:
        return True
    if spec.kind == 'int':
        try:
            return int(left) == int(right)
        except TypeError, ValueError:
            return False
    return False


def as_bool(value) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def display_value(spec: EnvField, value) -> str:
    if spec.secret:
        return 'Configurada' if value not in (None, '') else 'Sin configurar'
    if spec.kind == 'bool':
        return 'Sí' if as_bool(value) else 'No'
    if value in (None, ''):
        return '—'
    return str(value)


def strip_url_credentials(url: str) -> str:
    """`redis://:***@host:6379/1` en lugar de la URL con la contraseña."""
    if not url:
        return '—'
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return '—'
    if not parts.username and not parts.password:
        return url
    host = parts.hostname or ''
    if parts.port:
        host = f'{host}:{parts.port}'
    return urllib.parse.urlunsplit(
        (parts.scheme, f'***@{host}', parts.path, parts.query, parts.fragment)
    )


def secret_status(settings_obj=settings, environ=None) -> list[dict]:
    """Estado de cada secreto no administrable, sin el valor."""
    environ = os.environ if environ is None else environ
    db = (getattr(settings_obj, 'DATABASES', None) or {}).get('default', {})
    configured = {
        'SECRET_KEY': bool(getattr(settings_obj, 'SECRET_KEY', '')),
        'ENCRYPTION_KEY': bool(environ.get('ENCRYPTION_KEY')),
        'DB_PASS': bool(db.get('PASSWORD')),
    }
    return [
        {'key': key, 'label': label, 'configured': configured[key]} for key, label in SECRET_LABELS
    ]


def _readonly_groups(settings_obj, environ) -> dict[str, list[dict]]:
    db = (getattr(settings_obj, 'DATABASES', None) or {}).get('default', {})
    options = db.get('OPTIONS') or {}
    cache = (getattr(settings_obj, 'CACHES', None) or {}).get('default', {})

    return {
        'app': [
            _item('Perfil de configuración', _profile(settings_obj)),
            _item('Modo de depuración', _yes_no(getattr(settings_obj, 'DEBUG', False))),
            _item('Idioma', getattr(settings_obj, 'LANGUAGE_CODE', '—')),
            _item('Zona horaria', getattr(settings_obj, 'TIME_ZONE', '—')),
            _item('Python', platform.python_version()),
            _item('Django', django.get_version()),
        ],
        'domain': [
            # No es un atributo de `settings`: los perfiles lo leen del entorno y
            # lo inyectan en las listas de hosts y orígenes de confianza. Se
            # muestra acá porque es la causa de que esas dos listas tengan
            # entradas extra.
            _item('Host externo', environ.get('EXTERNAL_HOSTNAME') or '—'),
            _item('Hosts permitidos', _join(getattr(settings_obj, 'ALLOWED_HOSTS', ()))),
            _item(
                'Orígenes de confianza para CSRF',
                _join(getattr(settings_obj, 'CSRF_TRUSTED_ORIGINS', ())),
            ),
            _item(
                'Orígenes permitidos para CORS',
                _join(getattr(settings_obj, 'CORS_ALLOWED_ORIGINS', ())),
            ),
        ],
        'database': [
            _item('Motor', _friendly_engine(db.get('ENGINE'))),
            _item('Nombre', db.get('NAME') or '—'),
            _item('Usuario', db.get('USER') or '—'),
            _item('Host', db.get('HOST') or '—'),
            _item('Puerto', db.get('PORT') or '—'),
            _item('Modo SSL', options.get('sslmode') or options.get('ssl_mode') or '—'),
            _item('Vida máxima de la conexión (s)', db.get('CONN_MAX_AGE') or '—'),
        ],
        'cache': [
            _item('Motor', _friendly_backend(cache.get('BACKEND'))),
            _item('Redis habilitado', _yes_no(getattr(settings_obj, 'USE_REDIS_CACHE', False))),
            _item('URL de Redis', strip_url_credentials(getattr(settings_obj, 'REDIS_URL', ''))),
        ],
    }


def _section_notes(settings_obj, environ) -> dict[str, list[dict]]:
    """Filas de solo lectura que acompañan a una sección editable.

    No alcanza con decir "Correo es editable": dentro de la misma tarjeta hay
    valores que no lo son y que explican cómo está configurada de verdad.

    No se muestran rutas absolutas del servidor (archivo de correos, archivos de
    estado del despliegue): el panel no necesita publicar el layout del sistema
    de archivos para que el operador sepa cómo está configurado.
    """
    return {
        'email': [
            _item('Motor de envío', _friendly_backend(getattr(settings_obj, 'EMAIL_BACKEND', ''))),
        ],
    }


def _item(label: str, value) -> dict:
    return {'label': label, 'value': value if value not in (None, '') else '—'}


def _yes_no(value) -> str:
    return 'Sí' if value else 'No'


def _join(value) -> str:
    if not value:
        return '—'
    if isinstance(value, str):
        return value
    return ', '.join(str(item) for item in value) or '—'


def _last_segment(value) -> str:
    if not value:
        return '—'
    return str(value).rsplit('.', 1)[-1]


# El panel no muestra identificadores del código (`smtp.EmailBackend`,
# `django.db.backends.postgresql`): quien opera el sitio reconoce el motor por su
# nombre, no por la ruta del módulo. Un valor desconocido cae al último segmento
# para que un backend propio siga siendo legible en vez de desaparecer.
_BACKEND_NAMES = {
    'smtp.EmailBackend': 'SMTP',
    'console.EmailBackend': 'Consola (no envía)',
    'filebased.EmailBackend': 'Archivos en disco',
    'locmem.EmailBackend': 'Memoria (solo pruebas)',
    'dummy.EmailBackend': 'Descartado (solo pruebas)',
    'RedisCache': 'Redis',
    'LocMemCache': 'Memoria local',
    'FileBasedCache': 'Archivos en disco',
    'DatabaseCache': 'Base de datos',
    'DummyCache': 'Descartado',
    'PyLibMCCache': 'Memcached',
}

_ENGINE_NAMES = {
    'postgresql': 'PostgreSQL',
    'mysql': 'MySQL',
    'sqlite3': 'SQLite',
    'oracle': 'Oracle',
}


def _friendly_backend(value) -> str:
    """Nombre legible del motor de correo o de caché.

    La clave se busca con dos segmentos (`smtp.EmailBackend`) y, si no aparece,
    con el último (`RedisCache`): los dos estilos existen en Django y en
    backends de terceros.
    """
    if not value:
        return '—'
    parts = str(value).rsplit('.', 2)
    for candidate in ('.'.join(parts[-2:]), parts[-1]):
        if candidate in _BACKEND_NAMES:
            return _BACKEND_NAMES[candidate]
    return parts[-1]


def _friendly_engine(value) -> str:
    segment = _last_segment(value)
    return _ENGINE_NAMES.get(segment, segment)


def _profile(settings_obj) -> str:
    return 'Producción' if getattr(settings_obj, 'IS_PRODUCTION', False) else 'Desarrollo / pruebas'
