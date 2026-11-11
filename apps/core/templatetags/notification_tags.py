"""Avisos operativos en el navbar del dashboard (cambio 026, fase 3).

Inclusion tag y no context processor: el navbar del dashboard se renderiza en
cada pagina del panel, pero un context processor correria tambien en el portal
publico, donde no hay avisos que mostrar. Asi la consulta la paga solo quien
puede verlos.
"""

from django import template
from django.conf import settings

register = template.Library()

# Tope visual del badge. Pasado ese numero el contador deja de informar: dos
# digitos ya no cambian la decision de nadie y desbordan el icono.
BADGE_CAP = 9


@register.inclusion_tag('includes/dashboard/notifications_navbar.html', takes_context=True)
def notifications_navbar(context):
    """Datos del icono de avisos, o `visible=False` para no renderizarlo.

    Desde el cambio 030 la campana es de cualquiera con sesión: los admins ven
    sus avisos operativos y los clientes los de sus facturas y servicios. No hace
    falta comprobar rol porque el queryset siempre filtra por `recipient`: un
    usuario sin avisos ve el icono con el badge apagado, que es el estado
    correcto y no una fuga.

    Lo que sí se comprueba es el interruptor global del sistema: apagarlo es una
    decisión de operación, no un permiso.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)

    if not getattr(settings, 'NOTIFICATIONS_ENABLED', True):
        return {'visible': False}
    if user is None or not getattr(user, 'is_authenticated', False):
        return {'visible': False}

    # Import local: el tag se carga en el navbar de todas las paginas del panel,
    # y no queremos que importar la plantilla arrastre los modelos de `core`
    # en el arranque de la app.
    from apps.core.notifications import cached_unread_count, visible_for

    # El mismo numero que sirve `notifications:unread`: si el navbar contara por
    # su cuenta, el badge recien pintado y el primer refresco del JS podrian
    # discrepar y el numero parpadearia sin que nada haya cambiado.
    unread = cached_unread_count(user) or 0
    # Solo no leidas: el desplegable es una bandeja de entrada, no el historial.
    # Si mezclara leidas, el badge y la lista dirian cosas distintas y una no
    # leida antigua podria quedar fuera del tope por culpa de diez leidas nuevas.
    # El historial completo vive en `dashboard:notifications`.
    recent = list(
        visible_for(user).filter(read_at__isnull=True)[: settings.NOTIFICATIONS_MAX_VISIBLE]
    )

    return {
        'visible': True,
        'unread': unread,
        'badge': f'{BADGE_CAP}+' if unread > BADGE_CAP else unread,
        'recent': recent,
    }
