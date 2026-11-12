"""Clasificación del user-agent y del referrer.

El parseo es deliberadamente corto: el panel necesita agrupar tráfico por
dispositivo, navegador y sistema, no identificar la versión exacta de cada
navegador. Las listas de abajo se comparan por subcadena sobre el host, así que
`google.com`, `google.com.co` y `news.google.com` caen todos en "buscador".
"""

import re

_TABLET_RE = re.compile(r'iPad|Tablet|Kindle|Silk|PlayBook', re.IGNORECASE)
_MOBILE_RE = re.compile(r'iPhone|iPod|Windows Phone|IEMobile|Opera Mini|Mobile', re.IGNORECASE)
_ANDROID_RE = re.compile(r'Android', re.IGNORECASE)

_SEARCH_HOSTS = (
    'google.',
    'bing.',
    'yahoo.',
    'duckduckgo.',
    'yandex.',
    'ecosia.',
    'baidu.',
    'startpage.',
    'qwant.',
)
_SOCIAL_HOSTS = (
    'facebook.',
    'fb.',
    'instagram.',
    'twitter.',
    'x.com',
    't.co',
    'linkedin.',
    'lnkd.in',
    't.me',
    'telegram.',
    'whatsapp.',
    'youtube.',
    'tiktok.',
    'reddit.',
)

_BROWSERS = (
    ('Edge', re.compile(r'Edg/|Edge/', re.IGNORECASE)),
    ('Opera', re.compile(r'OPR/|Opera', re.IGNORECASE)),
    ('Samsung Internet', re.compile(r'SamsungBrowser', re.IGNORECASE)),
    ('Chrome', re.compile(r'Chrome/|CriOS', re.IGNORECASE)),
    ('Firefox', re.compile(r'Firefox/|FxiOS', re.IGNORECASE)),
    ('Safari', re.compile(r'Safari/', re.IGNORECASE)),
)


def device_type(user_agent):
    user_agent = user_agent or ''
    if not user_agent:
        return 'unknown'
    if _TABLET_RE.search(user_agent):
        return 'tablet'
    if _ANDROID_RE.search(user_agent):
        # Android sin "Mobile" es una tablet: el teléfono lo incluye siempre.
        return 'mobile' if _MOBILE_RE.search(user_agent) else 'tablet'
    if _MOBILE_RE.search(user_agent):
        return 'mobile'
    return 'desktop'


def browser_name(user_agent):
    user_agent = user_agent or ''
    for name, pattern in _BROWSERS:
        if pattern.search(user_agent):
            return name
    return 'Otro' if user_agent else 'Desconocido'


def operating_system(user_agent):
    user_agent = user_agent or ''
    if not user_agent:
        return 'Desconocido'
    if _ANDROID_RE.search(user_agent):
        return 'Android'
    if re.search(r'iPhone|iPad|iPod', user_agent, re.IGNORECASE):
        return 'iOS'
    if re.search(r'Windows', user_agent, re.IGNORECASE):
        return 'Windows'
    if re.search(r'Mac OS X|Macintosh', user_agent, re.IGNORECASE):
        return 'macOS'
    if re.search(r'Linux|X11', user_agent, re.IGNORECASE):
        return 'Linux'
    return 'Otro'


def _host_from_url(url):
    if not url:
        return ''
    host = url.split('://')[-1].split('/')[0].split('?')[0].lower()
    host = host.split(':')[0]
    if host.startswith('www.'):
        host = host[4:]
    return host[:255]


def classify_referrer(referrer_url, request_host):
    """Devuelve `(host, tipo)` según de dónde vino la visita."""
    host = _host_from_url(referrer_url or '')
    request_host = (request_host or '').split(':')[0].lower()
    if request_host.startswith('www.'):
        request_host = request_host[4:]
    if not host:
        return '', 'direct'
    if request_host and (host == request_host or host.endswith('.' + request_host)):
        return host, 'internal'
    if any(token in host for token in _SEARCH_HOSTS):
        return host, 'search'
    if any(token in host for token in _SOCIAL_HOSTS):
        return host, 'social'
    return host, 'external'
