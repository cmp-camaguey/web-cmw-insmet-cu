"""Consultas sobre los datos que reporta el beacon del navegador.

Sin beacon no hay filas: todas las funciones devuelven estructuras vacías con
`None` en los agregados, y la plantilla muestra "sin datos" en vez de un cero
que parecería tráfico real.
"""

from datetime import UTC, timedelta

from django.db.models import Avg, Count, Min, Subquery
from django.db.models.functions import TruncMinute
from django.utils import timezone

from apps.monitoring.models import JavaScriptError, PageView, WebVital

# Umbrales oficiales de Core Web Vitals: (bueno, necesita mejorar). Por encima
# del segundo valor, pobre. CLS es adimensional; el resto va en milisegundos.
VITAL_META = {
    WebVital.LCP: {'label': 'LCP', 'unit': 'ms', 'good': 2500, 'ok': 4000, 'decimals': 0},
    WebVital.FCP: {'label': 'FCP', 'unit': 'ms', 'good': 1800, 'ok': 3000, 'decimals': 0},
    WebVital.INP: {'label': 'INP', 'unit': 'ms', 'good': 200, 'ok': 500, 'decimals': 0},
    WebVital.CLS: {'label': 'CLS', 'unit': '', 'good': 0.1, 'ok': 0.25, 'decimals': 2},
}

VITAL_SAMPLE = 2000

_REFERRER_LABELS = dict(PageView.REFERRER_CHOICES)
_DEVICE_LABELS = dict(PageView.DEVICE_CHOICES)


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = round(fraction * (len(ordered) - 1))
    index = min(len(ordered) - 1, max(0, index))
    return ordered[index]


def rating_for(metric, value):
    meta = VITAL_META.get(metric)
    if meta is None or value is None:
        return None
    if value <= meta['good']:
        return WebVital.RATING_GOOD
    if value <= meta['ok']:
        return WebVital.RATING_NEEDS_IMPROVEMENT
    return WebVital.RATING_POOR


def active_visitors(minutes, now=None):
    now = now or timezone.now()
    since = now - timedelta(minutes=minutes)
    return PageView.objects.filter(created_at__gte=since).values('session_hash').distinct().count()


def new_sessions(minutes, now=None):
    """Sesiones cuyo primer pageview cae dentro de la ventana.

    La primera visita se busca sobre toda la historia de la sesión, no sobre la
    ventana: una sesión que ya existía antes no es nueva aunque haya vuelto a
    entrar hace un minuto. El subquery acota el `Min` a las sesiones vistas en
    la ventana, que son las únicas candidatas.
    """
    now = now or timezone.now()
    since = now - timedelta(minutes=minutes)
    window_sessions = PageView.objects.filter(created_at__gte=since).values('session_hash')
    return (
        PageView.objects.filter(session_hash__in=Subquery(window_sessions))
        .values('session_hash')
        .annotate(first_seen=Min('created_at'))
        .filter(first_seen__gte=since)
        .count()
    )


def pageview_series(minutes=30, now=None):
    """Pageviews por minuto, con los minutos vacíos en cero."""
    now = now or timezone.now()
    since = (now - timedelta(minutes=minutes)).replace(second=0, microsecond=0)
    rows = (
        PageView.objects.filter(created_at__gte=since)
        # La truncación se fija en UTC a propósito: el middleware de zona
        # horaria activa la zona del sitio por request, y un desfase con
        # minutos (p. ej. +05:30) correría los buckets fuera de la rejilla de
        # minutos que arma la serie.
        .annotate(bucket=TruncMinute('created_at', tzinfo=UTC))
        .values('bucket')
        .annotate(count=Count('id'))
    )
    buckets = {}
    for row in rows:
        bucket = row['bucket']
        if timezone.is_aware(bucket):
            bucket = timezone.localtime(bucket)
        buckets[bucket.replace(second=0, microsecond=0)] = row['count']
    series = []
    for index in range(minutes + 1):
        minute = since + timedelta(minutes=index)
        series.append({'time': minute.strftime('%H:%M'), 'count': buckets.get(minute, 0)})
    return series


def top_pages(minutes=30, limit=6, now=None):
    now = now or timezone.now()
    since = now - timedelta(minutes=minutes)
    return list(
        PageView.objects.filter(created_at__gte=since)
        .values('path')
        .annotate(views=Count('id'))
        .order_by('-views', 'path')[:limit]
    )


def referrers(minutes=30, limit=6, now=None):
    now = now or timezone.now()
    since = now - timedelta(minutes=minutes)
    queryset = PageView.objects.filter(created_at__gte=since)
    hosts = list(
        queryset.values('referrer_host', 'referrer_type')
        .annotate(count=Count('id'))
        .order_by('-count')[:limit]
    )
    for row in hosts:
        row['label'] = row['referrer_host'] or _REFERRER_LABELS.get(row['referrer_type'], '—')
    types = []
    for row in queryset.values('referrer_type').annotate(count=Count('id')).order_by('-count'):
        types.append(
            {
                'type': row['referrer_type'],
                'label': _REFERRER_LABELS.get(row['referrer_type'], row['referrer_type']),
                'count': row['count'],
            }
        )
    return {'hosts': hosts, 'types': types}


