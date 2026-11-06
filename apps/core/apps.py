import json
import logging
import traceback

from django.apps import AppConfig
from django.db.models import F
from django.utils import timezone
from huey import signals as huey_signals

from config.huey import huey

logger = logging.getLogger(__name__)

# Funciones seguras de re-encolar. Sus argumentos son ligeros, JSON-serializables
# y NO contienen datos sensibles (sin recipients, cuerpos de correo ni adjuntos).
# Solo para estas se persiste func_args (necesario para reintentar el trabajo) y
# su dotted path (para resolver la función con importlib).
#   clave  -> el nombre con que Huey registra la tarea (task.name / task_id)
#   valor  -> full dotted path de la función
RETRYABLE_TASKS = {
    'generate_invoice_pdf_and_email_task': 'apps.core.tasks.generate_invoice_pdf_and_email_task',
}


def _func_name(task):
    """Full dotted path of the task function (e.g. 'apps.core.tasks.send_email_task').

    En la señal Huey el objeto task NO expone .func; solo tenemos task.name (corto)
    y los args. Para poder re-encolar con importlib resolvemos el dotted path a
    partir del retryable whitelist; el resto guarda solo el nombre corto (informativo).
    """
    try:
        name = getattr(task, 'name', '') or ''
        return RETRYABLE_TASKS.get(name, name)
    except Exception:
        return ''


def _safe_args_json(task):
    """JSON-serialized args for RETRYABLE_TASKS, else empty.

    Only tasks in RETRYABLE_TASKS get their arguments persisted, and only when
    they are safely JSON-serializable; anything else keeps func_args empty so a
    retire from the dashboard is impossible (no sensitive data stored).
    """
    name = _func_name(task)
    if name not in RETRYABLE_TASKS.values():
        return ''
    try:
        args = getattr(task, 'args', None) or ()
        kwargs = getattr(task, 'kwargs', None) or {}
        payload = {'args': list(args), 'kwargs': dict(kwargs)}
        json.dumps(payload)  # validate serializable
        return json.dumps(payload)
    except TypeError, ValueError:
        return ''


def _safe_args(task):
    """Truncated, sanitized repr of task args.

    Only the first positional arg is shown and everything else is omitted so
    that sensitive values (recipients, email bodies, attachment contents) are
    never persisted to the execution log.
    """
    try:
        args = getattr(task, 'args', None) or ()
        if not args:
            return f'{task.name}()'[:512]
        head = repr(args[0])[:200]
        rest = ', (...) omitido' if len(args) > 1 else ''
        return f'{task.name}({head}{rest})'[:512]
    except Exception:
        return ''


def _tb(exc):
    """Truncated traceback string for an exception, or empty when none."""
    if exc is None:
        return ''
    try:
        return ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))[:4000]
    except Exception:
        return str(exc)


def _logical_key(task):
    """Clave estable del trabajo lógico, o '' si la tarea no tiene una.

    Huey da un `task.id` nuevo en cada encolado, así que ese id identifica un
    *intento*, no un trabajo. Para las tareas que sabemos repetir sobre la
    misma fila de negocio, la clave se deriva de esa fila: la tarea de
    factura siempre habla de la misma factura, la repita la cuenta que la
    repita. Sin esta clave, cada reintento desde el dashboard crea una fila
    nueva y el error original se queda ahí para siempre.
    """
    name = getattr(task, 'name', '') or ''
    if name != 'generate_invoice_pdf_and_email_task':
        return ''
    args = getattr(task, 'args', None) or ()
    if not args:
        return ''
    return f'invoice:{args[0]}'


