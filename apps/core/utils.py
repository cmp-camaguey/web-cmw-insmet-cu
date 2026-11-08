import os
from datetime import datetime, time, timedelta
from functools import wraps

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import render
from django.templatetags.static import static
from django.utils import timezone
from django.views import View

from apps.core.models import ActivityLog, CompanySettings

# Mapa de códigos meteorológicos a nombres base de archivos
TIEMPO_IMG_BASE_MAP = {
    'PN': 'poco_nublado',
    'PARCN': 'parcialmente_nublado',
    'N': 'nublado',
    'AIS CHUB': 'aislados_chubascos',
    'ALG CHUB': 'algunos_chubascos',
    'NUM CHUB': 'numerosos_chubascos',
    'ALG TORM': 'algunas_tormentas',
    'NUM TORM': 'numerosas_tormentas',
}

CODIGOS_SIN_VARIACION = ['N', 'ALG TORM', 'NUM TORM', 'ALG CHUB', 'NUM CHUB']

PERIOD_SUFFIXES = {'morning': 'm', 'afternoon': 'a', 'night': 'n'}

MOON_IMG_MAP = {
    'Luna Nueva': 'dist/img/moon_faces/new_moon.png',
    'Creciente': 'dist/img/moon_faces/waxing_crescent_moon.png',
    'Cuarto Creciente': 'dist/img/moon_faces/first_quarter_moon.png',
    'Gibosa Creciente': 'dist/img/moon_faces/waxing_gibbous_moon.png',
    'Luna Llena': 'dist/img/moon_faces/full_moon.png',
    'Gibosa Menguante': 'dist/img/moon_faces/waning_gibbous_moon.png',
    'Cuarto Menguante': 'dist/img/moon_faces/last_quarter_moon.png',
    'Menguante': 'dist/img/moon_faces/waning_crescent_moon.png',
}

SUN_IMG_MAP = {
    'sunrise': 'dist/img/sun/sunrise.png',
    'sunset': 'dist/img/sun/sunset.png',
}


def local_day_window(day):
    """Rango `[inicio, fin)` del dia local `day`, en instantes con zona.

    Los lookups `__date` resuelven la zona DENTRO de PostgreSQL
    (`(col AT TIME ZONE 'America/Havana')::date`), con el tzdata de la imagen de
    la base. `timezone.localdate()` la resuelve en Python, con el tzdata del
    paquete `tzdata` / del sistema. Cuando los dos discrepan — pasa: el paquete
    de PyPI y la imagen de PostgreSQL se mueven a distinto ritmo — el mismo
    instante cae en fechas distintas segun el lado, y el filtro se equivoca.

    Con una ventana de instantes la base solo compara `timestamptz` y no
    resuelve ninguna zona: la unica fuente de verdad es Python.
    """
    inicio = timezone.make_aware(datetime.combine(day, time.min))
    fin = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
    return inicio, fin


def log_action(user, obj, action_flag, message, request=None):
    LogEntry.objects.log_action(
        user_id=user.pk,
        content_type_id=ContentType.objects.get_for_model(obj).pk,
        object_id=obj.pk,
        object_repr=str(obj),
        action_flag=action_flag,
        change_message=message,
    )
    ActivityLog.objects.create(
        user=user if isinstance(user, User) else None,
        action_flag=action_flag,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk),
        object_repr=str(obj),
        message=message,
        ip_address=request.META.get('REMOTE_ADDR') if request else None,
        user_agent=request.META.get('HTTP_USER_AGENT', '') if request else '',
    )


def log_activity_from_request(request, obj, action_flag, message):
    """Registra la acción capturando IP y User-Agent desde el request."""
    return log_action(request.user, obj, action_flag, message, request=request)


def rate_limit_ip(limit=300, window=3600, key_prefix='rl'):
    """Limita requests por IP usando el cache de Django (LocMemCache por defecto)."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from django.core.cache import cache
            from django.http import HttpResponse

            ip = request.META.get('REMOTE_ADDR', 'unknown')
            cache_key = f'{key_prefix}:{ip}'
            count = cache.get(cache_key, 0)
            if count >= limit:
                return HttpResponse('Too Many Requests', status=429)
            cache.set(cache_key, count + 1, timeout=window)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def get_img_path(weather_code, period='afternoon'):
    if weather_code not in TIEMPO_IMG_BASE_MAP:
        return ''
    base_name = TIEMPO_IMG_BASE_MAP[weather_code]
    if weather_code in CODIGOS_SIN_VARIACION:
        file_path = f'dist/img/weather_icon/{base_name}.png'
    else:
        suffix = PERIOD_SUFFIXES.get(period, 'a')
        file_path = f'dist/img/weather_icon/{base_name}_{suffix}.png'
    return static(file_path)


def get_moon_img_path(moon_phase):
    file_path = MOON_IMG_MAP.get(moon_phase)
    return static(file_path) if file_path else ''


def get_sun_img_path(sun_event):
    file_path = SUN_IMG_MAP.get(sun_event)
    return static(file_path) if file_path else ''


def mail_send(request, alert_obj, subject, url, attachment_name=None):
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.urls import reverse
    from django.utils import timezone

    from apps.core.tasks import send_email_task

    recipient_list = (
        alert_obj.email_recipient_list.recipients.all() if alert_obj.email_recipient_list else []
    )
    if not recipient_list:
        return

    company = CompanySettings.get_instance()
    # `emails/alert_notification.html` espera `index_url`, `listado_url`, `title`
    # y `current_year`; sin ellos el correo sale con `href=""`, título vacío y
    # sin año. Mismo criterio que los correos comerciales
    # (`apps/commercial/views/invoice_utils.py`).
    #
    # La URL sale de `SITE_URL` y NO del `request`: el destinatario no está en la
    # red interna, y el host por el que el operador entró no es el público. El
    # parámetro `request` se sigue aceptando porque los llamadores lo pasan.
    base_url = settings.SITE_URL
    context = {
        'company': company,
        'alert': alert_obj,
        'subject': subject,
        'url': url,
        'index_url': base_url,
        'listado_url': base_url + reverse(url).lstrip('/'),
        # `Warning` tiene `title`; `WeatherReport` no, así que cae al asunto.
        'title': getattr(alert_obj, 'title', None) or subject,
        'current_year': timezone.now().year,
    }
    html_message = render_to_string('emails/alert_notification.html', context)
    recipient_emails = [r.email for r in recipient_list]

    pdf_attachment = None
    if hasattr(alert_obj, 'file') and alert_obj.file:
        pdf_attachment = alert_obj.file.path if os.path.isfile(alert_obj.file.path) else None

    send_email_task(
        subject,
        html_message,
        settings.DEFAULT_FROM_EMAIL,
        recipient_emails,
        attachment_name=attachment_name,
        attachment_path=pdf_attachment,
    )


class My400View(View):
    def dispatch(self, request, *args, **kwargs):
        return render(request, 'layouts/400.html', status=400)


class My403View(View):
    def dispatch(self, request, *args, **kwargs):
        return render(request, 'layouts/403.html', status=403)


class My404View(View):
    def dispatch(self, request, *args, **kwargs):
        return render(request, 'layouts/404.html', status=404)


class My500View(View):
    def dispatch(self, request, *args, **kwargs):
        return render(request, 'layouts/500.html', status=500)