def devices(minutes=30, now=None):
    now = now or timezone.now()
    since = now - timedelta(minutes=minutes)
    rows = (
        PageView.objects.filter(created_at__gte=since)
        .values('device_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    return [
        {
            'type': row['device_type'],
            'label': _DEVICE_LABELS.get(row['device_type'], row['device_type']),
            'count': row['count'],
        }
        for row in rows
    ]


def browsers(minutes=30, limit=6, now=None):
    now = now or timezone.now()
    since = now - timedelta(minutes=minutes)
    return list(
        PageView.objects.filter(created_at__gte=since)
        .values('browser')
        .annotate(count=Count('id'))
        .order_by('-count')[:limit]
    )


def traffic_snapshot(now=None):
    now = now or timezone.now()
    visitors_5m = active_visitors(5, now=now)
    visitors_30m = active_visitors(30, now=now)
    since_30m = now - timedelta(minutes=30)
    pageviews_30m = PageView.objects.filter(created_at__gte=since_30m).count()
    pageviews_24h = PageView.objects.filter(created_at__gte=now - timedelta(hours=24)).count()
    series = pageview_series(30, now=now)
    last_minute = series[-1]['count'] if series else 0
    return {
        'visitors_5m': visitors_5m,
        'visitors_30m': visitors_30m,
        'new_sessions_30m': new_sessions(30, now=now),
        'pageviews_30m': pageviews_30m,
        'pageviews_24h': pageviews_24h,
        'pageviews_last_minute': last_minute,
        'pages_per_visitor': round(pageviews_30m / visitors_30m, 2) if visitors_30m else None,
        'series': series,
        'top_pages': top_pages(now=now),
        'referrers': referrers(now=now),
        'devices': devices(now=now),
        'browsers': browsers(now=now),
        'has_data': PageView.objects.exists(),
    }


def web_vitals(hours=24, now=None):
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    queryset = WebVital.objects.filter(created_at__gte=since)
    metrics = {}
    for metric, meta in VITAL_META.items():
        values = list(
            queryset.filter(metric=metric)
            .order_by('-created_at')
            .values_list('value', flat=True)[:VITAL_SAMPLE]
        )
        p75 = _percentile(values, 0.75)
        # Las claves van con guion bajo y no con el valor del modelo
        # ("needs-improvement"): el parser de plantillas de Django corta en el
        # guion y no se puede escribir `distribution.needs-improvement`.
        rating_keys = {
            WebVital.RATING_GOOD: 'good',
            WebVital.RATING_NEEDS_IMPROVEMENT: 'needs_improvement',
            WebVital.RATING_POOR: 'poor',
        }
        distribution = {'good': 0, 'needs_improvement': 0, 'poor': 0}
        for row in queryset.filter(metric=metric).values('rating').annotate(count=Count('id')):
            distribution[rating_keys.get(row['rating'], 'poor')] += row['count']
        metrics[metric] = {
            'label': meta['label'],
            'unit': meta['unit'],
            'decimals': meta['decimals'],
            'good': meta['good'],
            'ok': meta['ok'],
            'p75': round(p75, meta['decimals']) if p75 is not None else None,
            'rating': rating_for(metric, p75),
            'samples': len(values),
            'distribution': distribution,
        }
    return {'window_hours': hours, 'metrics': metrics}


def javascript_errors(hours=24, limit=10, now=None):
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    queryset = JavaScriptError.objects.filter(created_at__gte=since)
    total = queryset.count()
    items = []
    for error in queryset.order_by('-created_at')[:limit]:
        items.append(
            {
                'message': error.message[:300],
                'path': error.path,
                'source': error.source,
                'lineno': error.lineno,
                'at': timezone.localtime(error.created_at).strftime('%H:%M:%S'),
            }
        )
    return {'count': total, 'items': items}


def page_weight(hours=24, now=None):
    now = now or timezone.now()
    since = now - timedelta(hours=hours)
    queryset = PageView.objects.filter(created_at__gte=since, page_weight_kb__isnull=False)
    aggregate = queryset.aggregate(
        avg_kb=Avg('page_weight_kb'),
        avg_slow=Avg('slow_resources'),
        samples=Count('id'),
    )
    return {
        'avg_kb': round(aggregate['avg_kb'], 1) if aggregate['avg_kb'] is not None else None,
        'avg_slow': round(aggregate['avg_slow'], 1) if aggregate['avg_slow'] is not None else None,
        'samples': aggregate['samples'] or 0,
    }