def _summary(task, logical_key):
    """Descripción legible de la tarea, o '' si no se puede resolver.

    Solo para mostrar en la tabla: 'Factura 2026-0001 · Cliente X'. Si la
    factura ya no existe, devuelve el prefijo solo en vez de romper el
    encolado, porque el log de tareas no es el lugar para fallar.
    """
    if not logical_key.startswith('invoice:'):
        return ''
    invoice_uuid = logical_key.split(':', 1)[1]
    try:
        from apps.commercial.models import Invoice

        invoice = (
            Invoice.objects.select_related('customer', 'customer__user')
            .only(
                'number',
                'customer__company_name',
                'customer__identity_document',
                'customer__user__first_name',
                'customer__user__last_name',
                'customer__user__email',
            )
            .get(uuid=invoice_uuid)
        )
    except Exception:
        return 'Factura (eliminado)'
    return f'Factura {invoice.number} · {_cliente_label(invoice.customer)}'[:255]


def _cliente_label(customer):
    """Nombre con el que una persona puede reconocer al cliente de un vistazo.

    `company_name` solo existe para personas jurídicas: en una natural está
    vacío, y el resumen quedaba como 'Factura X · None', que no identifica a
    nadie justo en el caso más común del portal. Para una natural caemos a
    sus nombres, y de ahí al correo, que siempre existe.
    """
    if customer is None:
        return 'sin cliente'
    nombre = (customer.company_name or '').strip()
    if nombre:
        return nombre
    user = customer.user
    if user is None:
        return customer.identity_document or 'sin identificar'
    nombre = f'{user.first_name or ""} {user.last_name or ""}'.strip()
    return nombre or user.email or customer.identity_document or 'sin identificar'


