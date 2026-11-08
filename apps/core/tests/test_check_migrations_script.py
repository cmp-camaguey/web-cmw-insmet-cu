"""Tests del gate de migraciones (`scripts/check_migrations.py`).

El gate es lo unico que queda entre un `RemoveField` y la base de produccion: el
deploy lo dispara el boton "Actualizar" del panel, sin nadie mirando, y el
rollback revierte el codigo, no el esquema. Por eso se prueba como proceso
(subprocess) contra repos git de verdad: lo que importa es el codigo de salida
que ve GitHub Actions y el texto que lee quien tiene el merge bloqueado.

Cada test arma su propio repo en un directorio temporal. No se toca el repo del
proyecto: un test que commitea en el checkout del que lo corre es un test que
rompe el trabajo de alguien.
"""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / 'scripts' / 'check_migrations.py'

MIGRACION_INICIAL = """
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='Municipio',
            fields=[('id', models.AutoField(primary_key=True))],
        ),
    ]
"""


def migracion(operaciones: str, comentario: str = '') -> str:
    """Una migracion nueva con las operaciones dadas, ya indentadas."""
    cuerpo = textwrap.indent(textwrap.dedent(operaciones).strip('\n'), ' ' * 8)
    return (
        'from django.db import migrations\n'
        '\n'
        '\n'
        'class Migration(migrations.Migration):\n'
        '    dependencies = []\n'
        '    operations = [\n'
        f'{comentario}'
        f'{cuerpo}\n'
        '    ]\n'
    )


