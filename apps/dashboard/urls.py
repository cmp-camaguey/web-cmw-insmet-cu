from django.urls import path

from apps.dashboard.views.dashboard.dashboard import (
    DashboardView,
    TaskMonitoringActionView,
    TaskMonitoringView,
)
from apps.dashboard.views.dashboard.notifications import (
    NotificationListView,
    NotificationReadAllView,
    NotificationReadView,
)
from apps.dashboard.views.dashboard.update import UpdateRequestView, UpdateStatusView

app_name = 'dashboard'

urlpatterns = [
    # Dashboard
    path('', DashboardView.as_view(), name='index'),
    # Monitoreo de tareas asíncronas (Huey)
    path('tasks/', TaskMonitoringView.as_view(), name='tasks'),
    # Acciones sobre un registro de tarea (reintentar/limpiar)
    path('tasks/<int:pk>/action/', TaskMonitoringActionView.as_view(), name='tasks_action'),
    # Actualizaciones del servidor (estado y petición de despliegue)
    path('update/', UpdateStatusView.as_view(), name='update_status'),
    path('update/request/', UpdateRequestView.as_view(), name='update_request'),
    # Avisos operativos (change 026)
    path('notifications/', NotificationListView.as_view(), name='notifications'),
    path(
        'notifications/read-all/',
        NotificationReadAllView.as_view(),
        name='notifications_read_all',
    ),
    path(
        'notifications/<uuid:uuid>/read/',
        NotificationReadView.as_view(),
        name='notification_read',
    ),
]
