from django import forms

from apps.core.audiences import audience_size
from apps.meteo.models import WeatherReport


class WeatherReportForm(forms.ModelForm):
    # Un reporte se corrige varias veces al día: mandar cada guardado a todo el
    # mundo sería ruido. Desmarcado siempre —también al crear— y el operador lo
    # marca cuando de verdad quiere que salga.
    send_email = forms.BooleanField(
        required=False,
        initial=False,
        label='Enviar por correo',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = WeatherReport
        fields = ['summary', 'content', 'file']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.report_type = kwargs.pop('report_type', None)
        super().__init__(*args, **kwargs)
        count = audience_size()
        plural = 'es' if count != 1 else ''
        self.fields['send_email'].help_text = (
            f'Marque para enviar este pronóstico a {count} suscriptor{plural}. '
            'La lista es automática: no hay que elegirla.'
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user:
            instance.user = self.user
        if self.report_type:
            instance.report_type = self.report_type
        if commit:
            instance.save()
        return instance
