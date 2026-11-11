from django import forms

from apps.core.audiences import audience_size
from apps.core.widgets import TempusAwareDateTimeInput
from apps.meteo.models import Warning


def recipients_text(*, resend=False):
    """Texto de la casilla con el número real de destinatarios.

    La audiencia es automática y el operador no elige lista, así que el único
    freno es la casilla. Ver cuántos correos saldrían evita el «publiqué y no me
    di cuenta».
    """
    count = audience_size()
    plural = 'es' if count != 1 else ''
    if resend:
        return (
            f'Marque para reenviar este aviso a {count} suscriptor{plural}. '
            'Editar sin marcar no envía correo.'
        )
    return f'Se enviará a {count} suscriptor{plural}. La lista es automática: no hay que elegirla.'


class WarningForm(forms.ModelForm):
    valid_until = forms.DateTimeField(
        input_formats=['%d/%m/%Y %I:%M %p', '%Y-%m-%dT%H:%M', '%d/%m/%Y %H:%M'],
        widget=TempusAwareDateTimeInput(attrs={'class': 'form-control'}),
        label='Válido hasta',
    )
    # Un aviso nuevo existe para comunicarse: marcado por defecto. En una
    # edición arranca desmarcado (ver `__init__`): reenviar a todo el mundo por
    # corregir una errata es peor que no reenviar.
    send_email = forms.BooleanField(
        required=False,
        initial=True,
        label='Enviar por correo',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = Warning
        fields = ['title', 'summary', 'valid_until', 'file']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.warning_type = kwargs.pop('warning_type', None)
        super().__init__(*args, **kwargs)
        # `Warning.uuid` es PK con `default=uuid4`, así que una instancia nueva
        # YA trae pk y `pk is not None` miente. `_state.adding` sí distingue
        # «nunca guardado» de «leído de la base».
        is_edit = not self.instance._state.adding
        self.fields['send_email'].initial = not is_edit
        self.fields['send_email'].help_text = recipients_text(resend=is_edit)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user:
            instance.user = self.user
        if self.warning_type:
            instance.warning_type = self.warning_type
        if commit:
            instance.save()
        return instance
