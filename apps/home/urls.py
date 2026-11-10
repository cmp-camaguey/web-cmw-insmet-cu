from django.urls import path

from apps.commercial.models import Service
from apps.core.views.serve_file import PublicServeFileView
from apps.home.views.avisos.alertas_tempranas.views import EarlyWarningListView
from apps.home.views.avisos.ciclones_tropicales.views import TropicalCycloneListView
from apps.home.views.avisos.tormentas.views import StormListView
from apps.home.views.home.views import IndexView
from apps.home.views.institucion.institucional.views import AboutView, MissionView, VisionView
from apps.home.views.institucion.publicaciones.views import ScientificPublicationListView
from apps.home.views.landing.views import LandingView
from apps.home.views.modelos.views import (
    DescargarGifView,
    ImageProxyModeloView,
)
from apps.home.views.pago.views import PagoView
from apps.home.views.satelites.views import ProxyImageView, SateliteView
from apps.home.views.servicios.comerciales.views import (
    CommercialServicesListView,
    PublicCommercialServicesListView,
    ServiceDetailView,
)
from apps.home.views.servicios.publicos.views import PublicServicesListView
from apps.home.views.suscripcion.views import (
    NewsletterConfirmView,
    NewsletterSubscribeView,
    NewsletterUnsubscribeView,
)
from apps.home.views.tiempo.views import WeatherReportDetailView
from apps.meteo.models import Warning as MeteoWarning
from apps.meteo.models import WeatherReport
from apps.publications.models import ScientificPublication

app_name = 'home'

urlpatterns = [
    # Inicio (landing pública en la raíz; home administrativo en /home/)
    path('', LandingView.as_view(), name='landing'),
    path('home/', IndexView.as_view(), name='index'),
    # Suscripción (pública, sin cuenta)
    path('suscripcion/', NewsletterSubscribeView.as_view(), name='newsletter_subscribe'),
    path(
        'suscripcion/confirmar/<uuid:token>/',
        NewsletterConfirmView.as_view(),
        name='newsletter_confirm',
    ),
    path(
        'suscripcion/baja/<uuid:token>/',
        NewsletterUnsubscribeView.as_view(),
        name='newsletter_unsubscribe',
    ),
    # Tiempo
    path(
        'tiempo/hoy/',
        WeatherReportDetailView.as_view(),
        {'report_type': 'today'},
        name='weather_today',
    ),
    path(
        'tiempo/manana/',
        WeatherReportDetailView.as_view(),
        {'report_type': 'tomorrow'},
        name='weather_tomorrow',
    ),
    # Comentario
    path(
        'comentario/tiempo/',
        WeatherReportDetailView.as_view(),
        {'report_type': 'commentary'},
        name='weather_commentary',
    ),
    path(
        'nota/meteorologica/',
        WeatherReportDetailView.as_view(),
        {'report_type': 'note'},
        name='weather_note',
    ),
    # PDF públicos (portal): sirven el archivo inline para que el modal lo
    # incruste. Reutilizan PublicServeFileView (sin login, SAMEORIGIN).
    path(
        'pdf/meteo/<uuid:uuid>/',
        PublicServeFileView.as_view(model=WeatherReport, field='file'),
        name='weather_report_pdf',
    ),
    path(
        'pdf/publicacion/<uuid:uuid>/',
        PublicServeFileView.as_view(model=ScientificPublication, field='pdf'),
        name='publication_pdf',
    ),
    path(
        'pdf/aviso/<uuid:uuid>/',
        PublicServeFileView.as_view(model=MeteoWarning, field='file'),
        name='warning_pdf',
    ),
    path(
        'pdf/servicio/<uuid:uuid>/',
        PublicServeFileView.as_view(model=Service, field='pdf'),
        name='service_pdf',
    ),
    # Avisos
    path('aviso/alerta_temprana/', EarlyWarningListView.as_view(), name='warnings_early'),
    path('aviso/ciclon_tropical/', TropicalCycloneListView.as_view(), name='warnings_tropical'),
    path('aviso/tormenta/', StormListView.as_view(), name='warnings_storm'),
    # Modelos
    # path('modelo/mapas/', MapaView.as_view(), name='models_maps'),
    # path('modelo/meteogram/', MeteogramView.as_view(), name='models_meteogram'),
    # path('modelo/sounding/', SoundingView.as_view(), name='models_sounding'),
    # path(
    #    'modelo/sounding/export/<str:fmt>/',
    #    SoundingExportView.as_view(),
    #    name='models_sounding_export',
    # ),
    # Servicios Públicos
    path('servicios/publicos/', PublicServicesListView.as_view(), name='services_public'),
    # Servicios Comerciales
    path(
        'servicios/comerciales/', CommercialServicesListView.as_view(), name='services_commercial'
    ),
    path(
        'servicios/comerciales/public/',
        PublicCommercialServicesListView.as_view(),
        name='services_commercial_public',
    ),
    path(
        'servicio/comercial/<uuid:uuid>/',
        ServiceDetailView.as_view(),
        name='services_commercial_detail',
    ),
    # Imagen Satélites
    path('imagenes/satelitales/', SateliteView.as_view(), name='satellite'),
    path('proxy_image/', ProxyImageView.as_view(), name='proxy_image'),
    # Pagos en linea
    path('pago_en_linea/', PagoView.as_view(), name='payment'),
    # Institución
    path(
        'institucion/publicaciones_cientificas/',
        ScientificPublicationListView.as_view(),
        name='publications',
    ),
    path('institucion/vision/', VisionView.as_view(), name='institution_vision'),
    path('institucion/mision/', MissionView.as_view(), name='institution_mission'),
    path('institucion/quienes-somos/', AboutView.as_view(), name='institution_about'),
    # Otros
    path('proxy_image_modelo/', ImageProxyModeloView.as_view(), name='proxy_image_modelo'),
    path('modelo/descargar_gif/', DescargarGifView.as_view(), name='models_download_gif'),
]
