from django.core.management.base import BaseCommand

from apps.monitoring.prune import prune_monitoring


class Command(BaseCommand):
    help = 'Borra métricas de peticiones y datos del beacon fuera de retención.'

    def handle(self, *args, **options):
        deleted = prune_monitoring()
        self.stdout.write(
            'Registros borrados: '
            f'{deleted["request_metrics"]} peticiones, '
            f'{deleted["pageviews"]} páginas vistas, '
            f'{deleted["web_vitals"]} métricas web, '
            f'{deleted["javascript_errors"]} errores JavaScript.'
        )
