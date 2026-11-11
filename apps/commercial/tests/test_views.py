import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.commercial.models import (
    Certificate,
    Contract,
    Customer,
    Invoice,
    InvoiceItem,
    Service,
    ServiceSubscription,
)
from apps.commercial.tests.factories import make_user, natural_customer
from apps.commercial.tests.test_forms import _make_png
from apps.core.models import SiteConfiguration


def _make_user(username='testuser', **kwargs):
    data = {
        'first_name': 'Test',
        'last_name': 'User',
        'email': f'{username}@example.com',
    }
    data.update(kwargs)
    return User.objects.create_user(username, **data)


def _make_superuser(username, **kwargs):
    email = kwargs.pop('email', f'{username}@example.com')
    data = {
        'first_name': 'Admin',
        'last_name': 'Super',
    }
    data.update(kwargs)
    return User.objects.create_superuser(username, email, 'pass', **data)


def disable_maintenance_mode():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


class CustomerListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff_user = _make_user('staff', is_staff=True)
        cls.url = reverse('commercial:cliente_list')

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f'/accounts/login/?next={self.url}')

    def test_user_with_permission_can_access(self):
        ct = ContentType.objects.get_for_model(Customer)
        perm = ct.permission_set.get(codename='view_customer')
        self.staff_user.user_permissions.add(perm)
        self.client.force_login(self.staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_natural_muestra_nombre_y_documento_de_identidad(self):
        # Columna unificada: para persona natural se muestra el carnet.
        customer = natural_customer(
            make_user('lista.natural', first_name='Ana', last_name='Norte'),
            identity_document='34111234567',
        )
        ct = ContentType.objects.get_for_model(Customer)
        self.staff_user.user_permissions.add(ct.permission_set.get(codename='view_customer'))
        self.client.force_login(self.staff_user)
        html = self.client.get(self.url).content.decode()
        self.assertIn('Ana Norte', html)
        self.assertIn('34111234567', html)
        self.assertNotIn('>None<', html)
        self.assertNotIn('REEUP', html)
        self.assertNotIn('NIT', html)
        self.assertEqual(customer.client_type, Customer.ClientType.NATURAL)

    def test_juridica_muestra_razon_social_y_datos_fiscales(self):
        natural_customer(make_user('lista.otro', first_name='Luis', last_name='Sur'))
        Customer.objects.create(
            client_type='juridica',
            user=make_user('lista.juridica', first_name='Empresa', last_name='Jurídica'),
            company_name='Empresa Listado S.A.',
            reeup='123.4.5678',
            nit='12345678901',
            account='1234567890123456',
            address='Calle 1',
            phone='71234567',
        )
        ct = ContentType.objects.get_for_model(Customer)
        self.staff_user.user_permissions.add(ct.permission_set.get(codename='view_customer'))
        self.client.force_login(self.staff_user)
        html = self.client.get(self.url).content.decode()
        self.assertIn('Empresa Listado S.A.', html)
        self.assertIn('REEUP', html)
        self.assertIn('123.4.5678', html)
        self.assertIn('NIT', html)
        self.assertIn('12345678901', html)


class CustomerCreateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin')
        cls.url = reverse('commercial:cliente_create')

    def test_get_returns_200(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_creates_customer_and_user(self):
        self.client.force_login(self.admin)
        data = {
            'username': 'newcliente',
            'first_name': 'Ana',
            'last_name': 'Norte',
            'password': 'Pass1234!',
            'password2': 'Pass1234!',
            'email': 'cliente@example.com',
            'client_type': 'juridica',
            'company_name': 'Empresa SL',
            'reeup': '123.4.5678',
            'nit': '12345678901',
            'account': '1234567890123456',
            'agency_bank': 'Banco Test',
            'address': 'Calle 123',
            'phone': '12345678',
        }
        self.client.post(self.url, data, follow=True)
        self.assertTrue(Customer.objects.filter(company_name='Empresa SL').exists())
        self.assertTrue(User.objects.filter(username='newcliente').exists())


class CustomerUpdateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin2')
        cls.owner_user = _make_user('owner')
        cls.other_user = _make_user('other')
        # Use juridica customer with all fields filled to avoid
        # CheckUserProfileMiddleware crash on None fields
        cls.customer = Customer.objects.create(
            client_type='juridica',
            user=cls.owner_user,
            company_name='Test Corp',
            reeup='111.1.1111',
            nit='11111111111',
            account='1234567890123456',
            agency_bank='Banco Test',
            address='Original',
            phone='12345678',
        )
        # Give owner the change_customer permission
        ct = ContentType.objects.get_for_model(Customer)
        perm = ct.permission_set.get(codename='change_customer')
        cls.owner_user.user_permissions.add(perm)

    def test_owner_can_access(self):
        self.client.force_login(self.owner_user)
        url = reverse('commercial:cliente_update', args=[self.customer.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access(self):
        self.client.force_login(self.admin)
        url = reverse('commercial:cliente_update', args=[self.customer.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_other_user_cannot_access(self):
        self.client.force_login(self.other_user)
        url = reverse('commercial:cliente_update', args=[self.customer.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)


class CustomerDeleteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff_user = _make_user('staffdel', is_staff=True)
        cls.customer = natural_customer(
            _make_user('todel'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )

    def test_post_soft_deletes(self):
        ct = ContentType.objects.get_for_model(Customer)
        perm = ct.permission_set.get(codename='delete_customer')
        self.staff_user.user_permissions.add(perm)
        self.client.force_login(self.staff_user)
        url = reverse('commercial:cliente_delete', args=[self.customer.uuid])
        self.client.post(url, follow=True)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.record_active)


class CustomerHardDeleteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin3')
        cls.normal = _make_user('normal')
        cls.customer = natural_customer(
            _make_user('harddelcust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )

    def test_superuser_can_hard_delete(self):
        self.client.force_login(self.admin)
        url = reverse('commercial:cliente_hard_delete', args=[self.customer.uuid])
        pk = self.customer.pk
        self.client.post(url, follow=True)
        self.assertFalse(Customer.objects.filter(pk=pk).exists())

    def test_hard_delete_with_own_user_does_not_crash(self):
        own = _make_superuser('ownadmin')
        own_customer = natural_customer(
            own,
            address='Addr2',
            phone='87654321',
            account='8923456789012345',
            agency_bank='BANDEC',
        )
        self.client.force_login(own)
        url = reverse('commercial:cliente_hard_delete', args=[own_customer.uuid])
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Customer.objects.filter(pk=own_customer.pk).exists())
        self.assertTrue(own.__class__.objects.filter(pk=own.pk).exists())

    def test_normal_user_cannot_hard_delete(self):
        self.client.force_login(self.normal)
        url = reverse('commercial:cliente_hard_delete', args=[self.customer.uuid])
        self.client.post(url, follow=True)
        # Normal user cannot hard delete (customer still exists)
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())


class ServiceListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = _make_user('staffsvc', is_staff=True)
        cls.url = reverse('commercial:servicio_list')

    def test_staff_can_access(self):
        ct = ContentType.objects.get_for_model(Service)
        perm = ct.permission_set.get(codename='view_service')
        self.staff.user_permissions.add(perm)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_non_staff_denied(self):
        user = _make_user('normalsvc')
        ct = ContentType.objects.get_for_model(Service)
        perm = ct.permission_set.get(codename='view_service')
        user.user_permissions.add(perm)
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)


class ServiceCreateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin4')
        cls.url = reverse('commercial:servicio_create')

    def test_post_creates_service(self):
        self.client.force_login(self.admin)
        pdf = SimpleUploadedFile(
            'doc.pdf',
            b'PDF content',
            content_type='application/pdf',
        )
        image = SimpleUploadedFile(
            'img.png',
            _make_png(),
            content_type='image/png',
        )
        data = {
            'title': 'New Service',
            'summary': 'Test summary',
            'service_type': 'public',
            'pdf': pdf,
            'image': image,
        }
        self.client.post(self.url, data, follow=True)
        self.assertTrue(Service.objects.filter(title='New Service').exists())


class ServiceCategorySelectRenderTests(TestCase):
    """categoria-y-layout-servicios — service_category select and side-by-side file layout.

    The ServiceForm exposes service_category (required=False, model default
    'pronostico') but the templates omitted it. These tests pin the rendered
    select: visible only for commercial, hidden for public; and that public
    services show PDF and image side-by-side in the form.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('adminsel')
        cls.public_service = Service.objects.create(
            user=cls.admin,
            title='Public Service',
            summary='Sum',
            service_type='public',
            service_category='agrometeo',
        )
        cls.commercial_service = Service.objects.create(
            user=cls.admin,
            title='Commercial Service',
            summary='Sum',
            service_type='commercial',
            service_category='pronostico',
            code='C200',
            price=Decimal('30.00'),
        )

    def test_create_field_category_hidden_by_default(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('commercial:servicio_create'))
        content = response.content.decode()
        category_tag = re.search(r'<div[^>]*id="field_category"[^>]*>', content)
        self.assertIsNotNone(category_tag)
        self.assertIn('display: none', category_tag.group(0))

    def test_create_select_present_without_required(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('commercial:servicio_create'))
        content = response.content.decode()
        self.assertRegex(
            content,
            r'<select[^>]*\bname="service_category"[^>]*>',
        )
        self.assertRegex(content, r'<option value="agrometeo"[^>]*>Agrometeorológico</option>')
        self.assertRegex(content, r'<option value="pronostico"[^>]*>Pronóstico</option>')
        select_tag = content.split('name="service_category"', 1)[1].split('>', 1)[0]
        self.assertNotIn('required', select_tag)
        self.assertNotRegex(
            content,
            r'<label class="form-label[^>]*required[^>]*for="id_service_category"',
        )

    def test_update_field_category_hidden_for_public(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': self.public_service.uuid})
        )
        content = response.content.decode()
        category_tag = re.search(r'<div[^>]*id="field_category"[^>]*>', content)
        self.assertIsNotNone(category_tag)
        self.assertIn('display:', category_tag.group(0))
        self.assertIn('none', category_tag.group(0))
        self.assertNotIn('block', category_tag.group(0))

    def test_update_field_category_visible_for_commercial_with_preselection(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': self.commercial_service.uuid})
        )
        content = response.content.decode()
        category_tag = re.search(r'<div[^>]*id="field_category"[^>]*>', content)
        self.assertIsNotNone(category_tag)
        self.assertIn('block', category_tag.group(0))
        self.assertRegex(
            content,
            r'<option value="pronostico"\s*selected\s*>Pronóstico</option>',
        )
        self.assertNotRegex(
            content,
            r'<label class="form-label[^>]*required[^>]*for="id_service_category"',
        )

    def test_update_public_form_shows_pdf_and_image_side_by_side(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': self.public_service.uuid})
        )
        content = response.content.decode()
        # Row wraps both fields
        self.assertIn('id="row_files"', content)
        pdf_tag = re.search(r'<div[^>]*id="field_pdf"[^>]*>', content)
        image_tag = re.search(r'<div[^>]*id="field_image"[^>]*>', content)
        self.assertIsNotNone(pdf_tag)
        self.assertIsNotNone(image_tag)
        self.assertIn('col-md-6', pdf_tag.group(0))
        self.assertIn('col-md-6', image_tag.group(0))

    def test_update_commercial_form_keeps_image_full_width(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': self.commercial_service.uuid})
        )
        content = response.content.decode()
        # PDF hidden for commercial
        pdf_tag = re.search(r'<div[^>]*id="field_pdf"[^>]*>', content)
        self.assertIsNotNone(pdf_tag)
        self.assertIn('none', pdf_tag.group(0))
        # Image NOT col-md-6 for commercial (full width)
        image_tag = re.search(r'<div[^>]*id="field_image"[^>]*>', content)
        self.assertIsNotNone(image_tag)
        self.assertNotIn('col-md-6', image_tag.group(0))

    def test_create_title_and_type_balanced_6_6(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('commercial:servicio_create'))
        content = response.content.decode()
        # Título y tipo: cada uno en col-md-6 (parejos)
        title_tag = re.search(
            r'<div class="col-md-6 mb-3">\s*<label[^>]*for="id_title"',
            content,
        )
        type_tag = re.search(
            r'<div class="col-md-6 mb-3">\s*<label[^>]*for="id_service_type"',
            content,
        )
        self.assertIsNotNone(title_tag)
        self.assertIsNotNone(type_tag)

    def test_update_title_and_type_balanced_6_6(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': self.public_service.uuid})
        )
        content = response.content.decode()
        # Título y tipo: cada uno en col-md-6 (parejos)
        title_tag = re.search(
            r'<div class="col-md-6 mb-3">\s*<label[^>]*for="id_title"',
            content,
        )
        type_tag = re.search(
            r'<div class="col-md-6 mb-3">\s*<label[^>]*for="id_service_type"',
            content,
        )
        self.assertIsNotNone(title_tag)
        self.assertIsNotNone(type_tag)

    def test_create_commercial_fields_each_col_4_in_order(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('commercial:servicio_create'))
        content = response.content.decode()
        # Categoría (primera), código y precio: cada uno col-md-4, en ese orden
        category_tag = re.search(r'<div class="col-md-4 mb-3"[^>]*id="field_category"', content)
        code_tag = re.search(r'<div class="col-md-4 mb-3"[^>]*id="field_code"', content)
        price_tag = re.search(r'<div class="col-md-4 mb-3"[^>]*id="field_price"', content)
        self.assertIsNotNone(category_tag)
        self.assertIsNotNone(code_tag)
        self.assertIsNotNone(price_tag)
        self.assertLess(
            category_tag.start(), code_tag.start(), 'categoría debe ir antes que código'
        )
        self.assertLess(code_tag.start(), price_tag.start(), 'código debe ir antes que precio')

    def test_update_commercial_fields_each_col_4_in_order(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': self.commercial_service.uuid})
        )
        content = response.content.decode()
        category_tag = re.search(r'<div class="col-md-4 mb-3"[^>]*id="field_category"', content)
        code_tag = re.search(r'<div class="col-md-4 mb-3"[^>]*id="field_code"', content)
        price_tag = re.search(r'<div class="col-md-4 mb-3"[^>]*id="field_price"', content)
        self.assertIsNotNone(category_tag)
        self.assertIsNotNone(code_tag)
        self.assertIsNotNone(price_tag)
        self.assertLess(
            category_tag.start(), code_tag.start(), 'categoría debe ir antes que código'
        )
        self.assertLess(code_tag.start(), price_tag.start(), 'código debe ir antes que precio')

    def test_create_public_image_is_required(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('commercial:servicio_create'))
        content = response.content.decode()
        # Image input must carry the required attr (public y commercial)
        image_input = re.search(r'<input[^>]*name="image"[^>]*>', content)
        self.assertIsNotNone(image_input)
        self.assertIn('required', image_input.group(0))

    def test_update_image_required_only_when_missing(self):
        self.client.force_login(self.admin)
        # Public service WITHOUT image must require image
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': self.public_service.uuid})
        )
        content = response.content.decode()
        image_input = re.search(r'<input[^>]*name="image"[^>]*>', content)
        self.assertIsNotNone(image_input)
        self.assertIn('required', image_input.group(0))

    def test_update_image_not_required_when_exists(self):
        self.client.force_login(self.admin)
        service = Service.objects.create(
            user=self.admin,
            title='Service With Image',
            summary='Sum',
            service_type='public',
            service_category='pronostico',
            image=SimpleUploadedFile(
                'with_img.png',
                _make_png(),
                content_type='image/png',
            ),
        )
        response = self.client.get(
            reverse('commercial:servicio_update', kwargs={'uuid': service.uuid})
        )
        content = response.content.decode()
        image_input = re.search(r'<input[^>]*name="image"[^>]*>', content)
        self.assertIsNotNone(image_input)
        self.assertNotIn('required', image_input.group(0))


class ServiceDeleteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin5')
        cls.service = Service.objects.create(
            user=cls.admin,
            title='To Delete',
            summary='Del',
            service_type='public',
        )

    def test_post_soft_deletes(self):
        self.client.force_login(self.admin)
        url = reverse('commercial:servicio_delete', args=[self.service.uuid])
        self.client.post(url, follow=True)
        self.service.refresh_from_db()
        self.assertFalse(self.service.record_active)


class ServiceReactivateViewTests(TestCase):
    """servicios-activacion-filtro-home — reactivación de servicios."""

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('adminreact')
        cls.staff = _make_user('staffreact', is_staff=True)
        cls.service = Service.objects.create(
            user=cls.admin,
            title='To Reactivate',
            summary='Reac',
            service_type='public',
        )
        cls.service.record_active = False
        cls.service.save(update_fields=['record_active'])
        cls.url = reverse('commercial:servicio_reactivate', args=[cls.service.uuid])

    def test_post_reactivates_service(self):
        self.client.force_login(self.staff)
        ct = ContentType.objects.get_for_model(Service)
        perm = ct.permission_set.get(codename='change_service')
        self.staff.user_permissions.add(perm)
        self.client.post(self.url, follow=True)
        self.service.refresh_from_db()
        self.assertTrue(self.service.record_active)
        self.assertIsNone(self.service.deleted_at)

    def test_post_when_already_active_is_noop_warning(self):
        self.client.force_login(self.staff)
        ct = ContentType.objects.get_for_model(Service)
        perm = ct.permission_set.get(codename='change_service')
        self.staff.user_permissions.add(perm)
        self.service.record_active = True
        self.service.save(update_fields=['record_active'])
        self.client.post(self.url, follow=True)
        self.service.refresh_from_db()
        self.assertTrue(self.service.record_active)

    def test_login_required(self):
        response = self.client.post(self.url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(
            response, f'/accounts/login/?next={self.url}', fetch_redirect_response=False
        )


class ServiceUpdateTypeImmutableTests(TestCase):
    """servicios-activacion-filtro-home — el tipo no se cambia en edición."""

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admintypelock')
        cls.service = Service.objects.create(
            user=cls.admin,
            title='Locked Type',
            summary='Sum',
            service_type='public',
            service_category='agrometeo',
            image=SimpleUploadedFile(
                'lock.png',
                _make_png(),
                content_type='image/png',
            ),
        )
        cls.url = reverse('commercial:servicio_update', args=[cls.service.uuid])

    def test_update_select_is_disabled_with_hidden_input(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        content = response.content.decode()
        select_tag = re.search(r'<select[^>]*name="service_type"[^>]*>', content)
        self.assertIsNotNone(select_tag)
        self.assertIn('disabled', select_tag.group(0))
        hidden = re.search(
            r'<input[^>]*type="hidden"[^>]*name="service_type"[^>]*value="public"[^>]*>',
            content,
        )
        self.assertIsNotNone(hidden)

    def test_post_cannot_change_service_type(self):
        self.client.force_login(self.admin)
        # Manipulated POST tries to switch to commercial; the view forces the
        # original type (public), so the save must keep it.
        self.client.post(
            self.url,
            {
                'title': 'Locked Type',
                'summary': 'Sum',
                'service_type': 'commercial',
            },
            follow=True,
        )
        self.service.refresh_from_db()
        self.assertEqual(self.service.service_type, 'public')


class ServiceListRenderTests(TestCase):
    """servicios-activacion-filtro-home — render del listado de servicios."""

    PAGINATE_BY = 10

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('adminlistrender')
        cls.public_service = Service.objects.create(
            user=cls.admin,
            title='Render Público',
            summary='Sum',
            service_type='public',
        )
        cls.commercial_service = Service.objects.create(
            user=cls.admin,
            title='Render Comercial',
            summary='Sum',
            service_type='commercial',
            code='C900',
            price=Decimal('45.50'),
        )
        cls.url = reverse('commercial:servicio_list')

    def test_public_row_shows_em_dash_for_price_and_subscriptions(self):
        self.client.force_login(self.admin)
        html = self.client.get(self.url).content.decode()
        self.assertEqual(html.count('<span class="text-muted">—</span>'), 2)
        self.assertNotIn('$0.00', html)

    def test_commercial_row_keeps_price_and_subscription_count(self):
        self.client.force_login(self.admin)
        html = self.client.get(self.url).content.decode()
        self.assertIn('$45,50', html)
        self.assertIn('0', html)

    def test_reactivate_button_only_for_inactive_services(self):
        self.service_inactive = Service.objects.create(
            user=self.admin,
            title='Inactivo para reactivar',
            summary='Sum',
            service_type='public',
        )
        self.service_inactive.record_active = False
        self.service_inactive.save(update_fields=['record_active'])
        self.client.force_login(self.admin)
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-action="reactivar"', html)
        self.assertIn('Reactivar servicio', html)


class SubscriptionListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = _make_user('staffsub', is_staff=True)
        cls.url = reverse('commercial:suscripcion_list')

    def test_staff_can_access(self):
        ct = ContentType.objects.get_for_model(ServiceSubscription)
        perm = ct.permission_set.get(codename='view_subscription')
        self.staff.user_permissions.add(perm)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


class SubscriptionListOwnerAccessTests(TestCase):
    """El titular de un Customer entra a "Mis Suscripciones" sin el permiso.

    Los correos de factura y certificado enlazan «Ver en línea» a
    ``commercial:suscripcion_list``, pero el cliente titular no siempre tiene
    ``commercial.view_subscription``: el grupo ``Clientes`` sólo lo otorga si
    el registro público lo creó primero, mientras que los caminos del dashboard
    crean el grupo vacío. El titular debe entrar y ver sólo sus suscripciones.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = _make_user('staffownerlist', is_staff=True)
        cls.owner = _make_user('ownerlist')
        cls.other = _make_user('otherlist')
        cls.customer = natural_customer(cls.owner)
        cls.other_customer = natural_customer(cls.other)
        cls.service = Service.objects.create(
            user=cls.staff,
            title='Svc Titular Listado',
            summary='S',
            service_type='commercial',
            code='OWN001',
            price=Decimal('30.00'),
        )
        cls.other_service = Service.objects.create(
            user=cls.staff,
            title='Svc Ajeno Listado',
            summary='S',
            service_type='commercial',
            code='OWN002',
            price=Decimal('40.00'),
        )
        cls.sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=cls.service,
            start_date=timezone.now(),
            payment_status='pending',
        )
        cls.other_sub = ServiceSubscription.objects.create(
            customer=cls.other_customer,
            service=cls.other_service,
            start_date=timezone.now(),
            payment_status='paid',
        )
        cls.url = reverse('commercial:suscripcion_list')

    def _assert_solo_lo_suyo(self, response):
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(self.service.title, html)
        self.assertIn(f'data-uuid="{self.sub.uuid}"', html)
        self.assertNotIn(self.other_service.title, html)
        self.assertNotIn(f'data-uuid="{self.other_sub.uuid}"', html)

    def test_titular_without_permission_or_group_sees_only_his_subscriptions(self):
        self.assertFalse(self.owner.groups.filter(name='Clientes').exists())
        self.client.force_login(self.owner)
        self._assert_solo_lo_suyo(self.client.get(self.url))

    def test_titular_in_clientes_group_without_permission_sees_only_his(self):
        # Camino del dashboard: el grupo `Clientes` existe pero quedó vacío
        # (sin `view_subscription`), como lo crean CustomerCreateView y
        # CustomerFromUserView.
        from django.contrib.auth.models import Group

        group, _ = Group.objects.get_or_create(name='Clientes')
        self.owner.groups.add(group)
        self.assertFalse(self.owner.has_perm('commercial.view_subscription'))
        self.client.force_login(self.owner)
        self._assert_solo_lo_suyo(self.client.get(self.url))

    def test_user_without_customer_and_without_permission_gets_403(self):
        self.client.force_login(_make_user('nocustomerlist'))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_user_with_permission_but_without_customer_gets_403(self):
        # No se amplía lo que ve un rol ajeno: con el permiso directo pero sin
        # Customer el resultado sigue siendo 403, igual que antes.
        user = _make_user('permnocustomer')
        ct = ContentType.objects.get_for_model(ServiceSubscription)
        user.user_permissions.add(ct.permission_set.get(codename='view_subscription'))
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_clientes_group_without_customer_still_sees_empty_list(self):
        # La rama del grupo `Clientes` sin Customer queda intacta: lista vacía
        # con 200, no un 403 (comportamiento previo que este cambio no toca).
        from django.contrib.auth.models import Group

        ct = ContentType.objects.get_for_model(ServiceSubscription)
        group, _ = Group.objects.get_or_create(name='Clientes')
        group.permissions.add(ct.permission_set.get(codename='view_subscription'))
        user = _make_user('groupnocustomer')
        user.groups.add(group)
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn(self.service.title, html)
        self.assertNotIn(self.other_service.title, html)

    def test_superuser_still_sees_every_subscription(self):
        self.client.force_login(_make_superuser('ownerlistadmin'))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(self.service.title, html)
        self.assertIn(self.other_service.title, html)


class InvoiceListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.user = _make_user('staffinv', is_staff=True)
        cls.url = reverse('commercial:factura_list')

    def test_staff_can_access(self):
        ct = ContentType.objects.get_for_model(Invoice)
        perm = ct.permission_set.get(codename='view_invoice')
        self.user.user_permissions.add(perm)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


class InvoiceListStatusColumnTests(TestCase):
    """La columna Estado del listado muestra el estado DERIVADO de la factura
    (pagada / pendiente / cancelada), no un "Activa/Anulada" desconectado del
    pago real de sus suscripciones."""

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('invstatus')
        customer = natural_customer(
            _make_user('invstatuscust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        service = Service.objects.create(
            user=cls.admin,
            title='Svc estado',
            summary='Svc estado',
            service_type='commercial',
            code='E100',
            price=Decimal('50.00'),
        )
        cls.customer = customer
        cls.service = service
        cls.url = reverse('commercial:factura_list')

    def _subscription(self, status):
        return ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now(),
            payment_status=status,
        )

    def _invoice(self, number, subscription=None, is_cancelled=False):
        # El vínculo canónico es la línea (InvoiceItem); el ancla
        # `subscription` sólo existe en facturas de una única suscripción y no
        # participa del cálculo del estado derivado.
        invoice = Invoice.objects.create(
            customer=self.customer,
            subscription=subscription,
            number=number,
            amount=Decimal('50.00'),
            is_cancelled=is_cancelled,
        )
        if subscription is not None:
            InvoiceItem.objects.create(
                invoice=invoice,
                subscription=subscription,
                descripcion='Svc estado',
                cantidad=Decimal('1'),
                precio=Decimal('50.00'),
            )
        return invoice

    def _render(self):
        self.client.force_login(self.admin)
        return self.client.get(self.url).content.decode()

    def test_paid_subscription_renders_pagada(self):
        sub = self._subscription('paid')
        self._invoice('F-PAG', subscription=sub)
        self.assertIn('>Pagada<', self._render())

    def test_pending_subscription_renders_pendiente(self):
        sub = self._subscription('pending')
        self._invoice('F-PEN', subscription=sub)
        html = self._render()
        self.assertIn('>Pendiente<', html)
        self.assertNotIn('>Pagada<', html)

    def test_cancelled_invoice_renders_cancelada(self):
        sub = self._subscription('pending')
        self._invoice('F-CAN', subscription=sub, is_cancelled=True)
        html = self._render()
        self.assertIn('>Cancelada<', html)
        self.assertNotIn('>Activa<', html)

    def _query_count_for(self, rows):
        sub = self._subscription('paid')
        for i in range(rows):
            self._invoice(f'F-QRY-{rows}-{i}', subscription=sub)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_list_view_annotates_display_status_without_n_plus_one(self):
        # La columna Estado se resuelve con items_total/items_paid anotados por
        # `with_display_status`. Sin esa anotación, `Invoice.status_display`
        # consultaría items por fila: el conteo crecería con el tamaño del
        # listado. Se comparan dos tamaños en vez de fijar un número absoluto,
        # porque el layout y los context processors aportan consultas fijas.
        self.client.force_login(self.admin)
        queries_one = self._query_count_for(1)
        queries_four = self._query_count_for(4)
        # 3 filas extra no deben sumar consultas.
        self.assertEqual(queries_one, queries_four)


class CancelInvoiceViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin6')
        customer = natural_customer(
            _make_user('cancust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        cls.invoice = Invoice.objects.create(
            customer=customer,
            number='INV-CAN',
            amount=Decimal('100.00'),
        )

    def test_post_cancels_invoice(self):
        self.client.force_login(self.admin)
        url = reverse('commercial:factura_cancel', args=[self.invoice.uuid])
        self.client.post(url, follow=True)
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.is_cancelled)

    @patch('apps.commercial.views.invoice_utils.generate_invoice_pdf_standalone')
    def test_download_generates_pdf_if_missing(self, mock_gen):
        def _fake_gen(invoice, customer, items):
            invoice.pdf.save('factura_test.pdf', ContentFile(b'%PDF-1.4 test'))

        mock_gen.side_effect = _fake_gen
        self.client.force_login(self.admin)
        url = reverse('commercial:factura_download', args=[self.invoice.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 test')
        # Se generó bajo demanda porque el PDF no existía.
        mock_gen.assert_called_once()


class ContractListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.user = _make_user('staffcon', is_staff=True)
        cls.url = reverse('commercial:contrato_list')

    def test_staff_can_access(self):
        ct = ContentType.objects.get_for_model(Contract)
        perm = ct.permission_set.get(codename='view_contract')
        self.user.user_permissions.add(perm)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


class ContractCreateViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin7')
        cls.customer = natural_customer(
            _make_user('conccust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        service = Service.objects.create(
            user=cls.admin,
            title='Svc',
            summary='Svc',
            service_type='commercial',
            code='C100',
            price=Decimal('50.00'),
        )
        cls.sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=service,
            start_date=timezone.now(),
        )
        cls.url = reverse('commercial:contrato_create')

    def test_post_creates_contract(self):
        self.client.force_login(self.admin)
        data = {
            'customer': self.customer.pk,
            'number': 'CONT-001',
            'start_date': date.today().isoformat(),
            'validity_years': '2',
        }
        self.client.post(self.url, data, follow=True)
        self.assertTrue(Contract.objects.filter(number='CONT-001').exists())

    def test_invalid_post_repopulates_values(self):
        self.client.force_login(self.admin)
        data = {
            'number': 'CONT-002',
            'start_date': date.today().isoformat(),
            'validity_years': '2',
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="CONT-002"')
        self.assertContains(response, f'value="{date.today().strftime("%d/%m/%Y")}"')


class ContractDeleteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin8')
        customer = natural_customer(
            _make_user('delcust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        cls.contract = Contract.objects.create(
            customer=customer,
            number='CONT-DEL',
            start_date=date.today(),
        )

    def test_post_soft_deletes(self):
        self.client.force_login(self.admin)
        url = reverse('commercial:contrato_delete', args=[self.contract.uuid])
        self.client.post(url, follow=True)
        self.contract.refresh_from_db()
        self.assertFalse(self.contract.record_active)


class CertificateListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.user = _make_user('staffcert', is_staff=True)
        cls.url = reverse('commercial:certificado_list')

    def test_staff_can_access(self):
        ct = ContentType.objects.get_for_model(Certificate)
        perm = ct.permission_set.get(codename='view_certificate')
        self.user.user_permissions.add(perm)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


class CertificateDeleteViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('admin9')
        customer = natural_customer(
            _make_user('certcust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        service = Service.objects.create(
            user=cls.admin,
            title='Svc3',
            summary='Svc3',
            service_type='commercial',
            code='C102',
            price=Decimal('70.00'),
        )
        sub = ServiceSubscription.objects.create(
            customer=customer,
            service=service,
            start_date=timezone.now(),
        )
        pdf = SimpleUploadedFile(
            'cert.pdf',
            b'PDF',
            content_type='application/pdf',
        )
        cls.cert = Certificate.objects.create(
            subscription=sub,
            pdf=pdf,
        )

    def test_post_soft_deletes(self):
        self.client.force_login(self.admin)
        url = reverse('commercial:certificado_delete', args=[self.cert.uuid])
        self.client.post(url, follow=True)
        self.cert.refresh_from_db()
        self.assertFalse(self.cert.record_active)


def _make_customer(username):
    customer = natural_customer(
        _make_user(username),
        account='1234567890123456',
        agency_bank='BANDEC',
        address='Calle 10',
        phone='12345678',
    )
    return customer


def _build_request(user, method='GET', path='/'):
    """Build a GET request with the user attached, plus messages storage."""
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    factory = RequestFactory()
    request = factory.get(path)
    request.user = user
    request.session = {}
    messages = FallbackStorage(request)
    request._messages = messages
    return request


class AjaxPendingSubscriptionsTests(TestCase):
    """Permission + data-quantity on the AJAX endpoint."""

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.customer = _make_customer('ajcust')
        service = Service.objects.create(
            user=_make_user('ajprov'),
            title='Agro Svc',
            summary='Sum',
            service_type='commercial',
            service_category='agrometeo',
            price=Decimal('120.00'),
        )
        cls.sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=service,
            start_date=timezone.now(),
            quantity=2,
            payment_status='requested',
        )
        cls.url = reverse('commercial:ajax_pending_subscriptions')

    def test_staff_with_view_subscription_gets_200(self):
        staff = _make_user('staffajax', is_staff=True)
        ct = ContentType.objects.get_for_model(ServiceSubscription)
        perm = ct.permission_set.get(codename='view_subscription')
        staff.user_permissions.add(perm)
        self.client.force_login(staff)
        response = self.client.get(self.url, {'customer': self.customer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-quantity="2"')

    def test_anonymous_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(self.url, {'customer': self.customer.pk})
        self.assertEqual(response.status_code, 302)


class BatchInvoiceQuantityTests(TestCase):
    """process_batch_invoice uses quantity × price, and `U/M` stays `U`.

    La columna `U/M` de las facturas reales del CMP nunca dice `MES` ni `DÍA`: dice
    `U` o nada, con cantidades de 30, 31, 2 y 4. La unidad de la fila vive en el
    rótulo de `cantidad`, no en `unidad_medida`, así que el lote deja que el
    `default='U'` del modelo sea el dueño del valor.
    """

    def test_agrometeo_batch_uses_monthly_quantity(self):
        from apps.commercial.views.invoices import InvoiceCreateView

        customer = _make_customer('batchcust')
        provider = _make_user('batchprov')
        service = Service.objects.create(
            user=provider,
            title='Agro',
            summary='S',
            service_type='commercial',
            service_category='agrometeo',
            code='B001',
            price=Decimal('100.00'),
        )
        sub = ServiceSubscription.objects.create(
            customer=customer,
            service=service,
            start_date=timezone.make_aware(datetime(2025, 5, 15)),
            quantity=3,
        )
        view = InvoiceCreateView()
        admin = _make_superuser('batchadmin')
        view.request = _build_request(admin)
        view.process_batch_invoice(customer, 'REG-001', [sub])
        item = InvoiceItem.objects.get(subscription=sub)
        invoice = item.invoice
        self.assertEqual(item.cantidad, 3)
        self.assertEqual(item.importe, Decimal('300.00'))
        self.assertEqual(item.unidad_medida, 'U')
        self.assertEqual(invoice.amount, Decimal('300.00'))

    def test_pronostico_batch_uses_daily_quantity(self):
        from apps.commercial.views.invoices import InvoiceCreateView

        customer = _make_customer('batchcust2')
        provider = _make_user('batchprov2')
        service = Service.objects.create(
            user=provider,
            title='Daily',
            summary='S',
            service_type='commercial',
            service_category='pronostico',
            code='B002',
            price=Decimal('5.00'),
        )
        sub = ServiceSubscription.objects.create(
            customer=customer,
            service=service,
            start_date=timezone.make_aware(datetime(2025, 5, 20)),
            quantity=10,
        )
        view = InvoiceCreateView()
        admin = _make_superuser('batchadmin2')
        view.request = _build_request(admin)
        view.process_batch_invoice(customer, 'REG-002', [sub])
        item = InvoiceItem.objects.get(subscription=sub)
        invoice = item.invoice
        self.assertEqual(item.cantidad, 10)
        self.assertEqual(item.importe, Decimal('50.00'))
        self.assertEqual(item.unidad_medida, 'U')
        self.assertEqual(invoice.amount, Decimal('50.00'))

    def test_facturar_no_sobreescribe_el_inicio_de_la_suscripcion(self):
        """La fecha de inicio es del acuerdo con el cliente, no de la factura.

        Facturar solía copiar el inicio del período facturado sobre
        `start_date`. Eso convertía un dato que el cliente eligió en un valor
        derivado de la factura, y hacía que el mismo servicio pareciera haber
        empezado en la fecha del cobro.
        """
        from apps.commercial.views.invoices import InvoiceCreateView

        customer = _make_customer('batchstart')
        provider = _make_user('batchstartprov')
        service = Service.objects.create(
            user=provider,
            title='Agro',
            summary='S',
            service_type='commercial',
            service_category='agrometeo',
            code='B003',
            price=Decimal('100.00'),
        )
        elegido = timezone.make_aware(datetime(2025, 9, 1))
        sub = ServiceSubscription.objects.create(
            customer=customer,
            service=service,
            start_date=elegido,
            quantity=3,
        )
        view = InvoiceCreateView()
        view.request = _build_request(_make_superuser('batchstartadmin'))

        # El período facturado no es el del contrato: se deduce del inicio y la cantidad.
        facturas = view.process_batch_invoice(customer, 'REG-003', [sub])

        sub.refresh_from_db()
        self.assertEqual(sub.start_date, elegido)
        self.assertEqual(sub.payment_status, 'pending')
        self.assertEqual(facturas[0].period_label, 'De septiembre a noviembre de 2025')

    def test_las_suscripciones_que_no_comparten_meses_van_a_facturas_distintas(self):
        """El agrupamiento vuelve, pero por el período *facturado*, no por vencimiento.

        La vista ya no separa por fecha de expiración porque no existe. Ahora separa
        por los meses que ocupa cada suscripción, que es el dato contra el que se
        valida cada línea: meterlas en una factura obligaría a un período que
        ninguna de las dos ocupa.
        """
        from apps.commercial.views.invoices import InvoiceCreateView

        customer = _make_customer('batchgroup')
        provider = _make_user('batchgroupprov')
        agro = Service.objects.create(
            user=provider,
            title='Agro',
            summary='S',
            service_type='commercial',
            service_category='agrometeo',
            code='B004',
            price=Decimal('100.00'),
        )
        diario = Service.objects.create(
            user=provider,
            title='Diario',
            summary='S',
            service_type='commercial',
            service_category='pronostico',
            code='B005',
            price=Decimal('5.00'),
        )
        # Enero con 3 meses ocupa enero-marzo; el pronóstico de 10 días sólo enero; y
        # agro starting el 28 con 2 meses, enero-febrero. Tres períodos distintos.
        subs = [
            ServiceSubscription.objects.create(
                customer=customer,
                service=servicio,
                start_date=timezone.make_aware(datetime(2025, 1, dia)),
                quantity=cantidad,
            )
            for dia, servicio, cantidad in ((1, agro, 3), (20, diario, 10), (28, agro, 2))
        ]

        view = InvoiceCreateView()
        view.request = _build_request(_make_superuser('batchgroupadmin'))
        facturas = view.process_batch_invoice(customer, 'REG-004', subs)

        self.assertEqual(Invoice.objects.filter(customer=customer).count(), 1)
        factura = facturas[0]
        self.assertEqual(factura.period_label, 'De enero a marzo de 2025')
        self.assertEqual(
            factura.amount,
            Decimal('100.00') * 3 + Decimal('5.00') * 10 + Decimal('100.00') * 2,
        )
        for item in factura.items.all():
            inicio = timezone.localtime(item.subscription.start_date).date()
            self.assertTrue(
                factura.period_start <= inicio <= factura.period_end,
                f'{inicio} no cae en el período de {factura.period_label}',
            )
        for sub in subs:
            sub.refresh_from_db()
            self.assertEqual(sub.payment_status, 'pending')


class SubscriptionListStateAndActionsTests(TestCase):
    """Columna Estado única y acciones ordenadas en el listado de suscripciones.

    Antes existían tres columnas redundantes (Estado / Pago / Registro) que
    además mostraban un "Expirado" imposible, y los dos botones de PDF
    compartían el mismo label "Ver PDF", imposible de distinguir.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('substate')
        customer = natural_customer(
            _make_user('substatecust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        service = Service.objects.create(
            user=cls.admin,
            title='Svc estado sub',
            summary='Svc estado sub',
            service_type='commercial',
            code='S100',
            price=Decimal('50.00'),
        )
        cls.customer = customer
        cls.service = service
        cls.url = reverse('commercial:suscripcion_list')

    def _subscription(self, status):
        return ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now(),
            payment_status=status,
        )

    def _invoice_with_pdf(self, subscription):
        # Factura con PDF real (`pdf_status` + archivo) y su item: es la única
        # combinación con la que las anotaciones `latest_invoice_*` del listado
        # resuelven `latest_invoice_pdf_ready=True`.
        invoice = Invoice.objects.create(
            subscription=subscription,
            number=f'F-{subscription.uuid.hex[:8].upper()}',
            amount=Decimal('50.00'),
            pdf='factura/invoice/live-status.pdf',
            pdf_status=Invoice.PdfStatus.READY,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            subscription=subscription,
            descripcion='Svc estado sub',
            cantidad=Decimal('1'),
            precio=Decimal('50.00'),
        )
        return invoice

    def _render(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_single_status_column_replaces_payment_and_registry_columns(self):
        self._subscription('paid')
        html = self._render()
        self.assertIn('<th>Estado</th>', html)
        self.assertNotIn('<th>Pago</th>', html)
        self.assertNotIn('<th>Registro</th>', html)
        # El estado derivado reemplaza a los labels viejos.
        self.assertIn('>Pagado<', html)
        self.assertNotIn('Expirado', html)
        self.assertNotIn('Desactivada', html)

    def test_cancelled_subscription_renders_cancelada_state(self):
        # `SoftDeleteModel` no filtra en el manager: el listado staff usa
        # `super().get_queryset()` sin filtrar, así que la suscripción dada de
        # baja SÍ aparece y la columna Estado debe decir "Cancelada" (antes
        # decía "Desactivada" en la columna Registro, que se eliminó).
        cancelled = self._subscription('requested')
        service_title = cancelled.service.title
        cancelled.delete()
        self.assertEqual(cancelled.status_display, 'cancelada')
        html = self._render()
        self.assertIn(service_title, html)
        self.assertIn('>Cancelada<', html)

    def test_document_buttons_are_distinguishable_by_label(self):
        sub = self._subscription('paid')
        invoice = Invoice.objects.create(
            subscription=sub,
            number='F-LBL-001',
            amount=Decimal('50.00'),
            # Los botones de documento sólo aparecen si el PDF está listo de
            # verdad (`pdf_status` + archivo). Una factura sin PDF no los lleva,
            # y el test es sobre las etiquetas, no sobre la existencia del PDF.
            pdf='factura/invoice/F-LBL-001.pdf',
            pdf_status=Invoice.PdfStatus.READY,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            subscription=sub,
            descripcion='Svc estado sub',
            cantidad=Decimal('1'),
            precio=Decimal('50.00'),
        )
        Certificate.objects.create(
            subscription=sub,
            pdf=SimpleUploadedFile('cert-state.pdf', b'%PDF-1.4 t', 'application/pdf'),
        )
        html = self._render()
        self.assertIn('aria-label="Ver Factura"', html)
        self.assertIn('aria-label="Ver Certificado"', html)
        # Ninguno conserva el label ambiguo.
        self.assertNotIn('aria-label="Ver PDF"', html)

    def test_edit_precedes_destructive_action_in_the_row(self):
        sub = self._subscription('pending')
        # "Aprobar Pago" sólo se muestra con PDF listo (gate T3), así que la
        # suscripción pendiente necesita su factura con PDF real e item.
        self._invoice_with_pdf(sub)
        html = self._render()
        edit_pos = html.index('title="Editar"')
        delete_pos = html.index('title="Anular suscripción"')
        self.assertLess(edit_pos, delete_pos)
        # El botón de factura/QR del estado va antes de Editar.
        self.assertLess(html.index('title="Aprobar Pago"'), edit_pos)


class SubscriptionCancelViewTests(TestCase):
    """La anulación es el único final de una suscripción, y es un bloqueo económico.

    Una suscripción no vence por tiempo, así que no hay fecha que la cierre: sólo
    la puede anular el operador. Lo que no puede es deshacer un cobro ya emitido,
    así que el servidor rechaza la anulación en cuanto hay pago aprobado o una
    factura vigente. La comprobación vive en la vista y no en la plantilla,
    porque el botón tampoco es la garantía: un POST directo la sortea.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('canceladmin')
        customer = natural_customer(
            _make_user('cancelcust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        service = Service.objects.create(
            user=cls.admin,
            title='Svc anulable',
            summary='Svc anulable',
            service_type='commercial',
            code='CANCEL',
            price=Decimal('50.00'),
        )
        cls.customer = customer
        cls.service = service
        cls.list_url = reverse('commercial:suscripcion_list')

    def _subscription(self, status='requested'):
        return ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now(),
            payment_status=status,
        )

    def _invoice(self, sub, number, is_cancelled=False):
        """Factura con una línea que apunta a la suscripción.

        El vínculo que la vista consulta es `invoice_items`, no
        `invoice.subscription`: una factura por lote deja el ancla en NULL.
        """
        invoice = Invoice.objects.create(
            subscription=sub,
            customer=self.customer,
            number=number,
            amount=Decimal('50.00'),
            is_cancelled=is_cancelled,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            subscription=sub,
            codigo='CANCEL',
            descripcion='Svc anulable',
            cantidad=1,
            unidad_medida='U',
            precio=Decimal('50.00'),
        )
        return invoice

    def _anular(self, sub):
        # Anular avisa al cliente por correo: con la cola real el envío queda
        # pendiente y ensucia a los tests que después cuentan la cola. En
        # proceso sale por el backend de pruebas y no deja rastro.
        from config.huey import huey

        self.client.force_login(self.admin)
        old = huey.immediate
        huey.immediate = True
        try:
            return self.client.post(
                reverse('commercial:suscripcion_cancel', args=[sub.uuid]),
            )
        finally:
            huey.immediate = old

    def test_rechaza_anular_una_suscripcion_pagada(self):
        # El pago aprobado es el punto sin retorno: anular aquí dejaría al
        # cliente con un cobro emitido y sin servicio.
        sub = self._subscription('paid')
        response = self._anular(sub)
        self.assertRedirects(response, self.list_url)
        sub.refresh_from_db()
        self.assertTrue(sub.record_active)

    def test_rechaza_anular_con_una_factura_no_anulada(self):
        # Factura emitida y todavía no cobrada: el cobro está comprometido aunque
        # el pago no se haya aprobado.
        sub = self._subscription('pending')
        self._invoice(sub, 'F-CANCEL-001')
        response = self._anular(sub)
        self.assertRedirects(response, self.list_url)
        sub.refresh_from_db()
        self.assertTrue(sub.record_active)

    def test_permite_anular_cuando_la_unica_factura_esta_anulada(self):
        """Caso borde: una factura cancelada ya no compromete un cobro.

        Si la factura se anuló, no hay nada pendiente de cobrar y el cliente puede
        retirar la solicitud. Es la diferencia entre "tiene factura" y "tiene
        factura vigente", y una de las dos hace la anulación imposible.
        """
        sub = self._subscription('pending')
        self._invoice(sub, 'F-CANCEL-002', is_cancelled=True)
        response = self._anular(sub)
        self.assertRedirects(response, self.list_url)
        sub.refresh_from_db()
        self.assertFalse(sub.record_active)
        self.assertEqual(sub.status_display, 'cancelada')

    def test_permite_anular_una_solicitud_sin_facturas(self):
        # El camino normal de cancelación: nada emitido, nada que deshacer.
        sub = self._subscription('requested')
        response = self._anular(sub)
        self.assertRedirects(response, self.list_url)
        sub.refresh_from_db()
        self.assertFalse(sub.record_active)

    def test_no_anula_dos_veces(self):
        sub = self._subscription('requested')
        self._anular(sub)
        self._anular(sub)
        sub.refresh_from_db()
        self.assertFalse(sub.record_active)
        self.assertIsNotNone(sub.deleted_at)

    def test_una_factura_anulada_no_oculta_la_vigente(self):
        # Dos facturas: una anulada y otra vigente. La anulada no debe tapar a la
        # que sí compromete el cobro.
        sub = self._subscription('pending')
        self._invoice(sub, 'F-CANCEL-003', is_cancelled=True)
        self._invoice(sub, 'F-CANCEL-004')
        self._anular(sub)
        sub.refresh_from_db()
        self.assertTrue(sub.record_active)


class VencimientoFueraDeLaSuscripcionTests(TestCase):
    """`end_date` no existe, y tampoco puede quedar vestigio de él en pantalla.

    El modelo ya no tiene fecha de expiración, así que cualquier superficie que
    la iba a mostrar o exportar es una referencia a un dato que ya no se captura:
    o imprime vacío o inventa una columna que el operador tiene que ignorar. Se
    afirma en el modelo y en cada superficie que lo mostraba.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.admin = _make_superuser('novenc')
        cls.customer = natural_customer(
            _make_user('novenccust'),
            address='Addr',
            phone='12345678',
            account='1234567890123456',
        )
        cls.service = Service.objects.create(
            user=cls.admin,
            title='Svc sin vencimiento',
            summary='Svc sin vencimiento',
            service_type='commercial',
            code='NOVENC',
            price=Decimal('50.00'),
        )
        cls.sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=cls.service,
            start_date=timezone.now(),
            payment_status='paid',
        )

    def test_el_modelo_no_tiene_campo_de_vencimiento(self):
        campos = {f.name for f in ServiceSubscription._meta.get_fields()}
        self.assertNotIn('end_date', campos)

    def test_el_listado_no_muestra_ninguna_columna_de_vencimiento(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse('commercial:suscripcion_list')).content.decode()
        self.assertIn(self.service.title, html)
        self.assertNotIn('Vencimiento', html)
        self.assertNotIn('Expiraci', html)

    def test_el_listado_no_imprime_ninguna_fecha_de_vencimiento(self):
        # Una columna se va por su rótulo, pero un `{{ ... end_date }}` suelto en
        # una plantilla tampoco imprime nada y sólo delata el resto: se verifica
        # que el HTML del listado no mencione el atributo.
        self.client.force_login(self.admin)
        html = self.client.get(reverse('commercial:suscripcion_list')).content.decode()
        self.assertNotIn('end_date', html)

    def test_home_no_muestra_vencimiento_de_sus_servicios(self):
        self.client.force_login(self.customer.user)
        html = self.client.get(reverse('home:services_commercial')).content.decode()
        self.assertIn(self.service.title, html)
        self.assertNotIn('Vencimiento', html)
        self.assertNotIn('Expiraci', html)


class CertificatePDFOwnerAccessTests(TestCase):
    """PDFs de certificado visibles por el cliente titular (home/dashboard).

    Antes ``CertificatePDFView`` exigía ``commercial.view_certificate`` (solo
    staff), así que los botones "Ver PDF"/"Descargar" de "Mis Servicios" y de
    "Mis Suscripciones" devolvían 403 para el cliente titular.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = _make_user('staffcertpdf', is_staff=True)
        cls.owner = _make_user('ownercert')
        cls.other = _make_user('othercert')
        cls.customer = natural_customer(
            cls.owner,
            account='1234567890123456',
            agency_bank='BANDEC',
            address='Addr',
            phone='12345678',
        )
        cls.other_customer = natural_customer(
            cls.other,
            account='6543210987654321',
            agency_bank='BANDEC',
            address='Addr',
            phone='87654321',
        )
        service = Service.objects.create(
            user=cls.staff,
            title='Svc Cert',
            summary='s',
            service_type='commercial',
            code='CERT001',
            price=Decimal('10.00'),
        )
        sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=service,
            start_date=timezone.now() - timedelta(days=1),
            payment_status='paid',
        )
        cls.cert = Certificate.objects.create(
            subscription=sub,
            pdf=SimpleUploadedFile('cert.pdf', b'%PDF-1.4 test', 'application/pdf'),
        )
        cls.url = reverse('commercial:certificado_pdf', args=[cls.cert.uuid])

    def test_owner_client_can_view_inline_pdf(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline', response['Content-Disposition'])
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 test')

    def test_owner_client_can_download_pdf(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])

    def test_other_client_gets_403(self):
        self.client.force_login(self.other)
        response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 403)

    def test_staff_without_permission_gets_403(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 403)

    def test_staff_with_permission_can_view(self):
        ct = ContentType.objects.get_for_model(Certificate)
        perm = ct.permission_set.get(codename='view_certificate')
        self.staff.user_permissions.add(perm)
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 200)


class InvoicePDFOwnerAccessTests(TestCase):
    """PDFs de factura visibles por el cliente titular (dashboard).

    ``InvoicePDFDownloadView`` exigía ``commercial.view_invoice``; un cliente
    pendiente ve el botón "Ver PDF" de su factura y recibía 403.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = _make_user('staffinvpdf', is_staff=True)
        cls.owner = _make_user('ownerinv')
        cls.other = _make_user('otherinv')
        cls.customer = natural_customer(
            cls.owner,
            account='1234567890123456',
            agency_bank='BANDEC',
            address='Addr',
            phone='12345678',
        )
        cls.other_customer = natural_customer(
            cls.other,
            account='6543210987654321',
            agency_bank='BANDEC',
            address='Addr',
            phone='87654321',
        )
        cls.invoice = Invoice.objects.create(
            customer=cls.customer,
            number='INV-OWNER-2026',
            amount=Decimal('100.00'),
            pdf=SimpleUploadedFile('factura.pdf', b'%PDF-1.4 test', 'application/pdf'),
        )
        cls.url = reverse('commercial:factura_download', args=[cls.invoice.uuid])

    def test_owner_client_can_view_inline_pdf(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline', response['Content-Disposition'])
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_owner_client_can_download_pdf(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])

    def test_other_client_gets_403(self):
        self.client.force_login(self.other)
        response = self.client.get(self.url, {'inline': '1'})
        self.assertEqual(response.status_code, 403)


class ServiceReRequestTests(TestCase):
    """reesolicitar-servicio-activo — ServiceDetailView re-request semantics."""

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.customer = _make_customer('reerq')
        cls.service = Service.objects.create(
            user=_make_user('reerqprov'),
            title='Re-request Svc',
            summary='Sum',
            service_type='commercial',
            service_category='pronostico',
            code='REQ1',
            price=Decimal('20.00'),
        )
        cls.url = reverse('home:services_commercial_detail', args=[cls.service.uuid])

    def _post_request(self):
        return self.client.post(
            self.url,
            {
                'payment_method': 'transfer',
                'start_date': (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
                'quantity': '1',
            },
        )

    def test_form_valid_with_active_paid_creates_requested_row(self):
        self.client.force_login(self.customer.user)
        paid_start = timezone.now() - timedelta(days=30)
        paid = ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=paid_start,
            payment_status='paid',
            payment_method='transfer',
            quantity=1,
        )
        initial_count = ServiceSubscription.objects.filter(
            customer=self.customer, service=self.service
        ).count()
        self._post_request()
        rows = ServiceSubscription.objects.filter(customer=self.customer, service=self.service)
        self.assertEqual(rows.count(), initial_count + 1)
        requested = rows.get(payment_status='requested')
        self.assertIsNotNone(requested)
        # Pedir el servicio otra vez crea una fila nueva y no toca la que ya
        # estaba pagada: ni su estado ni la fecha que eligió el cliente.
        paid.refresh_from_db()
        self.assertEqual(paid.payment_status, 'paid')
        self.assertEqual(paid.start_date, paid_start)
        self.assertEqual(paid.payment_method, 'transfer')

    def test_form_valid_with_requested_sub_still_creates_second_row(self):
        """B1: una solicitud en vuelo no bloquea pedir el servicio otra vez."""
        self.client.force_login(self.customer.user)
        ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now() - timedelta(days=1),
            payment_status='requested',
        )
        count_before = ServiceSubscription.objects.filter(
            customer=self.customer, service=self.service
        ).count()
        response = self._post_request()
        self.assertEqual(
            ServiceSubscription.objects.filter(
                customer=self.customer, service=self.service
            ).count(),
            count_before + 1,
        )
        self.assertEqual(response.status_code, 302)

    def test_form_valid_with_pending_sub_still_creates_second_row(self):
        """B1: tampoco una factura pendiente de pago impide una nueva solicitud."""
        self.client.force_login(self.customer.user)
        ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now() - timedelta(days=1),
            payment_status='pending',
        )
        count_before = ServiceSubscription.objects.filter(
            customer=self.customer, service=self.service
        ).count()
        response = self._post_request()
        self.assertEqual(
            ServiceSubscription.objects.filter(
                customer=self.customer, service=self.service
            ).count(),
            count_before + 1,
        )
        self.assertEqual(response.status_code, 302)


class ResendCertificateOwnerAccessTests(TestCase):
    """Reenvío del certificado por correo accesible al cliente titular.

    Antes ``ResendCertificateEmailView`` exigía ``commercial.change_subscription``
    (solo staff), así que el botón "Reenviar certificado por correo" de "Mis
    Suscripciones" devolvía 403 para el cliente titular.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = _make_user('staffresend', is_staff=True)
        cls.owner = _make_user('ownerresend')
        cls.other = _make_user('otherresend')
        cls.customer = natural_customer(
            cls.owner,
            account='1234567890123456',
            agency_bank='BANDEC',
            address='Addr',
            phone='12345678',
        )
        cls.other_customer = natural_customer(
            cls.other,
            account='6543210987654321',
            agency_bank='BANDEC',
            address='Addr',
            phone='87654321',
        )
        service = Service.objects.create(
            user=cls.staff,
            title='Svc Resend',
            summary='s',
            service_type='commercial',
            code='RESEND01',
            price=Decimal('10.00'),
        )
        cls.sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=service,
            start_date=timezone.now() - timedelta(days=1),
            payment_status='paid',
        )
        cls.cert = Certificate.objects.create(
            subscription=cls.sub,
            pdf=SimpleUploadedFile('cert.pdf', b'%PDF-1.4 test', 'application/pdf'),
        )
        cls.url = reverse('commercial:certificado_resend', args=[cls.sub.uuid])
        cls.list_url = reverse('commercial:suscripcion_list')
        # El grupo Clientes otorga view_subscription y habilita la rama cliente
        # del SubscriptionListView; se añade para el follow del redirect.
        from django.contrib.auth.models import Group

        clientes, _ = Group.objects.get_or_create(name='Clientes')
        cls.clientes = clientes

    def test_owner_client_can_resend(self):
        self.client.force_login(self.owner)
        with patch('apps.commercial.views.subscriptions.EmailMessage') as mock_email_cls:
            mock_email = mock_email_cls.return_value
            mock_email.send.return_value = 1
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.list_url)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any('reenviado correctamente' in m for m in msgs),
            f'expected success message, got {msgs}',
        )

    def test_other_client_gets_403(self):
        self.client.force_login(self.other)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_staff_without_permission_gets_403(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_staff_with_change_subscription_can_resend(self):
        ct = ContentType.objects.get_for_model(ServiceSubscription)
        perm = ct.permission_set.get(codename='change_subscription')
        self.staff.user_permissions.add(perm)
        self.client.force_login(self.staff)
        with patch('apps.commercial.views.subscriptions.EmailMessage') as mock_email_cls:
            mock_email = mock_email_cls.return_value
            mock_email.send.return_value = 1
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.list_url)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any('reenviado correctamente' in m for m in msgs),
            f'expected success message, got {msgs}',
        )


class InvoiceSubscriptionLinkTests(TestCase):
    """D1: el vínculo factura-suscripción vive en la línea, no en la factura.

    La facturación por lote agrupa suscripciones y la manual de varios servicios
    sólo colgaba la primera, así que `Invoice.subscription` quedaba NULL.
    Quien leía `subscription.invoices` no encontraba nada y el cliente veía la
    suscripción pendiente sin botones de factura.
    """

    def _sub(self, customer, provider, code, price=Decimal('50.00')):
        service = Service.objects.create(
            user=provider,
            title=f'Servicio {code}',
            summary='S',
            service_type='commercial',
            code=code,
            price=price,
        )
        return ServiceSubscription.objects.create(
            customer=customer,
            service=service,
            start_date=timezone.now(),
            quantity=2,
            payment_status='pending',
        )

    def _batch(self, subs, seq=None):
        from apps.commercial.views.invoices import InvoiceCreateView

        customer = subs[0].customer
        view = InvoiceCreateView()
        view.request = _build_request(
            _make_superuser(f'batchadmin-link-{seq if seq is not None else id(subs)}')
        )
        view.process_batch_invoice(customer, 'REG-LINK', subs)
        return InvoiceItem.objects.filter(subscription__in=subs).first().invoice

    def test_for_subscription_finds_invoice_linked_only_by_item(self):
        customer = _make_customer('linkcust')
        provider = _make_user('linkprov')
        subs = [
            self._sub(customer, provider, 'L001'),
            self._sub(customer, provider, 'L001b'),
        ]
        invoice = self._batch(subs)
        self.assertIsNone(invoice.subscription_id, 'precondición: la factura nace huérfana')
        for sub in subs:
            self.assertIn(invoice, Invoice.objects.for_subscription(sub))

    def test_batch_with_one_subscription_anchors_the_invoice(self):
        customer = _make_customer('linkcust1')
        provider = _make_user('linkprov1')
        sub = self._sub(customer, provider, 'L002')
        invoice = self._batch([sub])
        self.assertEqual(invoice.subscription_id, sub.pk)

    def test_batch_with_many_subscriptions_leaves_it_unanchored(self):
        customer = _make_customer('linkcust2')
        provider = _make_user('linkprov2')
        subs = [
            self._sub(customer, provider, 'L003'),
            self._sub(customer, provider, 'L004'),
        ]
        invoice = self._batch(subs)
        self.assertIsNone(invoice.subscription_id, 'no hay una suscripción única que colgarse')
        for sub in subs:
            self.assertIn(invoice, Invoice.objects.for_subscription(sub))

    def test_each_subscription_sees_only_its_own_invoice(self):
        customer = _make_customer('linkcust3')
        provider = _make_user('linkprov3')
        subs = [
            self._sub(customer, provider, 'L005'),
            self._sub(customer, provider, 'L006'),
        ]
        invoice_a = self._batch([subs[0]], seq='a')
        invoice_b = self._batch([subs[1]], seq='b')
        self.assertNotEqual(invoice_a.pk, invoice_b.pk)
        self.assertIn(invoice_a, Invoice.objects.for_subscription(subs[0]))
        self.assertNotIn(invoice_b, Invoice.objects.for_subscription(subs[0]))

    def test_cancelled_invoice_is_excluded_from_the_lookup(self):
        customer = _make_customer('linkcust4')
        provider = _make_user('linkprov4')
        subs = [
            self._sub(customer, provider, 'L007'),
            self._sub(customer, provider, 'L007b'),
        ]
        invoice = self._batch(subs)
        invoice.is_cancelled = True
        invoice.save(update_fields=['is_cancelled'])
        self.assertNotIn(
            invoice,
            Invoice.objects.for_subscription(subs[0]).filter(is_cancelled=False),
        )


class ClientPendingInvoiceButtonsTests(TestCase):
    """D1: en "Mis Suscripciones" el cliente pendiente ve sus botones de factura.

    La rama cliente leía `subscription.invoices`, que queda vacío cuando la
    factura cuelga sólo de su línea, así que la suscripción pendiente
    aparecía sin ningún botón: sin descarga y sin ver PDF.
    """

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        from django.contrib.auth.models import Group

        cls.customer = _make_customer('clicust')
        cls.user = cls.customer.user
        provider = _make_user('cliprov')
        service = Service.objects.create(
            user=provider,
            title='Servicio pending',
            summary='S',
            service_type='commercial',
            code='CLI001',
            price=Decimal('40.00'),
        )
        cls.sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=service,
            quantity=2,
            payment_status='pending',
            payment_method='transfer',
        )
        # Factura por lote de dos suscripciones: queda sin ancla a propósito,
        # porque con varias no hay una suscripción única que colgarse.
        other_service = Service.objects.create(
            user=provider,
            title='Otro servicio',
            summary='S',
            service_type='commercial',
            code='CLI002',
            price=Decimal('10.00'),
        )
        other_sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=other_service,
            quantity=1,
            payment_status='pending',
        )
        invoice = Invoice.objects.create(
            customer=cls.customer,
            subscription=None,
            number='2026-9001',
            amount=90,
            # El botón de descarga sólo existe si el PDF está listo; este test
            # verifica que la vista encuentre la factura por su línea, así que
            # la factura necesita un PDF real para que ese botón sea visible.
            pdf='factura/invoice/2026-9001.pdf',
            pdf_status=Invoice.PdfStatus.READY,
        )
        for sub, cantidad in ((cls.sub, 2), (other_sub, 1)):
            InvoiceItem.objects.create(
                invoice=invoice,
                subscription=sub,
                codigo=sub.service.code,
                descripcion=sub.service.title,
                cantidad=cantidad,
                unidad_medida='U',
                precio=sub.service.price,
                importe=sub.service.price * cantidad,
            )
        cls.invoice = invoice
        # Suscripción pendiente sin ninguna factura asociada: no debe mostrar
        # ningún botón, aunque su cliente tenga otras facturas.
        orphan_service = Service.objects.create(
            user=provider,
            title='Servicio sin factura',
            summary='S',
            service_type='commercial',
            code='CLI003',
            price=Decimal('15.00'),
        )
        cls.orphan_sub = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=orphan_service,
            quantity=1,
            payment_status='pending',
        )
        group, _ = Group.objects.get_or_create(name='Clientes')
        perm = ContentType.objects.get_for_model(ServiceSubscription).permission_set.get(
            codename='view_subscription'
        )
        group.permissions.add(perm)
        cls.user.groups.add(group)

    def setUp(self):
        self.url = reverse('commercial:suscripcion_list')

    def test_pending_client_sees_download_and_view_pdf_buttons(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        download_url = reverse('commercial:factura_download', args=[self.invoice.uuid])
        self.assertIn('Descargar Factura', html)
        self.assertIn(download_url, html)
        self.assertIn(f'data-pdf-title="Factura {self.invoice.number}"', html)

    def test_list_view_annotates_the_invoice_reachable_only_by_item(self):
        from apps.commercial.views.subscriptions import SubscriptionListView

        view = SubscriptionListView()
        view.request = _build_request(self.user)
        resolved = {s.pk: s.latest_invoice_uuid for s in view.get_queryset()}
        self.assertEqual(resolved[self.sub.pk], self.invoice.uuid)

    def test_subscription_without_invoice_gets_no_buttons(self):
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode()
        # Las dos suscripciones del lote comparten factura -> 2 botones.
        # La que no tiene factura no agrega un tercero.
        self.assertEqual(html.count('Descargar Factura'), 2)

    def test_orphan_subscription_annotation_is_empty(self):
        from apps.commercial.views.subscriptions import SubscriptionListView

        view = SubscriptionListView()
        view.request = _build_request(self.user)
        resolved = {s.pk: s.latest_invoice_uuid for s in view.get_queryset()}
        self.assertIsNone(resolved[self.orphan_sub.pk])


class SubscriptionPeriodFormViewTests(TestCase):
    """Alta y edición de suscripciones: sólo se pide lo que el operador elige.

    La unidad (días o meses) la decide la categoría del servicio y la suscripción
    no vence, así que ninguno de los dos formularios expone un `period` ni un
    `end_date`, escribibles ni derivados. El inicio puede estar en el pasado, la
    cantidad es la que el operador fija, y el estado de pago no se elige al
    capturar.
    """

    INICIO = '15/01/2026'

    @classmethod
    def setUpTestData(cls):
        disable_maintenance_mode()
        cls.staff = _make_superuser('subperiod')
        perms = ContentType.objects.get_for_model(ServiceSubscription).permission_set.filter(
            codename__in=['add_subscription', 'change_subscription']
        )
        cls.staff.user_permissions.add(*perms)
        cls.owner = _make_user('subperiod.owner')
        cls.customer = natural_customer(cls.owner)
        cls.pronostico = Service.objects.create(
            user=cls.owner,
            title='Pronóstico diario',
            summary='Commercial',
            service_type='commercial',
            code='SP001',
            price=Decimal('60.00'),
        )
        cls.agrometeo = Service.objects.create(
            user=cls.owner,
            title='Boletín agrometeorológico',
            summary='Commercial',
            service_type='commercial',
            code='SP002',
            price=Decimal('300.00'),
            service_category='agrometeo',
        )
        # La suscripción preexistente usa otro cliente: las pruebas de alta
        # cuentan o consultan suscripciones de `cls.customer` y no deben
        # confundirse con una ya existente.
        cls.edit_owner = _make_user('subperiod.edit')
        cls.edit_customer = natural_customer(cls.edit_owner)
        cls.sub = ServiceSubscription.objects.create(
            customer=cls.edit_customer,
            service=cls.agrometeo,
            start_date=timezone.now() - timedelta(days=10),
            quantity=1,
            payment_status='requested',
        )
        cls.create_url = reverse('commercial:suscripcion_create')
        cls.update_url = reverse('commercial:suscripcion_update', args=[cls.sub.uuid])
        cls.list_url = reverse('commercial:suscripcion_list')

    def setUp(self):
        self.client.force_login(self.staff)

    def _data(self, **overrides):
        data = {
            'customer': self.customer.pk,
            'service': self.agrometeo.pk,
            'start_date': self.INICIO,
            'quantity': '3',
            'payment_method': 'qr',
        }
        data.update(overrides)
        return data

    def test_crear_guarda_el_inicio_y_la_cantidad_elegidos(self):
        response = self.client.post(self.create_url, self._data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.list_url)
        sub = ServiceSubscription.objects.get(customer=self.customer)
        self.assertEqual(sub.start_date.strftime('%d/%m/%Y'), '15/01/2026')
        self.assertEqual(sub.quantity, 3)
        self.assertEqual(sub.service, self.agrometeo)

    def test_crear_no_deja_ningun_vencimiento_en_la_suscripcion(self):
        # La suscripción no vence: no hay fecha de expiración que calcular ni que
        # guardar. Lo que se capturó es sólo el acuerdo con el cliente.
        self.client.post(self.create_url, self._data())
        sub = ServiceSubscription.objects.get(customer=self.customer)
        self.assertFalse(hasattr(sub, 'end_date'))

    def test_crear_acepta_una_fecha_de_inicio_pasada(self):
        response = self.client.post(self.create_url, self._data(start_date='01/09/2025'))
        self.assertEqual(response.status_code, 302)
        sub = ServiceSubscription.objects.get(customer=self.customer)
        self.assertEqual(sub.start_date.strftime('%d/%m/%Y'), '01/09/2025')

    def test_crear_ignora_el_end_date_enviado(self):
        # Un POST a mano con `end_date` no crea ninguna fecha: el campo no existe
        # ni en el formulario ni en el modelo, así que el dato se descarta.
        self.client.post(self.create_url, self._data(end_date='01/01/2030'))
        sub = ServiceSubscription.objects.get(customer=self.customer)
        self.assertFalse(hasattr(sub, 'end_date'))

    def test_crear_sin_cantidad_no_crea_la_suscripcion(self):
        response = self.client.post(self.create_url, self._data(quantity=''))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ServiceSubscription.objects.filter(customer=self.customer).exists())
        self.assertIn('quantity', response.context['form'].errors)

    def test_editar_guarda_la_cantidad_nueva_sin_tocar_el_inicio(self):
        # Editar la solicitud cambia lo que el operador eligió, y nada más: el
        # inicio sigue siendo el que se capturó en el alta.
        response = self.client.post(self.update_url, self._data(quantity='6'))
        self.assertEqual(response.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.quantity, 6)
        self.assertFalse(hasattr(self.sub, 'end_date'))

    def test_editar_ignora_el_end_date_enviado(self):
        self.client.post(self.update_url, self._data(quantity='1', end_date='01/01/2030'))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.quantity, 1)
        self.assertFalse(hasattr(self.sub, 'end_date'))

    def test_el_formulario_de_crear_pide_cantidad_y_no_expone_periodo_fin_nivel_calculado(self):
        html = self.client.get(self.create_url).content.decode()
        self.assertIn('id="id_quantity"', html)
        self.assertNotIn('id="id_period"', html)
        self.assertNotIn('id="id_end_date"', html)
        self.assertNotIn('end-date-preview', html)
        self.assertNotIn('Vencimiento calculado', html)

    def test_el_formulario_de_editar_no_muestra_el_vencimiento(self):
        # El vencimiento se calcula y guarda en el servidor, pero no se presenta
        # mientras la suscripción está `requested`.
        html = self.client.get(self.update_url).content.decode()
        self.assertNotIn('end-date-preview', html)
        self.assertNotIn('Vencimiento calculado', html)
        self.assertNotIn('id="id_end_date"', html)

    def test_la_cantidad_se_rotula_con_la_unidad_del_servicio(self):
        # El rótulo lo imprime el servidor, así que se ve sin JavaScript; el
        # navegador sólo lo mantiene al día al cambiar de servicio.
        html = self.client.get(self.update_url).content.decode()
        self.assertIn('Cantidad de meses', html)

    def test_el_script_del_rotulo_se_incluye_de_verdad(self):
        """El asset puede existir y aun así no cargarse nunca."""
        for url in (self.create_url, self.update_url):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('subscription-quantity-label.js', html)

    def test_el_inicio_muestra_su_ayuda(self):
        """El include de la fecha tenía que imprimir el `help_text` del campo."""
        for url in (self.create_url, self.update_url):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('Fecha desde la cual necesita el servicio.', html)

    def test_los_formularios_no_piden_scripts_inexistentes(self):
        for url in (self.create_url, self.update_url):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                rutas = re.findall(r'<script src="([^"]+)"', html)
                self.assertTrue(rutas, 'la página no cargó ningún script')
                for ruta in rutas:
                    if not ruta.startswith(settings.STATIC_URL):
                        continue
                    relativa = ruta[len(settings.STATIC_URL) :].split('?')[0]
                    self.assertIsNotNone(
                        finders.find(relativa), f'asset inexistente pedido por el form: {relativa}'
                    )

    def test_el_inicio_se_pide_como_picker_de_fecha_y_no_de_fecha_hora(self):
        """El picker y el campo tienen que hablar el mismo idioma."""
        for url in (self.create_url, self.update_url):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('data-tempus="date"', html)
                self.assertNotIn('data-tempus="datetime"', html)
                self.assertNotIn('dd/mm/aaaa hh:mm', html)

    def test_el_estado_de_pago_no_se_elige_al_crear(self):
        html = self.client.get(self.create_url).content.decode()
        self.assertNotIn('name="payment_status"', html)

    def test_crear_ofrece_los_metodos_de_pago(self):
        html = self.client.get(self.create_url).content.decode()
        self.assertIn('name="payment_method"', html)
        for valor in ('qr', 'transfer', 'presencial'):
            self.assertIn(f'value="{valor}"', html)

    def test_las_tres_descripciones_se_renderizan_siempre(self):
        """Las tres, aunque en el alta no haya método vinculado todavía.

        El script cambia cuál queda visible al marcar otro radio, así que
        necesita que existan las tres. Si el servidor imprimiera sólo la del
        método ya elegido, en el formulario en blanco no habría ninguna y la
        descripción no podría aparecer nunca.
        """
        html = self.client.get(self.create_url).content.decode()
        for valor in ('qr', 'transfer', 'presencial'):
            self.assertIn(f'data-payment-method-info="{valor}"', html)

    def test_la_descripcion_visible_es_la_del_metodo_elegido(self):
        """Sin JavaScript, la del método vinculado es la única sin `d-none`.

        `d-none` y no `hidden`: `.alert` es `display:flex` sin `!important` y
        los estilos de autor ganan a la hoja del navegador, así que dentro de
        una alerta el atributo `hidden` no ocultaría nada.
        """
        self.sub.payment_method = 'transfer'
        self.sub.save(update_fields=['payment_method'])
        html = self.client.get(self.update_url).content.decode()

        def bloque(valor):
            return re.search(
                rf'<div[^>]*data-payment-method-info="{valor}".*?</div>', html, re.S
            ).group(0)

        self.assertNotIn('d-none', bloque('transfer'))
        for valor in ('qr', 'presencial'):
            with self.subTest(valor=valor):
                self.assertIn('d-none', bloque(valor))

    def test_las_ayudas_compartidas_dicen_lo_mismo_que_en_casa(self):
        """Dashboard y Home comparten los tres textos de ayuda al pie."""
        esperado = (
            'Fecha desde la cual necesita el servicio.',
            'El importe total se calcula multiplicando el precio por la cantidad seleccionada.',
            'Elige cómo deseas realizar el pago.',
        )
        html = self.client.get(self.create_url).content.decode()
        for texto in esperado:
            with self.subTest(texto=texto):
                self.assertEqual(html.count(texto), 1)

    def test_las_ayudas_al_pie_llevan_el_margen_de_las_ayudas_de_campo(self):
        """`.form-control + .form-hint` da 0.5rem, pero el help de pago no va
        tras un control: sin `mt-2` quedaba pegado al grupo de radios."""
        html = self.client.get(self.create_url).content.decode()
        self.assertIn('<small class="form-hint mt-2">', html)

    def test_los_botones_de_pago_reparten_el_ancho_disponible(self):
        for url in (self.create_url, self.update_url):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertEqual(html.count('form-selectgroup-item flex-md-fill'), 3)

    def test_crear_sin_metodo_de_pago_no_crea_la_suscripcion(self):
        response = self.client.post(self.create_url, self._data(payment_method=''))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ServiceSubscription.objects.filter(customer=self.customer).exists())
        self.assertIn('payment_method', response.context['form'].errors)

    def test_crear_deja_la_suscripcion_en_solicitado_aunque_el_post_mande_otro_estado(self):
        # El estado no es un campo del formulario, así que un POST a mano no puede
        # crear una suscripción ya pagada.
        response = self.client.post(self.create_url, self._data(payment_status='paid'))
        self.assertEqual(response.status_code, 302)
        sub = ServiceSubscription.objects.get(customer=self.customer)
        self.assertEqual(sub.payment_status, 'requested')

    def test_editar_no_cambia_el_estado_de_pago(self):
        # Editar es editar la solicitud (cliente, servicio, inicio, cantidad y
        # método de pago); el estado lo mueven las acciones, no este formulario.
        self.sub.payment_status = 'paid'
        self.sub.save(update_fields=['payment_status'])
        response = self.client.post(self.update_url, self._data(payment_status='requested'))
        self.assertEqual(response.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.payment_status, 'paid')

    def test_editar_guarda_el_metodo_de_pago_elegido(self):
        response = self.client.post(self.update_url, self._data(payment_method='presencial'))
        self.assertEqual(response.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.payment_method, 'presencial')

    def test_el_alta_y_la_edicion_agrupan_los_campos_en_dos_cards(self):
        # Cinco campos en tres cards partía el formulario en trozos sin sentido:
        # los datos de la solicitud van juntos y el pago aparte.
        for url in (self.create_url, self.update_url):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('Datos de la Solicitud', html)
                self.assertNotIn('Período de Vigencia', html)
                self.assertEqual(html.count('subheader text-muted fw-medium'), 2)
