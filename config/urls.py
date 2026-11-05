from django.urls import include, path


urlpatterns = [path("", include("advisor.urls"))]
