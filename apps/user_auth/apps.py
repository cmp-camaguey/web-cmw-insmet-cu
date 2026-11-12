from django.apps import AppConfig


class UserAuthConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.user_auth'
    verbose_name = 'Usuarios'

    def ready(self):
        # Importar el módulo registra los receptores de `Profile`. Sin esto la
        # casilla «Recibir novedades por email» no aplica nada: Django no
        # importa `signals.py` por su cuenta. Es el mismo motivo por el que
        # `CoreConfig.ready()` importa sus tareas.
        from apps.user_auth import signals  # noqa: F401
