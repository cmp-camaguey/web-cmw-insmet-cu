"""Formulario de una observación meteorológica capturada en el panel.

Los grupos del telegrama se llenan con los valores físicos y los códigos de
clave que ya usa ``Descodificador``. El tipo de observación decide qué horas son
válidas y qué grupos de la sección 2 se piden: la sinóptica (00/06/12/18) lleva
Tx/Tn y 24 h; la intermedia (03/09/15/21) no los lleva.

El día no se pide: lo calcula ``synop_expected_obs_date`` al validar, que es la
misma regla que usa el mapa para aceptar el telegrama. La estación se limita a
las que el usuario tiene por grupo, y la unicidad estación/día/hora se comprueba
antes de escribir para no pisar un ciclo existente por accidente.
"""

from django import forms

from apps.api.data.SynopDay import synop_expected_obs_date
from apps.api.data.SynopTelegram import INTERMEDIATE, SYNOPTIC, hours_for
from apps.api.data.Tablas import Tablas
from apps.meteo.models import Observation, Station
from apps.meteo.services.observations import observation_telegram, write_observation_telegram
from apps.meteo.station_roles import user_station_numbers

OCTAS_CHOICES = [
    (0, '0 — Despejado'),
    (1, '1 octa'),
    (2, '2 octas'),
    (3, '3 octas'),
    (4, '4 octas'),
    (5, '5 octas'),
    (6, '6 octas'),
    (7, '7 octas'),
    (8, '8 — Cubierto'),
]

# Tipos de nubes bajas, medias y altas: tablas 15, 16 y 17 del FM12 (OMM).
CLOUD_TYPE_CHOICES = {
    'low_cloud_type': [
        ('0', '0 — No hay nubes bajas'),
        ('1', '1 — Cumulus humilis o fractus'),
        ('2', '2 — Cumulus mediocris o congestus'),
        ('3', '3 — Cumulonimbus calvus'),
        ('4', '4 — Stratocumulus cumulogenitus'),
        ('5', '5 — Stratocumulus no cumulogenitus'),
        ('6', '6 — Stratus nebulosus o fractus de buen tiempo'),
        ('7', '7 — Stratus fractus o Cumulus fractus de mal tiempo (pannus)'),
        ('8', '8 — Cumulus y Stratocumulus con bases a distinto nivel'),
        ('9', '9 — Cumulonimbus capillatus, a menudo con yunque'),
    ],
    'medium_cloud_type': [
        ('0', '0 — No hay nubes medias'),
        ('1', '1 — Altostratus translúcidos'),
        ('2', '2 — Altostratus opacos o Nimbostratus'),
        ('3', '3 — Altocumulus translúcidos a un solo nivel'),
        ('4', '4 — Bancos de Altocumulus translúcidos cambiando a varios niveles'),
        ('5', '5 — Altocumulus en bandas o capas invadiendo el cielo'),
        ('6', '6 — Altocumulus cumulogenitus'),
        ('7', '7 — Altocumulus en dos o más capas, con Altostratus o Nimbostratus'),
        ('8', '8 — Altocumulus castellanus o floccus'),
        ('9', '9 — Altocumulus de cielo caótico a varios niveles'),
    ],
    'high_cloud_type': [
        ('0', '0 — No hay nubes altas'),
        ('1', '1 — Cirrus fibratus o uncinus sin invadir el cielo'),
        ('2', '2 — Cirrus spissatus, castellanus o floccus'),
        ('3', '3 — Cirrus spissatus cumulonimbogenitus'),
        ('4', '4 — Cirrus uncinus o fibratus invadiendo el cielo'),
        ('5', '5 — Cirrus y Cirrostratus, velo continuo bajo los 45°'),
        ('6', '6 — Cirrus y Cirrostratus, velo continuo sobre los 45°'),
        ('7', '7 — Cirrostratus cubriendo todo el cielo'),
        ('8', '8 — Cirrostratus sin invadir el cielo ni cubrirlo por completo'),
        ('9', '9 — Cirrocumulus predominando entre las nubes altas'),
    ],
}

HOUR_CHOICES = [
    ('00', '00 UTC'),
    ('03', '03 UTC'),
    ('06', '06 UTC'),
    ('09', '09 UTC'),
    ('12', '12 UTC'),
    ('15', '15 UTC'),
    ('18', '18 UTC'),
    ('21', '21 UTC'),
]


def _code_choices(table: dict) -> list[tuple[str, str]]:
    """``[(código, 'código — descripción')]`` ordenado por el código."""
    return [
        (code, f'{code} — {text}')
        for code, text in sorted(table.items(), key=lambda item: int(item[0]))
    ]


