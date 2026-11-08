"""
Tests for Content Security Policy (CSP) via django-csp (change 011-csp).

These tests are written FIRST (RED phase) before the CSP configuration is added.
They will fail until the CSP middleware and settings are implemented.

NOTE: django-csp 4.x (pinned by requirements/base.txt) renamed the middleware to
`csp.middleware.CSPMiddleware` and restructured the policy dict under a
`DIRECTIVES` sub-dict. The design draft referenced the 3.x names; tests and
implementation follow the installed 4.x API (documented deviation).
"""

from django.conf import settings
from django.test import TestCase, override_settings


def _directives():
    return settings.CONTENT_SECURITY_POLICY['DIRECTIVES']


class CSPSettingsTest(TestCase):
    """Verify that CSP settings are defined and contain required directives."""

    def test_content_security_policy_is_dict(self):
        """REQ-2: CONTENT_SECURITY_POLICY must be a dict."""
        self.assertIsInstance(settings.CONTENT_SECURITY_POLICY, dict)

    def test_default_src_is_self(self):
        """REQ-2: default-src must be 'self'."""
        self.assertEqual(_directives()['default-src'], ["'self'"])

    def test_middleware_is_registered(self):
        """REQ-1: CSP middleware must be registered in MIDDLEWARE."""
        self.assertIn('csp.middleware.CSPMiddleware', settings.MIDDLEWARE)

    def test_script_src_allows_unsafe_inline(self):
        """REQ-3: script-src must allow 'unsafe-inline' for interim exception."""
        self.assertIn("'unsafe-inline'", _directives()['script-src'])
        self.assertIn("'self'", _directives()['script-src'])

    def test_style_src_allows_unsafe_inline(self):
        """REQ-3: style-src must allow 'unsafe-inline' for interim exception."""
        self.assertIn("'unsafe-inline'", _directives()['style-src'])
        self.assertIn("'self'", _directives()['style-src'])

    def test_img_src_allows_cdn_and_data(self):
        """REQ-3: img-src must allow cdn.jsdelivr.net and data:."""
        img_src = _directives()['img-src']
        self.assertIn("'self'", img_src)
        self.assertIn('data:', img_src)
        self.assertIn('https://cdn.jsdelivr.net', img_src)

    def test_no_external_wrf_host_in_policy(self):
        """REQ-3: imgwrfserver.cmw.insmet.cu must NOT need an external entry."""
        policy_text = str(_directives())
        self.assertNotIn('imgwrfserver.cmw.insmet.cu', policy_text)

    def test_object_src_is_self(self):
        """object-src must be 'self' so the PDF modal can embed <object>
        same-origin documents."""
        self.assertEqual(_directives()['object-src'], ["'self'"])

    def test_frame_ancestors_is_self(self):
        """Hardening: frame-ancestors must be 'self'."""
        self.assertEqual(_directives()['frame-ancestors'], ["'self'"])

    def test_base_uri_is_self(self):
        """Hardening: base-uri must be 'self'."""
        self.assertEqual(_directives()['base-uri'], ["'self'"])

    def test_font_src_is_self(self):
        """Font loading from same-origin only."""
        self.assertEqual(_directives()['font-src'], ["'self'"])

    def test_connect_src_is_self(self):
        """API connections from same-origin only."""
        self.assertEqual(_directives()['connect-src'], ["'self'"])


class CSPHeaderPresenceTest(TestCase):
    """Verify that the CSP header is present on representative responses."""

    def test_home_page_has_csp_header(self):
        """REQ-5: GET / must include Content-Security-Policy header."""
        response = self.client.get('/')
        self.assertIn('Content-Security-Policy', response.headers)

    def test_csp_header_contains_default_src(self):
        """REQ-5: CSP header must contain default-src 'self'."""
        response = self.client.get('/')
        header = response.headers.get('Content-Security-Policy', '')
        self.assertIn("default-src 'self'", header)

    def test_csp_header_contains_script_src(self):
        """REQ-3: CSP header must contain script-src 'self' 'unsafe-inline'."""
        response = self.client.get('/')
        header = response.headers.get('Content-Security-Policy', '')
        self.assertIn("script-src 'self' 'unsafe-inline'", header)

    def test_csp_header_contains_style_src(self):
        """REQ-3: CSP header must contain style-src 'self' 'unsafe-inline'."""
        response = self.client.get('/')
        header = response.headers.get('Content-Security-Policy', '')
        self.assertIn("style-src 'self' 'unsafe-inline'", header)

    def test_csp_header_contains_img_src(self):
        """REQ-3: CSP header must contain img-src with cdn.jsdelivr.net."""
        response = self.client.get('/')
        header = response.headers.get('Content-Security-Policy', '')
        self.assertIn("img-src 'self' data: https://cdn.jsdelivr.net", header)

    def test_csp_header_contains_object_src(self):
        """CSP header must contain object-src 'self' (PDF <object> embedding)."""
        response = self.client.get('/')
        header = response.headers.get('Content-Security-Policy', '')
        self.assertIn("object-src 'self'", header)

    def test_csp_header_contains_frame_ancestors(self):
        """Hardening: CSP header must contain frame-ancestors 'self'."""
        response = self.client.get('/')
        header = response.headers.get('Content-Security-Policy', '')
        self.assertIn("frame-ancestors 'self'", header)


REPORT_ONLY_POLICY = {
    'DIRECTIVES': {
        'default-src': ["'self'"],
        'base-uri': ["'self'"],
        'frame-ancestors': ["'self'"],
        'object-src': ["'self'"],
        'script-src': ["'self'", "'unsafe-inline'"],
        'style-src': ["'self'", "'unsafe-inline'"],
        'img-src': ["'self'", 'data:', 'https://cdn.jsdelivr.net'],
        'font-src': ["'self'"],
        'connect-src': ["'self'"],
    },
}


@override_settings(
    CONTENT_SECURITY_POLICY=None,
    CONTENT_SECURITY_POLICY_REPORT_ONLY=REPORT_ONLY_POLICY,
)
class CSPReportOnlyTest(TestCase):
    """Verify the CSP_REPORT_ONLY staging toggle (tasks.md 2.4)."""

    def test_report_only_header_on_public_page(self):
        """With CSP_REPORT_ONLY the header is Content-Security-Policy-Report-Only."""
        response = self.client.get('/tiempo/hoy/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Content-Security-Policy-Report-Only', response.headers)
        self.assertNotIn('Content-Security-Policy', response.headers)

    def test_report_only_policy_allows_cdn_images(self):
        """The report-only policy keeps jsdelivr in img-src (no external-origin block)."""
        header = self.client.get('/tiempo/hoy/').headers.get(
            'Content-Security-Policy-Report-Only', ''
        )
        self.assertIn("img-src 'self' data: https://cdn.jsdelivr.net", header)
