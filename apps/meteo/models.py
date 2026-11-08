import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from apps.core.models import FileHandlerMixin, pdf_upload_path


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
        return f'Día {self.day_number} - {self.date}'

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
        return f'{labels.get(self.report_type, self.report_type)} - {self.date}'


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
        return f'{labels.get(self.warning_type, self.warning_type)} - {self.date}'


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
