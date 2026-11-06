"""Tests del generador de `.env` como script plano (`scripts/generate_env.py`).

El generador es un script y no un management command a propósito: existe para
correr sin settings, y probarlo como comando de Django probaría la mitad muerta
del contrato.

Cada test ejercita el script como proceso (subprocess), no sus funciones internas:
lo que importa es lo que un operador obtiene de la terminal.
"""

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from config.settings.base import decrypt_secret_key

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / 'scripts' / 'generate_env.py'


def run_script(*args, cwd=None):
    """Ejecuta el script como lo haría un operador y devuelve el CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


def read_env(path):
    """Lee un `.env` como dict, igual que lo que hace el chequeo de settings."""
    data = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        data[key.strip()] = value.strip()
    return data


class GenerateEnvScriptTests(unittest.TestCase):
    """Cada test usa su propio directorio temporal: el script es stateful
    (reusa claves, se niega a sobrescribir) y tests que se pisan entre sí
    mienten sobre las dos cosas que más importan."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env_file = self.tmp / '.env'
        self.key_file = self.tmp / 'encryption.env'
        self.addCleanup(self._tmp.cleanup)

    def _args(self, *extra):
        return (
            '--env-file',
            str(self.env_file),
            '--encryption-key-file',
            str(self.key_file),
            *extra,
        )

    def assert_private(self, path):
        """El `.env` y el archivo de clave son material de clave: 600 o nada."""
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(
            mode,
            stat.S_IRUSR | stat.S_IWUSR,
            f'{path} quedó en {mode:o} y debería estar en 600',
        )

    # --- El contrato de existir ---------------------------------------------
    def test_script_does_not_import_django(self):
        """El script NO puede importar Django.

        Es la razón de que el script exista: si importara Django, necesitaría los
        settings para generar la clave, y entonces no podría correr en el estado
        en que la aplicación todavía no tiene clave.
        """
        source = SCRIPT.read_text(encoding='utf-8')
        for forbidden in ('import django', 'from django', 'from config', 'import config'):
            self.assertNotIn(
                forbidden,
                source,
                f'generate_env.py no puede contener {forbidden!r}: debe correr sin Django',
            )

    def test_script_runs_without_django_in_sys_modules(self):
        """Prueba de humo del desacople: correr el script y comprobar que el
        proceso terminó sin haberse importado Django.

        El test anterior mira el texto del archivo; este mira lo que de verdad
        pasa. Los dos se necesitan: el primero no se puede engañar con un
        import dinámico, el segundo no se puede engañar con un import diferido.
        """
        runner = self.tmp / 'runner.py'
        runner.write_text(
            textwrap.dedent(
                f"""
                import runpy, sys
                sys.argv = {([str(SCRIPT), *self._args('--development')])!r}
                try:
                    runpy.run_path(sys.argv[0], run_name='__main__')
                except SystemExit as exc:
                    code = exc.code or 0
                else:
                    code = 0
                print('DJANGO_IMPORTED', 'django' in sys.modules)
                sys.exit(code)
                """
            ),
            encoding='utf-8',
        )
        result = subprocess.run(
            [sys.executable, str(runner)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DJANGO_IMPORTED False', result.stdout)

    # --- Desarrollo ----------------------------------------------------------
    def test_development_writes_decryptable_pair_and_is_private(self):
        result = run_script(*self._args('--development'))
        self.assertEqual(result.returncode, 0, result.stderr)

        data = read_env(self.env_file)
        self.assertEqual(data['DEBUG'], 'True')
        # Desarrollo apunta a PostgreSQL local, no a un archivo SQLite.
        self.assertEqual(data['DB_ENGINE'], 'postgresql')
        self.assertEqual(data['DB_NAME'], 'webcmp_dev')
        self.assertEqual(data['DB_USER'], 'webcmp_dev')
        self.assertEqual(data['DB_HOST'], '127.0.0.1')
        self.assertFalse(self.key_file.exists(), 'en desarrollo la clave va en el .env, no aparte')

        # El par que wrote el script es el que el proyecto sabe descifrar.
        plain = decrypt_secret_key(data['SECRET_KEY'], data['ENCRYPTION_KEY'])
        self.assertGreaterEqual(len(plain), 50, 'debe cumplir el mínimo de security.W009')
        self.assertGreaterEqual(len(set(plain)), 5, 'debe cumplir el mínimo de security.W009')
        self.assert_private(self.env_file)

    def test_development_console_email_backend(self):
        run_script(*self._args('--development'))
        self.assertEqual(
            read_env(self.env_file)['EMAIL_BACKEND'],
            'django.core.mail.backends.console.EmailBackend',
        )

    # --- Producción ----------------------------------------------------------
    def test_production_keeps_encryption_key_out_of_the_env_file(self):
        """La clave de descifrado vive aparte.

        Si compartieran archivo, un `.env` filtrado entregaría el secreto ya
        descifrado: la clave de descifrado viaja con el texto cifrado, así que no
        cifra nada.
        """
        result = run_script(*self._args('--production'))
        self.assertEqual(result.returncode, 0, result.stderr)

        env_text = self.env_file.read_text(encoding='utf-8')
        self.assertNotIn(
            'ENCRYPTION_KEY=', env_text, 'producción no escribe ENCRYPTION_KEY en el .env'
        )
        self.assertIn('DB_ENGINE=postgresql', env_text)
        self.assertIn('DEBUG=False', env_text)

        key_data = read_env(self.key_file)
        self.assertTrue(key_data['ENCRYPTION_KEY'], 'la clave tiene que existir en algun lado')
        # Y el .env sí descifra con la clave del archivo aparte (EnvironmentFile de systemd):
        # es exactamente el camino que va a recorrer el servicio en producción.
        plain = decrypt_secret_key(
            read_env(self.env_file)['SECRET_KEY'], key_data['ENCRYPTION_KEY']
        )
        self.assertGreaterEqual(len(plain), 50, 'el par tiene que descifrar de verdad')
        self.assertGreaterEqual(len(set(plain)), 5)
        self.assert_private(self.key_file)

    def test_production_refuses_to_write_env_when_key_file_is_unwritable(self):
        """Fail-closed del bootstrap: sin destino para la clave, no hay `.env`.

        Un `.env` cuya ENCRYPTION_KEY no existe en ningún lado no arranca y no
        dice por qué. Mejor no escribirlo.
        """
        # El script crea los directorios padres, así que para simular el fallo
        # hace falta un destino que no se pueda crear: un archivo usado como
        # directorio.
        blocker = self.tmp / 'blocker'
        blocker.write_text('soy un archivo, no un directorio\n', encoding='utf-8')

        result = run_script(
            '--production',
            '--env-file',
            str(self.env_file),
            '--encryption-key-file',
            str(blocker / 'encryption.env'),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('no se pudo escribir', result.stderr)
        self.assertFalse(
            self.env_file.exists(),
            'el .env no debe quedar escrito si su clave de descifrado no tiene destino',
        )

    def test_production_never_writes_console_email_backend(self):
        run_script(*self._args('--production'))
        self.assertNotIn(
            'console.EmailBackend',
            self.env_file.read_text(encoding='utf-8'),
            'producción no puede arrancar con el backend de consola',
        )

    # --- El estado del archivo: reuse, force, rotate --------------------------
    def test_refuses_to_overwrite_existing_env_without_force(self):
        run_script(*self._args('--development'))
        again = run_script(*self._args('--development'))
        self.assertEqual(again.returncode, 1)
        self.assertIn('--force', again.stderr)

    def test_force_preserves_key_pair(self):
        """Regenerar el `.env` no debe invalidar sesiones ni cookies."""
        run_script(*self._args('--development'))
        original = read_env(self.env_file)['SECRET_KEY']

        result = run_script(*self._args('--development', '--force'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_env(self.env_file)['SECRET_KEY'], original)
        self.assertIn('conservan', result.stdout)

    def test_rotate_keys_changes_pair_and_stays_decryptable(self):
        run_script(*self._args('--development'))
        original = read_env(self.env_file)

        result = run_script(*self._args('--development', '--rotate-keys'))
        self.assertEqual(result.returncode, 0, result.stderr)
        rotated = read_env(self.env_file)

        self.assertNotEqual(rotated['SECRET_KEY'], original['SECRET_KEY'])
        self.assertNotEqual(rotated['ENCRYPTION_KEY'], original['ENCRYPTION_KEY'])
        # Y el par nuevo sigue siendo coherente: rota es rotar, no romper.
        plain = decrypt_secret_key(rotated['SECRET_KEY'], rotated['ENCRYPTION_KEY'])
        self.assertGreaterEqual(len(plain), 50)

    def test_regenerates_broken_pair_instead_of_inheriting_it(self):
        """Un `.env` con un par que no descifra (keyfile cambiado a mano, copia
        parcial, restore a medias) se regenera en vez de arrastrarse."""
        run_script(*self._args('--development'))
        tampered = read_env(self.env_file)
        tampered['ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        self.env_file.write_text(
            '\n'.join(f'{k}={v}' for k, v in tampered.items()) + '\n', encoding='utf-8'
        )

        result = run_script(*self._args('--development', '--force'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NO descifra', result.stderr)
        fixed = read_env(self.env_file)
        recovered = decrypt_secret_key(fixed['SECRET_KEY'], fixed['ENCRYPTION_KEY'])
        self.assertGreaterEqual(len(recovered), 50)

    def test_requires_an_explicit_mode(self):
        """Sin `--development` ni `--production` no adivina: preguntar es mejor que
        escribir el `.env` equivocado en el servidor."""
        result = run_script('--env-file', str(self.env_file))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.env_file.exists())

    # --- Donde aterriza el archivo -------------------------------------------
    def test_default_env_path_is_the_repo_root_not_the_cwd(self):
        """El default se resuelve contra la raíz del repo, no contra el CWD.

        `config/settings/__init__.py` lee `BASE_DIR/'.env'`, un path absoluto. Un
        default relativo al CWD escribiría el archivo donde Django no lo busca, y
        el síntoma —"falta SECRET_KEY"— no señalaría la causa.
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location('generate_env_under_test', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.REPO_ROOT.resolve(), REPO_ROOT.resolve())
        # El modo es obligatorio, pero no tiene efecto sobre el path.
        self.assertEqual(
            Path(module.parse_args(['--development']).env_file).resolve(),
            (REPO_ROOT / '.env').resolve(),
        )
        self.assertEqual(
            Path(module.parse_args(['--development']).env_file).resolve(),
            Path(module.DEFAULT_ENV_PATH).resolve(),
        )

    def test_warns_when_env_file_is_not_the_one_django_reads(self):
        """Un `--env-file` fuera de la raíz funciona, pero hay que decirlo.

        El flag existe para los tests y para el job de CI, que quiere el par en
        el entorno. Si alguien lo cambia esperando que Django lo encuentre, el
        fallo llega mucho después y sin señalar la causa.
        """
        result = run_script(*self._args('--development'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NO es el .env del proyecto', result.stdout)
        self.assertIn('BASE_DIR', result.stdout)
