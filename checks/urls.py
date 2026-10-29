from django.urls import path

from checks import views


app_name = "checks"
urlpatterns = [
    path("", views.history_view, name="history"),
    path("<int:check_id>/", views.detail_view, name="detail"),
]
