"""Landing "traducida + contenido real" contract.

Tests for the landing as it stands on disk: a plain hero (no browser mockup),
six capability sections with ti-* icons and Spanish headings, institutional
logos in the footer, real marketing copy ("Servicios y herramientas del Centro"
con imagen GOES-16), newsletter CTA, and an institution navbar (Visión /
Misión / Quiénes Somos) that links to real pages. The landing NEVER duplicates the
home's dashboard content (no region cards, no live warnings strip, no
services grid). Global adjustments: the brand logo always points to the
landing (landing, home and dashboard), the home reuses the small transparent
dashboard footer, dashboard/home social icons render smaller (social-sm),
and logout redirects to home:index.
"""

import re
from datetime import date

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.commercial.models import Service
from apps.core.models import SiteConfiguration
from apps.meteo.models import ForecastRegions, Forecasts, Warning

BRANDS = ('facebook', 'instagram', 'x', 'telegram')
LABELS = ('Facebook', 'Instagram', 'X', 'Telegram')

# Social anchor: class="social social-app-{brand} [size/palette classes]".
# Regex (never literal substrings): djlint splits attributes across lines.
SOCIAL_ANCHOR_RE = re.compile(r'<a class="social social-app-([a-z]+)([^"]*)"[^>]*>')


def social_anchor_classes(html):
    """Dict brand -> extra class tokens of every social anchor in html."""
    return {m.group(1): m.group(2) for m in SOCIAL_ANCHOR_RE.finditer(html)}


class LandingFooterSocialTests(TestCase):
    """Socials plugin activation contract.

    Both /home/ (which reuses the dashboard footer) and the
    dashboard expose the four `social-gray` anchors and render them at the
    smaller `social-sm` size.
    """

    def setUp(self):
        self.public = self.client.get(reverse('home:index')).content.decode()
        staff = User.objects.create_user(
            username='staff-socials',
            email='staff@cmw.insmet.cu',
            first_name='Staff',
            last_name='Social',
            is_staff=True,
        )
        self.client.force_login(staff)
        self.dashboard = self.client.get(reverse('dashboard:index')).content.decode()

    def test_public_footer_exposes_four_social_gray_anchors(self):
        anchors = social_anchor_classes(self.public)
        self.assertEqual(set(BRANDS), set(anchors))
        for classes in anchors.values():
            self.assertIn('social-gray', classes, msg=classes)

    def test_dashboard_footer_exposes_four_social_gray_anchors(self):
        anchors = social_anchor_classes(self.dashboard)
        self.assertEqual(set(BRANDS), set(anchors))
        for classes in anchors.values():
            self.assertIn('social-gray', classes, msg=classes)

    def test_social_anchors_carry_brand_aria_labels(self):
        for _brand, label in zip(BRANDS, LABELS, strict=True):
            self.assertIn(f'aria-label="{label}"', self.public, msg=label)
            self.assertIn(f'aria-label="{label}"', self.dashboard, msg=label)

    def test_dashboard_and_public_socials_rendered_smaller(self):
        for page in (self.public, self.dashboard):
            anchors = social_anchor_classes(page)
            for brand in BRANDS:
                self.assertIn('social-sm', anchors[brand], msg=brand)

    def test_socials_stylesheet_linked_site_wide(self):
        for page in (self.public, self.dashboard):
            self.assertIn('dist/css/tabler-socials.min.css?v=151', page)

    def test_socials_stylesheet_not_served_from_cdn(self):
        for page in (self.public, self.dashboard):
            self.assertNotIn('cdn.jsdelivr.net', page, msg='CDN leak in rendered page')
            self.assertNotIn('unpkg.com', page)

    def test_social_assets_resolve(self):
        social_dir = settings.BASE_DIR / 'static' / 'dist' / 'img' / 'social'
        for brand in BRANDS:
            for asset in (f'{brand}.svg', f'{brand}-gray.svg'):
                self.assertTrue((social_dir / asset).is_file(), msg=asset)

    def test_html_lang_and_theme_attrs(self):
        for page in (self.public, self.dashboard):
            self.assertIn('<html lang="es" data-bs-theme="light"', page)


