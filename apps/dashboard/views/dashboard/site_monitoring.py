"""Panel de monitoreo del sitio.

Dos vistas con la misma regla de acceso que "Monitoreo de tareas": sólo
superusuarios. Una pinta la página; la otra entrega el mismo snapshot en JSON
para el auto-refresh de 5 segundos.

El snapshot se arma una sola vez por petición en `apps.monitoring.services`.
La vista no agrega lógica propia: si el panel y el JSON se armaran por caminos
distintos, tarde o temprano mostrarían números diferentes.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.urls import reverse
from django.views.generic import TemplateView, View

from apps.monitoring.services.snapshot import build_snapshot

AUTO_REFRESH_MS = 5000


class SiteMonitoringView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'pages/dashboard/monitoring.html'

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': 'Monitoreo del sitio',
                'parent': 'Dashboard',
                'segment': 'site_monitoring',
                'snapshot': build_snapshot(),
                'refresh_ms': AUTO_REFRESH_MS,
                'data_url': reverse('dashboard:monitoring_data'),
            }
        )
        return context


class SiteMonitoringDataView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, *args, **kwargs):
        return JsonResponse(build_snapshot())
