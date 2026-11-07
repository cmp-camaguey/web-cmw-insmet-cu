"""Truth table de los perfiles de `config.settings` (odd/tasks/split-config-settings.md).

Cada perfil se carga con `importlib` bajo un entorno controlado —`dotenv`
neutralizado y `os.environ` limpio— así que la tabla se verifica en los estados
canónicos, no en el estado accidental que produce un `.env` local.

    PRODUCTION en el entorno        ->  production
    sin PRODUCTION, DEBUG == True   ->  dev
    sin PRODUCTION, DEBUG != True   ->  testing

Estos tests corren bajo `config.settings`, el perfil que el dispatcher eligió al
arrancar el test runner; por eso cargan los perfiles por su cuenta en vez de
mirar `django.conf.settings`.
"""

import importlib
import os
import warnings
from unittest import mock

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.settings import base
from config.settings._testing_keys import inject_testing_key_pair

DEBUG_TOOLBAR_APP = 'debug_toolbar'
DEBUG_TOOLBAR_MIDDLEWARE = 'debug_toolbar.middleware.DebugToolbarMiddleware'
STATIC_FILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
MANIFEST_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
POSTGRES_ENGINE = 'django.db.backends.postgresql'
# Literal, no el `base.console_email_backend`: el test tiene que fijar la cadena
# exacta que el perfil de producción rechaza, no seguir a la constante.
CONSOLE_EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# `filebased` y `locmem` comparten el modo de fallo de `console`: aceptan el
# mensaje y lo descartan. Literales, por la misma razón que arriba.
FILEBASED_EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
LOCMEM_EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
DJANGO_SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
CUSTOM_EMAIL_BACKEND = 'config.custom_email_backend.CustomSTARTTLSBackend'
# El backend de desarrollo del proyecto: acepta el mensaje y lo entrega sin
# verificar el certificado del servidor SMTP. Literal, por la misma razón que
# los de arriba: el test tiene que fijar la cadena exacta que se rechaza.
INSECURE_DEV_EMAIL_BACKEND = 'config.mail_backends.InsecureDevEmailBackend'

# El bloque de cookies seguras, setting por setting.
SECURE_COOKIE_VALUES = {
    'SECURE_SSL_REDIRECT': False,
    'SECURE_PROXY_SSL_HEADER': ('HTTP_X_FORWARDED_PROTO', 'https'),
    'SECURE_CONTENT_TYPE_NOSNIFF': True,
    'SESSION_COOKIE_SECURE': True,
    'CSRF_COOKIE_SECURE': True,
    'SECURE_HSTS_SECONDS': 31536000,
    'SECURE_HSTS_INCLUDE_SUBDOMAINS': True,
    'SECURE_HSTS_PRELOAD': True,
    'SECURE_REFERRER_POLICY': 'same-origin',
    'SECURE_CROSS_ORIGIN_OPENER_POLICY': 'same-origin',
}


def load_profile(module_path, env=None, *, with_secret=True, secret_pair=None):
    """Carga `module_path` con `env` como único entorno.

    Recarga `base` primero: es el que lee `DEBUG`/`IS_PRODUCTION` y el que
    ejecuta `load_dotenv()` (neutralizado acá para que `.env` no decida por
    nosotros). No toca el objeto `django.conf.settings`, que ya copió sus valores.

    Por defecto inyecta un par de claves válido, porque `base` falla cerrado sin
    uno y casi ningún test de este archivo está probando esa regla. Los que SÍ
    la prueban pasan `with_secret=False` (y `secret_pair=` para una rama parcial).
    """
    env = dict(env or {})
    if with_secret:
        env.update(secret_pair or secret_env())
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch('dotenv.load_dotenv'),
        warnings.catch_warnings(),
    ):
        importlib.reload(base)
        return importlib.reload(importlib.import_module(module_path))


