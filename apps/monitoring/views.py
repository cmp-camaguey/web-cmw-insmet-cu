"""Endpoint público que recibe el beacon del navegador.

Es la única entrada de datos RUM y está expuesta sin sesión: la visitan también
las páginas públicas. Por eso el contrato es cerrado y defensivo:

- cuerpo máximo de 8 KB, rechazado antes de parsear;
- JSON de un solo objeto, con `kind` dentro de una lista blanca;
- sesión con hash y ventana de throttling por tipo de evento;
- ningún dato se devuelve y ningún error se explica: la respuesta es 204 o un
  código de rechazo sin cuerpo, que un visitante anónimo no puede aprovechar
  para descubrir el esquema interno.

Las escrituras fallidas no rompen el beacon: el navegador ya siguió adelante.
"""

import hashlib
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.monitoring.models import JavaScriptError, PageView, WebVital
from apps.monitoring.services import traffic
from apps.monitoring.services.useragent import (
    browser_name,
    classify_referrer,
    device_type,
    operating_system,
)

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 8192
THROTTLE_SECONDS = 2
ERROR_THROTTLE_SECONDS = 5
MAX_TEXT = 255
MAX_MESSAGE = 500
MAX_WEIGHT_KB = 2_000_000
MAX_VALUE = 3_600_000  # una hora en ms: más que eso no es una medición real
KINDS = ('pageview', 'vitals', 'error')


def _session_hash(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        return ''
    return hashlib.sha256(value.encode('utf-8', 'ignore')).hexdigest()[:32]


def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return ''
    return value.strip()[:limit]


def _number(value, minimum=0, maximum=MAX_VALUE, decimals=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        # Un entero arbitrariamente grande llega como int desde JSON y no cabe
        # en un float; no es una medición real.
        return None
    if number != number or number in (float('inf'), float('-inf')):
        return None
    if number < minimum or number > maximum:
        return None
    return round(number, decimals) if decimals is not None else number


def _integer(value, minimum=0, maximum=MAX_WEIGHT_KB):
    number = _number(value, minimum=minimum, maximum=maximum)
    return int(number) if number is not None else None


def _save_pageview(request, payload, session_hash):
    path = _text(payload.get('path')) or _text(request.META.get('HTTP_REFERER'))
    if not path:
        return
    request_host = request.get_host() if hasattr(request, 'get_host') else ''
    referrer_host, referrer_type = classify_referrer(payload.get('referrer'), request_host)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    PageView.objects.create(
        session_hash=session_hash,
        path=path,
        referrer_host=referrer_host,
        referrer_type=referrer_type,
        device_type=device_type(user_agent),
        browser=browser_name(user_agent),
        os=operating_system(user_agent),
        page_weight_kb=_integer(payload.get('weight_kb')),
        slow_resources=_integer(payload.get('slow_resources'), maximum=10000) or 0,
    )


def _save_vitals(payload, session_hash):
    metrics = payload.get('metrics')
    if not isinstance(metrics, dict):
        return
    path = _text(payload.get('path'))
    rows = []
    for metric in (WebVital.LCP, WebVital.FCP, WebVital.INP, WebVital.CLS):
        value = _number(metrics.get(metric), maximum=MAX_VALUE, decimals=3)
        if value is None:
            continue
        rating = traffic.rating_for(metric, value)
        if rating is None:
            continue
        rows.append(
            WebVital(
                session_hash=session_hash,
                path=path,
                metric=metric,
                value=value,
                rating=rating,
            )
        )
    if rows:
        WebVital.objects.bulk_create(rows)


def _save_error(payload, session_hash):
    message = _text(payload.get('message'), MAX_MESSAGE)
    if not message:
        return
    JavaScriptError.objects.create(
        session_hash=session_hash,
        path=_text(payload.get('path')),
        message=message,
        source=_text(payload.get('source')),
        lineno=_integer(payload.get('lineno'), maximum=10_000_000),
    )


_HANDLERS = {
    'pageview': _save_pageview,
    'vitals': lambda request, payload, session_hash: _save_vitals(payload, session_hash),
    'error': lambda request, payload, session_hash: _save_error(payload, session_hash),
}


@csrf_exempt
@require_POST
def rum_collect(request):
    if not getattr(settings, 'MONITORING_ENABLED', True):
        return HttpResponse(status=204)
    if len(request.body) > MAX_PAYLOAD_BYTES:
        return HttpResponse(status=413)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except ValueError:
        # Cubre JSON inválido y el cuerpo que no es UTF-8: `UnicodeDecodeError`
        # es subclase de `ValueError`.
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        return HttpResponse(status=400)

    kind = payload.get('kind')
    if kind not in KINDS:
        return HttpResponse(status=400)
    session_hash = _session_hash(payload.get('sid'))
    if not session_hash:
        return HttpResponse(status=400)

    throttle_key = f'monitoring:rum:{kind}:{session_hash}'
    throttle = ERROR_THROTTLE_SECONDS if kind == 'error' else THROTTLE_SECONDS
    if not cache.add(throttle_key, 1, throttle):
        return HttpResponse(status=204)

    handler = _HANDLERS[kind]
    try:
        handler(request, payload, session_hash)
    except Exception:
        # El visitante ya se fue; solo queda la traza para el operador.
        logger.warning('monitoring: el beacon no pudo guardar el evento %s', kind, exc_info=True)
    return HttpResponse(status=204)
