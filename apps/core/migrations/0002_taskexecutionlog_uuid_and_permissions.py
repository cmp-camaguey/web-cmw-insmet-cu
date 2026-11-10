import uuid

from django.db import migrations, models


def populate_uuids(apps, schema_editor):
    """Asigna un UUID distinto a cada registro existente.

    El campo se agrega nullable y único en dos pasos: un `AddField` con
    `unique=True` y default calcularía un solo valor y chocaría contra el
    índice único en cuanto hubiera más de una fila.
    """
    TaskExecutionLog = apps.get_model('core', 'TaskExecutionLog')
    for execution in TaskExecutionLog.objects.filter(uuid__isnull=True).iterator():
        execution.uuid = uuid.uuid4()
        execution.save(update_fields=['uuid'])


def rename_permissions(apps, schema_editor):
    """Actualiza las etiquetas de los permisos ya creados.

    `create_permissions` crea los que faltan, pero no renombra los existentes:
    sin este paso, una base ya migrada conservaría las etiquetas en inglés.
    """
    _set_permission_labels(
        apps,
        {
            'view_taskExecutionLog': 'Ver',
            'add_taskExecutionLog': 'Añadir',
            'change_taskExecutionLog': 'Editar',
            'delete_taskExecutionLog': 'Eliminar',
        },
    )


def restore_permission_labels(apps, schema_editor):
    """Devuelve las etiquetas originales al revertir la migración."""
    _set_permission_labels(
        apps,
        {
            'view_taskExecutionLog': 'Can view task execution log',
            'add_taskExecutionLog': 'Can add task execution log',
            'change_taskExecutionLog': 'Can change task execution log',
            'delete_taskExecutionLog': 'Can delete task execution log',
        },
    )


def _set_permission_labels(apps, labels):
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    content_type = ContentType.objects.filter(app_label='core', model='taskexecutionlog').first()
    if content_type is None:
        return
    for codename, name in labels.items():
        Permission.objects.filter(content_type=content_type, codename=codename).update(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='taskexecutionlog',
            name='uuid',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_uuids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='taskexecutionlog',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterModelOptions(
            name='taskexecutionlog',
            options={
                'ordering': ['-enqueued_at'],
                'default_permissions': (),
                'permissions': [
                    ('view_taskExecutionLog', 'Ver'),
                    ('add_taskExecutionLog', 'Añadir'),
                    ('change_taskExecutionLog', 'Editar'),
                    ('delete_taskExecutionLog', 'Eliminar'),
                ],
            },
        ),
        migrations.AlterModelOptions(
            name='companysettings',
            options={
                'verbose_name': 'Datos de la empresa',
                'verbose_name_plural': 'Datos de la empresa',
                'default_permissions': (),
                'permissions': [
                    ('view_companysettings', 'Ver'),
                    ('add_companysettings', 'Añadir'),
                    ('change_companysettings', 'Editar'),
                    ('delete_companysettings', 'Eliminar'),
                ],
            },
        ),
        migrations.RunPython(rename_permissions, restore_permission_labels),
    ]
