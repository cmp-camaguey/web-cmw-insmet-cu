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
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
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

    def test_intento_fallido_sobre_el_commit_pendiente_no_se_anuncia_como_disponible(self):
        # El caso que rompio produccion el 2026-11-11: el deploy murio antes de
        # escribir /etc/webcmp/deployed, asi que el commit siguio pendiente y el
        # panel lo anunciaba como "actualizacion disponible". Con `available` de
        # por medio el admin no ve nunca que el intento fallo: clica, el
        # despliegue vuelve a fallar y el aviso reaparece identico. El fallo
        # tiene que ser el estado, no una rama que el navegador nunca alcanza.
        self.set_deployed()
        self.set_remote()
        self.set_run_status(
            status='failed',
            sha=SHA_LATEST,
            started_at='2026-11-11T22:52:52+00:00',
            finished_at='2026-11-11T22:53:06+00:00',
        )
        self.client.force_login(self.admin)

        payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'failed')
        self.assertEqual(payload['last_run']['status'], 'failed')
        # Sin rango que comparar: el commit pendiente es el que acaba de fallar.
        # Pedir los mensajes seria una salida de red que no cambia nada.
        self.assertIsNone(payload['commits'])

    def test_intento_fallido_de_otro_commit_sigue_siendo_actualizacion_disponible(self):
        # Un fallo viejo no mancha un commit nuevo: si main avanzo despues del
        # intento fallido, lo pendiente no se intento todavia y el boton tiene
        # que ofrecer la actualizacion.
        self.set_deployed()
        self.set_remote()
        self.set_run_status(status='failed', sha='c' * 40)
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare', return_value=None):
            payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'available')
        self.assertEqual(payload['latest'], SHA_LATEST)

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

    def test_estado_fallido_permite_reintentar(self):
        # Si el panel dice "el despliegue fallo" y el boton no hace nada, el
        # unico camino que queda es SSH. Reintentar es justo lo que hace falta
        # despues de arreglar la causa, y el estado `failed` sigue siendo "hay
        # un commit pendiente": lo que cambio es que ya se intento.
        self.set_deployed()
        self.set_remote()
        self.set_run_status(status='failed', sha=SHA_LATEST)
        self.client.force_login(self.admin)

        response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 202)
        self.assertTrue(os.path.exists(self.request_file))
        with open(self.request_file, encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['expected_sha'], SHA_LATEST)

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

    def test_el_aviso_no_imprime_comentarios_literales(self):
        # Django solo entiende `{# ... #}` cuando cabe en una linea; uno de
        # varias se imprime tal cual en el HTML. Ya paso una vez en este repo,
        # y este widget es justo donde volvio a colarse.
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        html = self.client.get(reverse('dashboard:index')).content.decode()

        self.assertNotIn('{#', html)

    def test_el_desplegable_tiene_el_contenedor_de_mensajes(self):
        # El JS busca `#update-navbar-commits`; si el contenedor desaparece del
        # template, la lista se queda sin donde pintarse y falla en silencio.
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        response = self.client.get(reverse('dashboard:index'))

        self.assertContains(response, 'update-navbar-commits')

    def test_el_aviso_no_se_renderiza_para_no_superusuario(self):
        self.client.force_login(self.plain)

        response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'update-navbar')


