#!/usr/bin/env python3
"""Genera el archivo `.env` del proyecto. Script plano: NO importa Django.

Por qué un script y no un management command
--------------------------------------------
`manage.py` importa los settings ANTES de despachar el comando (`django.setup()`).
Un comando que genera la clave necesita entonces arrancar con settings sin clave
declarada, y esa tolerancia es la razón de que `config/settings/base.py` no pueda
fallar cerrado. Este script no importa settings: puede correr justo en el estado
en que la aplicación no puede, y con él el fail-closed de los settings se vuelve
implementable.

Única dependencia externa: `cryptography` (Fernet). Nada de `django`, nada de
`config.`.

Uso
---
    python scripts/generate_env.py --development
    python scripts/generate_env.py --production
    python scripts/generate_env.py --production --rotate-keys

No es interactivo y no pregunta nada: el modo se declara explícitamente y una
llamada ambigua es un error, no una pregunta. Un generador que adivina el
entorno es un generador que un día escribe el `.env` equivocado en el servidor.

Sobre ENCRYPTION_KEY
--------------------
En `--production` la clave de descifrado NO va al `.env`. Va a un archivo aparte
(por defecto `/etc/webcmp/encryption.env`, `chmod 600`), que el unit de systemd
carga con `EnvironmentFile=-`. Guardar la clave de descifrado junto al texto
cifrado no cifra nada: si el `.env` se filtra, la clave con la que estaba cifrado
filtra con él. En `--development` las dos van en el `.env`, porque un archivo local
no es un artefacto que se filtre y separar las dos solo agrega pasos.

En `--production`, si el archivo de clave no se puede escribir, el script aborta
ANTES de tocar el `.env`: nunca deja un `.env` cuya clave vive en ningún lado.
"""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import string
import sys
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# Mismo alfabeto y misma longitud que `django.core.management.utils.get_random_secret_key()`
# (50 caracteres de este set). Replicarlo a mano, sin importar Django, es lo que
# permite que el script siga siendo independiente y que la clave generada pase
# `security.W009` (>=50 caracteres, >=5 distintos, sin prefijo `django-insecure-`).
KEY_ALPHABET = string.ascii_lowercase + string.digits + '!@#$%^&*(-_=+)'
KEY_LENGTH = 50

DEFAULT_ENCRYPTION_KEY_FILE = '/etc/webcmp/encryption.env'
# El canonico lleva `www.` a proposito: es la unica URL que el proveedor publica.
DEFAULT_PRODUCTION_HOSTNAME = 'www.meteocamaguey.cu'
CUSTOM_EMAIL_BACKEND = 'config.custom_email_backend.CustomSTARTTLSBackend'

# El `.env` se resuelve contra la raíz del repo, NO contra el CWD.
# `config/settings/__init__.py` lo lee en `BASE_DIR/'.env'`, un path absoluto: si
# el default fuera relativo, correr el script desde otro directorio escribiría el
# archivo donde Django no lo busca, y el fallo sería "falta SECRET_KEY" a metros
# de distancia de la causa.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = REPO_ROOT / '.env'

BANNER = """# ============================================================
# ARCHIVO DE CONFIGURACIÓN DEL PROYECTO
# Generado por scripts/generate_env.py el {timestamp}
# NO commitear: contiene material de clave.
# ============================================================"""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def generate_secret_key() -> str:
    """50 caracteres del alfabeto de Django, sin importar Django."""
    return ''.join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_LENGTH))


def www_alias(hostname: str) -> str:
    """Alias `www.` solo si el canonico no lo trae ya.

    Prefijar a ciegas cuando el canonico ya es `www.meteocamaguey.cu` produce
    `www.www.meteocamaguey.cu`: un nombre que no existe en DNS. Va al
    `ALLOWED_HOSTS` y a la emision del certificado, donde hace fallar el deploy
    con un error de validacion de dominio en vez de uno de configuracion.
    """
    return '' if hostname.startswith('www.') else f'www.{hostname}'


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode()


