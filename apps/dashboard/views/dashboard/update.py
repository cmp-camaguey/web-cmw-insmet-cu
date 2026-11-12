"""Boton de actualizacion del panel (change 025-update-web-deploy).

Tres piezas, y cada una vive donde puede vivir:

- El **chequeo** ("hay un commit nuevo en ``main``") lo hace Django con
  ``git ls-remote`` contra el repo publico. Sin credenciales: el repositorio es
  publico, asi que no hay clave que guardar ni secret que rotar.
- La **peticion** de despliegue es un archivo. El panel corre como ``webcmp``,
  que tiene el shell en ``/bin/false`` y ningun privilegio. Escribe la peticion
  y se va. Darle sudo sobre ``webcmp-deploy`` convertiria cualquier RCE de
  Django en root en produccion; escribir un archivo no.
- Quien **ejecuta** es el usuario ``deploy``, con el sudoers que ya existia,
  disparado por una unidad systemd ``.path`` que reacciona al archivo. El
  usuario de despliegue no consulta la base de datos ni la red del panel: solo
  lee un archivo y llama al script que ya es su unico permiso.

El limite de privilegios queda igual que antes de este cambio: un compromiso
del proceso web consigue, como mucho, pedir un despliegue de lo que ya esta en
``main``. No consigue root.
"""

import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.views.generic import View

from apps.core.cache_utils import safe_cache_get, safe_cache_set

logger = logging.getLogger(__name__)

SHA_RE = re.compile(r'^[0-9a-f]{40}$')
REMOTE_CACHE_KEY = 'dashboard:update:remote_sha'
# El fallo se cachea menos que el exito: GitHub caido no debe dejar el panel
# mintiendo "no se pudo comprobar" durante cinco minutos despues de volver.
REMOTE_FAILURE_TTL = 60
# Estados que el ejecutor puede escribir. Cualquier otra cosa en el archivo se
# ignora: el archivo lo escribe otro usuario y no es una entrada confiable.
RUN_STATUSES = {'running', 'ok', 'failed'}
# Mensaje fijo por estado. El detalle real va al log del servidor, no al panel:
# el stderr de git puede contener rutas absolutas del servidor.
RUN_MESSAGES = {
    'running': 'Desplegando…',
    'ok': 'Despliegue completado.',
    'failed': 'El despliegue fallo. Revisa el log del servidor.',
}
# Un `running` mas viejo que esto solo puede ser un estado huerfano: la unidad
# de systemd tiene `TimeoutStartSec=45min`, asi que a la hora el ejecutor ya no
# esta corriendo. Sin este limite, un `systemctl stop` a mitad de un despliegue
# deja el panel en "Desplegando…" para siempre y el boton no vuelve nunca.
RUN_STALE_SECONDS = 3600

# --- Mensajes de los commits pendientes -------------------------------------
# La clave lleva los dos SHA: un rango cacheado no sirve despues de desplegar,
# porque el commit desplegado ya es otro.
COMPARE_CACHE_KEY = 'dashboard:update:compare:{deployed}:{latest}'
COMPARE_TTL = 300
COMPARE_FAILURE_TTL = 60
# Un desplegable con ochenta lineas no se lee. El resto se resume en el contador
# que ya devuelve la API (`total_commits`).
COMPARE_MAX_COMMITS = 10
# La respuesta de `compare` trae el diff de cada commit. Sin tope, un release
# grande devuelve megas que solo se van a descartar.
COMPARE_MAX_BYTES = 512 * 1024


def _read_text(path):
    """Contenido de ``path`` sin espacios en los extremos, o ``None``."""
    try:
        with open(path, encoding='utf-8') as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning('No se pudo leer %s', path, exc_info=True)
        return None


