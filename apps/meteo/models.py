import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.models import FileHandlerMixin, pdf_upload_path


def _fecha_local(value):
    """Fecha legible en la zona horaria del portal, no en ISO.

    Los `__str__` de estos modelos alimentan el registro de actividad y los
    selectores del admin: un `2026-11-09 13:43:04.827701+00:00` ahí no se lee.
    """
    if not value:
        return 'sin fecha'
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M')


def _dia_local(value):
    """Igual que `_fecha_local`, para los campos que solo guardan el día."""
    if not value:
        return 'sin fecha'
    return value.strftime('%d/%m/%Y')


class Forecasts(models.Model):
    TIEMPO_CHOICES = [
        ('PN', 'PN'),
        ('PARCN', 'PARCN'),
        ('N', 'N'),
        ('AIS CHUB', 'AIS CHUB'),
        ('ALG CHUB', 'ALG CHUB'),
        ('NUM CHUB', 'NUM CHUB'),
        ('ALG TORM', 'ALG TORM'),
        ('NUM TORM', 'NUM TORM'),
    ]

    VIENTO_DIRECCION_CHOICES = [
        ('VRB', 'VRB'),
        ('N', 'N'),
        ('NNE', 'NNE'),
        ('NE', 'NE'),
        ('ENE', 'ENE'),
        ('E', 'E'),
        ('ESE', 'ESE'),
        ('SE', 'SE'),
        ('SSE', 'SSE'),
        ('S', 'S'),
        ('SSW', 'SSW'),
        ('SW', 'SW'),
        ('WSW', 'WSW'),
        ('W', 'W'),
        ('WNW', 'WNW'),
        ('NW', 'NW'),
        ('NNW', 'NNW'),
    ]

    LUNA_CHOICES = [
        ('Luna Nueva', 'Luna Nueva'),
        ('Creciente', 'Creciente'),
        ('Cuarto Creciente', 'Cuarto Creciente'),
        ('Gibosa Creciente', 'Gibosa Creciente'),
        ('Luna Llena', 'Luna Llena'),
        ('Gibosa Menguante', 'Gibosa Menguante'),
        ('Cuarto Menguante', 'Cuarto Menguante'),
        ('Menguante', 'Menguante'),
    ]

    MAR_CHOICES = [
        ('TQ', 'TQ'),
        ('PO', 'PO'),
        ('O', 'O'),
        ('MRJ', 'MRJ'),
        ('FMRJ', 'FMRJ'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    date = models.DateField(verbose_name='Fecha', unique=True, db_index=True)
    lp = models.CharField(max_length=20, choices=LUNA_CHOICES, verbose_name='Fase Lunar')
    nlp = models.CharField(max_length=20, choices=LUNA_CHOICES, verbose_name='Próxima Fase Lunar')
    nlpd = models.DateField(verbose_name='Fecha Próxima Fase')
    sunrise = models.TimeField(verbose_name='Salida Sol')
    sunset = models.TimeField(verbose_name='Puesta Sol')
    uv_index = models.IntegerField(verbose_name='Índice UV')

    class Meta:
        verbose_name = 'Pronóstico'
        verbose_name_plural = 'Pronósticos'
        ordering = ('-date',)
        default_permissions = ()
        permissions = (
            ('view_forecast', 'Ver'),
            ('add_forecast', 'Añadir'),
            ('change_forecast', 'Editar'),
            ('delete_forecast', 'Eliminar'),
        )

    def __str__(self):
        return f'Pronóstico detallado - {self.date.strftime("%d/%m/%Y")}'

    def get_region_data(self, region):
        periods = {}
        for p in self.regions.filter(region=region).order_by('period_order'):
            periods[p.period] = {
                'temp': p.temp,
                'weather': p.weather,
                'weather_icon': p.weather_icon,
                'wind_dir': p.wind_dir,
                'wind_speed': p.wind_speed,
                'sea': p.sea_note,
            }
        return periods

    def get_extended_days(self):
        return self.extended_days.order_by('day_number').all()

    @property
    def north(self):
        return self.get_region_data('north')

    @property
    def interior(self):
        return self.get_region_data('interior')

    @property
    def south(self):
        return self.get_region_data('south')

    @property
    def extended_forecast(self):
        return self.get_extended_days()


class ForecastRegions(models.Model):
    REGION_CHOICES = [
        ('north', 'Costa Norte'),
        ('interior', 'Interior'),
        ('south', 'Costa Sur'),
    ]
    PERIOD_CHOICES = [
        ('morning', 'Mañana'),
        ('afternoon', 'Tarde'),
        ('night', 'Noche'),
    ]

    forecast = models.ForeignKey(Forecasts, on_delete=models.CASCADE, related_name='regions')
    region = models.CharField(max_length=10, choices=REGION_CHOICES)
    period = models.CharField(max_length=10, choices=PERIOD_CHOICES)
    period_order = models.IntegerField(default=0, editable=False)
    temp = models.IntegerField(
        verbose_name='Temperatura',
        validators=[MinValueValidator(-20), MaxValueValidator(60)],
    )
    weather = models.CharField(
        max_length=10, choices=Forecasts.TIEMPO_CHOICES, verbose_name='Tiempo'
    )
    wind_dir = models.CharField(
        max_length=10,
        choices=Forecasts.VIENTO_DIRECCION_CHOICES,
        verbose_name='Dirección del Viento',
    )
    wind_speed = models.CharField(max_length=5, verbose_name='Velocidad del Viento')
    sea_note = models.CharField(
        max_length=10, choices=Forecasts.MAR_CHOICES, blank=True, null=True, verbose_name='Mar'
    )

    class Meta:
        verbose_name = 'Pronóstico por Región'
        verbose_name_plural = 'Pronósticos por Región'
        default_permissions = ()
        permissions = (
            ('view_forecastregions', 'Ver'),
            ('add_forecastregions', 'Añadir'),
            ('change_forecastregions', 'Editar'),
            ('delete_forecastregions', 'Eliminar'),
        )
        unique_together = ['forecast', 'region', 'period']
        ordering = ['forecast', 'region', 'period_order']

    def __str__(self):
        return f'{self.get_region_display()} - {self.get_period_display()}'

    def save(self, *args, **kwargs):
        period_order_map = {'morning': 1, 'afternoon': 2, 'night': 3}
        self.period_order = period_order_map.get(self.period, 0)
        super().save(*args, **kwargs)

    @property
    def weather_icon(self):
        from apps.core.utils import get_img_path

        return get_img_path(self.weather, self.period)


class ForecastExtendedDay(models.Model):
    forecast = models.ForeignKey(Forecasts, on_delete=models.CASCADE, related_name='extended_days')
    day_number = models.IntegerField(verbose_name='Día')
    date = models.DateField(verbose_name='Fecha')
    min_temp = models.IntegerField(
        verbose_name='Temperatura Mínima',
        validators=[MinValueValidator(-20), MaxValueValidator(60)],
    )
    max_temp = models.IntegerField(
        verbose_name='Temperatura Máxima',
        validators=[MinValueValidator(-20), MaxValueValidator(60)],
    )
    weather = models.CharField(
        max_length=10, choices=Forecasts.TIEMPO_CHOICES, verbose_name='Tiempo'
    )

    class Meta:
        verbose_name = 'Día Extendido'
        verbose_name_plural = 'Días Extendidos'
        default_permissions = ()
        permissions = (
            ('view_forecastextendedday', 'Ver'),
            ('add_forecastextendedday', 'Añadir'),
            ('change_forecastextendedday', 'Editar'),
            ('delete_forecastextendedday', 'Eliminar'),
        )
        unique_together = ['forecast', 'day_number']
        ordering = ['forecast', 'day_number']

    def clean(self):
        if (
            self.min_temp is not None
            and self.max_temp is not None
            and self.min_temp >= self.max_temp
        ):
            raise ValidationError('La temperatura mínima debe ser menor que la máxima.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Día {self.day_number} - {_dia_local(self.date)}'

    @property
    def weather_icon(self):
        from apps.core.utils import get_img_path

        return get_img_path(self.weather, 'afternoon')


class WeatherReport(FileHandlerMixin, models.Model):
    TYPE_CHOICES = [
        ('today', 'Hoy'),
        ('tomorrow', 'Mañana'),
        ('commentary', 'Comentario'),
        ('note', 'Nota'),
    ]
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name='Autor', related_name='meteo_weather_reports'
    )
    date = models.DateTimeField(auto_now_add=True, verbose_name='Fecha y Hora de Creación')
    summary = models.TextField(max_length=300, verbose_name='Resumen')
    content = models.TextField(blank=True, verbose_name='Contenido')
    file = models.FileField(
        upload_to=pdf_upload_path, verbose_name='Archivo PDF', blank=True, null=True
    )
    report_type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name='Tipo')

    file_fields = ['file']

    class Meta:
        verbose_name = 'Reporte Meteorológico'
        verbose_name_plural = 'Reportes Meteorológicos'
        ordering = ['-date']
        default_permissions = ()
        permissions = (
            ('view_weather_report', 'Ver reportes meteorológicos'),
            ('view_weather_today', 'Ver Tiempo Hoy'),
            ('add_weather_today', 'Añadir Tiempo Hoy'),
            ('change_weather_today', 'Editar Tiempo Hoy'),
            ('delete_weather_today', 'Eliminar Tiempo Hoy'),
            ('view_weather_tomorrow', 'Ver Tiempo Mañana'),
            ('add_weather_tomorrow', 'Añadir Tiempo Mañana'),
            ('change_weather_tomorrow', 'Editar Tiempo Mañana'),
            ('delete_weather_tomorrow', 'Eliminar Tiempo Mañana'),
            ('view_weather_commentary', 'Ver Comentario del Tiempo'),
            ('add_weather_commentary', 'Añadir Comentario del Tiempo'),
            ('change_weather_commentary', 'Editar Comentario del Tiempo'),
            ('delete_weather_commentary', 'Eliminar Comentario del Tiempo'),
            ('view_weather_note', 'Ver Nota Meteorológica'),
            ('add_weather_note', 'Añadir Nota Meteorológica'),
            ('change_weather_note', 'Editar Nota Meteorológica'),
            ('delete_weather_note', 'Eliminar Nota Meteorológica'),
        )

    def __str__(self):
        labels = dict(self.TYPE_CHOICES)
        return f'{labels.get(self.report_type, self.report_type)} - {_fecha_local(self.date)}'


class Warning(FileHandlerMixin, models.Model):
    WARNING_TYPES = [
        ('early', 'Alerta Temprana'),
        ('storm', 'Tormenta'),
        ('tropical_cyclone', 'Ciclón Tropical'),
    ]

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    warning_type = models.CharField(
        max_length=20, choices=WARNING_TYPES, verbose_name='Tipo de aviso'
    )
    title = models.CharField(max_length=200, blank=True, null=True, verbose_name='Título')
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='Autor')
    summary = models.TextField(verbose_name='Resumen')
    file = models.FileField(
        upload_to=pdf_upload_path, verbose_name='Archivo PDF', blank=True, null=True
    )
    valid_until = models.DateTimeField(verbose_name='Válido hasta')
    date = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')

    file_fields = ['file']

    class Meta:
        verbose_name = 'Aviso'
        verbose_name_plural = 'Avisos'
        ordering = ['-date']
        default_permissions = ()
        permissions = (
            ('view_warning', 'Ver avisos'),
            ('add_warning', 'Añadir avisos'),
            ('change_warning', 'Editar avisos'),
            ('delete_warning', 'Eliminar avisos'),
        )

    def __str__(self):
        labels = dict(self.WARNING_TYPES)
        return f'{labels.get(self.warning_type, self.warning_type)} - {_fecha_local(self.date)}'


