"""Pertenencia del personal a las estaciones, resuelta por grupos de Django.

Cada estación se conforma con un jefe, un revisor y sus observadores. El
vínculo no vive en el perfil ni en una tabla nueva: es un grupo con nombre
convencional,

    Estacion <número de 5 dígitos> - <Jefe|Revisor|Observador>

administrable desde la pantalla de Grupos que ya existe. El nombre es el
contrato: si alguien lo renombra, esa estación pierde a su miembro, y por eso la
convención se documenta y el comando `create_observation_groups` la genera.

Los roles se traducen en permisos del modelo ``Observation``:

- observador: ver y añadir.
- revisor: además, editar (corrige).
- jefe: además, eliminar.

La restricción de estación se aplica en las vistas con ``user_station_numbers``.
El superusuario queda fuera de la regla: ve y gestiona todas.
"""

from __future__ import annotations

import re

ROLE_CHIEF = 'Jefe'
ROLE_REVIEWER = 'Revisor'
ROLE_OBSERVER = 'Observador'
ROLES = (ROLE_CHIEF, ROLE_REVIEWER, ROLE_OBSERVER)

# Permisos que otorga cada rol sobre meteo.Observation.
ROLE_PERMISSION_CODES = {
    ROLE_OBSERVER: ('view_observation', 'add_observation'),
    ROLE_REVIEWER: ('view_observation', 'add_observation', 'change_observation'),
    ROLE_CHIEF: (
        'view_observation',
        'add_observation',
        'change_observation',
        'delete_observation',
    ),
}

GROUP_PATTERN = re.compile(r'^Estacion (?P<number>\d{5}) - (?P<role>Jefe|Revisor|Observador)$')


def group_name(station_number: int, role: str) -> str:
    """Nombre del grupo para una estación y un rol."""
    if role not in ROLES:
        raise ValueError(f'Rol inválido: {role!r}. Use uno de: {ROLES}')
    return f'Estacion {int(station_number)} - {role}'


def parse_group_name(name: str) -> tuple[int, str] | None:
    """``(número, rol)`` de un grupo de estación, o ``None`` si no lo es."""
    match = GROUP_PATTERN.match(name or '')
    if not match:
        return None
    return int(match.group('number')), match.group('role')


def user_station_roles(user) -> dict[int, set[str]]:
    """Estaciones del usuario con los roles que tiene en cada una."""
    stations: dict[int, set[str]] = {}
    for name in user.groups.values_list('name', flat=True):
        parsed = parse_group_name(name)
        if parsed is None:
            continue
        number, role = parsed
        stations.setdefault(number, set()).add(role)
    return stations


def user_station_numbers(user) -> set[int]:
    """Números de estación a los que pertenece el usuario."""
    return set(user_station_roles(user))


def is_station_member(user, station_number: int) -> bool:
    """¿El usuario (o el superusuario) puede operar sobre esa estación?"""
    if user.is_superuser:
        return True
    return int(station_number) in user_station_numbers(user)


def has_station_role(user, station_number: int, roles) -> bool:
    """¿Tiene alguno de esos roles en la estación?"""
    if user.is_superuser:
        return True
    if isinstance(roles, str):
        roles = (roles,)
    return bool(user_station_roles(user).get(int(station_number), set()) & set(roles))
