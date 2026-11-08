import ipaddress
import os
from functools import wraps

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.shortcuts import render
from django.templatetags.static import static
from django.views import View

from apps.core.models import ActivityLog, CompanySettings
from apps.core.request_context import current_request

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


def log_action(user, obj, action_flag, message, request=None):
    """Registra la acción en el log de Django y en `ActivityLog`.

    El request se toma del parámetro o, si la vista no lo pasó, del
    `ContextVar` que deja `CurrentRequestMiddleware`. Sin ese respaldo la fila
    nace sin IP ni User-Agent y la tabla de auditoría muestra guiones en las dos
    columnas que sirven para reconstruir quién hizo qué.
    """
    request = request or current_request()
    content_type = ContentType.objects.get_for_model(obj)

    # Las dos filas son el mismo hecho y tienen que existir juntas o no existir.
    # Sin la transacción, un fallo al escribir `ActivityLog` deja el log de
    # Django con una entrada que la auditoría del portal no tiene, y la
    # diferencia es invisible hasta que alguien audita.
    with transaction.atomic():
        LogEntry.objects.log_action(
            user_id=user.pk,
            content_type_id=content_type.pk,
            object_id=obj.pk,
            object_repr=str(obj),
            action_flag=action_flag,
            change_message=message,
        )
        ActivityLog.objects.create(
            user=user if isinstance(user, User) else None,
            action_flag=action_flag,
            content_type=content_type,
            object_id=str(obj.pk),
            object_repr=str(obj),
            message=message,
            ip_address=client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT') or '') if request else '',
        )


def client_ip(request):
    """IP del visitante, o `None` si no se puede determinar con certeza.

    El portal corre detrás de Nginx y la petición llega por socket Unix, así que
    `REMOTE_ADDR` es el proxy y no el visitante. Nginx añade la IP que él vio al
    FINAL de `X-Forwarded-For` (`$proxy_add_x_forwarded_for`), por eso se toma la
    última entrada y no la primera: la primera la puede escribir el cliente, y
    guardarla sería dejar que cualquiera firme la auditoría con la IP que quiera.

    Se valida con `ipaddress` antes de devolverla: la columna es un
    `GenericIPAddressField` y un valor inventado haría fallar el INSERT, que es
    justo la línea que no puede perderse.
    """
    if request is None:
        return None

    remote_addr = (request.META.get('REMOTE_ADDR') or '').strip()

    # El encabezado sólo se cree cuando quien lo escribió es el proxy. Con
    # Gunicorn escuchando en `unix:/run/webcmp/gunicorn.sock` el par es el socket
    # (sin `REMOTE_ADDR`); si algún día Nginx pasa a TCP en loopback, sigue
    # entrando por esta puerta. Un Gunicorn expuesto directo tiene un par que no
    # es loopback, y ahí el encabezado lo pudo escribir cualquiera: se ignora y
    # se usa `REMOTE_ADDR`, que en ese caso sí es el visitante.
    forwarded = ''
    if _is_trusted_proxy_peer(remote_addr):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')

    # La última entrada es la que agregó el proxy de confianza. Se recorre de
    # atrás hacia adelante y se acepta la primera que sea una IP válida: si el
    # proxy escribió algo raro, la anterior sigue siendo más confiable que
    # `REMOTE_ADDR`, que aquí es siempre el propio proxy.
    for candidate in reversed([part.strip() for part in forwarded.split(',') if part.strip()]):
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue

    # Sin encabezado (desarrollo, o un proxy que no lo añade) `REMOTE_ADDR` sí
    # es el visitante.
    try:
        return str(ipaddress.ip_address(remote_addr))
    except ValueError:
        return None


def _is_trusted_proxy_peer(remote_addr):
    """`True` cuando `REMOTE_ADDR` es el proxy y no el visitante.

    Socket Unix (el despliegue real) no deja `REMOTE_ADDR`, y un proxy local sí
    deja una dirección de loopback. Cualquier otra cosa significa que la
    petición llegó directo y su `X-Forwarded-For` no vale nada.
    """
    if not remote_addr:
        return True
    try:
        return ipaddress.ip_address(remote_addr).is_loopback
    except ValueError:
        return False


def log_activity_from_request(request, obj, action_flag, message):
    """Registra la acción capturando IP y User-Agent desde el request."""
    return log_action(request.user, obj, action_flag, message, request=request)


