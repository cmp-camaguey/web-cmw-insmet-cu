"""Quién recibe un envío masivo, resuelto por política y no a mano.

Antes cada formulario elegía una FK y `mail_send` la leía; si la FK estaba
vacía, el correo no salía y no quedaba rastro. Ahora la audiencia es una sola y
automática: los suscriptores confirmados y activos de la lista principal.

El operador no elige lista en ningún formulario. La contrapartida es que el
envío tiene que ser un acto explícito (la casilla «Enviar por correo» de cada
publicación), porque un envío automático disparado por cada guardado sería spam
en el buzón de todo el mundo.

Ver `openspec/specs/boletin-y-suscripcion/spec.md`.
"""

from apps.core.models import EmailRecipientList, Subscriber


def resolve_audience():
    """Suscriptores que deben recibir un envío masivo, en orden estable.

    Devuelve objetos `Subscriber` y no solo direcciones porque cada correo
    necesita su propio enlace de baja: mandar todos los destinatarios en un
    mismo `to:` expondría las direcciones entre sí y haría imposible darse de
    baja con un enlace individual.
    """
    recipient_list = EmailRecipientList.get_default()
    if recipient_list is None:
        return []

    return list(
        Subscriber.objects.filter(
            subscriptions__recipient_list=recipient_list,
            active=True,
            confirmed_at__isnull=False,
        )
        .order_by('email')
        .distinct()
    )


def audience_size():
    """Cuántos correos saldrían ahora mismo, sin construir la lista completa."""
    recipient_list = EmailRecipientList.get_default()
    if recipient_list is None:
        return 0
    return (
        Subscriber.objects.filter(
            subscriptions__recipient_list=recipient_list,
            active=True,
            confirmed_at__isnull=False,
        )
        .distinct()
        .count()
    )