class LandingRoutingTests(TestCase):
    """Landing at '/', index at '/home/'."""

    def test_landing_reverses_to_root(self):
        self.assertEqual(reverse('home:landing'), '/')

    def test_index_reverses_to_home(self):
        self.assertEqual(reverse('home:index'), '/home/')

    def test_get_root_renders_landing_template(self):
        response = self.client.get('/')
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'pages/home/landing.html')

    def test_get_home_renders_index_template(self):
        response = self.client.get('/home/')
        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'pages/home/index.html')

    def test_other_home_urls_unchanged(self):
        self.assertEqual('/tiempo/hoy/', reverse('home:weather_today'))
        self.assertEqual('/imagenes/satelitales/', reverse('home:satellite'))
        self.assertEqual('/institucion/publicaciones_cientificas/', reverse('home:publications'))


class LandingRenderTests(TestCase):
    """PRODUCT SHOWCASE render with data present.

    With a forecast, an active warning and published services in the DB, the
    landing shows the product (browser mockup, capability sections, logos,
    real Spanish copy) and must NOT mirror the home's live content.
    """

    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(username='landing-author', password='x')
        forecast = Forecasts.objects.create(
            date=date.today(),
            lp='Luna Llena',
            nlp='Menguante',
            nlpd=date.today(),
            sunrise='06:30:00',
            sunset='18:30:00',
            uv_index=5,
        )
        seas = {'north': 'TQ', 'interior': None, 'south': 'MRJ'}
        for region, sea in seas.items():
            for period in ('morning', 'afternoon', 'night'):
                ForecastRegions.objects.create(
                    forecast=forecast,
                    region=region,
                    period=period,
                    temp=28,
                    weather='PARCN',
                    wind_dir='NE',
                    wind_speed='15',
                    sea_note=sea,
                )
        Warning.objects.create(
            warning_type='early',
            title='Alerta por lluvias intensas',
            summary='Se esperan lluvias intensas en la provincia.',
            user=cls.author,
            valid_until=timezone.now() + timezone.timedelta(days=2),
        )
        for i in range(3):
            Service.objects.create(
                title=f'Servicio destacado {i}',
                summary=f'Resumen del servicio {i}.',
                user=cls.author,
                service_type=Service.COMMERCIAL,
                price=100,
            )

    def setUp(self):
        self.html = self.client.get('/').content.decode()

    def test_hero_ctas_render(self):
        """CTAs traducidos: portal administrativo + página "Quiénes Somos"."""
        self.assertIn('Explorar el portal', self.html)
        self.assertIn('Conozca el centro', self.html)
        self.assertIn(reverse('home:index'), self.html)
        self.assertIn(reverse('home:institution_about'), self.html)

    def test_hero_copy_real_spanish(self):
        self.assertIn('Centro Meteorológico Provincial de Camagüey', self.html)
        self.assertIn('Explore el pronóstico, los avisos, los modelos', self.html)

    def test_feature_sections_render_one_capability_each(self):
        sections = (
            ('ti-temperature-sun', 'El tiempo en su región'),
            ('ti-alert-triangle', 'Avisos y alertas meteorológicas'),
            ('ti-atom', 'Física de la atmósfera'),
            ('ti-satellite', 'Imágenes satelitales'),
            ('ti-tools', 'Servicios meteorológicos'),
            ('ti-book', 'Publicaciones científicas'),
        )
        for icon, heading in sections:
            self.assertIn(icon, self.html, msg=icon)
            self.assertIn(heading, self.html, msg=heading)

    def test_home_dashboard_content_not_duplicated(self):
        """Live home content (region cards, warnings, services) must NOT leak
        into the showcase landing."""
        for marker in (
            'Costa Norte',
            'Costa Sur',
            'Alerta por lluvias intensas',
            'Servicio destacado 0',
        ):
            self.assertNotIn(marker, self.html, msg=marker)

    def test_institution_logos_render(self):
        for asset in ('dist/img/citma.png', 'dist/img/ama.png', 'dist/img/insmet.svg'):
            self.assertIn(asset, self.html, msg=asset)

    def test_marketing_section_uses_real_spanish_copy(self):
        """Copy real del centro (no Lorem ipsum / copy genérico de Tabler)."""
        for marker in (
            'Servicios y herramientas del Centro',
            'Información meteorológica autorizada, confiable y oportuna',
            'Atención especializada al cliente',
            'Información oficial del INSMET',
            'Vigilancia permanente del tiempo',
            'dist/img/webp/cloud.webp',
        ):
            self.assertIn(marker, self.html, msg=marker)
        for marker in (
            'Everything you need to deploy your app',
            'Lorem ipsum',
            'Designed with users in mind',
            'Built for developers',
            'Fully customizable',
        ):
            self.assertNotIn(marker, self.html, msg=marker)

    def test_newsletter_cta_renders(self):
        self.assertIn('Suscríbase al boletín del Centro', self.html)
        self.assertIn('Reciba en su correo los avisos, pronósticos y novedades', self.html)
        self.assertIn('Suscribirse', self.html)

    def test_newsletter_form_is_real(self):
        """El formulario del diseño tiene que enviar, no ser un mock.

        El rediseño de las páginas públicas trajo un `<input>` y un botón
        `type="submit"` sin `<form>`: se veía igual y no hacía nada. Doble
        opt-in exige `action`, `method`, CSRF y el campo con nombre.
        """
        self.assertIn(f'action="{reverse("home:newsletter_subscribe")}"', self.html)
        self.assertIn('method="post"', self.html)
        self.assertIn('name="csrfmiddlewaretoken"', self.html)
        self.assertIn('name="email"', self.html)
        self.assertIn('id="newsletter-email"', self.html)
        self.assertIn('Le enviaremos un correo para confirmar la suscripción', self.html)

    def test_public_footer_socials_on_landing(self):
        """Big footer stays on the landing with its 4 social-gray anchors."""
        anchors = social_anchor_classes(self.html)
        self.assertEqual(set(BRANDS), set(anchors))
        for brand, classes in anchors.items():
            self.assertNotIn('social-sm', classes, msg=brand)

    def test_landing_keeps_light_theme_default(self):
        self.assertIn('<html lang="es" data-bs-theme="light"', self.html)

    def test_marketing_stylesheet_linked(self):
        """head.html links the vendored tabler-marketing CSS."""
        self.assertIn('dist/css/tabler-marketing.min.css?v=151', self.html)


