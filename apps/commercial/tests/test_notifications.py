"""Notificaciones comerciales (cambio 026: huecos de solicitudes y cobros)."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.commercial.models import (
    Customer,
    Invoice,
    InvoiceItem,
    Service,
    ServiceSubscription,
)
from apps.commercial.notifications import notificar_solicitud_suscripcion
from apps.commercial.tasks import facturas_pendientes
from apps.commercial.tests.factories import natural_customer
from apps.core.models import Notification, SiteConfiguration


def _disable_maintenance_mode():
    SiteConfiguration.objects.update_or_create(defaults={'maintenance_mode': False})


def _superuser(username='admin-notif'):
    return User.objects.create_superuser(
        username,
        f'{username}@example.com',
        'secreto',
        first_name='Ada',
        last_name='Min',
    )


class SolicitudDeServicioNotificaTests(TestCase):
    """El cliente pide un servicio desde el portal y el panel se entera."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.admin = _superuser()
        cls.other_admin = _superuser('admin-notif-2')
        cls.provider = User.objects.create_user(
            'proveedor',
            'proveedor@example.com',
            'pass',
            first_name='P',
            last_name='R',
        )
        cls.client_user = User.objects.create_user(
            'solicitante',
            'solicitante@example.com',
            'pass',
            first_name='C',
            last_name='L',
        )
        cls.customer = natural_customer(cls.client_user)
        cls.service = Service.objects.create(
            user=cls.provider,
            title='Pronóstico extendido',
            summary='Resumen',
            service_type=Service.COMMERCIAL,
            service_category='pronostico',
            price=Decimal('20.00'),
            code='NOTIF-001',
        )

    def _post_solicitud(self, quantity=2):
        self.client.force_login(self.client_user)
        return self.client.post(
            reverse('home:services_commercial_detail', args=[self.service.uuid]),
            {
                'payment_method': 'transfer',
                'start_date': timezone.localdate().strftime('%Y-%m-%d'),
                'quantity': quantity,
            },
        )

    def test_la_solicitud_del_cliente_avisa_a_todos_los_admins(self):
        response = self._post_solicitud()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.filter(source='subscription').count(), 2)
        self.assertEqual(
            set(
                Notification.objects.filter(source='subscription').values_list(
                    'recipient', flat=True
                )
            ),
            {self.admin.pk, self.other_admin.pk},
        )

    def test_el_aviso_nombra_al_cliente_y_al_servicio(self):
        self._post_solicitud()

        notification = Notification.objects.get(source='subscription', recipient=self.admin)

        self.assertIn(self.customer.display_name, notification.body)
        self.assertIn(self.service.title, notification.body)
        self.assertEqual(notification.url, '/commercial/suscripciones/')

    def test_notificar_dos_veces_la_misma_suscripcion_no_duplica(self):
        subscription = ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now(),
            payment_status='requested',
        )

        notificar_solicitud_suscripcion(subscription)
        notificar_solicitud_suscripcion(subscription)

        self.assertEqual(Notification.objects.filter(source='subscription').count(), 2)

    def test_un_admin_inactivo_no_recibe_el_aviso(self):
        self.other_admin.is_active = False
        self.other_admin.save(update_fields=['is_active'])

        self._post_solicitud()

        self.assertEqual(
            list(Notification.objects.values_list('recipient', flat=True)), [self.admin.pk]
        )

    @override_settings(NOTIFICATIONS_ENABLED=False)
    def test_con_el_sistema_apagado_no_se_crea_nada(self):
        self._post_solicitud()

        self.assertEqual(Notification.objects.count(), 0)


