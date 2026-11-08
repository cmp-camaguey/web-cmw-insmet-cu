import shutil
import tempfile

from django.test import TestCase, override_settings


class FileHandlingTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()


def add_confirmed_subscriber(recipient_list, email, *, source='admin'):
    """Suscriptor confirmado y su pertenencia, como una carga del operador.

    Con el esquema de 030 la dirección vive en `Subscriber` y la pertenencia en
    `Subscription`; crear solo una de las dos deja un destinatario que
    `resolve_audience` ignora a propósito.
    """
    from django.utils import timezone

    from apps.core.models import Subscriber, Subscription

    subscriber, _created = Subscriber.objects.get_or_create(
        email=email,
        defaults={'source': source, 'confirmed_at': timezone.now()},
    )
    Subscription.objects.get_or_create(
        subscriber=subscriber,
        recipient_list=recipient_list,
        defaults={'source': source},
    )
    return subscriber


def get_default_list():
    """La lista principal, creándola si la migración no la dejó.

    La migración `core.0003` garantiza una lista principal en cualquier base,
    también en la de test. Los tests que la necesitan no deben crear otra: el
    constraint parcial solo admite una.
    """
    from apps.core.models import EmailRecipientList

    return EmailRecipientList.get_default() or EmailRecipientList.objects.create(
        name='Actualizaciones', is_default=True
    )
