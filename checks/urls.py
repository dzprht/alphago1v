from django.urls import path

from checks import views


app_name = "checks"
urlpatterns = [
    path("", views.history_view, name="history"),
    path("new/<str:kind>/", views.input_view, name="new"),
    path("<int:check_id>/", views.detail_view, name="detail"),
]
