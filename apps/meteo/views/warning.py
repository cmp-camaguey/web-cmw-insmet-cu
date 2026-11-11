from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, UpdateView, View

from apps.core.utils import log_action, mail_send
from apps.core.views import ServeModelFileView
from apps.meteo.forms.warning import WarningForm
from apps.meteo.models import Warning

WARNING_CONFIG = {
    'early': {
        'segment': 'early',
        'title_create': 'Añadir Alerta Temprana',
        'title_list': 'Alertas Tempranas',
        'title_update': 'Actualizar Aviso Alerta Temprana',
        'btn': 'Añadir Alerta Temprana',
        'url_list': 'meteo:alerta_temprana_list',
        'url_create': 'meteo:alerta_temprana_create',
        'url_update': 'meteo:alerta_temprana_update',
        'url_delete': 'meteo:alerta_temprana_delete',
        'template_list': 'pages/meteo/warning/early_warning/list.html',
        'template_create': 'pages/meteo/warning/early_warning/create.html',
        'template_update': 'pages/meteo/warning/early_warning/update.html',
        'subject_create': 'Alerta Temprana',
        'subject_update': 'Alerta Temprana Actualizado',
        'mail_url': 'home:warnings_early',
    },
    'storm': {
        'segment': 'storm',
        'title_create': 'Añadir Aviso de Tormenta',
        'title_list': 'Avisos de Tormentas',
        'title_update': 'Actualizar Aviso de Tormenta',
        'btn': 'Añadir Aviso de Tormenta',
        'url_list': 'meteo:tormenta_list',
        'url_create': 'meteo:tormenta_create',
        'url_update': 'meteo:tormenta_update',
        'url_delete': 'meteo:tormenta_delete',
        'template_list': 'pages/meteo/warning/storm/list.html',
        'template_create': 'pages/meteo/warning/storm/create.html',
        'template_update': 'pages/meteo/warning/storm/update.html',
        'subject_create': 'Aviso de Tormentas',
        'subject_update': 'Aviso de Tormentas Actualizado',
        'mail_url': 'home:warnings_storm',
    },
    'tropical_cyclone': {
        'segment': 'cyclone',
        'title_create': 'Añadir Aviso Ciclón Tropical',
        'title_list': 'Ciclones Tropicales',
        'title_update': 'Actualizar Aviso Ciclón Tropical',
        'btn': 'Añadir Aviso Ciclón Tropical',
        'url_list': 'meteo:ciclon_tropical_list',
        'url_create': 'meteo:ciclon_tropical_create',
        'url_update': 'meteo:ciclon_tropical_update',
        'url_delete': 'meteo:ciclon_tropical_delete',
        'template_list': 'pages/meteo/warning/tropical_cyclone/list.html',
        'template_create': 'pages/meteo/warning/tropical_cyclone/create.html',
        'template_update': 'pages/meteo/warning/tropical_cyclone/update.html',
        'subject_create': 'Aviso de Ciclon Tropical',
        'subject_update': 'Aviso de Ciclon Tropical Actualizado',
        'mail_url': 'home:warnings_tropical',
    },
}


class WarningListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Warning
    parent = 'avisos'

    def get_warning_type(self):
        return self.kwargs.get('warning_type', 'early')

    def get_config(self):
        return WARNING_CONFIG[self.get_warning_type()]

    @property
    def permission_required(self):
        return 'meteo.view_warning'

    def get_template_names(self):
        return [self.get_config()['template_list']]

    def get_queryset(self):
        return Warning.objects.select_related('user').filter(warning_type=self.get_warning_type())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cfg = self.get_config()
        context['title'] = cfg['title_list']
        context['parent'] = self.parent
        context['segment'] = cfg['segment']
        context['btn'] = cfg['btn']
        context['url_create'] = reverse_lazy(cfg['url_create'])
        context['url_list'] = reverse_lazy(cfg['url_list'])
        context['is_superuser'] = self.request.user.is_superuser
        context['objects'] = self.get_queryset()
        context['now'] = timezone.now()
        return context


class WarningCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Warning
    form_class = WarningForm
    parent = 'avisos'

    def get_warning_type(self):
        return self.kwargs.get('warning_type', 'early')

    def get_config(self):
        return WARNING_CONFIG[self.get_warning_type()]

    @property
    def permission_required(self):
        return 'meteo.add_warning'

    def get_template_names(self):
        return [self.get_config()['template_create']]

    def get_success_url(self):
        return reverse_lazy(self.get_config()['url_list'])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['warning_type'] = self.get_warning_type()
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        cfg = self.get_config()

        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=ADDITION,
            message=(
                f'Se creó un nuevo {cfg["title_create"]}: {self.object.date.strftime("%d-%m-%Y")}.'
            ),
        )

        messages.success(
            self.request, f'{cfg["title_create"]} ha sido creado con éxito.', extra_tags='success'
        )
        if form.cleaned_data.get('send_email'):
            mail_send(
                self.request,
                self.object,
                cfg['subject_create'],
                cfg['mail_url'],
                attachment_name=f'alerta_{self.object.date.strftime("%Y-%m-%d")}.pdf',
            )

        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cfg = self.get_config()
        context['title'] = cfg['title_create']
        context['parent'] = self.parent
        context['segment'] = cfg['segment']
        context['url_list'] = reverse_lazy(cfg['url_list'])
        return context


class WarningUpdateView(
    LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin, UpdateView
):
    model = Warning
    form_class = WarningForm
    parent = 'avisos'

    def get_warning_type(self):
        return self.kwargs.get('warning_type', 'early')

    def get_config(self):
        return WARNING_CONFIG[self.get_warning_type()]

    @property
    def permission_required(self):
        return 'meteo.change_warning'

    def get_template_names(self):
        return [self.get_config()['template_update']]

    def get_success_url(self):
        return reverse_lazy(self.get_config()['url_list'])

    def get_object(self, queryset=None):
        uuid = self.kwargs.get('uuid')
        return get_object_or_404(Warning, uuid=uuid)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['warning_type'] = self.get_warning_type()
        return kwargs

    def form_valid(self, form):
        cfg = self.get_config()

        response = super().form_valid(form)

        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=CHANGE,
            message=f'Se actualizó {cfg["title_update"]}: {self.object.date.strftime("%d-%m-%Y")}.',
        )

        if form.cleaned_data.get('send_email'):
            mail_send(
                self.request,
                self.object,
                cfg['subject_update'],
                cfg['mail_url'],
                attachment_name=f'alerta_{self.object.date.strftime("%Y-%m-%d")}.pdf',
            )

        messages.success(
            self.request,
            f'{cfg["title_update"]} ha sido actualizado con éxito.',
            extra_tags='success',
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cfg = self.get_config()
        context['title'] = cfg['title_update']
        context['parent'] = self.parent
        context['segment'] = cfg['segment']
        context['url_list'] = reverse_lazy(cfg['url_list'])
        return context

    def test_func(self):
        return self.request.user.is_superuser or self.get_object().user == self.request.user


class WarningDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'meteo.delete_warning'

    def get_warning_type(self):
        return self.kwargs.get('warning_type', 'early')

    def get_config(self):
        return WARNING_CONFIG[self.get_warning_type()]

    def post(self, request, uuid, *args, **kwargs):
        warning = get_object_or_404(Warning, uuid=uuid)
        cfg = self.get_config()
        log_action(
            user=self.request.user,
            obj=warning,
            action_flag=DELETION,
            message=f'Se eliminó {cfg["title_list"]}: {warning.date.strftime("%d-%m-%Y")}.',
        )
        try:
            warning.delete()
            messages.success(request, f'{cfg["title_list"]} ha sido eliminado con éxito.')
        except Exception as e:
            messages.error(request, str(e))
        return redirect(cfg['url_list'])


class WarningPDFDownloadView(ServeModelFileView):
    model = Warning
    field = 'file'
    permission_required = 'meteo.view_warning'

    def get_filename(self, obj):
        return f'alerta_{obj.date:%Y-%m-%d}.pdf'