def _ls_remote():
    """SHA de ``main`` segun el remoto, o el motivo por el que no se pudo."""
    try:
        process = subprocess.run(
            ['git', 'ls-remote', settings.UPDATE_REPO_URL, settings.UPDATE_BRANCH_REF],
            capture_output=True,
            text=True,
            timeout=settings.UPDATE_CHECK_TIMEOUT,
            # Sin terminal no hay quien conteste un pedido de credenciales: sin
            # esto, un remoto que empiece a pedir auth cuelga hasta el timeout.
            env={
                **os.environ,
                'GIT_TERMINAL_PROMPT': '0',
                'GIT_ASKPASS': '',
                'SSH_ASKPASS': '',
            },
        )
    except FileNotFoundError:
        logger.warning('git no esta instalado en el servidor')
        return {'sha': None, 'error': 'git no está instalado en el servidor.'}
    except subprocess.TimeoutExpired:
        logger.warning('git ls-remote supero %ss', settings.UPDATE_CHECK_TIMEOUT)
        return {'sha': None, 'error': 'El repositorio no respondió a tiempo.'}
    except OSError:
        logger.warning('No se pudo ejecutar git ls-remote', exc_info=True)
        return {'sha': None, 'error': 'No se pudo consultar el repositorio.'}

    if process.returncode != 0:
        logger.warning(
            'git ls-remote fallo (%s): %s', process.returncode, process.stderr.strip()[:500]
        )
        return {'sha': None, 'error': 'No se pudo consultar el repositorio.'}

    fields = process.stdout.split()
    if not fields or not SHA_RE.match(fields[0]):
        logger.warning('git ls-remote devolvio algo inesperado: %r', process.stdout[:200])
        return {'sha': None, 'error': 'El repositorio respondió algo inesperado.'}

    return {'sha': fields[0], 'error': None}


def remote_sha(refresh=False):
    """SHA de ``main`` en el remoto, cacheado ``UPDATE_CHECK_TTL`` segundos."""
    if not refresh:
        cached = safe_cache_get(REMOTE_CACHE_KEY)
        if cached is not None:
            return cached

    payload = _ls_remote()
    ttl = settings.UPDATE_CHECK_TTL if payload['sha'] else REMOTE_FAILURE_TTL
    safe_cache_set(REMOTE_CACHE_KEY, payload, ttl)
    return payload


def deployed_sha():
    """Commit que ``deploy.sh`` dejo sirviendo, o ``None`` si no consta."""
    raw = _read_text(settings.UPDATE_DEPLOYED_SHA_FILE)
    return raw if raw and SHA_RE.match(raw) else None


def _fetch_compare(deployed, latest):
    """Commits entre dos SHA segun la API publica, o ``None`` si no se pudo.

    Se lee de GitHub y no del checkout local a proposito: el servidor no tiene
    un clon con historia, y un ``git fetch`` desde la vista escribiria en disco
    y competiria por ``.git/lock``. La API es la misma fuente que ya usa
    ``ls-remote``, con el mismo limite anonimo.
    """
    url = f'{settings.UPDATE_REPO_API.rstrip("/")}/compare/{deployed}...{latest}'
    # `UPDATE_REPO_API` sale de la configuracion, no de la peticion, pero una
    # variable de entorno mal puesta podria apuntar a `file:///etc/shadow` y el
    # panel leeria del disco del servidor. Se exige https antes de abrir nada.
    if urllib.parse.urlsplit(url).scheme != 'https':
        logger.error('UPDATE_REPO_API no apunta a https: %s', settings.UPDATE_REPO_API)
        return None

    request = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/vnd.github+json',
            # La API de GitHub exige un User-Agent; sin el responde 403 y el
            # panel perderia los mensajes sin motivo real.
            'User-Agent': 'webcmp-dashboard',
        },
    )

    timeout = settings.UPDATE_CHECK_TIMEOUT
    try:
        # El esquema se valido arriba y la URL la compone la configuracion, no
        # el usuario; de ahi la excepcion de B310.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            raw = response.read(COMPARE_MAX_BYTES)
    except urllib.error.HTTPError as exc:
        # 403 es el limite anonimo agotado; 404, un commit que ya no esta en la
        # rama. Los dos se degradan igual: sin mensajes, con los SHA que ya se
        # muestran.
        logger.info('compare devolvio HTTP %s', exc.code)
        return None
    except urllib.error.URLError, TimeoutError, OSError:
        logger.warning('No se pudo consultar compare', exc_info=True)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError, UnicodeDecodeError:
        logger.warning('compare no devolvio JSON valido')
        return None

    commits = data.get('commits') if isinstance(data, dict) else None
    if not isinstance(commits, list):
        return None

    total = data.get('total_commits')
    if not isinstance(total, int) or total < 0:
        total = len(commits)

    items = []
    for entry in commits[:COMPARE_MAX_COMMITS]:
        if not isinstance(entry, dict):
            continue
        commit = entry.get('commit') if isinstance(entry.get('commit'), dict) else {}
        message = commit.get('message')
        # Solo la primera linea: el cuerpo de un commit no cabe en el
        # desplegable y no es lo que decide si actualizar.
        subject = message.splitlines()[0].strip() if isinstance(message, str) and message else ''
        sha = entry.get('sha')
        author = commit.get('author') if isinstance(commit.get('author'), dict) else {}
        name = author.get('name') if isinstance(author, dict) else ''
        items.append(
            {
                'sha': sha[:7] if isinstance(sha, str) and SHA_RE.match(sha) else None,
                'subject': subject[:120],
                'author': name[:60] if isinstance(name, str) else '',
            }
        )

    return {'commits': items, 'total': total, 'truncated': total > len(items)}


