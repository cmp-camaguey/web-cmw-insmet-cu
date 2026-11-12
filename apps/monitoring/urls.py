from django.urls import path

from apps.monitoring.views import rum_collect

app_name = 'monitoring'

urlpatterns = [
    path('collect/', rum_collect, name='rum_collect'),
]