def _height_choices() -> list[tuple[str, str]]:
    """Códigos ``h`` de la base de nubes con su rango en metros."""
    ranges = {
        '0': '0 - 49 m',
        '1': '50 - 99 m',
        '2': '100 - 199 m',
        '3': '200 - 299 m',
        '4': '300 - 599 m',
        '5': '600 - 999 m',
        '6': '1000 - 1499 m',
        '7': '1500 - 1999 m',
        '8': '2000 - 2499 m',
        '9': '2500 m o más, o sin nubes',
    }
    return [(code, f'{code} — {text}') for code, text in ranges.items()]


def _select():
    return forms.Select(attrs={'class': 'form-select'})


def _number(placeholder=None):
    attrs = {'class': 'form-control', 'step': '0.1', 'inputmode': 'decimal'}
    if placeholder:
        attrs['placeholder'] = placeholder
    return forms.TextInput(attrs=attrs)


def _integer(placeholder=None):
    attrs = {'class': 'form-control', 'inputmode': 'numeric'}
    if placeholder:
        attrs['placeholder'] = placeholder
    return forms.TextInput(attrs=attrs)


class ObservationForm(forms.ModelForm):
    hour = forms.ChoiceField(label='Hora UTC', choices=HOUR_CHOICES, widget=_select())
    cloud_amount = forms.TypedChoiceField(
        label='Nubosidad total (octas)', choices=OCTAS_CHOICES, coerce=int, widget=_select()
    )
    cloud_base_height = forms.TypedChoiceField(
        label='Altura de la base de nubes',
        choices=_height_choices(),
        coerce=int,
        widget=_select(),
    )
    visibility = forms.ChoiceField(
        label='Visibilidad', choices=_code_choices(Tablas().VV), widget=_select()
    )
    wind_direction = forms.ChoiceField(
        label='Dirección del viento', choices=_code_choices(Tablas().dd), widget=_select()
    )
    pressure_tendency = forms.TypedChoiceField(
        label='Tendencia barométrica',
        choices=Observation.TENDENCY_CHOICES,
        coerce=int,
        widget=_select(),
    )
    present_weather = forms.ChoiceField(
        label='Tiempo presente (ww)',
        choices=_code_choices(Tablas().ww),
        widget=_select(),
        help_text='Código ww de 00 a 99.',
    )
    past_weather = forms.ChoiceField(
        label='Tiempo pasado (W1)',
        choices=_code_choices(Tablas().W),
        widget=_select(),
        help_text='Código W1 de 0 a 9.',
    )
    past_weather_2 = forms.ChoiceField(
        label='Tiempo pasado (W2)',
        choices=_code_choices(Tablas().W),
        widget=_select(),
        help_text='Código W2 de 0 a 9.',
    )
    low_cloud_type = forms.ChoiceField(
        label='Nubes bajas (CL)', choices=CLOUD_TYPE_CHOICES['low_cloud_type'], widget=_select()
    )
    medium_cloud_type = forms.ChoiceField(
        label='Nubes medias (CM)',
        choices=CLOUD_TYPE_CHOICES['medium_cloud_type'],
        widget=_select(),
    )
    high_cloud_type = forms.ChoiceField(
        label='Nubes altas (CH)', choices=CLOUD_TYPE_CHOICES['high_cloud_type'], widget=_select()
    )

    class Meta:
        model = Observation
        fields = [
            'station',
            'observation_type',
            'hour',
            'cloud_amount',
            'cloud_base_height',
            'visibility',
            'wind_direction',
            'wind_speed',
            'temperature',
            'dew_point',
            'station_pressure',
            'sea_pressure',
            'pressure_tendency',
            'pressure_change',
            'precipitation_3h',
            'precipitation_3h_trace',
            'present_weather',
            'past_weather',
            'past_weather_2',
            'low_cloud_type',
            'medium_cloud_type',
            'high_cloud_type',
            'max_temperature',
            'min_temperature',
            'precipitation_24h',
            'precipitation_24h_trace',
        ]
        widgets = {
            'station': _select(),
            'observation_type': _select(),
            'wind_speed': _integer('ej. 12'),
            'temperature': _number('ej. 28.4'),
            'dew_point': _number('ej. 21.0'),
            'station_pressure': _number('ej. 1013.2'),
            'sea_pressure': _number('ej. 1013.5'),
            'pressure_change': _number('ej. 0.8'),
            'precipitation_3h': _number('ej. 0.0'),
            'precipitation_3h_trace': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'max_temperature': _number('ej. 31.5'),
            'min_temperature': _number('ej. 22.1'),
            'precipitation_24h': _number('ej. 5.4'),
            'precipitation_24h_trace': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.editing = bool(self.instance and self.instance.pk)

        stations = Station.objects.order_by('name')
        if user is not None and not user.is_superuser:
            stations = stations.filter(number__in=user_station_numbers(user))
        self.fields['station'].queryset = stations

        if not self.editing and user is not None and not user.is_superuser:
            numbers = user_station_numbers(user)
            if len(numbers) == 1:
                self.fields['station'].initial = stations.filter(number=next(iter(numbers))).first()

        if self.editing:
            # Corregir valores, no mover la observación a otro ciclo: el archivo
            # del mapa ya tiene ese nombre.
            for name in ('station', 'observation_type', 'hour'):
                self.fields[name].disabled = True

    def clean_wind_speed(self):
        value = self.cleaned_data.get('wind_speed')
        if value is None or not 0 <= value <= 99:
            raise forms.ValidationError('La velocidad del viento debe estar entre 0 y 99 m/s.')
        return value

    def clean_pressure_change(self):
        value = self.cleaned_data.get('pressure_change')
        if value is not None and value < 0:
            raise forms.ValidationError(
                'El cambio de presión se informa en valor absoluto (0 o más).'
            )
        return value

    def clean_precipitation_3h(self):
        value = self.cleaned_data.get('precipitation_3h')
        if value is not None and value < 0:
            raise forms.ValidationError('La precipitación no puede ser negativa.')
        return value

    def clean_precipitation_24h(self):
        value = self.cleaned_data.get('precipitation_24h')
        if value is not None and value < 0:
            raise forms.ValidationError('La precipitación no puede ser negativa.')
        return value

    def clean(self):
        cleaned = super().clean()
        observation_type = cleaned.get('observation_type')
        hour = cleaned.get('hour')

        if observation_type == SYNOPTIC:
            for field in ('max_temperature', 'min_temperature'):
                if cleaned.get(field) is None:
                    self.add_error(
                        field,
                        'Obligatoria en la observación sinóptica.',
                    )
            maximum = cleaned.get('max_temperature')
            minimum = cleaned.get('min_temperature')
            if maximum is not None and minimum is not None and minimum > maximum:
                self.add_error(
                    'min_temperature', 'La temperatura mínima no puede superar a la máxima.'
                )
            self._check_traza(cleaned, 'precipitation_24h', 'precipitation_24h_trace')
        elif observation_type == INTERMEDIATE:
            # La intermedia no publica estos grupos; si el navegador los envió,
            # no deben llegar al telegrama.
            cleaned['max_temperature'] = None
            cleaned['min_temperature'] = None
            cleaned['precipitation_24h'] = None
            cleaned['precipitation_24h_trace'] = False
            self.instance.max_temperature = None
            self.instance.min_temperature = None
            self.instance.precipitation_24h = None
            self.instance.precipitation_24h_trace = False

        self._check_traza(cleaned, 'precipitation_3h', 'precipitation_3h_trace')

        if observation_type and hour:
            if hour not in hours_for(observation_type):
                label = dict(Observation.Type.choices)[observation_type].lower()
                self.add_error(
                    'hour', f'La hora {hour}:00 no corresponde a una observación {label}.'
                )
            else:
                self._clean_slot(cleaned, hour)

        return cleaned

    def _check_traza(self, cleaned, amount_field, trace_field):
        amount = cleaned.get(amount_field)
        if cleaned.get(trace_field) and amount not in (None, 0):
            self.add_error(
                amount_field,
                'Indique la cantidad o marque traza, no ambas.',
            )

    def _clean_slot(self, cleaned, hour):
        """Calcula el día y comprueba que el ciclo no esté ocupado."""
        station = cleaned.get('station')
        if station is None:
            return
        obs_date = self.instance.obs_date if self.editing else synop_expected_obs_date(hour)
        if obs_date is None:
            return
        self.instance.obs_date = obs_date

        conflict = Observation.objects.filter(station=station, obs_date=obs_date, hour=hour)
        if self.editing:
            conflict = conflict.exclude(pk=self.instance.pk)
        if conflict.exists():
            self.add_error(
                'hour',
                f'Ya existe una observación de {station.name} para las {hour}:00 '
                f'del {obs_date:%d/%m/%Y}. Edítela desde el registro.',
            )

    def save(self, commit=True):
        observation = super().save(commit=False)
        if not observation.pk and self.user is not None:
            observation.user = self.user
        observation.telegram = observation_telegram(observation)
        if commit:
            observation.save()
            write_observation_telegram(observation)
        return observation
