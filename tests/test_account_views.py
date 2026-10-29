from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.forms import RegistrationForm
from accounts.models import LoginAttempt, User
from accounts.views import LOGIN_FAILURE_LIMIT, LOGIN_FAILURE_WINDOW


PASSWORD = "Lantern river 482!h"
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name", ["register", "login"])
def test_account_forms_open_without_login(client, name):
    # проверяет открытие форм без создания счетчика входа
    response = client.get(reverse(f"accounts:{name}"))

    assert response.status_code == 200
    assert "csrfmiddlewaretoken" in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert LoginAttempt.objects.count() == 0


def test_register_login_profile_and_logout_with_csrf():
    # проверяет полный путь аккаунта через настоящие запросы
    client = Client(enforce_csrf_checks=True)
    register_url = reverse("accounts:register")
    login_url = reverse("accounts:login")
    profile_url = reverse("accounts:profile")
    logout_url = reverse("accounts:logout")

    response = client.get(profile_url)
    assert response.status_code == 302
    assert response.url.startswith(login_url)
    client.get(register_url)
    response = client.post(
        register_url,
        {
            "username": "anna",
            "password1": PASSWORD,
            "password2": PASSWORD,
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 302
    assert response.url == login_url
    user = User.objects.get(username="anna")
    assert user.check_password(PASSWORD)
    assert SESSION_KEY not in client.session

    response = client.post(
        login_url,
        {
            "username": "anna",
            "password": "wrong password",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    assert SESSION_KEY not in client.session
    assert LoginAttempt.objects.get().failures == 1

    response = client.post(
        login_url + "?next=https://example.com/",
        {
            "username": "anna",
            "password": PASSWORD,
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 302
    assert response.url == profile_url
    assert client.get(profile_url).context["user"].pk == user.pk
    assert LoginAttempt.objects.get().failures == 1
    old_session_key = client.session.session_key

    assert client.get(logout_url).status_code == 405
    assert SESSION_KEY in client.session
    assert client.get(login_url).url == profile_url
    assert client.get(register_url).url == profile_url
    response = client.post(
        logout_url, {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
    )
    assert response.status_code == 302
    assert response.url == login_url
    assert SESSION_KEY not in client.session
    assert not Session.objects.filter(session_key=old_session_key).exists()
    assert client.get(profile_url).status_code == 302


def test_invalid_registration_shows_errors_without_saving(client):
    # проверяет отказ регистрации через обработчик
    response = client.post(
        reverse("accounts:register"),
        {"username": "anna", "password1": PASSWORD, "password2": PASSWORD + "x"},
    )

    assert response.status_code == 200
    assert "password2" in response.context["form"].errors
    assert PASSWORD not in response.content.decode()
    assert User.objects.count() == 0


def test_registration_collision_shows_username_error(client, monkeypatch):
    # проверяет понятную ошибку при занятом перед сохранением логине
    original_is_valid = RegistrationForm.is_valid

    def validate_then_create_competing_user(form):
        # имитирует чужую регистрацию после проверки формы
        valid = original_is_valid(form)
        if valid:
            User.objects.create_user(username="ANNA")
        return valid

    monkeypatch.setattr(RegistrationForm, "is_valid", validate_then_create_competing_user)
    response = client.post(
        reverse("accounts:register"),
        {"username": "anna", "password1": PASSWORD, "password2": PASSWORD},
    )

    assert response.status_code == 200
    assert "username" in response.context["form"].errors
    assert User.objects.count() == 1
    assert User.objects.get().username == "ANNA"
    assert SESSION_KEY not in client.session


@pytest.mark.parametrize("name", ["register", "login", "logout"])
def test_account_posts_require_csrf(name):
    # проверяет защиту всех действий от запроса без токена
    client = Client(enforce_csrf_checks=True)
    if name == "logout":
        user = User.objects.create_user(username="anna")
        client.force_login(user)

    response = client.post(reverse(f"accounts:{name}"), {})

    assert response.status_code == 403
    assert LoginAttempt.objects.count() == 0
    if name == "logout":
        assert SESSION_KEY in client.session
    else:
        assert User.objects.count() == 0


def test_login_limit_blocks_password_check_without_extending_window(client):
    # проверяет ранний отказ после пяти ошибок входа
    User.objects.create_user(username="anna", password=PASSWORD)
    login_url = reverse("accounts:login")
    for _ in range(LOGIN_FAILURE_LIMIT):
        response = client.post(login_url, {"username": "anna", "password": "wrong"})
        assert response.status_code == 200
    attempt = LoginAttempt.objects.get()
    original_start = attempt.window_started_at

    with patch("django.contrib.auth.forms.authenticate") as authenticate:
        response = client.post(login_url, {"username": "anna", "password": PASSWORD})
        authenticate.assert_not_called()

    assert response.status_code == 429
    assert 0 < int(response["Retry-After"]) <= 900
    assert SESSION_KEY not in client.session
    attempt.refresh_from_db()
    assert attempt.failures == LOGIN_FAILURE_LIMIT
    assert attempt.window_started_at == original_start


def test_own_login_does_not_reset_failed_attempts_for_another_account(client):
    # проверяет что свой аккаунт не позволяет обойти лимит ошибок
    User.objects.create_user(username="victim", password=PASSWORD)
    attacker = User.objects.create_user(username="attacker", password=PASSWORD)
    login_url = reverse("accounts:login")
    wrong_credentials = {"username": "victim", "password": "wrong"}

    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        assert client.post(login_url, wrong_credentials).status_code == 200
    attempt = LoginAttempt.objects.get()
    original_start = attempt.window_started_at

    response = client.post(login_url, {"username": "attacker", "password": PASSWORD})
    assert response.status_code == 302
    assert client.session[SESSION_KEY] == str(attacker.pk)
    assert client.post(reverse("accounts:logout")).status_code == 302
    attempt.refresh_from_db()
    assert attempt.failures == LOGIN_FAILURE_LIMIT - 1
    assert attempt.window_started_at == original_start

    assert client.post(login_url, wrong_credentials).status_code == 200
    attempt.refresh_from_db()
    assert attempt.failures == LOGIN_FAILURE_LIMIT
    assert client.post(login_url, wrong_credentials).status_code == 429
    assert SESSION_KEY not in client.session


def test_login_limit_expires_and_ignores_forwarded_ip(client):
    # проверяет срок лимита и недоверие к присланному заголовку
    User.objects.create_user(username="anna", password=PASSWORD)
    attempt = LoginAttempt.objects.create(
        ip_address="127.0.0.1", failures=LOGIN_FAILURE_LIMIT
    )
    login_url = reverse("accounts:login")
    data = {"username": "anna", "password": PASSWORD}

    response = client.post(login_url, data, HTTP_X_FORWARDED_FOR="192.0.2.1")
    assert response.status_code == 429
    response = client.post(login_url, data, REMOTE_ADDR="192.0.2.2")
    assert response.status_code == 302
    assert LoginAttempt.objects.get(ip_address="192.0.2.2").failures == 0
    client.post(reverse("accounts:logout"))

    attempt.window_started_at = timezone.now() - LOGIN_FAILURE_WINDOW - timedelta(seconds=1)
    attempt.save()
    response = client.post(login_url, {"username": "anna", "password": "wrong"})
    assert response.status_code == 200
    attempt.refresh_from_db()
    assert attempt.failures == 1
    assert timezone.now() - attempt.window_started_at < timedelta(seconds=10)


@pytest.mark.django_db(transaction=True)
def test_parallel_login_requests_share_one_counter():
    # проверяет что параллельные ошибки не теряются
    login_url = reverse("accounts:login")
    barrier = Barrier(2)

    def start_request(number):
        # запускает неверный вход в отдельном соединении
        try:
            barrier.wait(timeout=10)
            return Client().post(login_url, {}).status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(start_request, range(2)))

    assert results == [200, 200]
    assert LoginAttempt.objects.get().failures == 2
