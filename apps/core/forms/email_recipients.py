from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from apps.core.models import EmailRecipientList, Subscriber, Subscription


class EmailRecipientListForm(forms.ModelForm):
    class Meta:
        model = EmailRecipientList
        fields = ['name', 'description', 'is_default']
        widgets = {
            'name': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': 'Nombre de la lista'}
            ),
            'description': forms.TextInput(
                attrs={'class': 'form-control', 'placeholder': 'Descripción (opcional)'}
            ),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SubscriptionForm(forms.ModelForm):
    """Pertenencia a una lista, escrita como un correo.

    El operador escribe una dirección, no un `Subscriber`: el formulario
    resuelve la persona y crea la pertenencia. Así una dirección que ya está en
    otra lista se agrega a esta sin duplicarse ni mentir sobre lo aceptado.
    """

    email = forms.EmailField(
        required=False,
        label='Correo electrónico',
        widget=forms.EmailInput(
            attrs={'class': 'form-control', 'placeholder': 'Correo electrónico'}
        ),
    )

    class Meta:
        model = Subscription
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `subscriber_id` y no `subscriber`: una fila recién construida por el
        # formset puede traer pk sin la FK resuelta, y tocar la relación ahí
        # revienta con RelatedObjectDoesNotExist.
        if self.instance.pk and self.instance.subscriber_id:
            self.fields['email'].initial = self.instance.subscriber.email

    @property
    def existing(self):
        return self.instance.pk is not None and not self.instance._state.adding

    def save(self, commit=True):
        subscription = super().save(commit=False)
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return subscription

        subscriber, _created = Subscriber.objects.get_or_create(
            email=email,
            defaults={
                'source': Subscriber.Source.ADMIN,
                # Viene de una carga manual del operador: su consentimiento se
                # asume confirmado, que es la decisión 3 de la propuesta 030.
                'confirmed_at': timezone.now(),
            },
        )
        subscription.subscriber = subscriber
        subscription.source = Subscriber.Source.ADMIN
        if commit:
            subscription.save()
        return subscription


class BaseSubscriptionFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen = set()
        for form in self.forms:
            if form in self.deleted_forms:
                continue
            email = (form.cleaned_data.get('email') or '').strip().lower()
            if not email:
                continue
            if email in seen:
                raise forms.ValidationError('El mismo correo aparece dos veces en la lista.')
            seen.add(email)

        if not seen:
            return

        existing_pks = [form.instance.pk for form in self.forms if form.instance.pk]
        duplicates = Subscription.objects.filter(
            recipient_list=self.instance, subscriber__email__in=seen
        ).exclude(pk__in=existing_pks)
        if duplicates.exists():
            raise forms.ValidationError('Ese correo ya está en esta lista.')


EmailRecipientFormSet = inlineformset_factory(
    EmailRecipientList,
    Subscription,
    form=SubscriptionForm,
    formset=BaseSubscriptionFormSet,
    extra=0,
    can_delete=True,
)
