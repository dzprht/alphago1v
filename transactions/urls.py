from django.urls import path

from transactions import views


app_name = "transactions"
urlpatterns = [
    path("", views.history_view, name="history"),
    path("delete/", views.delete_view, name="delete"),
    path("template/", views.template_view, name="template"),
]
