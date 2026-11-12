"""Middleware que mide cada petición servida.

Se coloca justo después de WhiteNoise: los estáticos salen del stack antes de
llegar acá, así que no ensucian la muestra, y a la vez el cronómetro envuelve
todo lo demás (sesiones, auth, CSRF, vista, plantilla).

Lo que se mide es el tiempo del backend hasta entregar la respuesta: es el
componente de TTFB que depende de este servidor, no el TTFB percibido por el
usuario (que incluye red y navegador).
"""

import logging
import time

from apps.monitoring.services import http as http_metrics

logger = logging.getLogger(__name__)


class RequestMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        duration_ms = (time.perf_counter() - started) * 1000
        # Nunca se deja que la medición rompa la petición medida. El servicio
        # ya atrapa sus propios errores; esto cubre cualquier otra cosa.
        try:
            http_metrics.record_request(request, response.status_code, duration_ms)
        except Exception:
            logger.warning('monitoring: fallo inesperado al registrar la petición', exc_info=True)
        return response
