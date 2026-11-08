from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.core.models import EmailRecipientList, SiteConfiguration, Subscriber, Subscription
from apps.core.tests.base import add_confirmed_subscriber


def disable_maintenance_mode():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


class EmailRecipientListCreateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.superuser = User.objects.create_superuser(
            'admin_recip',
            'admin_recip@example.com',
            'password',
            first_name='Admin',
            last_name='User',
        )
        cls.url = reverse('core:email_recipient_create')

    def test_get_renders_recipients_table_and_add_button(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Destinatarios')
        self.assertContains(response, 'id="recipient-tbody"')
        self.assertContains(response, 'Añadir Destinatario')
        self.assertContains(response, 'id_recipients-TOTAL_FORMS')
        self.assertNotContains(response, 'name="recipients-0-email"')
        self.assertNotContains(response, 'name="recipients-0-uuid"')

    def test_get_renders_coauthor_style_js_with_form_index(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'let formIndex = 0;')
        self.assertContains(response, 'recipientRowHtml(formIndex)')
        self.assertContains(
            response, "tbody.insertAdjacentHTML('beforeend', recipientRowHtml(formIndex))"
        )

    def test_get_does_not_render_broken_delete_name_literal(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'form-${formIndex}-DELETE')
        self.assertNotContains(response, 'recipients-__PREFIX__-email')

    def test_post_creates_list_and_recipients(self):
        self.client.force_login(self.superuser)
        data = {
            'name': 'Lista Create',
            'description': '',
            'recipients-TOTAL_FORMS': '2',
            'recipients-INITIAL_FORMS': '0',
            'recipients-MIN_NUM_FORMS': '0',
            'recipients-MAX_NUM_FORMS': '1000',
            'recipients-0-email': 'uno@example.com',
            'recipients-1-email': 'dos@example.com',
        }
        self.client.post(self.url, data, follow=True)
        lst = EmailRecipientList.objects.filter(name='Lista Create').first()
        self.assertIsNotNone(lst)
        self.assertEqual(lst.subscriptions.count(), 2)


class EmailRecipientListListViewTests(TestCase):
    """El badge anuncia a quién le llega de verdad, no cuántas filas hay.

    La pantalla se usa para decidir a quién se escribe. Contar miembros sin
    confirmar o dados de baja anunciaría un alcance que el envío no tiene:
    `resolve_audience` exige `active` y `confirmed_at`, así que la lista dice
    «3 en la lista» aparte, para que el operador vea la diferencia en vez de
    descubrirla cuando el correo no llega.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.superuser = User.objects.create_superuser(
            'admin_alcance',
            'admin_alcance@example.com',
            'password',
            first_name='Admin',
            last_name='User',
        )
        cls.lst = EmailRecipientList.objects.create(name='Lista Alcance')
        add_confirmed_subscriber(cls.lst, 'confirmado@example.com')

        pendiente = Subscriber.objects.create(email='pendiente@example.com')
        Subscription.objects.create(subscriber=pendiente, recipient_list=cls.lst)

        de_baja = add_confirmed_subscriber(cls.lst, 'baja@example.com')
        de_baja.active = False
        de_baja.save(update_fields=['active'])

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_cuenta_solo_los_correos_que_reciben(self):
        response = self.client.get(reverse('core:email_recipient_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 correo')

    def test_avisa_cuantos_miembros_no_reciben(self):
        response = self.client.get(reverse('core:email_recipient_list'))

        self.assertContains(response, '3 en la lista')

    def test_no_anuncia_las_direcciones_que_no_reciben(self):
        response = self.client.get(reverse('core:email_recipient_list'))

        self.assertContains(response, 'confirmado@example.com')
        self.assertNotContains(response, 'pendiente@example.com')
        self.assertNotContains(response, 'baja@example.com')


class EmailRecipientListUpdateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.superuser = User.objects.create_superuser(
            'admin_recip2',
            'admin_recip2@example.com',
            'password',
            first_name='Admin',
            last_name='User',
        )
        cls.lst = EmailRecipientList.objects.create(name='Lista Update')
        cls.r1 = add_confirmed_subscriber(cls.lst, 'keep@example.com').subscriptions.get()
        cls.r2 = add_confirmed_subscriber(cls.lst, 'drop@example.com').subscriptions.get()
        cls.url = reverse('core:email_recipient_update', args=[cls.lst.pk])

    def test_get_renders_existing_rows_and_matching_form_index(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="recipients-0-email"')
        self.assertContains(response, 'name="recipients-1-email"')
        self.assertContains(response, 'name="recipients-0-uuid"')
        self.assertContains(response, 'recipients-0-DELETE')
        self.assertContains(response, 'recipients-1-DELETE')
        self.assertContains(response, 'let formIndex = 2;')
        self.assertNotContains(response, 'name="recipients-2-email"')
        self.assertNotContains(response, 'name="recipients-2-uuid"')

    def test_post_with_delete_flag_removes_recipient(self):
        self.client.force_login(self.superuser)
        data = {
            'name': 'Lista Update',
            'description': '',
            'recipients-TOTAL_FORMS': '2',
            'recipients-INITIAL_FORMS': '2',
            'recipients-MIN_NUM_FORMS': '0',
            'recipients-MAX_NUM_FORMS': '1000',
            'recipients-0-uuid': str(self.r1.pk),
            'recipients-0-email': 'keep@example.com',
            'recipients-1-uuid': str(self.r2.pk),
            'recipients-1-email': 'drop@example.com',
            'recipients-1-DELETE': 'on',
        }
        self.client.post(self.url, data, follow=True)
        self.assertTrue(Subscription.objects.filter(pk=self.r1.pk).exists())
        self.assertFalse(Subscription.objects.filter(pk=self.r2.pk).exists())
