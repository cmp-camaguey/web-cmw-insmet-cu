"""Tests for generate_skewt_file multi-format export (008-exportar-graficos)."""

from django.test import TestCase

from apps.home.data.plot_generators import generate_skewt, generate_skewt_file


def _make_sounding_data():
    """Build a minimal valid sounding_data dict for unit tests."""
    return {
        'p': {'value': [1000, 900, 800, 700, 600, 500, 400, 300], 'unit': 'hPa'},
        'T': {'value': [30, 25, 20, 15, 10, 5, 0, -5], 'unit': 'degC'},
        'Td': {'value': [20, 15, 10, 5, 0, -5, -10, -15], 'unit': 'degC'},
        'u': {'value': [5, 10, 15, 20, 25, 30, 35, 40], 'unit': 'knots'},
        'v': {'value': [2, 4, 6, 8, 10, 12, 14, 16], 'unit': 'knots'},
        'z': {'value': [0, 1000, 2000, 3000, 4000, 5500, 7000, 9000], 'unit': 'meter'},
    }


class GenerateSkewtFileFormatTests(TestCase):
    """RED: Tests asserting format validity of generate_skewt_file output."""

    def setUp(self):
        self.sounding_data = _make_sounding_data()

    def test_png_starts_with_magic_bytes(self):
        """PNG output MUST begin with the PNG magic bytes."""
        result = generate_skewt_file(self.sounding_data, fmt='png')
        self.assertIsInstance(result, bytes)
        self.assertTrue(
            result[:8] == b'\x89PNG\r\n\x1a\n', 'PNG output must start with magic bytes \\x89PNG'
        )

    def test_pdf_starts_with_header(self):
        """PDF output MUST begin with %PDF."""
        result = generate_skewt_file(self.sounding_data, fmt='pdf')
        self.assertIsInstance(result, bytes)
        self.assertTrue(result[:4] == b'%PDF', 'PDF output must start with %PDF')

    def test_svg_contains_root_element(self):
        """SVG output MUST contain an <svg root element."""
        result = generate_skewt_file(self.sounding_data, fmt='svg')
        self.assertIsInstance(result, bytes)
        self.assertIn(b'<svg', result, 'SVG output must contain an <svg root element')

    def test_invalid_input_raises_value_error(self):
        """Missing required keys MUST raise ValueError."""
        with self.assertRaises(ValueError):
            generate_skewt_file({'p': {}}, fmt='png')


class GenerateSkewtWrapperTests(TestCase):
    """Ensure generate_skewt still returns base64 PNG (backward compat)."""

    def setUp(self):
        self.sounding_data = _make_sounding_data()

    def test_returns_base64_string(self):
        """generate_skewt MUST return a base64-encoded string."""
        import base64

        result = generate_skewt(self.sounding_data)
        self.assertIsInstance(result, str)
        decoded = base64.b64decode(result)
        self.assertTrue(
            decoded[:8] == b'\x89PNG\r\n\x1a\n', 'Base64 wrapper must decode to valid PNG'
        )
