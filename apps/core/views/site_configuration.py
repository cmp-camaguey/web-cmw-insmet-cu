from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import CHANGE
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from apps.core.forms.site_configuration import SiteConfigurationForm
from apps.core.models import ActivityLog, SiteConfiguration
from apps.core.utils import log_action


class SiteConfigurationUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = SiteConfiguration
    form_class = SiteConfigurationForm
    template_name = 'pages/core/site/settings.html'
    success_url = reverse_lazy('core:site_configuration')
    permission_required = 'core.change_siteconfiguration'

    def get_object(self, queryset=None):
        return SiteConfiguration.get_instance()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Configuración del sitio'
        context['parent'] = 'configuracion'
        context['segment'] = 'core:site_configuration'
        context['url_list'] = reverse_lazy('core:site_configuration')
        context['server_timezone'] = settings.TIME_ZONE
        return context

    def post(self, request, *args, **kwargs):
        if 'delete_logo' in request.POST:
            site = self.get_object()
            site.brand_logo.delete(save=False)
            site.save()
            log_action(
                user=request.user,
                obj=site,
                action_flag=CHANGE,
                message='Logo del sitio eliminado',
            )
            messages.success(
                self.request, 'El logo ha sido eliminado con éxito.', extra_tags='danger'
            )
            return redirect(self.success_url)
        if 'delete_favicon' in request.POST:
            site = self.get_object()
            site.favicon.delete(save=False)
            site.save()
            log_action(
                user=request.user,
                obj=site,
                action_flag=CHANGE,
                message='Favicon del sitio eliminado',
            )
            messages.success(
                self.request, 'El favicon ha sido eliminado con éxito.', extra_tags='danger'
            )
            return redirect(self.success_url)
        if 'reset_theme_defaults' in request.POST:
            site = self.get_object()
            site.primary_color = '#2b4b9b'
            site.theme_base = 'neutral'
            site.theme_font = 'sans-serif'
            site.theme_radius = '1'
            site.save()
            log_action(
                user=request.user,
                obj=site,
                action_flag=CHANGE,
                message='Tema restablecido a los valores por defecto',
            )
            messages.success(
                self.request, 'El tema ha sido restablecido a los valores por defecto.'
            )
            return redirect(self.success_url)
        if 'toggle_maintenance' in request.POST:
            # Instant AJAX toggle from the maintenance switch (superuser only).
            if not request.user.is_superuser:
                return JsonResponse(
                    {'ok': False, 'error': 'Solo los superusuarios pueden cambiar el modo.'},
                    status=403,
                )
            site = self.get_object()
            site.maintenance_mode = request.POST.get('maintenance_mode') == '1'
            site.save()
            verb = 'activó' if site.maintenance_mode else 'desactivó'
            log_action(
                user=request.user,
                obj=site,
                action_flag=ActivityLog.ACTIVITY_MAINTENANCE,
                message=f'El usuario {request.user.username} {verb} el modo de mantenimiento.',
            )
            return JsonResponse({'ok': True, 'maintenance_mode': site.maintenance_mode})
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        self.object = form.save()
        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=CHANGE,
            message='Configuración del sitio actualizada',
        )
        messages.success(self.request, 'Configuración del sitio guardada correctamente.')
        return redirect(self.success_url)
