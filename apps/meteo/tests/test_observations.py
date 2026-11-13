"""Pruebas de las observaciones manuales (change 035).

Cubre el codificador (ida y vuelta por ``Descodificador``), el formulario
(horas por tipo, extremos obligatorios, unicidad y estaciones propias), el
alcance por estación, los permisos por rol, la escritura/borrado del telegrama,
el modo automático en consulta y el interruptor del panel de entorno.
"""

import tempfile
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.api.data.Descodificador import Descodificador
from apps.api.data.SynopDay import synop_expected_obs_date
from apps.api.data.SynopTelegram import build_telegram, telegram_filename
from apps.api.data.Tablas import Tablas
from apps.core.env_config import EDITABLE_FIELDS
from apps.core.models import ActivityLog, SiteConfiguration
from apps.meteo.forms.observation import ObservationForm
from apps.meteo.models import Observation, Province, Station
from apps.meteo.services.observations import (
    telegram_path,
    write_observation_telegram,
)
from apps.meteo.station_roles import (
    ROLE_CHIEF,
    ROLE_OBSERVER,
    ROLE_PERMISSION_CODES,
    ROLE_REVIEWER,
    group_name,
)

VALUES = {
    'cloud_amount': 5,
    'cloud_base_height': 8,
    'visibility': '20',
    'wind_direction': '07',
    'wind_speed': 4,
    'temperature': 28.5,
    'dew_point': 24.1,
    'station_pressure': 1013.2,
    'sea_pressure': 1015.8,
    'pressure_tendency': 4,
    'pressure_change': 1.2,
    'precipitation_3h': 12.3,
    'precipitation_3h_trace': False,
    'present_weather': '61',
    'past_weather': '7',
    'past_weather_2': '7',
    'low_cloud_type': '1',
    'medium_cloud_type': '2',
    'high_cloud_type': '1',
    'max_temperature': 31.2,
    'min_temperature': 22.4,
    'precipitation_24h': None,
    'precipitation_24h_trace': False,
}


