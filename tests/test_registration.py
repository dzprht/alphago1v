import pytest

from accounts.forms import RegistrationForm
from accounts.models import User


PASSWORD = "Lantern river 482!h"


@pytest.mark.django_db
def test_registration_creates_user():
    # проверяет создание пользователя через форму
    form = RegistrationForm(
        data={"username": "petya", "password1": PASSWORD, "password2": PASSWORD}
    )

    assert form.is_valid(), form.errors
    assert User.objects.count() == 0

    user = form.save()
    user.refresh_from_db()

    assert user.username == "petya"
    assert user.check_password(PASSWORD)
    assert user.email == ""
    assert not user.is_staff
    assert not user.is_superuser


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("username", "password1", "password2", "error_field", "error_code"),
    [
        ("ivan", PASSWORD, PASSWORD, "username", "unique"),
        ("IVAN", PASSWORD, PASSWORD, "username", "unique"),
        ("petya", PASSWORD, PASSWORD + " other", "password2", "password_mismatch"),
        ("petya", "123", "123", "password2", "password_too_short"),
    ],
)
def test_registration_rejects_invalid_data(username, password1, password2, error_field, error_code):
    # проверяет отказ при ошибках регистрации
    User.objects.create_user(username="ivan")
    form = RegistrationForm(
        data={"username": username, "password1": password1, "password2": password2}
    )

    assert not form.is_valid()
    assert error_field in form.errors
    assert error_code in [error.code for error in form.errors.as_data()[error_field]]
    with pytest.raises(ValueError):
        form.save()
    assert User.objects.count() == 1
