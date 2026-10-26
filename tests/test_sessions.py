import pytest
from django.contrib.auth import SESSION_KEY, get_user, logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session

from accounts.models import User
from accounts.services import login_user


PASSWORD = "Lantern river 482!h"
pytestmark = pytest.mark.django_db


@pytest.fixture
def session_request(rf):
    # создает запрос с пустой сессией
    request = rf.post("/accounts/login/")
    request.session = SessionStore()
    request.user = AnonymousUser()
    return request


def test_login_creates_session(session_request, rf):
    # проверяет вход и сохранение сессии
    user = User.objects.create_user(username="anna", password=PASSWORD)

    form = login_user(session_request, {"username": "anna", "password": PASSWORD})

    assert form.is_valid(), form.errors
    assert session_request.user == user
    session_request.session.save()

    next_request = rf.get("/")
    next_request.session = SessionStore(session_key=session_request.session.session_key)

    assert get_user(next_request) == user


@pytest.mark.parametrize(
    ("username", "password", "is_active"),
    [
        ("anna", "wrong password", True),
        ("unknown", PASSWORD, True),
        ("anna", PASSWORD, False),
    ],
)
def test_login_rejects_invalid_credentials(session_request, username, password, is_active):
    # проверяет отказ при неверных данных входа
    User.objects.create_user(username="anna", password=PASSWORD, is_active=is_active)

    form = login_user(session_request, {"username": username, "password": password})

    assert not form.is_valid()
    assert form.non_field_errors()
    assert session_request.user.is_anonymous
    assert SESSION_KEY not in session_request.session


def test_logout_invalidates_session(session_request, rf):
    # проверяет завершение сессии после выхода
    user = User.objects.create_user(username="anna", password=PASSWORD)
    login_user(session_request, {"username": "anna", "password": PASSWORD})
    assert session_request.user == user
    session_request.session.save()
    old_session_key = session_request.session.session_key

    logout(session_request)

    assert session_request.user.is_anonymous
    assert not Session.objects.filter(session_key=old_session_key).exists()

    next_request = rf.get("/")
    next_request.session = SessionStore(session_key=old_session_key)

    assert get_user(next_request).is_anonymous