class FakeResponse:
    """Respuesta minima de `urlopen`: solo lo que `_fetch_compare` usa."""

    def __init__(self, payload):
        self._payload = payload

    def read(self, size=None):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class CompareFetchTest(TestCase):
    """El parseo de `compare`: segunda y ultima salida de red del panel."""

    def payload(self, **overrides):
        body = {
            'total_commits': 2,
            'commits': [
                {
                    'sha': 'c' * 40,
                    'commit': {
                        'message': 'feat(commercial): arreglar facturacion\n\nCuerpo que no cabe.',
                        'author': {'name': 'Yoelvis'},
                    },
                },
                {
                    'sha': 'd' * 40,
                    'commit': {'message': 'chore: pin', 'author': {'name': 'Yoelvis'}},
                },
            ],
        }
        body.update(overrides)
        return json.dumps(body).encode('utf-8')

    def fetch(self, payload):
        with mock.patch.object(
            update_module.urllib.request, 'urlopen', return_value=FakeResponse(payload)
        ) as urlopen:
            return update_module._fetch_compare(SHA_DEPLOYED, SHA_LATEST), urlopen

    def test_rechaza_un_esquema_que_no_sea_https(self):
        # `UPDATE_REPO_API` sale de la configuracion, pero una variable de
        # entorno mal puesta podria apuntar a `file:///etc/shadow` y el panel
        # leeria del disco del servidor. Se exige https antes de abrir nada, y
        # asi la excepcion de Bandit (B310) tiene una razon real detras.
        with (
            mock.patch.object(
                update_module.urllib.request, 'urlopen', return_value=FakeResponse(b'{}')
            ) as urlopen,
            mock.patch.object(update_module.settings, 'UPDATE_REPO_API', 'file:///etc/shadow'),
        ):
            parsed = update_module._fetch_compare(SHA_DEPLOYED, SHA_LATEST)

        self.assertIsNone(parsed)
        urlopen.assert_not_called()

    def test_parsea_mensaje_sha_y_autor(self):
        parsed, _ = self.fetch(self.payload())

        first = parsed['commits'][0]
        self.assertEqual(first['sha'], 'c' * 7)
        # Solo la primera linea: el cuerpo de un commit no va al desplegable.
        self.assertEqual(first['subject'], 'feat(commercial): arreglar facturacion')
        self.assertEqual(first['author'], 'Yoelvis')
        self.assertFalse(parsed['truncated'])
        self.assertEqual(parsed['total'], 2)

    def test_recorta_y_marca_los_que_sobran(self):
        many = [{'sha': 'e' * 40, 'commit': {'message': f'commit {i}'}} for i in range(15)]
        parsed, _ = self.fetch(self.payload(total_commits=15, commits=many))

        self.assertEqual(len(parsed['commits']), update_module.COMPARE_MAX_COMMITS)
        self.assertTrue(parsed['truncated'])
        self.assertEqual(parsed['total'], 15)

    def test_sha_invalido_no_se_reenvia(self):
        body = {
            'commits': [
                {'sha': '../../etc/passwd', 'commit': {'message': 'x', 'author': {'name': 'y'}}}
            ]
        }
        parsed, _ = self.fetch(json.dumps(body).encode('utf-8'))

        self.assertIsNone(parsed['commits'][0]['sha'])

    def test_json_invalido(self):
        parsed, _ = self.fetch(b'<html>no es json</html>')

        self.assertIsNone(parsed)

    def test_respuesta_sin_commits(self):
        # La API responde `{"message": "Not Found"}` cuando el rango no existe
        # (force push, commit expirado). Eso no es una lista vacia.
        parsed, _ = self.fetch(b'{"message": "Not Found"}')

        self.assertIsNone(parsed)

    def test_limite_anonimo_agotado(self):
        error = urllib.error.HTTPError('https://api.github.com', 403, 'rate limit', {}, None)
        with mock.patch.object(update_module.urllib.request, 'urlopen', side_effect=error):
            self.assertIsNone(update_module._fetch_compare(SHA_DEPLOYED, SHA_LATEST))

    def test_red_caida(self):
        error = urllib.error.URLError('sin ruta al host')
        with mock.patch.object(update_module.urllib.request, 'urlopen', side_effect=error):
            self.assertIsNone(update_module._fetch_compare(SHA_DEPLOYED, SHA_LATEST))

    def test_manda_user_agent(self):
        # Sin User-Agent la API responde 403 y el panel perderia los mensajes
        # por un motivo que no tiene nada que ver con el repositorio.
        _, urlopen = self.fetch(self.payload())

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header('User-agent'), 'webcmp-dashboard')


