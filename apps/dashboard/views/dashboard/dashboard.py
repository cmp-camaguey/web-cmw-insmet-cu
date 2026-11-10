import hashlib
import html
import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import Group, User
from django.contrib.sessions.models import Session
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.commercial.models import Customer, Invoice, InvoiceItem, ServiceSubscription
from apps.core.cache_utils import safe_cache_get, safe_cache_set
from apps.core.models import TaskExecutionLog
from apps.core.utils import local_day_window
from apps.meteo.models import Forecasts, Warning

logger = logging.getLogger(__name__)


def _region_temp(f, region_key, period):
    data = getattr(f, region_key)
    return (
        float(data[period]['temp'])
        if data and data.get(period) and data[period].get('temp')
        else 0.0
    )


def serialize_sub(sub):
    return {
        # `company_name` es NULL en una persona natural: el panel mostraba
        # "N/A" en lugar del nombre. `display_name` resuelve el nombre visible
        # de cualquier tipo de cliente; el escape lo aplica esta función.
        'customer': html.escape(sub.customer.display_name) if sub.customer else 'N/A',
        'service': html.escape(sub.service.title or 'N/A') if sub.service else 'N/A',
        # Sin vencimiento: la suscripción queda vigente hasta que se anule, así
        # que lo único que hay que fechar es cuándo arranca el servicio.
        'start_date': sub.start_date.strftime('%d/%m/%Y') if sub.start_date else 'N/A',
    }


paid_direct = Exists(
    ServiceSubscription.objects.filter(
        id=OuterRef('subscription_id'), payment_status='paid', record_active=True
    )
)


paid_via_items = Exists(
    InvoiceItem.objects.filter(
        invoice_id=OuterRef('id'),
        subscription__payment_status='paid',
        subscription__record_active=True,
    )
)


