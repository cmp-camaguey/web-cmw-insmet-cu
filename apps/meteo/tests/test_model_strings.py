"""Formato de los `__str__` que alimentan el registro de actividad.

El registro guarda `str(obj)` como `object_repr`: si el modelo devuelve el
`repr` de un `datetime`, el panel muestra
`Alerta Temprana - 2026-11-09 13:43:04.827701+00:00`. La fecha va en la zona
horaria del portal y en el mismo formato que el resto de la interfaz.
"""

from datetime import UTC, date, datetime

from django.test import SimpleTestCase

from apps.meteo.models import ForecastExtendedDay, Warning, WeatherReport


class ModelStringTests(SimpleTestCase):
    def test_un_aviso_muestra_la_fecha_local_y_no_la_iso(self):
        # 02:00 UTC del 9 son las 22:00 del 8 en La Habana: cambia hasta el día.
        warning = Warning(
            warning_type='early',
            date=datetime(2026, 11, 9, 2, 0, tzinfo=UTC),
        )

        self.assertEqual(str(warning), 'Alerta Temprana - 08/11/2026 22:00')

    def test_un_reporte_muestra_la_fecha_local(self):
        report = WeatherReport(
            report_type='today',
            date=datetime(2026, 11, 9, 13, 43, 4, tzinfo=UTC),
        )

        self.assertEqual(str(report), 'Hoy - 09/11/2026 09:43')

    def test_un_dia_extendido_muestra_el_dia_en_formato_local(self):
        day = ForecastExtendedDay(day_number=2, date=date(2026, 11, 9))

        self.assertEqual(str(day), 'Día 2 - 09/11/2026')

    def test_sin_fecha_no_revienta(self):
        self.assertEqual(str(Warning(warning_type='early')), 'Alerta Temprana - sin fecha')
