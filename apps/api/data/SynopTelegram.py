"""Codificador de telegramas SYNOP (FM-12) a partir de valores decodificados.

Es el camino inverso de ``Descodificador`` y comparte su contrato: el texto que
produce este módulo tiene que poder leerse con las mismas posiciones que usa
``Descodificador.funcion`` (grupo ``6RRRtR`` en el índice 8, ``7wwW1W2`` en el 9
y ``8NhCLCMCH`` en el 10; el decodificador elige esos índices cuando
``iR <= 1``).

El módulo es puro: sin Django, sin I/O. El nombre del archivo y la regla de la
hora son la única fuente de verdad compartida con el simulador y con el servicio
que escribe en ``media/obs``.

Horas (protocolo AAXX, tal como las usa ``FileObs``):

- ``SM`` — observación sinóptica principal: 00, 06, 12, 18.
- ``SI`` — observación sinóptica intermedia: 03, 09, 15, 21.
"""

from datetime import date

SM_HOURS = ('00', '06', '12', '18')
SI_HOURS = ('03', '09', '15', '21')
ALL_HOURS = ('00', '03', '06', '09', '12', '15', '18', '21')

SYNOPTIC = 'synoptic'
INTERMEDIATE = 'intermediate'


def hours_for(observation_type: str) -> tuple[str, ...]:
    """Horas válidas para el tipo de observación."""
    if observation_type == SYNOPTIC:
        return SM_HOURS
    if observation_type == INTERMEDIATE:
        return SI_HOURS
    raise ValueError(f'Tipo de observación inválido: {observation_type!r}')


def is_synoptic_hour(hour: str) -> bool:
    return hour in SM_HOURS


def telegram_filename(station_number: int, hour: str) -> str:
    """``SM{NN}.{HH}`` para las horas principales y ``SI{NN}.{HH}`` para las intermedias."""
    if hour not in ALL_HOURS:
        raise ValueError(f'Hora inválida: {hour!r}. Use una de: {ALL_HOURS}')
    prefix = 'SM' if is_synoptic_hour(hour) else 'SI'
    return f'{prefix}{str(station_number)[2:]}.{hour}'


def build_telegram(values, station_number: int, hour: str, obs_date: date) -> str:
    """Telegrama completo de una observación.

    ``values`` es un mapeo con las claves del modelo ``Observation`` (valores ya
    en unidades físicas y códigos de clave). ``obs_date`` es el día que embebe
    la cabecera y debe venir de ``SynopDay.synop_expected_obs_date``.
    """
    if hour not in ALL_HOURS:
        raise ValueError(f'Hora inválida: {hour!r}. Use una de: {ALL_HOURS}')

    header = f'AAXX {obs_date.day:02d}{hour}1'
    lines = [
        header,
        _session1(values, station_number),
        _session2(values, hour),
        'NNNN',
    ]
    return '\n'.join(lines) + '\n'


def _session1(values, station_number: int) -> str:
    """``IIiii iRixhVV Nddff 1SnTTT 2SnTdTdTd 3PPPP 4PPPP 5appp 6RRRtR 7wwW1W2 8NhCLCMCH 333 30///``."""
    temp_sign, temp = _signed_tenths(values['temperature'])
    dew_sign, dew = _signed_tenths(values['dew_point'])
    tendency = int(values['pressure_tendency'])
    tendency_amount = min(max(_tenths(values['pressure_change']) or 0, 0), 999)
    rain_3h = _rain_3h(values['precipitation_3h'], values.get('precipitation_3h_trace'))

    return (
        f'{station_number} '
        f'11{int(values["cloud_base_height"])}{str(values["visibility"]).zfill(2)} '
        f'{int(values["cloud_amount"])}{str(values["wind_direction"]).zfill(2)}'
        f'{int(values["wind_speed"]):02d} '
        f'1{temp_sign}{temp:03d} '
        f'2{dew_sign}{dew:03d} '
        f'3{_pressure(values["station_pressure"])} '
        f'4{_pressure(values["sea_pressure"])} '
        f'5{tendency}{tendency_amount:03d} '
        f'6{rain_3h}1 '
        f'7{str(values["present_weather"]).zfill(2)}'
        f'{int(values["past_weather"])}{int(values["past_weather_2"])} '
        f'8{int(values["cloud_amount"])}'
        f'{int(values["low_cloud_type"])}'
        f'{int(values["medium_cloud_type"])}'
        f'{int(values["high_cloud_type"])} '
        '333 30///'
    )


def _session2(values, hour: str) -> str:
    """Sinóptica con Tx/Tn (y 24 h si hay dato); intermedia sin esos grupos."""
    if not is_synoptic_hour(hour):
        return '56900 81825='

    max_sign, max_tenths = _signed_tenths(values['max_temperature'])
    min_sign, min_tenths = _signed_tenths(values['min_temperature'])
    groups = [
        f'10{max_sign}{max_tenths:03d}',
        f'20{min_sign}{min_tenths:03d}',
        '30///',
        '56909',
        '58012',
        '81826=',
    ]
    rain_24h = _rain_24h(values.get('precipitation_24h'), values.get('precipitation_24h_trace'))
    if rain_24h is not None:
        groups.append(f'7{rain_24h}')
    return ' '.join(groups)


def _signed_tenths(value) -> tuple[int, int]:
    """``(Sn, TTT)`` para un valor en °C con un decimal."""
    tenths = _tenths(value) or 0
    return (0 if tenths >= 0 else 1), abs(tenths)


def _tenths(value) -> int | None:
    """Décimas enteras de un valor decimal (``23.5`` → ``235``)."""
    if value is None or value == '':
        return None
    return int(round(float(value) * 10))


def _pressure(value) -> str:
    """``PPPP`` en décimas de hPa, sin el dígito del millar."""
    tenths = _tenths(value) or 0
    return f'{tenths % 10000:04d}'


def _rain_3h(value, trace) -> str:
    """``RRR``: ``990`` traza, ``000`` sin precipitación, resto en décimas."""
    if trace:
        return '990'
    tenths = _tenths(value) or 0
    if tenths <= 0:
        return '000'
    return f'{min(tenths, 989):03d}'


def _rain_24h(value, trace) -> str | None:
    """``RRRR``: ``9999`` traza; ``None`` omite el grupo (sin dato)."""
    if trace:
        return '9999'
    tenths = _tenths(value)
    if tenths is None:
        return None
    if tenths <= 0:
        return '0000'
    return f'{min(tenths, 9998):04d}'
