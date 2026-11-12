"""Modelos de monitoreo del sitio.

Hay dos familias y conviene no mezclarlas:

- `RequestMetric`: una fila por petición servida. Se escribe desde el
  middleware y se poda por días. No se usan contadores en caché porque la
  caché por defecto es `locmem` y cada worker de gunicorn tendría su propio
  contador: el panel mostraría un tercio del tráfico real sin avisar.
- `PageView`, `WebVital` y `JavaScriptError`: lo que llega del beacon del
  navegador. Son datos de usuario real, no de servidor, y por eso viven
  separados.

Ninguno de estos modelos expone URLs propias ni aparece en el admin, así que
no necesitan UUID en la URL; la convención del proyecto de los cuatro permisos
custom se mantiene igual.
"""

from django.db import models
from django.utils import timezone


class RequestMetric(models.Model):
    """Una petición HTTP servida, medida de punta a punta del stack Django."""

    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    status_code = models.PositiveSmallIntegerField()
    # Milisegundos que tardó el backend en entregar la respuesta. Es el
    # componente de TTFB que controla el servidor: no incluye red ni navegador.
    duration_ms = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ('view_requestmetric', 'Ver métricas de peticiones'),
            ('add_requestmetric', 'Agregar métricas de peticiones'),
            ('change_requestmetric', 'Modificar métricas de peticiones'),
            ('delete_requestmetric', 'Eliminar métricas de peticiones'),
        ]
        indexes = [
            models.Index(fields=['created_at', 'status_code'], name='mon_req_created_status'),
        ]

    def __str__(self):
        return f'{self.method} {self.path} -> {self.status_code} ({self.duration_ms} ms)'


class PageView(models.Model):
    """Una carga de página reportada por el beacon del navegador."""

    REFERRER_DIRECT = 'direct'
    REFERRER_INTERNAL = 'internal'
    REFERRER_SEARCH = 'search'
    REFERRER_SOCIAL = 'social'
    REFERRER_EXTERNAL = 'external'
    REFERRER_CHOICES = [
        (REFERRER_DIRECT, 'Directo'),
        (REFERRER_INTERNAL, 'Interno'),
        (REFERRER_SEARCH, 'Buscador'),
        (REFERRER_SOCIAL, 'Red social'),
        (REFERRER_EXTERNAL, 'Externo'),
    ]

    DEVICE_MOBILE = 'mobile'
    DEVICE_TABLET = 'tablet'
    DEVICE_DESKTOP = 'desktop'
    DEVICE_UNKNOWN = 'unknown'
    DEVICE_CHOICES = [
        (DEVICE_MOBILE, 'Móvil'),
        (DEVICE_TABLET, 'Tablet'),
        (DEVICE_DESKTOP, 'Escritorio'),
        (DEVICE_UNKNOWN, 'Desconocido'),
    ]

    # Identificador de sesión del navegador, pasado por SHA-256: no se guarda
    # el valor crudo ni nada que permita reidentificar a una persona.
    session_hash = models.CharField(max_length=32, db_index=True)
    path = models.CharField(max_length=255)
    referrer_host = models.CharField(max_length=255, blank=True)
    referrer_type = models.CharField(
        max_length=20, choices=REFERRER_CHOICES, default=REFERRER_DIRECT
    )
    device_type = models.CharField(max_length=20, choices=DEVICE_CHOICES, default=DEVICE_UNKNOWN)
    browser = models.CharField(max_length=64, blank=True)
    os = models.CharField(max_length=64, blank=True)
    # Peso total transferido según resource timing, y cuántos recursos tardaron
    # más de 500 ms. Se guardan con la página porque son suyos, no globales.
    page_weight_kb = models.PositiveIntegerField(null=True, blank=True)
    slow_resources = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ('view_pageview', 'Ver páginas vistas'),
            ('add_pageview', 'Agregar páginas vistas'),
            ('change_pageview', 'Modificar páginas vistas'),
            ('delete_pageview', 'Eliminar páginas vistas'),
        ]
        indexes = [
            models.Index(fields=['created_at', 'path'], name='mon_pv_created_path'),
            models.Index(fields=['created_at', 'session_hash'], name='mon_pv_created_session'),
        ]

    def __str__(self):
        return f'{self.path} ({self.created_at:%Y-%m-%d %H:%M})'


class WebVital(models.Model):
    """Una medición de Core Web Vital reportada por el navegador."""

    LCP = 'lcp'
    FCP = 'fcp'
    INP = 'inp'
    CLS = 'cls'
    METRIC_CHOICES = [
        (LCP, 'LCP'),
        (FCP, 'FCP'),
        (INP, 'INP'),
        (CLS, 'CLS'),
    ]

    RATING_GOOD = 'good'
    RATING_NEEDS_IMPROVEMENT = 'needs-improvement'
    RATING_POOR = 'poor'
    RATING_CHOICES = [
        (RATING_GOOD, 'Bueno'),
        (RATING_NEEDS_IMPROVEMENT, 'Necesita mejorar'),
        (RATING_POOR, 'Pobre'),
    ]

    session_hash = models.CharField(max_length=32, db_index=True)
    path = models.CharField(max_length=255, blank=True)
    metric = models.CharField(max_length=8, choices=METRIC_CHOICES, db_index=True)
    # LCP/FCP/INP en milisegundos; CLS es un número sin unidad.
    value = models.FloatField()
    rating = models.CharField(max_length=20, choices=RATING_CHOICES)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ('view_webvital', 'Ver métricas web'),
            ('add_webvital', 'Agregar métricas web'),
            ('change_webvital', 'Modificar métricas web'),
            ('delete_webvital', 'Eliminar métricas web'),
        ]
        indexes = [
            models.Index(fields=['created_at', 'metric'], name='mon_vital_created_metric'),
        ]

    def __str__(self):
        return f'{self.metric.upper()}={self.value} ({self.rating})'


class JavaScriptError(models.Model):
    """Excepción no controlada reportada por el navegador."""

    session_hash = models.CharField(max_length=32, db_index=True)
    path = models.CharField(max_length=255, blank=True)
    message = models.TextField()
    source = models.CharField(max_length=255, blank=True)
    lineno = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        default_permissions = ()
        permissions = [
            ('view_javascripterror', 'Ver errores JavaScript'),
            ('add_javascripterror', 'Agregar errores JavaScript'),
            ('change_javascripterror', 'Modificar errores JavaScript'),
            ('delete_javascripterror', 'Eliminar errores JavaScript'),
        ]
        indexes = [
            models.Index(fields=['created_at'], name='mon_jserr_created'),
        ]

    def __str__(self):
        return self.message[:80]
