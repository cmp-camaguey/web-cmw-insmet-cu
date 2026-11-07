"""Contador de avisos sin leer en su propio endpoint (cambio 026).

El numero vivia dentro del JSON de estado de despliegue del panel. Ahi no puede
quedarse: ese JSON arrastra `deployed_sha()`, `remote_sha()` y `last_run()` en
cada peticion, y lo que en produccion van a consultar cientos de navegadores con
la campana del portal (cambio 030) tiene que ser un `COUNT` indexado y nada mas.
Separarlo es barato ahora y caro despues: el transporte se cambia cuando haga
falta, el acoplamiento con las actualizaciones no se deshace solo.
"""

import hashlib
import json

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse, HttpResponseNotModified
from django.views.generic import View

from apps.core.notifications import unread_for


def _etag_for(payload):
    """ETag del cuerpo ya serializado.

    Se deriva del JSON final, no del conteo: asi el `304` y el `200` no pueden
    discrepar, porque no hay dos fuentes que puedan calcular cosas distintas.
    """
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return f'"{digest[:32]}"'


class NotificationUnreadView(LoginRequiredMixin, UserPassesTestMixin, View):
    """`GET /notificaciones/sin-leer/` -> `{"unread": n}`.

    Solo superusuario, igual que las demas vistas de 026: hoy los avisos son
    operativos y un usuario normal no tiene nada que ver aqui. El permiso vive
    en la vista y no en `unread_for`, para que el cambio 030 solo tenga que
    cambiar este `test_func`.

    `ETag` + `304` no ahorran la peticion; ahorran el cuerpo. Con un intervalo
    de refresco de 20 s la mayoria de las respuestas no cambia, y devolver 0
    bytes sin parsear JSON es la diferencia entre un sondeo barato y uno que se
    nota en una red lenta.
    """

    raise_exception = True
    http_method_names = ['get']

    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, *args, **kwargs):
        payload = json.dumps({'unread': unread_for(request.user)}, separators=(',', ':'))
        etag = _etag_for(payload)

        if request.headers.get('If-None-Match') == etag:
            response = HttpResponseNotModified()
        else:
            response = HttpResponse(payload, content_type='application/json')

        # `private`: lleva el conteo de una persona y no puede quedar en un cache
        # intermedio. `no-cache` con revalidacion obligatoria, para que el
        # navegador pregunte en vez de servir su copia sin consultar.
        response['Cache-Control'] = 'private, no-cache'
        response['ETag'] = etag
        return response
