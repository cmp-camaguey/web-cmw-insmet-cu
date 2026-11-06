"""Punto de entrada de la configuración: selecciona el perfil y lo reexporta.

RESTRICCIÓN DURA (no negociable): el perfil lo eligen estas DOS variables de
entorno, con esta precedencia:

    IS_PRODUCTION = 'PRODUCTION' in os.environ
    DEBUG = os.getenv('DEBUG', 'False') == 'True'

El unit de systemd (`deploy/systemd/webcmp.service`) exporta `PRODUCTION=1`
en el propio `[Service]`. Si esta selección usara otra variable, un despliegue
que no la exportara arrancaría producción con el perfil equivocado: un outage
silencioso. Por eso la tabla de verdad se preserva por construcción:

    PRODUCTION en el entorno        ->  production
    sin PRODUCTION, DEBUG == True   ->  dev
    sin PRODUCTION, DEBUG != True   ->  testing

`PRODUCTION` gana sobre `DEBUG`. `load_dotenv()` corre ACÁ, antes de leer las
banderas, así que `.env` participa en la decisión.

Por qué este módulo lee el entorno y no `base`
---------------------------------------------
`base` falla cerrado cuando no hay par de claves descifrable, así que el perfil
tiene que decidirse ANTES de importarlo: importando este paquete con las banderas
leídas de `base`, el perfil `testing` —que existe justamente para arrancar sin
`.env`— se moriría en el import sin llegar a inyectar su par.

El orden que funciona es el inverso: leer `.env` y las banderas acá, y recién
entonces importar el perfil, que importa `base` a su vez. Por eso la lectura de
`DEBUG` está duplicada literalmente de `base.DEBUG`: si alguna vez divergen, un
perfil se elige distinto según por dónde se entre. Eso también lo cubre
`DispatcherTests`.

Los perfiles también son importables por su cuenta
(`DJANGO_SETTINGS_MODULE=config.settings.production`): cada uno hace su propio
`from .base import *`.
"""

# Los settings del perfil llegan por `import *` a propósito: `config.settings` es
# un módulo de settings completo, no un selector.
# ruff: noqa: F405
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Idéntico al de `base`: `.env` se lee antes de decidir, en los dos módulos.
load_dotenv(BASE_DIR / '.env')

# La MISMA expresión que `base.DEBUG`. Ver la docstring.
_DEBUG = os.getenv('DEBUG', 'False') == 'True'

if 'PRODUCTION' in os.environ:
    from .production import *  # noqa: E402, F403
elif _DEBUG:
    from .dev import *  # noqa: E402, F403
else:
    # `testing` es el único perfil que debe arrancar sin material de clave, así que
    # su par determinista se inyecta ANTES de importar el perfil (y por lo tanto antes
    # de importar `base`). `testing.py` repite la llamada para el caso en que se lo
    # importe directo; la función es idempotente.
    from ._testing_keys import inject_testing_key_pair  # noqa: E402

    inject_testing_key_pair()

    from .testing import *  # noqa: E402, F403

# `base` ya está importado a esta altura por la cadena del perfil; se importa con
# nombre explícito para las dos referencias siguientes.
from . import base as _base  # noqa: E402
from .base import _CONTENT_SECURITY_POLICY_DIRECTIVES  # noqa: E402,F401  (no lo exporta `*`)


def get_database_config():
    """Envoltura de `base.get_database_config()`.

    Ya no decide nada: el motor sale del entorno y es obligatorio en los tres
    perfiles. Se conserva como punto de importación estable para los tests y
    para cualquier código que lo consulte.
    """
    return _base.get_database_config()


def load_tests(loader, tests, pattern):
    """Evita que `unittest` importe este paquete buscando tests dentro.

    Motivo: el descubrimiento trata todo módulo `test*.py` de un paquete como
    módulo de tests y lo IMPORTA. `config/settings/testing.py` es un perfil de
    settings, no un test: importarlo ejecuta su validación estricta de secretos
    y de base de datos, y lanza `ImproperlyConfigured` si el entorno no coincide
    con ese perfil. Con un `.env` local que pone `DEBUG=True` (perfil `dev`) y
    un bloque `DB_*` incompleto, `manage.py test` fallaba durante el
    descubrimiento con un error que nada tenía que ver con los tests.

    El protocolo `load_tests` de `unittest` hace que el descubrimiento use lo que
    se devuelve aquí y NO entre en `config/settings/`. Este paquete no contiene
    ningún test, así que se devuelve la suite vacía tal cual.
    """
    return tests
