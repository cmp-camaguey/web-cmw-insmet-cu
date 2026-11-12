from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.monitoring'

    def ready(self):
        # Importar el módulo es lo que registra la `periodic_task` de la poda en
        # Huey. Sin este import, la tarea existe pero nunca se agenda.
        from apps.monitoring import tasks  # noqa: F401
