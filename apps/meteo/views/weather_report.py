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
from apps.meteo.forms.weather_report import WeatherReportForm
from apps.meteo.models import WeatherReport

REPORT_CONFIG = {
    'today': {
        'perm_prefix': 'weather_today',
        'segment': 'tiempo_h',
        'parent': 'tiempo',
        'title_create': 'Añadir Tiempo para Hoy',
        'title_list': 'Listado del Tiempo para Hoy',
        'title_update': 'Actualizar Tiempo para Hoy',
        'title_detail': 'Detalle Tiempo para Hoy',
        'btn': 'Añadir Tiempo para Hoy',
        'url_list': 'meteo:tiempo_hoy_list',
        'url_create': 'meteo:tiempo_hoy_create',
        'url_update': 'meteo:tiempo_hoy_update',
        'url_delete': 'meteo:tiempo_hoy_delete',
        'template_list': 'pages/meteo/weather_report/today/list.html',
        'template_create': 'pages/meteo/weather_report/today/create.html',
        'template_update': 'pages/meteo/weather_report/today/update.html',
        'template_pdf': 'pages/meteo/weather_report/today/pdf.html',
        'subject_create': 'El Tiempo para Hoy',
        'subject_update': 'El Tiempo para Hoy Actualizado',
        'pdf_filename_prefix': 'tiempo_hoy',
        'context_name': 'weather_today',
        'mail_url': 'home:weather_today',
    },
    'tomorrow': {
        'perm_prefix': 'weather_tomorrow',
        'segment': 'tiempo_m',
        'parent': 'tiempo',
        'title_create': 'Añadir Tiempo para Mañana',
        'title_list': 'Listado del Tiempo para Mañana',
        'title_update': 'Actualizar Tiempo para Mañana',
        'title_detail': 'Detalle Tiempo para Mañana',
        'btn': 'Añadir Tiempo para Mañana',
        'url_list': 'meteo:tiempo_manana_list',
        'url_create': 'meteo:tiempo_manana_create',
        'url_update': 'meteo:tiempo_manana_update',
        'url_delete': 'meteo:tiempo_manana_delete',
        'template_list': 'pages/meteo/weather_report/tomorrow/list.html',
        'template_create': 'pages/meteo/weather_report/tomorrow/create.html',
        'template_update': 'pages/meteo/weather_report/tomorrow/update.html',
        'template_pdf': 'pages/meteo/weather_report/tomorrow/pdf.html',
        'subject_create': 'El Tiempo para Mañana',
        'subject_update': 'El Tiempo para Mañana Actualizado',
        'pdf_filename_prefix': 'tiempo_manana',
        'context_name': 'weather_tomorrow',
        'mail_url': 'home:weather_tomorrow',
    },
    'commentary': {
        'perm_prefix': 'weather_commentary',
        'segment': 'comentario_tiempo',
        'parent': 'comentario',
        'title_create': 'Añadir Comentario del Tiempo',
        'title_list': 'Listado de Comentarios del Tiempo',
        'title_update': 'Actualizar Comentario del Tiempo',
        'title_detail': 'Detalle Comentario del Tiempo',
        'btn': 'Añadir Comentario del Tiempo',
        'url_list': 'meteo:comentario_tiempo_list',
        'url_create': 'meteo:comentario_tiempo_create',
        'url_update': 'meteo:comentario_tiempo_update',
        'url_delete': 'meteo:comentario_tiempo_delete',
        'template_list': 'pages/meteo/weather_report/commentaries/weather/list.html',
        'template_create': 'pages/meteo/weather_report/commentaries/weather/create.html',
        'template_update': 'pages/meteo/weather_report/commentaries/weather/update.html',
        'template_pdf': 'pages/meteo/weather_report/commentaries/weather/pdf.html',
        'subject_create': 'Comentario del Tiempo',
        'subject_update': 'Comentario del Tiempo Actualizado',
        'pdf_filename_prefix': 'comentario_tiempo',
        'context_name': 'weather_commentary',
        'mail_url': 'home:weather_commentary',
    },
    'note': {
        'perm_prefix': 'weather_note',
        'segment': 'nota_meteorologica',
        'parent': 'comentario',
        'title_create': 'Añadir Nota Meteorológica',
        'title_list': 'Listado de Notas Meteorológicas',
        'title_update': 'Actualizar Nota Meteorológica',
        'title_detail': 'Detalle Nota Meteorológica',
        'btn': 'Añadir Nota Meteorológica',
        'url_list': 'meteo:nota_meteorologica_list',
        'url_create': 'meteo:nota_meteorologica_create',
        'url_update': 'meteo:nota_meteorologica_update',
        'url_delete': 'meteo:nota_meteorologica_delete',
        'template_list': 'pages/meteo/weather_report/commentaries/notes/list.html',
        'template_create': 'pages/meteo/weather_report/commentaries/notes/create.html',
        'template_update': 'pages/meteo/weather_report/commentaries/notes/update.html',
        'template_pdf': 'pages/meteo/weather_report/commentaries/notes/pdf.html',
        'subject_create': 'Nota Meteorológica',
        'subject_update': 'Nota Meteorológica Actualizada',
        'pdf_filename_prefix': 'nota_meteorologica',
        'context_name': 'weather_note',
        'mail_url': 'home:weather_note',
    },
}


class WeatherReportListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = WeatherReport

    def get_report_type(self):
        return self.kwargs.get('report_type', 'today')

    def get_config(self):
        return REPORT_CONFIG[self.get_report_type()]

    @property
    def permission_required(self):
        cfg = self.get_config()
        return f'meteo.view_{cfg["perm_prefix"]}'

    def get_template_names(self):
        return [self.get_config()['template_list']]

    def get_queryset(self):
        return WeatherReport.objects.filter(report_type=self.get_report_type()).select_related(
            'user'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cfg = self.get_config()
        context['title'] = cfg['title_list']
        context['parent'] = cfg['parent']
        context['segment'] = cfg['segment']
        context['btn'] = cfg['btn']
        context['url_create'] = reverse_lazy(cfg['url_create'])
        context['url_list'] = reverse_lazy(cfg['url_list'])
        context['is_superuser'] = self.request.user.is_superuser
        context['objects'] = self.get_queryset()
        return context


class WeatherReportCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = WeatherReport
    form_class = WeatherReportForm

    def get_report_type(self):
        return self.kwargs.get('report_type', 'today')

    def get_config(self):
        return REPORT_CONFIG[self.get_report_type()]

    @property
    def permission_required(self):
        cfg = self.get_config()
        return f'meteo.add_{cfg["perm_prefix"]}'

    def get_template_names(self):
        return [self.get_config()['template_create']]

    def get_success_url(self):
        return reverse_lazy(self.get_config()['url_list'])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['report_type'] = self.get_report_type()
        return kwargs

    def form_valid(self, form):
        form.instance.date = timezone.now()
        self.object = form.save()
        cfg = self.get_config()

        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=ADDITION,
            message=f'Se creó un nuevo {cfg["title_create"]}: {self.object.date}.',
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
                attachment_name=f'{cfg["pdf_filename_prefix"]}_{self.object.date.strftime("%Y-%m-%d")}.pdf',
            )

        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cfg = self.get_config()
        context['title'] = cfg['title_create']
        context['parent'] = cfg['parent']
        context['segment'] = cfg['segment']
        context['url_list'] = reverse_lazy(cfg['url_list'])
        return context


class WeatherReportUpdateView(
    LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin, UpdateView
):
    model = WeatherReport
    form_class = WeatherReportForm

    def get_report_type(self):
        return self.kwargs.get('report_type', 'today')

    def get_config(self):
        return REPORT_CONFIG[self.get_report_type()]

    @property
    def permission_required(self):
        cfg = self.get_config()
        return f'meteo.change_{cfg["perm_prefix"]}'

    def get_template_names(self):
        return [self.get_config()['template_update']]

    def get_success_url(self):
        return reverse_lazy(self.get_config()['url_list'])

    def get_object(self, queryset=None):
        uuid = self.kwargs.get('uuid')
        return get_object_or_404(WeatherReport, uuid=uuid)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['report_type'] = self.get_report_type()
        return kwargs

    def form_valid(self, form):
        cfg = self.get_config()

        form.instance.date = timezone.now()
        self.object = form.save()

        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=CHANGE,
            message=f'Se actualizó {cfg["title_update"]}: {self.object.date}.',
        )

        if form.cleaned_data.get('send_email'):
            mail_send(
                self.request,
                self.object,
                cfg['subject_update'],
                cfg['mail_url'],
                attachment_name=f'{cfg["pdf_filename_prefix"]}_{self.object.date.strftime("%Y-%m-%d")}.pdf',
            )

        messages.success(
            self.request,
            f'{cfg["title_update"]} ha sido actualizado con éxito.',
            extra_tags='success',
        )
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cfg = self.get_config()
        context['title'] = cfg['title_update']
        context['parent'] = cfg['parent']
        context['segment'] = cfg['segment']
        context['url_list'] = reverse_lazy(cfg['url_list'])
        return context

    def test_func(self):
        return self.request.user.is_superuser or self.get_object().user == self.request.user


class WeatherReportDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    def get_report_type(self):
        return self.kwargs.get('report_type', 'today')

    def get_config(self):
        return REPORT_CONFIG[self.get_report_type()]

    @property
    def permission_required(self):
        cfg = self.get_config()
        return f'meteo.delete_{cfg["perm_prefix"]}'

    def post(self, request, uuid, *args, **kwargs):
        obj = get_object_or_404(WeatherReport, uuid=uuid)
        cfg = self.get_config()
        log_action(
            user=self.request.user,
            obj=obj,
            action_flag=DELETION,
            message=f'Se eliminó {cfg["title_list"]}: {obj.date.strftime("%d-%m-%Y")}.',
        )
        try:
            obj.delete()
            messages.success(request, f'{cfg["title_list"]} ha sido eliminado con éxito.')
        except Exception as e:
            messages.error(request, str(e))
        return redirect(cfg['url_list'])


class WeatherReportFileDownloadView(ServeModelFileView):
    model = WeatherReport
    field = 'file'

    _PREFIX_MAP = {
        'today': 'tiempo_hoy',
        'tomorrow': 'tiempo_manana',
        'commentary': 'comentario_tiempo',
        'note': 'nota_meteorologica',
    }

    def get_permission_required(self, obj):
        return f'meteo.view_weather_{obj.report_type}'

    def get_filename(self, obj):
        prefix = self._PREFIX_MAP.get(obj.report_type, 'reporte')
        return f'{prefix}_{obj.date:%Y-%m-%d}.pdf'
