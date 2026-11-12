"""Los enlaces que viajan en un correo salen de `SITE_URL`, no del host del `request`.

El host del `request` es el host por el que el operador entró: la IP del
servidor en la red interna (`10.0.0.5:8000`), `localhost` o el nombre de la
máquina de desarrollo. El destinatario del correo está fuera de esa red, así que
un enlace armado con ese host es un enlace que no abre. `SITE_URL` es la URL
pública del sitio y es lo único que puede viajar en un correo.
"""

import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.core.tests.base import add_confirmed_subscriber, get_default_list
from apps.core.utils import mail_send
from apps.meteo.models import Warning

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATE_ENV_SCRIPT = REPO_ROOT / 'scripts' / 'generate_env.py'

PUBLIC_SITE_URL = 'https://cmw.insmet.cu/'
INTERNAL_HOST = '10.0.0.5:8000'
# El host interno tiene que estar permitido: si no, Django lo rechaza antes de
# que el código llegue a armar el enlace, y el test probaría el `ALLOWED_HOSTS`
# en vez del contrato de `SITE_URL`.
ALLOWED_HOSTS = ['testserver', '10.0.0.5']


class MailSendUsesSiteUrlTests(TestCase):
    """`mail_send` (avisos y reportes del tiempo) arma sus enlaces con `SITE_URL`."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            'siteurladmin',
            'siteurladmin@example.com',
            'pass',
            first_name='Ana',
            last_name='Norte',
        )
        cls.recipient_list = get_default_list()
        add_confirmed_subscriber(cls.recipient_list, 'destinatario@example.com')
        cls.warning = Warning.objects.create(
            warning_type='early',
            title='Aviso de prueba',
            user=cls.admin,
            summary='Resumen del aviso',
            valid_until=timezone.now() + timedelta(days=1),
        )

    def _request_desde_la_red_interna(self):
        return RequestFactory().post('/aviso/alerta_temprana/', HTTP_HOST=INTERNAL_HOST)

    @override_settings(SITE_URL=PUBLIC_SITE_URL, ALLOWED_HOSTS=ALLOWED_HOSTS)
    def test_el_enlace_usa_el_host_publico_y_no_el_del_request(self):
        request = self._request_desde_la_red_interna()
        with mock.patch('apps.core.tasks.send_email_task') as tarea:
            mail_send(request, self.warning, 'Asunto de prueba', 'home:warnings_early')

        self.assertTrue(tarea.called, 'mail_send tiene que encolar el envío')
        cuerpo = tarea.call_args.args[1]
        self.assertIn(f'{PUBLIC_SITE_URL}aviso/alerta_temprana/', cuerpo)
        self.assertNotIn(INTERNAL_HOST, cuerpo)

    @override_settings(SITE_URL=PUBLIC_SITE_URL, ALLOWED_HOSTS=ALLOWED_HOSTS)
    def test_el_indice_del_correo_tambien_sale_de_site_url(self):
        request = self._request_desde_la_red_interna()
        with mock.patch('apps.core.tasks.send_email_task') as tarea:
            mail_send(request, self.warning, 'Asunto de prueba', 'home:warnings_early')

        cuerpo = tarea.call_args.args[1]
        self.assertIn(f'href="{PUBLIC_SITE_URL}"', cuerpo)
        self.assertNotIn(f'href="http://{INTERNAL_HOST}/"', cuerpo)


class GenerateEnvWritesSiteUrlTests(unittest.TestCase):
    """El `.env` que genera el script tiene que traer `SITE_URL`.

    Sin la variable, el `.env` de producción arranca sin la URL pública y los
    correos salen con el enlace roto: el fallo aparece cuando el cliente
    recibe el correo, que es el peor momento para descubrirlo.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env_file = self.tmp / '.env'
        self.key_file = self.tmp / 'encryption.env'
        self.addCleanup(self._tmp.cleanup)

    def _run(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(GENERATE_ENV_SCRIPT),
                '--env-file',
                str(self.env_file),
                '--encryption-key-file',
                str(self.key_file),
                *extra,
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )

    def _read_env(self):
        data = {}
        for line in self.env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            data[key.strip()] = value.strip()
        return data

    def test_desarrollo_escribe_site_url(self):
        result = self._run('--development')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._read_env()['SITE_URL'], 'http://localhost:8000/')

    def test_produccion_escribe_site_url(self):
        result = self._run('--production')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._read_env()['SITE_URL'], 'https://www.meteocamaguey.cu/')

    def test_produccion_no_duplica_el_www(self):
        """El canonico ya lleva `www.`: agregarlo otra vez da `www.www.`.

        Ese nombre no existe en DNS y hace fallar la emision del certificado,
        asi que el alias solo se agrega cuando el canonico no lo trae.
        """
        result = self._run('--production')
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self._read_env()
        self.assertEqual(env['EXTERNAL_HOSTNAME'], 'www.meteocamaguey.cu')
        self.assertEqual(env['ALLOWED_HOSTS'], 'www.meteocamaguey.cu')
        self.assertEqual(env['CSRF_TRUSTED_ORIGINS'], 'https://www.meteocamaguey.cu')
        self.assertEqual(env['CORS_ALLOWED_ORIGINS'], 'https://www.meteocamaguey.cu')
