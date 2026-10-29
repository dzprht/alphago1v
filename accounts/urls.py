from django.urls import path

from accounts import views


app_name = "accounts"
urlpatterns = [
    path("", views.account_view, name="profile"),
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
]
