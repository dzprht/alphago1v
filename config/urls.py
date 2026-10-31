from django.urls import include, path

from checks.views import home_view


urlpatterns = [
    path("", home_view, name="home"),
    path("accounts/", include("accounts.urls")),
    path("checks/", include("checks.urls")),
    path("transactions/", include("transactions.urls")),
]