def secret_env():
    """Par `SECRET_KEY`/`ENCRYPTION_KEY` válido, como el que genera el script.

    Sin esto `base` falla cerrado en el import y no hay forma de examinar el
    resto de las decisiones de un perfil.
    """
    key = Fernet.generate_key()
    return {
        'SECRET_KEY': Fernet(key).encrypt(b'secret-key-de-prueba').decode(),
        'ENCRYPTION_KEY': key.decode(),
    }


class SecureCookieAssertionsMixin:
    """El bloque de cookies seguras: presente en testing/producción, ausente en dev."""

    def assert_secure_cookies(self, module):
        for name, value in SECURE_COOKIE_VALUES.items():
            self.assertEqual(
                getattr(module, name), value, f'{module.__name__}.{name} no coincide con la tabla'
            )

    def assert_no_secure_cookies(self, module):
        # Ausentes, no "con el default de Django".
        for name in SECURE_COOKIE_VALUES:
            self.assertNotIn(name, vars(module), f'{module.__name__} no debe definir {name}')


class DebugToolbarAssertionsMixin:
    def assert_debug_toolbar(self, module):
        self.assertIs(module.DEBUG_TOOLBAR_ENABLED, True)
        self.assertIn(DEBUG_TOOLBAR_APP, module.INSTALLED_APPS)
        self.assertEqual(module.INTERNAL_IPS, ['127.0.0.1', '::1'])
        self.assertEqual(module.DEBUG_TOOLBAR_CONFIG, {'IS_RUNNING_TESTS': False})
        self.assertEqual(module.MIDDLEWARE[-1], DEBUG_TOOLBAR_MIDDLEWARE)

    def assert_no_debug_toolbar(self, module):
        self.assertIs(module.DEBUG_TOOLBAR_ENABLED, False)
        self.assertNotIn(DEBUG_TOOLBAR_APP, module.INSTALLED_APPS)
        self.assertNotIn(DEBUG_TOOLBAR_MIDDLEWARE, module.MIDDLEWARE)
        # El toolbar va con sus dos settings o con ninguno: no queda en un default.
        self.assertNotIn('DEBUG_TOOLBAR_CONFIG', vars(module))
        self.assertNotIn('INTERNAL_IPS', vars(module))


