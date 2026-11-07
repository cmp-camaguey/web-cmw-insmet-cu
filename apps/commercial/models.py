import calendar
import uuid
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from apps.core.models import FileHandlerMixin, SoftDeleteModel, image_upload_path, pdf_upload_path
from apps.core.validators import (
    validate_account,
    validate_image_upload,
    validate_nit,
    validate_phones,
    validate_reeup,
)

MESES = (
    'enero',
    'febrero',
    'marzo',
    'abril',
    'mayo',
    'junio',
    'julio',
    'agosto',
    'septiembre',
    'octubre',
    'noviembre',
    'diciembre',
)


# Constantes de validación compartidas con los formularios
CUSTOMER_IDENTITY_REQUIRED_ERROR = (
    'El documento de identidad es obligatorio para personas naturales.'
)
CUSTOMER_IDENTITY_TAKEN_ERROR = 'Este documento de identidad ya está registrado.'
CUSTOMER_COMPANY_NAME_REQUIRED_ERROR = (
    'El nombre de la empresa es obligatorio para personas jurídicas.'
)
CUSTOMER_REEUP_REQUIRED_ERROR = 'El REEUP es obligatorio para personas jurídicas.'
CUSTOMER_NIT_REQUIRED_ERROR = 'El NIT es obligatorio para personas jurídicas.'
CUSTOMER_REEUP_TAKEN_ERROR = 'Este código REEUP ya está registrado.'
CUSTOMER_NIT_TAKEN_ERROR = 'Este NIT ya está registrado.'


def frase_de_periodo(inicio, fin):
    """`Mes de mayo de 2025` para un mes, `De mayo a junio de 2025` para varios."""
    primero = MESES[inicio.month - 1]
    ultimo = MESES[fin.month - 1]
    if (inicio.year, inicio.month) == (fin.year, fin.month):
        return f'Mes de {primero} de {inicio.year}'
    if inicio.year == fin.year:
        return f'De {primero} a {ultimo} de {fin.year}'
    return f'De {primero} de {inicio.year} a {ultimo} de {fin.year}'


def primer_dia_del_mes(fecha):
    return date(fecha.year, fecha.month, 1)


def ultimo_dia_del_mes(fecha):
    return date(fecha.year, fecha.month, calendar.monthrange(fecha.year, fecha.month)[1])