def _income_num_months(income_range, now):
    if income_range == '1m':
        return 1
    if income_range == '3m':
        return 3
    if income_range == '6m':
        return 6
    if income_range == 'all':
        earliest = Invoice.objects.filter(is_cancelled=False).order_by('issue_date').first()
        if earliest and earliest.issue_date:
            return max((now - earliest.issue_date).days // 30, 1)
        return 12
    return 12


def _build_forecast_series(start_date):
    """Expensive, user-agnostic forecast series. Cached by the caller."""
    forecasts = Forecasts.objects.filter(date__gte=start_date).order_by('date')
    total_forecasts = forecasts.count()
    if total_forecasts > 30:
        step = total_forecasts // 30
        forecast_ids = forecasts.values_list('id', flat=True)
        sampled_ids = forecast_ids[::step]
        forecasts = Forecasts.objects.filter(id__in=sampled_ids).order_by('date')
    has_forecasts = forecasts.exists()
    if not has_forecasts:
        return {'has_forecasts': False}
    forecasts_list = list(forecasts)
    return {
        'has_forecasts': True,
        'temperature_labels': json.dumps([f.date.strftime('%d/%m') for f in forecasts_list]),
        'max_temperatures_north': json.dumps(
            [_region_temp(f, 'north', 'afternoon') for f in forecasts_list]
        ),
        'min_temperatures_north': json.dumps(
            [_region_temp(f, 'north', 'night') for f in forecasts_list]
        ),
        'max_temperatures_south': json.dumps(
            [_region_temp(f, 'south', 'afternoon') for f in forecasts_list]
        ),
        'min_temperatures_south': json.dumps(
            [_region_temp(f, 'south', 'night') for f in forecasts_list]
        ),
        'max_temperatures_inland': json.dumps(
            [_region_temp(f, 'interior', 'afternoon') for f in forecasts_list]
        ),
        'min_temperatures_inland': json.dumps(
            [_region_temp(f, 'interior', 'night') for f in forecasts_list]
        ),
    }


def _build_alerts_block():
    """Expensive, user-agnostic latest-alerts aggregation. Cached by the caller."""
    now = timezone.now()
    latest_early = (
        Warning.objects.filter(warning_type='early', valid_until__gte=now).order_by('-date').first()
    )
    latest_cyclone = (
        Warning.objects.filter(warning_type='tropical_cyclone', valid_until__gte=now)
        .order_by('-date')
        .first()
    )
    latest_storm = (
        Warning.objects.filter(warning_type='storm', valid_until__gte=now).order_by('-date').first()
    )
    return {
        'early_warnings': [latest_early] if latest_early else [],
        'tropical_cyclones': [latest_cyclone] if latest_cyclone else [],
        'storm_warnings': [latest_storm] if latest_storm else [],
    }


def _build_commercial_income(income_range):
    """Expensive, user-agnostic commercial income chart. Cached by the caller."""
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    num_months = _income_num_months(income_range, now)
    income_start = month_start - timezone.timedelta(days=num_months * 30)

    billed_qs = (
        Invoice.objects.filter(is_cancelled=False, issue_date__gte=income_start)
        .values('issue_date__year', 'issue_date__month')
        .annotate(total=Sum('amount'))
        .order_by('issue_date__year', 'issue_date__month')
    )
    paid_qs = (
        Invoice.objects.filter(is_cancelled=False, issue_date__gte=income_start)
        .filter(paid_direct | paid_via_items)
        .values('issue_date__year', 'issue_date__month')
        .annotate(total=Sum('amount'))
        .order_by('issue_date__year', 'issue_date__month')
    )

    billed_map = {}
    paid_map = {}
    for entry in billed_qs:
        billed_map[(entry['issue_date__year'], entry['issue_date__month'])] = float(entry['total'])
    for entry in paid_qs:
        paid_map[(entry['issue_date__year'], entry['issue_date__month'])] = float(entry['total'])

    months_labels = []
    billed_data = []
    paid_data = []
    for i in range(num_months - 1, -1, -1):
        m = now.month - i
        y = now.year
        while m < 1:
            m += 12
            y -= 1
        months_labels.append(f'{m:02d}/{y}')
        billed_data.append(billed_map.get((y, m), 0))
        paid_data.append(paid_map.get((y, m), 0))

    return {
        'income_months': json.dumps(months_labels),
        'income_billed_data': json.dumps(billed_data),
        'income_paid_data': json.dumps(paid_data),
    }


def build_shared_kpis(time_range, income_range, *, forecast=False, alerts=False, commercial=False):
    """Return user-agnostic Dashboard aggregates, cached per (range, income_range).

    Only the expensive shared aggregations are cached (forecast series, alerts,
    commercial income chart). Per-client data is NEVER placed here, so there is
    no cross-user leakage. Cached values are JSON strings / counts (picklable for
    Redis). Reads/writes use safe_cache_* so a Redis outage degrades to live compute.
    """
    cache_key = f'dashboard:kpi:{time_range}:{income_range}'
    shared = safe_cache_get(cache_key) or {}
    if forecast and 'forecast' not in shared:
        series = _build_forecast_series(
            timezone.localdate()
            - timezone.timedelta(
                days=30 if time_range == '30d' else 90 if time_range == '3m' else 7
            )
        )
        if series.get('has_forecasts'):
            shared['forecast'] = series
            safe_cache_set(cache_key, shared, 300)
    if alerts and 'alerts' not in shared:
        shared['alerts'] = _build_alerts_block()
        safe_cache_set(cache_key, shared, 300)
    if commercial and 'commercial' not in shared:
        shared['commercial'] = _build_commercial_income(income_range)
        safe_cache_set(cache_key, shared, 300)
    return shared


class DashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'pages/dashboard/index.html'

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not (user.is_staff or user.groups.filter(name='Clientes').exists()):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        user = self.request.user
        return user.is_staff or user.groups.filter(name='Clientes').exists()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        time_range = self.request.GET.get('range', '7d')
        income_range = self.request.GET.get('income_range', '12m')

        context.update(
            {
                'title': 'Dashboard',
                'parent': '',
                'segment': 'dashboard',
                'is_superuser': user.is_superuser,
                'selected_range': time_range,
            }
        )

        context['show_alerts'] = user.has_perm('meteo.view_warning')
        context['show_forecast'] = user.has_perm('meteo.view_forecast')
        context['is_client'] = (
            user.groups.filter(name='Clientes').exists() and not user.is_superuser
        )
        context['show_commercial'] = (
            any(
                [
                    user.has_perm('commercial.view_subscription'),
                    user.has_perm('commercial.view_invoice'),
                ]
            )
            and not context['is_client']
        )

        now = timezone.now()

        shared = build_shared_kpis(
            time_range,
            income_range,
            forecast=context['show_forecast'],
            alerts=context['show_alerts'],
            commercial=context['show_commercial'],
        )

        if context['is_client']:
            try:
                customer = Customer.objects.get(user=user)
                subs = ServiceSubscription.objects.filter(customer=customer, record_active=True)
                # Ninguna suscripción vence: `client_active_subs` son las pagadas
                # y no anuladas, y lo siguen siendo para siempre. Por eso no
                # existen "vencidas" ni "por vencer": una vez pagada, la única
                # forma de perder la vigencia es anularla.
                context['client_active_subs'] = subs.filter(payment_status='paid')
                context['client_pending_subs'] = subs.filter(payment_status='pending')
                context['client_requested_subs'] = subs.filter(payment_status='requested')
                context['client_invoices'] = Invoice.objects.filter(customer=customer).order_by(
                    '-issue_date'
                )[:5]
            except Customer.DoesNotExist:
                context['client_active_subs'] = ServiceSubscription.objects.none()
                context['client_pending_subs'] = ServiceSubscription.objects.none()
                context['client_requested_subs'] = ServiceSubscription.objects.none()
                context['client_invoices'] = Invoice.objects.none()

        if context['show_forecast']:
            try:
                context['latest_forecast'] = Forecasts.objects.prefetch_related(
                    'regions', 'extended_days'
                ).latest('date')
            except ObjectDoesNotExist:
                context['latest_forecast'] = None

            if 'forecast' in shared:
                context.update(shared['forecast'])

        if context['show_alerts'] and 'alerts' in shared:
            context['latest_alerts'] = shared['alerts']

        if context['show_commercial']:
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            context['selected_income_range'] = income_range

            if 'commercial' in shared:
                context.update(shared['commercial'])

            # Las suscripciones no vencen: `active_subs` son las pagadas y no
            # anuladas. Las métricas `expired_subs` y `expiring_soon` se
            # eliminaron porque no tienen un conjunto que contar — una suscripción
            # pagada nunca pasa a "vencida" sola.
            context['active_subs'] = ServiceSubscription.objects.filter(
                payment_status='paid', record_active=True
            ).count()
            context['pending_subs'] = ServiceSubscription.objects.filter(
                payment_status='pending', record_active=True
            ).count()
            context['requested_subs'] = ServiceSubscription.objects.filter(
                payment_status='requested', record_active=True
            ).count()

            month_end = (month_start + timezone.timedelta(days=32)).replace(day=1)
            month_income_paid = (
                Invoice.objects.filter(
                    is_cancelled=False, issue_date__gte=month_start, issue_date__lt=month_end
                )
                .filter(paid_direct | paid_via_items)
                .aggregate(total=Sum('amount'))['total']
                or 0
            )
            context['month_income'] = float(month_income_paid)

            # Ordenar por la misma clave que se muestra. `company_name` sólo
            # existe para personas jurídicas: en una natural es NULL, así que
            # ordenar por esa columna metía a todas las naturales al principio
            # (SQLite) o al final (PostgreSQL) según el motor y dejaba el
            # orden entre ellas al azar. El `Coalesce` cae al apellido, luego
            # al nombre y por último al username, que nunca es NULL.
            orden_cliente = Coalesce(
                'customer__company_name',
                'customer__user__last_name',
                'customer__user__first_name',
                'customer__user__username',
            )

            active_subs_qs = (
                ServiceSubscription.objects.filter(payment_status='paid', record_active=True)
                .select_related('customer', 'service')
                .order_by(orden_cliente)[:20]
            )

            pending_subs_qs = (
                ServiceSubscription.objects.filter(payment_status='pending', record_active=True)
                .select_related('customer', 'service')
                .order_by(orden_cliente)[:20]
            )

            requested_subs_qs = (
                ServiceSubscription.objects.filter(payment_status='requested', record_active=True)
                .select_related('customer', 'service')
                .order_by(orden_cliente)[:20]
            )

            context['active_subs_list'] = json.dumps([serialize_sub(s) for s in active_subs_qs])
            context['pending_subs_list'] = json.dumps([serialize_sub(s) for s in pending_subs_qs])
            context['requested_subs_list'] = json.dumps(
                [serialize_sub(s) for s in requested_subs_qs]
            )

        if user.is_superuser:
            inicio_hoy, fin_hoy = local_day_window(timezone.localdate())
            context['user_stats'] = {
                'total_users': User.objects.count(),
                # Rango de instantes, no `last_login__date`: el `__date` resuelve
                # America/Havana dentro de PostgreSQL con su propio tzdata y el
                # conteo se corre un dia cuando discrepa del de Python.
                'active_today': User.objects.filter(
                    last_login__gte=inicio_hoy, last_login__lt=fin_hoy
                ).count(),
                'staff_users': User.objects.filter(is_staff=True).count(),
            }

            permission_groups = (
                Group.objects.annotate(user_count=Count('user'))
                .prefetch_related('permissions', 'user_set')
                .order_by('-user_count')
            )
            paginator_groups = Paginator(permission_groups, 2)
            page_number_groups = self.request.GET.get('page_groups')
            context['permission_groups_page'] = paginator_groups.get_page(page_number_groups)

            recent_logins = User.objects.filter(
                last_login__gte=timezone.now() - timezone.timedelta(hours=24)
            ).order_by('-last_login')
            paginator_logins = Paginator(recent_logins, 2)
            page_number_logins = self.request.GET.get('page_logins')
            context['recent_logins_page'] = paginator_logins.get_page(page_number_logins)

            context['active_sessions'] = Session.objects.filter(
                expire_date__gt=timezone.now()
            ).count()

        return context


def _paso_a_reintentar(execution, paso_pedido):
    """Qué paso de la tarea de factura hay que reintentar.

    Devuelve 'pdf', 'email' o None (dejar que la tarea decida sola).

    Si el operador pidió un paso explícito, se respeta. Si no, se deduce del
    estado de la factura, que es la fuente de verdad: si el PDF quedó `ready`
    y el correo `failed`, el único paso que sirve reintentar es el correo.
    """
    if paso_pedido in ('pdf', 'email'):
        return paso_pedido
    if not (execution.logical_key or '').startswith('invoice:'):
        return None
    try:
        from apps.commercial.models import Invoice

        invoice = Invoice.objects.get(uuid=execution.logical_key.split(':', 1)[1])
    except Exception:
        return None
    if not invoice.pdf_ready and invoice.pdf_status == Invoice.PdfStatus.FAILED:
        return 'pdf'
    if invoice.email_status == Invoice.EmailStatus.FAILED:
        return 'email'
    return None


def _tabla_fingerprint(qs):
    """Token barato que cambia si y solo si cambia lo que la tabla muestra.

    El cliente lo consulta cada pocos segundos y solo descarga y repinta la
    tabla cuando el token se movió. Sin esto, estaríamos renderizando el
    partial completo cada 5 segundos para reemplazar filas idénticas, que es
    trabajo de servidor y red tirados a la basura.

    El modelo no tiene `updated_at`, así que la huella se arma con los campos
    que la tabla muestra de verdad: si ninguno cambió, la pantalla tampoco
    tiene nada nuevo que pintar.
    """
    filas = list(
        qs.values_list(
            'pk',
            'status',
            'attempts',
            'enqueued_at',
            'started_at',
            'finished_at',
            'summary',
        )
    )
    return hashlib.sha1(
        repr(filas).encode('utf-8'),
        usedforsecurity=False,
    ).hexdigest()[:16]


class TaskMonitoringView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'pages/dashboard/tasks.html'
    max_rows = 500

    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, *args, **kwargs):
        # La sonda de "¿cambió algo?" no renderiza nada: responde un token.
        if request.GET.get('fingerprint'):
            return JsonResponse({'token': _tabla_fingerprint(self._queryset())})
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        # El polling del cliente pide solo la tabla (?partial=1) para
        # recargar los datos sin reconstruir la página ni la instancia de
        # DataTables: si no, cada pasada pierde el orden y la búsqueda.
        if self.request.GET.get('partial'):
            return ['pages/dashboard/tasks_table.html']
        return [self.template_name]

    def _queryset(self):
        qs = TaskExecutionLog.objects.all()
        status_filter = self.request.GET.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs[: self.max_rows]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        stale_threshold_minutes = 5

        qs = self._queryset()
        executions = qs
        stale_count = TaskExecutionLog.objects.filter(
            status=TaskExecutionLog.STATUS_ENQUEUED,
            enqueued_at__lt=now - timezone.timedelta(minutes=stale_threshold_minutes),
        ).count()
        error_count = TaskExecutionLog.objects.filter(status=TaskExecutionLog.STATUS_ERROR).count()

        context.update(
            {
                'title': 'Monitoreo de tareas',
                'parent': 'dashboard',
                'segment': 'task_monitoring',
                'executions': executions,
                'status_choices': TaskExecutionLog.STATUS_CHOICES,
                # Para marcar qué botón de filtro está aplicado: sin esto, el
                # operador no tiene forma de saber qué está viendo después de
                # filtrar, porque el filtro ya no recarga la página.
                'current_status': self.request.GET.get('status', ''),
                'stale_threshold_minutes': stale_threshold_minutes,
                'stale_count': stale_count,
                'error_count': error_count,
            }
        )
        return context


