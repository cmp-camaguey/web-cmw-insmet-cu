import contextlib
import json
import logging
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.html import escape
from django.views.generic import FormView, ListView, View

from apps.commercial.forms.invoice import (
    InvoiceCostAllocationFormSet,
    InvoiceForm,
    InvoiceItemFormSet,
)
from apps.commercial.models import (
    Customer,
    Invoice,
    InvoiceCostAllocation,
    InvoiceItem,
    Service,
    ServiceSubscription,
    meses_de_la_linea,
    rango_desde_meses,
)
from apps.commercial.notifications import notificar_factura_emitida
from apps.commercial.views.invoice_utils import (
    _centro_de_costo_de_codigo,
    _prefill_cost_allocations_from_items,
    centros_de_costo_conocidos,
    enviar_correo_factura,
)
from apps.core.models import CompanySettings
from apps.core.tasks import generate_invoice_pdf_and_email_task
from apps.core.utils import log_action
from apps.core.views import ServeModelFileView

logger = logging.getLogger(__name__)


def meses_de_la_suscripcion(subscription):
    """Los meses que ocupa la suscripción que se va a facturar.

    Es el adaptador de la solicitud: saca la fecha de inicio, la cantidad y la
    categoría del servicio y delega en `meses_de_la_linea`, la misma derivación
    que usa la facturación manual. El período no se elige en ninguno de los dos
    caminos: se deduce de lo que se está cobrando.
    """
    inicio = subscription.start_date
    if inicio is None:
        raise ValidationError(
            f'La suscripción de {subscription.service.title} no tiene fecha de inicio: '
            'no se puede deducir el período que se va a facturar.'
        )
    if timezone.is_naive(inicio):
        inicio = timezone.make_aware(inicio)
    inicio = timezone.localtime(inicio).date()
    return meses_de_la_linea(inicio, subscription.quantity, subscription.service.service_category)


class InvoiceListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Invoice
    template_name = 'pages/commercial/invoice/list.html'
    context_object_name = 'objects'
    permission_required = 'commercial.view_invoice'

    def get_queryset(self):
        # `with_display_status` trae items_total/items_paid anotados para que la
        # columna Estado no dispare una consulta por fila. `customer__user` viene
        # en el select_related porque la columna Cliente muestra `display_name`,
        # que para una persona natural lee los nombres del User.
        #
        # El desglose de líneas lee `subscription.payment_status`/`record_active`
        # y `service.title` de cada línea, así que el `Prefetch` trae las líneas
        # con la suscripción y su servicio ya resueltos: sin el
        # `select_related('subscription__service')` cada línea pagaría sus
        # propias consultas (N+1).
        return (
            Invoice.objects.select_related(
                'customer__user', 'subscription__customer__user', 'subscription__service'
            )
            .prefetch_related(
                Prefetch(
                    'items',
                    queryset=InvoiceItem.objects.select_related('subscription__service'),
                )
            )
            .with_display_status()
            .order_by('-issue_date')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Listado de Facturas'
        context['parent'] = 'facturacion'
        context['segment'] = 'facturas'
        context['btn'] = 'Añadir Factura'
        context['url_create'] = reverse_lazy('commercial:factura_create')
        context['url_list'] = reverse_lazy('commercial:factura_list')
        context['is_superuser'] = self.request.user.is_superuser
        return context


class InvoiceCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = 'pages/commercial/invoice/create.html'
    form_class = InvoiceForm
    permission_required = 'commercial.add_invoice'
    success_url = reverse_lazy('commercial:factura_list')

    def get_initial(self):
        initial = super().get_initial()
        customer_uuid = self.request.GET.get('customer_uuid')
        if not customer_uuid:
            return initial
        with contextlib.suppress(Customer.DoesNotExist):
            initial['customer'] = Customer.objects.get(uuid=customer_uuid)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Añadir Factura'
        context['parent'] = 'facturacion'
        context['segment'] = 'facturas'
        context['url_list'] = reverse_lazy('commercial:factura_list')
        context['items_formset'] = InvoiceItemFormSet(
            prefix='items', form_kwargs=self._items_inicial()
        )
        context['cost_allocations_formset'] = (
            kwargs['cost_allocations_formset']
            if 'cost_allocations_formset' in kwargs
            else self.cost_allocations_formset()
        )
        context['regenerar_sub'] = self.suscripcion_a_regenerar()
        commercial_services = Service.objects.filter(service_type=Service.COMMERCIAL).values(
            'id', 'code', 'title', 'price', 'service_category'
        )
        # The manual lines need `service_category` to tell months from days, and
        # the browser cannot derive the unit from the dates alone.
        #
        # El centro de costo viaja resuelto: es la misma regla del prefijo que usa
        # el resto del sistema (`700501072507005` es `700.50107`), y mandarla lista
        # evita que el JS del template reimplemente la derivación y las dos copias
        # terminen separándose.
        context['commercial_services_json'] = json.dumps(
            [
                {**servicio, 'centro_de_costo': _centro_de_costo_de_codigo(servicio['code'])}
                for servicio in commercial_services
            ],
            cls=DjangoJSONEncoder,
        )
        context['company'] = CompanySettings.get_instance()
        context['centros_de_costo'] = centros_de_costo_conocidos()
        return context

    def _items_inicial(self):
        """Valores por defecto de cada línea manual.

        Va como `form_kwargs` y no como `initial` del formset: `initial` es una
        lista por forma inicial, así que un dict hace que Django cuente esa fila
        como inicial y agrega otra, dejando una línea en blanco.

        El inicio por defecto es un dato de la línea —la fecha del servicio—, no
        del formulario: el período se deduce de estas fechas y de las cantidades,
        así que no hay un rango declarado antes que pueda dejar la fecha afuera.
        """
        return {'initial': {'start_date': self._inicio_de_la_linea().isoformat()}}

    def _inicio_de_la_linea(self):
        """Fecha con la que arranca una línea manual.

        Si el operador vino a regenerar la factura de una suscripción, el servicio
        empezó en la fecha de esa suscripción y no hoy. Si no, hoy: es la regla del
        CMP, y el operador la corrige cuando el servicio empezó antes.
        """
        subscription = self.suscripcion_a_regenerar()
        if subscription is not None and subscription.start_date is not None:
            inicio = subscription.start_date
            if timezone.is_naive(inicio):
                inicio = timezone.make_aware(inicio)
            return timezone.localtime(inicio).date()
        return timezone.localdate()

    def suscripcion_a_regenerar(self):
        """Suscripción que el operador pidió regenerar, si el pedido es válido.

        `regenerar` llega en el GET y vuelve en el POST (el formulario lo
        reenvía en un campo oculto), así que se lee de los dos. Se valida en
        ambos casos: una suscripción inexistente o ya pagada no tiene factura que
        regenerar, y en ese caso el formulario abre normal en vez de prometer
        una anulación que no va a pasar.
        """
        uuid = self.request.GET.get('regenerar') or self.request.POST.get('regenerar')
        if not uuid:
            return None
        subscription = (
            ServiceSubscription.objects.filter(uuid=uuid, record_active=True)
            .select_related('service')
            .first()
        )
        if subscription is None or subscription.payment_status == 'paid':
            return None
        return subscription

    def _save_cost_allocations(self, invoice, cost_formset=None, filas=None):
        """Guarda la imputación de la factura ya creada.

        Acepta dos fuentes, que son las dos formas en que una factura declara su
        reparto:

        - `cost_formset`, el formset que confirmó el operador: es el reparto
          exacto de una factura cuando el lote entero es esa factura, y el de la
          facturación manual.
        - `filas`, filas ya resueltas `[{'codigo', 'porcentaje'}]`: es lo que se
          deriva de los ítems de una factura cuando el lote produce varias, y así
          cada una se imputa con sus propios centros.

        Se persiste a mano y no con `formset.save()` porque en este punto la
        factura existe recién, y un formset atado a la línea no puede resolver la
        FK hacia algo que todavía no estaba en la base cuando se validó. Son de
        dos a tres filas: no vale la pena el indirecto de un inline formset para
        eso. `full_clean` va explícito porque `Model.save()` no valida, y la regla
        del porcentaje vive en el `clean()` del modelo.
        """
        if filas is None:
            filas = self._filas_del_formset(cost_formset)
        for fila in filas:
            registro = InvoiceCostAllocation(
                invoice=invoice,
                codigo=fila['codigo'],
                porcentaje=fila['porcentaje'],
            )
            registro.full_clean()
            registro.save()

    def _filas_del_formset(self, cost_formset):
        """Filas vivas del formset, ya normalizadas a `{'codigo', 'porcentaje'}`.

        Se salta las formas sin `cleaned_data` (las que no se validaron) y las
        marcadas `DELETE`. Sin formset no hay filas. Es el mismo recorrido que
        hacía la persistencia, extraído para que las filas derivadas de los ítems
        y las que confirmó el operador se guarden por un único camino.
        """
        if cost_formset is None:
            return []
        filas = []
        for form in cost_formset.forms:
            if not hasattr(form, 'cleaned_data') or form.cleaned_data.get('DELETE'):
                continue
            filas.append(
                {
                    'codigo': form.cleaned_data['codigo'],
                    'porcentaje': form.cleaned_data['porcentaje'],
                }
            )
        return filas

    def cost_allocations_formset(self):
        """Formset de imputación a centros de costo.

        En el GET se precarga desde los servicios del cliente cuando ya se sabe
        cuál es; sin cliente no hay códigos de los que deducir un centro.

        El `queryset` vacío es obligatorio: las filas de esta pantalla son nuevas
        hasta que el POST las crea, y un `modelformset` sin acotar cae al manager
        del modelo, con lo que `initial_form_count()` pasa a ser
        `len(get_queryset())` y la pantalla abre con TODAS las imputaciones de la
        base como filas editables (más la fila en blanco de `extra=1`). Si esas
        filas suman 100, la factura se imputa al centro de otro cliente en
        silencio; si no suman 100, el POST se rechaza sin explicar de dónde salen.
        """
        prefix = 'cost_allocations'
        # Las imputaciones de la factura que se está creando todavía no existen:
        # no hay ninguna fila previa que editar.
        existing = InvoiceCostAllocation.objects.none()
        if self.request.method == 'POST':
            # El GET de `?regenerar=` precarga una fila y un modelformset cuenta
            # las filas de `initial` como *existentes*: `INITIAL_FORMS` viaja
            # desde la pantalla con ese valor y hace obligatorio el `id` de la
            # fila 0. Las imputaciones son filas nuevas de una factura que
            # todavía no existe, así que no hay `id` que mandar y el POST se
            # rechazaría por un campo que el operador nunca vio. Se normaliza el
            # management form a cero antes de validar.
            data = self.request.POST.copy()
            data[f'{prefix}-INITIAL_FORMS'] = '0'
            return InvoiceCostAllocationFormSet(data, prefix=prefix, queryset=existing)
        return InvoiceCostAllocationFormSet(
            prefix=prefix, queryset=existing, initial=self._prefill_inicial()
        )

    def _prefill_inicial(self):
        subscription = self.suscripcion_a_regenerar()
        if subscription is None:
            return []
        return _prefill_cost_allocations_from_items(
            [InvoiceItem(codigo=subscription.service.code or '')]
        )

    def _motivo_rechazo_imputacion(self, cost_formset):
        """Primer error real de la imputación, como texto plano y con fila y campo.

        Se arma desde las cadenas de cada `errors`, nunca desde `str(errors)`: el
        `ErrorList` se renderiza como HTML (`<ul class="errorlist">`) y ese markup
        no tiene nada que hacer dentro del mensaje flash. Un rechazo que sólo
        repite el texto genérico deja al operador sin saber qué corregir.
        """
        for indice, form in enumerate(cost_formset.forms):
            for campo, errores in form.errors.items():
                for error in errores:
                    return f'Fila {indice + 1} · {campo}: {error}'
        for error in cost_formset.non_form_errors():
            return str(error)
        return 'revisa el reparto entre centros de costo.'

    def form_valid(self, form):
        customer = form.cleaned_data['customer']
        commercial_registry = form.cleaned_data['commercial_registry']
        subscriptions = form.cleaned_data.get('subscriptions')
        regenerar = self.suscripcion_a_regenerar()

        # La imputación se valida antes de crear la factura: si el reparto no
        # suma 100 % no debe quedar una factura a medias en la base, y el error
        # tiene que volver al formulario en vez de perderse en un redirect.
        cost_formset = self.cost_allocations_formset()
        if not cost_formset.is_valid():
            messages.error(
                self.request,
                'Corrige el reparto entre centros de costo de la factura: '
                f'{self._motivo_rechazo_imputacion(cost_formset)}',
            )
            return self.render_to_response(
                self.get_context_data(form=form, cost_allocations_formset=cost_formset)
            )

        if subscriptions and subscriptions.exists():
            # El período sale de cada suscripción y las que no coinciden se
            # facturan en facturas separadas: un lote puede abarcar mayo y junio y
            # también un pronóstico de marzo, y meterlos en una sola factura
            # obligaría a un período que ninguno de los dos ocupa.
            try:
                nuevas = self.process_batch_invoice(
                    customer,
                    commercial_registry,
                    subscriptions,
                    cost_formset,
                )
            except ValidationError as error:
                # La regla del contrato vive en el modelo; el lote la dispara y
                # tiene que volver al formulario, no convertirse en un 500.
                messages.error(self.request, ' '.join(error.messages))
                return self.render_to_response(
                    self.get_context_data(form=form, cost_allocations_formset=cost_formset)
                )
            # El aviso nombra cada factura con su propio período, que es lo que va
            # a imprimir el PDF: con varias facturas por lote un único período
            # sería falso para las que no lo comparten.
            resumen = ', '.join(f'{factura.number} ({factura.period_label})' for factura in nuevas)
            messages.success(
                self.request,
                f'Se generaron las facturas: {resumen}.',
            )
            # Después de crear, nunca antes: la factura anterior sólo se anula
            # cuando su reemplazo ya existe.
            self.anular_factura_previa(regenerar, subscriptions, nuevas)
            return redirect(self.success_url)
        else:
            # Facturación manual: crea suscripciones nuevas, así que la
            # suscripción regenerada no está en esta factura y no hay nada que
            # reemplazar. Se avisa en vez de anular a ciegas.
            self.anular_factura_previa(regenerar, subscriptions)
            return self.process_manual_invoice(form, customer, commercial_registry, cost_formset)

    def anular_factura_previa(self, regenerar, subscriptions, nuevas_facturas=None):
        """Anula la factura que la nueva reemplaza, sólo si la nueva la reemplaza.

        Va después de crear la factura nueva: si algo falla antes, la factura
        anterior sigue viva. Sin `transaction.atomic` porque la tarea de PDF se
        encola adentro y el worker podría renderizar contra filas sin confirmar.
        """
        if regenerar is None:
            return
        incluida = subscriptions and subscriptions.filter(pk=regenerar.pk).exists()
        if not incluida:
            messages.warning(
                self.request,
                'La suscripción a regenerar no se facturó en esta factura: su factura '
                'anterior no se anuló y su certificado sigue vigente.',
            )
            return

        # `Invoice.subscription` es un ancla opcional, así que `for_subscription`
        # cubre las facturas de lote por el vínculo de línea. Las nuevas se
        # excluyen explícitamente: en el caso de un solo período la factura
        # recién creada también cuelga de la suscripción.
        nuevas_ids = [invoice.pk for invoice in nuevas_facturas or []]
        anteriores = (
            Invoice.objects.for_subscription(regenerar)
            .exclude(pk__in=nuevas_ids)
            .filter(is_cancelled=False)
        )
        numeros = []
        for invoice in anteriores:
            invoice.is_cancelled = True
            invoice.save(update_fields=['is_cancelled'])
            numeros.append(invoice.number)
            log_action(
                user=self.request.user,
                obj=invoice,
                action_flag=CHANGE,
                message=f'Factura {invoice.number} anulada al confirmar la factura nueva',
                request=self.request,
            )

        # `Certificate` es `SoftDeleteModel` y `QuerySet.delete()` ignora su
        # `delete()`: borra la fila de verdad y deja el PDF huérfano en `media/`.
        # Por eso se da de baja uno por uno: baja lógica (la trazabilidad del
        # certificado emitido se conserva) y `_cleanup_files()` borra el archivo,
        # que es lo que corresponde a un documento que ya no vale. `hard_delete()`
        # además sería peor: el certificado se le entregó al cliente.
        certificados = regenerar.certificates.filter(record_active=True)
        dados_de_baja = 0
        for certificado in certificados:
            certificado.delete()
            dados_de_baja += 1
        if dados_de_baja:
            log_action(
                user=self.request.user,
                obj=regenerar,
                action_flag=CHANGE,
                message=(
                    f'{dados_de_baja} certificado(s) dado(s) de baja al confirmar la factura nueva'
                ),
                request=self.request,
            )

        if numeros:
            messages.success(
                self.request,
                f'Factura anterior anulada ({", ".join(numeros)}) al confirmar la nueva.',
            )

    def anchor_invoice(self, invoice, subscriptions):
        """Cuelga la factura de la suscripción cuando hay una sola.

        Con varias queda en NULL y el vínculo real está en cada línea.
        """
        unique = {sub.pk: sub for sub in subscriptions}
        if len(unique) == 1:
            invoice.subscription = next(iter(unique.values()))
            invoice.save(update_fields=['subscription'])
        return invoice

    def process_batch_invoice(
        self,
        customer,
        commercial_registry,
        subscriptions,
        cost_formset=None,
    ):
        # Comportamiento alineado con facturación manual: todas las
        # suscripciones seleccionadas van en UNA sola factura, con el período
        # unificado (unión de meses de todas las líneas). El usuario decide qué
        # marcar y todo lo marcado se factura junto.
        meses_union = set()
        grupo = list(subscriptions)
        for sub in grupo:
            meses_union |= set(meses_de_la_suscripcion(sub))
        meses = tuple(sorted(meses_union))

        facturas = [
            self._facturar_grupo(
                customer,
                meses,
                commercial_registry,
                grupo,
                cost_formset,
                imputar_por_items=False,
            )
        ]

        site_url = settings.SITE_URL
        for factura in facturas:
            generate_invoice_pdf_and_email_task(str(factura.uuid), site_url)
            notificar_factura_emitida(factura)
        return facturas

    def _facturar_grupo(
        self,
        customer,
        meses,
        commercial_registry,
        subscriptions,
        cost_formset,
        imputar_por_items=False,
    ):
        """Crea la factura de un período del lote.

        `cost_formset` es el reparto que confirmó el operador y sólo se guarda
        tal cual cuando esta factura es todo el lote. `imputar_por_items` marca
        el caso de un lote con varios períodos: cada factura se imputa con los
        centros derivados de sus propios ítems, porque el reparto global le
        atribuiría a una factura centros de servicios que no factura.
        """
        with transaction.atomic():
            start, end = rango_desde_meses(meses)
            invoice = Invoice(
                subscription=None,
                customer=customer,
                amount=0,
                number=self.generate_invoice_number(),
                period_start=start,
                period_end=end,
            )
            # Antes de crear la fila: si la jurídica no tiene contrato que cubra
            # el período, la transacción se deshace sin dejar factura ni ítems.
            invoice.validar_contrato()
            invoice.save()

            total = 0
            items = []
            for sub in subscriptions:
                price = sub.service.price or 0
                quantity = sub.quantity
                amount = price * quantity
                # `unidad_medida` no se pasa: lo pone el `default='U'` del modelo, que
                # es lo que imprimen las facturas reales. `descripcion` es el título
                # tal cual: el "por meses" de la 279 es parte del nombre en el catálogo.
                item = InvoiceItem.objects.create(
                    invoice=invoice,
                    subscription=sub,
                    codigo=sub.service.code or '',
                    descripcion=sub.service.title,
                    cantidad=quantity,
                    precio=price,
                    importe=amount,
                )
                items.append(item)
                total += amount

                # La suscripción conserva la fecha de inicio que eligió quien la
                # solicitó: es un dato del contrato con el cliente, no del periodo
                # facturado, y sobreescribirlo lo convertía en un valor derivado de
                # la factura. Al facturar sólo cambia el estado de pago. El periodo
                # facturado es de la factura, no de la suscripción: las
                # suscripciones no vencen por tiempo.
                sub.payment_status = 'pending'
                sub.save()

                log_action(
                    user=self.request.user,
                    obj=sub,
                    action_flag=CHANGE,
                    message=(
                        f'Suscripción facturada en lote: {sub.service.title} '
                        f'para {sub.customer.display_name}'
                    ),
                )

            invoice.amount = total
            invoice.save()
            self.anchor_invoice(invoice, subscriptions)

            log_action(
                user=self.request.user,
                obj=invoice,
                action_flag=ADDITION,
                message=(
                    f'Factura por lote {invoice.number} - Cliente: '
                    f'{customer.display_name} - Monto: ${total:.2f}'
                ),
                request=self.request,
            )

            # Las asignaciones se guardan antes del task: el PDF se genera aparte,
            # y si no estuvieran ya en la base saldría sin imputación.
            if imputar_por_items:
                # Sin centros derivables la lista viene vacía y no se inventa
                # ninguna fila: la factura queda sin imputación.
                self._save_cost_allocations(
                    invoice, filas=_prefill_cost_allocations_from_items(items)
                )
            else:
                self._save_cost_allocations(invoice, cost_formset)
        return invoice

    def process_manual_invoice(self, form, customer, commercial_registry, cost_formset=None):
        """Crea la factura a mano, una suscripción nueva por línea.

        El inicio sale de cada línea: el contrato con el cliente empieza cuando
        él empezó a usar el servicio, y eso puede diferir entre líneas. El período
        también sale de las líneas —la unión de los meses que ocupa cada una—, así
        que la factura se guarda entera o no se guarda.
        """
        items_formset = InvoiceItemFormSet(self.request.POST, prefix='items')
        if not items_formset.is_valid():
            messages.error(self.request, 'Corrige los errores en las líneas de factura.')
            context = self.get_context_data(form=form)
            context['items_formset'] = items_formset
            return self.render_to_response(context)

        try:
            invoice, total, created_subs = self._guardar_factura_manual(
                customer, items_formset, commercial_registry
            )
        except ValidationError as error:
            messages.error(self.request, ' '.join(error.messages))
            context = self.get_context_data(form=form)
            context['items_formset'] = items_formset
            return self.render_to_response(context)

        # Fuera de la transacción: el worker del PDF no debe renderizar contra
        # filas sin confirmar.
        self._save_cost_allocations(invoice, cost_formset)
        # La URL del correo sale de `SITE_URL`, nunca del request: el cliente
        # no está en la red interna, y el host por el que entró el operador
        # no es el que el destinatario alcanza.
        site_url = settings.SITE_URL
        generate_invoice_pdf_and_email_task(str(invoice.uuid), site_url)
        notificar_factura_emitida(invoice)
        messages.success(
            self.request,
            f'Factura manual generada del período {invoice.period_label} '
            '(con suscripciones creadas).',
        )
        return redirect(self.success_url)

    def _guardar_factura_manual(self, customer, items_formset, commercial_registry):
        """Escribe la factura manual dentro de una transacción.

        El período no se declara: se deduce de las líneas. Cada una aporta los
        meses que ocupa —su inicio y su cantidad— y la factura cubre la unión de
        todos, así que el rango impreso nunca es más ancho que lo que se cobra ni
        puede dejar una línea afuera.

        Las líneas se validan al guardarse, contra ese período: si una quedara
        afuera, `transaction.atomic` deshace también la suscripción que se había
        creado para ella, así que no queda una suscripción cobrada sin factura que
        la respalde.
        """
        filas = [
            item_form.cleaned_data
            for item_form in items_formset
            if item_form.cleaned_data and not item_form.cleaned_data.get('DELETE', False)
        ]
        if not filas:
            raise ValidationError(
                'La factura necesita al menos una línea: de las líneas sale el período '
                'que se va a facturar.'
            )

        meses = {
            mes
            for fila in filas
            for mes in meses_de_la_linea(
                fila['start_date'], fila['cantidad'], fila['service'].service_category
            )
        }
        start, end = rango_desde_meses(meses)

        with transaction.atomic():
            invoice = Invoice(
                subscription=None,
                customer=customer,
                amount=0,
                number=self.generate_invoice_number(),
                period_start=start,
                period_end=end,
            )
            # El período ya está resuelto: si el contrato de la jurídica no lo
            # cubre se rechaza acá, y `transaction.atomic` deshace también las
            # suscripciones que se hubieran creado para las líneas.
            invoice.validar_contrato()
            invoice.save()

            total = 0
            items = []
            created_subs = []

            for cd in filas:
                service = cd['service']
                cantidad = cd['cantidad']

                sub = ServiceSubscription.objects.create(
                    customer=customer,
                    service=service,
                    start_date=timezone.make_aware(
                        datetime.combine(cd['start_date'], datetime.min.time())
                    ),
                    payment_status='pending',
                    record_active=True,
                    quantity=cantidad,
                )

                created_subs.append(sub)

                item = InvoiceItem.objects.create(
                    invoice=invoice,
                    subscription=sub,
                    codigo=cd.get('codigo', service.code or ''),
                    descripcion=service.title,
                    cantidad=cantidad,
                    # Sin `unidad_medida`: la deja el `default='U'` del modelo, que es
                    # lo que dicen las facturas reales del CMP. La unidad de la fila
                    # la nombra el rótulo de `cantidad` —"Cantidad de meses" o
                    # "Cantidad de días"—, y `descripcion` va sin sufijo porque el
                    # "por meses" de la 279 es parte del título del catálogo y las
                    # facturas de pronóstico no lo llevan.
                    precio=cd['precio'],
                    importe=cantidad * cd['precio'],
                )
                items.append(item)
                total += cantidad * cd['precio']

                log_action(
                    user=self.request.user,
                    obj=sub,
                    action_flag=ADDITION,
                    message=(
                        f'Suscripción creada manualmente: {customer.display_name} - {service.title}'
                    ),
                )

            invoice.amount = total
            invoice.save()
            self.anchor_invoice(invoice, created_subs)

            log_action(
                user=self.request.user,
                obj=invoice,
                action_flag=ADDITION,
                message=(
                    f'Factura manual {invoice.number} - Cliente: '
                    f'{customer.display_name} - Monto: ${total:.2f}'
                ),
                request=self.request,
            )
        return invoice, total, created_subs

    def generate_invoice_number(self):
        year = timezone.now().year
        last = Invoice.objects.filter(issue_date__year=year).order_by('-issue_date').first()
        if last and last.number:
            try:
                num = int(last.number.split('-')[-1])
                return f'{year}-{num + 1:04d}'
            except ValueError, IndexError:
                pass
        return f'{year}-0001'


class InvoicePDFDownloadView(ServeModelFileView):
    model = Invoice
    field = 'pdf'
    permission_required = 'commercial.view_invoice'

    def get_queryset(self):
        return super().get_queryset().select_related('subscription')

    def get_permission_required(self, obj):
        """Staff con permiso, o el cliente titular de la factura.

        Sin esto sus botones Ver PDF/Descargar devuelven 403: no tienen
        ``commercial.view_invoice``.
        """
        user = self.request.user
        if not hasattr(user, 'commercial_customer'):
            return self.permission_required
        customer = user.commercial_customer
        if obj.customer_id == customer.pk:
            return None
        if obj.subscription_id and obj.subscription.customer_id == customer.pk:
            return None
        return self.permission_required

    def get_filename(self, obj):
        return f'factura_{obj.number}_{obj.issue_date:%Y-%m-%d}.pdf'

    def get(self, request, uuid):
        invoice = self.get_object()
        perm = self.get_permission_required(invoice)
        if perm and not request.user.has_perm(perm):
            raise PermissionDenied
        if not getattr(invoice, self.field):
            # El PDF se genera por una tarea Huey; si no existe (p. ej. el
            # worker no corre en dev) lo generamos bajo demanda para no
            # ocultar los botones de Descargar / Ver PDF en el listado.
            self._generate_pdf_if_missing(invoice)
        return super().get(request, uuid)

    def _generate_pdf_if_missing(self, invoice):
        from apps.commercial.views.invoice_utils import generate_invoice_pdf_standalone

        customer = invoice.customer
        items = list(invoice.items.all())
        # El período sale de `invoice.period_label` con respaldo en `issue_date`;
        # la regeneración bajo demanda usa exactamente el mismo camino que la
        # tarea asíncrona, así que las dos imprimen el mismo período.
        try:
            generate_invoice_pdf_standalone(invoice, customer, items)
            invoice.refresh_from_db(fields=[self.field])
        except Exception as exc:  # pragma: no cover - depends on WeasyPrint/Pango
            logger.exception(
                'Fallo la generación bajo demanda del PDF de la factura %s: %s',
                invoice.uuid,
                exc,
            )


def _liberar_suscripciones_de_factura(invoice, user, motivo):
    """Devuelve a 'solicitado' las suscripciones que la factura dejó pendientes.

    Anular y borrar son las dos formas de deshacer una factura, así que
    comparten el criterio: sólo vuelven a 'solicitado' las que están en
    'pending'/'requested'; una `paid` ya está cobrada y no se toca. Está en un
    solo lugar para que los dos caminos no puedan divergir.

    Se lee antes de cualquier borrado: el borrado duro se lleva los ítems y con
    ellos el vínculo hacia la suscripción.
    """
    items_con_suscripcion = invoice.items.filter(subscription__isnull=False).select_related(
        'subscription'
    )
    for item in items_con_suscripcion:
        sub = item.subscription
        if sub.payment_status in ['pending', 'requested']:
            sub.payment_status = 'requested'
            sub.save()
            log_action(
                user=user,
                obj=sub,
                action_flag=CHANGE,
                message=(
                    f"Estado revertido a 'solicitado' por {motivo} de factura {invoice.number}"
                ),
            )


class CancelInvoiceView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'commercial.delete_invoice'

    def post(self, request, uuid):
        invoice = get_object_or_404(Invoice, uuid=uuid)

        if invoice.is_cancelled:
            messages.warning(request, f'La factura {invoice.number} ya estaba anulada.')
            return redirect('commercial:factura_list')

        invoice.is_cancelled = True
        invoice.save()

        _liberar_suscripciones_de_factura(invoice, request.user, 'anulación')

        log_action(
            user=request.user,
            obj=invoice,
            action_flag=CHANGE,
            message=f'Factura {invoice.number} anulada',
        )
        messages.success(request, f'Factura {invoice.number} anulada correctamente.')
        return redirect('commercial:factura_list')


class RetryInvoicePdfView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Vuelve a encolar el render del PDF sin reenviar el correo.

    El PDF va en una tarea Huey asíncrona, así que puede quedar faltando sin que
    nada lo impida. Reencolar sólo el PDF es idempotente y no duplica el correo
    si el de la factura ya salió.
    """

    permission_required = 'commercial.change_invoice'

    def post(self, request, uuid):
        invoice = get_object_or_404(Invoice, uuid=uuid)

        if invoice.pdf_ready:
            messages.warning(request, f'La factura {invoice.number} ya tiene su PDF.')
            return redirect('commercial:factura_list')
        if invoice.is_cancelled:
            messages.warning(
                request, f'No se genera el PDF de una factura anulada ({invoice.number}).'
            )
            return redirect('commercial:factura_list')

        # La URL del correo sale de `SITE_URL`, nunca del request (ver arriba).
        site_url = settings.SITE_URL
        generate_invoice_pdf_and_email_task(str(invoice.uuid), site_url, solo_paso='pdf')
        log_action(
            user=request.user,
            obj=invoice,
            action_flag=CHANGE,
            message=f'Reencolado el PDF de la factura {invoice.number}',
            request=request,
        )
        messages.success(
            request,
            f'Generación del PDF de la factura {invoice.number} reencolada.',
        )
        return redirect('commercial:factura_list')


class InvoiceHardDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, uuid):
        invoice = get_object_or_404(Invoice, uuid=uuid)
        invoice_number = invoice.number
        log_action(
            user=request.user,
            obj=invoice,
            action_flag=DELETION,
            message=f'Factura {invoice.number} eliminada permanentemente',
        )
        # Borrar es la otra forma de deshacer una factura: libera las
        # suscripciones con el mismo criterio que anular, antes del borrado
        # físico que se lleva sus ítems. Si no, la solicitud queda en `pending`,
        # desaparece del listado del lote y no hay forma de volver a facturarla.
        _liberar_suscripciones_de_factura(invoice, request.user, 'eliminación')
        invoice.hard_delete()
        messages.success(request, f'Factura {invoice_number} eliminada permanentemente.')
        return redirect('commercial:factura_list')


class ResendInvoiceEmailView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'commercial.change_invoice'

    def get(self, request, uuid):
        invoice = get_object_or_404(Invoice, uuid=uuid)

        customer = None
        if invoice.subscription:
            customer = invoice.subscription.customer
        elif invoice.customer:
            customer = invoice.customer
        else:
            first_item = invoice.items.first()
            if first_item and first_item.subscription:
                customer = first_item.subscription.customer

        if not customer or not customer.user or not customer.user.email:
            messages.error(
                request, 'No se pudo determinar el cliente o no tiene correo electrónico.'
            )
            return redirect('commercial:factura_list')

        exito = enviar_correo_factura(invoice, customer, request=request)
        if exito:
            messages.success(
                request, f'Correo de la factura {invoice.number} reenviado correctamente.'
            )
        else:
            messages.error(
                request,
                f'No se pudo reenviar el correo de la factura {invoice.number}. Revise los logs.',
            )
        return redirect('commercial:factura_list')


@login_required
@permission_required('commercial.view_subscription')
def ajax_pending_subscriptions(request):
    customer_id = request.GET.get('customer')
    if not customer_id:
        return HttpResponse('')
    # El lote sólo ofrece lo que todavía no se facturó: una suscripción `pending`
    # ya tiene factura vigente y ofrecerla acá invita a facturarla dos veces. La
    # excepción es la que el operador vino a *regenerar*: `regenerar` sólo
    # aplica a una `pending` (su factura existe), y si desapareciera de la lista
    # no habría casilla que marcar y el POST caería en facturación manual
    # avisando que la factura anterior no se anuló.
    filtro = Q(payment_status='requested')
    regenerar_uuid = request.GET.get('regenerar')
    if regenerar_uuid:
        with contextlib.suppress(ValueError):
            filtro |= Q(uuid=UUID(regenerar_uuid), payment_status='pending')
    qs = ServiceSubscription.objects.filter(
        filtro, customer_id=customer_id, record_active=True
    ).select_related('service')
    if not request.user.is_staff and not request.user.is_superuser:
        qs = qs.filter(customer__user=request.user)
    subs = qs
    if not subs.exists():
        return HttpResponse('<p class="text-muted">No hay solicitudes por facturar.</p>')
    html = ''
    for sub in subs:
        title = escape(sub.service.title or '')
        summary = escape(sub.service.summary or '')
        # El período del contrato se muestra, no se edita: es un dato que la
        # suscripción ya tiene (su inicio y la cantidad que multiplica al
        # precio) y que la pantalla ocultaba. La unidad sale de
        # `get_quantity_period_display()`, que ya la deriva de la categoría del
        # servicio, así que no hay dos lugares que puedan discrepar.
        #
        # La fecha va con año: `desde 15/05` de una suscripción del año pasado se
        # lee como del año en curso, que es justo la confusión que el rediseño
        # viene a cerrar.
        unidad = sub.get_quantity_period_display()
        inicio = sub.start_date.strftime('%d/%m/%Y') if sub.start_date else 'sin fecha'
        periodo = escape(f'desde {inicio} · {unidad}')
        # El centro de costo se resuelve acá, una sola vez: el navegador lo
        # recibe listo en `data-centro` y no reimplementa la regla del prefijo
        # (`700501072507005` es `700.50107`), que así queda escrita en un solo
        # lugar. Un código sin centro derivable viaja vacío: no se inventa ninguno.
        #
        # El periodo facturado, en cambio, no se manda: lo fija el operador en
        # el formulario. La suscripción no aporta ninguna fecha para escribirlo.
        code = escape(sub.service.code or '')
        centro = escape(_centro_de_costo_de_codigo(sub.service.code) or '')
        html += f'''
        <div class="form-check">
          <input class="form-check-input subscription-check" type="checkbox"
                 name="subscriptions" value="{sub.pk}"
                 id="sub_{sub.pk}"
                 data-quantity="{sub.quantity}"
                 data-periodo="{periodo}"
                 data-code="{code}"
                 data-centro="{centro}"
                 data-service="{title}" data-summary="{summary}">
          <label class="form-check-label" for="sub_{sub.pk}">
            <strong>{title}</strong>
            <br><span class="text-muted">{periodo}</span>
            <br><small class="text-muted">{summary}</small>
          </label>
        </div>
        '''
    return HttpResponse(html)