class LandingSevereWarningStripTests(TestCase):
    """La franja del aviso cruza al landing solo cuando el aviso es grave.

    El landing es la raíz del dominio y no replica el contenido vivo del home
    (ver LandingRenderTests), pero un ciclón tropical o una tormenta fuerte son
    la excepción: esconderlos tras un clic a "Tiempo" no se defiende. Un aviso
    `early` por lluvias es rutina y se queda fuera, para que la franja no se
    vuelva paisaje.
    """

    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(username='landing-strip-author', password='x')
        SiteConfiguration.get_instance()

    def _create_warning(self, warning_type, title):
        return Warning.objects.create(
            warning_type=warning_type,
            title=title,
            summary='Resumen del aviso.',
            user=self.author,
            valid_until=timezone.now() + timezone.timedelta(days=1),
        )

    # Se busca `data-emergency-banner`, el atributo del `<aside>` de la franja, y
    # no la cadena `emergency-banner`: `scripts.html` carga
    # `emergency-banner.js` en todas las páginas, así que esa cadena aparece en
    # el HTML aunque no haya ningún aviso vigente.
    BANNER_MARKER = 'data-emergency-banner'

    def test_tropical_cyclone_warning_renders_strip_on_landing(self):
        self._create_warning('tropical_cyclone', 'Huracán acercándose a la provincia')
        html = self.client.get('/').content.decode()
        self.assertIn(self.BANNER_MARKER, html)
        self.assertIn('Huracán acercándose a la provincia', html)

    def test_storm_warning_renders_strip_on_landing(self):
        self._create_warning('storm', 'Tormenta eléctrica severa')
        html = self.client.get('/').content.decode()
        self.assertIn(self.BANNER_MARKER, html)
        self.assertIn('Tormenta eléctrica severa', html)

    def test_early_warning_does_not_render_strip_on_landing(self):
        self._create_warning('early', 'Alerta por lluvias intensas')
        html = self.client.get('/').content.decode()
        self.assertNotIn(self.BANNER_MARKER, html)
        self.assertNotIn('Alerta por lluvias intensas', html)

    def test_early_warning_still_renders_on_home(self):
        """El home conserva la franja para cualquier aviso vigente."""
        self._create_warning('early', 'Alerta por lluvias intensas')
        html = self.client.get(reverse('home:index')).content.decode()
        self.assertIn(self.BANNER_MARKER, html)
        self.assertIn('Alerta por lluvias intensas', html)