class DevProfileTests(SecureCookieAssertionsMixin, DebugToolbarAssertionsMixin, SimpleTestCase):
    """`DEBUG=True`: PostgreSQL, toolbar, sin cookies seguras, sin WhiteNoise."""

    def setUp(self):
        self.module = load_profile(
            'config.settings.dev',
            {
                'DEBUG': 'True',
                'DB_ENGINE': 'postgresql',
                'DB_NAME': 'meteo',
                'DB_USER': 'u',
                'DB_HOST': 'h',
                'DB_PASS': 'p',
            },
        )

    def test_flags(self):
        self.assertIs(self.module.DEBUG, True)
        self.assertIs(self.module.IS_PRODUCTION, False)

    def test_no_secure_cookies(self):
        self.assert_no_secure_cookies(self.module)

    def test_debug_toolbar_enabled(self):
        self.assert_debug_toolbar(self.module)

    def test_static_storage_is_plain(self):
        self.assertEqual(self.module.STORAGES['staticfiles']['BACKEND'], STATIC_FILES_STORAGE)

    def test_whitenoise_middleware_removed(self):
        self.assertNotIn(base.whitenoise_middleware, self.module.MIDDLEWARE)

    def test_database_comes_from_the_environment(self):
        """Dev usa el mismo motor que producción: no tiene base de datos propia."""
        config = self.module.DATABASES['default']
        self.assertEqual(config['ENGINE'], POSTGRES_ENGINE)
        self.assertEqual(config['NAME'], 'meteo')
        self.assertEqual(config['PORT'], '5432')
        self.assertEqual(config['CONN_MAX_AGE'], 600)

    def test_without_db_engine_does_not_fall_back(self):
        """Sin `DB_ENGINE` no hay respaldo silencioso a SQLite: el arranque falla."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile('config.settings.dev', {'DEBUG': 'True'})
        self.assertIn('DB_ENGINE', str(ctx.exception))

    def test_local_synop_simulations(self):
        self.assertIs(self.module.OBS_LOCAL_ONLY_DEFAULT, True)

    def test_console_email_by_default(self):
        """Sin `EMAIL_BACKEND` en el entorno, dev cae a consola (base defaultea SMTP)."""
        self.assertEqual(self.module.EMAIL_BACKEND, CONSOLE_EMAIL_BACKEND)

    def test_explicit_email_backend_wins(self):
        """Un valor explícito NO se pisa: desde dev se prueba envío real (MailHog, etc.)."""
        module = load_profile(
            'config.settings.dev',
            {
                'DEBUG': 'True',
                'EMAIL_BACKEND': CUSTOM_EMAIL_BACKEND,
                'DB_ENGINE': 'postgresql',
                'DB_NAME': 'meteo',
                'DB_USER': 'u',
                'DB_HOST': 'h',
                'DB_PASS': 'p',
            },
        )
        self.assertEqual(module.EMAIL_BACKEND, CUSTOM_EMAIL_BACKEND)


class TestingProfileTests(SecureCookieAssertionsMixin, DebugToolbarAssertionsMixin, SimpleTestCase):
    """Sin `DEBUG` y sin `PRODUCTION`: es CI, no "dev con la toolbar apagada"."""

    def setUp(self):
        self.module = load_profile(
            'config.settings.testing',
            {
                'DEBUG': 'False',
                'DB_ENGINE': 'postgresql',
                'DB_NAME': 'meteo',
                'DB_USER': 'u',
                'DB_HOST': 'h',
                'DB_PASS': 'p',
            },
        )

    def test_flags(self):
        self.assertIs(self.module.DEBUG, False)
        self.assertIs(self.module.IS_PRODUCTION, False)

    def test_secure_cookies(self):
        self.assert_secure_cookies(self.module)

    def test_no_debug_toolbar(self):
        self.assert_no_debug_toolbar(self.module)

    def test_static_storage_is_plain(self):
        self.assertEqual(self.module.STORAGES['staticfiles']['BACKEND'], STATIC_FILES_STORAGE)

    def test_whitenoise_middleware_removed(self):
        self.assertNotIn(base.whitenoise_middleware, self.module.MIDDLEWARE)

    def test_database_comes_from_the_environment(self):
        config = self.module.DATABASES['default']
        self.assertEqual(config['ENGINE'], POSTGRES_ENGINE)
        self.assertEqual(config['NAME'], 'meteo')

    def test_without_db_engine_does_not_fall_back(self):
        """Testing tampoco cae a SQLite: el perfil de CI exige el motor real."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile('config.settings.testing', {'DEBUG': 'False'})
        self.assertIn('DB_ENGINE', str(ctx.exception))

    def test_secret_key_comes_from_the_injected_pair(self):
        """El perfil `testing` arranca sin `.env` porque su par sale del par inyectado."""
        self.assertTrue(self.module.SECRET_KEY)

    def test_ftp_simulations_off(self):
        self.assertIs(self.module.OBS_LOCAL_ONLY_DEFAULT, False)


class SqliteIsNotSupportedTests(SimpleTestCase):
    """SQLite se retiró del proyecto: tiene que fallar, no degradar en silencio.

    El riesgo no es "elegir mal el motor", es que un `.env` viejo con
    `DB_ENGINE=sqlite3` siga arrancando y la suite verde mida un motor que no
    existe en producción.
    """

    PROFILES = (
        ('config.settings.dev', {'DEBUG': 'True'}),
        ('config.settings.testing', {'DEBUG': 'False'}),
        (
            'config.settings.production',
            {'PRODUCTION': '1', 'SITE_URL': 'https://cmw.insmet.cu/'},
        ),
    )

    def test_sqlite_engine_is_rejected_in_every_profile(self):
        for module_path, env in self.PROFILES:
            for engine in ('sqlite', 'sqlite3'):
                with self.subTest(profile=module_path, engine=engine):
                    with self.assertRaises(ImproperlyConfigured) as ctx:
                        load_profile(module_path, {**env, 'DB_ENGINE': engine})
                    message = str(ctx.exception)
                    self.assertIn('SQLite', message)
                    self.assertIn('postgresql', message)
                    self.assertIn('generate_env.py', message)


