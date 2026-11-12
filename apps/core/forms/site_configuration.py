import re

from django import forms

from apps.core.models import SiteConfiguration

HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


class SiteConfigurationForm(forms.ModelForm):
    """Tema, identidad, horario y formato de hora del sitio.

    El offset solo se exige en modo manual. En automático el selector puede
    llegar deshabilitado por la interfaz (y por lo tanto ausente del POST), así
    que el campo es opcional y se conserva el valor guardado.
    """

    class Meta:
        model = SiteConfiguration
        fields = [
            'primary_color',
            'theme_base',
            'theme_font',
            'theme_radius',
            'timezone_mode',
            'utc_offset_hours',
            'time_format',
            'brand_logo',
            'favicon',
        ]
        widgets = {
            'primary_color': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'data-coloris': '',
                    'data-coloris-format': 'hex',
                    'placeholder': '#2b4b9b',
                }
            ),
            'theme_base': forms.Select(attrs={'class': 'form-select'}),
            'theme_font': forms.Select(attrs={'class': 'form-select'}),
            'theme_radius': forms.Select(attrs={'class': 'form-select'}),
            'timezone_mode': forms.Select(attrs={'class': 'form-select'}),
            'utc_offset_hours': forms.Select(attrs={'class': 'form-select'}),
            'time_format': forms.Select(attrs={'class': 'form-select'}),
            'brand_logo': forms.FileInput(attrs={'class': 'form-control'}),
            'favicon': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['utc_offset_hours'].required = False

    def clean_primary_color(self):
        value = self.cleaned_data.get('primary_color')
        if not HEX_COLOR_RE.match(value):
            raise forms.ValidationError('El color debe tener formato #RRGGBB.')
        return value

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get('timezone_mode')
        offset = cleaned_data.get('utc_offset_hours')
        if mode == SiteConfiguration.TimeZoneMode.MANUAL and offset in (None, ''):
            self.add_error('utc_offset_hours', 'Seleccione la diferencia con UTC.')
        elif offset in (None, '') and self.instance.pk:
            cleaned_data['utc_offset_hours'] = self.instance.utc_offset_hours
        return cleaned_data
