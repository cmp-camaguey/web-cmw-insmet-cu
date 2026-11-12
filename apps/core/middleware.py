from django.contrib import messages
from django.contrib.auth.middleware import get_user
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SiteConfiguration
from apps.core.request_context import reset_current_request, set_current_request
from apps.core.timezone_utils import reset_request_time_format, set_request_time_format
from apps.user_auth.models import Profile


class CurrentRequestMiddleware:
    """Deja el request en un `ContextVar` mientras dura la petición.

    Existe por la auditoría: `log_action` no recibe el request en la mayoría de
    las vistas y la fila quedaba sin IP. Con esto el dato se captura igual,
    aunque la vista se olvide de pasarlo.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_current_request(request)
        try:
            return self.get_response(request)
        finally:
            reset_current_request(token)


class CheckUserProfileMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # El redirect a "complete su perfil" no puede atrapar el login ni la
        # baja de sesión. Si los atrapara, quien tiene el perfil a medias queda
        # encerrado: el modo mantenimiento exime `/logout/` justamente para que
        # pueda cerrar sesión, pero este middleware corre antes y lo devuelve al
        # formulario. La excepción tiene que estar en los dos.
        self.exempt_paths = {
            reverse('user_auth:login'),
            reverse('user_auth:logout'),
            reverse('user_auth:profile_update'),
        }

    def __call__(self, request):
        user = get_user(request)
        if user.is_authenticated:
            profile, created = Profile.objects.get_or_create(user=user)

            missing_personal = not user.email or not user.first_name or not user.last_name

            missing_company = False
            if hasattr(user, 'commercial_customer'):
                customer = user.commercial_customer
                required_fields = ['account', 'agency_bank', 'address', 'phone']
                if customer.client_type == customer.ClientType.JURIDICA:
                    required_fields += ['company_name', 'reeup', 'nit']
                for field in required_fields:
                    if not (getattr(customer, field, '') or '').strip():
                        missing_company = True
                        break

            if (missing_personal or missing_company) and request.path not in self.exempt_paths:
                update_url = reverse('user_auth:profile_update')
                if missing_company:
                    messages.warning(
                        request,
                        'Por favor, complete los datos de su cliente antes de continuar.',
                    )
                else:
                    messages.warning(request, 'Por favor, complete su perfil antes de continuar.')
                return redirect(update_url)

        return self.get_response(request)


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # Only the auth paths may stay reachable while the flag is set: an
        # anonymous visitor (or a just-logged-out admin) must be able to log in
        # to restore the site. Everything else, public and admin, shows the
        # maintenance page during the outage.
        self.exempt_paths = [
            reverse('user_auth:login'),
            reverse('user_auth:logout'),
        ]
        self.template_name = 'layouts/maintenance.html'

    def __call__(self, request):
        site_config = SiteConfiguration.objects.first()
        # Reuse the singleton fetch for the rest of the request (e.g. the
        # site_branding context processor) to avoid a redundant DB query.
        request.site_config = site_config
        if site_config and site_config.maintenance_mode and not request.user.is_superuser:
            if request.path in self.exempt_paths:
                if request.user.is_authenticated:
                    messages.warning(request, 'El sitio está en modo mantenimiento.')
                return self.get_response(request)
            # Anonymous visitors and non-superuser staff both hit the
            # maintenance page (503), not the public content.
            return render(request, self.template_name, status=503)
        return self.get_response(request)


class SiteTimezoneMiddleware:
    """Activa la zona horaria configurada en `SiteConfiguration`.

    Va después de `MaintenanceModeMiddleware` para reutilizar el singleton que
    ese middleware ya consultó (`request.site_config`): una sola lectura por
    request. `timezone.activate` es thread-local, y el `finally` la desactiva
    para que el valor no se filtre al siguiente request del mismo hilo.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        site_config = getattr(request, 'site_config', None)
        if site_config is not None:
            timezone.activate(site_config.get_timezone())
            set_request_time_format(site_config.get_time_format())
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
            reset_request_time_format()
