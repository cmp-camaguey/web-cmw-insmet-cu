"""La regla de facturación por contrato, en el modelo y en los dos flujos.

Una persona jurídica no se factura sin un contrato que cubra el período; una
natural se factura igual, porque en el centro no lleva contrato. La regla vive en
`Invoice.validar_contrato()` y los flujos que crean facturas la llaman antes de
escribir la fila, porque `Model.save()` no ejecuta `clean()`.
"""

import itertools
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.commercial.models import (
    Contract,
    Customer,
    Invoice,
    Service,
    ServiceSubscription,
)
from apps.commercial.tests.factories import (
    juridica_customer,
    make_user,
    natural_customer,
)
from apps.commercial.views.invoices import InvoiceCreateView
from apps.core.models import CompanySettings, SiteConfiguration
from config.huey import huey


def _build_request(user):
    """Request con usuario y storage de mensajes, como lo usan las vistas."""
    from django.contrib.messages.storage.fallback import FallbackStorage

    request = RequestFactory().get('/')
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _aware(fecha):
    return timezone.make_aware(datetime(fecha.year, fecha.month, fecha.day, 12, 0))


_username_seq = itertools.count(1)


def _short_username(prefix):
    """Usuario único y corto para `setUp`.

    `self.id()` devuelve el nombre cualificado del test: 141 caracteres en este
    archivo. Sumarle un prefijo daba 156 y `User.username` es `varchar(150)`.
    SQLite no valida `max_length`; PostgreSQL sí, así que el test moría con
    `DataError` al crear el usuario en vez de evaluar la regla de facturación.
    El contador mantiene la unicidad sin depender del nombre del test.
    """
    return f'{prefix}-{next(_username_seq)}'


class _Linea:
    """Forma mínima que consume `_guardar_factura_manual` de una línea."""

    def __init__(self, **cleaned_data):
        self.cleaned_data = cleaned_data


class InvoiceContractRuleModelTests(TestCase):
    """`validar_contrato()` como fuente única de verdad."""

    @classmethod
    def setUpTestData(cls):
        cls.provider = make_user('prov-regla')
        cls.juridica = juridica_customer(username='juridica-regla')
        cls.natural = natural_customer(username='natural-regla')

    def _factura(self, customer, inicio=date(2026, 3, 1), fin=date(2026, 3, 31)):
        return Invoice(
            customer=customer,
            number='INV-REGLA',
            amount=Decimal('100.00'),
            period_start=inicio,
            period_end=fin,
        )

    def test_juridica_sin_contrato_no_se_factura(self):
        with self.assertRaises(ValidationError) as ctx:
            self._factura(self.juridica).validar_contrato()
        self.assertIn('ninguno de sus contratos', str(ctx.exception))

    def test_juridica_con_contrato_vencido_no_se_factura(self):
        Contract.objects.create(
            customer=self.juridica,
            number='C-VENCIDO',
            start_date=date(2024, 1, 1),
            validity_years=1,  # vence 2025-01-01
        )
        with self.assertRaises(ValidationError):
            self._factura(self.juridica).validar_contrato()

    def test_juridica_con_contrato_vigente_se_factura(self):
        Contract.objects.create(
            customer=self.juridica,
            number='C-VIGENTE',
            start_date=date(2026, 1, 1),
            validity_years=2,
        )
        self._factura(self.juridica).validar_contrato()

    def test_el_contrato_debe_cubrir_todo_el_periodo(self):
        """Un contrato que cubre marzo pero no abril no alcanza para marzo-abril."""
        Contract.objects.create(
            customer=self.juridica,
            number='C-PARCIAL',
            start_date=date(2025, 3, 15),
            validity_years=1,  # vence 2026-03-15
        )
        with self.assertRaises(ValidationError):
            self._factura(self.juridica, date(2026, 3, 1), date(2026, 4, 30)).validar_contrato()

    def test_renovaciones_encadenadas_cubren_el_periodo(self):
        """Dos contratos seguidos cubren un rango que ninguno cubre solo."""
        Contract.objects.create(
            customer=self.juridica, number='C-2025', start_date=date(2025, 1, 1), validity_years=1
        )
        Contract.objects.create(
            customer=self.juridica, number='C-2026', start_date=date(2026, 1, 1), validity_years=2
        )
        self._factura(self.juridica, date(2026, 1, 1), date(2026, 4, 30)).validar_contrato()

    def test_un_contrato_desactivado_no_cuenta(self):
        contrato = Contract.objects.create(
            customer=self.juridica,
            number='C-BORRADO',
            start_date=date(2026, 1, 1),
            validity_years=2,
        )
        contrato.delete()  # soft delete: el manager por defecto lo excluye
        with self.assertRaises(ValidationError):
            self._factura(self.juridica).validar_contrato()

    def test_natural_sin_contrato_se_factura(self):
        self.assertEqual(self.natural.contracts.count(), 0)
        self._factura(self.natural).validar_contrato()

    def test_clean_reporta_el_contrato_ademas_del_monto(self):
        with self.assertRaises(ValidationError) as ctx:
            self._factura(self.juridica).clean()
        self.assertIn('ninguno de sus contratos', str(ctx.exception))