class ProductionProfileTests(
    SecureCookieAssertionsMixin, DebugToolbarAssertionsMixin, SimpleTestCase
):
    """`PRODUCTION` en el entorno: fail-closed y WhiteNoise, sin depender de `DEBUG`."""

    DB_ENV = {
        'PRODUCTION': '1',
        'DB_ENGINE': 'postgresql',
        'DB_NAME': 'meteo',
        'DB_USER': 'u',
        'DB_HOST': 'h',
        'DB_PASS': 'p',
        'SITE_URL': 'https://cmw.insmet.cu/',
        **secret_env(),
    }

    def setUp(self):
        self.module = load_profile('config.settings.production', self.DB_ENV)

    def test_is_production(self):
        self.assertIs(self.module.IS_PRODUCTION, True)

    def test_secure_cookies(self):
        self.assert_secure_cookies(self.module)

    def test_no_debug_toolbar(self):
        self.assert_no_debug_toolbar(self.module)

    def test_manifest_static_storage(self):
        self.assertEqual(self.module.STORAGES['staticfiles']['BACKEND'], MANIFEST_STORAGE)

    def test_whitenoise_middleware_present_and_early(self):
        middleware = self.module.MIDDLEWARE
        self.assertIn(base.whitenoise_middleware, middleware)
        # El bloque de producción inserta WhiteNoise en MIDDLEWARE[1] si falta; la
        # lista base ya lo trae, así que hoy la inserción no ocurre. Se fija la
        # posición efectiva, no la intención del `insert`.
        self.assertEqual(middleware.index(base.whitenoise_middleware), 2)
        self.assertEqual(middleware[0], 'django.middleware.security.SecurityMiddleware')

    def test_database_built_from_db_engine(self):
        database = self.module.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(database['NAME'], 'meteo')
        self.assertEqual(database['PORT'], '5432')
        self.assertEqual(database['CONN_MAX_AGE'], 600)

    def test_fail_closed_without_db_engine(self):
        """Con claves pero sin DB_ENGINE, producción no arranca (nunca cae a sqlite)."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile('config.settings.production', {'PRODUCTION': '1', **secret_env()})
        self.assertIn('DB_ENGINE', str(ctx.exception))

    def test_missing_secret_key_raises_before_database(self):
        """Sin claves y sin DB_ENGINE, el primer error es el de SECRET_KEY.

        `with_secret=False` porque el default de `load_profile` inyecta un par
        válido, y con par este perfil llega hasta el error de base de datos: el
        orden que se quiere verificar es justamente que la clave se queja antes.
        """
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile('config.settings.production', {'PRODUCTION': '1'}, with_secret=False)
        message = str(ctx.exception)
        self.assertIn('SECRET_KEY', message)
        self.assertNotIn('DB_ENGINE', message)

    def test_debug_is_pinned_false(self):
        """`DEBUG=True` en el entorno no se hereda: producción lo pinea en False.

        Sin el pin, un `DEBUG=True` colado en el `.env` de producción publicaría
        tracebacks con paths del servidor.
        """
        module = load_profile('config.settings.production', {**self.DB_ENV, 'DEBUG': 'True'})
        self.assertIs(module.DEBUG, False)

    def test_console_email_backend_is_rejected(self):
        """Rama NEGATIVA del assert: un backend que descarta en producción es un
        `.env` regenerable.

        El pie real no es "dev usa consola", es "producción descarta el correo sin
        un solo error": el mensaje se acepta y se pierde. `filebased` y `locmem`
        fallan igual, aunque el README los recomiende para desarrollo.
        """
        for backend in (CONSOLE_EMAIL_BACKEND, FILEBASED_EMAIL_BACKEND, LOCMEM_EMAIL_BACKEND):
            with self.subTest(backend=backend):
                with self.assertRaises(ImproperlyConfigured) as ctx:
                    load_profile(
                        'config.settings.production',
                        {**self.DB_ENV, 'EMAIL_BACKEND': backend},
                    )
                message = str(ctx.exception)
                self.assertIn(backend, message)
                self.assertIn('generate_env.py --production', message)

    def test_real_email_backends_are_accepted(self):
        """Rama POSITIVA del assert: todo backend menos consola pasa."""
        for backend in (DJANGO_SMTP_BACKEND, CUSTOM_EMAIL_BACKEND):
            with self.subTest(backend=backend):
                module = load_profile(
                    'config.settings.production', {**self.DB_ENV, 'EMAIL_BACKEND': backend}
                )
                self.assertEqual(module.EMAIL_BACKEND, backend)

    def test_insecure_dev_email_backend_is_rejected(self):
        """El backend de desarrollo del proyecto no verifica el certificado del
        servidor SMTP: en producción el correo sale sin cifrar contra un
        intermediario, y nadie se entera porque el envío "funciona"."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile(
                'config.settings.production',
                {**self.DB_ENV, 'EMAIL_BACKEND': INSECURE_DEV_EMAIL_BACKEND},
            )
        message = str(ctx.exception)
        self.assertIn(INSECURE_DEV_EMAIL_BACKEND, message)
        self.assertIn('certificado', message)
        self.assertIn('generate_env.py --production', message)

    def test_fail_closed_without_site_url(self):
        """Sin `SITE_URL`, producción no arranca.

        Los enlaces de los correos (factura, aviso, reporte del tiempo y
        restablecimiento de contraseña) necesitan la URL pública del sitio. Sin
        ella no se puede inventar una: un enlace a un host interno no lo puede
        abrir el destinatario, y el fallo se descubre cuando el cliente recibe
        el correo.
        """
        env = {key: value for key, value in self.DB_ENV.items() if key != 'SITE_URL'}
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile('config.settings.production', env)
        message = str(ctx.exception)
        self.assertIn('SITE_URL', message)
        self.assertIn('generate_env.py --production', message)

    def test_site_url_comes_from_the_environment(self):
        """Producción usa la URL pública que declara el entorno, tal cual."""
        module = load_profile(
            'config.settings.production', {**self.DB_ENV, 'SITE_URL': 'https://otro.cu/'}
        )
        self.assertEqual(module.SITE_URL, 'https://otro.cu/')

    def test_ftp_simulations_off(self):
        self.assertIs(self.module.OBS_LOCAL_ONLY_DEFAULT, False)


