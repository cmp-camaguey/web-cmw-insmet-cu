"""Garantiza la lista principal y da de alta a quien ya aceptó por perfil.

# data-safety: reviewed: solo crea la lista principal y rellena suscripciones
# desde Profile.newsletter; no borra ni reescribe filas existentes.

El fallo que corrige: en una base sin listas de correo, `resolve_audience`
devolvía cero y `mail_send` avisaba al admin «no llegó a nadie». El operador no
tenía forma de saber que faltaba la lista principal, y marcar «recibir
novedades» en el perfil no servía de nada porque el signal ya se había
disparado antes.

La migración es idempotente: si ya existe una lista principal no la toca, y solo
crea pertenencias que falten.
"""

from django.db import migrations
from django.utils import timezone

DEFAULT_LIST_NAME = 'Actualizaciones'
DEFAULT_LIST_DESCRIPTION = 'Recibe los avisos, pronósticos y boletines del sitio.'


def ensure_default_list_and_backfill(apps, schema_editor):
    EmailRecipientList = apps.get_model('core', 'EmailRecipientList')
    Subscriber = apps.get_model('core', 'Subscriber')
    Subscription = apps.get_model('core', 'Subscription')
    Profile = apps.get_model('user_auth', 'Profile')

    default_list = EmailRecipientList.objects.filter(is_default=True).first()
    if default_list is None:
        default_list = EmailRecipientList.objects.filter(name=DEFAULT_LIST_NAME).first()
        if default_list is None:
            default_list = EmailRecipientList.objects.create(
                name=DEFAULT_LIST_NAME,
                description=DEFAULT_LIST_DESCRIPTION,
                is_default=True,
            )
        else:
            default_list.is_default = True
            default_list.save(update_fields=['is_default'])

    now = timezone.now()
    for profile in Profile.objects.filter(newsletter=True).select_related('user'):
        email = (profile.user.email or '').strip().lower()
        if not email:
            continue
        subscriber, _created = Subscriber.objects.get_or_create(
            email=email,
            defaults={
                'source': 'perfil',
                'confirmed_at': now,
                'active': True,
            },
        )
        # El perfil dice que acepta: si la fila venía de una baja anterior, se
        # reactiva. La baja y el consentimiento son estados distintos.
        if not subscriber.active:
            subscriber.active = True
            subscriber.save(update_fields=['active'])
        Subscription.objects.get_or_create(
            subscriber=subscriber,
            recipient_list=default_list,
            defaults={'source': 'perfil'},
        )


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_newsletter_subscriber_subscription_and_more'),
        ('user_auth', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(ensure_default_list_and_backfill, migrations.RunPython.noop),
    ]
