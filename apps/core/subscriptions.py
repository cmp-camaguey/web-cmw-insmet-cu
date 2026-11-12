"""Consentimiento de correo: la única puerta a `Subscriber` y `Subscription`.

Ninguna vista, signal ni comando escribe esos modelos por su cuenta. Si lo
hicieran, el consentimiento dejaría de tener un solo significado y volveríamos
al defecto que motivó el cambio 030: un `newsletter=True` que miente.

Ver `openspec/changes/030-boletin-y-notificaciones-de-clientes/design.md` §3.
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.models import EmailRecipientList, Subscriber, Subscription

logger = logging.getLogger(__name__)

DEFAULT_CONFIRMATION_TTL_HOURS = 72

# Espera antes del único recordatorio, y vida de un alta que nunca se confirmó.
# El recordatorio es uno solo a propósito: quien no confirmó en tres días no va a
# confirmar por insistencia, y cada reenvío es correo no solicitado.
DEFAULT_REMINDER_HOURS = 48
DEFAULT_PENDING_RETENTION_DAYS = 7

CONFIRM_OK = 'ok'
CONFIRM_INVALID = 'invalid'
CONFIRM_EXPIRED = 'expired'


def normalize_email(email):
    return (email or '').strip().lower()


def confirmation_ttl():
    hours = getattr(settings, 'NEWSLETTER_CONFIRMATION_TTL_HOURS', DEFAULT_CONFIRMATION_TTL_HOURS)
    return timedelta(hours=hours)


def reminder_delay():
    hours = getattr(settings, 'NEWSLETTER_REMINDER_HOURS', DEFAULT_REMINDER_HOURS)
    return timedelta(hours=hours)


def pending_retention():
    days = getattr(settings, 'NEWSLETTER_PENDING_RETENTION_DAYS', DEFAULT_PENDING_RETENTION_DAYS)
    return timedelta(days=days)


def _issue_confirmation_token(subscriber):
    subscriber.confirmation_token = uuid.uuid4()
    subscriber.confirmation_sent_at = timezone.now()
    subscriber.save(update_fields=['confirmation_token', 'confirmation_sent_at'])


def subscribe(email, *, source, recipient_list=None, confirm=False):
    """Crea o actualiza el suscriptor y su pertenencia al boletín.

    Devuelve `(subscriber, created, needs_confirmation)`.

    `needs_confirmation` es `True` cuando hace falta mandar el correo de
    confirmación: suscriptor nuevo, pendiente, o dado de baja. Un suscriptor
    confirmado que vuelve a escribir su correo no recibe otro mensaje, y la
    respuesta pública es la misma en los tres casos para no revelar quién está
    en la lista.
    """
    email = normalize_email(email)
    if not email:
        raise ValueError('El correo no puede estar vacío.')

    recipient_list = recipient_list or EmailRecipientList.get_default()
    if recipient_list is None:
        logger.warning('No hay lista marcada como boletín; no se suscribió %s.', email)
        return None, False, False

    with transaction.atomic():
        subscriber, created = Subscriber.objects.get_or_create(
            email=email,
            defaults={
                'source': source,
                'confirmed_at': timezone.now() if confirm else None,
            },
        )
        Subscription.objects.get_or_create(
            subscriber=subscriber,
            recipient_list=recipient_list,
            defaults={'source': source},
        )

        needs_confirmation = False
        if not confirm and (subscriber.confirmed_at is None or not subscriber.active):
            _issue_confirmation_token(subscriber)
            needs_confirmation = True
        elif confirm and subscriber.confirmed_at is None:
            subscriber.confirmed_at = timezone.now()
            subscriber.save(update_fields=['confirmed_at'])

    return subscriber, created, needs_confirmation


def confirm(token):
    """Confirma un suscriptor con su token. Devuelve `(subscriber, status)`.

    Un token usado no existe: se anula al confirmar, así que el segundo intento
    cae en `CONFIRM_INVALID` y la página lo explica en vez de dar un 404.
    """
    if not token:
        return None, CONFIRM_INVALID

    try:
        subscriber = Subscriber.objects.get(confirmation_token=token)
    except Subscriber.DoesNotExist:
        return None, CONFIRM_INVALID

    sent_at = subscriber.confirmation_sent_at
    if sent_at is not None and timezone.now() - sent_at > confirmation_ttl():
        return subscriber, CONFIRM_EXPIRED

    subscriber.confirmed_at = timezone.now()
    subscriber.active = True
    subscriber.confirmation_token = None
    subscriber.save(update_fields=['confirmed_at', 'active', 'confirmation_token'])

    # Si la dirección pertenece a una cuenta del sitio, la casilla del perfil
    # tiene que decir lo mismo que la suscripción. Sin esto, un usuario
    # registrado que se suscribe desde el landing queda recibiendo correos
    # mientras su perfil dice que no los recibe: la misma mentira que este
    # servicio existe para evitar, solo al revés.
    #
    # Se marca al CONFIRMAR y no al pedir la suscripción: hasta que la persona
    # no abre el enlace del correo no demostró que el buzón es suyo, y marcar la
    # casilla antes sería dar por aceptado algo que todavía nadie aceptó.
    #
    # `update()` y no `save()`: guardar el perfil dispara su `post_save`, que
    # llamaría a `set_consent` y volvería a entrar aquí.
    from apps.user_auth.models import Profile

    Profile.objects.filter(user__email__iexact=subscriber.email, newsletter=False).update(
        newsletter=True
    )
    return subscriber, CONFIRM_OK


def unsubscribe(token):
    """Da de baja al suscriptor. Idempotente: dos visitas dan el mismo resultado.

    No borra la fila a propósito (decisión 7 de la propuesta): conservar a quién
    se le escribió es lo que evita volver a importunarlo.

    La baja es más que apagar `active`. Si solo se apagara el flag, la casilla
    `Profile.newsletter` seguiría marcada y la pertenencia a la lista seguiría
    ahí: el sistema diría «no recibe» por un lado y «aceptó recibir» por el
    otro. Peor todavía, cualquier guardado posterior del perfil reaplicaría la
    casilla marcada y volvería a dar de alta a la persona sin que lo pidiera.
    Por eso la baja apaga también la casilla y saca la dirección de la lista
    principal, que es la única que usa la audiencia automática.
    """
    if not token:
        return None

    try:
        subscriber = Subscriber.objects.get(unsubscribe_token=token)
    except Subscriber.DoesNotExist:
        return None

    # Import local a propósito: `apps.user_auth.models` importa
    # `apps.core.models`, así que subirlo al encabezado invierte la dirección
    # de dependencia entre las dos apps.
    from apps.user_auth.models import Profile

    recipient_list = EmailRecipientList.get_default()
    was_active = subscriber.active
    with transaction.atomic():
        if subscriber.active:
            subscriber.active = False
            subscriber.save(update_fields=['active'])
        if recipient_list is not None:
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=recipient_list
            ).delete()
        # `update()` y no `save()`: guardar el perfil dispara su `post_save`,
        # que llama a `set_consent` y volvería a entrar aquí.
        Profile.objects.filter(user__email__iexact=subscriber.email, newsletter=True).update(
            newsletter=False
        )

    # Solo cuando la baja cambió algo. Un segundo clic en el mismo enlace —o un
    # cliente de correo que reabre la URL— no reenvía nada: el aviso dice «quedó
    # registrada», y mandarlo dos veces por una baja que ya estaba hecha es
    # ruido con apariencia de error.
    if was_active:
        try:
            send_unsubscribe_confirmation(subscriber)
        except Exception:
            # La baja ya está escrita y es lo que la persona pidió. Si el
            # encolado falla (Redis caído), el peor caso es que no reciba el
            # aviso; deshacer la baja o devolver un 500 sería peor.
            logger.exception('No se pudo encolar el correo de baja de %s', subscriber.pk)

    return subscriber


def set_consent(user, enabled, *, source):
    """Aplica la casilla `Profile.newsletter` sin doble opt-in.

    El usuario está autenticado y su correo ya es de confianza: pedirle una
    confirmación por correo sería pedirle que demuestre lo que el login ya
    demostró.

    Desmarcar no crea un suscriptor: si la dirección nunca estuvo en ninguna
    lista no hay nada que apagar, y una fila «confirmada» sin consentimiento
    sería justo la mentira que este servicio existe para evitar. Desmarcar
    tampoco deja la fila a medias: apaga `active` además de borrar la
    pertenencia, para que el estado no diga «no recibe» y «aceptó recibir» a la
    vez.
    """
    email = normalize_email(getattr(user, 'email', ''))
    if not email:
        logger.warning('El usuario %s no tiene correo; no se aplicó consentimiento.', user.pk)
        return None

    recipient_list = EmailRecipientList.get_default()
    if recipient_list is None:
        logger.warning('No hay lista marcada como boletín; no se aplicó consentimiento.')
        return None

    with transaction.atomic():
        subscriber = Subscriber.objects.filter(email=email).first()

        if not enabled:
            if subscriber is None:
                return None
            if subscriber.active:
                subscriber.active = False
                subscriber.save(update_fields=['active'])
            Subscription.objects.filter(
                subscriber=subscriber, recipient_list=recipient_list
            ).delete()
            return subscriber

        if subscriber is None:
            subscriber, _created = Subscriber.objects.get_or_create(
                email=email,
                defaults={
                    'source': source,
                    'confirmed_at': timezone.now(),
                },
            )

        fields = []
        if subscriber.confirmed_at is None:
            subscriber.confirmed_at = timezone.now()
            fields.append('confirmed_at')
        if not subscriber.active:
            subscriber.active = True
            fields.append('active')
        if fields:
            subscriber.save(update_fields=fields)

        Subscription.objects.get_or_create(
            subscriber=subscriber,
            recipient_list=recipient_list,
            defaults={'source': source},
        )

    return subscriber


def move_subscription(old_email, new_email):
    """Mueve la suscripción cuando el usuario cambia de correo.

    Sin esto, quien cambia de dirección sigue recibiendo el boletín en la
    anterior, que puede ser un buzón abandonado o de otra persona.
    """
    old_email = normalize_email(old_email)
    new_email = normalize_email(new_email)
    if not old_email or not new_email or old_email == new_email:
        return None

    try:
        subscriber = Subscriber.objects.get(email=old_email)
    except Subscriber.DoesNotExist:
        return None

    target = Subscriber.objects.filter(email=new_email).first()
    if target is not None and target.pk != subscriber.pk:
        # Ya existe una persona con la dirección nueva: se le suman las
        # pertenencias y la vieja queda inactiva, sin perder el historial.
        for subscription in subscriber.subscriptions.all():
            Subscription.objects.get_or_create(
                subscriber=target,
                recipient_list=subscription.recipient_list,
                defaults={'source': subscription.source},
            )
        subscriber.active = False
        subscriber.save(update_fields=['active'])
        return target

    subscriber.email = new_email
    subscriber.save(update_fields=['email'])
    return subscriber


def confirmation_url(subscriber):
    from django.urls import reverse

    base_url = settings.SITE_URL
    path = reverse('home:newsletter_confirm', args=[subscriber.confirmation_token])
    return base_url + path.lstrip('/')


def unsubscribe_url(subscriber):
    from django.urls import reverse

    base_url = settings.SITE_URL
    path = reverse('home:newsletter_unsubscribe', args=[subscriber.unsubscribe_token])
    return base_url + path.lstrip('/')


def subscribe_url():
    """Alta pública, la misma que exige doble opt-in.

    El correo de baja la enlaza para quien se arrepiente. No se reusa el alta
    directa a propósito: volver a la lista tiene que pasar por la confirmación,
    igual que la primera vez.
    """
    from django.urls import reverse

    return settings.SITE_URL + reverse('home:newsletter_subscribe').lstrip('/')


def send_unsubscribe_confirmation(subscriber):
    """Avisa que la baja quedó registrada.

    Es un correo **transaccional**, no un envío masivo: sale solo, por el mismo
    camino que la confirmación, y no pasa por la audiencia ni por la ventana
    horaria. Sin él, la persona que pulsa el enlace no recibe nada y no sabe si
    la baja se aplicó; y una baja silenciosa es indistinguible de un enlace roto.
    """
    from django.template.loader import render_to_string

    from apps.core.tasks import send_email_task

    context = {
        'subscriber': subscriber,
        'title': 'Su baja quedó registrada',
        'subscribe_url': subscribe_url(),
        'index_url': settings.SITE_URL,
        'current_year': timezone.now().year,
    }
    html_message = render_to_string('emails/newsletter_unsubscribe.html', context)
    send_email_task(
        'Su baja de las novedades del Centro quedó registrada',
        html_message,
        settings.DEFAULT_FROM_EMAIL,
        [subscriber.email],
    )


def send_confirmation(subscriber):
    """Encola el correo de confirmación. No envía en línea."""
    from django.template.loader import render_to_string

    from apps.core.tasks import send_email_task

    context = {
        'subscriber': subscriber,
        'title': 'Confirme su suscripción',
        'confirm_url': confirmation_url(subscriber),
        'unsubscribe_url': unsubscribe_url(subscriber),
        'index_url': settings.SITE_URL,
        'current_year': timezone.now().year,
    }
    html_message = render_to_string('emails/newsletter_confirmation.html', context)
    send_email_task(
        'Confirme su suscripción a las novedades del Centro',
        html_message,
        settings.DEFAULT_FROM_EMAIL,
        [subscriber.email],
    )


def _refresh_confirmation_token_if_expired(subscriber, now):
    """Reemite el token solo cuando el que tiene ya no sirve.

    Reemitirlo siempre sería más simple y peor: invalidaría el enlace del correo
    original, que a las 48 h todavía es válido, y quien lo abriera después del
    recordatorio vería «enlace no válido» con el correo de confirmación delante.
    """
    sent_at = subscriber.confirmation_sent_at
    if sent_at is None or now - sent_at > confirmation_ttl():
        _issue_confirmation_token(subscriber)


def remind_pending_subscribers(now=None):
    """Reenvía una sola vez la confirmación a quien sigue sin confirmar.

    Devuelve cuántos correos se encolaron. El filtro usa `reminder_sent_at` y no
    «sigue pendiente», porque «sigue pendiente» es cierto para siempre y la tarea
    corre a diario: sin esa marca, cada noche saldría otro correo.

    Solo mira a suscriptores `active=True`. Una fila inactiva con
    `confirmed_at` nulo es una baja pedida antes de confirmar, no un alta
    olvidada: recordarle la confirmación a quien ya pidió no recibir nada sería
    lo contrario de lo que pidió.
    """
    now = now or timezone.now()
    cutoff = now - reminder_delay()

    due = Subscriber.objects.filter(
        active=True,
        confirmed_at__isnull=True,
        reminder_sent_at__isnull=True,
        confirmation_sent_at__isnull=False,
        confirmation_sent_at__lt=cutoff,
    )

    sent = 0
    for subscriber in due:
        _refresh_confirmation_token_if_expired(subscriber, now)
        try:
            send_confirmation(subscriber)
        except Exception:
            # Si el encolado falla, `reminder_sent_at` se queda nulo y la tarea
            # de mañana lo reintenta. Marcarlo igual perdería el recordatorio
            # para siempre por un fallo pasajero de la cola.
            logger.exception('No se pudo encolar el recordatorio de %s', subscriber.pk)
            continue
        Subscriber.objects.filter(pk=subscriber.pk).update(reminder_sent_at=now)
        sent += 1

    return sent


def purge_stale_pending(now=None):
    """Borra las altas que nunca se confirmaron y ya no van a hacerlo.

    Devuelve cuántas filas se borraron. Un pendiente no consintió nada —no hay
    consentimiento que conservar ni historial de a quién se le escribió— y
    dejarlo crecer solo engorda la tabla de una gente que nunca entró en la
    lista.

    Un suscriptor confirmado no se borra nunca, ni siquiera dado de baja: ahí sí
    hay un historial de consentimiento y de baja que la baja lógica existe para
    conservar.
    """
    now = now or timezone.now()
    cutoff = now - pending_retention()

    stale = Subscriber.objects.filter(active=True, confirmed_at__isnull=True, created_at__lt=cutoff)
    count = stale.count()
    if count:
        stale.delete()
    return count