class FacturasPendientesTests(TestCase):
    """Recordatorio de facturas con pago pendiente hace demasiado tiempo."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = _superuser()
        cls.provider = User.objects.create_user(
            'proveedor-factura',
            'proveedor-factura@example.com',
            'pass',
            first_name='P',
            last_name='R',
        )
        cls.customer = natural_customer(username='cliente-factura')
        cls.service = Service.objects.create(
            user=cls.provider,
            title='Servicio facturable',
            summary='Resumen',
            service_type=Service.COMMERCIAL,
            service_category='pronostico',
            price=Decimal('100.00'),
            code='NOTIF-002',
        )

    def _factura(self, *, status='pending', days=10, cancelled=False, with_item=True):
        subscription = ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now() - timedelta(days=days),
            payment_status=status,
        )
        invoice = Invoice.objects.create(
            customer=self.customer,
            subscription=subscription,
            number=f'F-{Invoice.objects.count() + 1:04d}',
            amount=Decimal('100.00'),
            is_cancelled=cancelled,
        )
        if with_item:
            InvoiceItem.objects.create(
                invoice=invoice,
                subscription=subscription,
                descripcion='Servicio facturable',
                cantidad=1,
                precio=Decimal('100.00'),
                importe=Decimal('100.00'),
            )
        # `issue_date` es `auto_now_add`: se corrige después de crear la fila.
        Invoice.objects.filter(pk=invoice.pk).update(
            issue_date=timezone.now() - timedelta(days=days)
        )
        invoice.refresh_from_db()
        return invoice

    def test_una_factura_vencida_de_plazo_genera_el_recordatorio(self):
        invoice = self._factura(days=10)

        created = facturas_pendientes()

        self.assertEqual(len(created), 1)
        notification = created[0]
        self.assertEqual(notification.source, 'invoice_pending')
        self.assertIn(invoice.number, notification.body)
        self.assertIn('más de 7 días', notification.body)

    def test_una_factura_recien_emitida_no_genera_nada(self):
        self._factura(days=2)

        self.assertEqual(facturas_pendientes(), [])

    def test_una_factura_pagada_no_se_recordatorio(self):
        self._factura(status='paid', days=30)

        self.assertEqual(facturas_pendientes(), [])

    def test_una_factura_anulada_no_se_recordatorio(self):
        self._factura(cancelled=True, days=30)

        self.assertEqual(facturas_pendientes(), [])

    def test_una_factura_sin_lineas_no_se_recordatorio(self):
        self._factura(days=30, with_item=False)

        self.assertEqual(facturas_pendientes(), [])

    def test_el_recordatorio_no_repite_mientras_el_aviso_es_reciente(self):
        self._factura(days=10)
        facturas_pendientes()
        # El admin lo leyó: sin `repeat_after` la corrida siguiente lo volvería
        # a crear el mismo día.
        Notification.objects.update(read_at=timezone.now())

        self.assertEqual(facturas_pendientes(), [])

    def test_el_recordatorio_vuelve_pasado_el_intervalo(self):
        self._factura(days=10)
        facturas_pendientes()
        Notification.objects.update(
            read_at=timezone.now(),
            created_at=timezone.now() - timedelta(days=8),
        )

        created = facturas_pendientes()

        self.assertEqual(len(created), 1)

    def test_una_factura_con_varias_lineas_pendientes_avisa_una_sola_vez(self):
        subscription = ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now() - timedelta(days=10),
            payment_status='pending',
        )
        invoice = Invoice.objects.create(
            customer=self.customer,
            number='F-MULTI',
            amount=Decimal('200.00'),
        )
        for index, description in enumerate(('Línea A', 'Línea B')):
            InvoiceItem.objects.create(
                invoice=invoice,
                subscription=subscription,
                descripcion=f'{description} {index}',
                cantidad=1,
                precio=Decimal('100.00'),
                importe=Decimal('100.00'),
            )
        Invoice.objects.filter(pk=invoice.pk).update(issue_date=timezone.now() - timedelta(days=10))

        created = facturas_pendientes()

        self.assertEqual(len(created), 1)


class ClienteDesactivadoTests(TestCase):
    """Desactivar un cliente con servicios vivos deja de cobrar sin decirlo."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.admin = _superuser('admin-baja')
        cls.other_admin = _superuser('admin-baja-2')
        cls.staff = User.objects.create_user(
            'staff-baja',
            'staff-baja@example.com',
            'pass',
            is_staff=True,
            first_name='Es',
            last_name='Taff',
        )
        ct = ContentType.objects.get_for_model(Customer)
        # El operador real tiene los dos permisos: sin `view_customer` el
        # redirect posterior al listado devuelve 403 y el test mediría eso en
        # vez de la baja.
        cls.staff.user_permissions.add(
            ct.permission_set.get(codename='delete_customer'),
            ct.permission_set.get(codename='view_customer'),
        )
        cls.customer = natural_customer(username='cliente-baja')
        cls.service = Service.objects.create(
            user=cls.staff,
            title='Servicio vigente',
            summary='Resumen',
            service_type=Service.COMMERCIAL,
            service_category='pronostico',
            price=Decimal('50.00'),
            code='NOTIF-003',
        )

    def _dar_de_baja(self):
        self.client.force_login(self.staff)
        return self.client.post(
            reverse('commercial:cliente_delete', args=[self.customer.uuid]), follow=True
        )

    def _suscripcion(self, *, status='paid', record_active=True):
        subscription = ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now(),
            payment_status=status,
        )
        if not record_active:
            # `SoftDeleteModel.delete()` es la baja lógica que usa el panel.
            subscription.delete()
        return subscription

    def test_la_baja_con_servicio_vivo_avisa_a_los_admins(self):
        self._suscripcion()

        response = self._dar_de_baja()

        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.record_active)
        notification = Notification.objects.get(source='commercial', recipient=self.admin)
        self.assertEqual(notification.severity, Notification.Severity.WARNING)
        self.assertIn(self.customer.display_name, notification.body)
        self.assertIn('su suscripción activa sigue en pie', notification.body)
        self.assertEqual(notification.url, '/commercial/suscripciones/')

    def test_la_baja_sin_servicios_vivos_no_avisa_de_nada(self):
        self._dar_de_baja()

        self.assertEqual(Notification.objects.filter(source='commercial').count(), 0)

    def test_una_suscripcion_ya_anulada_no_cuenta_como_viva(self):
        self._suscripcion(record_active=False)

        self._dar_de_baja()

        self.assertEqual(Notification.objects.filter(source='commercial').count(), 0)

    def test_el_aviso_cuenta_todas_las_suscripciones_vivas(self):
        self._suscripcion()
        self._suscripcion(status='pending')

        self._dar_de_baja()

        notification = Notification.objects.get(source='commercial', recipient=self.admin)
        self.assertIn('sus 2 suscripciones activas siguen en pie', notification.body)

    def test_repetir_la_baja_no_duplica_el_aviso(self):
        self._suscripcion()
        self._dar_de_baja()

        self._dar_de_baja()

        self.assertEqual(
            Notification.objects.filter(source='commercial', recipient=self.admin).count(), 1
        )
