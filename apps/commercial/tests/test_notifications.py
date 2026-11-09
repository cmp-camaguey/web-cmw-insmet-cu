"""Notificaciones comerciales (cambio 026: huecos de solicitudes y cobros)."""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.commercial.models import (
    Contract,
    Customer,
    Invoice,
    InvoiceItem,
    Service,
    ServiceSubscription,
)
from apps.commercial.notifications import (
    notificar_estado_suscripcion,
    notificar_factura_emitida,
    notificar_solicitud_suscripcion,
)
from apps.commercial.tasks import contratos_por_vencer, facturas_pendientes
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


class ContratosPorVencerTests(TestCase):
    """Un contrato que vence sin aviso se descubre cuando ya venció."""

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.admin = _superuser('admin-contrato')
        cls.other_admin = _superuser('admin-contrato-2')
        cls.customer = natural_customer(username='cliente-contrato')

    def _contrato(self, *, fin_en_dias, numero='C-001', customer=None, vigencia=2):
        """Contrato cuyo `end_date` cae exactamente a `fin_en_dias` de hoy.

        `end_date` es derivada de `start_date` + `validity_years`, así que se
        construye al revés: la fecha de fin se elige y el inicio se calcula
        restándole la vigencia. El `assertEqual` de abajo es la garantía de que
        el cálculo no se desvió en un 29 de febrero.
        """
        fin = timezone.localdate() + timedelta(days=fin_en_dias)
        anio = fin.year - vigencia
        inicio = date(anio, fin.month, min(fin.day, calendar.monthrange(anio, fin.month)[1]))
        contrato = Contract.objects.create(
            customer=customer or self.customer,
            number=numero,
            start_date=inicio,
            validity_years=vigencia,
        )
        self.assertEqual(contrato.end_date, fin)
        return contrato

    def test_un_contrato_que_vence_pronto_avisa_a_los_admins(self):
        contrato = self._contrato(fin_en_dias=20, numero='C-20')

        created = contratos_por_vencer()

        self.assertEqual(len(created), 2)
        notification = Notification.objects.get(source='commercial', recipient=self.admin)
        self.assertEqual(notification.severity, Notification.Severity.WARNING)
        self.assertIn('C-20', notification.body)
        self.assertIn(self.customer.display_name, notification.body)
        self.assertIn(contrato.end_date.strftime('%d/%m/%Y'), notification.body)
        self.assertEqual(notification.url, '/commercial/contratos/')

    def test_un_contrato_lejano_no_avisa(self):
        self._contrato(fin_en_dias=90)

        self.assertEqual(contratos_por_vencer(), [])

    def test_un_contrato_ya_vencido_no_avisa(self):
        """Ya pasó: lo que hacía falta era el aviso previo, no el epitafio."""
        self._contrato(fin_en_dias=-1)

        self.assertEqual(contratos_por_vencer(), [])

    def test_un_contrato_renovado_no_avisa(self):
        viejo = self._contrato(fin_en_dias=20, numero='C-VIEJO')
        # La renovación empieza antes del fin del anterior y lo cubre: el
        # servicio no se interrumpe y no hay nada que renovar.
        Contract.objects.create(
            customer=self.customer,
            number='C-NUEVO',
            start_date=viejo.end_date - timedelta(days=10),
            validity_years=2,
        )

        self.assertEqual(contratos_por_vencer(), [])

    def test_una_renovacion_firmada_el_mismo_dia_no_avisa(self):
        """El inicio se escribe a mano: dos contratos pueden empezar igual.

        Una renovación firmada el mismo día que el contrato anterior, con más
        vigencia, cubre el fin de este de sobra. Comparar sólo por `start_date`
        estricto dejaba pasar el empate y el admin recibía un aviso de algo que
        ya estaba renovado.
        """
        viejo = self._contrato(fin_en_dias=20, numero='C-VIEJO', vigencia=2)
        Contract.objects.create(
            customer=self.customer,
            number='C-RENOVADO',
            start_date=viejo.start_date,
            validity_years=5,
        )

        self.assertEqual(contratos_por_vencer(), [])

    def test_dos_contratos_iguales_que_vencen_el_mismo_dia_avisan_los_dos(self):
        """Dos servicios contratados el mismo día no son renovación uno del otro.

        Cubren el mismo rango, así que ninguno se extiende más allá del fin del
        otro: suprimir uno con el otro escondería dos vencimientos reales.
        """
        self._contrato(fin_en_dias=20, numero='C-A')
        self._contrato(fin_en_dias=20, numero='C-B')

        created = contratos_por_vencer()

        self.assertEqual(len(created), 4)
        bodies = list(
            Notification.objects.filter(recipient=self.admin).values_list('body', flat=True)
        )
        self.assertEqual(len(bodies), 2)
        self.assertTrue(any('C-A' in body for body in bodies))
        self.assertTrue(any('C-B' in body for body in bodies))

    def test_un_contrato_desactivado_no_avisa(self):
        contrato = self._contrato(fin_en_dias=20)
        contrato.delete()

        self.assertEqual(contratos_por_vencer(), [])

    def test_el_aviso_no_se_duplica_mientras_sigue_sin_leer(self):
        self._contrato(fin_en_dias=20)
        contratos_por_vencer()

        self.assertEqual(contratos_por_vencer(), [])
        self.assertEqual(
            Notification.objects.filter(source='commercial', recipient=self.admin).count(), 1
        )

    def test_el_aviso_vuelve_pasada_una_semana(self):
        """El contrato sigue por vencer: leerlo no puede silenciarlo para siempre."""
        self._contrato(fin_en_dias=20)
        contratos_por_vencer()
        Notification.objects.update(
            read_at=timezone.now(), created_at=timezone.now() - timedelta(days=8)
        )

        self.assertEqual(len(contratos_por_vencer()), 2)


