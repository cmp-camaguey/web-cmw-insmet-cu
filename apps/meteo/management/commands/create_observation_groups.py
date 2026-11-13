"""Crea los grupos de personal de cada estación con sus permisos.

Idempotente: un segundo pase no duplica grupos y no quita permisos agregados a
mano (usa ``add``, no ``set``). La convención de nombres y los permisos de cada
rol viven en ``apps.meteo.station_roles``.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from apps.meteo.models import Station
from apps.meteo.station_roles import ROLE_PERMISSION_CODES, group_name


class Command(BaseCommand):
    help = 'Crea los grupos Estacion <número> - <rol> con sus permisos de observaciones'

    def handle(self, *args, **options):
        permissions = {
            permission.codename: permission
            for permission in Permission.objects.filter(
                content_type__app_label='meteo',
                codename__in={
                    codename
                    for codenames in ROLE_PERMISSION_CODES.values()
                    for codename in codenames
                },
            )
        }
        missing = {
            codename for codenames in ROLE_PERMISSION_CODES.values() for codename in codenames
        } - set(permissions)
        if missing:
            self.stderr.write(
                self.style.ERROR(
                    f'Faltan permisos de meteo.Observation: {", ".join(sorted(missing))}. '
                    'Ejecute las migraciones antes del comando.'
                )
            )
            return

        stations = list(Station.objects.order_by('number'))
        if not stations:
            self.stdout.write(
                self.style.WARNING('No hay estaciones: ejecute add_stations_data primero.')
            )
            return

        created_count = 0
        for station in stations:
            for role, codenames in ROLE_PERMISSION_CODES.items():
                group, created = Group.objects.get_or_create(name=group_name(station.number, role))
                group.permissions.add(*[permissions[codename] for codename in codenames])
                created_count += int(created)
                state = 'creado' if created else 'actualizado'
                self.stdout.write(f'  · {group.name} ({state})')

        self.stdout.write(
            self.style.SUCCESS(
                f'{len(stations)} estaciones × {len(ROLE_PERMISSION_CODES)} roles: '
                f'{created_count} grupos nuevos.'
            )
        )
