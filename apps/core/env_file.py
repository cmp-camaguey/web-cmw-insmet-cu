"""Lectura y escritura quirúrgica del archivo `.env`.

El panel de "Entorno del servidor" administra un subconjunto de variables. Este
módulo existe para que guardar ese subconjunto no reescriba el archivo entero:
los comentarios que explican decisiones de operación, el orden de las secciones
y las claves que el panel no conoce se conservan tal cual. Un `.env` regenerado
desde cero perdería esa información y, peor, podría perder una clave que otra
parte del despliegue agregó a mano.

La escritura es atómica y serializada:

- Se escribe un temporal en el MISMO directorio y se publica con `os.replace`,
  así que un corte a mitad de la escritura deja el archivo viejo intacto y no
  uno truncado. Un temporal en `/tmp` no sirve: `os.replace` entre sistemas de
  archivos distintos falla.
- Se toma un `flock` exclusivo sobre `<archivo>.lock`, para que dos guardados
  simultáneos no se pisen: el segundo leería el contenido viejo y borraría el
  cambio del primero.

El formato es el de `python-dotenv` y el que escribe `scripts/generate_env.py`:
`CLAVE=valor`, líneas `#` como comentario y valores con espacios entre comillas.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import tempfile
from pathlib import Path

try:  # pragma: no cover - Windows no tiene fcntl; allí el bloqueo es un no-op
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

# `export CLAVE=valor` no lo usa el generador, pero un `.env` editado a mano
# puede traerlo y perder el prefijo al guardar sería un cambio gratuito.
_ASSIGNMENT_RE = re.compile(
    r'^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<rest>.*)$'
)
# Comentario al final de la línea. Solo se reconoce cuando el valor no está
# entrecomillado: con comillas, un `#` puede ser parte del valor y cortar ahí
# corrompería el dato.
_INLINE_COMMENT_RE = re.compile(r'^(?P<value>.*?)(?P<comment>\s+#.*)$')


def read_env_file(path) -> dict[str, str]:
    """Variables del archivo como `dict`. Silencioso si no existe o no se puede leer."""
    data: dict[str, str] = {}
    try:
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith('#') or '=' not in stripped:
                    continue
                key, _, value = stripped.partition('=')
                data[key.strip()] = _unquote(value.strip())
    except OSError:
        pass
    return data


def is_writable(path) -> bool:
    """¿Se puede guardar en `path`?

    La escritura es atómica (`os.replace`), así que lo que manda es el permiso
    sobre el DIRECTORIO, no sobre el archivo. Se exige además poder leerlo,
    porque cada guardado hace lectura-modificación-escritura.
    """
    path = Path(path)
    if not os.access(path.parent, os.W_OK):
        return False
    return not path.exists() or os.access(path, os.R_OK)


def update_env_file(path, updates: dict[str, str]) -> list[str]:
    """Actualiza solo las claves de `updates` y devuelve las que cambiaron.

    Lanza `ValueError` si un valor no se puede representar en el formato (por
    ejemplo, si trae un salto de línea).
    """
    path = Path(path)
    quoted = {key: _quote_value(value) for key, value in updates.items()}
    if not quoted:
        return []

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f'{path.name}.lock')

    with open(lock_path, 'a', encoding='utf-8') as lock:
        _lock(lock)
        try:
            return _rewrite(path, quoted)
        finally:
            _unlock(lock)


def _rewrite(path: Path, quoted: dict[str, str]) -> list[str]:
    original = ''
    mode = None
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        with open(path, encoding='utf-8', newline='') as handle:
            original = handle.read()

    newline = '\r\n' if '\r\n' in original else '\n'
    pending = dict(quoted)
    changed: list[str] = []
    lines: list[str] = []

    for line in original.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        ending = line[len(body) :]
        parsed = _ASSIGNMENT_RE.match(body)
        if parsed and parsed.group('key') in pending:
            key = parsed.group('key')
            _, comment = _split_value(parsed.group('rest'))
            lines.append(f'{parsed.group("prefix")}{key}={pending.pop(key)}{comment}{ending}')
            changed.append(key)
        else:
            lines.append(line)

    if pending:
        # Sin salto final, agregar una línea pegada a la última rompería el
        # último valor. Se completa primero.
        if lines and not lines[-1].endswith(('\n', '\r')):
            lines[-1] = lines[-1] + newline
        for key, value in pending.items():
            lines.append(f'{key}={value}{newline}')
            changed.append(key)

    _atomic_write(path, ''.join(lines), mode)
    return changed


def _atomic_write(path: Path, text: str, mode: int | None) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp'
    )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode if mode is not None else 0o600)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _split_value(rest: str) -> tuple[str, str]:
    """Separa `valor` de un comentario final. Devuelve `(valor, comentario)`."""
    rest = rest.strip()
    if rest[:1] in ("'", '"'):
        quote = rest[0]
        end = rest.find(quote, 1)
        if end != -1:
            tail = rest[end + 1 :]
            if tail.strip().startswith('#'):
                return rest[: end + 1], tail
            return rest, ''
    match = _INLINE_COMMENT_RE.match(rest)
    if match:
        return match.group('value').strip(), match.group('comment')
    return rest, ''


def _quote_value(value) -> str:
    value = '' if value is None else str(value)
    if '\n' in value or '\r' in value:
        raise ValueError('Un valor de entorno no puede contener saltos de línea.')
    if value == '':
        return ''
    if not any(char.isspace() for char in value) and '#' not in value:
        return value
    if "'" in value and '"' in value:
        raise ValueError(
            'El valor mezcla comillas simples y dobles; no se puede escribir sin corromperlo.'
        )
    quote = '"' if "'" in value else "'"
    return f'{quote}{value}{quote}'


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _lock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