def pending_commits(deployed, latest, refresh=False):
    """Mensajes de los commits entre ``deployed`` y ``latest``.

    Devuelve ``None`` cuando no se pudo preguntar, y no una lista vacia: el
    panel distingue "no hay mensajes que mostrar" de "no se pudo consultar", y
    solo lo segundo amerita texto de respaldo. El estado del panel no depende de
    esto: si la API falla, sigue diciendo que hay actualizacion.
    """
    if not deployed or not latest or deployed == latest:
        return None

    key = COMPARE_CACHE_KEY.format(deployed=deployed, latest=latest)
    if not refresh:
        cached = safe_cache_get(key)
        # Se envuelve el resultado para poder cachear el fallo: `None` en el
        # cache es indistinguible de un miss.
        if cached is not None:
            return cached.get('data')

    payload = _fetch_compare(deployed, latest)
    ttl = COMPARE_TTL if payload is not None else COMPARE_FAILURE_TTL
    safe_cache_set(key, {'data': payload}, ttl)
    return payload


def _iso_text(value):
    """``value`` si es una fecha ISO 8601 interpretable, o ``None``.

    El archivo de estado lo escribe otro proceso y puede traer cualquier cosa.
    Se valida igual que el SHA, y no solo por higiene: de ``started_at`` depende
    que el panel sepa si un ``running`` sigue vivo o quedo colgado.
    """
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _iso_moment(value):
    """Como ``_iso_text``, pero devuelto como ``datetime`` con zona."""
    text = _iso_text(value)
    if text is None:
        return None
    moment = datetime.fromisoformat(text)
    # `date -Is` escribe con offset, pero un archivo escrito a mano puede venir
    # sin zona. Se asume UTC en vez de reventar al restar.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _run_is_stale(run):
    """``True`` si el ejecutor dice ``running`` pero ya no puede estarlo.

    El ejecutor escribe ``running`` al empezar y sobrescribe el archivo al
    terminar. Si systemd lo mata antes de esa segunda escritura —OOM, `systemctl
    stop`, un corte—, el archivo queda en ``running`` para siempre: el panel
    muestra "Desplegando…" eternamente y el boton no vuelve nunca. El limite es
    holgado a proposito: ``TimeoutStartSec=45min`` en la unidad es el techo real,
    asi que una hora solo puede ser un estado huerfano.
    """
    started = _iso_moment(run.get('started_at'))
    if started is None:
        return False
    return (datetime.now(UTC) - started).total_seconds() > RUN_STALE_SECONDS


def last_run():
    """Ultimo intento de despliegue escrito por el ejecutor, saneado."""
    raw = _read_text(settings.UPDATE_STATUS_FILE)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning('%s no es JSON valido', settings.UPDATE_STATUS_FILE)
        return None
    if not isinstance(data, dict):
        return None

    status = data.get('status')
    if status not in RUN_STATUSES:
        return None

    sha = data.get('sha')
    return {
        'status': status,
        'message': RUN_MESSAGES[status],
        # Solo se devuelve el SHA si tiene forma de SHA: el archivo lo escribe
        # otro proceso y no se reenvia tal cual al navegador.
        'sha': sha if isinstance(sha, str) and SHA_RE.match(sha) else None,
        'started_at': _iso_text(data.get('started_at')),
        'finished_at': _iso_text(data.get('finished_at')),
    }


