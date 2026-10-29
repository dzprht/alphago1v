from django.urls import include, path


urlpatterns = [
    path("accounts/", include("accounts.urls")),
    path("checks/", include("checks.urls")),
    path("transactions/", include("transactions.urls")),
]
