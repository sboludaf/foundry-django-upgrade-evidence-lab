from django.urls import path

from . import views


urlpatterns = [
    path("", views.index, name="index"),
    path("health/", views.health, name="health"),
    path("api/analyze/", views.analyze, name="analyze"),
    path("api/reports/<uuid:report_id>/", views.report_json, name="report-json"),
    path("reports/<uuid:report_id>/", views.report_html, name="report-html"),
]
