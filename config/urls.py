from django.urls import include, path

from checks.views import home_view
from config.views import health_view, ping_view


urlpatterns = [
    path("", home_view, name="home"),
    path("ping/", ping_view, name="ping"),
    path("health/", health_view, name="health"),
    path("accounts/", include("accounts.urls")),
    path("checks/", include("checks.urls")),
    path("transactions/", include("transactions.urls")),
]
