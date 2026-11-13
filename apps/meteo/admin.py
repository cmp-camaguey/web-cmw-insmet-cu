from django.contrib import admin

from apps.meteo.models import (
    ForecastExtendedDay,
    ForecastRegions,
    Forecasts,
    Observation,
    Province,
    Station,
    Town,
    Warning,
    WeatherReport,
)


class ForecastRegionsInline(admin.TabularInline):
    model = ForecastRegions
    extra = 3


class ForecastExtendedDayInline(admin.TabularInline):
    model = ForecastExtendedDay
    extra = 5


@admin.register(Forecasts)
class ForecastsAdmin(admin.ModelAdmin):
    inlines = [ForecastRegionsInline, ForecastExtendedDayInline]
    list_display = ('date', 'sunrise', 'sunset', 'uv_index')


@admin.register(WeatherReport)
class WeatherReportAdmin(admin.ModelAdmin):
    list_display = ('report_type', 'date', 'user')
    list_filter = ('report_type',)


@admin.register(Province)
class ProvinceAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')


@admin.register(Town)
class TownAdmin(admin.ModelAdmin):
    list_display = ('name', 'province')
    list_filter = ('province',)
    search_fields = ('name',)


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ('name', 'number', 'latitude', 'longitude', 'province')
    list_filter = ('province',)
    search_fields = ('name',)


@admin.register(Warning)
class WarningAdmin(admin.ModelAdmin):
    list_display = ('warning_type', 'summary', 'date', 'user')
    list_filter = ('warning_type',)
    search_fields = ('summary',)


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ('station', 'observation_type', 'obs_date', 'hour', 'user', 'updated_at')
    list_filter = ('observation_type', 'station')
    search_fields = ('station__name', 'station__number')
    readonly_fields = ('telegram', 'created_at', 'updated_at')
