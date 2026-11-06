"""Contrato de los booleanos entre `deploy/install.sh` y los settings.

Las settings leen estos valores con `os.getenv('<KEY>', ...) == 'True'`. Cualquier
otra palabra (`si`, `no`, `yes`, `1`) es False, y False no avisa: el instalador
pregunta en español y escribe el valor que Django compara, así que la distancia
entre las dos palabras se pierde en silencio.

Ya pasó una vez. `EmailTlsTests` documenta que el 465 se agregó porque
`EMAIL_USE_SSL` no llegaba a Django, pero el instalador seguía escribiendo `si`:
la variable llegaba, la comparación daba False, y el síntoma era una conexión en
claro contra un puerto que espera el handshake TLS en el primer byte.

Estos tests no ejecutan el instalador —necesita root y TTY— sino que fijan la
mitad que se puede romper sin que nadie lo note: qué literal escribe y contra qué
literal compara el otro lado.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SCRIPT = REPO_ROOT / 'deploy' / 'install.sh'
BASE_SETTINGS = REPO_ROOT / 'config' / 'settings' / 'base.py'

# Variables que `install.sh` escribe al `.env` y que `config/settings/base.py`
# compara con `== 'True'`.
BOOLEAN_KEYS = ('EMAIL_USE_TLS', 'EMAIL_USE_SSL', 'USE_REDIS_CACHE')


class InstallScriptBooleanTests(unittest.TestCase):
    def setUp(self):
        self.script = INSTALL_SCRIPT.read_text(encoding='utf-8')

    def test_boolean_values_are_written_as_true_or_false(self):
        """Ningún literal `si`/`no` puede llegar al `.env` de estas claves."""
        for key in BOOLEAN_KEYS:
            with self.subTest(key=key):
                values = re.findall(rf'\b{key}=([A-Za-z0-9_]+)', self.script)
                self.assertTrue(values, f'{key} no se asigna en install.sh')
                for value in values:
                    self.assertIn(
                        value,
                        ('True', 'False'),
                        f'{key}={value} en install.sh: los settings comparan con '
                        f"'True', y {value!r} es False sin error.",
                    )

    def test_settings_still_compare_against_true(self):
        """Si este test falla, el anterior dejó de proteger nada."""
        base = BASE_SETTINGS.read_text(encoding='utf-8')
        for key in BOOLEAN_KEYS:
            with self.subTest(key=key):
                lines = [
                    line
                    for line in base.splitlines()
                    if f"os.getenv('{key}'" in line and not line.lstrip().startswith('#')
                ]
                self.assertTrue(lines, f'base.py ya no lee {key}')
                self.assertTrue(
                    any("== 'True'" in line for line in lines),
                    f"base.py ya no compara {key} con 'True'",
                )
