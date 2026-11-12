import logging

from django.conf import settings
from django.utils import timezone

from apps.core.models import SiteConfiguration
from apps.meteo.models import Warning

logger = logging.getLogger(__name__)

# Orden de gravedad para la franja de emergencia. Un ciclón tapa una alerta
# temprana aunque la alerta temprana sea más reciente: la franja solo tiene un
# renglón y debe llevar el hecho más grave, no el último que alguien publicó.
_EMERGENCY_PRIORITY = {'tropical_cyclone': 0, 'storm': 1, 'early': 2}

# Nombre de URL de la lista de cada tipo. Vive acá y no en el modelo porque es
# una ruta del portal público: `apps.meteo` no tiene por qué saber que `home`
# publica los avisos.
_EMERGENCY_URL_NAMES = {
    'tropical_cyclone': 'home:warnings_tropical',
    'storm': 'home:warnings_storm',
    'early': 'home:warnings_early',
}


def _build_emergency_banner(active_warnings):
    """Candidatos de la franja, o `None` si no hay nada vigente que anunciar.

    Devuelve dos listas con el mismo orden: `candidates` con todos los avisos y
    `severe_candidates` con los graves. El landing usa la segunda: un aviso
    temprano por lluvias es rutina y en la portada solo enseña a ignorar la
    franja.

    El orden es el de la franja: primero el más grave y, entre varios de la
    misma gravedad, el más reciente. Cuál de ellos se ve NO lo decide esto sino
    el navegador: cerrar un aviso es un hecho de `localStorage`, y el servidor
    no lo conoce. Entrega la lista completa y el script en línea muestra el
    primero que el visitante no haya cerrado.
    """
    if not active_warnings or not getattr(settings, 'EMERGENCY_BANNER_ENABLED', True):
        return None

    newest_first = sorted(active_warnings, key=lambda warning: warning.date, reverse=True)
    ordered = sorted(
        newest_first,
        key=lambda warning: _EMERGENCY_PRIORITY.get(warning.warning_type, 9),
    )
    total = len(ordered)

    def entry(warning):
        return {
            'warning': warning,
            'url_name': _EMERGENCY_URL_NAMES.get(warning.warning_type, 'home:warnings_early'),
            'total': total,
            'extra': total - 1,
        }

    return {
        'candidates': [entry(warning) for warning in ordered],
        'severe_candidates': [
            entry(warning) for warning in ordered if warning.warning_type != 'early'
        ],
    }


def site_branding(request):
    # Reuse the SiteConfiguration already fetched by MaintenanceModeMiddleware
    # when available; fall back to the singleton lookup otherwise.
    site_config = getattr(request, 'site_config', None)
    if site_config is None:
        site_config = SiteConfiguration.get_instance()
    return {'site_branding': site_config}


def menu_notifications(request):
    context = {}
    now = timezone.now()

    # Una sola consulta para los cuatro datos que salen de `Warning`: tres
    # conteos y el aviso que encabeza la franja de emergencia. Antes eran tres
    # `COUNT(*)` en cada página; trayendo las filas vigentes (unas pocas) el
    # coste es el mismo o menor y la franja sale gratis.
    try:
        active_warnings = list(Warning.objects.filter(valid_until__gte=now))
    except Exception as e:
        logger.warning('Context processor query failed: %s', e)
        context['early_warning_count'] = 0
        context['tropical_cyclone_count'] = 0
        context['storm_warning_count'] = 0
        context['emergency_banner'] = None
    else:
        counts = {'early': 0, 'storm': 0, 'tropical_cyclone': 0}
        for warning in active_warnings:
            counts[warning.warning_type] = counts.get(warning.warning_type, 0) + 1
        context['early_warning_count'] = counts['early']
        context['tropical_cyclone_count'] = counts['tropical_cyclone']
        context['storm_warning_count'] = counts['storm']
        context['emergency_banner'] = _build_emergency_banner(active_warnings)

    # `request.user` no siempre existe: lo pone AuthenticationMiddleware, que va
    # DESPUES de SecurityMiddleware y CommonMiddleware. Si cualquiera de esos dos
    # levanta una excepcion (un Host no permitido, por ejemplo), Django entra al
    # handler de error con una request que todavia no tiene `.user`, y aca el
    # AttributeError tapa el error real con un 500: el cliente ve "error interno"
    # en lugar del 400 que corresponde, y el log muestra el context processor en
    # vez de la causa.
    #
    # O sea: los handlers de error de este proyecto (apps/core/utils.py) renderizan
    # plantillas, y toda plantilla pasa por este context processor. Sin el getattr,
    # la pagina de error se cae sola, que es el peor momento para que se caiga.
    user = getattr(request, 'user', None)

    if user is not None and user.is_authenticated and (user.is_staff or user.is_superuser):
        try:
            from django.db.models import Count, Q

            from apps.commercial.models import ServiceSubscription

            # Una sola consulta agregada en vez de un count por estado: los
            # badges salen en cada página del panel.
            counts = ServiceSubscription.objects.filter(record_active=True).aggregate(
                requested=Count('pk', filter=Q(payment_status='requested')),
                pending=Count('pk', filter=Q(payment_status='pending')),
            )
            context['staff_requested_count'] = counts['requested']
            context['staff_pending_count'] = counts['pending']
        except Exception as e:
            logger.warning('Staff counts query failed: %s', e)
            context['staff_requested_count'] = 0
            context['staff_pending_count'] = 0

    if user is not None and user.is_authenticated and hasattr(user, 'commercial_customer'):
        try:
            from django.db.models import Count, Q

            from apps.commercial.models import ServiceSubscription

            customer = user.commercial_customer
            counts = ServiceSubscription.objects.filter(
                customer=customer, record_active=True
            ).aggregate(
                requested=Count('pk', filter=Q(payment_status='requested')),
                pending=Count('pk', filter=Q(payment_status='pending')),
                active=Count('pk', filter=Q(payment_status='paid')),
                total=Count('pk'),
            )
            context['client_requested_count'] = counts['requested']
            context['client_pending_count'] = counts['pending']
            context['client_active_count'] = counts['active']
            # El total es lo que decide si "Mis Servicios" aparece: el cliente
            # lo necesita con cualquier suscripción, también pagada y vencida.
            context['client_subscriptions_count'] = counts['total']
            context['client_pending_actions'] = (
                context['client_requested_count'] + context['client_pending_count']
            )
        except Exception as e:
            logger.warning('Client counts query failed: %s', e)
            context['client_requested_count'] = 0
            context['client_pending_count'] = 0
            context['client_active_count'] = 0
            context['client_subscriptions_count'] = 0
            context['client_pending_actions'] = 0

    context.setdefault('early_warning_count', 0)
    context.setdefault('tropical_cyclone_count', 0)
    context.setdefault('storm_warning_count', 0)

    return context
