"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.utils import My400View, My403View, My404View, My500View

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('apps.api.urls')),
    path('commercial/', include('apps.commercial.urls')),
    path('meteo/', include('apps.meteo.urls')),
    path('config/', include('apps.core.urls')),
    path('notificaciones/', include('apps.core.urls_notifications')),
    path('accounts/', include('apps.user_auth.urls')),
    path('dashboard/', include('apps.dashboard.urls')),
    path('rum/', include('apps.monitoring.urls')),
    path('publications/', include('apps.publications.urls')),
    path('', include('apps.home.urls')),
]

handler400 = My400View.as_view()
handler403 = My403View.as_view()
handler404 = My404View.as_view()
handler500 = My500View.as_view()

if getattr(settings, 'DEBUG_TOOLBAR_ENABLED', False):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += [path('__debug__/', include('debug_toolbar.urls'))]
