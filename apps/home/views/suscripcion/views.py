"""Suscripción pública al boletín: alta con doble opt-in, confirmación y baja.

Las tres vistas son públicas y no requieren cuenta. La única que escribe es la
de alta, y siempre a través de `apps.core.subscriptions`.

Ver `openspec/specs/boletin-y-suscripcion/spec.md`.
"""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views import View

from apps.core.models import Subscriber
from apps.core.subscriptions import confirm, send_confirmation, subscribe, unsubscribe
from apps.core.utils import client_ip

DEFAULT_SUBSCRIBE_LIMIT = 5
SUBSCRIBE_WINDOW = 3600

SUBSCRIBED_MESSAGE = (
    'Si el correo es válido, recibirá un mensaje para confirmar la suscripción. '
    'Revise también la carpeta de correo no deseado.'
)


class NewsletterSubscribeForm(forms.Form):
    email = forms.EmailField()

    def clean_email(self):
        return (self.cleaned_data.get('email') or '').strip().lower()


def _rate_limited(ip):
    """Límite por IP en cache.

    La IP sale de `apps.core.utils.client_ip`, no de este módulo: detrás de Nginx
    `REMOTE_ADDR` es el proxy y la primera entrada de `X-Forwarded-For` la
    escribe el cliente, así que leerla aquí dejaría el límite a un encabezado de
    distancia.

    El conteo usa `add` + `incr` y no `get` + `set`: la lectura y la escritura
    separadas pierden incrementos concurrentes y dejan pasar más altas que
    `limit`. Con `add` el TTL se fija una sola vez, así que la ventana es fija y
    no se corre hacia adelante con cada intento.

    Sin Redis el cache es LocMem y el límite es por proceso: es best-effort y
    así se documenta. Con Redis (producción) es compartido entre workers.
    """
    if not ip:
        return False
    limit = getattr(settings, 'NEWSLETTER_SUBSCRIBE_LIMIT', DEFAULT_SUBSCRIBE_LIMIT)
    key = f'newsletter:subscribe:{ip}'
    cache.add(key, 0, timeout=SUBSCRIBE_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:
        # La clave venció entre el `add` y el `incr`.
        cache.set(key, 1, timeout=SUBSCRIBE_WINDOW)
        count = 1
    return count > limit


class NewsletterSubscribeView(View):
    def post(self, request):
        if _rate_limited(client_ip(request)):
            messages.error(
                request,
                'Demasiados intentos desde su conexión. Inténtelo de nuevo más tarde.',
            )
            return redirect('home:landing')

        form = NewsletterSubscribeForm(request.POST)
        if not form.is_valid():
            # Validación de servidor, no solo del navegador: un `type="email"`
            # se salta con una petición directa.
            messages.error(request, 'Escriba un correo electrónico válido.')
            return redirect('home:landing')

        subscriber, _created, needs_confirmation = subscribe(
            form.cleaned_data['email'], source=Subscriber.Source.LANDING
        )
        if needs_confirmation and subscriber is not None:
            send_confirmation(subscriber)

        # La misma respuesta para correo nuevo, existente o pendiente: no se
        # revela qué direcciones están en la lista.
        messages.success(request, SUBSCRIBED_MESSAGE)
        return redirect('home:landing')

    def get(self, request):
        return redirect('home:landing')


class NewsletterConfirmView(View):
    def get(self, request, token):
        subscriber, status = confirm(token)
        return render(
            request,
            'pages/home/suscripcion/confirm.html',
            {'status': status, 'subscriber': subscriber},
        )


class NewsletterUnsubscribeView(View):
    def get(self, request, token):
        subscriber = unsubscribe(token)
        return render(
            request,
            'pages/home/suscripcion/unsubscribe.html',
            {'subscriber': subscriber, 'valid': subscriber is not None},
        )
