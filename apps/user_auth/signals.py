from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from apps.core.subscriptions import set_consent
from apps.user_auth.models import Profile


@receiver(pre_save, sender=Profile)
def remember_newsletter_state(sender, instance, **kwargs):
    """Guarda el valor anterior de la casilla para detectar un cambio real.

    `post_save` no puede saber qué había antes: cuando corre, la fila ya tiene
    el valor nuevo. Sin esta comparación, cualquier guardado del perfil —cambiar
    el avatar, por ejemplo— reaplicaría la casilla y podría dar de baja sin
    querer a alguien que se suscribió desde el portal público.
    """
    if instance._state.adding:
        instance._previous_newsletter = False
        return
    instance._previous_newsletter = (
        Profile.objects.filter(pk=instance.pk).values_list('newsletter', flat=True).first()
    )


@receiver(post_save, sender=Profile)
def sync_newsletter_recipient(sender, instance, **kwargs):
    """Aplica la casilla `Profile.newsletter` a través del servicio de consentimiento.

    El signal no escribe `Subscriber` ni `Subscription` por su cuenta: la
    decisión de qué significa «aceptó recibir novedades» vive en
    `apps.core.subscriptions`, y aquí solo se dispara.

    Solo actúa cuando la casilla cambió de verdad. Un `Profile` recién creado
    nace con `newsletter=False`: eso no es una negativa de nadie, es el valor
    por defecto, así que no se toca el consentimiento existente.
    """
    user = instance.user
    if not user or not user.email:
        return
    if getattr(instance, '_previous_newsletter', None) == instance.newsletter:
        return
    source = 'registro' if kwargs.get('created') else 'perfil'
    set_consent(user, instance.newsletter, source=source)


@receiver(pre_delete, sender=Profile)
def remove_newsletter_recipient(sender, instance, **kwargs):
    user = instance.user
    if user and user.email:
        set_consent(user, False, source='perfil')