class InvoiceContractRuleFlowTests(TestCase):
    """Los dos caminos que crean facturas no pueden saltarse la regla."""

    @classmethod
    def setUpTestData(cls):
        cls.provider = make_user('prov-flujo')
        cls.service = Service.objects.create(
            user=cls.provider,
            title='Agrometeorología',
            summary='Servicio mensual',
            service_type='commercial',
            service_category='agrometeo',
            code='AGR-REGLA',
            price=Decimal('50.00'),
        )

    def setUp(self):
        self.juridica = juridica_customer(username=_short_username('juridica-flujo'))
        self.natural = natural_customer(username=_short_username('natural-flujo'))
        self.view = InvoiceCreateView()
        self.view.request = _build_request(make_user(_short_username('operador')))

    def _suscripcion(self, customer, cantidad=1, inicio=date(2026, 3, 10)):
        return ServiceSubscription.objects.create(
            customer=customer,
            service=self.service,
            start_date=_aware(inicio),
            quantity=cantidad,
            payment_status='pending',
        )

    # --- facturación por lote -------------------------------------------

    def test_el_lote_rechaza_una_juridica_sin_contrato(self):
        sub = self._suscripcion(self.juridica)
        with self.assertRaises(ValidationError):
            self.view.process_batch_invoice(self.juridica, 'REG', [sub])
        self.assertEqual(Invoice.objects.count(), 0, 'la transacción deshace la factura')

    def test_el_lote_rechaza_una_juridica_con_contrato_vencido(self):
        Contract.objects.create(
            customer=self.juridica,
            number='C-LOTE-VENCIDO',
            start_date=date(2024, 1, 1),
            validity_years=1,
        )
        sub = self._suscripcion(self.juridica)
        with self.assertRaises(ValidationError):
            self.view.process_batch_invoice(self.juridica, 'REG', [sub])
        self.assertEqual(Invoice.objects.count(), 0)

    def test_el_lote_factura_una_juridica_con_contrato_vigente(self):
        Contract.objects.create(
            customer=self.juridica,
            number='C-LOTE-OK',
            start_date=date(2026, 1, 1),
            validity_years=2,
        )
        sub = self._suscripcion(self.juridica)
        factura = self.view.process_batch_invoice(self.juridica, 'REG', [sub])[0]
        self.assertEqual(factura.customer_id, self.juridica.pk)
        self.assertEqual(factura.period_start, date(2026, 3, 1))
        self.assertEqual(factura.period_end, date(2026, 3, 31))

    def test_el_lote_factura_una_persona_natural_sin_contrato(self):
        sub = self._suscripcion(self.natural)
        factura = self.view.process_batch_invoice(self.natural, 'REG', [sub])[0]
        self.assertEqual(factura.customer_id, self.natural.pk)
        self.assertTrue(self.natural.client_type == Customer.ClientType.NATURAL)

    # --- facturación manual ---------------------------------------------

    def _linea(self, inicio=date(2026, 3, 10), cantidad=1):
        return _Linea(
            start_date=inicio,
            cantidad=cantidad,
            service=self.service,
            codigo=self.service.code,
            precio=self.service.price,
        )

    def test_la_manual_rechaza_una_juridica_sin_contrato(self):
        with self.assertRaises(ValidationError):
            self.view._guardar_factura_manual(self.juridica, [self._linea()], 'REG')
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(
            ServiceSubscription.objects.filter(customer=self.juridica).count(),
            0,
            'la suscripción creada para la línea se deshace con la factura',
        )

    def test_la_manual_rechaza_una_juridica_con_contrato_vencido(self):
        Contract.objects.create(
            customer=self.juridica,
            number='C-MANUAL-VENCIDO',
            start_date=date(2023, 1, 1),
            validity_years=1,
        )
        with self.assertRaises(ValidationError):
            self.view._guardar_factura_manual(self.juridica, [self._linea()], 'REG')
        self.assertEqual(Invoice.objects.count(), 0)

    def test_la_manual_factura_una_juridica_con_contrato_vigente(self):
        Contract.objects.create(
            customer=self.juridica,
            number='C-MANUAL-OK',
            start_date=date(2026, 1, 1),
            validity_years=2,
        )
        factura, total, subs = self.view._guardar_factura_manual(
            self.juridica, [self._linea()], 'REG'
        )
        self.assertEqual(factura.customer_id, self.juridica.pk)
        self.assertEqual(factura.period_start, date(2026, 3, 1))
        self.assertEqual(total, Decimal('50.00'))
        self.assertEqual(len(subs), 1)

    def test_la_manual_factura_una_persona_natural_sin_contrato(self):
        factura, _total, _subs = self.view._guardar_factura_manual(
            self.natural, [self._linea()], 'REG'
        )
        self.assertEqual(factura.customer_id, self.natural.pk)

    def test_la_manual_cubre_un_rango_de_dos_meses_con_un_solo_contrato(self):
        Contract.objects.create(
            customer=self.juridica,
            number='C-MANUAL-RANGO',
            start_date=date(2026, 1, 1),
            validity_years=2,
        )
        factura, _total, _subs = self.view._guardar_factura_manual(
            self.juridica, [self._linea(cantidad=2)], 'REG'
        )
        self.assertEqual(factura.period_start, date(2026, 3, 1))
        self.assertEqual(factura.period_end, date(2026, 4, 30))


