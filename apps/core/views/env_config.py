"""Vista del panel que muestra y edita la configuración del entorno.

Dos límites deliberados:

- **Solo superusuarios.** La página expone infraestructura interna (host de la
  base, hosts permitidos, backend de correo) y escribe material de credenciales.
  Es el mismo criterio de `TaskMonitoringView` y `UpdateStatusView`.
- **El panel no reinicia el servicio.** Escribir la configuración no cambia nada
  en caliente: Django la leyó al arrancar. Reiniciar desde la web es exactamente
  la acción que puede tumbar el sitio, así que la página avisa que los cambios
  aplican al reiniciar y muestra qué valores quedaron pendientes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from apps.core.env_apply import (
    ApplyRequestPendingError,
    apply_status,
    can_request,
    request_apply,
)
from apps.core.env_config import (
    EDITABLE_FIELDS,
    EDITABLE_GROUP_TITLES,
    as_bool,
    build_environment,
    display_value,
)
from apps.core.env_file import is_writable, read_env_file, update_env_file
from apps.core.forms.env_config import EnvironmentConfigForm
from apps.core.models import ActivityLog

logger = logging.getLogger(__name__)


class EnvironmentConfigView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """Muestra y guarda el subconjunto editable del entorno.

    `apply_changes` decide si el POST además pide el reinicio que hace efectivos
    los valores guardados. La subclase `EnvironmentConfigApplyView` lo activa;
    así las dos rutas comparten guardado, validación y auditoría, y no hay dos
    copias de la lógica que puedan divergir.
    """

    template_name = 'pages/core/site/environment.html'
    success_url = reverse_lazy('core:environment_config')
    apply_changes = False

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        env_path = Path(settings.ENV_FILE_PATH)
        file_values = read_env_file(env_path)
        environment = build_environment(settings, os.environ, file_values)
        can_write = is_writable(env_path)

        form = kwargs.get('form') or EnvironmentConfigForm(
            initial=self._initial(file_values, environment['effective']), writable=can_write
        )

        context.update(
            {
                'title': 'Entorno del servidor',
                'parent': 'configuracion',
                'segment': 'core:environment_config',
                'groups': environment['groups'],
                'secrets': environment['secrets'],
                'editable_sections': self._sections(form, environment),
                'form': form,
                'can_write': can_write,
                # Sin la unidad systemd instalada, la petición no tiene quién la
                # recoja. Se deshabilita el botón en vez de dejar que el operador
                # pulse y espere un reinicio que nunca ocurre.
                'can_apply': can_request(settings.ENV_APPLY_REQUEST_FILE),
                'apply_status': apply_status(settings.ENV_APPLY_STATUS_FILE),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        env_path = Path(settings.ENV_FILE_PATH)

        if not is_writable(env_path):
            messages.error(
                request,
                'El proceso web no puede guardar la configuración; queda en solo lectura.',
            )
            return redirect(self.success_url)

        form = EnvironmentConfigForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        changed = self._save(request, form, env_path)
        if changed is None:
            return redirect(self.success_url)

        if self.apply_changes:
            return self._request_apply(request, changed)

        if changed:
            messages.success(
                request,
                'Configuración guardada. Los cambios se aplican al reiniciar el servicio.',
            )
        else:
            messages.info(request, 'No había cambios que guardar.')

        return redirect(self.success_url)

    def _save(self, request, form, env_path: Path) -> list[str] | None:
        """Escribe el archivo y devuelve las claves cambiadas, o `None` si falló."""
        file_values = read_env_file(env_path)
        updates = form.to_updates(current=file_values, settings_obj=settings)

        try:
            changed = update_env_file(env_path, updates)
        except ValueError as error:
            messages.error(request, f'No se pudo guardar: {error}')
            return None
        except OSError:
            logger.exception('No se pudo escribir %s', env_path)
            messages.error(request, 'No se pudo guardar la configuración.')
            return None

        self._log_change(request, changed)
        return changed

    def _request_apply(self, request, changed: list[str]):
        """Deja la petición de reinicio después de guardar.

        Se pide aunque no haya cambios que guardar: el operador pudo editar el
        archivo a mano y usar el botón solo para aplicar.
        """
        try:
            request_apply(settings.ENV_APPLY_REQUEST_FILE, request.user)
        except ApplyRequestPendingError:
            messages.warning(
                request, 'Ya hay un reinicio en curso. Espere a que termine antes de repetirlo.'
            )
            return redirect(self.success_url)
        except OSError:
            logger.exception('No se pudo escribir la petición de reinicio')
            messages.error(
                request,
                'Los cambios se guardaron, pero el servidor no aceptó la petición de '
                'reinicio. Reinicie el servicio desde el servidor.',
            )
            return redirect(self.success_url)

        self._log_apply(request)
        messages.success(
            request,
            'Cambios guardados y reinicio solicitado. El sitio vuelve en unos segundos '
            'con los valores nuevos.',
        )
        return redirect(self.success_url)

    @staticmethod
    def _initial(file_values: dict[str, str], effective: dict[str, str]) -> dict:
        initial = {}
        for spec in EDITABLE_FIELDS:
            if spec.secret:
                # Nunca se precarga una contraseña: el campo vacío significa
                # "no cambiar" y el navegador no recibe el valor guardado.
                initial[spec.key] = ''
                continue
            raw = file_values.get(spec.key)
            if raw is None:
                # Sin la clave en el archivo, se muestra lo que está en
                # ejecución: el operador ve la realidad, no un campo vacío.
                raw = effective.get(spec.key, '')
            if spec.kind == 'bool':
                initial[spec.key] = as_bool(raw)
            elif spec.kind == 'int':
                try:
                    initial[spec.key] = int(str(raw).strip())
                except TypeError, ValueError:
                    initial[spec.key] = None
            else:
                initial[spec.key] = raw
        return initial

    @staticmethod
    def _sections(form, environment: dict) -> list[dict]:
        grouped: dict[str, list[dict]] = {}
        for spec in EDITABLE_FIELDS:
            effective = environment['effective'][spec.key]
            grouped.setdefault(spec.group, []).append(
                {
                    'spec': spec,
                    'field': form[spec.key],
                    'width_class': 'col-12' if spec.width == 'full' else 'col-md-6',
                    'is_secret': spec.secret,
                    'configured': bool(effective),
                    # Un secreto nunca viaja al template, ni siquiera en el
                    # contexto que no se renderiza.
                    'effective': '' if spec.secret else effective,
                    'display': display_value(spec, effective),
                    'pending': environment['pending'][spec.key],
                }
            )
        return [
            {
                'key': key,
                'title': EDITABLE_GROUP_TITLES[key],
                'rows': rows,
                'readonly': environment['section_notes'].get(key, []),
            }
            for key, rows in grouped.items()
        ]

    @staticmethod
    def _log_change(request, changed: list[str]) -> None:
        if not changed:
            return
        ActivityLog.objects.create(
            user=request.user,
            action_flag=ActivityLog.ACTIVITY_CHANGE,
            content_type=None,
            object_id=None,
            object_repr='Entorno del servidor',
            message='Parámetros actualizados: ' + ', '.join(changed),
            ip_address=request.META.get('REMOTE_ADDR') or None,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

    @staticmethod
    def _log_apply(request) -> None:
        ActivityLog.objects.create(
            user=request.user,
            action_flag=ActivityLog.ACTIVITY_CHANGE,
            content_type=None,
            object_id=None,
            object_repr='Entorno del servidor',
            message='Reinicio del servicio solicitado',
            ip_address=request.META.get('REMOTE_ADDR') or None,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )


class EnvironmentConfigApplyView(EnvironmentConfigView):
    """Guarda y además pide el reinicio que aplica los cambios.

    Es la misma vista con una bandera encendida: el guardado, la validación y la
    auditoría son un solo camino. Duplicar el POST para agregar el reinicio
    habría dejado dos versiones de la misma escritura que se separan con el
    primer arreglo.
    """

    apply_changes = True
