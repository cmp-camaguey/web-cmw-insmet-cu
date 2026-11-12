"""URLs de notificaciones, fuera del prefijo de configuracion.

`apps/core/urls.py` se monta bajo `/config/`, que es un prefijo de
administracion con vistas de formulario. El contador no pertenece ahi: lo
consulta el JS desde cualquier pagina del panel, y desde el cambio 030 tambien
la campana del cliente, que solo necesita su propio conteo.
"""

from django.urls import path

from apps.core.views.notifications import NotificationUnreadView

app_name = 'notifications'

urlpatterns = [
    path('sin-leer/', NotificationUnreadView.as_view(), name='unread'),
]
