import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.db.models import BooleanField, Case, OuterRef, Q, Subquery, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, UpdateView, View

from apps.commercial.emails import enviar_correo_anulacion
from apps.commercial.forms.subscription import CertificateUploadForm, SubscriptionForm
from apps.commercial.models import (
    Certificate,
    Customer,
    Invoice,
    ServiceSubscription,
)
from apps.commercial.notifications import notificar_estado_suscripcion
from apps.core.utils import log_action

logger = logging.getLogger(__name__)


def queryset_de_suscripciones_con_estado(user):
    """Suscripciones con el estado de su última factura, filtradas por rol.

    Compartida por `SubscriptionListView` y `ajax_subscription_status`: el
    endpoint no puede filtrar distinto que la vista de listado, o el polling
    expondría filas ajenas al grupo `Clientes`.

    Sin grupo `Clientes` también entra el titular de un Customer (los correos
    de factura y certificado enlazan al listado), siempre acotado a sus propias
    filas; sin Customer ni grupo es `PermissionDenied`.
    """
    qs = ServiceSubscription.objects.select_related('customer', 'service')
    # El vínculo por línea es el que siempre existe: la factura de un lote o
    # de varios servicios no cuelga de `Invoice.subscription`. Sin esto el
    # cliente ve la suscripción pendiente sin ningún botón de factura.
    latest_invoice = (
        Invoice.objects.for_subscription(OuterRef('pk'))
        .filter(is_cancelled=False)
        .order_by('-issue_date')
    )
    qs = qs.annotate(
        latest_invoice_uuid=Subquery(latest_invoice.values('uuid')[:1]),
        latest_invoice_number=Subquery(latest_invoice.values('number')[:1]),
        # `latest_invoice_pdf_ready` replica `Invoice.pdf_ready` en SQL, y
        # hace falta: la propiedad no se puede usar en el template sin una
        # consulta por fila, y anotar sólo el UUID no alcanza porque una
        # factura con `pdf_status='failed'` igual dejaba botón de descarga
        # apuntando a un archivo que no existe.
        # La condición (`ready` Y pdf no vacío) es la misma de `pdf_ready`;
        # `test_annotation_pdf_ready_coincide_con_la_propiedad` ata las dos
        # versiones para que no diverjan sin que nadie lo note.
        latest_invoice_pdf_ready=Subquery(
            latest_invoice.annotate(
                ready=Case(
                    When(
                        Q(pdf_status=Invoice.PdfStatus.READY) & Q(pdf__isnull=False) & ~Q(pdf=''),
                        then=Value(True),
                    ),
                    default=Value(False),
                    output_field=BooleanField(),
                )
            ).values('ready')[:1]
        ),
    )
    if user.is_superuser or user.is_staff:
        return qs.order_by('-start_date')
    elif user.groups.filter(name='Clientes').exists():
        try:
            customer = user.commercial_customer
            return qs.filter(customer=customer, record_active=True).order_by('-start_date')
        except Customer.DoesNotExist:
            return qs.none()
    # Sin grupo `Clientes`: sólo el titular de un Customer puede entrar. Los
    # correos de factura y certificado enlazan «Ver en línea» a este listado y
    # el grupo no siempre existe con el permiso (los caminos del dashboard lo
    # crean vacío), así que la titularidad —no el grupo— es el criterio. Sigue
    # viendo sólo sus propias suscripciones: mismo filtro que la rama del
    # grupo, con `record_active=True`.
    try:
        customer = user.commercial_customer
    except Customer.DoesNotExist:
        raise PermissionDenied from None
    return qs.filter(customer=customer, record_active=True).order_by('-start_date')


class SubscriptionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = ServiceSubscription
    template_name = 'pages/commercial/subscription/list.html'
    context_object_name = 'objects'
    permission_required = 'commercial.view_subscription'

    def has_permission(self):
        """Staff con el permiso, o el cliente titular de un Customer.

        Los correos de factura y certificado enlazan «Ver en línea» a este
        listado, pero el cliente titular no siempre tiene
        ``commercial.view_subscription``: el grupo ``Clientes`` sólo lo otorga
        si el registro público lo creó primero, y los caminos del dashboard lo
        crean vacío. El alcance no se amplía: `get_queryset` acota al titular
        a sus propias suscripciones.
        """
        if super().has_permission():
            return True
        # `hasattr` es falso sin Customer: el OneToOne inverso lanza
        # RelatedObjectDoesNotExist, que es subclase de AttributeError.
        return hasattr(self.request.user, 'commercial_customer')

    def get_queryset(self):
        return queryset_de_suscripciones_con_estado(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = (
            'Mis Suscripciones' if not self.request.user.is_staff else 'Todas las Suscripciones'
        )
        context['parent'] = 'servicios'
        context['segment'] = 'suscripciones'
        context['is_superuser'] = self.request.user.is_superuser
        context['now'] = timezone.now()
        if self.request.user.is_staff or self.request.user.is_superuser:
            context['btn'] = 'Añadir Suscripción'
            context['url_create'] = reverse_lazy('commercial:suscripcion_create')
        return context


class SubscriptionCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = ServiceSubscription
    form_class = SubscriptionForm
    template_name = 'pages/commercial/subscription/create.html'
    permission_required = 'commercial.add_subscription'
    success_url = reverse_lazy('commercial:suscripcion_list')
    url_redirect = success_url

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Añadir Suscripción'
        context['parent'] = 'servicios'
        context['segment'] = 'suscripciones'
        context['url_list'] = reverse_lazy('commercial:suscripcion_list')
        return context

    def form_valid(self, form):
        form.instance.record_active = True
        form.instance.payment_status = 'requested'
        response = super().form_valid(form)
        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=ADDITION,
            message=(
                f'Suscripción creada para: {self.object.customer.display_name} '
                f'- {self.object.service.title}'
            ),
        )
        messages.success(self.request, 'Suscripción creada con éxito.')
        return response


class SubscriptionUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = ServiceSubscription
    form_class = SubscriptionForm
    template_name = 'pages/commercial/subscription/update.html'
    permission_required = 'commercial.change_subscription'
    success_url = reverse_lazy('commercial:suscripcion_list')
    url_redirect = success_url

    def get_object(self, queryset=None):
        return get_object_or_404(ServiceSubscription, uuid=self.kwargs['uuid'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Editar Suscripción'
        context['parent'] = 'servicios'
        context['segment'] = 'suscripciones'
        context['url_list'] = reverse_lazy('commercial:suscripcion_list')
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            user=self.request.user,
            obj=self.object,
            action_flag=CHANGE,
            message=(
                f'Suscripción actualizada para: {self.object.customer.display_name} '
                f'- {self.object.service.title}'
            ),
        )
        messages.success(self.request, 'Suscripción actualizada con éxito.')
        return response


class ApproveSubscriptionView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = ServiceSubscription
    form_class = CertificateUploadForm
    template_name = 'pages/commercial/subscription/upload_certificate.html'
    permission_required = 'commercial.change_subscription'
    success_url = reverse_lazy('commercial:suscripcion_list')
    url_redirect = success_url

    def get_object(self, queryset=None):
        return get_object_or_404(ServiceSubscription, uuid=self.kwargs['uuid'])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop('instance', None)
        return kwargs

    def form_valid(self, form):
        sub = self.get_object()
        if sub.payment_status != 'pending':
            messages.error(self.request, 'Esta suscripción no está pendiente de pago.')
            return redirect(self.success_url)

        certificate = Certificate(subscription=sub, pdf=form.cleaned_data['pdf'])
        certificate.save()

        sub.payment_status = 'paid'
        sub.save()

        self.send_certificate_email(self.request, sub, certificate)
        notificar_estado_suscripcion(sub, 'paid')

        log_action(
            user=self.request.user,
            obj=sub,
            action_flag=CHANGE,
            message=f'Pago aprobado, certificado {certificate.pk} subido',
        )
        log_action(
            user=self.request.user,
            obj=certificate,
            action_flag=ADDITION,
            message=f'Certificado generado para suscripción {sub.uuid}',
        )

        messages.success(self.request, 'Pago aprobado y certificado enviado.')
        return redirect(self.success_url)

    def send_certificate_email(self, request, subscription, certificate):
        enviar_correo_certificado(subscription, request=request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Aprobar pago y subir certificado'
        context['parent'] = 'servicios'
        context['segment'] = 'suscripciones'
        context['subscription'] = self.get_object()
        context['url_list'] = reverse_lazy('commercial:suscripcion_list')
        return context


class RegenerateInvoiceView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Prepara el formulario para volver a facturar una suscripción.

    Anular la factura anterior, dar de baja su certificado y revertir el estado
    de la suscripción son consecuencias de *tener una factura nueva*, no
    condiciones para abrir el formulario. Haclas en este POST dejaba al cliente
    sin comprobante en cuanto el staff cerraba la pestaña: la factura vieja ya
    estaba anulada y la nueva todavía no existía.

    Por eso acá no se toca ningún registro: sólo se lleva al formulario con la
    suscripción a regenerar y la anulación queda a cargo de
    `InvoiceCreateView.form_valid`, que corre cuando la factura nueva ya existe.
    """

    permission_required = 'commercial.change_subscription'

    def post(self, request, *args, **kwargs):
        subscription = get_object_or_404(ServiceSubscription, uuid=kwargs['uuid'])

        if subscription.payment_status == 'paid':
            messages.error(
                request, 'No se puede regenerar una factura de una suscripción ya pagada.'
            )
            return redirect('commercial:suscripcion_list')

        # Sólo hay algo que reemplazar si hay una factura vigente; una factura ya
        # anulada no se regenera, se vuelve a facturar desde cero.
        invoices = Invoice.objects.for_subscription(subscription).filter(is_cancelled=False)
        if not invoices.exists():
            messages.error(request, 'Esta suscripción no tiene facturas para regenerar.')
            return redirect('commercial:suscripcion_list')

        log_action(
            user=request.user,
            obj=subscription,
            action_flag=CHANGE,
            message='Regeneración preparada: se abrió el formulario sin anular nada',
        )

        messages.info(
            request,
            'Regeneración preparada: la factura anterior se anula sólo cuando confirme la nueva.',
        )
        return redirect(
            f'{reverse("commercial:factura_create")}'
            f'?customer_uuid={subscription.customer.uuid}&regenerar={subscription.uuid}'
        )


class SubscriptionCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Anula (soft delete) una suscripción que todavía no llegó a facturarse.

    Una suscripción no vence por tiempo: su único final es esta anulación. Por eso
    el bloqueo es económico, no temporal — no se puede deshacer un cobro ya
    emitido, así que se rechaza la anulación en cuanto existe una factura
    asociada o el pago fue aprobado. La comprobación vive acá y no en la
    plantilla porque el botón tampoco es la garantía: un POST directo la sortea.
    """

    permission_required = 'commercial.delete_subscription'

    def post(self, request, uuid):
        subscription = get_object_or_404(ServiceSubscription, uuid=uuid)

        if not subscription.record_active:
            messages.warning(request, 'La suscripción ya estaba desactivada.')
            return redirect('commercial:suscripcion_list')

        # `payment_status` cubre el pago aprobado; el ítem de factura cubre el
        # caso de una factura emitida que todavía no se pagó. Sólo cuentan las
        # facturas no anuladas: una factura cancelada ya no compromete un cobro,
        # así que no debe impedir que el cliente retire la solicitud. Se filtra
        # por `invoice__is_cancelled=False` en vez de con un `exclude`, porque la
        # relación con la suscripción vive en el ítem y así una sola consulta
        # resuelve el caso borde del ítem huérfano.
        if (
            subscription.payment_status == 'paid'
            or subscription.invoice_items.filter(invoice__is_cancelled=False).exists()
        ):
            messages.error(
                request,
                'No se puede anular una suscripción con factura generada o pago aprobado.',
            )
            return redirect('commercial:suscripcion_list')

        subscription.delete()

        log_action(
            user=request.user,
            obj=subscription,
            action_flag=DELETION,
            message=(
                f'Suscripción desactivada: {subscription.customer.display_name} '
                f'- {subscription.service.title}'
            ),
        )
        notificar_estado_suscripcion(subscription, 'cancelled')
        # La anulación ya está escrita, que es lo que el operador pidió. Si el
        # encolado falla (Redis caído), el peor caso es que el cliente no reciba
        # el aviso; devolver un 500 sería peor, porque la fila ya cambió.
        try:
            enviar_correo_anulacion(subscription)
        except Exception:
            logger.exception(
                'No se pudo encolar el aviso de anulación de la suscripción %s',
                subscription.uuid,
            )
        messages.success(request, 'Suscripción desactivada con éxito.')
        return redirect('commercial:suscripcion_list')


class SubscriptionHardDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Eliminación física permanente (solo superusuarios)."""

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, uuid):
        subscription = get_object_or_404(ServiceSubscription, uuid=uuid)
        customer_name = subscription.customer.display_name
        service_title = subscription.service.title

        subscription.hard_delete()

        log_action(
            user=request.user,
            obj=subscription,
            action_flag=DELETION,
            message=f'Suscripción eliminada físicamente: {customer_name} - {service_title}',
        )
        messages.success(request, f'Suscripción de {customer_name} eliminada permanentemente.')
        return redirect('commercial:suscripcion_list')


def enviar_correo_certificado(subscription, request=None):
    """
    Envía el certificado de una suscripción por correo.
    Retorna True si se envió correctamente, False si falló o no hay certificado/correo.
    """
    if not subscription.customer.user or not subscription.customer.user.email:
        return False

    certificate = subscription.certificates.first()
    if not certificate:
        return False

    subject = f'Certificado de {subscription.service.title}'
    # La URL sale de `SITE_URL` y NO del `request`: el cliente no está en la red
    # interna, y el host por el que el operador entró no es el público.
    base_url = settings.SITE_URL
    context = {
        'subscription': subscription,
        'index_url': base_url,
        'listado_url': base_url + reverse('commercial:suscripcion_list').lstrip('/'),
        'current_year': timezone.now().year,
    }
    html_content = render_to_string('pages/commercial/emails/certificado.html', context)

    email = EmailMessage(
        subject, html_content, settings.DEFAULT_FROM_EMAIL, [subscription.customer.user.email]
    )
    email.content_subtype = 'html'

    if certificate.pdf:
        with certificate.pdf.storage.open(certificate.pdf.name, 'rb') as f:
            email.attach(
                f'certificado_{certificate.issued_date:%Y-%m-%d}.pdf',
                f.read(),
                'application/pdf',
            )

    try:
        email.send()
        return True
    except Exception as e:
        logger.error(f'Error enviando certificado: {e}')
        return False


class ResendCertificateEmailView(LoginRequiredMixin, View):
    """Reenvía el certificado de una suscripción por correo.

    Acceso: staff con ``commercial.change_subscription`` o el cliente titular
    de la suscripción del certificado (las páginas "Mis Servicios" y "Mis
    Suscripciones" muestran el botón de reenvío). Cualquier otro usuario
    recibe 403.
    """

    def has_permission(self, subscription):
        user = self.request.user
        if user.has_perm('commercial.change_subscription'):
            return True
        if not hasattr(user, 'commercial_customer'):
            return False
        return subscription.customer_id == user.commercial_customer.pk

    def get(self, request, uuid):
        subscription = get_object_or_404(ServiceSubscription, uuid=uuid)

        if not self.has_permission(subscription):
            raise PermissionDenied

        if subscription.payment_status != 'paid':
            messages.error(
                request, 'Solo se pueden reenviar certificados de suscripciones pagadas.'
            )
            return redirect('commercial:suscripcion_list')

        if not subscription.certificates.exists():
            messages.error(request, 'Esta suscripción no tiene certificado.')
            return redirect('commercial:suscripcion_list')

        exito = enviar_correo_certificado(subscription, request=request)
        if exito:
            messages.success(
                request, f'Certificado de {subscription.service.title} reenviado correctamente.'
            )
        else:
            messages.error(request, 'No se pudo reenviar el certificado. Revise los logs.')
        return redirect('commercial:suscripcion_list')


class CertificateHardDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Eliminación física de certificado (solo superusuarios)."""

    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, uuid):
        certificate = get_object_or_404(Certificate, uuid=uuid)
        certificate.hard_delete()
        log_action(
            user=request.user,
            obj=certificate,
            action_flag=DELETION,
            message=f'Certificado {certificate.pk} eliminado físicamente.',
        )
        messages.success(request, 'Certificado eliminado permanentemente.')
        return redirect('commercial:suscripcion_list')
