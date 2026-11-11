from django import template
from django.conf import settings

from apps.monitoring.services.formatting import format_duration

register = template.Library()

EMPTY = '—'


@register.simple_tag
def monitoring_enabled():
    """Interruptor único del monitoreo, para no inyectar el beacon al apagarlo."""
    return getattr(settings, 'MONITORING_ENABLED', True)


@register.filter
def dash(value):
    """Un faltante sin datos se dibuja como raya, nunca como 0."""
    if value is None or value == '' or value == []:
        return EMPTY
    return value


def _as_float(value):
    """Convierte a número sin asumir que el valor del template lo sea."""
    try:
        return float(value)
    except TypeError:
        return None
    except ValueError:
        return None


@register.filter
def pct(value, decimals=2):
    if value is None:
        return EMPTY
    number = _as_float(value)
    if number is None:
        return value
    text = f'{number:.{int(decimals)}f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return f'{text}%'


@register.filter
def ms(value):
    if value is None:
        return EMPTY
    number = _as_float(value)
    if number is None:
        return value
    return f'{int(round(number))} ms'


@register.filter
def num(value, decimals=0):
    if value is None:
        return EMPTY
    number = _as_float(value)
    if number is None:
        return value
    return f'{number:,.{int(decimals)}f}'


@register.filter
def duration(seconds):
    return format_duration(seconds) or EMPTY


# Estado de un bloque de disponibilidad y calificación de un Web Vital
# comparten escala de color: verde bueno, ámbar aviso, rojo malo, gris sin datos.
_STATE_CLASSES = {
    'ok': 'success',
    'good': 'success',
    'degraded': 'warning',
    'needs-improvement': 'warning',
    'down': 'danger',
    'poor': 'danger',
    'nodata': 'secondary',
}


@register.filter
def state_class(value):
    return _STATE_CLASSES.get(value, 'secondary')


@register.filter
def percent_of(value, total):
    """Porcentaje de `value` sobre `total`, para anchos de barras."""
    value = _as_float(value or 0)
    total = _as_float(total or 0)
    if value is None or total is None or total <= 0:
        return 0
    return round(100 * value / total, 2)