class EmailTlsTests(SimpleTestCase):
    """El puerto 465 es TLS implícito: `EMAIL_USE_SSL` tiene que llegar a Django.

    La historia tuvo dos mitades. Primero ningún archivo de settings leía la
    variable, así que `EMAIL_USE_SSL` quedaba en su default `False` y Django
    abría una conexión en claro contra un puerto que espera el handshake TLS en
    el primer byte: el error no nombraba el puerto ni la variable. Después se
    agregó la lectura, pero `deploy/install.sh` seguía escribiendo `si`, que
    contra `== 'True'` también es False. Las dos mitades están cubiertas:
    `test_install_script_booleans` fija la del instalador.
    """

    DEV_DB = {
        'DEBUG': 'True',
        'DB_ENGINE': 'postgresql',
        'DB_NAME': 'meteo',
        'DB_USER': 'u',
        'DB_HOST': 'h',
        'DB_PASS': 'p',
    }

    def _production(self, **env):
        return {
            'PRODUCTION': '1',
            'DB_ENGINE': 'postgresql',
            'DB_NAME': 'meteo',
            'DB_USER': 'u',
            'DB_HOST': 'h',
            'DB_PASS': 'p',
            'SITE_URL': 'https://cmw.insmet.cu/',
            **secret_env(),
            **env,
        }

    def test_ssl_is_read_from_the_environment(self):
        module = load_profile(
            'config.settings.dev',
            {**self.DEV_DB, 'EMAIL_USE_TLS': 'False', 'EMAIL_USE_SSL': 'True'},
        )
        self.assertIs(module.EMAIL_USE_TLS, False)
        self.assertIs(module.EMAIL_USE_SSL, True)

    def test_ssl_defaults_to_false(self):
        """Sin la variable, STARTTLS sigue siendo el default: no se cambia el 587."""
        module = load_profile('config.settings.dev', self.DEV_DB)
        self.assertIs(module.EMAIL_USE_SSL, False)
        self.assertIs(module.EMAIL_USE_TLS, True)

    def test_tls_and_ssl_are_mutually_exclusive(self):
        """Django lanzaría `ValueError` al enviar; el arranque lo dice antes."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile(
                'config.settings.dev',
                {**self.DEV_DB, 'EMAIL_USE_TLS': 'True', 'EMAIL_USE_SSL': 'True'},
            )
        message = str(ctx.exception)
        self.assertIn('EMAIL_USE_TLS', message)
        self.assertIn('EMAIL_USE_SSL', message)

    def test_port_465_without_ssl_is_rejected_in_production(self):
        """465 es SMTPS por definición: con STARTTLS no se llega ni al banner."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            load_profile(
                'config.settings.production',
                self._production(EMAIL_PORT='465', EMAIL_USE_TLS='True'),
            )
        message = str(ctx.exception)
        self.assertIn('465', message)
        self.assertIn('EMAIL_USE_SSL', message)

    def test_port_465_with_ssl_loads(self):
        """El 465 con TLS implícito es la configuración correcta, y no se rechaza."""
        module = load_profile(
            'config.settings.production',
            self._production(EMAIL_PORT='465', EMAIL_USE_TLS='False', EMAIL_USE_SSL='True'),
        )
        self.assertIs(module.EMAIL_USE_SSL, True)
        self.assertIs(module.EMAIL_USE_TLS, False)

    def test_port_587_with_starttls_loads(self):
        """El 587 no se toca: STARTTLS es su modo, y sigue siendo el default."""
        module = load_profile('config.settings.production', self._production(EMAIL_PORT='587'))
        self.assertIs(module.EMAIL_USE_TLS, True)
        self.assertIs(module.EMAIL_USE_SSL, False)