def rate_limit_ip(limit=300, window=3600, key_prefix='rl'):
    """Limita requests por IP usando el cache de Django (LocMemCache por defecto).

    La IP sale de `client_ip` y no de `REMOTE_ADDR`: detrás de Nginx
    `REMOTE_ADDR` es siempre el proxy, así que limitar por ahí convierte el
    límite por visitante en un límite global del sitio y un cliente puede
    agotarlo para todos.

    El conteo usa `add` + `incr` y no `get` + `set`: la lectura y la escritura
    separadas pierden incrementos concurrentes y dejan pasar más peticiones que
    `limit`. Con `add` el TTL se fija una sola vez, así que la ventana es fija y
    no se corre hacia adelante con cada request.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            from django.core.cache import cache
            from django.http import HttpResponse

            ip = client_ip(request) or 'unknown'
            cache_key = f'{key_prefix}:{ip}'

            cache.add(cache_key, 0, timeout=window)
            try:
                count = cache.incr(cache_key)
            except ValueError:
                # La clave venció entre el `add` y el `incr`.
                cache.set(cache_key, 1, timeout=window)
                count = 1

            if count > limit:
                return HttpResponse('Too Many Requests', status=429)
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
    import logging

    from django.conf import settings
    from django.template.loader import render_to_string
    from django.urls import reverse
    from django.utils import timezone

    from apps.core.audiences import resolve_audience
    from apps.core.models import Notification
    from apps.core.notifications import make_dedupe_key, notify_superusers
    from apps.core.subscriptions import unsubscribe_url
    from apps.core.tasks import send_email_task

    logger = logging.getLogger(__name__)
    source = alert_obj._meta.model_name
    subscribers = resolve_audience()
    # El enlace se guarda como ruta, no como nombre de URL: `url` llega con el
    # nombre (`home:warnings_tropical`) porque es lo que el correo necesita para
    # `reverse()`. Guardarlo tal cual en la notificación producía un
    # `href="home:..."`, que el navegador interpreta como esquema propio y
    # delega en `xdg-open`.
    link = url if url.startswith('/') else reverse(url)
    if not subscribers:
        # Antes esto era un `return` mudo: el aviso se publicaba en el portal,
        # el correo no salía y nadie se enteraba. Ahora deja rastro y avisa al
        # admin, con clave de deduplicación por objeto para que reintentar el
        # mismo formulario no llene la bandeja.
        logger.warning(
            'Sin destinatarios para %s %s (source=%s): no se envió "%s".',
            alert_obj._meta.label,
            alert_obj.pk,
            source,
            subject,
        )
        notify_superusers(
            source='email',
            severity=Notification.Severity.WARNING,
            title='Un aviso se publicó sin destinatarios de correo',
            body=(
                f'{alert_obj._meta.verbose_name}: {getattr(alert_obj, "title", None) or subject}'
                ' no llegó a nadie porque la lista principal no tiene suscriptores '
                'confirmados.'
            ),
            url=link,
            dedupe_key=make_dedupe_key('email', source, alert_obj.pk),
        )
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
    pdf_attachment = None
    if hasattr(alert_obj, 'file') and alert_obj.file:
        pdf_attachment = alert_obj.file.path if os.path.isfile(alert_obj.file.path) else None

    # Un correo por destinatario, no una lista en `to:`: así cada uno recibe su
    # propio enlace de baja y ninguno ve la dirección de los demás. El render es
    # barato y corre dentro de la tarea de Huey, fuera del ciclo de la petición.
    for subscriber in subscribers:
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
            'unsubscribe_url': unsubscribe_url(subscriber),
        }
        html_message = render_to_string('emails/alert_notification.html', context)
        send_email_task(
            subject,
            html_message,
            settings.DEFAULT_FROM_EMAIL,
            [subscriber.email],
            attachment_name=attachment_name,
            attachment_path=pdf_attachment,
        )

    # Hasta aquí solo dejaba rastro el fracaso. Un envío que salió bien es justo
    # el que nadie puede reconstruir después: el log de la tarea dice «correo
    # enviado» por cada destinatario, sin decir de qué publicación ni a cuántos.
    # La clave incluye `sent` para que reenviar el mismo objeto no duplique el
    # aviso mientras siga sin leer, pero sí vuelva a avisar si el admin ya lo vio.
    notify_superusers(
        source='email',
        severity=Notification.Severity.INFO,
        title='Publicación enviada por correo',
        body=(
            f'{alert_obj._meta.verbose_name}: {getattr(alert_obj, "title", None) or subject} '
            f'llegó a {len(subscribers)} suscriptor{"es" if len(subscribers) != 1 else ""}.'
        ),
        url=link,
        dedupe_key=make_dedupe_key('email', source, alert_obj.pk, 'sent'),
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
