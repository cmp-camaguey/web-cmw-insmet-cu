"""Notificaciones operativas del panel (cambio 026, fase 3).

Cinco vistas y un solo destinatario posible: el superusuario autenticado. Las
notificaciones de 026 son operativas —una tarea que fallo, una factura que no se
pudo generar, un aviso por vencer—, asi que un usuario normal no tiene nada que
ver aqui, y el queryset siempre se filtra por `recipient=request.user` ademas del
permiso. Un superusuario no puede leer los avisos de otro aunque los dos sean
administradores: cada fila tiene un destinatario concreto.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView, View

from apps.core.models import Notification
from apps.core.notifications import unread_count, visible_for


def _redirect_back(request):
    """Vuelve a donde estaba el usuario, sin aceptar destinos externos.

    Se prefiere `next` del formulario y se cae al `Referer`: el mismo boton
    "marcar leida" vive en el desplegable del navbar, que se abre en cualquier
    pagina del panel, y obligar a volver siempre al historial seria peor.
    """
    fallback = reverse('dashboard:notifications')
    target = request.POST.get('next') or request.META.get('HTTP_REFERER') or fallback
    if url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return fallback


class NotificationListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """Historial paginado de los avisos del usuario.

    Sin DataTables: solo se usan cuando se cargan todos los registros y aqui hay
    un historial que crece. `paginate_by = 10` es la convencion del proyecto.
    """

    template_name = 'pages/dashboard/notifications.html'
    context_object_name = 'notifications'
    paginate_by = 10
    raise_exception = True

    def test_func(self):
        return self.request.user.is_superuser

    def get_queryset(self):
        return visible_for(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Notificaciones'
        context['parent'] = 'Panel'
        context['segment'] = 'notifications'
        context['unread'] = unread_count(self.request.user) or 0
        return context


class NotificationReadView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Marca como leido un aviso concreto. Solo POST: leer no deberia cambiar nada."""

    raise_exception = True
    http_method_names = ['post']

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, uuid):
        notification = get_object_or_404(Notification, uuid=uuid, recipient=request.user)
        if notification.read_at is None:
            # `update_fields` para no reescribir una fila que puede venir de un
            # `created_at` que no cambia: solo se toca lo que se pidio.
            notification.read_at = timezone.now()
            notification.save(update_fields=['read_at'])
        # El clic en el desplegable dispara un POST de fondo y navega por el
        # enlace: un 302 ahi haria que el navegador descargue la pagina destino
        # dos veces. 204 cierra la peticion sin redireccion; el POST normal del
        # historial sigue recibiendo el redirect de siempre.
        if request.headers.get('X-Requested-With') == 'fetch':
            return HttpResponse(status=204)
        return redirect(_redirect_back(request))


class NotificationReadAllView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Marca como leidos todos los avisos sin leer del usuario."""

    raise_exception = True
    http_method_names = ['post']

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request):
        updated = Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(
            read_at=timezone.now()
        )
        if updated:
            messages.success(
                request,
                f'{updated} notificación{"es" if updated != 1 else ""} marcada'
                f'{"s" if updated != 1 else ""} como leída{"s" if updated != 1 else ""}.',
            )
        return redirect(_redirect_back(request))


class NotificationDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Elimina una notificacion concreta. Solo POST: borrar no es idempotente
    desde la perspectiva del usuario y no puede dispararse con un GET que
    prefetchee un navegador o un bot.
    """

    raise_exception = True
    http_method_names = ['post']

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, uuid):
        notification = get_object_or_404(Notification, uuid=uuid, recipient=request.user)
        notification.delete()
        return redirect(_redirect_back(request))


class NotificationDeleteAllView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Elimina todas las notificaciones del usuario, leidas y sin leer.

    Borra tambien las vencidas que `visible_for` no muestra: el usuario pidio
    vaciar su bandeja, no conservar lo que ya no ve.
    """

    raise_exception = True
    http_method_names = ['post']

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request):
        deleted, _ = Notification.objects.filter(recipient=request.user).delete()
        if deleted:
            messages.success(
                request,
                f'{deleted} notificación{"es" if deleted != 1 else ""} eliminada'
                f'{"s" if deleted != 1 else ""}.',
            )
        return redirect(_redirect_back(request))
