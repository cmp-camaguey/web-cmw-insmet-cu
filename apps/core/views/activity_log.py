from datetime import datetime

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.views.generic import ListView

from apps.core.models import ActivityLog
from apps.core.utils import local_day_window


class ActivityLogListView(ListView):
    model = ActivityLog
    template_name = 'pages/core/activity_log.html'
    context_object_name = 'objects'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_template_names(self):
        # La petición fetch de los filtros pide solo el tbody (?partial=1).
        if self.request.GET.get('partial'):
            return ['pages/core/activity_log_table.html']
        return [self.template_name]

    @staticmethod
    def _parse_date_filter(value):
        """Parsea fechas del picker (dd/mm/yyyy) o ISO (yyyy-mm-dd) a date."""
        if not value:
            return None
        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None

    def get_queryset(self):
        qs = ActivityLog.objects.select_related('user', 'content_type').all()

        user_id = self.request.GET.get('user')
        action_flag = self.request.GET.get('action_flag')
        date_from = self._parse_date_filter(self.request.GET.get('date_from'))
        date_to = self._parse_date_filter(self.request.GET.get('date_to'))

        if user_id:
            qs = qs.filter(user_id=user_id)
        if action_flag and action_flag.isdigit():
            qs = qs.filter(action_flag=int(action_flag))
        if date_from:
            # Rango de instantes, no `action_time__date__gte`: el `__date`
            # resuelve America/Havana dentro de PostgreSQL con su propio tzdata
            # y desplaza el limite una hora cuando discrepa del de Python.
            inicio, _ = local_day_window(date_from)
            qs = qs.filter(action_time__gte=inicio)
        if date_to:
            _, fin = local_day_window(date_to)
            qs = qs.filter(action_time__lt=fin)

        return qs.order_by('-action_time')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Auditoría de Actividad'
        context['parent'] = 'configuracion'
        context['segment'] = 'core:activity_log'
        context['users'] = (
            User.objects.filter(activity_logs__isnull=False).distinct().order_by('username')
        )
        context['action_flags'] = ActivityLog.ACTIVITY_FLAG_CHOICES
        # El estado vacío lo pinta DataTables (el template no renderiza una fila
        # con colspan: rompería el mapeo por posición de celdas). En una vista
        # con filtros, el mensaje genérico pierde el matiz, así que se declara
        # uno propio.
        context['empty_table_message'] = 'No hay registros que coincidan con los filtros.'
        context['current_filters'] = {
            'user': self.request.GET.get('user', ''),
            'action_flag': self.request.GET.get('action_flag', ''),
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
        }
        return context