def mes_siguiente(fecha):
    return date(fecha.year + fecha.month // 12, fecha.month % 12 + 1, 1)


def meses_de_la_linea(inicio, cantidad, categoria):
    """Los meses que ocupa una línea facturada, según su inicio y su cantidad.

    Es la única forma de saber el período: no se declara, se deduce de lo que se
    está cobrando. La cantidad no se lee igual en las dos categorías que vende el
    catálogo: `agrometeo` se cobra por meses, así que la cantidad es el largo del
    período; `pronostico` se cobra por días, así que lo que define el período es
    hasta qué día llega la cobertura.
    """
    cantidad = max(cantidad or 1, 1)
    if categoria == 'pronostico':
        # Días inclusivos: un pronóstico de un día cae en un solo mes, pero uno de
        # 31 empieza en un mes y termina en el siguiente.
        ultimo = inicio + timedelta(days=cantidad - 1)
        meses = []
        year, month = inicio.year, inicio.month
        while (year, month) <= (ultimo.year, ultimo.month):
            meses.append((year, month))
            month += 1
            if month == 13:
                year, month = year + 1, 1
        return tuple(meses)

    # `agrometeo` y cualquier otra categoría se cuentan por meses, así que el
    # período se arma mes a mes. La aritmética va sobre (año, mes) y no sobre
    # días: sumar 31 días a un mes de 30 días se pasa al mes siguiente.
    year, month = inicio.year, inicio.month
    meses = []
    for _ in range(cantidad):
        meses.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return tuple(meses)


def rango_desde_meses(meses):
    """Primer día del primer mes y último del último; `(None, None)` si no hay.

    Acepta cualquier iterable de `(año, mes)`: el período de una factura es la
    unión de los meses de sus líneas, así que lo normal es que llegue un `set`.
    """
    if not meses:
        return None, None
    meses = sorted(meses)
    y0, m0 = meses[0]
    yn, mn = meses[-1]
    return primer_dia_del_mes(date(y0, m0, 1)), ultimo_dia_del_mes(date(yn, mn, 1))


class Customer(SoftDeleteModel):
    class ClientType(models.TextChoices):
        NATURAL = 'natural', 'Persona Natural'
        JURIDICA = 'juridica', 'Persona Jurídica'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    client_type = models.CharField(
        max_length=8,
        choices=ClientType.choices,
        default=ClientType.JURIDICA,
        verbose_name='Tipo de Cliente',
    )
    identity_document = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Documento de identidad',
    )
    company_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Nombre de la Empresa',
    )
    reeup = models.CharField(
        max_length=12,
        blank=True,
        null=True,
        unique=True,
        verbose_name='REEUP',
        validators=[validate_reeup],
    )
    nit = models.CharField(
        max_length=11,
        blank=True,
        null=True,
        unique=True,
        verbose_name='NIT',
        validators=[validate_nit],
    )
    account = models.CharField(
        max_length=16,
        unique=True,
        verbose_name='Cuenta Bancaria',
        validators=[validate_account],
    )
    agency_bank = models.CharField(
        max_length=100, blank=True, null=True, verbose_name='Agencia Bancaria'
    )
    address = models.TextField(verbose_name='Dirección')
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='Usuario',
        related_name='commercial_customer',
    )
    phone = models.CharField(
        max_length=100,
        verbose_name='Teléfonos',
        validators=[validate_phones],
    )
    accept_terms = models.BooleanField(default=False, verbose_name='Aceptó Términos')

    class Meta:
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'
        default_permissions = ()
        permissions = (
            ('view_customer', 'Ver'),
            ('add_customer', 'Añadir'),
            ('change_customer', 'Editar'),
            ('delete_customer', 'Eliminar'),
        )

    @property
    def display_name(self):
        """Nombre del cliente tal como debe mostrarse en la interfaz.

        `company_name` solo existe para personas jurídicas: en una natural está
        vacío, y las plantillas que leían el campo crudo imprimían `None` en
        lugar del nombre de la persona, que vive en el User. Se centraliza acá
        para que listados, tooltips y atributos `data-*` no diverjan.
        """
        try:
            user = self.user
        except Exception:
            # Una instancia sin `user_id` todavía (no guardada, o
            # cargada con `.only()`) no tiene a quién mostrarle nombre: se
            # devuelve un placeholder legible en vez de explotar en la plantilla.
            user = None
        full_name = user.get_full_name() if user else ''
        fallback = full_name or (user.username if user else '')
        if self.client_type == self.ClientType.NATURAL:
            return fallback or '—'
        return self.company_name or fallback or '—'

    def clean(self):
        """Obliga la regla de identidad que antes solo vivía en formularios.

        La regla es: una persona natural debe tener documento de identidad;
        una persona jurídica debe tener razón social, REEUP y NIT. Los campos
        ajenos no se fuerzan a vacío (el formulario los controla), pero sí se
        validan si se envían con formato inválido o duplicado.
        """
        # Solo aplicamos las reglas de identidad aquí para no romper flujos
        # existentes que crean instancias sin pasar por todos los validadores
        # de campos. Los formularios siguen haciendo su propia validación.
        client_type = self.client_type

        if client_type == self.ClientType.NATURAL:
            identity_document = (self.identity_document or '').strip()
            if not identity_document:
                raise ValidationError({'identity_document': [CUSTOMER_IDENTITY_REQUIRED_ERROR]})
            if not self.pk:
                if Customer.objects.filter(identity_document=identity_document).exists():
                    raise ValidationError({'identity_document': [CUSTOMER_IDENTITY_TAKEN_ERROR]})
            else:
                if (
                    Customer.objects.filter(identity_document=identity_document)
                    .exclude(pk=self.pk)
                    .exists()
                ):
                    raise ValidationError({'identity_document': [CUSTOMER_IDENTITY_TAKEN_ERROR]})
            self.identity_document = identity_document
            return

        if client_type == self.ClientType.JURIDICA:
            company_name = (self.company_name or '').strip()
            reeup = (self.reeup or '').strip()
            nit = (self.nit or '').strip()
            errors = {}
            if not company_name:
                errors['company_name'] = [CUSTOMER_COMPANY_NAME_REQUIRED_ERROR]
            else:
                self.company_name = company_name
            if not reeup:
                errors['reeup'] = [CUSTOMER_REEUP_REQUIRED_ERROR]
            else:
                try:
                    validate_reeup(reeup)
                except ValidationError as exc:
                    errors['reeup'] = list(exc.messages)
                else:
                    self.reeup = reeup
                    if not self.pk:
                        if Customer.objects.filter(reeup=reeup).exists():
                            errors['reeup'] = [CUSTOMER_REEUP_TAKEN_ERROR]
                    else:
                        if Customer.objects.filter(reeup=reeup).exclude(pk=self.pk).exists():
                            errors['reeup'] = [CUSTOMER_REEUP_TAKEN_ERROR]
            if not nit:
                errors['nit'] = [CUSTOMER_NIT_REQUIRED_ERROR]
            else:
                try:
                    validate_nit(nit)
                except ValidationError as exc:
                    errors['nit'] = list(exc.messages)
                else:
                    self.nit = nit
                    if not self.pk:
                        if Customer.objects.filter(nit=nit).exists():
                            errors['nit'] = [CUSTOMER_NIT_TAKEN_ERROR]
                    else:
                        if Customer.objects.filter(nit=nit).exclude(pk=self.pk).exists():
                            errors['nit'] = [CUSTOMER_NIT_TAKEN_ERROR]
            if errors:
                raise ValidationError(errors)
            return

    def _validate_identity_rules(self):
        """Validación de identidad sin llamar full_clean() para no romper
        el comportamiento existente en toda la app."""
        client_type = self.client_type
        if client_type == self.ClientType.NATURAL:
            identity_document = (self.identity_document or '').strip()
            if not identity_document:
                raise ValidationError({'identity_document': [CUSTOMER_IDENTITY_REQUIRED_ERROR]})
            if self.pk:
                if (
                    Customer.objects.filter(identity_document=identity_document)
                    .exclude(pk=self.pk)
                    .exists()
                ):
                    raise ValidationError({'identity_document': [CUSTOMER_IDENTITY_TAKEN_ERROR]})
            else:
                if Customer.objects.filter(identity_document=identity_document).exists():
                    raise ValidationError({'identity_document': [CUSTOMER_IDENTITY_TAKEN_ERROR]})
            self.identity_document = identity_document
            return
        if client_type == self.ClientType.JURIDICA:
            company_name = (self.company_name or '').strip()
            reeup = (self.reeup or '').strip()
            nit = (self.nit or '').strip()
            errors = {}
            if not company_name:
                errors['company_name'] = [CUSTOMER_COMPANY_NAME_REQUIRED_ERROR]
            else:
                self.company_name = company_name
            if not reeup:
                errors['reeup'] = [CUSTOMER_REEUP_REQUIRED_ERROR]
            else:
                try:
                    validate_reeup(reeup)
                except ValidationError as exc:
                    errors['reeup'] = list(exc.messages)
                else:
                    self.reeup = reeup
                    if self.pk:
                        if Customer.objects.filter(reeup=reeup).exclude(pk=self.pk).exists():
                            errors['reeup'] = [CUSTOMER_REEUP_TAKEN_ERROR]
                    else:
                        if Customer.objects.filter(reeup=reeup).exists():
                            errors['reeup'] = [CUSTOMER_REEUP_TAKEN_ERROR]
            if not nit:
                errors['nit'] = [CUSTOMER_NIT_REQUIRED_ERROR]
            else:
                try:
                    validate_nit(nit)
                except ValidationError as exc:
                    errors['nit'] = list(exc.messages)
                else:
                    self.nit = nit
                    if self.pk:
                        if Customer.objects.filter(nit=nit).exclude(pk=self.pk).exists():
                            errors['nit'] = [CUSTOMER_NIT_TAKEN_ERROR]
                    else:
                        if Customer.objects.filter(nit=nit).exists():
                            errors['nit'] = [CUSTOMER_NIT_TAKEN_ERROR]
            if errors:
                raise ValidationError(errors)
            return

    def save(self, *args, **kwargs):
        # Garantiza la regla también fuera de formularios.
        if kwargs.pop('run_validators', True):
            self._validate_identity_rules()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name


