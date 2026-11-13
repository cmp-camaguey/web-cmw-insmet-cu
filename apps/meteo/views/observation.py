"""Sección Observaciones del dashboard: registro, detalle y captura.

Tres reglas gobiernan estas vistas:

1. **Alcance por estación.** El queryset se limita a las estaciones de los
   grupos del usuario; superusuario ve todas. Detalle, edición y eliminación de
   otra estación responden 404, no 403, para no revelar que el registro existe.
2. **Modo manual.** Con ``OBS_AUTOMATIC`` encendido las observaciones son del
   FTP y la sección es de consulta; las mutaciones redirigen con el aviso.
3. **Auditoría.** Alta, edición y eliminación escriben ``ActivityLog`` con el
   autor y la estación/hora afectada.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from apps.core.utils import log_action
from apps.meteo.forms.observation import ObservationForm
from apps.meteo.models import Observation
from apps.meteo.services.observations import delete_observation_telegram
from apps.meteo.station_roles import user_station_numbers

AUTOMATIC_MODE_MESSAGE = (
    'Las observaciones se reciben automáticamente por FTP. Para cargarlas desde '
    'el panel, desactive «Observaciones automáticas por FTP» en '
    'Configuración → Entorno del servidor.'
)


def scoped_observations(user):
    """Observaciones que el usuario puede ver, según sus estaciones."""
    queryset = Observation.objects.select_related('station', 'user')
    if user.is_superuser:
        return queryset
    return queryset.filter(station__number__in=user_station_numbers(user))


class AutomaticModeMixin:
    """Bloquea las mutaciones mientras las observaciones lleguen por FTP."""

    def dispatch(self, request, *args, **kwargs):
        if getattr(settings, 'OBS_AUTOMATIC', True):
            messages.info(request, AUTOMATIC_MODE_MESSAGE)
            return redirect('meteo:observacion_list')
        return super().dispatch(request, *args, **kwargs)


class StationScopedMixin:
    """Obtiene el registro por ``uuid`` dentro de las estaciones del usuario.

    ``SingleObjectMixin`` filtra por ``pk`` aunque el kwarg se llame distinto,
    así que el ``uuid`` de la URL hay que resolverlo a mano para que una
    estación no pueda abrir el registro de otra.
    """

    def get_object(self, queryset=None):
        return get_object_or_404(scoped_observations(self.request.user), uuid=self.kwargs['uuid'])


class ObservationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Observation
    template_name = 'pages/meteo/observation/list.html'
    permission_required = 'meteo.view_observation'
    context_object_name = 'objects'

    def get_queryset(self):
        return scoped_observations(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        automatic = getattr(settings, 'OBS_AUTOMATIC', True)
        context.update(
            {
                'title': 'Observaciones',
                'parent': '',
                'segment': 'observacion',
                'btn': 'Nueva Observación',
                'url_create': reverse_lazy('meteo:observacion_create'),
                'url_list': reverse_lazy('meteo:observacion_list'),
                'is_superuser': self.request.user.is_superuser,
                'automatic_mode': automatic,
                'can_create': not automatic and self.request.user.has_perm('meteo.add_observation'),
                'list_id': 'observations',
                'empty_table_message': 'No hay observaciones registradas para sus estaciones.',
            }
        )
        return context


class ObservationDetailView(
    LoginRequiredMixin, PermissionRequiredMixin, StationScopedMixin, DetailView
):
    model = Observation
    template_name = 'pages/meteo/observation/detail.html'
    permission_required = 'meteo.view_observation'
    context_object_name = 'observation'

    def get_queryset(self):
        return scoped_observations(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': 'Detalle de la observación',
                'parent': '',
                'segment': 'observacion',
                'url_list': reverse_lazy('meteo:observacion_list'),
            }
        )
        return context


class ObservationCreateView(
    AutomaticModeMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView
):
    model = Observation
    form_class = ObservationForm
    template_name = 'pages/meteo/observation/form.html'
    permission_required = 'meteo.add_observation'
    success_url = reverse_lazy('meteo:observacion_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=ADDITION,
            message=(
                f'Se registró la observación de {self.object.station.name} para las '
                f'{self.object.hour}:00 UTC del {self.object.obs_date:%d/%m/%Y}.'
            ),
        )
        messages.success(
            self.request,
            'La observación ha sido registrada y el telegrama se escribió para el mapa.',
            extra_tags='success',
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': 'Nueva Observación',
                'parent': '',
                'segment': 'observacion',
                'url_list': reverse_lazy('meteo:observacion_list'),
                'observation': None,
            }
        )
        return context


class ObservationUpdateView(
    AutomaticModeMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    StationScopedMixin,
    UpdateView,
):
    model = Observation
    form_class = ObservationForm
    template_name = 'pages/meteo/observation/form.html'
    permission_required = 'meteo.change_observation'
    success_url = reverse_lazy('meteo:observacion_list')
    context_object_name = 'observation'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=CHANGE,
            message=(
                f'Se corrigió la observación de {self.object.station.name} para las '
                f'{self.object.hour}:00 UTC del {self.object.obs_date:%d/%m/%Y}.'
            ),
        )
        messages.success(
            self.request,
            'La observación ha sido actualizada y el telegrama se reescribió.',
            extra_tags='success',
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'title': 'Editar Observación',
                'parent': '',
                'segment': 'observacion',
                'url_list': reverse_lazy('meteo:observacion_list'),
            }
        )
        return context


class ObservationDeleteView(AutomaticModeMixin, LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'meteo.delete_observation'

    def post(self, request, uuid, *args, **kwargs):
        observation = get_object_or_404(scoped_observations(request.user), uuid=uuid)
        log_action(
            user=request.user,
            obj=observation,
            action_flag=DELETION,
            message=(
                f'Se eliminó la observación de {observation.station.name} para las '
                f'{observation.hour}:00 UTC del {observation.obs_date:%d/%m/%Y}, '
                f'registrada por {observation.user.username if observation.user else "—"}.'
            ),
        )
        delete_observation_telegram(observation)
        observation.delete()
        messages.success(request, 'La observación ha sido eliminada.')
        return redirect('meteo:observacion_list')
