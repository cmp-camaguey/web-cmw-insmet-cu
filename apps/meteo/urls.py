from django.urls import path

from apps.meteo.views import (
    forecast,
    observation,
    province,
    station,
    town,
    warning,
    weather_report,
)

app_name = 'meteo'

urlpatterns = [
    # ──────────────────────────────────────────────
    # Forecast
    # ──────────────────────────────────────────────
    path('pronosticos/', forecast.ForecastsListView.as_view(), name='pronostico_list'),
    path('pronosticos/crear/', forecast.AllForecastCreateView.as_view(), name='pronostico_create'),
    path(
        'pronosticos/<uuid:uuid>/editar/',
        forecast.ForecastUpdateView.as_view(),
        name='pronostico_update',
    ),
    path(
        'pronosticos/<uuid:uuid>/eliminar/',
        forecast.ForecastDeleteView.as_view(),
        name='pronostico_delete',
    ),
    path('pronosticos/excel-json/', forecast.ExcelJSONView.as_view(), name='excel_json'),
    path(
        'pronosticos/plantilla-excel/',
        forecast.ForecastExcelTemplateView.as_view(),
        name='excel_template',
    ),
    # ──────────────────────────────────────────────
    # Weather Reports (today, tomorrow, commentary, note)
    # ──────────────────────────────────────────────
    # Today
    path(
        'tiempo/hoy/',
        weather_report.WeatherReportListView.as_view(),
        {'report_type': 'today'},
        name='tiempo_hoy_list',
    ),
    path(
        'tiempo/hoy/crear/',
        weather_report.WeatherReportCreateView.as_view(),
        {'report_type': 'today'},
        name='tiempo_hoy_create',
    ),
    path(
        'tiempo/hoy/<uuid:uuid>/editar/',
        weather_report.WeatherReportUpdateView.as_view(),
        {'report_type': 'today'},
        name='tiempo_hoy_update',
    ),
    path(
        'tiempo/hoy/<uuid:uuid>/eliminar/',
        weather_report.WeatherReportDeleteView.as_view(),
        {'report_type': 'today'},
        name='tiempo_hoy_delete',
    ),
    # Tomorrow
    path(
        'tiempo/manana/',
        weather_report.WeatherReportListView.as_view(),
        {'report_type': 'tomorrow'},
        name='tiempo_manana_list',
    ),
    path(
        'tiempo/manana/crear/',
        weather_report.WeatherReportCreateView.as_view(),
        {'report_type': 'tomorrow'},
        name='tiempo_manana_create',
    ),
    path(
        'tiempo/manana/<uuid:uuid>/editar/',
        weather_report.WeatherReportUpdateView.as_view(),
        {'report_type': 'tomorrow'},
        name='tiempo_manana_update',
    ),
    path(
        'tiempo/manana/<uuid:uuid>/eliminar/',
        weather_report.WeatherReportDeleteView.as_view(),
        {'report_type': 'tomorrow'},
        name='tiempo_manana_delete',
    ),
    # Commentary
    path(
        'comentarios/tiempo/',
        weather_report.WeatherReportListView.as_view(),
        {'report_type': 'commentary'},
        name='comentario_tiempo_list',
    ),
    path(
        'comentarios/tiempo/crear/',
        weather_report.WeatherReportCreateView.as_view(),
        {'report_type': 'commentary'},
        name='comentario_tiempo_create',
    ),
    path(
        'comentarios/tiempo/<uuid:uuid>/editar/',
        weather_report.WeatherReportUpdateView.as_view(),
        {'report_type': 'commentary'},
        name='comentario_tiempo_update',
    ),
    path(
        'comentarios/tiempo/<uuid:uuid>/eliminar/',
        weather_report.WeatherReportDeleteView.as_view(),
        {'report_type': 'commentary'},
        name='comentario_tiempo_delete',
    ),
    # Note
    path(
        'comentarios/nota-meteorologica/',
        weather_report.WeatherReportListView.as_view(),
        {'report_type': 'note'},
        name='nota_meteorologica_list',
    ),
    path(
        'comentarios/nota-meteorologica/crear/',
        weather_report.WeatherReportCreateView.as_view(),
        {'report_type': 'note'},
        name='nota_meteorologica_create',
    ),
    path(
        'comentarios/nota-meteorologica/<uuid:uuid>/editar/',
        weather_report.WeatherReportUpdateView.as_view(),
        {'report_type': 'note'},
        name='nota_meteorologica_update',
    ),
    path(
        'comentarios/nota-meteorologica/<uuid:uuid>/eliminar/',
        weather_report.WeatherReportDeleteView.as_view(),
        {'report_type': 'note'},
        name='nota_meteorologica_delete',
    ),
    path(
        'tiempo/<uuid:uuid>/pdf/',
        weather_report.WeatherReportFileDownloadView.as_view(),
        name='reporte_download',
    ),
    # ──────────────────────────────────────────────
    # Warnings (early, storm, tropical_cyclone)
    # ──────────────────────────────────────────────
    # Early warning
    path(
        'avisos/alertas-tempranas/',
        warning.WarningListView.as_view(),
        {'warning_type': 'early'},
        name='alerta_temprana_list',
    ),
    path(
        'avisos/alertas-tempranas/crear/',
        warning.WarningCreateView.as_view(),
        {'warning_type': 'early'},
        name='alerta_temprana_create',
    ),
    path(
        'avisos/alertas-tempranas/<uuid:uuid>/editar/',
        warning.WarningUpdateView.as_view(),
        {'warning_type': 'early'},
        name='alerta_temprana_update',
    ),
    path(
        'avisos/alertas-tempranas/<uuid:uuid>/eliminar/',
        warning.WarningDeleteView.as_view(),
        {'warning_type': 'early'},
        name='alerta_temprana_delete',
    ),
    # Storm
    path(
        'avisos/tormentas/',
        warning.WarningListView.as_view(),
        {'warning_type': 'storm'},
        name='tormenta_list',
    ),
    path(
        'avisos/tormentas/crear/',
        warning.WarningCreateView.as_view(),
        {'warning_type': 'storm'},
        name='tormenta_create',
    ),
    path(
        'avisos/tormentas/<uuid:uuid>/editar/',
        warning.WarningUpdateView.as_view(),
        {'warning_type': 'storm'},
        name='tormenta_update',
    ),
    path(
        'avisos/tormentas/<uuid:uuid>/eliminar/',
        warning.WarningDeleteView.as_view(),
        {'warning_type': 'storm'},
        name='tormenta_delete',
    ),
    # Tropical cyclone
    path(
        'avisos/ciclones-tropicales/',
        warning.WarningListView.as_view(),
        {'warning_type': 'tropical_cyclone'},
        name='ciclon_tropical_list',
    ),
    path(
        'avisos/ciclones-tropicales/crear/',
        warning.WarningCreateView.as_view(),
        {'warning_type': 'tropical_cyclone'},
        name='ciclon_tropical_create',
    ),
    path(
        'avisos/ciclones-tropicales/<uuid:uuid>/editar/',
        warning.WarningUpdateView.as_view(),
        {'warning_type': 'tropical_cyclone'},
        name='ciclon_tropical_update',
    ),
    path(
        'avisos/ciclones-tropicales/<uuid:uuid>/eliminar/',
        warning.WarningDeleteView.as_view(),
        {'warning_type': 'tropical_cyclone'},
        name='ciclon_tropical_delete',
    ),
    path(
        'avisos/alerta/<uuid:uuid>/pdf/',
        warning.WarningPDFDownloadView.as_view(),
        name='alerta_download',
    ),
    # ──────────────────────────────────────────────
    # Province
    # ──────────────────────────────────────────────
    path('provincias/', province.ProvinceListView.as_view(), name='provincia_list'),
    path('provincias/crear/', province.ProvinceCreateView.as_view(), name='provincia_create'),
    path(
        'provincias/<uuid:uuid>/editar/',
        province.ProvinceUpdateView.as_view(),
        name='provincia_update',
    ),
    path(
        'provincias/<uuid:uuid>/eliminar/',
        province.ProvinceDeleteView.as_view(),
        name='provincia_delete',
    ),
    # ──────────────────────────────────────────────
    # Town
    # ──────────────────────────────────────────────
    path('municipios/', town.TownListView.as_view(), name='municipio_list'),
    path('municipios/crear/', town.TownCreateView.as_view(), name='municipio_create'),
    path('municipios/<uuid:uuid>/editar/', town.TownUpdateView.as_view(), name='municipio_update'),
    path(
        'municipios/<uuid:uuid>/eliminar/', town.TownDeleteView.as_view(), name='municipio_delete'
    ),
    # ──────────────────────────────────────────────
    # Station
    # ──────────────────────────────────────────────
    path('estaciones/', station.StationListView.as_view(), name='estacion_list'),
    path('estaciones/crear/', station.StationCreateView.as_view(), name='estacion_create'),
    path(
        'estaciones/<uuid:uuid>/editar/',
        station.StationUpdateView.as_view(),
        name='estacion_update',
    ),
    path(
        'estaciones/<uuid:uuid>/eliminar/',
        station.StationDeleteView.as_view(),
        name='estacion_delete',
    ),
    # ──────────────────────────────────────────────
    # Observations (manual SYNOP capture)
    # ──────────────────────────────────────────────
    path('observaciones/', observation.ObservationListView.as_view(), name='observacion_list'),
    path(
        'observaciones/crear/',
        observation.ObservationCreateView.as_view(),
        name='observacion_create',
    ),
    path(
        'observaciones/<uuid:uuid>/',
        observation.ObservationDetailView.as_view(),
        name='observacion_detail',
    ),
    path(
        'observaciones/<uuid:uuid>/editar/',
        observation.ObservationUpdateView.as_view(),
        name='observacion_update',
    ),
    path(
        'observaciones/<uuid:uuid>/eliminar/',
        observation.ObservationDeleteView.as_view(),
        name='observacion_delete',
    ),
]