class LandingMarketingRedesignEmptyTests(TestCase):
    """Marketing redesign survives the empty state
    and the stylesheet is linked site-wide (off the landing too)."""

    def test_newsletter_cta_renders_without_data(self):
        response = self.client.get('/')
        self.assertEqual(200, response.status_code)
        self.assertIn(
            'Suscríbase al boletín del Centro',
            response.content.decode(),
        )

    def test_marketing_stylesheet_linked_off_landing(self):
        response = self.client.get(reverse('home:index'))
        self.assertEqual(200, response.status_code)
        self.assertIn('dist/css/tabler-marketing.min.css?v=151', response.content.decode())


class LandingEmptyStateTests(TestCase):
    """The showcase renders 200 without any data.

    The landing does not list DB content, so the empty state IS the
    showcase (hero + newsletter CTA render regardless of data).
    """

    def test_get_root_succeeds_without_data(self):
        response = self.client.get('/')
        self.assertEqual(200, response.status_code)
        html = response.content.decode()
        self.assertNotIn('Traceback', html)
        self.assertNotIn('Avisos meteorológicos activos', html)
        self.assertIn('Suscríbase al boletín del Centro', html)


class LandingNavbarTests(TestCase):
    """Navbar contract.

    The landing navbar (its own include) points its brand to the landing,
    links to the institution pages (Visión/Misión/Quiénes Somos), keeps the
    theme toggles, and offers 'Registrarse'/'Iniciar sesión' when anonymous;
    authenticated users get a plain 'Inicio' link (home:index) with no user
    menu. The home navbar and the dashboard sidebar point their brand to the
    landing too, while the home 'Inicio' menu item keeps /home/.
    """

    NAVBAR_OPEN = re.compile(r'<header class="navbar[^"]*">', re.S)
    NAVBAR_BODY = re.compile(r'<header class="navbar[^"]*">(.*?)</header>', re.S)

    @classmethod
    def _navbar_html(cls, page_html):
        """HTML del primer header navbar (tolerante a atributos multi-línea)."""
        match = cls.NAVBAR_BODY.search(page_html)
        return match.group(1) if match else ''

    @staticmethod
    def _anchor_href(html, anchor_class):
        """Extrae el href del primer anchor con la clase dada (tolerante a
        atributos en líneas separadas, como formatea djlint)."""
        match = re.search(rf'<a class="{re.escape(anchor_class)}"[^>]*\bhref="([^"]*)"', html)
        return match.group(1) if match else None

    @staticmethod
    def _anchor_href_by_text(html, text):
        """Extrae el href del primer anchor cuyo texto es exactamente `text`."""
        match = re.search(r'<a\b(?P<attrs>[^>]*)>\s*' + re.escape(text) + r'\s*</a>', html)
        if not match:
            return None
        href = re.search(r'\bhref="([^"]*)"', match.group('attrs'))
        return href.group(1) if href else None

    @staticmethod
    def _dashboard_brand_href(html):
        """Href del anchor dentro del h1 brand del sidebar dashboard."""
        match = re.search(
            r'<h1 class="navbar-brand navbar-brand-autodark">\s*<a\b[^>]*\bhref="([^"]*)"',
            html,
        )
        return match.group(1) if match else None

    def _anonymous_navbar(self):
        return self._navbar_html(self.client.get('/').content.decode())

    def test_landing_navbar_brand_points_to_landing(self):
        navbar = self._anonymous_navbar()
        self.assertEqual('/', self._anchor_href(navbar, 'navbar-brand navbar-brand-autodark'))

    def test_landing_navbar_links_to_institution_pages(self):
        navbar = self._anonymous_navbar()
        for label, path in (
            ('Visión', reverse('home:institution_vision')),
            ('Misión', reverse('home:institution_mission')),
            ('Quiénes Somos', reverse('home:institution_about')),
        ):
            self.assertIn(label, navbar, msg=label)
            self.assertIn(path, navbar, msg=path)
        # Servicios/Publicaciones links live in the footer, not here.
        for label in ('Servicios', 'Publicaciones'):
            self.assertNotIn(label, navbar, msg=label)

    def test_landing_navbar_login_link_anonymous(self):
        navbar = self._anonymous_navbar()
        self.assertIn('Iniciar sesión', navbar)
        self.assertEqual(
            reverse('user_auth:login'), self._anchor_href_by_text(navbar, 'Iniciar sesión')
        )

    def test_landing_navbar_register_link_anonymous(self):
        navbar = self._anonymous_navbar()
        self.assertIn('Registrarse', navbar)
        self.assertEqual(
            reverse('user_auth:customer_register'),
            self._anchor_href_by_text(navbar, 'Registrarse'),
        )

    def test_landing_navbar_authenticated_shows_home_link(self):
        staff = User.objects.create_user(
            username='staff-navbar-v3',
            email='staff2@cmw.insmet.cu',
            first_name='Staff',
            last_name='Navbar',
            is_staff=True,
        )
        self.client.force_login(staff)
        navbar = self._navbar_html(self.client.get('/').content.decode())
        self.assertEqual('/home/', self._anchor_href_by_text(navbar, 'Inicio'))
        self.assertNotIn('Iniciar sesión', navbar)
        self.assertNotIn('dropdown', navbar, msg='sin menú de usuario')

    def test_landing_navbar_has_theme_toggles(self):
        landing = self.client.get('/').content.decode()
        self.assertIn('?theme=dark', landing, msg='toggle de modo oscuro presente')
        self.assertIn('?theme=light', landing, msg='toggle de modo claro presente')
        self.assertNotIn('id="navbar-menu"', landing, msg='collapse del navbar de home ausente')

    def test_home_navbar_brand_points_to_landing(self):
        index = self.client.get('/home/').content.decode()
        self.assertEqual('/', self._anchor_href(index, 'navbar-brand navbar-brand-autodark'))

    def test_home_navbar_inicio_points_to_home(self):
        index = self.client.get('/home/').content.decode()
        self.assertEqual('/home/', self._anchor_href(index, 'nav-link'))

    def test_dashboard_sidebar_brand_points_to_landing(self):
        staff = User.objects.create_user(
            username='staff-sidebar-v3',
            email='staff3@cmw.insmet.cu',
            first_name='Staff',
            last_name='Sidebar',
            is_staff=True,
        )
        self.client.force_login(staff)
        dashboard = self.client.get(reverse('dashboard:index')).content.decode()
        self.assertEqual('/', self._dashboard_brand_href(dashboard))


