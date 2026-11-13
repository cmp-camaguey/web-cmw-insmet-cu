"""Roles de acceso al dashboard, resueltos por grupos de Django.

Cada módulo del menú lateral tiene tres niveles acumulativos y un grupo con
nombre convencional,

    <Módulo> - <Ver|Editar|Gestionar>

que se administra desde la pantalla de Grupos existente. El nombre es el
contrato: si alguien lo renombra, quien estaba en ese rol pierde sus permisos,
y por eso la convención se documenta y el comando `create_role_groups` la
genera.

La jerarquía es acumulativa DENTRO de cada módulo y nunca entre módulos:

- Ver: permisos ``view_*``.
- Editar: además ``add_*`` y ``change_*``.
- Gestionar: además ``delete_*``.

No hay un grupo que herede de otro módulo a propósito: un rol comercial con
``Gestionar`` no puede borrar pronósticos que nadie le pidió tocar. Quien
necesite dos áreas recibe dos grupos.

Los permisos no se escriben a mano: se derivan de ``Model._meta`` en cada
corrida, así un modelo nuevo queda cubierto sin editar este archivo. Un permiso
que no sigue los cuatro prefijos se informa en lugar de quedar fuera en
silencio.

Los grupos ``Clientes`` (portal del cliente) y ``Estacion <número> - <rol>``
(alcance por estación, ver ``apps.meteo.station_roles``) son de otro contrato y
este módulo no los toca.
"""

from __future__ import annotations

from django.apps import apps
from django.db import connection
from django.db.migrations.loader import MigrationLoader

LEVEL_VIEW = 'Ver'
LEVEL_EDIT = 'Editar'
LEVEL_MANAGE = 'Gestionar'
LEVELS = (LEVEL_VIEW, LEVEL_EDIT, LEVEL_MANAGE)

_VIEW_PREFIXES = ('view_',)
_EDIT_PREFIXES = _VIEW_PREFIXES + ('add_', 'change_')
_MANAGE_PREFIXES = _EDIT_PREFIXES + ('delete_',)

LEVEL_PREFIXES = {
    LEVEL_VIEW: _VIEW_PREFIXES,
    LEVEL_EDIT: _EDIT_PREFIXES,
    LEVEL_MANAGE: _MANAGE_PREFIXES,
}

ALLOWED_PREFIXES = _MANAGE_PREFIXES

# Módulos alineados con las secciones del menú lateral. ``apps`` toma todos los
# modelos de la app; ``models`` una lista explícita; ``exclude`` los descarta.
MODULES = {
    'Meteorologia': {'apps': ('meteo',), 'exclude': ('meteo.observation',)},
    'Observaciones': {'models': ('meteo.observation',)},
    'Comercial': {'apps': ('commercial',)},
    'Publicaciones': {'apps': ('publications',)},
    'Configuracion': {'apps': ('core',)},
    'Usuarios': {'apps': ('user_auth', 'auth')},
}

# Orden estable de creación y de listado.
ROLES = tuple((module, level) for module in MODULES for level in LEVELS)


def model_key(model) -> str:
    """``app_label.model_name`` de un modelo, para comparar con el esquema migrado."""
    return f'{model._meta.app_label}.{model._meta.model_name}'


def module_models(module: str) -> list:
    """Modelos que cubre un módulo, en el orden declarado."""
    spec = MODULES[module]
    if 'models' in spec:
        return [apps.get_model(key) for key in spec['models']]

    excluded = set(spec.get('exclude', ()))
    models = []
    for app_label in spec['apps']:
        models.extend(apps.get_app_config(app_label).get_models())
    return [model for model in models if model_key(model) not in excluded]


def module_app_labels(module: str) -> set[str]:
    """Etiquetas de app con las que se buscan sus permisos en la base."""
    return {model._meta.app_label for model in module_models(module)}


def model_permissions(model) -> list[tuple[str, str]]:
    """``(app_label, codename)`` de un modelo: custom declarados + default."""
    opts = model._meta
    codenames = [codename for codename, _ in opts.permissions]
    codenames += [f'{action}_{opts.model_name}' for action in opts.default_permissions]
    return [(opts.app_label, codename) for codename in codenames]


def module_permissions(module: str) -> list[tuple[str, str]]:
    """Todos los permisos de los modelos de un módulo."""
    permissions = []
    for model in module_models(module):
        permissions.extend(model_permissions(model))
    return permissions


def migrated_model_keys() -> set[tuple[str, str]]:
    """``(app_label, model_name)`` de los modelos que existen en el esquema.

    El registro de apps incluye modelos que solo existen durante las pruebas
    (``apps/core/tests/test_file_handling.py``): no están en las migraciones, no
    tienen permisos en la base y no pueden recibir un grupo. El estado de las
    migraciones es la única fuente que distingue esos modelos de un modelo real
    al que le faltan sus permisos, y esa diferencia decide entre omitir y fallar.
    """
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    return set(loader.project_state().models.keys())


def outside_schema_models(module: str, migrated_keys: set[tuple[str, str]]) -> list[str]:
    """Claves de los modelos del módulo que no están en el esquema migrado."""
    return sorted(
        model_key(model)
        for model in module_models(module)
        if (model._meta.app_label, model._meta.model_name) not in migrated_keys
    )


def schema_permissions(
    module: str, level: str, migrated_keys: set[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Permisos del nivel, limitados a los modelos que existen en el esquema."""
    if level not in LEVELS:
        raise ValueError(f'Nivel inválido: {level!r}. Use uno de: {LEVELS}')
    if module not in MODULES:
        raise ValueError(f'Módulo inválido: {module!r}. Use uno de: {tuple(MODULES)}')

    allowed = set()
    for model in module_models(module):
        if (model._meta.app_label, model._meta.model_name) in migrated_keys:
            allowed.update(model_permissions(model))

    prefixes = LEVEL_PREFIXES[level]
    return sorted(pair for pair in allowed if pair[1].startswith(prefixes))


def unclassified_codenames(codenames) -> list[str]:
    """Codenames que no entran en ningún nivel por no seguir los prefijos."""
    return sorted(codename for codename in codenames if not codename.startswith(ALLOWED_PREFIXES))


def role_permissions(module: str, level: str) -> list[tuple[str, str]]:
    """Permisos ``(app_label, codename)`` que otorga un módulo en un nivel."""
    if level not in LEVELS:
        raise ValueError(f'Nivel inválido: {level!r}. Use uno de: {LEVELS}')
    if module not in MODULES:
        raise ValueError(f'Módulo inválido: {module!r}. Use uno de: {tuple(MODULES)}')

    prefixes = LEVEL_PREFIXES[level]
    return [
        (app_label, codename)
        for app_label, codename in module_permissions(module)
        if codename.startswith(prefixes)
    ]


def group_name(module: str, level: str) -> str:
    """Nombre del grupo para un módulo y un nivel."""
    if module not in MODULES:
        raise ValueError(f'Módulo inválido: {module!r}. Use uno de: {tuple(MODULES)}')
    if level not in LEVELS:
        raise ValueError(f'Nivel inválido: {level!r}. Use uno de: {LEVELS}')
    return f'{module} - {level}'