def _decode(text: str, station: int) -> Descodificador:
    """Decodifica el telegrama como lo hace ``OpenFileObs.station()``."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header = lines[0].split()[-1]
    return Descodificador(
        {
            'day': header[:2],
            'hour': header[2:4],
            'number': station,
            'sesion1': lines[1],
            'sesion2': lines[2],
        }
    )


def _disable_maintenance_mode():
    SiteConfiguration.objects.get_or_create(defaults={'maintenance_mode': False})


def _user(username: str) -> User:
    """Crea un usuario con perfil completo.

    ``CheckUserProfileMiddleware`` devuelve a ``/accounts/profile/update/`` a
    quien no tenga correo ni nombre, así que los usuarios de prueba los llevan.
    """
    return User.objects.create_user(
        username,
        f'{username}@example.com',
        'x',
        first_name='Nombre',
        last_name='Apellido',
    )


def _permissions(codenames):
    return [
        Permission.objects.get(content_type__app_label='meteo', codename=codename)
        for codename in codenames
    ]


def _add_role(user, station_number, role):
    group, _ = Group.objects.get_or_create(name=group_name(station_number, role))
    group.permissions.add(*_permissions(ROLE_PERMISSION_CODES[role]))
    user.groups.add(group)


class SynopTelegramTests(SimpleTestCase):
    def test_synoptic_telegram_round_trips_through_the_decoder(self):
        text = build_telegram(VALUES, 78355, '12', date(2026, 11, 13))
        decoded = _decode(text, 78355)

        self.assertEqual(decoded.get_estacion(), 78355)
        self.assertEqual(decoded.get_temp(), 28.5)
        self.assertEqual(decoded.get_td(), 24.1)
        self.assertEqual(decoded.get_ddViento(), Tablas().dd['07'])
        self.assertEqual(decoded.get_ffViento(), '4')
        self.assertEqual(decoded.get_precipitacion(), 12.3)
        self.assertEqual(decoded.get_tempTx(), 31.2)
        self.assertEqual(decoded.get_tempTn(), 22.4)
        self.assertEqual(decoded.get_cielo_cubierto(), 5)
        self.assertEqual(decoded.get_estado_cielo(), 'Parcialmente nublado')

    def test_header_carries_the_observation_day_and_hour(self):
        text = build_telegram(VALUES, 78355, '06', date(2026, 11, 5))
        self.assertTrue(text.startswith('AAXX 05061\n'))
        self.assertTrue(text.endswith('NNNN\n'))

    def test_intermediate_telegram_has_no_extremes_or_24h_rain(self):
        values = {**VALUES, 'max_temperature': None, 'min_temperature': None}
        text = build_telegram(values, 78355, '09', date(2026, 11, 13))
        session2 = text.splitlines()[2]
        self.assertEqual(session2, '56900 81825=')
        self.assertNotIn('7', session2)

    def test_trace_rain_uses_the_key_codes(self):
        values = {**VALUES, 'precipitation_3h_trace': True, 'precipitation_24h_trace': True}
        text = build_telegram(values, 78355, '12', date(2026, 11, 13))
        session1 = text.splitlines()[1]
        self.assertIn(' 69901 ', session1)
        self.assertIn(' 79999', text)

    def test_filename_follows_the_sm_si_convention(self):
        self.assertEqual(telegram_filename(78355, '00'), 'SM355.00')
        self.assertEqual(telegram_filename(78355, '12'), 'SM355.12')
        self.assertEqual(telegram_filename(78350, '03'), 'SI350.03')
        with self.assertRaises(ValueError):
            telegram_filename(78355, '04')


class ObservationFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.province = Province.objects.create(name='Camagüey', code='09')
        cls.station_a = Station.objects.create(
            province=cls.province, name='Alfa', number=78355, latitude=21.4, longitude=-77.9
        )
        cls.station_b = Station.objects.create(
            province=cls.province, name='Beta', number=78350, latitude=21.4, longitude=-77.9
        )
        cls.observer = _user('observador')
        _add_role(cls.observer, 78355, ROLE_OBSERVER)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.tmp.name), OBS_AUTOMATIC=False)
        override.enable()
        self.addCleanup(override.disable)

    def _payload(self, **overrides):
        data = {
            'station': self.station_a.pk,
            'observation_type': 'synoptic',
            'hour': '12',
            'cloud_amount': 5,
            'cloud_base_height': 8,
            'visibility': '20',
            'wind_direction': '07',
            'wind_speed': 4,
            'temperature': '28.5',
            'dew_point': '24.1',
            'station_pressure': '1013.2',
            'sea_pressure': '1015.8',
            'pressure_tendency': 4,
            'pressure_change': '1.2',
            'precipitation_3h': '12.3',
            'present_weather': '61',
            'past_weather': '7',
            'past_weather_2': '7',
            'low_cloud_type': '1',
            'medium_cloud_type': '2',
            'high_cloud_type': '1',
            'max_temperature': '31.2',
            'min_temperature': '22.4',
        }
        data.update(overrides)
        return data

    def test_valid_synoptic_observation_is_saved_with_its_telegram_file(self):
        form = ObservationForm(data=self._payload(), user=self.observer)
        self.assertTrue(form.is_valid(), form.errors)
        observation = form.save()

        self.assertEqual(observation.user, self.observer)
        self.assertEqual(observation.obs_date, synop_expected_obs_date('12'))
        path = telegram_path(observation)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding='utf-8'), observation.telegram)
        self.assertEqual(_decode(observation.telegram, 78355).get_temp(), 28.5)

    def test_hour_must_belong_to_the_observation_type(self):
        form = ObservationForm(data=self._payload(hour='09'), user=self.observer)
        self.assertFalse(form.is_valid())
        self.assertIn('hour', form.errors)

    def test_synoptic_requires_maximum_and_minimum(self):
        form = ObservationForm(
            data=self._payload(max_temperature='', min_temperature=''), user=self.observer
        )
        self.assertFalse(form.is_valid())
        self.assertIn('max_temperature', form.errors)
        self.assertIn('min_temperature', form.errors)

    def test_minimum_cannot_exceed_maximum(self):
        form = ObservationForm(
            data=self._payload(max_temperature='20.0', min_temperature='25.0'),
            user=self.observer,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('min_temperature', form.errors)

    def test_intermediate_observation_drops_the_synoptic_groups(self):
        form = ObservationForm(
            data=self._payload(observation_type='intermediate', hour='09'),
            user=self.observer,
        )
        self.assertTrue(form.is_valid(), form.errors)
        observation = form.save()
        self.assertIsNone(observation.max_temperature)
        self.assertIsNone(observation.min_temperature)
        self.assertEqual(observation.telegram.splitlines()[2], '56900 81825=')

    def test_duplicate_slot_is_rejected(self):
        first = ObservationForm(data=self._payload(), user=self.observer)
        self.assertTrue(first.is_valid(), first.errors)
        first.save()

        second = ObservationForm(data=self._payload(), user=self.observer)
        self.assertFalse(second.is_valid())
        self.assertIn('hour', second.errors)
        self.assertEqual(Observation.objects.count(), 1)

    def test_trace_and_amount_together_are_rejected(self):
        form = ObservationForm(
            data=self._payload(precipitation_3h_trace='on', precipitation_3h='4.0'),
            user=self.observer,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('precipitation_3h', form.errors)

    def test_weather_codes_come_from_the_wmo_tables(self):
        form = ObservationForm(user=self.observer)
        self.assertEqual(
            [code for code, _ in form.fields['present_weather'].choices],
            list(Tablas().ww),
        )
        self.assertEqual(
            [code for code, _ in form.fields['past_weather'].choices],
            list(Tablas().W),
        )
        self.assertEqual(
            [code for code, _ in form.fields['past_weather_2'].choices],
            list(Tablas().W),
        )

        unknown_ww = ObservationForm(data=self._payload(present_weather='100'), user=self.observer)
        self.assertFalse(unknown_ww.is_valid())
        self.assertIn('present_weather', unknown_ww.errors)

        unknown_w = ObservationForm(data=self._payload(past_weather='10'), user=self.observer)
        self.assertFalse(unknown_w.is_valid())
        self.assertIn('past_weather', unknown_w.errors)

    def test_station_choices_are_limited_to_the_user_membership(self):
        form = ObservationForm(user=self.observer)
        self.assertEqual(list(form.fields['station'].queryset), [self.station_a])

        form_b = ObservationForm(user=User.objects.create_superuser('root', 'r@x.com', 'x'))
        self.assertEqual(form_b.fields['station'].queryset.count(), 2)


class ObservationScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()
        cls.province = Province.objects.create(name='Camagüey', code='09')
        cls.station_a = Station.objects.create(
            province=cls.province, name='Alfa', number=78355, latitude=21.4, longitude=-77.9
        )
        cls.station_b = Station.objects.create(
            province=cls.province, name='Beta', number=78350, latitude=21.4, longitude=-77.9
        )
        cls.observer_a = _user('observador-a')
        cls.observer_b = _user('observador-b')
        cls.reviewer_a = _user('revisor-a')
        cls.chief_a = _user('jefe-a')
        cls.stranger = _user('ajeno')
        _add_role(cls.observer_a, 78355, ROLE_OBSERVER)
        _add_role(cls.observer_b, 78350, ROLE_OBSERVER)
        _add_role(cls.reviewer_a, 78355, ROLE_REVIEWER)
        _add_role(cls.chief_a, 78355, ROLE_CHIEF)

        today = synop_expected_obs_date('12')
        cls.observation_a = Observation.objects.create(
            station=cls.station_a,
            observation_type='synoptic',
            hour='12',
            obs_date=today,
            user=cls.observer_a,
            telegram='',
            **VALUES,
        )
        cls.observation_b = Observation.objects.create(
            station=cls.station_b,
            observation_type='synoptic',
            hour='12',
            obs_date=today,
            user=cls.observer_b,
            telegram='',
            **VALUES,
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.tmp.name), OBS_AUTOMATIC=False)
        override.enable()
        self.addCleanup(override.disable)

    def test_list_only_shows_the_user_station(self):
        self.client.force_login(self.observer_a)
        response = self.client.get(reverse('meteo:observacion_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '78355')
        self.assertNotContains(response, '78350')
        self.assertContains(response, 'observador-a')

    def test_user_without_permission_gets_403(self):
        self.client.force_login(self.stranger)
        response = self.client.get(reverse('meteo:observacion_list'))
        self.assertEqual(response.status_code, 403)

    def test_detail_of_another_station_is_not_found(self):
        self.client.force_login(self.observer_a)
        response = self.client.get(
            reverse('meteo:observacion_detail', args=[self.observation_b.uuid])
        )
        self.assertEqual(response.status_code, 404)

    def _payload(self, **overrides):
        data = {
            'station': self.station_a.pk,
            'observation_type': 'synoptic',
            'hour': '12',
            'cloud_amount': 5,
            'cloud_base_height': 8,
            'visibility': '20',
            'wind_direction': '07',
            'wind_speed': 4,
            'temperature': '28.5',
            'dew_point': '24.1',
            'station_pressure': '1013.2',
            'sea_pressure': '1015.8',
            'pressure_tendency': 4,
            'pressure_change': '1.2',
            'precipitation_3h': '12.3',
            'present_weather': '61',
            'past_weather': '7',
            'past_weather_2': '7',
            'low_cloud_type': '1',
            'medium_cloud_type': '2',
            'high_cloud_type': '1',
            'max_temperature': '31.2',
            'min_temperature': '22.4',
        }
        data.update(overrides)
        return data

    def test_observer_creates_an_observation_and_its_file(self):
        self.client.force_login(self.observer_a)
        today = synop_expected_obs_date('12')
        Observation.objects.filter(station=self.station_a, hour='12', obs_date=today).delete()

        response = self.client.post(reverse('meteo:observacion_create'), self._payload())

        self.assertEqual(response.status_code, 302)
        observation = Observation.objects.get(station=self.station_a, hour='12', obs_date=today)
        self.assertEqual(observation.user, self.observer_a)
        self.assertTrue(telegram_path(observation).is_file())
        self.assertTrue(
            ActivityLog.objects.filter(user=self.observer_a, object_id=str(observation.pk)).exists()
        )

    def test_update_and_delete_of_another_station_are_not_found(self):
        self.client.force_login(self.chief_a)
        observation_b = Observation.objects.get(pk=self.observation_b.pk)

        update_url = reverse('meteo:observacion_update', args=[observation_b.uuid])
        self.assertEqual(self.client.get(update_url).status_code, 404)
        self.assertEqual(self.client.post(update_url, self._payload()).status_code, 404)

        delete_url = reverse('meteo:observacion_delete', args=[observation_b.uuid])
        self.assertEqual(self.client.post(delete_url).status_code, 404)
        self.assertTrue(Observation.objects.filter(pk=observation_b.pk).exists())

    def test_rejected_submission_creates_neither_record_nor_file(self):
        self.client.force_login(self.observer_a)
        today = synop_expected_obs_date('12')
        Observation.objects.filter(station=self.station_a, hour='12', obs_date=today).delete()

        response = self.client.post(
            reverse('meteo:observacion_create'),
            self._payload(hour='09', observation_type='synoptic'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Observation.objects.filter(station=self.station_a, hour='09', obs_date=today).exists()
        )
        self.assertFalse((Path(self.tmp.name) / 'obs' / 'SM355.12').exists())
        self.assertFalse((Path(self.tmp.name) / 'obs' / 'SI355.09').exists())

    def test_automatic_mode_keeps_the_section_read_only(self):
        self.client.force_login(self.observer_a)
        with self.settings(OBS_AUTOMATIC=True):
            response = self.client.get(reverse('meteo:observacion_create'))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('meteo:observacion_list'))

    def test_observer_cannot_edit_and_reviewer_can(self):
        self.client.force_login(self.observer_a)
        url = reverse('meteo:observacion_update', args=[self.observation_a.uuid])
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.reviewer_a)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_reviewer_cannot_delete_and_chief_can(self):
        write_observation_telegram(Observation.objects.get(pk=self.observation_a.pk))
        path = telegram_path(self.observation_a)

        self.client.force_login(self.reviewer_a)
        url = reverse('meteo:observacion_delete', args=[self.observation_a.uuid])
        self.assertEqual(self.client.post(url).status_code, 403)

        self.client.force_login(self.chief_a)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Observation.objects.filter(pk=self.observation_a.pk).exists())
        self.assertFalse(path.exists())

    def test_editing_rewrites_the_telegram_and_keeps_the_author(self):
        observation = Observation.objects.get(pk=self.observation_a.pk)
        write_observation_telegram(observation)
        before = observation.telegram

        self.client.force_login(self.reviewer_a)
        response = self.client.post(
            reverse('meteo:observacion_update', args=[observation.uuid]),
            {
                'station': self.station_a.pk,
                'observation_type': 'synoptic',
                'hour': '12',
                'cloud_amount': 5,
                'cloud_base_height': 8,
                'visibility': '20',
                'wind_direction': '07',
                'wind_speed': 4,
                'temperature': '30.0',
                'dew_point': '24.1',
                'station_pressure': '1013.2',
                'sea_pressure': '1015.8',
                'pressure_tendency': 4,
                'pressure_change': '1.2',
                'precipitation_3h': '12.3',
                'present_weather': '61',
                'past_weather': '7',
                'past_weather_2': '7',
                'low_cloud_type': '1',
                'medium_cloud_type': '2',
                'high_cloud_type': '1',
                'max_temperature': '31.2',
                'min_temperature': '22.4',
            },
        )

        self.assertEqual(response.status_code, 302)
        observation.refresh_from_db()
        self.assertEqual(observation.user, self.observer_a)
        self.assertNotEqual(observation.telegram, before)
        self.assertIn('10300', observation.telegram)
        self.assertEqual(
            telegram_path(observation).read_text(encoding='utf-8'), observation.telegram
        )


class ObservationModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _disable_maintenance_mode()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        override.enable()
        self.addCleanup(override.disable)

    def test_file_obs_serves_the_local_telegram_in_manual_mode(self):
        from apps.api.data.FileObs import FileObs

        directory = Path(self.tmp.name) / 'obs'
        directory.mkdir(parents=True)
        path = directory / 'SM355.12'
        path.write_text('AAXX 13121\n', encoding='utf-8')

        with override_settings(OBS_LOCAL_ONLY=False, OBS_AUTOMATIC=False):
            self.assertEqual(FileObs().descargar_archivos_por_hora('12', 78355), str(path))

    def test_file_obs_fails_without_the_telegram_in_manual_mode(self):
        from apps.api.data.FileObs import FileObs

        with (
            override_settings(OBS_LOCAL_ONLY=False, OBS_AUTOMATIC=False),
            self.assertRaises(FileNotFoundError),
        ):
            FileObs().descargar_archivos_por_hora('12', 78355)

    def test_api_does_not_simulate_observations_in_manual_mode(self):
        url = reverse('station-observation', args=['12', 78355])
        with (
            override_settings(OBS_LOCAL_ONLY=True, OBS_AUTOMATIC=False),
            patch('apps.api.data.SynopSimulator.SynopSimulator.generate_to_file') as generate,
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['data']['data'])
        generate.assert_not_called()

    def test_observations_catalog_exposes_the_mode_switch(self):
        spec = next(field for field in EDITABLE_FIELDS if field.key == 'OBS_AUTOMATIC')
        self.assertEqual(spec.group, 'ftp')
        self.assertEqual(spec.kind, 'bool')
        self.assertTrue(spec.default == 'True')


class CreateObservationGroupsCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        province = Province.objects.create(name='Camagüey', code='09')
        Station.objects.create(
            province=province, name='Alfa', number=78355, latitude=21.4, longitude=-77.9
        )
        Station.objects.create(
            province=province, name='Beta', number=78350, latitude=21.4, longitude=-77.9
        )

    def test_command_creates_the_groups_once_and_grants_permissions(self):
        call_command('create_observation_groups', stdout=StringIO())
        groups = Group.objects.filter(name__startswith='Estacion ')
        self.assertEqual(groups.count(), 6)
        observer = groups.get(name=group_name(78355, ROLE_OBSERVER))
        self.assertTrue(observer.permissions.filter(codename='add_observation').exists())
        self.assertFalse(observer.permissions.filter(codename='delete_observation').exists())

        call_command('create_observation_groups', stdout=StringIO())
        self.assertEqual(Group.objects.filter(name__startswith='Estacion ').count(), 6)