class InstitutionPagesTests(TestCase):
    """Páginas institucionales del navbar landing (Visión/Misión/Quiénes Somos)."""

    PAGES = (
        ('home:institution_vision', 'pages/home/institution/vision.html', 'Visión', 'vision'),
        ('home:institution_mission', 'pages/home/institution/mission.html', 'Misión', 'mision'),
        (
            'home:institution_about',
            'pages/home/institution/about.html',
            'Quiénes Somos',
            'quienes_somos',
        ),
    )

    def test_urls_reverse(self):
        self.assertEqual(reverse('home:institution_vision'), '/institucion/vision/')
        self.assertEqual(reverse('home:institution_mission'), '/institucion/mision/')
        self.assertEqual(reverse('home:institution_about'), '/institucion/quienes-somos/')

    def test_pages_render_with_context(self):
        for name, template, title, segment in self.PAGES:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(200, response.status_code)
                self.assertTemplateUsed(response, template)
                self.assertTemplateUsed(response, 'layouts/landing.html')
                self.assertEqual(title, response.context['title'])
                self.assertEqual('institucion', response.context['parent'])
                self.assertEqual(segment, response.context['segment'])

    def test_landing_navbar_links_to_institution_pages(self):
        html = self.client.get('/').content.decode()
        for label, path in (
            ('Visión', reverse('home:institution_vision')),
            ('Misión', reverse('home:institution_mission')),
            ('Quiénes Somos', reverse('home:institution_about')),
        ):
            self.assertIn(label, html, msg=label)
            self.assertIn(path, html, msg=path)


