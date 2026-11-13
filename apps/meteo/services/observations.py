"""Genera y guarda el telegrama SYNOP de una observación capturada a mano.

El archivo va al mismo directorio y con el mismo nombre que usa la descarga del
FTP (``MEDIA_ROOT/obs/SM{NN}.{HH}`` / ``SI{NN}.{HH}``), porque es el que lee
``FileObs`` y, a través de él, el mapa de observaciones. No hay tarea asíncrona:
el dato tiene que estar en disco para la siguiente petición del mapa.
"""

from pathlib import Path

from django.conf import settings

from apps.api.data.SynopTelegram import build_telegram

# Campos del modelo que consume el codificador, en el orden del formulario.
VALUE_FIELDS = (
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
)


def observation_values(observation) -> dict:
    """Valores del modelo con las claves que espera el codificador."""
    return {field: getattr(observation, field) for field in VALUE_FIELDS}


def observation_telegram(observation) -> str:
    """Telegrama que corresponde al estado actual de la observación."""
    return build_telegram(
        observation_values(observation),
        observation.station.number,
        observation.hour,
        observation.obs_date,
    )


def telegram_directory() -> Path:
    return Path(settings.MEDIA_ROOT) / 'obs'


def telegram_path(observation) -> Path:
    """Ruta del archivo que lee el mapa para esa estación y hora."""
    return telegram_directory() / observation.filename


def write_observation_telegram(observation) -> Path:
    """Escribe el telegrama ya calculado en ``observation.telegram``."""
    path = telegram_path(observation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(observation.telegram, encoding='utf-8')
    return path


def delete_observation_telegram(observation) -> None:
    """Borra el archivo, si existe. La fila se elimina por separado."""
    path = telegram_path(observation)
    if path.is_file():
        path.unlink()
