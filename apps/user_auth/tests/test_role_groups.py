"""Pruebas de los grupos de rol por jerarquía (`create_role_groups`)."""

from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.meteo.models import Forecasts
from apps.user_auth.roles import (
    LEVELS,
    MODULES,
    ROLES,
    group_name,
    migrated_model_keys,
    module_permissions,
    role_permissions,
    schema_permissions,
    unclassified_codenames,
)


def _as_set(pairs):
    return {f'{app_label}.{codename}' for app_label, codename in pairs}


class RoleDefinitionTests(TestCase):
    def test_every_module_has_the_three_levels(self):
        self.assertEqual(len(ROLES), len(MODULES) * len(LEVELS))

    def test_hierarchy_is_accumulative_inside_each_module(self):
        for module in MODULES:
            view = _as_set(role_permissions(module, 'Ver'))
            edit = _as_set(role_permissions(module, 'Editar'))
            manage = _as_set(role_permissions(module, 'Gestionar'))
            self.assertLess(view, edit, module)
            self.assertLess(edit, manage, module)

    def test_view_level_only_grants_read_permissions(self):
        for module in MODULES:
            for app_label, codename in role_permissions(module, 'Ver'):
                self.assertTrue(codename.startswith('view_'), f'{app_label}.{codename}')

    def test_every_model_permission_is_covered_by_exactly_one_module(self):
        covered = {}
        for module in MODULES:
            for app_label, codename in role_permissions(module, 'Gestionar'):
                key = f'{app_label}.{codename}'
                self.assertNotIn(key, covered, f'{key} aparece en {covered.get(key)} y {module}')
                covered[key] = module

        declared = set()
        for module in MODULES:
            declared |= _as_set(module_permissions(module))
        self.assertEqual(declared, set(covered))

    def test_unclassified_reports_codenames_outside_the_four_prefixes(self):
        self.assertEqual(
            unclassified_codenames(['view_forecast', 'add_forecast', 'approve_forecast']),
            ['approve_forecast'],
        )

    def test_schema_permissions_ignore_models_outside_the_migrations(self):
        # Un modelo que sólo existe en las pruebas no está en el esquema migrado:
        # no aparece en el estado de las migraciones y no puede recibir permisos.
        only_scientific = {('publications', 'scientificpublication')}

        permissions = schema_permissions('Publicaciones', 'Ver', only_scientific)

        self.assertIn(('publications', 'view_scientific_publication'), permissions)
        self.assertNotIn(('publications', 'view_author'), permissions)


class CreateRoleGroupsCommandTests(TestCase):
    def run_command(self, **options):
        out = StringIO()
        call_command('create_role_groups', stdout=out, **options)
        return out.getvalue()

    def test_creates_every_group_with_its_permissions(self):
        self.run_command()
        migrated = migrated_model_keys()
        for module, level in ROLES:
            group = Group.objects.get(name=group_name(module, level))
            got = {f'{p.content_type.app_label}.{p.codename}' for p in group.permissions.all()}
            self.assertEqual(got, _as_set(schema_permissions(module, level, migrated)), group.name)
            self.assertTrue(hasattr(group, 'profile'), group.name)

    def test_second_run_does_not_duplicate_or_remove_manual_permissions(self):
        self.run_command()
        group = Group.objects.get(name='Comercial - Ver')
        extra = Permission.objects.get(content_type__app_label='meteo', codename='delete_forecast')
        group.permissions.add(extra)

        self.run_command()

        self.assertEqual(Group.objects.filter(name='Comercial - Ver').count(), 1)
        self.assertIn(extra, group.permissions.all())

    def test_leaves_existing_groups_alone(self):
        Group.objects.create(name='Clientes')
        Group.objects.create(name='Estacion 00001 - Jefe')

        self.run_command()

        self.assertFalse(Group.objects.get(name='Clientes').permissions.exists())
        self.assertEqual(Group.objects.filter(name__startswith='Estacion').count(), 1)

    def test_dry_run_writes_nothing(self):
        output = self.run_command(dry_run=True)

        self.assertIn('Meteorologia - Ver', output)
        self.assertFalse(Group.objects.filter(name='Meteorologia - Ver').exists())

    def test_fails_closed_when_a_declared_permission_is_missing(self):
        permission = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(Forecasts),
            codename='view_forecast',
        )
        permission.delete()

        with self.assertRaises(CommandError):
            self.run_command()