class Service(SoftDeleteModel, FileHandlerMixin, models.Model):
    PUBLIC = 'public'
    COMMERCIAL = 'commercial'
    TYPE_CHOICES = [
        (PUBLIC, 'Público'),
        (COMMERCIAL, 'Comercial'),
    ]

    PERIOD_DAYS = 1
    PERIOD_MONTHS = 1

    SERVICE_CATEGORY_CHOICES = [
        ('agrometeo', 'Agrometeorológico'),
        ('pronostico', 'Pronóstico'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    date = models.DateTimeField(auto_now_add=True, verbose_name='Fecha')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='Usuario',
        related_name='commercial_created_services',
    )
    title = models.CharField(max_length=100, verbose_name='Título')
    summary = models.CharField(max_length=500, verbose_name='Resumen')
    service_type = models.CharField(
        max_length=10, choices=TYPE_CHOICES, default=PUBLIC, verbose_name='Tipo de Servicio'
    )
    pdf = models.FileField(
        upload_to=pdf_upload_path, blank=True, null=True, verbose_name='Archivo PDF'
    )
    image = models.ImageField(
        upload_to=image_upload_path,
        blank=True,
        null=True,
        verbose_name='Imagen',
        validators=[validate_image_upload],
    )
    code = models.CharField(
        max_length=50, unique=True, null=True, blank=True, verbose_name='Código del servicio'
    )
    price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Precio (CUP)'
    )
    service_category = models.CharField(
        max_length=10,
        choices=SERVICE_CATEGORY_CHOICES,
        default='pronostico',
        verbose_name='Categoría del servicio',
    )

    file_fields = ['pdf', 'image']

    class Meta:
        verbose_name = 'Servicio'
        verbose_name_plural = 'Servicios'
        default_permissions = ()
        permissions = (
            ('view_service', 'Ver'),
            ('add_service', 'Añadir'),
            ('change_service', 'Editar'),
            ('delete_service', 'Eliminar'),
        )

    def __str__(self):
        return self.title

    def get_image_url(self):
        if self.image:
            return self.image.url
        return f'{settings.STATIC_URL}dist/img/default.svg'

    def get_billing_period_display(self):
        return 'mes' if self.service_category == 'agrometeo' else 'día'

    def get_price_per_period_display(self):
        if self.price is None:
            return ''
        return f'${self.price:.2f} / {self.get_billing_period_display()}'


