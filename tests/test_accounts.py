import pytest
from django.contrib.auth import authenticate

from accounts.models import User


@pytest.mark.django_db
def test_user_is_saved():
    # проверяет сохранение пользователя в базе
    user = User.objects.create_user(username="ivan", email="ivan@example.com")

    saved_user = User.objects.get(pk=user.pk)

    assert saved_user.username == "ivan"
    assert saved_user.email == "ivan@example.com"


@pytest.mark.django_db
def test_password_authentication():
    # проверяет пароль пользователя при входе
    password = "Test password 478!"
    user = User.objects.create_user(username="anna", password=password)
    user.refresh_from_db()

    assert user.password != password
    assert authenticate(username="anna", password=password) == user
    assert authenticate(username="anna", password="wrong password") is None
