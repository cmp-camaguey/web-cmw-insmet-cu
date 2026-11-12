# data-safety: reviewed: rellena `uuid` en filas existentes; no borra ni reescribe datos.
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


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0006_notificationpreference'),
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
    ]
