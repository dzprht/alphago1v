import pytest
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import User
from checks.models import Check


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("authenticated", [False, True])
def test_home_and_five_forms_are_public(client, authenticated):
    # проверяет страницы для гостя и аккаунта
    if authenticated:
        client.force_login(User.objects.create_user(username="anna"))
    response = client.get(reverse("home"))
    assert response.status_code == 200
    for kind in Check.Kind:
        url = reverse("checks:new", args=[kind])
        assert url in response.content.decode()
        page = client.get(url)
        assert page.status_code == 200
        assert page.context["form"].kind == kind
        assert "no-store" in page["Cache-Control"]
    assert client.post(reverse("checks:new", args=["text"]), {"text": "Пример"}).status_code == 503
    assert Check.objects.count() == 0
    assert User.objects.count() == int(authenticated)
    assert Session.objects.count() == int(authenticated)


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("text", {"text": "<script>alert(1)</script>"}),
        ("url", {"url": "https://example.com"}),
        ("transfer", {"amount": "1.50", "purpose": "unknown"}),
    ],
)
def test_input_errors_and_missing_analyzer_do_not_save_data(client, kind, data):
    # проверяет ввод и честный отказ без сохранения проверки
    url = reverse("checks:new", args=[kind])
    assert client.post(url, {}).status_code == 400
    response = client.post(url, data)
    assert response.status_code == 503
    assert response.context["form"].is_valid()
    assert "Анализ пока не подключен" in response.content.decode()
    assert "<script>" not in response.content.decode()
    assert Check.objects.count() == User.objects.count() == Session.objects.count() == 0


def test_media_uploads_are_disabled(client):
    # проверяет что обработка файлов пока выключена
    for kind in ["audio", "screenshot"]:
        url = reverse("checks:new", args=[kind])
        assert "<fieldset disabled>" in client.get(url).content.decode()
        response = client.post(url, {"audio": SimpleUploadedFile("sample.wav", b"test")})
        assert response.status_code == 503
        assert not response.context["form"].is_bound
    assert Check.objects.count() == Session.objects.count() == 0


def test_input_csrf_methods_and_unknown_mode(client):
    # проверяет токен методы запроса и неверный режим
    url = reverse("checks:new", args=["text"])
    protected = Client(enforce_csrf_checks=True)
    protected.get(url)
    assert protected.post(url, {"text": "Пример"}).status_code == 403
    response = protected.post(
        url, {"text": "Пример", "csrfmiddlewaretoken": protected.cookies["csrftoken"].value}
    )
    assert response.status_code == 503
    assert client.post(reverse("home"), {}).status_code == 405
    assert client.put(url).status_code == 405
    assert client.get(reverse("checks:new", args=["unknown"])).status_code == 404
