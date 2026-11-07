"""Safe cache helpers for the Redis-backed caching layer (change 003-cache-redis).

These wrappers degrade gracefully: if the configured cache backend raises a
connection error (e.g. Redis is unreachable while ``USE_REDIS_CACHE=True``), the
read is treated as a miss and the write is skipped instead of bubbling up a 500.
A missing ``redis`` package degrades the same way -- see
``_cache_failure_exceptions``.

The ``redis`` package is imported lazily so this module can be imported in
environments where Redis is not installed (dev/CI run with LocMemCache).
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def _cache_failure_exceptions():
    """Return the exception types that mean "cache backend is unavailable".

    ``redis`` ships in ``prod.txt`` only, so a dev or CI box that sets
    ``USE_REDIS_CACHE=True`` without it fails at the first cache call with
    ``ModuleNotFoundError``. That is an ``ImportError``, not an ``OSError``:
    without it here the wrappers below re-raise and the caller answers 500,
    which is the opposite of what these helpers exist for.

    ``ImportError`` is therefore always in the tuple, and the ``redis``
    exceptions are added on top only when the package is importable.
    """
    base = (ImportError, ConnectionError, OSError)
    try:
        from redis.exceptions import RedisError
    except ImportError:  # pragma: no cover - redis not installed
        return base

    return (RedisError, *base)


def safe_cache_get(key, default=None):
    """``cache.get`` that degrades to ``default`` on a backend failure."""
    try:
        return cache.get(key, default)
    except _cache_failure_exceptions() as exc:
        logger.warning('Cache GET failed for %s; serving uncached. %s', key, exc)
        return default


def safe_cache_set(key, value, timeout):
    """``cache.set`` that silently skips the write on a backend failure."""
    try:
        cache.set(key, value, timeout)
    except _cache_failure_exceptions() as exc:
        logger.warning('Cache SET failed for %s; skipping write. %s', key, exc)


def safe_cache_delete(key):
    """``cache.delete`` that swallows backend failures.

    Se llama despues de una escritura que ya se guardo: un cache inalcanzable
    no puede deshacer una fila marcada como leida, asi que fallar aca
    convertiria una accion exitosa en un 500.
    """
    try:
        cache.delete(key)
    except _cache_failure_exceptions() as exc:
        logger.warning('Cache DELETE failed for %s; value stays until TTL. %s', key, exc)


def build_api_cache_key(request):
    """Build a stable cache key for a read-only API request.

    Key format: ``api:{request.path}:{normalized_sorted_query_string}``.
    The query string is normalized (keys and values sorted) so that two requests
    differing only in query-param order resolve to the same key, while distinct
    param values resolve to distinct keys.
    """
    items = []
    for key in sorted(request.GET):
        for value in sorted(request.GET.getlist(key)):
            items.append(f'{key}={value}')
    normalized = '&'.join(items)
    if normalized:
        return f'api:{request.path}:{normalized}'
    return f'api:{request.path}:'
