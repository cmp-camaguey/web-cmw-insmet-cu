from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.api.data.GetData import GetData
from apps.api.data.SynopDay import synop_expected_obs_date
from apps.api.serializers import (
    ForecastSerializer,
    ScientificPublicationSerializer,
    ServiceSerializer,
    StationObservationSerializer,
    StationSerializer,
    WarningSerializer,
    WeatherReportSerializer,
)
from apps.commercial.models import Service
from apps.core.cache_utils import build_api_cache_key, safe_cache_get, safe_cache_set
from apps.meteo.models import (
    Forecasts,
    Station,
    WeatherReport,
)
from apps.meteo.models import (
    Warning as MeteoWarning,
)
from apps.publications.models import ScientificPublication

ALLOWED_REPORT_TYPES = {'today', 'tomorrow', 'commentary', 'note'}


class CacheAPIMixin:
    """Cache read-only ``AllowAny`` GET responses by request path + query string.

    Only successful (HTTP 200) responses are cached; errors (400/404) are never
    stored. Cache reads/writes go through ``safe_cache_get``/``safe_cache_set`` so
    a Redis outage degrades to a live (uncached) response instead of a 500.

    ``cache_timeout`` is overridden per endpoint: 60s for volatile sources
    (observations, warnings) and 300s for stable sources (stations, publications,
    services, forecast).
    """

    cache_timeout = 300

    def get(self, request, *args, **kwargs):
        key = build_api_cache_key(request)
        cached = safe_cache_get(key)
        if cached is not None:
            data, status_code = cached
            return Response(data, status=status_code)
        response = super().get(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            safe_cache_set(key, (response.data, response.status_code), self.cache_timeout)
        return response


class StationObservationView(CacheAPIMixin, GenericAPIView):
    cache_timeout = 60

    """
    ### Vista de Observación de Estación Meteorológica
    Recupera datos de observación para una estación específica a una hora determinada.

    **Parámetros:**
    - `hour` (str): Hora de observación (formato HH).
      Horas permitidas: 00, 03, 06, 09, 12, 15, 18, 21
    - `station_number` (str): Número de la estación meteorológica.

    **Estaciones disponibles:**
    - Florida: 78350
    - Palo Seco: 78354
    - Nuevitas: 78353
    - Esmeralda: 78352
    - Santa Cruz: 78351
    - Camagüey: 78355

    **Respuestas:**
    - `200 OK`: Devuelve los datos de observación.
    - `400 Bad Request`: Devuelve errores de validación.
    """

    permission_classes = [AllowAny]
    serializer_class = StationObservationSerializer

    def get(self, request, hour, station_number):
        allowed_hours = {'00', '03', '06', '09', '12', '15', '18', '21'}
        stations = {
            '78350': 'Florida',
            '78354': 'Palo Seco',
            '78353': 'Nuevitas',
            '78352': 'Esmeralda',
            '78351': 'Santa Cruz',
            '78355': 'Camagüey',
        }

        hour_str = str(hour).zfill(2)

        if hour_str not in allowed_hours:
            return Response(
                {'error': 'Invalid hour parameter. Valid hours: 00, 03, 06, 09, 12, 15, 18, 21'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if str(station_number) not in stations:
            return Response(
                {
                    'error': 'Invalid station number. Available stations: '
                    + ', '.join(f'{k} ({v})' for k, v in stations.items())
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Modo local (022): si la observación pedida no está en disco, el API
        # autogenera el SYNOP del (hora, estación) con el día que dicta la regla
        # compartida (SynopDay): los horarios 00/03 embeben el día posterior
        # (+1), salvo que la petición llegue en horas UTC anteriores (día actual);
        # el resto de horarios usan el día actual. Generador y validador leen la
        # misma regla → el SYNOP generado pasa la validación y el API devuelve
        # datos (el mapa de home pinta). En modo manual no se inventan datos: lo
        # que no capturó la estación no existe.
        data = GetData().get_station(hour_str, station_number)
        if (
            getattr(settings, 'OBS_LOCAL_ONLY', False)
            and getattr(settings, 'OBS_AUTOMATIC', True)
            and data is not None
            and data.get('data') is None
        ):
            from apps.api.data.SynopSimulator import SynopSimulator

            SynopSimulator().generate_to_file(
                station_number,
                hour_str,
                synop_expected_obs_date(hour_str),
                settings.MEDIA_ROOT / 'obs',
            )
            data = GetData().get_station(hour_str, station_number)

        serializer = self.get_serializer(
            data={'hour': hour_str, 'station_number': station_number, 'data': data}
        )

        if serializer.is_valid():
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StationListAPIView(CacheAPIMixin, ListAPIView):
    cache_timeout = 300
    """
    ### Vista de Listado de Estaciones
    Esta vista proporciona una lista de todas las estaciones.

    **Respuestas:**
    - `200 OK`: Devuelve la lista de estaciones.
    """

    queryset = Station.objects.select_related('province')
    serializer_class = StationSerializer
    permission_classes = [AllowAny]
    # Keep /api/stations/ a flat array for map_station.js (data.forEach).
    pagination_class = None


class ForecastAPIView(CacheAPIMixin, GenericAPIView):
    cache_timeout = 300
    """
    ### Vista de API de Pronóstico
    Esta vista recupera los datos de pronóstico.

    **Parámetros:**
    - `date`: La fecha en formato Año-Mes-Dia (YYYY-MM-DD) para la cual se
      solicitan los datos de pronóstico.

    **Respuestas:**
    - `200 OK`: Devuelve los datos de pronóstico.
    - `400 Bad Request`: Devuelve errores de validación.
    - `404 Not Found`: Devuelve un mensaje si los pronósticos no se han actualizado.
    """

    permission_classes = [AllowAny]
    queryset = Forecasts.objects.prefetch_related('regions', 'extended_days').all()
    serializer_class = ForecastSerializer

    def get(self, request, date=None):
        if date is None:
            date = timezone.localdate()
        else:
            date = timezone.datetime.strptime(date, '%Y-%m-%d').date()

        forecasts = self.queryset.filter(date=date).first()

        if not forecasts:
            return Response({'message': 'Los pronósticos no se han actualizado.'}, status=404)

        serializer = self.get_serializer(forecasts)
        return Response(serializer.data)


class EarlyWarningListAPIView(CacheAPIMixin, ListAPIView):
    cache_timeout = 60
    serializer_class = WarningSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return MeteoWarning.objects.select_related('user').filter(
            warning_type='early', valid_until__gte=timezone.now()
        )


class TropicalCycloneListAPIView(CacheAPIMixin, ListAPIView):
    cache_timeout = 60
    serializer_class = WarningSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return MeteoWarning.objects.select_related('user').filter(
            warning_type='tropical_cyclone', valid_until__gte=timezone.now()
        )


class StormWarningListAPIView(CacheAPIMixin, ListAPIView):
    cache_timeout = 60
    serializer_class = WarningSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return MeteoWarning.objects.select_related('user').filter(
            warning_type='storm', valid_until__gte=timezone.now()
        )


class WeatherReportListAPIView(CacheAPIMixin, ListAPIView):
    cache_timeout = 300
    serializer_class = WeatherReportSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        report_type = self.kwargs.get('type')
        if report_type not in ALLOWED_REPORT_TYPES:
            return WeatherReport.objects.none()
        return WeatherReport.objects.select_related('user').filter(report_type=report_type)


class ScientificPublicationListAPIView(CacheAPIMixin, ListAPIView):
    cache_timeout = 300
    queryset = ScientificPublication.objects.select_related('author').prefetch_related('coauthors')
    serializer_class = ScientificPublicationSerializer
    permission_classes = [AllowAny]


class ServiceListAPIView(CacheAPIMixin, ListAPIView):
    cache_timeout = 300
    queryset = Service.objects.filter(service_type='public').order_by('-date').order_by('-date')
    serializer_class = ServiceSerializer
    permission_classes = [AllowAny]