class Province(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=15, verbose_name='Nombre')
    code = models.CharField(
        max_length=2,
        unique=True,
        validators=[
            RegexValidator(r'^\d{2}$', 'El código debe ser numérico de 2 dígitos (ej: 09).')
        ],
        verbose_name='Código',
        help_text='Código numérico de 2 dígitos (ej: 09)',
    )

    class Meta:
        verbose_name = 'Provincia'
        verbose_name_plural = 'Provincias'
        ordering = ('name',)
        default_permissions = ()
        permissions = (
            ('view_province', 'Ver'),
            ('add_province', 'Añadir'),
            ('change_province', 'Editar'),
            ('delete_province', 'Eliminar'),
        )

    def __str__(self):
        return self.name


class Town(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    province = models.ForeignKey(
        Province,
        on_delete=models.SET_NULL,
        null=True,
        related_name='towns',
        verbose_name='Provincia',
    )
    name = models.CharField(max_length=25, verbose_name='Nombre')
    latitude = models.FloatField(verbose_name='Latitud')
    longitude = models.FloatField(verbose_name='Longitud')

    class Meta:
        verbose_name = 'Municipio'
        verbose_name_plural = 'Municipios'
        ordering = ('name',)
        default_permissions = ()
        permissions = (
            ('view_town', 'Ver'),
            ('add_town', 'Añadir'),
            ('change_town', 'Editar'),
            ('delete_town', 'Eliminar'),
        )

    def __str__(self):
        return self.name


class Station(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    province = models.ForeignKey(
        Province,
        on_delete=models.SET_NULL,
        null=True,
        related_name='stations',
        verbose_name='Provincia',
    )
    name = models.CharField(max_length=15, verbose_name='Nombre')
    number = models.IntegerField(unique=True, verbose_name='Número')
    latitude = models.FloatField(verbose_name='Latitud')
    longitude = models.FloatField(verbose_name='Longitud')

    class Meta:
        verbose_name = 'Estación'
        verbose_name_plural = 'Estaciones'
        ordering = ('name',)
        default_permissions = ()
        permissions = (
            ('view_station', 'Ver'),
            ('add_station', 'Añadir'),
            ('change_station', 'Editar'),
            ('delete_station', 'Eliminar'),
        )

    def __str__(self):
        return self.name


# Regiones con litoral (tienen estado de mar); el interior no.
REGION_HAS_SEA = {'north': True, 'interior': False, 'south': True}


class Observation(models.Model):
    """Observación meteorológica capturada desde el panel.

    Guarda los valores ya decodificados (unidades físicas y códigos de clave)
    más el telegrama que se escribe en ``media/obs`` y consume el mapa. Una fila
    por estación, día y hora: el índice único impide dos telegramas para el
    mismo ciclo.
    """

    class Type(models.TextChoices):
        SYNOPTIC = 'synoptic', 'Sinóptica'
        INTERMEDIATE = 'intermediate', 'Intermedia'

    TENDENCY_CHOICES = [
        (0, 'Aumenta y luego disminuye'),
        (1, 'Aumenta y luego se mantiene'),
        (2, 'Aumenta de forma sostenida'),
        (3, 'Disminuye o se mantiene y luego aumenta'),
        (4, 'Se mantiene'),
        (5, 'Disminuye y luego aumenta'),
        (6, 'Disminuye y luego se mantiene'),
        (7, 'Disminuye de forma sostenida'),
        (8, 'Se mantiene o aumenta y luego disminuye'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    station = models.ForeignKey(
        Station,
        on_delete=models.CASCADE,
        related_name='observations',
        verbose_name='Estación',
    )
    observation_type = models.CharField(
        max_length=12, choices=Type.choices, verbose_name='Tipo de observación'
    )
    hour = models.CharField(max_length=2, verbose_name='Hora UTC')
    obs_date = models.DateField(verbose_name='Día de la observación')
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='meteo_observations',
        verbose_name='Observador',
    )

    # Grupo iR iX h VV. iR = iX = 1: estación con personal y precipitación en
    # la sección 1, que es lo que espera el decodificador.
    cloud_base_height = models.PositiveSmallIntegerField(
        verbose_name='Altura de la base de nubes (código h)',
        validators=[MaxValueValidator(9)],
    )
    visibility = models.CharField(max_length=2, verbose_name='Visibilidad (código VV)')

    # Grupo Nddff
    cloud_amount = models.PositiveSmallIntegerField(
        verbose_name='Nubosidad total (octas)',
        validators=[MaxValueValidator(8)],
    )
    wind_direction = models.CharField(max_length=2, verbose_name='Dirección del viento (código dd)')
    wind_speed = models.PositiveSmallIntegerField(
        verbose_name='Velocidad del viento (m/s)',
        validators=[MaxValueValidator(99)],
    )

    # Temperaturas y presión
    temperature = models.DecimalField(
        max_digits=4, decimal_places=1, verbose_name='Temperatura (°C)'
    )
    dew_point = models.DecimalField(
        max_digits=4, decimal_places=1, verbose_name='Punto de rocío (°C)'
    )
    station_pressure = models.DecimalField(
        max_digits=5, decimal_places=1, verbose_name='Presión de la estación (hPa)'
    )
    sea_pressure = models.DecimalField(
        max_digits=5, decimal_places=1, verbose_name='Presión a nivel del mar (hPa)'
    )
    pressure_tendency = models.PositiveSmallIntegerField(
        choices=TENDENCY_CHOICES,
        verbose_name='Tendencia barométrica (código a)',
        validators=[MaxValueValidator(8)],
    )
    pressure_change = models.DecimalField(
        max_digits=4, decimal_places=1, verbose_name='Cambio de presión en 3 h (hPa)'
    )

    # Precipitación y tiempo
    precipitation_3h = models.DecimalField(
        max_digits=4, decimal_places=1, default=0, verbose_name='Precipitación en 3 h (mm)'
    )
    precipitation_3h_trace = models.BooleanField(
        default=False, verbose_name='Precipitación de 3 h: traza'
    )
    present_weather = models.CharField(max_length=2, verbose_name='Tiempo presente (código ww)')
    past_weather = models.CharField(max_length=1, verbose_name='Tiempo pasado (W1)')
    past_weather_2 = models.CharField(max_length=1, verbose_name='Tiempo pasado (W2)')

    # Nubes CL / CM / CH
    low_cloud_type = models.CharField(max_length=1, verbose_name='Nubes bajas (CL)')
    medium_cloud_type = models.CharField(max_length=1, verbose_name='Nubes medias (CM)')
    high_cloud_type = models.CharField(max_length=1, verbose_name='Nubes altas (CH)')

    # Solo observación sinóptica (sección 2 con Tx/Tn y 24 h)
    max_temperature = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name='Temperatura máxima (°C)',
    )
    min_temperature = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name='Temperatura mínima (°C)',
    )
    precipitation_24h = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name='Precipitación en 24 h (mm)',
    )
    precipitation_24h_trace = models.BooleanField(
        default=False, verbose_name='Precipitación de 24 h: traza'
    )

    telegram = models.TextField(verbose_name='Telegrama SYNOP')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada')

    class Meta:
        verbose_name = 'Observación'
        verbose_name_plural = 'Observaciones'
        ordering = ('-obs_date', '-hour')
        default_permissions = ()
        permissions = (
            ('view_observation', 'Ver'),
            ('add_observation', 'Añadir'),
            ('change_observation', 'Editar'),
            ('delete_observation', 'Eliminar'),
        )
        constraints = (
            models.UniqueConstraint(
                fields=('station', 'obs_date', 'hour'),
                name='meteo_unique_observation_slot',
            ),
        )

    def __str__(self):
        return (
            f'{self.station.name} {self.get_observation_type_display()} '
            f'{_dia_local(self.obs_date)} {self.hour}:00 UTC'
        )

    @property
    def is_synoptic(self):
        return self.observation_type == self.Type.SYNOPTIC

    @property
    def filename(self):
        from apps.api.data.SynopTelegram import telegram_filename

        return telegram_filename(self.station.number, self.hour)

    @property
    def visibility_label(self):
        from apps.api.data.Tablas import Tablas

        return Tablas().VV.get(self.visibility, self.visibility)

    @property
    def wind_direction_label(self):
        from apps.api.data.Tablas import Tablas

        return Tablas().dd.get(self.wind_direction, self.wind_direction)