class LandingFooterLayoutTests(TestCase):
    """Footer layout contract.

    The home (and dashboard) reuse the small transparent footer with `<li>`
    socials rendered at social-sm; the big institutional footer (with the
    'Síguenos' column) stays ONLY on the landing, socials at default size.
    """

    FOOTER_OPEN_RE = re.compile(r'<footer class="([^"]*)"')

    @staticmethod
    def _footer_class(html):
        match = LandingFooterLayoutTests.FOOTER_OPEN_RE.search(html)
        return match.group(1) if match else ''

    def test_home_uses_transparent_dashboard_footer(self):
        html = self.client.get('/home/').content.decode()
        self.assertIn('footer-transparent', self._footer_class(html))
        anchors = social_anchor_classes(html)
        self.assertEqual(set(BRANDS), set(anchors))
        self.assertNotIn('Síguenos', html, msg='footer grande no debe estar en /home/')

    def test_landing_keeps_big_footer(self):
        html = self.client.get('/').content.decode()
        self.assertNotIn('footer-transparent', self._footer_class(html))
        self.assertIn('Síguenos', html, msg='columna de redes del footer grande')

    def test_dashboard_and_home_socials_rendered_smaller(self):
        home = self.client.get('/home/').content.decode()
        staff = User.objects.create_user(
            username='staff-footer-v3',
            email='staff4@cmw.insmet.cu',
            first_name='Staff',
            last_name='Footer',
            is_staff=True,
        )
        self.client.force_login(staff)
        dashboard = self.client.get(reverse('dashboard:index')).content.decode()
        for page in (home, dashboard):
            anchors = social_anchor_classes(page)
            self.assertEqual(set(BRANDS), set(anchors))
            for brand, classes in anchors.items():
                self.assertIn(
                    'social-sm',
                    classes,
                    msg=f'{brand} en {"home" if page is home else "dashboard"}',
                )

    def test_landing_big_footer_socials_keep_default_size(self):
        html = self.client.get('/').content.decode()
        anchors = social_anchor_classes(html)
        self.assertEqual(set(BRANDS), set(anchors))
        for brand, classes in anchors.items():
            self.assertNotIn('social-sm', classes, msg=brand)


class LandingSeoTests(TestCase):
    """Meta description + OG tags; CSP intact."""

    def setUp(self):
        self.response = self.client.get('/')
        self.html = self.response.content.decode()

    def test_meta_description_present(self):
        self.assertIsNotNone(
            re.search(r'<meta name="description"[^>]*\bcontent="[^"]+"', self.html)
        )

    def test_five_og_tags_present(self):
        for prop in ('og:title', 'og:description', 'og:type', 'og:url', 'og:locale'):
            self.assertIn(f'property="{prop}"', self.html, msg=prop)

    def test_og_url_is_absolute_root(self):
        self.assertIn('property="og:url" content="http://testserver/"', self.html)

    def test_csp_header_present(self):
        self.assertIn('Content-Security-Policy', self.response.headers)


class LandingCdnScanTests(TestCase):
    """The two landing templates carry no CDN references (read-only scan)."""

    def test_new_templates_free_of_cdn_references(self):
        files = (
            settings.BASE_DIR / 'apps' / 'home' / 'templates' / 'pages' / 'home' / 'landing.html',
            settings.BASE_DIR / 'templates' / 'layouts' / 'landing.html',
        )
        for path in files:
            content = path.read_text(encoding='utf-8')
            self.assertNotIn('cdn.jsdelivr.net', content, msg=str(path))
            self.assertNotIn('unpkg.com', content, msg=str(path))