def build_state(refresh=False, include_commits=True):
    """Estado que consume el panel. Una sola forma, la use quien la use.

    ``include_commits`` existe para la peticion de despliegue: decide con los
    SHA, no con los mensajes, y no tiene por que esperar una segunda salida de
    red que ademas puede tardar el timeout entero.

    El contador de avisos sin leer **no** viaja aqui. Este JSON lee archivos,
    consulta el remoto y compara commits: es caro por diseno y por eso solo lo
    pide el panel de administracion. El contador tiene su propio endpoint
    (`notifications:unread`), que es lo unico que puede consultar la campana del
    portal publico cuando lleguen los clientes (cambio 030).
    """
    deployed = deployed_sha()
    remote = remote_sha(refresh=refresh)
    run = last_run()

    if run and run['status'] == 'running' and not _run_is_stale(run):
        state = 'running'
    elif remote['error']:
        state = 'unknown'
    elif not deployed:
        # Sin saber que se desplego no se puede afirmar "hay actualizacion":
        # seria mandar al admin a un despliegue por un motivo inventado.
        state = 'unknown'
    elif deployed == remote['sha']:
        state = 'up_to_date'
    elif run and run['status'] == 'failed' and run['sha'] == remote['sha']:
        # El ultimo intento fue contra ESTE commit y fallo. El commit sigue
        # pendiente, pero anunciarlo como `available` miente dos veces: oculta
        # el fallo y sugiere que nunca se intento. Paso en produccion el
        # 2026-11-11: el deploy murio antes de escribir /etc/webcmp/deployed y
        # el panel repitio "Actualizacion disponible" para siempre.
        #
        # Se compara el SHA a proposito: un fallo viejo sobre un commit que ya
        # quedo atras no dice nada del que esta pendiente ahora.
        state = 'failed'
    else:
        state = 'available'

    # Motivo legible del `unknown`. Sin esto los dos casos —"el remoto no
    # responde" y "no consta que se desplego"— llegaban al navegador con el
    # mismo texto, y en desarrollo eso manda a revisar la red cuando el
    # problema es que falta el archivo (o `UPDATE_DEPLOYED_SHA_FILE`).
    if state != 'unknown':
        reason = None
    elif remote['error']:
        reason = remote['error']
    else:
        reason = 'Este servidor no registra qué versión está sirviendo.'

    # Solo se pregunta cuando hay algo pendiente: en `up_to_date` no hay rango
    # que comparar, en `unknown` no hay con que comparar, y en `failed` el
    # rango es exactamente el commit que acaba de fallar. Un fallo de la API
    # devuelve `None` y no cambia el estado: los SHA siguen siendo ciertos.
    commits = None
    if include_commits and state == 'available':
        commits = pending_commits(deployed, remote['sha'], refresh=refresh)

    return {
        'state': state,
        'reason': reason,
        'deployed': deployed,
        'deployed_short': deployed[:7] if deployed else None,
        'latest': remote['sha'],
        'latest_short': remote['sha'][:7] if remote['sha'] else None,
        'check_error': remote['error'],
        'last_run': run,
        'commits': commits,
    }


class UpdateStatusView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Estado de actualizaciones, como JSON.

    ``raise_exception`` a proposito: estas vistas son JSON y un redirect al
    login en un ``fetch`` se ve como un 200 con HTML, no como un permiso
    denegado.
    """

    raise_exception = True
    http_method_names = ['get']

    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, *args, **kwargs):
        return JsonResponse(build_state())


class UpdateRequestView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Escribe la peticion de despliegue que recoge systemd."""

    raise_exception = True
    http_method_names = ['post']

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, *args, **kwargs):
        state = build_state(include_commits=False)

        if state['state'] == 'running':
            return JsonResponse(
                {'ok': False, 'reason': 'Ya hay una actualización en curso.'}, status=409
            )
        if state['state'] == 'unknown':
            return JsonResponse(
                {
                    'ok': False,
                    'reason': state['reason'] or 'No se pudo comprobar si hay actualización.',
                },
                status=409,
            )
        # `failed` tambien se acepta: el commit sigue pendiente, lo que cambio es
        # que ya se intento una vez. Sin esto el panel mostraria el fallo y el
        # boton no haria nada, y el unico camino que quedaria es entrar por SSH.
        if state['state'] not in ('available', 'failed'):
            return JsonResponse(
                {'ok': False, 'reason': 'No hay ninguna actualización pendiente.'}, status=409
            )

        payload = {
            'requested_by': request.user.get_username(),
            # Informativo: el ejecutor resuelve el SHA por su cuenta y nunca
            # confia en este valor (ver deploy/autodeploy.sh).
            'expected_sha': state['latest'],
            'requested_at': datetime.now(UTC).isoformat(),
        }

        try:
            # O_EXCL y no un write normal: dos pestanas abiertas con el boton
            # pulsado a la vez no deben encolar dos despliegues.
            descriptor = os.open(
                settings.UPDATE_REQUEST_FILE,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            return JsonResponse(
                {'ok': False, 'reason': 'Ya hay una actualización en curso.'}, status=409
            )
        except OSError:
            logger.exception('No se pudo escribir la peticion de despliegue')
            return JsonResponse(
                {
                    'ok': False,
                    'reason': 'El servidor no acepta peticiones de despliegue.',
                },
                status=503,
            )

        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle)

        logger.info(
            'Despliegue solicitado por %s (esperado %s)',
            payload['requested_by'],
            state['latest_short'],
        )
        return JsonResponse({'ok': True, 'target': state['latest']}, status=202)
