from datetime import timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.core.templatetags.utils_filters import format_cup, local_datetime_in_text

# Zona fija UTC-5, sin horario de verano. El portal usa America/Havana, pero su
# regla de verano depende de la versión de tzdata instalada: el runner de CI y
# un equipo de desarrollo pueden resolver el mismo instante con una hora de
# diferencia. Lo que este filtro promete es convertir a la zona activa y dar
# formato legible, así que la prueba fija la zona y no el huso de Cuba.
ZONA_FIJA = dt_timezone(timedelta(hours=-5))


class FormatCupFilterTest(TestCase):
    def test_decimal_with_thousands_separator(self):
        self.assertEqual(format_cup(Decimal('1234.56')), '$1.234,56')

    def test_small_decimal_uses_comma_for_decimals(self):
        self.assertEqual(format_cup(Decimal('45.50')), '$45,50')

    def test_integer_gets_two_decimals(self):
        self.assertEqual(format_cup(1234), '$1.234,00')

    def test_float_gets_two_decimals(self):
        self.assertEqual(format_cup(45.5), '$45,50')

    def test_none_returns_zero(self):
        self.assertEqual(format_cup(None), '$0,00')

    def test_empty_string_returns_zero(self):
        self.assertEqual(format_cup(''), '$0,00')

    def test_zero_returns_zero(self):
        self.assertEqual(format_cup(0), '$0,00')

    def test_available_via_my_filters_template_tag(self):
        rendered = Template('{% load my_filters %}{{ price|format_cup }}').render(
            Context({'price': Decimal('1234.56')}),
        )
        self.assertEqual(rendered, '$1.234,56')


class LocalDatetimeInTextFilterTest(SimpleTestCase):
    def test_reescribe_una_fecha_iso_guardada_a_hora_local(self):
        with timezone.override(ZONA_FIJA):
            self.assertEqual(
                local_datetime_in_text('Ciclón Tropical - 2026-11-07 22:41:39.815152+00:00'),
                'Ciclón Tropical - 07/11/2026 17:41',
            )

    def test_deja_intacto_un_texto_sin_fecha(self):
        self.assertEqual(local_datetime_in_text('Alerta Temprana'), 'Alerta Temprana')

    def test_vacio_devuelve_texto_vacio(self):
        self.assertEqual(local_datetime_in_text(''), '')
