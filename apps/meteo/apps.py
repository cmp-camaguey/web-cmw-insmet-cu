from django.apps import AppConfig


class MeteoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.meteo'
    verbose_name = 'Meteorología'

    def ready(self):
        # Importar el módulo es lo que registra la `periodic_task` en el
        # TaskRegistry. Sin esto el consumer de Huey no conoce la tarea y el
        # recordatorio de avisos por vencer nunca se programa. Es el mismo
        # motivo por el que `CoreConfig.ready()` importa `apps.core.tasks`.
        from apps.meteo import tasks  # noqa: F401