class InvoiceContractRuleHttpTests(TestCase):
    """La regla en la pantalla: el rechazo vuelve con un aviso, no con un 500.

    `form_valid` atrapa el `ValidationError` que sube desde `validar_contrato()`
    y re-renderiza el formulario. Sin eso, la excepción del modelo se convertiría
    en un error 500 y el operador no sabría por qué no puede facturar.
    """

    @classmethod
    def setUpTestData(cls):
        SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})
        company = CompanySettings.get_instance()
        company.registro_comercial = 'A54877'
        company.save(update_fields=['registro_comercial'])
        # `CheckUserProfileMiddleware` rebota a quien no tiene nombre y apellido.
        cls.admin = User.objects.create_superuser(
            username='contratoadmin',
            email='contrato@example.com',
            password='pass',  # pragma: allowlist secret
            first_name='Admin',
            last_name='Contrato',
        )
        cls.customer = juridica_customer(username='juridica-http')
        cls.service = Service.objects.create(
            user=cls.admin,
            title='Agrometeorología',
            summary='Servicio mensual',
            service_type='commercial',
            service_category='agrometeo',
            code='700501072507005',
            price=Decimal('45.00'),
        )
        cls.subscription = ServiceSubscription.objects.create(
            customer=cls.customer,
            service=cls.service,
            start_date=timezone.now(),
            quantity=1,
            payment_status='pending',
        )

    def setUp(self):
        huey.flush()
        self.addCleanup(huey.flush)
        self.client.force_login(self.admin)

    def _post(self):
        hoy = timezone.localdate()
        return self.client.post(
            reverse('commercial:factura_create'),
            {
                'customer': self.customer.pk,
                'year': str(hoy.year),
                'month_from': str(hoy.month),
                'month_to': str(hoy.month),
                'commercial_registry': 'REG-CONTRATO',
                'subscriptions': [str(self.subscription.pk)],
                'cost_allocations-TOTAL_FORMS': '1',
                'cost_allocations-INITIAL_FORMS': '0',
                'cost_allocations-MIN_NUM_FORMS': '0',
                'cost_allocations-MAX_NUM_FORMS': '1000',
                'cost_allocations-0-codigo': '700.50107',
                'cost_allocations-0-porcentaje': '100.00',
            },
        )

    def test_el_lote_sin_contrato_avisa_y_no_crea_factura(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        avisos = [str(mensaje) for mensaje in get_messages(response.wsgi_request)]
        self.assertTrue(
            any('ninguno de sus contratos' in aviso for aviso in avisos),
            f'el aviso tiene que nombrar el contrato, y llegó: {avisos}',
        )
        self.assertFalse(Invoice.objects.filter(customer=self.customer).exists())

    def test_el_lote_con_contrato_vigente_factura(self):
        Contract.objects.create(
            customer=self.customer,
            number='C-HTTP',
            start_date=timezone.localdate().replace(month=1, day=1),
            validity_years=2,
        )

        self._post()

        self.assertTrue(Invoice.objects.filter(customer=self.customer).exists())
