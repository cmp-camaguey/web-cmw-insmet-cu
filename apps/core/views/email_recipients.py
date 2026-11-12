import logging

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView, View

from apps.core.forms.email_recipients import EmailRecipientFormSet, EmailRecipientListForm
from apps.core.models import EmailRecipientList, Subscription
from apps.core.utils import log_action

# Quién recibe de verdad un envío: la misma condición que `apps.core.audiences`.
# La pantalla de listas la usa para no anunciar un alcance que el envío no tiene.
_REACHABLE = Q(
    subscriptions__subscriber__active=True,
    subscriptions__subscriber__confirmed_at__isnull=False,
)

logger = logging.getLogger(__name__)


def _discard_empty_recipient_forms(formset):
    for form in formset.forms:
        if form in formset.deleted_forms:
            continue
        if not form.cleaned_data.get('email'):
            form.cleaned_data['DELETE'] = True


class EmailRecipientListListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    template_name = 'pages/core/email_recipient/list.html'
    model = EmailRecipientList
    permission_required = 'core.view_emailrecipientlist'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Listados de Correos'
        context['parent'] = ''
        context['segment'] = 'email'
        context['btn'] = 'Añadir Listado'
        context['url_create'] = reverse_lazy('core:email_recipient_create')
        context['url_list'] = reverse_lazy('core:email_recipient_list')
        context['is_superuser'] = self.request.user.is_superuser
        context['objects'] = EmailRecipientList.objects.annotate(
            member_count=Count('subscriptions', distinct=True),
            reachable_count=Count('subscriptions', filter=_REACHABLE, distinct=True),
        ).prefetch_related(
            Prefetch(
                'subscriptions',
                queryset=Subscription.objects.filter(
                    subscriber__active=True,
                    subscriber__confirmed_at__isnull=False,
                )
                .select_related('subscriber')
                .order_by('subscriber__email'),
                to_attr='reachable_subscriptions',
            )
        )
        return context


class EmailRecipientListCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = EmailRecipientList
    form_class = EmailRecipientListForm
    template_name = 'pages/core/email_recipient/create.html'
    permission_required = 'core.add_emailrecipientlist'
    success_url = reverse_lazy('core:email_recipient_list')

    def form_valid(self, form):
        logger.debug('Datos enviados (POST): %s', self.request.POST)
        response = super().form_valid(form)
        formset = EmailRecipientFormSet(
            self.request.POST, instance=self.object, prefix='recipients'
        )

        if formset.is_valid():
            _discard_empty_recipient_forms(formset)
            formset.save()
            log_action(
                user=self.request.user,
                obj=self.object,
                action_flag=ADDITION,
                message=f'Se creó un nuevo listado de correos: {self.object.name}.',
            )
            messages.success(
                self.request, 'La lista de correos y los destinatarios se han creado con éxito.'
            )
        else:
            logger.debug('Errores del formset: %s', formset.errors)
            messages.error(
                self.request, 'Hubo un error con los destinatarios. Verifica los campos.'
            )
            return self.render_to_response(self.get_context_data(form=form, formset=formset))

        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['formset'] = kwargs.get(
            'formset', EmailRecipientFormSet(instance=self.object, prefix='recipients')
        )
        context['title'] = 'Crear Listado de Correos'
        context['parent'] = ''
        context['segment'] = 'email'
        context['url_list'] = reverse_lazy('core:email_recipient_list')
        return context


class EmailRecipientListUpdateView(
    LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin, UpdateView
):
    model = EmailRecipientList
    form_class = EmailRecipientListForm
    template_name = 'pages/core/email_recipient/update.html'
    permission_required = 'core.change_emailrecipientlist'
    success_url = reverse_lazy('core:email_recipient_list')

    def get_object(self, queryset=None):
        pk = self.kwargs.get('pk')
        return get_object_or_404(EmailRecipientList, pk=pk)

    def form_valid(self, form):
        response = super().form_valid(form)
        formset = EmailRecipientFormSet(
            self.request.POST, instance=self.object, prefix='recipients'
        )

        if formset.is_valid():
            _discard_empty_recipient_forms(formset)
            formset.save()
            log_action(
                user=self.request.user,
                obj=self.object,
                action_flag=CHANGE,
                message=f'Se actualizó el listado de correos: {self.object.name}.',
            )
            messages.success(
                self.request,
                'La lista de correos y los destinatarios han sido actualizados con éxito.',
            )
        else:
            logger.debug('Errores del formset en update: %s', formset.errors)
            messages.error(
                self.request, 'Hubo un error con los destinatarios. Verifica los campos.'
            )
            return self.render_to_response(self.get_context_data(form=form, formset=formset))

        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        formset = kwargs.get(
            'formset', EmailRecipientFormSet(instance=self.object, prefix='recipients')
        )

        for form in formset:
            if not form.instance.pk:
                form.initial['email'] = ''

        context['formset'] = formset
        context['title'] = 'Actualizar Listado de Correos'
        context['parent'] = ''
        context['segment'] = 'email'
        context['url_list'] = reverse_lazy('core:email_recipient_list')
        return context

    def test_func(self):
        return self.request.user.is_superuser


class EmailRecipientListDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'core.delete_emailrecipientlist'

    def post(self, request, pk):
        email_list = get_object_or_404(EmailRecipientList, pk=pk)
        log_action(
            user=self.request.user,
            obj=email_list,
            action_flag=DELETION,
            message=f'Se eliminó el listado de correo {email_list.name}.',
        )
        try:
            email_list.delete()
            messages.success(request, 'El listado de correo ha sido eliminado con éxito.')
        except Exception as e:
            messages.error(request, str(e))
        return redirect('core:email_recipient_list')
