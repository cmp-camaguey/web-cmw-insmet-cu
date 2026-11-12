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


def apply_status(path, now=None) -> dict | None:
    """Estado legible del último reinicio, o `None` si no hay nada que informar.

    Un archivo ilegible o con una forma que no se reconoce devuelve `None`: el
    panel no puede inventar un resultado a partir de datos que no entiende.
    """
    raw = _read_status(path)
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


def _read_status(path) -> dict | None:
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


def _instant(value: datetime) -> str:
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M')


def _clock(value: datetime) -> str:
    return timezone.localtime(value).strftime('%H:%M')
