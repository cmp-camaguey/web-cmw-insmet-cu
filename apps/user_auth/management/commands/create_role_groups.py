"""Crea los grupos ``<Módulo> - <Ver|Editar|Gestionar>`` con sus permisos.

Idempotente: un segundo pase no duplica grupos y no quita permisos agregados a
mano (usa ``add``, no ``set``). La convención de nombres y los permisos de cada
nivel viven en ``apps.user_auth.roles`` y se derivan de ``Model._meta``.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError

from apps.user_auth.models import GroupProfile
from apps.user_auth.roles import (
    MODULES,
    ROLES,
    group_name,
    migrated_model_keys,
    module_app_labels,
    module_permissions,
    outside_schema_models,
    schema_permissions,
    unclassified_codenames,
)


class Command(BaseCommand):
    help = 'Crea los grupos <Módulo> - <Ver|Editar|Gestionar> con sus permisos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Imprime el plan sin escribir grupos ni permisos',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Un permiso que no sigue view_/add_/change_/delete_ no entra en ningún
        # nivel. Se nombra para que no quede fuera de la jerarquía sin que nadie
        # lo note al agregar el modelo.
        for module in MODULES:
            outside = unclassified_codenames(codename for _, codename in module_permissions(module))
            if outside:
                self.stderr.write(
                    self.style.WARNING(
                        f'{module}: permisos fuera de la jerarquía '
                        f'(sin prefijo view_/add_/change_/delete_): {", ".join(outside)}'
                    )
                )

        migrated = migrated_model_keys()
        for module in MODULES:
            for key in outside_schema_models(module, migrated):
                self.stderr.write(
                    self.style.WARNING(f'{key}: modelo fuera del esquema migrado, se omite.')
                )

        app_labels = sorted(
            {app_label for module in MODULES for app_label in module_app_labels(module)}
        )
        available = {
            (permission.content_type.app_label, permission.codename): permission
            for permission in Permission.objects.select_related('content_type').filter(
                content_type__app_label__in=app_labels
            )
        }

        requested = {role: schema_permissions(*role, migrated) for role in ROLES}
        missing = sorted(
            {
                f'{app_label}.{codename}'
                for pairs in requested.values()
                for app_label, codename in pairs
                if (app_label, codename) not in available
            }
        )
        if missing:
            raise CommandError(
                f'Faltan permisos declarados por los modelos: {", ".join(missing)}. '
                'Ejecute las migraciones antes del comando.'
            )

        created_count = 0
        for module, level in ROLES:
            name = group_name(module, level)
            permissions = [available[pair] for pair in requested[(module, level)]]

            if dry_run:
                self.stdout.write(f'  · {name} ({len(permissions)} permisos, sin escribir)')
                continue

            group, created = Group.objects.get_or_create(name=name)
            group.permissions.add(*permissions)
            # La señal post_save de Group ya crea el perfil; este get_or_create
            # repara los grupos que existían antes de la señal, sin los cuales la
            # pantalla de Grupos no puede editar el grupo.
            GroupProfile.objects.get_or_create(group=group)
            created_count += int(created)
            self.stdout.write(f'  · {name} ({"creado" if created else "actualizado"})')

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'{len(ROLES)} grupos de rol: {len(ROLES)} por crear (dry-run).')
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f'{len(ROLES)} grupos de rol en {len(MODULES)} módulos: {created_count} nuevos.'
            )
        )