# Los receivers de Huey viven en el `Signal` interno de la instancia, no en las
# constantes de `huey.signals` (esas son solo strings). `ready()` se puede
# invocar más de una vez en el mismo proceso —los tests lo hacen—, y cada llamada
# volvería a conectar los cinco receivers sobre `config.huey.huey`, que es un
# singleton. Sin esta bandera, un solo encolado ejecutaría el mismo receiver N
# veces y el log de tareas acumularía intentos de más. Registrar una sola vez por
# proceso es correcto: `config.huey` no se recarga en ningún momento (el perfil de
# tests solo recarga `config.settings.base`).
_TASK_SIGNALS_REGISTERED = False


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    verbose_name = 'Configuración'

    def ensure_media_root(self):
        """Crea MEDIA_ROOT con `parents=True, exist_ok=True`.

        Idempotente y sin estado: lee `settings.MEDIA_ROOT` en cada llamada, así que
        los tests pueden apuntarlo a un temporal y restaurarlo después.
        """
        from pathlib import Path

        from django.conf import settings

        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)

    def register_task_signals(self):
        """Conecta los receivers que escriben `TaskExecutionLog` a las señales de Huey.

        Idempotente: la primera llamada conecta los cinco receivers y las siguientes
        son no-op (ver `_TASK_SIGNALS_REGISTERED`). Los bodies no cambian; solo se
        movieron acá desde `ready()`.
        """
        global _TASK_SIGNALS_REGISTERED

        if _TASK_SIGNALS_REGISTERED:
            return

        from apps.core.models import TaskExecutionLog

        @huey.signal(huey_signals.SIGNAL_ENQUEUED)
        def on_enqueued(signal, task):
            try:
                logical_key = _logical_key(task)
                defaults = {
                    'task_name': task.name,
                    'status': TaskExecutionLog.STATUS_ENQUEUED,
                    'enqueued_at': timezone.now(),
                    'args_repr': _safe_args(task),
                    'func_name': _func_name(task),
                    'func_args': _safe_args_json(task),
                    'summary': _summary(task, logical_key),
                    'logical_key': logical_key,
                }
                if logical_key:
                    # Reintento de un trabajo conocido: se reutiliza la fila
                    # existente. `task_id` se refresca al id nuevo porque las
                    # señales siguientes (executing/complete/retrying/error)
                    # buscan por `task_id`, no por `logical_key`: sin esto la
                    # fila reutilizada queda en `enqueued` para siempre, sin
                    # registrar ni un resultado. `attempts` se arrastra para
                    # que se vea cuántos intentos lleva.
                    existing = (
                        TaskExecutionLog.objects.filter(logical_key=logical_key)
                        .order_by('-enqueued_at')
                        .first()
                    )
                    if existing:
                        defaults['attempts'] = F('attempts') + 1
                        defaults['task_id'] = task.id
                        TaskExecutionLog.objects.filter(pk=existing.pk).update(**defaults)
                        return
                TaskExecutionLog.objects.update_or_create(
                    task_id=task.id,
                    defaults=defaults,
                )
            except Exception:
                logger.exception('on_enqueued: fallo al registrar la tarea %s', task.id)

        @huey.signal(huey_signals.SIGNAL_EXECUTING)
        def on_executing(signal, task):
            try:
                TaskExecutionLog.objects.filter(task_id=task.id).update(
                    status=TaskExecutionLog.STATUS_EXECUTING,
                    started_at=timezone.now(),
                )
            except Exception:
                logger.exception('on_executing: fallo al actualizar %s', task.id)

        @huey.signal(huey_signals.SIGNAL_COMPLETE)
        def on_complete(signal, task):
            try:
                TaskExecutionLog.objects.filter(task_id=task.id).update(
                    status=TaskExecutionLog.STATUS_SUCCESS,
                    finished_at=timezone.now(),
                )
            except Exception:
                logger.exception('on_complete: fallo al actualizar %s', task.id)

        @huey.signal(huey_signals.SIGNAL_RETRYING)
        def on_retrying(signal, task, exc=None):
            try:
                # Huey emits SIGNAL_RETRYING without the exception, so we must
                # preserve the error_message/traceback that on_error already
                # stored for the failed attempt instead of overwriting them.
                update = {
                    'status': TaskExecutionLog.STATUS_RETRYING,
                    'attempts': F('attempts') + 1,
                    'finished_at': timezone.now(),
                }
                if exc is not None:
                    update['error_message'] = str(exc)[:2000]
                    update['traceback'] = _tb(exc)
                TaskExecutionLog.objects.filter(task_id=task.id).update(**update)
            except Exception:
                logger.exception('on_retrying: fallo al actualizar %s', task.id)

        @huey.signal(huey_signals.SIGNAL_ERROR)
        def on_error(signal, task, exc=None):
            try:
                TaskExecutionLog.objects.filter(task_id=task.id).update(
                    status=TaskExecutionLog.STATUS_ERROR,
                    attempts=F('attempts') + 1,
                    error_message=str(exc)[:2000],
                    traceback=_tb(exc),
                    finished_at=timezone.now(),
                )
                logger.error('Huey task %s failed: %s', task.name, exc)
            except Exception:
                logger.exception('on_error: fallo al registrar el error de %s', task.id)

            # Aviso al admin, pero SOLO en el fallo final. Huey emite
            # SIGNAL_ERROR en cada intento y deja en `task.retries` los que
            # todavía le quedan; si quedan, la cola puede resolverlo sola en el
            # reintento siguiente y avisar acá mandaría un error por algo que se
            # arregló solo. Es el mismo cálculo que usa Huey para decidir si
            # reencola (`final = not task.retries`).
            if getattr(task, 'retries', 0):
                return
            try:
                from apps.core.notifications import notify_task_failure

                notify_task_failure(
                    task.name,
                    task_id=task.id,
                    logical_key=_logical_key(task),
                    error=str(exc) if exc is not None else '',
                )
            except Exception:
                # El aviso es información derivada: si falla, el log de la tarea
                # sigue siendo la fuente de verdad y no puede perderse por esto.
                logger.exception('on_error: fallo al crear el aviso de %s', task.id)

        _TASK_SIGNALS_REGISTERED = True

    def ready(self):
        self.ensure_media_root()

        # Importar el módulo de tareas es lo que registra los `@huey.task()` en el
        # TaskRegistry. Sin esto, el consumer de `run_huey.sh` y de
        # `webcmp-huey.service` arranca sin ninguna tarea conocida y cada dequeue
        # explota con `HueyException: <tarea> not found in TaskRegistry`: el worker
        # queda vivo, pero jamás procesa nada. El proceso del consumer no importa
        # vistas, así que nadie más lo hace por él.
        from apps.core import tasks  # noqa: F401

        self.register_task_signals()
