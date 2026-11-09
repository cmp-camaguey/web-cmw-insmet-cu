"""Regresión: el TaskRegistry de Huey tiene que estar poblado en el consumer.

El síntoma de este bug era invisible desde Django: la web encolaba facturas nomás,
`manage.py check` pasaba, los tests de vistas pasaban, y el worker arrancaba
"vivo" para no procesar una sola tarea. En el log, repetido:

    HueyException: apps.core.tasks.generate_invoice_pdf_and_email_task not found
    in TaskRegistry

Dos mitades, y el test tiene que cubrir las dos:

1. `huey_consumer` no llama a `django.setup()`; sin eso los decoradores
   `@huey.task()` nunca corren. Se resuelve con `config/huey_consumer_entry.py`.
2. `django.setup()` tampoco basta: `CoreConfig.ready()` tiene que importar
   `apps/core/tasks.py` —y `MeteoConfig.ready()` el suyo—, porque el proceso del
   consumer no importa vistas y nada más lo haría.

Por eso la aserción va en un SUBPROCESO. Dentro del proceso de test el registro
siempre queda poblado —el runner importa módulos que a su vez importan
`apps/core/tasks.py`—, así que un test in-process pasa con el bug presente y no
prueba nada. El subproceso es lo único que reproduce de verdad las condiciones
del consumer: un intérprete limpio que solo hace `django.setup()`.
"""

import subprocess
import sys
import textwrap

from django.conf import settings
from django.test import SimpleTestCase

from config.huey import huey

EXPECTED_TASKS = {
    'apps.core.tasks.generate_invoice_pdf_and_email_task',
    'apps.core.tasks.send_email_task',
    # 026: recordatorio diario de avisos por vencer. Se registra por
    # `MeteoConfig.ready()`, así que también tiene que estar en el consumer.
    'apps.meteo.tasks.notificar_avisos_por_vencer',
    # 026 fase 4: poda diaria de avisos. Se registra por `CoreConfig.ready()`,
    # que ya importa `apps/core/tasks.py`.
    'apps.core.tasks.podar_notificaciones',
    # 030: recordatorio único y limpieza de las altas sin confirmar.
    'apps.core.tasks.mantener_suscriptores_pendientes',
    # 026 (huecos): recordatorio de facturas pendientes. Se registra por
    # `CommercialConfig.ready()`.
    'apps.commercial.tasks.recordar_facturas_pendientes',
    # 030: contratos que vencen dentro del horizonte de aviso.
    'apps.commercial.tasks.avisar_contratos_por_vencer',
}

# Lo que hace `huey_consumer config.huey_consumer_entry.huey`, reducido a lo que
# importa para el registro: un intérprete nuevo, sin imports previos.
CONSUMER_SETUP = textwrap.dedent(
    """
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from config.huey import huey
    print(','.join(sorted(huey._registry._registry)))
    """
)


class ConsumerRegistryTests(SimpleTestCase):
    def test_setup_registra_las_tareas_en_un_proceso_limpio(self):
        """El contrato que el consumer necesita para no girar en vacío.

        Falla si `CoreConfig.ready()` deja de importar `apps/core/tasks.py`, o si
        `config.huey` deja de usar la misma instancia que los decoradores.
        """
        resultado = subprocess.run(
            [sys.executable, '-c', CONSUMER_SETUP],
            capture_output=True,
            text=True,
            cwd=settings.BASE_DIR,
            timeout=120,
        )

        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        registradas = set(resultado.stdout.strip().split(',')) - {''}
        self.assertEqual(
            registradas,
            EXPECTED_TASKS,
            'El consumer arranca sin tareas registradas: cada dequeue va a fallar '
            'con HueyException y el worker no va a procesar nada.',
        )


class ConsumerEntryModuleTests(SimpleTestCase):
    def test_el_modulo_del_consumer_expone_la_instancia(self):
        """`huey_consumer` resuelve `<módulo>.<atributo>`: si el atributo falta,
        el worker muere al arrancar, que es un síntoma mucho más visible."""
        from config import huey_consumer_entry

        self.assertIs(huey_consumer_entry.huey, huey)