class Repo:
    """Repo git temporal con la forma minima de un proyecto Django."""

    def __init__(self, root: Path):
        self.root = root
        self.git('init', '-q')
        # `git init` sin `-b main` sigue dependiendo de init.defaultBranch, que es
        # configuracion de la maquina. El gate compara contra `main`, asi que el
        # nombre de la rama no puede quedar a merced de eso.
        self.git('symbolic-ref', 'HEAD', 'refs/heads/main')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'commit.gpgsign', 'false')

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ['git', '-C', str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def escribir(self, relpath: str, contenido: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contenido, encoding='utf-8')
        return path

    def commit(self, mensaje: str = 'cambio') -> None:
        self.git('add', '-A')
        self.git('commit', '-q', '-m', mensaje)

    def rama(self, nombre: str) -> None:
        self.git('checkout', '-q', '-b', nombre, 'main')


class CheckMigrationsTests(unittest.TestCase):
    """Un `main` con una migracion publicada y una rama por caso."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Repo(Path(self._tmp.name))

        self.repo.escribir('apps/meteo/migrations/__init__.py', '')
        self.repo.escribir('apps/meteo/migrations/0001_initial.py', MIGRACION_INICIAL)
        self.repo.commit('base')

    def gate(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), '--repo-root', str(self.repo.root), *args],
            capture_output=True,
            text=True,
        )

    def gate_en_rama(self, nombre: str = 'feat') -> subprocess.CompletedProcess:
        return self.gate('--base', 'main', '--head', nombre)

    # --- Destructivas -------------------------------------------------------

    def test_removefield_bloquea(self):
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_quita.py',
            migracion("migrations.RemoveField(model_name='Municipio', name='poblacion'),"),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('BLOQUEA  RemoveField', resultado.stdout)
        # El mensaje tiene que decir QUE se borra: un "RemoveField" pelado no le
        # dice nada a quien revisa el merge.
        self.assertIn('Municipio.poblacion', resultado.stdout)

    def test_deletemodel_bloquea(self):
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_quita.py',
            migracion("migrations.DeleteModel(name='Municipio'),"),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('borra la tabla', resultado.stdout)

    def test_runsql_bloquea(self):
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_sql.py',
            migracion("migrations.RunSQL('DELETE FROM meteo_municipio'),"),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('SQL arbitrario', resultado.stdout)

    def test_operacion_desconocida_bloquea(self):
        """El gate falla cerrado: no saber que hace una operacion no es saber que
        no hace dano."""
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_rara.py',
            migracion('migrations.TruncarTabla(name="Municipio"),'),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('no reconocida por el gate', resultado.stdout)

    def test_sintaxis_rota_bloquea(self):
        self.repo.rama('feat')
        self.repo.escribir('apps/meteo/migrations/0002_rota.py', 'class Migration(\n')
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('no se pudo parsear', resultado.stdout)

    # --- El escape auditado -------------------------------------------------

    def test_escape_con_motivo_deja_pasar(self):
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_quita.py',
            migracion(
                "migrations.RemoveField(model_name='Municipio', name='poblacion'),",
                comentario=' ' * 8 + '# data-safety: reviewed: columna migrada y verificada\n',
            ),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 0, resultado.stdout)
        self.assertIn('ESCAPE', resultado.stdout)
        self.assertIn('columna migrada y verificada', resultado.stdout)

    def test_escape_sin_motivo_no_cuenta(self):
        """Un marcador vacio es una tecla que se aprieta sola: sigue bloqueando."""
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_quita.py',
            migracion(
                "migrations.RemoveField(model_name='Municipio', name='poblacion'),",
                comentario=' ' * 8 + '# data-safety: reviewed:\n',
            ),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('esta vacio', resultado.stdout)

    # --- Historia ya publicada ---------------------------------------------

    def test_modificar_migracion_publicada_bloquea(self):
        self.repo.rama('feat')
        path = self.repo.root / 'apps/meteo/migrations/0001_initial.py'
        path.write_text(path.read_text(encoding='utf-8') + '\n# retoque\n', encoding='utf-8')
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('no se edita ni se borra', resultado.stdout)

    def test_borrar_migracion_publicada_bloquea(self):
        self.repo.rama('feat')
        (self.repo.root / 'apps/meteo/migrations/0001_initial.py').unlink()
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('borrada', resultado.stdout)

    def test_renombrar_migracion_publicada_bloquea(self):
        self.repo.rama('feat')
        self.repo.git(
            'mv',
            'apps/meteo/migrations/0001_initial.py',
            'apps/meteo/migrations/0001_base.py',
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 1, resultado.stdout)
        self.assertIn('no se renombra', resultado.stdout)

    # --- Lo que NO bloquea --------------------------------------------------

    def test_advertencia_no_bloquea_el_merge(self):
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_agrega.py',
            migracion(
                'migrations.AddField(\n'
                "    model_name='Municipio',\n"
                "    name='codigo',\n"
                '    field=models.CharField(null=True),\n'
                '),',
            ).replace(
                'from django.db import migrations\n', 'from django.db import migrations, models\n'
            ),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 0, resultado.stdout)
        self.assertIn('AVISO', resultado.stdout)

    def test_addfield_not_null_sin_default_avisa(self):
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_agrega.py',
            migracion(
                'migrations.AddField(\n'
                "    model_name='Municipio',\n"
                "    name='codigo',\n"
                '    field=models.CharField(),\n'
                '),',
            ).replace(
                'from django.db import migrations\n', 'from django.db import migrations, models\n'
            ),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 0, resultado.stdout)
        self.assertIn('NOT NULL sin default', resultado.stdout)

    def test_createmodel_no_bloquea(self):
        self.repo.rama('feat')
        self.repo.escribir(
            'apps/meteo/migrations/0002_nueva.py',
            migracion(
                "migrations.CreateModel(\n    name='Estacion',\n    fields=[],\n),",
            ),
        )
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 0, resultado.stdout)

    def test_sin_migraciones_en_el_pr_pasa(self):
        self.repo.rama('feat')
        self.repo.escribir('apps/meteo/models.py', 'X = 1\n')
        self.repo.commit()

        resultado = self.gate_en_rama()

        self.assertEqual(resultado.returncode, 0, resultado.stdout)
        self.assertIn('no toca migraciones', resultado.stdout)

    # --- Fallas cerradas ----------------------------------------------------

    def test_base_inexistente_sale_2(self):
        """Un gate que no puede comparar no puede aprobar."""
        resultado = self.gate('--base', 'no-existe', '--head', 'main')

        self.assertEqual(resultado.returncode, 2, resultado.stdout)
        self.assertIn('no-existe', resultado.stderr)

    def test_script_no_importa_django(self):
        """Corre antes de instalar dependencias y sin venv.

        Si importara Django, necesitaria settings y una base para decidir algo
        que se decide leyendo archivos.
        """
        source = SCRIPT.read_text(encoding='utf-8')
        for prohibido in ('import django', 'from django', 'from config', 'import config'):
            self.assertNotIn(prohibido, source)
