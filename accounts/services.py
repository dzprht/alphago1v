from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm


def login_user(request, data):
    # проверяет данные и выполняет вход
    form = AuthenticationForm(request=request, data=data)
    if form.is_valid():
        login(request, form.get_user())
    return form