class SiteUrlNormalizationTests(SimpleTestCase):
    """`SITE_URL` se expone con barra final: el host y la ruta se unen con una
    sola barra, nunca con dos.

    La convención es del módulo, no del gusto: los enlaces se arman con
    `SITE_URL + reverse(...).lstrip('/')`, y `lstrip` quita la barra de la ruta
    para que la del host sea la única que queda.
    """

    def _site_url(self, value=None):
        env = {'DEBUG': 'True'}
        if value is not None:
            env['SITE_URL'] = value
        return load_profile('config.settings.base', env).SITE_URL

    def test_default_de_desarrollo(self):
        self.assertEqual(self._site_url(), 'http://localhost:8000/')

    def test_con_barra_final_no_se_toca(self):
        self.assertEqual(self._site_url('https://cmw.insmet.cu/'), 'https://cmw.insmet.cu/')

    def test_sin_barra_final_se_agrega(self):
        self.assertEqual(self._site_url('https://cmw.insmet.cu'), 'https://cmw.insmet.cu/')

    def test_con_ruta_base(self):
        self.assertEqual(
            self._site_url('https://ejemplo.cu/meteorologia'),
            'https://ejemplo.cu/meteorologia/',
        )

    def test_los_espacios_al_rededor_no_quedan(self):
        self.assertEqual(self._site_url('  https://cmw.insmet.cu  '), 'https://cmw.insmet.cu/')


