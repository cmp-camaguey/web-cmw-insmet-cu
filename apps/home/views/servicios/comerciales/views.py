from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, OuterRef, Subquery, When
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView, ListView

from apps.commercial.forms import PaymentMethodForm
from apps.commercial.models import Customer, Invoice, Service, ServiceSubscription
from apps.commercial.notifications import notificar_solicitud_suscripcion


class CommercialServicesListView(LoginRequiredMixin, ListView):
    model = ServiceSubscription
    template_name = 'pages/home/services/commercial.html'
    context_object_name = 'subscriptions'
    paginate_by = 10

    STATUS_RIBBONS = {
        'pagado': 'bg-green',
        'pendiente': 'bg-orange',
        'solicitado': 'bg-blue',
        'cancelada': 'bg-red',
    }

    def get_queryset(self):
        try:
            customer = self.request.user.commercial_customer
        except Customer.DoesNotExist:
            return ServiceSubscription.objects.none()
        # `for_subscription` cubre el ancla de la factura y el vínculo por línea; las
        # anuladas no se ofrecen porque no hay nada que pagar.
        latest_invoice = (
            Invoice.objects.for_subscription(OuterRef('pk'))
            .filter(is_cancelled=False)
            .order_by('-issue_date')
        )
        return (
            ServiceSubscription.objects.filter(customer=customer, record_active=True)
            .select_related('service', 'service__user')
            .annotate(latest_invoice_uuid=Subquery(latest_invoice.values('uuid')[:1]))
            .order_by(
                Case(
                    When(payment_status='requested', then=0),
                    When(payment_status='pending', then=1),
                    When(payment_status='paid', then=2),
                    default=3,
                    output_field=IntegerField(),
                ),
                '-start_date',
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Mis Servicios'
        context['parent'] = 'servicios'
        context['segment'] = 'comerciales'
        context['status_ribbon'] = self.STATUS_RIBBONS
        for sub in context.get('subscriptions', []):
            sub.total_to_pay = (sub.service.price or 0) * (sub.quantity or 1)
        return context


class PublicCommercialServicesListView(ListView):
    model = Service
    template_name = 'pages/home/services/commercial_public.html'
    context_object_name = 'services'
    paginate_by = 10

    def get_queryset(self):
        return Service.objects.filter(service_type=Service.COMMERCIAL, record_active=True).order_by(
            'title'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Comerciales'
        context['parent'] = 'servicios'
        context['segment'] = 'comercial_p'
        return context


class ServiceDetailView(LoginRequiredMixin, FormView):
    template_name = 'pages/home/services/service_detail.html'
    form_class = PaymentMethodForm
    success_url = reverse_lazy('commercial:suscripcion_list')

    def dispatch(self, request, *args, **kwargs):
        self.service = get_object_or_404(
            Service,
            uuid=kwargs['uuid'],
            service_type=Service.COMMERCIAL,
            record_active=True,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['service'] = self.service
        context['title'] = self.service.title
        context['parent'] = 'servicios'
        context['segment'] = 'comerciales'
        # El precio crudo se expone vía context['service'].price; el template
        # compone el formato con el filtro format_cup.
        related_qs = (
            Service.objects.filter(
                service_type=Service.COMMERCIAL,
                service_category=self.service.service_category,
                record_active=True,
            )
            .exclude(uuid=self.service.uuid)
            .order_by('title')
        )
        paginator = Paginator(related_qs, 3)
        page_number = self.request.GET.get('related_page', 1)
        related_page = paginator.get_page(page_number)
        context['related_services'] = related_page
        context['related_page_obj'] = related_page

        if self.request.user.is_authenticated and hasattr(self.request.user, 'commercial_customer'):
            customer = self.request.user.commercial_customer
            # Sin `record_active` una suscripción anulada (baja lógica) seguía
            # apareciendo como activa: la baja no borra la fila, la marca.
            subs = ServiceSubscription.objects.filter(
                customer=customer,
                service=self.service,
                record_active=True,
            )
            in_flight = (
                subs.filter(payment_status__in=['requested', 'pending'])
                .order_by('-start_date')
                .first()
            )
            active = subs.filter(payment_status='paid').order_by('-start_date').first()
            context['in_flight_subscription'] = in_flight
            context['active_subscription'] = active
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['billing_period'] = self.service.get_billing_period_display()
        return kwargs

    def post(self, request, *args, **kwargs):
        if not (request.user.is_authenticated and hasattr(request.user, 'commercial_customer')):
            messages.error(request, 'Debes ser un cliente registrado para solicitar servicios.')
            login_url = reverse('user_auth:login')
            return redirect(f'{login_url}?next={quote(request.path)}')
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        customer = self.request.user.commercial_customer
        start_date = form.cleaned_data['start_date']
        quantity = form.cleaned_data['quantity']

        # Sin `end_date`: la suscripción no vence, se queda viva hasta que se
        # cancele. La cantidad es sólo el factor que multiplica al precio.
        subscription = ServiceSubscription.objects.create(
            customer=customer,
            service=self.service,
            start_date=start_date,
            quantity=quantity,
            payment_status='requested',
            payment_method=form.cleaned_data['payment_method'],
        )
        # La venta queda esperando una persona: sin este aviso, la solicitud
        # solo existe si alguien abre la lista de suscripciones por casualidad.
        notificar_solicitud_suscripcion(subscription)
        messages.success(self.request, 'Solicitud enviada. El staff generará una factura.')
        return redirect(self.success_url)
