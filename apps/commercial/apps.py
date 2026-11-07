from django.apps import AppConfig


class CommercialConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.commercial'
    verbose_name = 'Comercial'

    def ready(self):
        # Importar el módulo es lo que registra la `periodic_task` en el
        # TaskRegistry de Huey. Sin esto el consumer arranca sin conocer el
        # recordatorio de facturas y la tarea nunca se programa. Es el mismo
        # motivo por el que `CoreConfig.ready()` y `MeteoConfig.ready()` importan
        # sus módulos de tareas.
        from apps.commercial import tasks  # noqa: F401
