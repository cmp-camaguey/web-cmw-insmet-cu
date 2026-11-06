"""Tests de configuración de producción (feature 089)."""

import os
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings

from config.settings import get_database_config


class ProxyHeaderTests(TestCase):
    """Django debe resolver is_secure() tras el proxy vía X-Forwarded-Proto."""

    def test_is_secure_with_x_forwarded_proto(self):
        factory = RequestFactory()
        request = factory.get('/', HTTP_X_FORWARDED_PROTO='https')
        with override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')):
            self.assertTrue(request.is_secure())

    def test_is_secure_without_header(self):
        factory = RequestFactory()
        request = factory.get('/')
        with override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')):
            self.assertFalse(request.is_secure())


class DatabaseConfigTests(TestCase):
    """El motor sale del entorno en TODOS los perfiles, sin respaldo silencioso."""

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_fail_closed_without_db_engine(self):
        """Sin `DB_ENGINE` no hay base de datos: ni producción ni desarrollo adivinan."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            get_database_config()
        self.assertIn('DB_ENGINE', str(ctx.exception))

    @mock.patch.dict(
        os.environ,
        {
            'DB_ENGINE': 'postgresql',
            'DB_NAME': 'x',
            'DB_USER': 'u',
            'DB_HOST': 'h',
            'DB_PASS': 'p',
        },
        clear=True,
    )
    def test_prod_with_complete_vars(self):
        config = get_database_config()['default']
        self.assertEqual(config['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(config['PORT'], '5432')
        self.assertEqual(config['CONN_MAX_AGE'], 600)
        self.assertEqual(config['OPTIONS']['sslmode'], 'prefer')

    @mock.patch.dict(os.environ, {'DB_ENGINE': 'sqlite3'}, clear=True)
    def test_sqlite3_is_rejected(self):
        """Un `.env` viejo no puede seguir midiendo un motor que producción no tiene.

        El mensaje tiene que decir qué hacer: el arreglo real es apuntar el `.env`
        a PostgreSQL, no descubrirlo leyendo el código de `get_database_config`.
        """
        with self.assertRaises(ImproperlyConfigured) as ctx:
            get_database_config()
        message = str(ctx.exception)
        self.assertIn('SQLite', message)
        self.assertIn('postgresql', message)
        self.assertIn('generate_env.py', message)

    @mock.patch.dict(os.environ, {'DB_ENGINE': 'sqlite'}, clear=True)
    def test_sqlite_alias_is_rejected(self):
        """`sqlite` y `sqlite3` son el mismo error, no dos caminos distintos."""
        with self.assertRaises(ImproperlyConfigured):
            get_database_config()

    @mock.patch.dict(os.environ, {'DB_ENGINE': 'postgresql', 'DB_NAME': 'x'}, clear=True)
    def test_server_engine_still_requires_every_credential(self):
        """Quitar SQLite no relaja la exigencia de credenciales de un motor de red."""
        with self.assertRaises(ImproperlyConfigured) as ctx:
            get_database_config()
        message = str(ctx.exception)
        self.assertIn('DB_USER', message)
        self.assertIn('postgresql', message)
