"""Formato de los `__str__` que alimentan el registro de actividad.

El registro guarda `str(obj)` como `object_repr`: si el modelo devuelve el
`repr` de un `datetime`, el panel muestra
`Alerta Temprana - 2026-11-09 13:43:04.827701+00:00`. La fecha va en la zona
horaria del portal y en el mismo formato que el resto de la interfaz.
"""

from datetime import UTC, date, datetime, timedelta
from datetime import timezone as dt_timezone

from django.test import SimpleTestCase
from django.utils import timezone

from apps.meteo.models import ForecastExtendedDay, Warning, WeatherReport

# Zona fija UTC-5, sin horario de verano. La zona del portal es America/Havana y
# su regla de verano depende de la versión de tzdata instalada; si el test fija
# el huso de Cuba, cambia de resultado entre el runner de CI y un equipo de
# desarrollo. Lo que el modelo promete es convertir a la zona activa y dar
# formato legible, no un valor concreto de UTC-4 ni de UTC-5.
ZONA_FIJA = dt_timezone(timedelta(hours=-5))


class ModelStringTests(SimpleTestCase):
    def test_un_aviso_muestra_la_fecha_local_y_no_la_iso(self):
        # 02:00 UTC del 9 son las 21:00 del 8 en UTC-5: cambia hasta el día.
        warning = Warning(
            warning_type='early',
            date=datetime(2026, 11, 9, 2, 0, tzinfo=UTC),
        )

        with timezone.override(ZONA_FIJA):
            self.assertEqual(str(warning), 'Alerta Temprana - 08/11/2026 21:00')

    def test_un_reporte_muestra_la_fecha_local(self):
        report = WeatherReport(
            report_type='today',
            date=datetime(2026, 11, 9, 13, 43, 4, tzinfo=UTC),
        )

        with timezone.override(ZONA_FIJA):
            self.assertEqual(str(report), 'Hoy - 09/11/2026 08:43')

    def test_un_dia_extendido_muestra_el_dia_en_formato_local(self):
        day = ForecastExtendedDay(day_number=2, date=date(2026, 11, 9))

        self.assertEqual(str(day), 'Día 2 - 09/11/2026')

    def test_sin_fecha_no_revienta(self):
        self.assertEqual(str(Warning(warning_type='early')), 'Alerta Temprana - sin fecha')
