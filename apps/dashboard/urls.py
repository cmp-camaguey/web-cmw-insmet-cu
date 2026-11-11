from django.urls import path

from apps.dashboard.views.dashboard.dashboard import (
    DashboardView,
    TaskMonitoringActionView,
    TaskMonitoringView,
)
from apps.dashboard.views.dashboard.notifications import (
    NotificationDeleteAllView,
    NotificationDeleteView,
    NotificationListView,
    NotificationReadAllView,
    NotificationReadView,
)
from apps.dashboard.views.dashboard.site_monitoring import (
    SiteMonitoringDataView,
    SiteMonitoringView,
)
from apps.dashboard.views.dashboard.update import UpdateRequestView, UpdateStatusView

app_name = 'dashboard'

urlpatterns = [
    # Dashboard
    path('', DashboardView.as_view(), name='index'),
    # Monitoreo de tareas asíncronas (Huey)
    path('tasks/', TaskMonitoringView.as_view(), name='tasks'),
    # Acciones sobre un registro de tarea (reintentar/limpiar)
    path('tasks/<uuid:uuid>/action/', TaskMonitoringActionView.as_view(), name='tasks_action'),
    # Monitoreo del sitio en tiempo real (feature 034)
    path(
        'monitoring/',
        SiteMonitoringView.as_view(),
        name='monitoring',
    ),
    path(
        'monitoring/data/',
        SiteMonitoringDataView.as_view(),
        name='monitoring_data',
    ),
    # Actualizaciones del servidor (estado y petición de despliegue)
    path('update/', UpdateStatusView.as_view(), name='update_status'),
    path('update/request/', UpdateRequestView.as_view(), name='update_request'),
    # Notificaciones operativas (change 026)
    path('notifications/', NotificationListView.as_view(), name='notifications'),
    path(
        'notifications/read-all/',
        NotificationReadAllView.as_view(),
        name='notifications_read_all',
    ),
    path(
        'notifications/delete-all/',
        NotificationDeleteAllView.as_view(),
        name='notifications_delete_all',
    ),
    path(
        'notifications/<uuid:uuid>/read/',
        NotificationReadView.as_view(),
        name='notification_read',
    ),
    path(
        'notifications/<uuid:uuid>/delete/',
        NotificationDeleteView.as_view(),
        name='notification_delete',
    ),
]