class TaskMonitoringActionView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Acciones POST sobre un registro de tarea: reintentar o limpiar.

    Requiere superuser (igual que la tabla). Reintentar solo es posible para
    tareas cuyos argumentos se persistieron de forma segura (RETRYABLE_TASKS).
    """

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, *args, **kwargs):
        import json as _json
        from importlib import import_module

        from apps.core.apps import RETRYABLE_TASKS

        execution = get_object_or_404(TaskExecutionLog, uuid=self.kwargs['uuid'])
        action = request.POST.get('action')

        if action == 'retry':
            if execution.func_name not in RETRYABLE_TASKS.values() or not execution.func_args:
                messages.error(
                    request,
                    'No se puede reintentar: la tarea no está marcada como segura de reencolar.',
                )
                return redirect('dashboard:tasks')
            try:
                module_name, _, func_name = execution.func_name.rpartition('.')
                func = getattr(import_module(module_name), func_name)
                payload = _json.loads(execution.func_args)
                args = list(payload.get('args', []))
                kwargs = dict(payload.get('kwargs', {}))
                # Reintentar el paso que falló, no la tarea entera: si el PDF
                # ya salió bien y lo que no salió fue el correo, volver a
                # renderizar el PDF no arregla nada y lo deja con una marca de
                # tiempo nueva. La tarea es idempotente, así que
                # `_paso_a_reintentar` solo decide qué se le pide explícitamente.
                paso = _paso_a_reintentar(execution, request.POST.get('paso'))
                if paso:
                    kwargs['solo_paso'] = paso
                func(*args, **kwargs)
                messages.success(
                    request,
                    f'Tarea reencolada'
                    f'{f" (paso: {paso})" if paso else ""}. Se actualizó la misma fila.',
                )
            except Exception:
                logger.exception('No se pudo reintentar la tarea %s', execution.func_name)
                messages.error(request, 'No se pudo reintentar la tarea. Revise los logs.')
        elif action == 'delete':
            execution.delete()
            messages.success(request, f'Registro de {execution.task_name} eliminado.')
        else:
            messages.error(request, 'Acción no reconocida.')

        return redirect('dashboard:tasks')
