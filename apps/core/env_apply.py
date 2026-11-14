"""Petición de reinicio del panel de "Entorno del servidor".

Guardar el archivo de entorno no cambia nada en caliente: Django lo leyó al
arrancar. Para que los valores nuevos entren en vigor hay que reiniciar los
servicios, y el proceso web no puede hacerlo ni debe poder: corre sin
privilegios y ejecutar `systemctl` desde ahí sería darle root a la aplicación.

El mecanismo es el mismo del botón "Actualizar": el panel escribe un archivo en
un directorio que le pertenece, una unidad `systemd .path` lo ve aparecer y
arranca un ejecutor con los privilegios que el proceso web no tiene. El contenido
del archivo no se usa como destino —solo dice quién lo pidió—, así que un proceso
web comprometido no consigue más que reiniciar los servicios que ya están
declarados en la unidad.

El resultado del intento lo escribe el ejecutor en un JSON que esta página lee.
Sin eso, un reinicio que falló se vería igual que uno que nunca se pidió, y el
operador se quedaría esperando un cambio que no llegó.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from django.utils import timezone

logger = logging.getLogger(__name__)

# Un reinicio de los dos servicios tarda segundos. Pasado este plazo, un estado
# `running` ya no describe un reinicio en curso sino uno que se quedó sin final:
# systemd mató la unidad por timeout, o el ejecutor no llegó a escribir el
# desenlace. Sin esta ventana el panel mostraría "en curso" para siempre.
RUN_STALE_SECONDS = 180

# Margen antes de declarar huerfana una peticion. El ejecutor consume el archivo
# en milisegundos, pero la pagina se vuelve a pintar justo despues de crearlo, y
# sin margen el primer render diria "nadie la recogio" de una peticion que si se
# estaba procesando. Treinta segundos es mas que cualquier arranque de systemd y
# menos que lo que tarda un operador en mirar la pantalla y desconfiar.
PENDING_GRACE_SECONDS = 30


class ApplyRequestPendingError(Exception):
    """Ya hay una petición en disco: no se encola otra."""


def can_request(path) -> bool:
    """¿Puede el proceso web dejar la petición en `path`?

    Lo que manda es el permiso del DIRECTORIO: el archivo se crea con `O_EXCL`,
    así que nunca se escribe sobre uno existente.
    """
    parent = Path(path).parent
    return os.access(parent, os.W_OK | os.X_OK)


def request_apply(path, user) -> dict:
    """Deja la petición de reinicio.

    Lanza `ApplyRequestPendingError` si ya hay una, y `OSError` si el directorio no
    acepta el archivo.
    """
    payload = {
        'requested_by': user.get_username(),
        'requested_at': datetime.now(UTC).isoformat(),
    }
    try:
        # O_EXCL y no un write normal: dos pestañas con el botón pulsado a la vez
        # no deben encolar dos reinicios.
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as error:
        raise ApplyRequestPendingError from error
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)
    return payload


def pending_request(path, now=None) -> dict | None:
    """Petición de reinicio que nadie recogió, o `None` si no hay ninguna.

    El ejecutor borra el archivo ANTES de hacer nada, así que una petición que
    sigue en disco y ya pasó el margen no está "en curso": no la procesó nadie.
    Sin esta comprobación el panel no distingue "nadie la recogió" de "nunca se
    pidió", y las dos se ven igual: ningún aviso. Es el estado en el que queda un
    servidor al que le falta la unidad `systemd` que vigila el archivo.
    """
    try:
        modified = os.stat(path).st_mtime
    except OSError:
        return None

    reference = (now or datetime.now(UTC)).timestamp()
    if reference - modified < PENDING_GRACE_SECONDS:
        # Recién escrita: el ejecutor todavía puede recogerla.
        return None

    raw = _read_json(path) or {}
    return {
        'tone': 'warning',
        'icon': 'plug-off',
        'text': _pending_text(str(raw.get('requested_by') or '').strip(), modified),
    }


def apply_status(path, now=None) -> dict | None:
    """Estado legible del último reinicio, o `None` si no hay nada que informar.

    Un archivo ilegible o con una forma que no se reconoce devuelve `None`: el
    panel no puede inventar un resultado a partir de datos que no entiende.
    """
    raw = _read_json(path)
    if raw is None:
        return None

    state = str(raw.get('status') or '').strip().lower()
    if state not in ('running', 'ok', 'failed'):
        return None

    requested_by = str(raw.get('requested_by') or '').strip()
    started = _parse_instant(raw.get('started_at'))
    finished = _parse_instant(raw.get('finished_at'))

    if state == 'running' and _is_stale(started, now):
        state = 'stale'

    return {
        'state': state,
        'tone': _TONES[state],
        'icon': _ICONS[state],
        'text': _status_text(
            state,
            requested_by=requested_by,
            started=started,
            finished=finished,
            error=str(raw.get('error') or '').strip(),
        ),
    }


# La plantilla no decide colores: el estado ya sabe cómo se ve. Así un estado
# nuevo no se queda sin estilo porque alguien olvidó tocar el HTML.
_TONES = {
    'running': 'info',
    'ok': 'success',
    'failed': 'danger',
    'stale': 'warning',
}

_ICONS = {
    'running': 'loader',
    'ok': 'circle-check',
    'failed': 'alert-triangle',
    'stale': 'clock-exclamation',
}


def _read_json(path) -> dict | None:
    """El archivo como objeto JSON, o `None` si no se puede leer.

    Lo usan los dos archivos del mecanismo: el de estado y el de petición. Un
    archivo ilegible o con una forma que no se reconoce no se adivina.
    """
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except OSError, ValueError:
        # `ValueError` cubre el JSON a medias: el ejecutor escribe con
        # `os.replace`, pero un archivo escrito a mano puede estar roto y la
        # página no puede caerse por eso.
        return None
    return data if isinstance(data, dict) else None


def _parse_instant(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # El ejecutor escribe con `date -Is`, que lleva el desfase de la zona del
    # servidor. Un valor sin zona se interpreta como UTC en vez de descartarse.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_stale(started: datetime | None, now: datetime | None) -> bool:
    if started is None:
        # Un `running` sin instante de arranque no se puede acotar, y dejarlo
        # "en curso" para siempre es peor que decir que quedó sin confirmar.
        return True
    reference = now or datetime.now(UTC)
    return (reference - started).total_seconds() > RUN_STALE_SECONDS


def _status_text(state, *, requested_by, started, finished, error) -> str:
    who = f' (solicitado por {requested_by})' if requested_by else ''
    if state == 'running':
        since = f' desde las {_clock(started)}' if started else ''
        return f'Reinicio en curso{since}{who}.'
    if state == 'ok':
        when = f' el {_instant(finished)}' if finished else ''
        return f'Último reinicio completado{when}{who}.'
    if state == 'failed':
        detail = f': {error}' if error else '.'
        return f'El último reinicio falló{detail}'
    return (
        'El último reinicio quedó sin confirmar. Revise el estado de los servicios en el servidor.'
    )


def _pending_text(requested_by: str, modified: float) -> str:
    who = f' (solicitada por {requested_by})' if requested_by else ''
    clock = _clock(datetime.fromtimestamp(modified, tz=UTC))
    return (
        f'La petición de reinicio de las {clock} quedó sin recoger{who}. '
        'Ningún proceso la procesó: la unidad que la vigila no está instalada o no está '
        'activa en el servidor, así que los cambios guardados siguen sin aplicarse. '
        'Revise systemctl status webcmp-apply.path en el servidor.'
    )


def _instant(value: datetime) -> str:
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M')


def _clock(value: datetime) -> str:
    return timezone.localtime(value).strftime('%H:%M')
