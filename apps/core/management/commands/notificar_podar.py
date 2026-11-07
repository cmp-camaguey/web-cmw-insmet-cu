"""Poda de avisos operativos (cambio 026, fase 4).

La logica vive en `apps.core.notifications.prune_notifications` porque la usan
dos entradas: este comando (para correrlo a mano o desde cron) y la tarea
periodica de Huey. Duplicar las tres ventanas de retencion en dos sitios era la
forma segura de que se separaran con el tiempo.
"""

from django.core.management.base import BaseCommand

from apps.core.notifications import prune_notifications


class Command(BaseCommand):
    help = 'Borra avisos vencidos y los que ya pasaron su ventana de retencion.'

    def handle(self, *args, **options):
        deleted = prune_notifications()
        self.stdout.write(
            'Avisos borrados: '
            f'{deleted["expired"]} vencidos, '
            f'{deleted["read"]} leídos fuera de retención, '
            f'{deleted["unread"]} no leídos fuera de retención.'
        )