class AvisosDeClienteTests(TestCase):
    """T11: el cliente ve su aviso y el admin sigue viendo el suyo.

    Las dos fuentes de cliente (`invoice` y `subscription`) alcanzan al titular
    del cliente sin dejar de alcanzar a los superusuarios. Lo que se prueba no
    es que la función escriba filas, sino a quién deja entrar cada hecho.
    """

    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.admin = _superuser('admin-avisos-cliente')
        cls.client_user = User.objects.create_user(
            'titular-avisos',
            'titular-avisos@example.com',
            'pass',
            first_name='Ti',
            last_name='Tular',
        )
        cls.customer = natural_customer(cls.client_user)
        cls.service = Service.objects.create(
            user=cls.admin,
            title='Pronóstico trimestral',
            summary='Resumen',
            service_type=Service.COMMERCIAL,
            service_category='pronostico',
            price=Decimal('80.00'),
            code='NOTIF-004',
        )

    def _suscripcion(self, status='requested'):
        return ServiceSubscription.objects.create(
            customer=self.customer,
            service=self.service,
            start_date=timezone.now(),
            payment_status=status,
        )

    def _factura(self, number='F-AVISOS-001'):
        return Invoice.objects.create(
            customer=self.customer,
            number=number,
            amount=Decimal('80.00'),
        )

    def _destinatarios(self, source=None):
        qs = Notification.objects.all()
        if source:
            qs = qs.filter(source=source)
        return set(qs.values_list('recipient__username', flat=True))

    def test_la_factura_emitida_avisa_al_cliente_y_al_admin(self):
        factura = self._factura()

        creadas = notificar_factura_emitida(factura)

        self.assertEqual(len(creadas), 2)
        self.assertEqual(
            self._destinatarios('invoice'),
            {self.admin.username, self.client_user.username},
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.client_user, title=f'Factura {factura.number} emitida'
            ).exists()
        )

    def test_reemitir_la_misma_factura_no_duplica_el_aviso(self):
        factura = self._factura()

        notificar_factura_emitida(factura)
        notificar_factura_emitida(factura)

        self.assertEqual(
            Notification.objects.filter(source='invoice', recipient=self.client_user).count(), 1
        )

    def test_el_pago_aprobado_avisa_al_cliente(self):
        sub = self._suscripcion('pending')

        notificar_estado_suscripcion(sub, 'paid')

        self.assertEqual(
            self._destinatarios('subscription'),
            {self.admin.username, self.client_user.username},
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.client_user, title__startswith='Pago aprobado'
            ).exists()
        )

    def test_la_anulacion_avisa_al_cliente(self):
        sub = self._suscripcion('requested')
        sub.delete()

        notificar_estado_suscripcion(sub, 'cancelled')

        self.assertTrue(
            Notification.objects.filter(
                recipient=self.client_user, title__startswith='Suscripción anulada'
            ).exists()
        )

    def test_anular_la_suscripcion_desde_el_panel_deja_el_aviso_al_cliente(self):
        """El aviso nace del hecho real, no solo de llamar a la función."""
        from config.huey import huey

        sub = self._suscripcion('requested')
        self.client.force_login(self.admin)
        # La anulación encola el correo al cliente: con la cola real la tarea
        # queda pendiente y ensucia a los tests que después cuentan la cola.
        old = huey.immediate
        huey.immediate = True
        try:
            self.client.post(reverse('commercial:suscripcion_cancel', args=[sub.uuid]))
        finally:
            huey.immediate = old

        self.assertTrue(
            Notification.objects.filter(
                recipient=self.client_user, title__startswith='Suscripción anulada'
            ).exists()
        )