class PendingCommitsTest(UpdateTestCase):
    """Contrato del panel: los mensajes son un extra, nunca un requisito."""

    COMMITS = {
        'commits': [{'sha': 'b' * 7, 'subject': 'fix: algo', 'author': 'Yoelvis'}],
        'total': 1,
        'truncated': False,
    }

    def test_hay_actualizacion_incluye_los_mensajes(self):
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare', return_value=self.COMMITS):
            payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['commits']['commits'][0]['subject'], 'fix: algo')

    def test_al_dia_no_consulta_la_api(self):
        # Con la misma version desplegada no hay rango que comparar: una
        # peticion por carga del panel solo gastaria el limite anonimo.
        self.set_deployed()
        self.set_remote(sha=SHA_DEPLOYED)
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare') as fetch:
            payload = self.client.get(self.status_url).json()

        fetch.assert_not_called()
        self.assertIsNone(payload['commits'])

    def test_sin_commit_desplegado_no_consulta_la_api(self):
        self.set_remote()
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare') as fetch:
            self.client.get(self.status_url)

        fetch.assert_not_called()

    def test_fallo_de_la_api_no_rompe_el_estado(self):
        # Lo que decide si actualizar son los SHA, no los mensajes. Si GitHub
        # limita o esta caido, el aviso sigue siendo cierto.
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare', return_value=None):
            payload = self.client.get(self.status_url).json()

        self.assertEqual(payload['state'], 'available')
        self.assertIsNone(payload['commits'])
        self.assertIsNone(payload['check_error'])

    def test_pedir_el_despliegue_no_consulta_los_mensajes(self):
        # El POST decide con los SHA. La llamada a compare sobra, y con GitHub
        # lento el clic esperaria hasta el timeout antes de encolar nada.
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare') as fetch:
            response = self.client.post(self.request_url)

        self.assertEqual(response.status_code, 202)
        fetch.assert_not_called()

    def test_cachea_el_rango(self):
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare', return_value=self.COMMITS) as fetch:
            self.client.get(self.status_url)
            self.client.get(self.status_url)

        self.assertEqual(fetch.call_count, 1)

    def test_el_cache_no_sobrevive_a_un_despliegue(self):
        # La clave lleva los dos SHA: despues de desplegar, el rango viejo ya no
        # describe nada y no puede servirse de cache.
        self.set_deployed()
        self.set_remote()
        self.client.force_login(self.admin)

        with mock.patch.object(update_module, '_fetch_compare', return_value=self.COMMITS) as fetch:
            self.client.get(self.status_url)
            self.set_deployed('f' * 40)
            self.client.get(self.status_url)

        self.assertEqual(fetch.call_count, 2)


class DashboardUpdateScriptTest(TestCase):
    def test_el_script_no_asigna_html_crudo(self):
        # Un mensaje de commit lo escribe cualquiera que pueda pushear a main.
        # Asignar `innerHTML` ahi seria XSS almacenado en el panel del
        # administrador; la lista se construye con `createElement` y
        # `textContent`. Se busca la asignacion, no la palabra: el comentario
        # que explica la decision la nombra a proposito.
        source = (Path(settings.BASE_DIR) / 'static/dist/js/dashboard-update.js').read_text(
            encoding='utf-8'
        )

        self.assertNotRegex(source, r'\.innerHTML\s*=')
        self.assertNotIn('insertAdjacentHTML', source)
        self.assertIn('createElement', source)

    def test_el_script_no_revienta_si_falta_la_lista(self):
        # El proyecto no tiene runner de JS (sin Node, sin bundler), asi que la
        # unica comprobacion posible es sobre el fuente. `commitsBox` se resuelve
        # fuera del render: si el template viejo sigue en cache y el JS ya es el
        # nuevo, la lista no existe. Sin guarda, `clearCommits` lanzaria
        # `TypeError` y el aviso de actualizacion dejaria de pintarse entero.
        source = (Path(settings.BASE_DIR) / 'static/dist/js/dashboard-update.js').read_text(
            encoding='utf-8'
        )

        self.assertIn('if (!commitsBox)', source)