class ServiceSubscription(SoftDeleteModel, FileHandlerMixin, models.Model):
    PAYMENT_STATUS_CHOICES = [
        ('requested', 'Solicitado'),
        ('pending', 'Pendiente de pago'),
        ('paid', 'Pagado'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('qr', 'Pago por Código QR'),
        ('transfer', 'Transferencia Bancaria'),
        ('presencial', 'Pago Presencial'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, verbose_name='Cliente')
    service = models.ForeignKey(Service, on_delete=models.CASCADE, verbose_name='Servicio')
    start_date = models.DateTimeField(verbose_name='Fecha de inicio', null=True, blank=True)
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name='Cantidad',
        help_text='Meses (agrometeo) o días (pronóstico)',
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default='requested',
        verbose_name='Estado de pago',
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        verbose_name='Método de pago',
        blank=True,
        null=True,
    )

    file_fields = []

    class Meta:
        verbose_name = 'Suscripción de servicio'
        verbose_name_plural = 'Suscripciones de servicios'
        default_permissions = ()
        permissions = (
            ('view_subscription', 'Ver'),
            ('add_subscription', 'Añadir'),
            ('change_subscription', 'Editar'),
            ('delete_subscription', 'Eliminar'),
        )

    @property
    def is_active(self):
        """Una suscripción está activa cuando está pagada y no ha sido cancelada."""
        if not self.record_active:
            return False
        return self.payment_status == 'paid'

    def get_quantity_period_display(self):
        """Cantidad + unidad de facturación: `1 día`, `3 meses`, `30 días`."""
        unit = self.service.get_billing_period_display()  # 'día' | 'mes'
        plural = {'día': 'días', 'mes': 'meses'}.get(unit, f'{unit}s')
        return f'{self.quantity} {plural if self.quantity != 1 else unit}'

    @property
    def status_display(self):
        """Estado único del ciclo de vida, para listados y exportaciones.

        `cancelada` sale de la baja lógica (`record_active`), no de un valor
        propio de `payment_status`: anular ya marca `record_active=False`, y
        llevar el mismo dato en dos campos es la forma más directa de que
        diverjan. El estado es sólo el del pago: una suscripción no tiene
        expiración que consultarse acá.
        """
        if not self.record_active:
            return 'cancelada'
        return {
            'requested': 'solicitado',
            'pending': 'pendiente',
            'paid': 'pagado',
        }.get(self.payment_status, 'solicitado')

    def __str__(self):
        return f'{self.customer.display_name} - {self.service.title}'


class InvoiceQuerySet(models.QuerySet):
    def for_subscription(self, sub):
        """Facturas de una suscripción, por cualquiera de los dos caminos.

        `Invoice.subscription` es un ancla de conveniencia y queda NULL cuando la
        factura cubre varias suscripciones: la facturación por lote agrupa
        suscripciones con el mismo período y la manual de varios servicios sólo
        cuelga la primera. El vínculo que nunca falta es el de cada línea
        (`InvoiceItem.subscription`), así que leer sólo la relación inversa deja
        al cliente sin botones de factura justo en esos casos.
        """
        return self.filter(Q(subscription=sub) | Q(items__subscription=sub)).distinct()

    def with_display_status(self):
        """Anota el conteo de líneas pagadas para no repetir la consulta por fila.

        `Invoice.status_display` cae a las líneas cuando no encuentra estas
        anotaciones, así que sin esto el listado pagaría una consulta por
        factura.
        """
        return self.annotate(
            items_total=Count(
                'items__subscription',
                distinct=True,
                filter=Q(items__subscription__isnull=False),
            ),
            items_paid=Count(
                'items__subscription',
                distinct=True,
                filter=Q(items__subscription__payment_status='paid'),
            ),
        )


class Invoice(SoftDeleteModel, FileHandlerMixin):
    objects = InvoiceQuerySet.as_manager()
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    subscription = models.ForeignKey(
        ServiceSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices',
        verbose_name='Suscripción',
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoices',
        verbose_name='Cliente',
    )
    number = models.CharField(max_length=50, unique=True, verbose_name='Número de factura')
    issue_date = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de emisión')
    period_start = models.DateField(null=True, blank=True, verbose_name='Período facturado desde')
    period_end = models.DateField(null=True, blank=True, verbose_name='Período facturado hasta')
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Monto')
    pdf = models.FileField(
        upload_to=pdf_upload_path, verbose_name='Archivo PDF', blank=True, null=True
    )
    is_cancelled = models.BooleanField(default=False, verbose_name='¿Anulada?')

    class PdfStatus(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        READY = 'ready', 'Generado'
        FAILED = 'failed', 'Falló'

    class EmailStatus(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        SENT = 'sent', 'Enviado'
        FAILED = 'failed', 'Falló'

    pdf_status = models.CharField(
        max_length=10,
        choices=PdfStatus.choices,
        default=PdfStatus.PENDING,
        verbose_name='Estado del PDF',
    )
    pdf_error = models.TextField(blank=True, null=True, verbose_name='Error al generar el PDF')
    email_status = models.CharField(
        max_length=10,
        choices=EmailStatus.choices,
        default=EmailStatus.PENDING,
        verbose_name='Estado del correo',
    )
    email_error = models.TextField(blank=True, null=True, verbose_name='Error al enviar')

    file_fields = ['pdf']

    @property
    def pdf_ready(self):
        """El PDF se puede mostrar y descargar solo si el render terminó bien.

        No alcanza con que el archivo exista: un render fallido a mitad de
        camino deja el campo `pdf` PopulationError. El estado explícito es lo
        que decide si los botones aparecen.
        """
        return self.pdf_status == self.PdfStatus.READY and bool(self.pdf)

    @property
    def period_label(self):
        """La frase del período facturado que imprime el PDF.

        Sin período cae a la fecha de emisión: es un respaldo de lectura, no una
        migración de datos, porque la frase se calcula en lectura en vez de
        guardarse duplicada.
        """
        if self.period_start and self.period_end:
            return frase_de_periodo(self.period_start, self.period_end)
        return self.issue_date.strftime('%d/%m/%Y')

    class Meta:
        verbose_name = 'Factura'
        verbose_name_plural = 'Facturas'
        ordering = ['-issue_date']
        default_permissions = ()
        permissions = (
            ('view_invoice', 'Ver'),
            ('add_invoice', 'Añadir'),
            ('change_invoice', 'Editar'),
            ('delete_invoice', 'Eliminar'),
        )

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError('El monto de la factura debe ser mayor que cero.')
        self.validar_contrato()

    def validar_contrato(self):
        """Una jurídica no se factura fuera de la vigencia de sus contratos.

        Las personas naturales no llevan contrato y se facturan igual: es una
        medida del centro, no un descuido. La regla mira el mes facturado, no la
        vigencia de hoy, porque los meses pasados también se facturan.

        Es pública y se llama explícitamente desde los dos flujos que crean
        facturas (`_facturar_grupo` y `_guardar_factura_manual`) por dos razones:
        `Model.save()` no ejecuta `clean()`, así que `objects.create()` la
        sortearía; y `full_clean()` en ese punto rechazaría la factura por el
        `amount=0` con el que nace, antes de que se sumen sus líneas. Validar en
        `save()` tampoco sirve: anular una factura histórica no puede depender de
        que el contrato siga vigente hoy.
        """
        if self.customer_id is None or self.customer.client_type != Customer.ClientType.JURIDICA:
            return
        if not self.period_start or not self.period_end:
            return
        if Contract.cubre_rango(self.customer, self.period_start, self.period_end):
            return
        raise ValidationError(
            f'No se puede facturar a {self.customer.display_name}: ninguno de sus contratos '
            f'cubre el período facturado ({self.period_label}).'
        )

    @property
    def status_display(self):
        """Estado derivado: la fuente de verdad del pago es la suscripción.

        No hay campo de estado en la factura a propósito. La facturación por
        lote cubre varias suscripciones y cada una se aprueba por separado, así
        que un campo propio sólo añadiría una segunda fuente de verdad capaz de
        divergir de la real. Se lee de las líneas, o de las anotaciones que
        deja `with_display_status()` cuando el listado ya las trajo.
        """
        if self.is_cancelled:
            return 'cancelada'
        if hasattr(self, 'items_total'):
            total, paid = self.items_total, self.items_paid
        else:
            # `filter`, nunca `exclude`: `items` es una relación inversa, y
            # excluir sobre una relación multi-valorada arma un subquery que
            # termina descartando todas las líneas.
            subs = self.items.filter(subscription__isnull=False).values_list(
                'subscription__payment_status', flat=True
            )
            states = list(subs)
            total, paid = len(states), states.count('paid')
        return 'pagada' if total and paid == total else 'pendiente'

    @property
    def line_payments(self):
        """Líneas con suscripción y su estado de cobro, para el desglose del listado.

        Vive en el modelo, no en la plantilla ni en la vista, por la misma razón
        que `status_display`: el estado de una línea se deriva de su suscripción
        (`payment_status` y la baja lógica `record_active`), y resolverlo en la
        plantilla o en la vista es abrir una segunda fuente de verdad que puede
        divergir de la real.

        No dispara consultas propias: itera `self.items` tal como lo dejó el
        `Prefetch('items', select_related('subscription__service'))` de las dos
        querysets que renderizan la celda (listado y endpoint). Leer
        `subscription.payment_status`/`record_active` y `service.title` sale de
        ese prefetch; sin él, cada línea pagaría sus propias consultas.
        """
        lines = []
        for item in self.items.all():
            subscription = item.subscription
            if subscription is None:
                continue
            if not subscription.record_active:
                estado = 'cancelada'
            elif subscription.payment_status == 'paid':
                estado = 'pagado'
            else:
                estado = 'pendiente'
            lines.append(
                {
                    'uuid': str(subscription.uuid),
                    'descripcion': item.descripcion,
                    'servicio': subscription.service.title,
                    'importe': item.importe,
                    'estado': estado,
                    # Aprobable = activa y `pending`, el único estado que acepta
                    # `ApproveSubscriptionView` (`views/subscriptions.py:190-193`).
                    # Sin `record_active` una suscripción dada de baja con el pago
                    # todavía en `pending` ofrecería el link, que el listado de
                    # suscripciones nunca ofrece; sin el filtro de `pending` una
                    # `requested` daría un enlace muerto (la vista la rechaza).
                    'aprobable': (
                        subscription.record_active and subscription.payment_status == 'pending'
                    ),
                }
            )
        return lines

    @property
    def linea_aprobable(self):
        """La única línea por cobrar de la factura, o `None` si hay 0 o 2+.

        Un botón de fila sólo es inequívoco cuando la factura tiene una sola
        suscripción: con dos o más pendientes no hay acción de fila que las
        abarque y el desglose por línea (`line_payments.html`) es el que lleva
        cada link. Por eso devuelve `None` si hay más de una línea, aunque una
        sola esté pendiente: ahí manda el desglose.

        Reusa `line_payments` para no abrir una segunda fuente de verdad del
        estado de cobro; no dispara consultas propias (las líneas vienen del
        `Prefetch('items', ...)` del listado y del endpoint).
        """
        lineas = self.line_payments
        if len(lineas) != 1:
            return None
        return lineas[0] if lineas[0]['aprobable'] else None

    def __str__(self):
        if self.subscription:
            return f'Factura {self.number} - {self.subscription.customer.display_name}'
        return f'Factura {self.number} (manual)'


class InvoiceItem(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    subscription = models.ForeignKey(
        ServiceSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_items',
    )
    codigo = models.CharField(max_length=50, blank=True)
    descripcion = models.TextField()
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    unidad_medida = models.CharField(max_length=5, default='U')
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    importe = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Item'
        verbose_name_plural = 'Items'
        ordering = ['invoice', 'codigo']
        default_permissions = ()
        permissions = (
            ('view_invoice_item', 'Ver'),
            ('add_invoice_item', 'Añadir'),
            ('change_invoice_item', 'Editar'),
            ('delete_invoice_item', 'Eliminar'),
        )

    def clean(self):
        if self.cantidad is not None and self.cantidad <= 0:
            raise ValidationError('La cantidad debe ser mayor que cero.')
        if self.precio is not None and self.precio <= 0:
            raise ValidationError('El precio debe ser mayor que cero.')
        self._validar_periodo()

    def _validar_periodo(self):
        """La línea tiene que caer dentro del período facturado.

        Vive en el modelo y no en la vista porque es una regla de integridad: si
        dependiera del formulario, cualquier otro camino que guarde una línea
        podría esquivarla. La fecha de la línea es la de su suscripción, que es el
        único lugar donde vive; copiarla a la línea sería una segunda fuente de
        verdad. Sin período no hay contra qué comparar.
        """
        if not self.invoice_id or not self.subscription_id:
            return
        periodo = self.invoice
        if not periodo.period_start or not periodo.period_end:
            return
        inicio = self.subscription.start_date
        if inicio is None:
            return
        if timezone.is_naive(inicio):
            inicio = timezone.make_aware(inicio)
        inicio = timezone.localtime(inicio).date()
        if not periodo.period_start <= inicio <= periodo.period_end:
            raise ValidationError(
                f'La línea no puede empezar el {inicio:%d/%m/%Y}: ese día está fuera '
                f'del período facturado ({periodo.period_label}).'
            )

    def save(self, *args, **kwargs):
        self.importe = self.cantidad * self.precio
        self.full_clean()
        super().save(*args, **kwargs)


class InvoiceCostAllocation(models.Model):
    """Reparto del importe de una factura entre centros de costo.

    Las tres facturas reales del CMP reparten de forma distinta: 182 a un solo
    centro al 100 %, 279 a dos centros (90/10) y 276 a tres centros (25/65/10).
    Con un solo centro el reparto era un literal fijo en el template, y las otras
    dos facturas salían con la imputación contable equivocada.

    No es un dato derivable de los ítems: la 276 tiene un único ítem repartido en
    tres centros, cosa que ningún cálculo sobre montos puede producir. Por eso es
    una tabla y no una columna. Lo único derivable es el centro por defecto, que
    viaja en el prefijo del código de servicio.

    La regla de que las filas sumen 100 % NO se valida acá a propósito: vive en el
    formset, porque en el modelo impediría borrar y rehacer el reparto con
    `can_delete`, que es la operación normal cuando el operador corrige.
    """

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='cost_allocations')
    codigo = models.CharField(max_length=50, verbose_name='Centro de Costo')
    porcentaje = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='Porcentaje')

    class Meta:
        verbose_name = 'Asignación de centro de costo'
        verbose_name_plural = 'Asignaciones de centro de costo'
        ordering = ['codigo']
        default_permissions = ()
        permissions = (
            ('view_invoice_cost_allocation', 'Ver'),
            ('add_invoice_cost_allocation', 'Añadir'),
            ('change_invoice_cost_allocation', 'Editar'),
            ('delete_invoice_cost_allocation', 'Eliminar'),
        )

    def __str__(self):
        return f'{self.codigo} {self.porcentaje:f} %'

    def clean(self):
        if self.porcentaje is not None and self.porcentaje <= 0:
            raise ValidationError('El porcentaje debe ser mayor que cero.')


class ContractQuerySet(models.QuerySet):
    def vigente_para(self, customer, fecha):
        """El contrato que cubre `fecha`, el más reciente si hay renovaciones.

        El vencimiento se filtra en Python: `end_date` es una propiedad derivada
        de `start_date` + `validity_years`, así que la base no puede compararla.
        Con renovaciones cada contrato tiene su propio rango y gana el más
        reciente (mayor `start_date`) que todavía cubre `fecha`.
        """
        candidatos = self.filter(customer=customer, start_date__lte=fecha).order_by('-start_date')
        for contrato in candidatos:
            if contrato.end_date >= fecha:
                return contrato
        return None


class Contract(SoftDeleteModel):
    objects = ContractQuerySet.as_manager()

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='contracts',
        verbose_name='Cliente',
    )
    number = models.CharField(max_length=50, verbose_name='Número de contrato')
    start_date = models.DateField(verbose_name='Fecha')
    validity_years = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1)],
        verbose_name='Vigencia (años)',
    )

    class Meta:
        verbose_name = 'Contrato'
        verbose_name_plural = 'Contratos'
        default_permissions = ()
        permissions = (
            ('view_contract', 'Ver'),
            ('add_contract', 'Añadir'),
            ('change_contract', 'Editar'),
            ('delete_contract', 'Eliminar'),
        )
        ordering = ('-start_date',)

    def __str__(self):
        return f'Contrato {self.number} - {self.customer.display_name}'

    @property
    def end_date(self):
        """Fin de vigencia derivado: `start_date` + `validity_years` años.

        No se edita ni se guarda: `start_date` y `validity_years` son las dos
        únicas fuentes de verdad, así que no pueden divergir. Con 29/02 como
        inicio y un número de años que no cae en bisiesto, el vencimiento es
        28/02 (no 01/03): es la convención de fechas del CMP.
        """
        anio = self.start_date.year + self.validity_years
        mes = self.start_date.month
        dia = min(self.start_date.day, calendar.monthrange(anio, mes)[1])
        return date(anio, mes, dia)

    def cubre(self, fecha):
        """Este contrato cubre `fecha`."""
        return self.start_date <= fecha <= self.end_date

    @staticmethod
    def cubre_rango(customer, inicio, fin):
        """Si cada mes de `inicio`..`fin` cae en algún contrato del cliente.

        Con varios contratos encadenados por renovación el rango puede estar
        repartido entre ellos, así que alcanza con que la vigencia se solape
        completa, no con que un solo contrato lo abarque.

        Sólo cuentan los contratos activos: el borrado es lógico y sin el filtro
        un contrato desactivado seguiría autorizando facturas.
        """
        contratos = list(customer.contracts.filter(record_active=True).order_by('start_date'))
        mes = primer_dia_del_mes(inicio)
        while mes <= fin:
            if not any(contrato.cubre(mes) for contrato in contratos):
                return False
            mes = mes_siguiente(mes)
        return True


class Certificate(SoftDeleteModel, FileHandlerMixin):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    subscription = models.ForeignKey(
        ServiceSubscription,
        on_delete=models.CASCADE,
        related_name='certificates',
        verbose_name='Suscripción',
    )
    issued_date = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de emisión')
    pdf = models.FileField(upload_to=pdf_upload_path, verbose_name='Certificado PDF')

    file_fields = ['pdf']

    class Meta:
        verbose_name = 'Certificado'
        verbose_name_plural = 'Certificados'
        ordering = ['-issued_date']
        default_permissions = ()
        permissions = (
            ('view_certificate', 'Ver'),
            ('add_certificate', 'Añadir'),
            ('change_certificate', 'Editar'),
            ('delete_certificate', 'Eliminar'),
        )

    def __str__(self):
        service = self.subscription.service
        customer = self.subscription.customer
        return f'Certificado de {service.title} - {customer.display_name}'
