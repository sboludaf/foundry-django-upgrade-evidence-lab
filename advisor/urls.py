from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("health/", views.health, name="health"),
    path("api/jobs/", views.create_job, name="create-job"),
    path("api/jobs/<uuid:report_id>/<str:operation>/", views.action, name="job-action"),
    path("api/reports/<uuid:report_id>/", views.report_json, name="report-json"),
    path("reports/<uuid:report_id>/", views.report_html, name="report-html"),
]