def load_env_file(path: Path) -> dict[str, str]:
    """Lee un `.env` como `dict`. Silencioso si no existe: un `.env` ausente es el caso normal."""
    data: dict[str, str] = {}
    try:
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def write_private_file(path: Path, content: str) -> None:
    """Escribe y deja el archivo en 600.

    El `.env` y el archivo de clave son material de clave: leerlos no debería
    requerir nada más que ser el dueño. En Windows `chmod` no hace nada útil, y
    por eso el fallo se ignora solo ahí en vez de romper el deploy.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        if os.name != 'nt':
            raise


def decrypts(encrypted: str, encryption_key: str) -> bool:
    """¿El par cifrado/descifrado realmente funciona? Sin esto, una clave a medio
    rotar se arrastra en silencio y el primer síntoma es un 500 en producción."""
    try:
        Fernet(encryption_key.encode()).decrypt(encrypted.encode())
    except InvalidToken, ValueError, TypeError:
        return False
    return True


# ---------------------------------------------------------------------------
# Secciones del .env
# ---------------------------------------------------------------------------
def section(title: str) -> str:
    return f'\n# =====================\n# {title}\n# =====================\n'


def build_env(*, production: bool, encrypted_secret_key: str, encryption_key: str | None) -> str:
    """Arma el contenido completo del `.env`.

    `encryption_key` es None en producción a propósito: la clave de descifrado no
    se escribe en el `.env` (ver docstring del módulo).
    """
    hostname = DEFAULT_PRODUCTION_HOSTNAME if production else 'localhost'
    # Una sola fuente para hosts y origins: el canonico y su alias tienen que
    # decir lo mismo en ALLOWED_HOSTS, CSRF y CORS. Si difieren, una capa acepta
    # un nombre que las otras rechazan y el sintoma es un 400 en el login.
    alias = www_alias(hostname) if production else ''
    production_hosts = ','.join(h for h in (hostname, alias) if h)
    production_origins = ','.join(f'https://{h}' for h in (hostname, alias) if h)
    parts = [BANNER.format(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))]

    parts.append(section('CONFIGURACIÓN BÁSICA (REQUERIDA)'))
    parts.append(f'DEBUG={"False" if production else "True"}\n')
    parts.append(f'SECRET_KEY={encrypted_secret_key}\n')
    if encryption_key:
        parts.append(f'ENCRYPTION_KEY={encryption_key}\n')
    else:
        parts.append(
            '# ENCRYPTION_KEY NO va aquí a propósito: está en el archivo de clave que carga\n'
            '# systemd (ver --encryption-key-file). Una clave de descifrado junto al texto\n'
            '# cifrado no cifra nada.\n'
        )
    parts.append('\n')

    parts.append(section('CONFIGURACIÓN DE DOMINIO'))
    if production:
        parts.append(f'EXTERNAL_HOSTNAME={hostname}\n')
        parts.append(f'ALLOWED_HOSTS={production_hosts}\n')
        parts.append(f'CSRF_TRUSTED_ORIGINS={production_origins}\n')
        # La URL pública del sitio: es la que viajan los enlaces de los correos.
        parts.append(f'SITE_URL=https://{hostname}/\n\n')
    else:
        parts.append('ALLOWED_HOSTS=localhost,127.0.0.1\n')
        parts.append('CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000\n')
        parts.append('SITE_URL=http://localhost:8000/\n\n')

    parts.append(section('CONFIGURACIÓN DE EMAIL'))
    if production:
        parts.append(
            '# CHANGE_ME: producción no acepta el backend de consola (assert en production.py).\n'
        )
        parts.append(f'EMAIL_BACKEND={CUSTOM_EMAIL_BACKEND}\n')
        parts.append('EMAIL_USE_TLS=True\nEMAIL_HOST=CHANGE_ME\nEMAIL_PORT=587\n')
        parts.append('EMAIL_HOST_USER=CHANGE_ME\nEMAIL_HOST_PASSWORD=CHANGE_ME\n')
        parts.append("DEFAULT_FROM_EMAIL='Centro Meteorológico Camagüey <CHANGE_ME>'\n")
        parts.append('EMAIL_USE_SSL=False\n\n')
    else:
        parts.append('# Consola: en desarrollo el correo se imprime, no se envía.\n')
        parts.append('EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend\n\n')

    parts.append(section('BASE DE DATOS'))
    if production:
        parts.append(
            '# CHANGE_ME: credenciales reales de PostgreSQL. El driver (psycopg[binary]) está\n'
            '# en requirements/base.txt.\n'
        )
        parts.append('DB_ENGINE=postgresql\n')
        parts.append('DB_NAME=webcmp\n')
        parts.append('DB_USER=webcmp\n')
        parts.append('DB_PASS=CHANGE_ME\n')
        parts.append('DB_HOST=localhost\n')
        parts.append('DB_PORT=5432\n')
        parts.append('DB_SSL_MODE=prefer\n\n')
    else:
        parts.append(
            '# PostgreSQL local. El rol necesita CREATEDB porque la suite crea su propia base\n'
            '# de tests. Los pasos para crearlo están en docs/1-instalacion.md.\n'
        )
        parts.append('DB_ENGINE=postgresql\n')
        parts.append('DB_NAME=webcmp_dev\n')
        parts.append('DB_USER=webcmp_dev\n')
        parts.append('DB_PASS=CAMBIAR_ESTA_CLAVE\n')
        parts.append('DB_HOST=127.0.0.1\n')
        parts.append('DB_PORT=5432\n')
        parts.append('DB_SSL_MODE=prefer\n\n')

    parts.append(section('CACHÉ (REDIS)'))
    parts.append('REDIS_URL=redis://127.0.0.1:6379/1\n')
    parts.append(f'USE_REDIS_CACHE={"True" if production else "False"}\n\n')

    parts.append(section('LOGGING'))
    parts.append('LOG_LEVEL=INFO\n')
    parts.append('LOG_FILE=logs/app.log\n\n')

    parts.append(section('CORS'))
    if production:
        parts.append(f'CORS_ALLOWED_ORIGINS={production_origins}\n\n')
    else:
        parts.append('CORS_ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000\n\n')

    parts.append(section('OBSERVACIONES (SYNOP)'))
    if production:
        parts.append(
            '# CHANGE_ME si el deployment usa FTP de observaciones; si no, comentá el bloque.\n'
        )
    parts.append('FTP_OBS_HOST=\nFTP_OBS_USER=\nFTP_OBS_PASS=\nFTP_OBS_PORT=990\n\n')

    parts.append(section('LDAP (OPCIONAL)'))
    parts.append('# Desactivado: comentá y completá estas líneas para activarlo.\n')
    parts.append(
        '# LDAP_SERVER_URI=ldap://dc.example.cu:389\n'
        '# LDAP_START_TLS=True\n'
        '# LDAP_BIND_DN=cn=admin,dc=example,dc=cu\n'
        '# LDAP_BIND_PASSWORD=\n'
        '# LDAP_USER_SEARCH_BASE=ou=users,dc=example,dc=cu\n'
        '# LDAP_GROUP_SEARCH_BASE=ou=groups,dc=example,dc=cu\n'
        '# LDAP_STAFF_GROUP=cn=staff,ou=groups,dc=example,dc=cu\n'
        '# LDAP_SUPERUSER_GROUP=cn=superuser,ou=groups,dc=example,dc=cu\n'
        '# LDAP_USER_ATTR_MAP=first_name:givenName,last_name:sn,email:mail\n'
        '# LDAP_CACHE_TIMEOUT=3600\n\n'
    )

    return ''.join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='generate_env.py',
        description='Genera el .env del proyecto. No interactivo: el modo se declara explícito.',
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        '--development', action='store_true', help='Perfil de desarrollo (PostgreSQL local)'
    )
    mode.add_argument('--production', action='store_true', help='Perfil de producción (PostgreSQL)')
    parser.add_argument(
        '--rotate-keys',
        action='store_true',
        help='Regenera SECRET_KEY y ENCRYPTION_KEY. Invalida sesiones y cookies firmadas.',
    )
    parser.add_argument(
        '--env-file',
        default=str(DEFAULT_ENV_PATH),
        help=f'Ruta del .env a escribir (default: {DEFAULT_ENV_PATH})',
    )
    parser.add_argument(
        '--encryption-key-file',
        default=DEFAULT_ENCRYPTION_KEY_FILE,
        help=(
            f'Archivo de la ENCRYPTION_KEY en producción (default: {DEFAULT_ENCRYPTION_KEY_FILE})'
        ),
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Sobrescribe un .env existente (sin esto, se niega)',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    production = args.production
    env_path = Path(args.env_file).expanduser().resolve()
    existing = load_env_file(env_path)

    if env_path.exists() and not args.force and not args.rotate_keys:
        print(
            f'ERROR: {env_path} ya existe. Sin --force el script no lo pisa: un .env afinado a '
            f'mano no se regenera por accidente.\n'
            f'       Para regenerarlo: --force. Para rotar solo las claves: --rotate-keys.',
            file=sys.stderr,
        )
        return 1

    # --- Claves: reusar lo que ya funciona, generar lo que no -----------------
    encrypted_secret_key = existing.get('SECRET_KEY', '')
    encryption_key = existing.get('ENCRYPTION_KEY', '')
    if production and encryption_key:
        # En producción la clave puede estar en el archivo externo, no en el .env.
        encryption_key = load_env_file(Path(args.encryption_key_file)).get('ENCRYPTION_KEY', '')

    if not args.rotate_keys and encrypted_secret_key and encryption_key:
        if decrypts(encrypted_secret_key, encryption_key):
            print('• Se conservan SECRET_KEY/ENCRYPTION_KEY existentes (no se invalidan sesiones).')
        else:
            print(
                '• AVISO: el par de claves existente NO descifra. Se generan claves nuevas.',
                file=sys.stderr,
            )
            encrypted_secret_key, encryption_key = '', ''
    elif args.rotate_keys:
        print(
            '• Rotando SECRET_KEY/ENCRYPTION_KEY: las sesiones y cookies firmadas quedan '
            'invalidadas (esperado al rotar).'
        )
        encrypted_secret_key, encryption_key = '', ''

    if not encryption_key:
        encryption_key = generate_encryption_key()
    if not encrypted_secret_key:
        encrypted_secret_key = (
            Fernet(encryption_key.encode()).encrypt(generate_secret_key().encode()).decode()
        )

    # --- Producción: la clave de descifrado va a su propio archivo -------------
    key_file: Path | None = None
    if production:
        key_file = Path(args.encryption_key_file).expanduser()
        try:
            write_private_file(
                key_file,
                f'# ENCRYPTION_KEY del proyecto. chmod 600, propiedad de root.\n'
                f'# Lo carga el unit de systemd con EnvironmentFile=-.\n'
                f'ENCRYPTION_KEY={encryption_key}\n',
            )
        except OSError as exc:
            print(
                f'ERROR: no se pudo escribir {key_file}: {exc}\n'
                f'       El .env NO se tocó. Sin ese archivo, producción no tiene con qué '
                f'descifrar SECRET_KEY.\n'
                f'       Creá el directorio y volvé a intentarlo (como root si hace falta).',
                file=sys.stderr,
            )
            return 1

    # --- El .env -------------------------------------------------------------
    write_private_file(
        env_path,
        build_env(
            production=production,
            encrypted_secret_key=encrypted_secret_key,
            encryption_key=encryption_key if not production else None,
        ),
    )

    print(f'\n✓ {env_path} escrito (600).')
    if key_file:
        print(f'✓ {key_file} escrito (600): contiene ENCRYPTION_KEY y NO está en el repositorio.')
        print(f'  Cargalo con:  systemctl edit webcmp  →  EnvironmentFile=-{key_file}')

    if env_path != DEFAULT_ENV_PATH.resolve():
        # `--env-file` existe para los tests y para el job de CI, que necesita el
        # par en el entorno y no un archivo. En esos casos nadie espera que Django
        # lo lea. Pero si alguien lo cambia esperando que los settings lo encuentren,
        # no lo van a encontrar: config/settings/__init__.py lee BASE_DIR/'.env', fijo.
        print(
            f'\n  ATENCION: {env_path} NO es el .env del proyecto ({DEFAULT_ENV_PATH}).\n'
            "  Los settings lo leen en BASE_DIR/'.env', fijo: Django no va a encontrar\n"
            '  este archivo y va a fallar cerrado por falta de SECRET_KEY. Para que lo use,\n'
            '  dejalo en la raiz del repo o cargalo vos en el entorno.'
        )

    print(
        '\n  Editá los valores CHANGE_ME antes de arrancar producción:\n'
        '  - EMAIL_HOST / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD\n'
        '  - DB_PASS (y DB_* si el PostgreSQL no es local)'
        if production
        else '\n  Listo. `python manage.py runserver` ya debería arrancar.'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
