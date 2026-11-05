"""Boton de actualizacion del panel (change 025-update-web-deploy).

Lo que se prueba aca es el contrato que el ejecutor privilegiado (`deploy`) y
el proceso web (`webcmp`) comparten a traves de archivos:

- el panel NUNCA escribe el estado, solo lo lee;
- la peticion se crea con ``O_EXCL``, asi que dos clics no encolan dos deploys;
- sin commit desplegado no se afirma que hay actualizacion;
- el archivo de estado lo escribe otro usuario, asi que su contenido no se
  reenvia tal cual al navegador.
"""

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.dashboard.views.dashboard import update as update_module

SHA_DEPLOYED = 'a' * 40
SHA_LATEST = 'b' * 40


class UpdateTestCase(TestCase):
    """Aisla los tres archivos en un directorio temporal por test."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name

        self.deployed_file = os.path.join(self.tmpdir, 'deployed')
        self.status_file = os.path.join(self.tmpdir, 'deploy-status.json')
        self.request_file = os.path.join(self.tmpdir, 'deploy-request')

        override = override_settings(
            UPDATE_DEPLOYED_SHA_FILE=self.deployed_file,
            UPDATE_STATUS_FILE=self.status_file,
            UPDATE_REQUEST_FILE=self.request_file,
        )
        override.enable()
        self.addCleanup(override.disable)

        # Nombre y apellido obligatorios: `CheckUserProfileMiddleware` redirige a
        # completar el perfil cuando faltan, y un 302 en un test de permisos se ve
        # como otro problema.
        self.admin = User.objects.create_superuser(
            'admin', 'admin@example.com', 'x', first_name='Admin', last_name='Prueba'
        )
        # `is_staff` sin `is_superuser`: el dashboard es accesible para staff, el
        # boton de actualizacion no.
        self.plain = User.objects.create_user(
            'plain',
            'plain@example.com',
            'x',
            is_staff=True,
            first_name='Staff',
            last_name='Prueba',
        )

        self.status_url = reverse('dashboard:update_status')
        self.request_url = reverse('dashboard:update_request')

    def write(self, path, content):
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(content)

    def set_deployed(self, sha=SHA_DEPLOYED):
        self.write(self.deployed_file, sha + '\n')

    def set_run_status(self, **payload):
        self.write(self.status_file, json.dumps(payload))

    def set_remote(self, sha=SHA_LATEST, error=None):
        patcher = mock.patch.object(
            update_module, '_ls_remote', return_value={'sha': sha, 'error': error}
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class UpdateStatusViewTest(UpdateTestCase):
    def test_anonimo_recibe_403_y_no_un_redirect(self):
        # Las vistas son JSON: un redirect al login en un `fetch` se ve como un
        # 200 con HTML. `raise_exception` convierte la negativa en 403.
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 403)

    def test_no_superusuario_recibe_403(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.status_url).status_code, 403)

    def test_al_dia(self):
        self.set_deployed()
        self.set_remote(sha=SHA_DEPLOYED)
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'up_to_date')
        self.assertEqual(payload['deployed_short'], SHA_DEPLOYED[:7])
        self.assertIsNone(payload['check_error'])

    def test_hay_actualizacion(self):
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'available')
        self.assertEqual(payload['latest'], SHA_LATEST)
        self.assertEqual(payload['deployed'], SHA_DEPLOYED)

    def test_sin_commit_desplegado_no_afirma_actualizacion(self):
        self.set_remote()
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'unknown')
        self.assertIsNone(payload['deployed'])
        # El motivo tiene que distinguir "falta el archivo" de "el remoto no
        # responde": son dos diagnosticos opuestos y antes decian lo mismo.
        self.assertIn('no registra', payload['reason'])

    def test_remoto_inalcanzable(self):
        self.set_deployed()
        self.set_remote(sha=None, error='No se pudo consultar el repositorio.')
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'unknown')
        self.assertEqual(payload['check_error'], 'No se pudo consultar el repositorio.')
        self.assertEqual(payload['reason'], 'No se pudo consultar el repositorio.')

    def test_archivo_desplegado_manipulado_se_ignora(self):
        # El archivo lo escribe deploy.sh, pero igual se valida: un contenido
        # raro no debe llegar al navegador ni cambiar el estado.
        self.write(self.deployed_file, '../../etc/passwd')
        self.set_remote()
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'unknown')
        self.assertIsNone(payload['deployed'])

    def test_despliegue_en_curso(self):
        self.set_deployed()
        self.set_remote()
        self.set_run_status(status='running', sha=SHA_LATEST)
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'running')
        self.assertEqual(payload['last_run']['status'], 'running')

    def test_estado_ilegible_se_ignora(self):
        self.set_deployed()
        self.set_remote(sha=SHA_DEPLOYED)
        self.write(self.status_file, '{no es json')
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertIsNone(payload['last_run'])
        # Un estado roto no convierte el panel en una mentira: sigue informando.
        self.assertEqual(payload['state'], 'up_to_date')

    def test_estado_con_claves_desconocidas_no_se_reenvia(self):
        self.set_deployed()
        self.set_remote()
        self.set_run_status(
            status='failed',
            sha='../../etc/shadow',
            finished_at='2026-11-03T10:00:00+00:00',
            stderr='/srv/webcmp/secreto',
        )
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()
        run = payload['last_run']

        self.assertEqual(run['status'], 'failed')
        self.assertIsNone(run['sha'])
        self.assertEqual(set(run), {'status', 'message', 'sha', 'started_at', 'finished_at'})
        self.assertNotIn('secreto', json.dumps(payload))

    def test_finished_at_invalido_se_ignora(self):
        self.set_deployed()
        self.set_remote()
        self.set_run_status(status='failed', finished_at='ayer por la tarde')
        self.client.force_login(self.admin)

        self.assertIsNone(self.client.get(self.status_url).json()['last_run']['finished_at'])

    def test_despliegue_reciente_bloquea_el_panel(self):
        # El limite no puede ser "todo running es huerfano": mientras el deploy
        # esta de verdad en curso, el boton tiene que seguir sin aparecer.
        self.set_deployed()
        self.set_remote()
        started = datetime.now(UTC) - timedelta(minutes=5)
        self.set_run_status(status='running', sha=SHA_LATEST, started_at=started.isoformat())
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(self.status_url).json()['state'], 'running')

    def test_despliegue_huerfano_no_deja_el_panel_pegado(self):
        # Un `systemctl stop` a mitad de un despliegue deja `running` escrito y
        # nunca llega a escribir el final. Pasada la hora, el panel tiene que
        # volver a comparar commits en vez de decir "Desplegando…" para siempre.
        self.set_deployed()
        self.set_remote()
        started = datetime.now(UTC) - timedelta(hours=2)
        self.set_run_status(status='running', sha=SHA_LATEST, started_at=started.isoformat())
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'available')
        # El estado huerfano se sigue reportando: es informacion, no se borra.
        self.assertEqual(payload['last_run']['status'], 'running')


class UpdateRequestViewTest(UpdateTestCase):
    def test_no_superusuario_no_puede_pedir(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.post(self.request_url).status_code, 403)

    def test_get_no_permitido(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.request_url).status_code, 405)

    def test_pide_el_despliegue(self):
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 202)
        self.assertTrue(os.path.exists(self.request_file))
        with open(self.request_file, encoding='utf-8') as handle:
            payload = json.load(handle)
        self.assertEqual(payload['requested_by'], 'admin')
        self.assertEqual(payload['expected_sha'], SHA_LATEST)

    def test_al_dia_no_encola_nada(self):
        self.set_deployed()
        self.set_remote(sha=SHA_DEPLOYED)
        self.client.force_login(self.admin)

        response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(os.path.exists(self.request_file))

    def test_no_encola_dos_veces(self):
        # Segunda peticion con la primera todavia en disco: se rechaza en vez de
        # sobrescribirla, que es lo que haria un write normal.
        self.set_deployed()
        self.set_remote()
        self.write(self.request_file, '{"requested_by": "otro"}')
        self.client.force_login(self.admin)

        response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 409)
        with open(self.request_file, encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['requested_by'], 'otro')

    def test_despliegue_en_curso_no_acepta_otro(self):
        self.set_deployed()
        self.set_remote()
        self.set_run_status(status='running', sha=SHA_LATEST)
        self.client.force_login(self.admin)

        response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(os.path.exists(self.request_file))

    def test_sin_commit_desplegado_explica_el_motivo(self):
        # En desarrollo no existe el archivo del commit desplegado. El 409 tiene
        # que decir eso, no "no se pudo comprobar" a secas: lo segundo manda a
        # revisar la red cuando el problema es otro.
        self.set_remote()
        self.client.force_login(self.admin)

        response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 409)
        self.assertIn('no registra', response.json()['reason'])
        self.assertFalse(os.path.exists(self.request_file))

    def test_sin_permiso_de_escritura_avisa(self):
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        with mock.patch.object(update_module.os, 'open', side_effect=PermissionError):
            response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 503)
        self.assertIn('no acepta peticiones', response.json()['reason'])


class LsRemoteTest(TestCase):
    """El parseo de ``git ls-remote``: es la unica salida de red del panel."""

    def test_parsea_el_sha(self):
        completed = mock.Mock(returncode=0, stdout=f'{SHA_LATEST}\trefs/heads/main\n', stderr='')
        with mock.patch.object(update_module.subprocess, 'run', return_value=completed):
            self.assertEqual(update_module._ls_remote(), {'sha': SHA_LATEST, 'error': None})

    def test_git_ausente(self):
        with mock.patch.object(update_module.subprocess, 'run', side_effect=FileNotFoundError):
            payload = update_module._ls_remote()

        self.assertIsNone(payload['sha'])
        self.assertIn('git', payload['error'])

    def test_timeout(self):
        timeout = update_module.subprocess.TimeoutExpired('git', 8)
        with mock.patch.object(update_module.subprocess, 'run', side_effect=timeout):
            payload = update_module._ls_remote()

        self.assertIsNone(payload['sha'])
        self.assertIn('tiempo', payload['error'])

    def test_fallo_del_comando(self):
        completed = mock.Mock(returncode=128, stdout='', stderr='fatal: no such repo')
        with mock.patch.object(update_module.subprocess, 'run', return_value=completed):
            payload = update_module._ls_remote()

        self.assertIsNone(payload['sha'])
        self.assertEqual(payload['error'], 'No se pudo consultar el repositorio.')

    def test_salida_inesperada(self):
        completed = mock.Mock(returncode=0, stdout='no-es-un-sha refs/heads/main\n', stderr='')
        with mock.patch.object(update_module.subprocess, 'run', return_value=completed):
            payload = update_module._ls_remote()

        self.assertIsNone(payload['sha'])

    def test_no_hay_prompt_de_credenciales(self):
        # Sin terminal, un remoto que empiece a pedir auth no debe colgar la
        # peticion del dashboard hasta el timeout.
        completed = mock.Mock(returncode=0, stdout=f'{SHA_LATEST}\trefs/heads/main\n', stderr='')
        with mock.patch.object(update_module.subprocess, 'run', return_value=completed) as run:
            update_module._ls_remote()

        self.assertEqual(run.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')


class DashboardNavbarTest(UpdateTestCase):
    def test_el_dashboard_incluye_el_aviso(self):
        # Regresion: el aviso vive en el navbar y el navbar tiene que estar en la
        # pagina, que ademas tiene que seguir renderizando sin datos.
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'update-navbar')
        self.assertContains(response, 'dashboard-update.js')

    def test_el_aviso_aparece_en_cualquier_pagina_del_dashboard(self):
        # Esta en el layout, no en el index: si alguien lo vuelve a mover a una
        # sola pagina, este test lo delata.
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        response = self.client.get(reverse('dashboard:tasks'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'update-navbar')

    def test_el_aviso_no_se_renderiza_para_no_superusuario(self):
        self.client.force_login(self.plain)

        response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'update-navbar')
