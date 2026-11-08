from django import forms
from django.contrib.auth.models import User

from apps.commercial.models import Customer
from apps.core.subscriptions import move_subscription
from apps.core.validators import validate_image_upload
from apps.user_auth.models import Profile


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=30, required=True, label='Nombre')
    last_name = forms.CharField(max_length=30, required=True, label='Apellido')
    email = forms.EmailField(required=True, label='Correo electrónico')

    company_name = forms.CharField(
        max_length=100,
        required=False,
        label='Nombre de la Empresa',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    reeup = forms.CharField(
        max_length=12,
        required=False,
        label='REEUP',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    nit = forms.CharField(
        max_length=11,
        required=False,
        label='NIT',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    account = forms.CharField(
        max_length=16,
        required=False,
        label='Cuenta Bancaria',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    agency_bank = forms.CharField(
        max_length=100,
        required=False,
        label='Agencia Bancaria',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    address = forms.CharField(
        required=False,
        label='Dirección',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )
    phone = forms.CharField(
        max_length=100,
        required=False,
        label='Teléfonos',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    newsletter = forms.BooleanField(
        required=False,
        label='Recibir novedades por email',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = Profile
        fields = ['avatar', 'first_name', 'last_name', 'email']
        widgets = {
            'avatar': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.user_id is not None:
            user = self.instance.user
            self.fields['newsletter'].initial = self.instance.newsletter
            if hasattr(user, 'commercial_customer'):
                customer = user.commercial_customer
                self.fields['company_name'].initial = customer.company_name
                self.fields['reeup'].initial = customer.reeup
                self.fields['nit'].initial = customer.nit
                self.fields['account'].initial = customer.account
                self.fields['agency_bank'].initial = customer.agency_bank
                self.fields['address'].initial = customer.address
                self.fields['phone'].initial = customer.phone

                common_fields = ['account', 'agency_bank', 'address', 'phone']
                for field_name in common_fields:
                    self.fields[field_name].required = True

                if customer.client_type == customer.ClientType.JURIDICA:
                    for field_name in ['company_name', 'reeup', 'nit']:
                        self.fields[field_name].required = True

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        # Only validate a NEW upload: a pre-existing avatar that is already
        # corrupted on disk must not block editing the name, the email or any
        # other field of the profile.
        if avatar is not None and self.files.get('avatar'):
            validate_image_upload(avatar)
        return avatar

    def clean_email(self):
        email = self.cleaned_data.get('email')
        user = self.instance.user
        if (
            user is not None
            and user.pk is not None
            and User.objects.filter(email=email).exclude(pk=user.pk).exists()
        ):
            raise forms.ValidationError('Este correo electrónico ya está registrado.')
        return email

    def _run_validators(self, field_name, value):
        for validator in Customer._meta.get_field(field_name).validators:
            validator(value)

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone:
            self._run_validators('phone', phone)
        return phone

    def clean_reeup(self):
        reeup = self.cleaned_data.get('reeup')
        if reeup:
            self._run_validators('reeup', reeup)
        return reeup

    def clean_nit(self):
        nit = self.cleaned_data.get('nit')
        if nit:
            self._run_validators('nit', nit)
        return nit

    def clean_account(self):
        account = self.cleaned_data.get('account')
        if account:
            self._run_validators('account', account)
        return account

    def save(self, commit=True):
        profile = super().save(commit=False)

        if profile.user_id is None:
            raise ValueError('El perfil no tiene un usuario asociado.')

        user = profile.user
        old_email = user.email
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.email = self.cleaned_data['email']
        profile.newsletter = self.cleaned_data.get('newsletter', False)

        if commit:
            # Antes de guardar: si la dirección cambió, la suscripción tiene que
            # viajar con ella. Sin esto la persona seguiría recibiendo en el
            # buzón viejo y, con la casilla marcada, se crearía una segunda
            # suscripción en la dirección nueva.
            if old_email and old_email != user.email:
                move_subscription(old_email, user.email)
            user.save()
            profile.save()

            if hasattr(user, 'commercial_customer'):
                customer = user.commercial_customer
                if customer.client_type == customer.ClientType.JURIDICA:
                    customer.company_name = self.cleaned_data.get('company_name') or None
                    customer.reeup = self.cleaned_data.get('reeup') or None
                    customer.nit = self.cleaned_data.get('nit') or None
                else:
                    customer.company_name = None
                    customer.reeup = None
                    customer.nit = None
                customer.account = self.cleaned_data.get('account') or ''
                customer.agency_bank = self.cleaned_data.get('agency_bank') or ''
                customer.address = self.cleaned_data.get('address') or ''
                customer.phone = self.cleaned_data.get('phone') or ''
                customer.save()

        return profile
