from unittest.mock import Mock

import pytest
from django.db import DatabaseError


@pytest.fixture
def no_model_loading(monkeypatch):
    # запрещает загрузку моделей для служебных запросов
    loader = Mock(side_effect=AssertionError("Модели не должны загружаться"))
    for name in ("analysis.runtime.load_analyzer", "analysis.runtime.load_media_model",
                 "checks.views.load_analyzer", "checks.views.load_media_model"):
        monkeypatch.setattr(name, loader)
    return loader


def test_ping_is_public_and_does_not_use_database_or_models(client, no_model_loading):
    # отвечает без входа базы данных и тяжелых моделей
    response = client.get("/ping/")
    assert response.status_code == 200 and response.content == b"pong"
    assert response["Content-Type"].startswith("text/plain")
    assert "no-store" in response["Cache-Control"]
    no_model_loading.assert_not_called()


@pytest.mark.django_db
def test_health_checks_database_without_loading_models(client, no_model_loading, django_assert_num_queries):
    # проверяет доступность базы одним простым запросом
    with django_assert_num_queries(1):
        response = client.get("/health/")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert "no-store" in response["Cache-Control"]
    no_model_loading.assert_not_called()


def test_health_returns_unavailable_without_database_details(client, monkeypatch, no_model_loading):
    # сообщает об ошибке базы без ее внутренних данных
    secret = "private database credentials"
    monkeypatch.setattr("config.views.connection.cursor", Mock(side_effect=DatabaseError(secret)))
    response = client.get("/health/")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}
    assert secret not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    no_model_loading.assert_not_called()


@pytest.mark.parametrize("url", ["/ping/", "/health/"])
def test_health_endpoints_reject_post(client, url, no_model_loading):
    # не разрешает изменяющие запросы к проверке состояния
    response = client.post(url, {})
    assert response.status_code == 405 and response["Allow"] == "GET"
    no_model_loading.assert_not_called()