class SecretKeyFailClosedTests(SimpleTestCase):
    """Sin un par de claves que descifre, la aplicación NO arranca. En ningún perfil.

    La regla tiene un solo dueño, `base.load_secret_key()`, y es implementable
    porque el generador (`scripts/generate_env.py`) no importa Django: corre en el
    estado exacto en que la aplicación todavía no puede arrancar.
    Ver odd/tasks/production-deploy-systemd.md.
    """

    def load_base(self, env):
        """Carga `base` SIN inyectarle par: el par, cuando hace falta, va en `env`.

        El default de `load_profile` es inyectarlo, y para una clase cuyo objeto
        es probar la ausencia del par, ese default es exactamente lo contrario de
        lo que se quiere.
        """
        return load_profile('config.settings.base', env, with_secret=False)

    def assert_refuses(self, env, expected):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.load_base(env)
        self.assertIn(expected, str(ctx.exception))

    def test_no_env_at_all_refuses(self):
        """El caso del servidor recién estrenado: no hay `.env`."""
        self.assert_refuses({'DEBUG': 'True'}, 'Falta el archivo .env')

    def test_message_names_the_command_to_run(self):
        """Un mensaje que no dice QUÉ ejecutar convierte el arranque en una adivinanza."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.load_base({'DEBUG': 'True'})
        self.assertIn('scripts/generate_env.py', str(ctx.exception))

    def test_secret_without_encryption_key_refuses(self):
        """Producción: la clave de descifrado llega por systemd, no por el `.env`.

        El mensaje tiene que señalar el archivo externo, porque ese es el paso que
        falta y no se deduce del resto.
        """
        # El valor es ruido a propósito: lo que se prueba es la AUSENCIA de
        # ENCRYPTION_KEY. El pragma evita que `detect-secrets` lo tome por un
        # secreto, que es la lectura que tendría un escáner mirando esta línea.
        self.assert_refuses(
            {'DEBUG': 'True', 'SECRET_KEY': 'lo-que-sea'},  # pragma: allowlist secret
            'EnvironmentFile=-/etc/webcmp/encryption.env',
        )

    def test_encryption_key_without_secret_refuses(self):
        self.assert_refuses(
            {'DEBUG': 'True', 'ENCRYPTION_KEY': Fernet.generate_key().decode()},
            'Falta SECRET_KEY',
        )

    def test_mismatched_pair_refuses(self):
        """Un `.env` copiado de otro servidor es el caso real, no uno teórico."""
        other_key = Fernet.generate_key()
        self.assert_refuses(
            {
                'DEBUG': 'True',
                'SECRET_KEY': Fernet(other_key).encrypt(b'de-otra-instalacion').decode(),
                'ENCRYPTION_KEY': Fernet.generate_key().decode(),
            },
            'no descifra',
        )

    def test_valid_pair_loads_and_decrypts(self):
        """Con par válido, `SECRET_KEY` es exactamente el texto que se cifró."""
        module = self.load_base({'DEBUG': 'True', **secret_env()})
        self.assertEqual(module.SECRET_KEY, 'secret-key-de-prueba')

    def test_loading_base_does_not_mutate_the_environment(self):
        """`base` no inyecta nada: si el par falta, se niega; no se auto-parchea.

        Un perfil que se rellena a sí mismo es un perfil que vuelve a fallar en
        el proceso siguiente, y esta vez sin explanation.
        """
        with (
            mock.patch.dict(os.environ, {'DEBUG': 'True'}, clear=True),
            mock.patch('dotenv.load_dotenv'),
        ):
            with self.assertRaises(ImproperlyConfigured):
                importlib.reload(base)
            self.assertNotIn('SECRET_KEY', os.environ)
            self.assertNotIn('ENCRYPTION_KEY', os.environ)

    def test_testing_profile_is_the_bootstrap_escape_hatch(self):
        """El perfil `testing` inyecta su par antes de importar `base`.

        Es lo que permite que `base` sea fail-closed sin que la suite tenga que
        hacer bootstrap: CI y un clon limpio no tienen `.env`.
        """
        with (
            mock.patch.dict(
                os.environ,
                {
                    'DEBUG': 'False',
                    'DB_ENGINE': 'postgresql',
                    'DB_NAME': 'meteo',
                    'DB_USER': 'u',
                    'DB_HOST': 'h',
                    'DB_PASS': 'p',
                },
                clear=True,
            ),
            mock.patch('dotenv.load_dotenv'),
        ):
            inject_testing_key_pair()
            module = importlib.reload(importlib.import_module('config.settings.testing'))
        self.assertTrue(module.SECRET_KEY)
        self.assertIs(module.DEBUG, False)

    def test_testing_pair_is_deterministic_across_calls(self):
        """Un texto plano FIJO, no uno nuevo por corrida: con clave nueva, la
        firma de sesión de un test contra otro sería indeterminista.

        Lo que tiene que ser estable es el PLANO, no el cifrado: Fernet mete un
        IV aleatorio y una marca de tiempo, así que dos llamadas dan dos
        ciphertexts distintos. Lo que importa es que los dos descifren al mismo
        texto, que es lo que realmente firman las cookies.
        """
        plains = []
        for _ in range(2):
            with mock.patch.dict(os.environ, {}, clear=True):
                inject_testing_key_pair()
                with (
                    mock.patch.dict(
                        os.environ,
                        {
                            'SECRET_KEY': os.environ['SECRET_KEY'],
                            'ENCRYPTION_KEY': os.environ['ENCRYPTION_KEY'],
                        },
                        clear=True,
                    ),
                    mock.patch('dotenv.load_dotenv'),
                ):
                    plains.append(importlib.reload(base).SECRET_KEY)
        self.assertEqual(plains[0], plains[1])

    def test_testing_pair_satisfies_the_deploy_secret_key_check(self):
        """El job `deploy-check` de CI corre `check --deploy` sobre ESTE perfil, así
        que el par inyectado tiene que pasar `security.W009` (>=50 caracteres y
        >=5 distintos) o el gate empieza a avisar por el material de prueba."""
        from django.core.checks.security.base import _check_secret_key

        with mock.patch.dict(os.environ, {}, clear=True):
            inject_testing_key_pair()
            module = importlib.reload(base)
        self.assertTrue(
            _check_secret_key(module.SECRET_KEY),
            'el par de testing no pasa el criterio de security.W009',
        )

    def test_testing_pair_does_not_override_a_real_one(self):
        """Si el operador exporta material real para correr la suite, no se pisa."""
        real = secret_env()
        with mock.patch.dict(os.environ, real, clear=True):
            inject_testing_key_pair()
            self.assertEqual(os.environ['ENCRYPTION_KEY'], real['ENCRYPTION_KEY'])
            self.assertEqual(os.environ['SECRET_KEY'], real['SECRET_KEY'])


class DispatcherTests(SimpleTestCase):
    """La restricción dura: el perfil lo eligen estas DOS variables de entorno."""

    def test_flags_are_read_from_the_environment(self):
        from config import settings

        self.assertIs(settings.IS_PRODUCTION, 'PRODUCTION' in os.environ)
        self.assertIs(settings.DEBUG, os.getenv('DEBUG', 'False') == 'True')

    def test_production_profile_loads_without_the_dispatcher(self):
        """`DJANGO_SETTINGS_MODULE=config.settings.production` no pasa por el dispatcher."""
        env = {'PRODUCTION': '1', **secret_env(), **ProductionProfileTests.DB_ENV}
        module = load_profile('config.settings.production', env)
        self.assertEqual(module.__name__, 'config.settings.production')
        self.assertIs(module.IS_PRODUCTION, True)
