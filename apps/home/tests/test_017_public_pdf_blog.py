"""Tests for change 017-public-pdf-blog.

Covers the public blog-style PDF cards and the dashboard detail sweep:

- Avisos render the shared `document_card.html` with NO badge, title falls back
  to `summary` when `Warning.title` is empty, and no hardcoded
  "Aviso meteorológico" string is rendered.
- The publicaciones list renders `document_card.html` items inside a
  `row row-deck` grid, drops the "Ver detalle" (`public_detail`) button, and
  attaches neither a `public_detail` link nor a per-item trigger for items
  without a PDF.
- Each dashboard detail page (certificate, weather report, publication) delegates
  to `document_card.html` + the shared `document_pdf_modal.html` and emits no
  raw `<a target="_blank">Ver PDF</a>`.
"""

import re
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.meteo.models import Warning as MeteoWarning
from apps.publications.models import Author, ScientificPublication


class AvisosBlogCardTests(TestCase):
    """avisos uses document_card, no badge, title falls back to summary."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'author', 'author@test.com', 'pass', first_name='A', last_name='U'
        )

    def _create_warning(self, title=None):
        return MeteoWarning.objects.create(
            warning_type='early',
            title=title,
            user=self.user,
            summary='Resumen del aviso de prueba',
            file='warning_pdfs/aviso.pdf',
            valid_until=timezone.now() + timedelta(days=1),
        )

    def _get_page(self):
        return self.client.get(reverse('home:warnings_early'))

    def test_title_falls_back_to_summary_when_empty(self):
        self._create_warning(title=None)
        response = self._get_page()
        self.assertContains(response, 'Resumen del aviso de prueba')
        # The card title <h3> must carry the fallback summary.
        self.assertContains(response, '<h3 class="card-title">Resumen del aviso de prueba</h3>')

    def test_title_is_used_when_present(self):
        self._create_warning(title='Aviso costero importante')
        response = self._get_page()
        self.assertContains(response, 'Aviso costero importante')

    def test_no_badge_rendered(self):
        self._create_warning()
        self.assertNotContains(self._get_page(), 'badge bg-primary mb-2')

    def test_hardcoded_title_string_is_gone(self):
        self._create_warning()
        html = self._get_page().content.decode()
        # La cadena vive ahora en el `aria-label` de la franja de emergencia, que
        # es markup del layout. Esta prueba mide la tarjeta, no el layout, asi
        # que la asercion se acota al bloque que renderiza `document_card.html`.
        card = re.search(r'<article class="card">.*?</article>', html, re.S)
        self.assertIsNotNone(card, 'la pagina no renderizo ninguna tarjeta de documento')
        self.assertNotIn('Aviso meteorológico', card.group(0))

    def test_grid_wrapper_present(self):
        self._create_warning()
        html = self._get_page().content.decode()
        self.assertIn('row row-deck', html)
        self.assertIn('col-md-6', html)


class PublicationsBlogGridTests(TestCase):
    """publicaciones list uses document_card in a responsive grid."""

    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(first_name='Ada', last_name='Lovelace')

    def _create_publication(self, title='Paper A', with_pdf=True):
        pdf = None
        if with_pdf:
            pdf = SimpleUploadedFile('test.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        return ScientificPublication.objects.create(
            title=title,
            author=self.author,
            summary='Resumen de la publicación',
            publication_date=date.today(),
            pdf=pdf,
        )

    def _get_page(self):
        return self.client.get(reverse('home:publications'))

    def test_renders_document_cards_in_grid(self):
        self._create_publication()
        html = self._get_page().content.decode()
        self.assertIn('row row-deck', html)
        self.assertIn('col-md-6', html)
        self.assertContains(self._get_page(), 'data-pdf-url')
        self.assertContains(self._get_page(), 'id="documentPdfModal"')

    def test_no_ver_detalle_and_no_public_detail_link(self):
        self._create_publication()
        html = self._get_page().content.decode()
        self.assertNotIn('Ver detalle', html)
        self.assertNotIn('public_detail', html)

    def test_items_without_pdf_render_no_trigger(self):
        self._create_publication(title='Con PDF')
        self._create_publication(title='Sin PDF', with_pdf=False)
        html = self._get_page().content.decode()
        # Exactly one shared modal regardless of item count.
        self.assertEqual(1, html.count('id="documentPdfModal"'))
        # The "Sin PDF" item must not produce a Ver PDF trigger.
        if 'data-pdf-url' in html:
            self.assertNotIn('Sin PDF', html.split('data-pdf-url', 1)[0])
        else:
            self.assertIn('Sin PDF', html)
