"""Offline, deterministic SYNOP observation simulator.

Generates realistic FM-12 SYNOP reports (``SM{NN}.{HH}`` / ``SI{NN}.{HH}``)
for the six Camagüey provincial stations (78350-78355) so the observation
pipeline ``FileObs -> OpenFileObs -> Descodificador -> GetData`` can be
exercised without the INSMET FTPS server or any network access.

Values follow a plausible tropical diurnal cycle and decode through
``Descodificador`` into the climatic ranges observed in the September
reference data (see the 021 change design for the derivation: D1-D7).

The module is pure standard library: no Django imports, no I/O beyond the
explicit ``generate_to_file``/``generate_all`` write helpers.
"""

import hashlib
import math
import random
from datetime import UTC, date, datetime
from pathlib import Path

from apps.api.data.SynopTelegram import telegram_filename

# Constants of the atmosphere used by the Magnus dew-point inversion (D3),
# matching the formula in ``Descodificador.get_rh``.
_MAGNUS_A = 17.67
_MAGNUS_B = 243.5
_SATURATION_PRESSURE_0C = 6.112  # hPa


class SynopSimulator:
    """Deterministic generator of SYNOP observation files for Camagüey.

    Every call to :meth:`generate` is reproducible: the per-observation random
    stream is seeded with an MD5 digest of ``"{station}:{date}:{hour}"`` so
    re-runs produce byte-identical files (D4). An optional constructor seed is
    mixed into that digest for tests that want a fixed sequence while keeping
    different observations distinct.
    """

    STATIONS = [78350, 78351, 78352, 78353, 78354, 78355]
    HOURS = ['00', '03', '06', '09', '12', '15', '18', '21']
    SM_HOURS = ['00', '06', '12', '18']

    # FM-12 code tables kept self-consistent with apps.api.data.Tablas (D5):
    # dd2 codes {03..09} map to azimuths 45-90 (NE -> E), all valid in Tablas.dd2.
    _DD_CODES = ['03', '04', '05', '06', '07', '08', '09']
    _CL_CODES = ['1', '2', '3']  # low cloud types
    _CM_CODES = ['0', '1', '2']  # medium cloud types
    _CH_CODES = ['1', '2']  # high cloud types

    # "Occasional" rain: ~15 % of observations carry 3 h / 24 h precipitation.
    _RAIN_PROBABILITY = 0.15

    def __init__(self, seed: int | None = None):
        self._seed = seed

    # ------------------------------------------------------------------ API

    def generate(self, station: int, hour: str, obs_date: date | None = None) -> str:
        """Return the complete SYNOP file text for one observation.

        :param station: one of :attr:`STATIONS` (e.g. ``78352``).
        :param hour: one of :attr:`HOURS` (e.g. ``'12'``).
        :param obs_date: observation date (day feeds the ``YY`` header field);
            defaults to today's UTC date.
        :raises ValueError: for an unknown station or hour, listing the valid
            values so the caller can recover.
        """
        if station not in self.STATIONS:
            raise ValueError(f'Invalid station {station}. Valid stations: {self.STATIONS}')
        if hour not in self.HOURS:
            raise ValueError(f'Invalid hour {hour!r}. Valid hours: {self.HOURS}')
        obs_date = obs_date or self._today_utc()
        rng = self._rng_for(station, obs_date, hour)
        values = self._observation_values(rng, hour, self._is_sm(hour))

        header = f'AAXX {obs_date.day:02d}{hour}1'  # YY GG iW, iW=1 -> m/s
        lines = [
            header,
            self._session1_text(station, values),
            self._session2_text(values),
            'NNNN',
        ]
        return '\n'.join(lines) + '\n'

    def generate_to_file(
        self, station: int, hour: str, obs_date: date | None, output_dir: str | Path
    ) -> Path:
        """Write one observation file and return its path.

        The file is named ``SM{NN}.{HH}`` for the main hours
        (00/06/12/18) and ``SI{NN}.{HH}`` otherwise, with
        ``NN = str(station)[2:]`` (e.g. ``SM352.12``).
        """
        text = self.generate(station, hour, obs_date)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        path = output / self.filename_for(station, hour)
        path.write_text(text, encoding='utf-8')
        return path

    def generate_all(
        self,
        output_dir: str | Path,
        stations: list[int] | None = None,
        hours: list[str] | None = None,
        obs_date: date | None = None,
    ) -> list[Path]:
        """Generate the full grid (default: all stations x all hours) and return paths."""
        stations = stations or self.STATIONS
        hours = hours or self.HOURS
        return [
            self.generate_to_file(station, hour, obs_date, output_dir)
            for station in stations
            for hour in hours
        ]

    @classmethod
    def filename_for(cls, station: int, hour: str) -> str:
        """Return the conventional file name for a station/hour pair.

        La regla vive en ``SynopTelegram`` porque el alta manual de observaciones
        escribe el mismo archivo que lee el mapa.
        """
        return telegram_filename(station, hour)

    @classmethod
    def _is_sm(cls, hour: str) -> bool:
        return hour in cls.SM_HOURS

    @staticmethod
    def _today_utc() -> date:
        return datetime.now(UTC).date()

    # --------------------------------------------------------------- seeding

    def _rng_for(self, station: int, obs_date: date, hour: str) -> random.Random:
        """Deterministic per-observation random stream (D4).

        CPython string hashing is salted per process, so a plain ``str`` seed
        would not reproduce across runs; an integer derived from an MD5 digest
        of the observation identity is stable and cheap. An optional
        constructor seed is XOR-mixed in so tests can pin a sequence without
        collapsing distinct observations onto identical bytes.
        """
        digest = hashlib.md5(
            f'{station}:{obs_date.isoformat()}:{hour}'.encode(),
            usedforsecurity=False,
        ).hexdigest()
        base_seed = int(digest, 16)
        if self._seed is not None:
            base_seed ^= self._seed
        return random.Random(base_seed)

    # ------------------------------------------------------------ meteorology

    def _observation_values(self, rng: random.Random, hour: str, is_sm: bool) -> dict:
        """Sample all encoded values for one observation from its RNG stream."""
        temperature = self._diurnal_temperature(rng, hour)
        temperature_tenths = self._quantize_tenths(temperature)
        dew_point_tenths = self._dew_point_tenths(rng, temperature, temperature_tenths)

        cloud_amount = rng.randint(0, 5)  # N = Nh in octas (D5)
        wind_direction = rng.choice(self._DD_CODES)
        wind_speed = rng.randint(0, 7)  # m/s, ff <= 7 (D5)

        station_pressure = rng.uniform(1011.0, 1015.0)  # hPa
        sea_pressure = rng.uniform(1016.0, 1021.0)  # hPa
        tendency_code = rng.randint(0, 8)
        tendency_amount = rng.randint(0, 30)  # tenths of hPa

        raining = rng.random() < self._RAIN_PROBABILITY
        if raining:
            precipitation_3h = '990' if rng.random() < 0.5 else f'{rng.randint(1, 500):03d}'
            present_weather = rng.choice(['60', '61', '80', '81'])
            past_weather = '7'
        else:
            precipitation_3h = '000'
            present_weather = '00'
            past_weather = '0'

        low_cloud = medium_cloud = high_cloud = '0'
        if cloud_amount:
            low_cloud = rng.choice(self._CL_CODES)
            medium_cloud = rng.choice(self._CM_CODES)
            high_cloud = rng.choice(self._CH_CODES)

        precipitation_24h = None
        max_tenths = min_tenths = None
        if is_sm:
            max_tenths = self._quantize_tenths(rng.uniform(29.5, 32.2))  # Tx (D2)
            min_tenths = self._quantize_tenths(rng.uniform(15.5, 24.4))  # Tn (D2)
            if rng.random() < self._RAIN_PROBABILITY:
                precipitation_24h = '9999' if rng.random() < 0.5 else f'{rng.randint(1, 500):04d}'

        return {
            'temperature_tenths': temperature_tenths,
            'dew_point_tenths': dew_point_tenths,
            'cloud_amount': cloud_amount,
            'wind_direction': wind_direction,
            'wind_speed': wind_speed,
            'station_pressure': f'{int(round(station_pressure * 10)) % 10000:04d}',
            'sea_pressure': f'{int(round(sea_pressure * 10)) % 10000:04d}',
            'tendency_code': tendency_code,
            'tendency_amount': tendency_amount,
            'precipitation_3h': precipitation_3h,
            'present_weather': present_weather,
            'past_weather': past_weather,
            'low_cloud': low_cloud,
            'medium_cloud': medium_cloud,
            'high_cloud': high_cloud,
            'max_tenths': max_tenths,
            'min_tenths': min_tenths,
            'precipitation_24h': precipitation_24h,
        }

    def _diurnal_temperature(self, rng: random.Random, hour: str) -> float:
        """Diurnal T: ``23.5 + 5.0*cos(2*pi*(h-20)/24) + U(-1.2, 1.2)`` (D2).

        The cycle peaks near 18-21z and bottoms near 09-12z, and the added
        uniform noise keeps the result inside the September climate window
        [17.0, 30.5] with margin; the clamp is a hard guarantee.
        """
        hour_float = float(hour)
        temperature = 23.5 + 5.0 * math.cos(2 * math.pi * (hour_float - 20) / 24)
        temperature += rng.uniform(-1.2, 1.2)
        return min(30.5, max(17.0, temperature))

    # ------------------------------------------------------------------ encode

    def _session1_text(self, station: int, values: dict) -> str:
        """Session 1 with the fixed iR=1, iX=1 layout (D1).

        Layout: ``IIiii iRixhVV Nddff 1SnTTT 2SnTdTdTd 3PPPP 4PPPP 5appp
        6RRRtR 7wwW1W2 8NhCLCMCH 333 30///``. ``Descodificador`` reads the
        ``8NhCLCMCH`` sky group from token index 10 exactly when ``iR <= 1``
        and the ``7wwW1W2`` group from index 9 when ``iX == 1``; omitting any
        group would shift the indexes and produce garbage.
        """
        cloud = values['cloud_amount']
        cloud_base_height = '9' if cloud == 0 else '8'  # h: no clouds / 2000-2499 m
        visibility = '20'  # VV: 2 km
        return (
            f'{station} 11{cloud_base_height}{visibility} '
            f'{cloud}{values["wind_direction"]}{values["wind_speed"]:03d} '
            f'{self._signed_temp_group("10", values["temperature_tenths"])} '
            f'{self._signed_temp_group("20", values["dew_point_tenths"])} '
            f'3{values["station_pressure"]} 4{values["sea_pressure"]} '
            f'5{values["tendency_code"]}{values["tendency_amount"]:03d} '
            f'6{values["precipitation_3h"]}1 '
            f'7{values["present_weather"]}{values["past_weather"]}{values["past_weather"]} '
            f'8{cloud}{values["low_cloud"]}{values["medium_cloud"]}{values["high_cloud"]} '
            '333 30///'
        )

    def _session2_text(self, values: dict) -> str:
        """Session 2: Tx/Tn groups (SM only) or the short SI layout (D7).

        SM: ``10SnTxTxTx 20SnTnTnTn 30/// 56909 58012 81826=`` plus an optional
        ``7R24R24R24R24`` 24 h precipitation group (``9999`` = trace).
        SI: ``56900 81825=`` with no Tx/Tn groups.
        """
        if values['max_tenths'] is None:
            return '56900 81825='
        groups = [
            self._signed_temp_group('10', values['max_tenths']),
            self._signed_temp_group('20', values['min_tenths']),
            '30///',
            '56909',
            '58012',
            '81826=',
        ]
        if values['precipitation_24h'] is not None:
            groups.append(f'7{values["precipitation_24h"]}')
        return ' '.join(groups)

    # ------------------------------------------------------------------ helpers

    def _dew_point_tenths(
        self, rng: random.Random, temperature: float, temperature_tenths: int
    ) -> int:
        """Dew point from a target RH via the Magnus inversion (D3).

        A target RH in [45, 92] gives ``e = (RH/100)*e_s(T)``; inverting the
        Magnus formula yields Td with ``Td < T`` by construction. The result
        is quantized to 0.1 degrees and hard-clamped below the *encoded*
        temperature so the decoded pair always satisfies ``Td < T`` (the
        decoded RH then lands inside [38, 100]).
        """
        rh_target = rng.uniform(45.0, 92.0)
        saturation = _SATURATION_PRESSURE_0C * math.exp(
            _MAGNUS_A * temperature / (temperature + _MAGNUS_B)
        )
        vapour_pressure = (rh_target / 100.0) * saturation
        log_term = math.log(vapour_pressure / _SATURATION_PRESSURE_0C)
        dew_point = _MAGNUS_B * log_term / (_MAGNUS_A - log_term)
        dew_point_tenths = self._quantize_tenths(round(dew_point, 1))
        return min(dew_point_tenths, temperature_tenths - 1)

    @staticmethod
    def _signed_temp_group(prefix: str, tenths: int) -> str:
        """Encode a temperature group ``{prefix}SnTTT`` with sign 0/1.

        Only positive values occur in this simulator (tropical climate, clamps
        in D2), but the sign is computed so the encoder stays correct for any
        input. ``Descodificador`` reads Sn at index 1 and TTT from index 2.
        """
        sign = 0 if tenths >= 0 else 1
        return f'{prefix}{sign}{abs(tenths):03d}'

    @staticmethod
    def _quantize_tenths(value: float) -> int:
        """Round a Celsius value to the nearest tenth (SYNOP resolution)."""
        return int(round(value * 10))
