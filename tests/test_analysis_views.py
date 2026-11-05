from unittest.mock import Mock

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from accounts.models import User
from checks.models import Check, CheckFeedback, TrainingConsent


pytestmark = pytest.mark.django_db
RESULT = {
    "status": "ok", "risk": 0, "sufficiency": "sufficient", "analyzer_version": "analysis-test",
    "findings": [{"description": "Пример", "source_ref": {
        "text": "<script>alert(1)</script>", "url": "https://example.com/?a=<script>",
    }}], "recommendations": ["Проверьте просьбу"], "limitations": ["Пример ограничения"],
}


@pytest.fixture
def configured_analyzer(monkeypatch, settings):
    # подставляет готовый анализатор без загрузки весов
    settings.ANALYZER_DIR = "/test/models"
    analyzer = Mock()
    analyzer.analyze.return_value = (RESULT, {})
    loader = Mock(return_value=analyzer)
    monkeypatch.setattr("checks.views.load_analyzer", loader)
    return analyzer, loader


@pytest.mark.parametrize(("kind", "data", "risk", "label"), [
    ("text", {"text": "Пример"}, 0, "Явных признаков опасности не обнаружено"),
    ("url", {"url": "https://example.com"}, 1, "Есть основания насторожиться"),
    ("transfer", {"amount": "1.50", "purpose": "unknown"}, 2, "Выраженная опасность"),
])
def test_guest_analysis_is_displayed_without_persistence(client, configured_analyzer, kind, data, risk, label):
    # проверяет три гостевых режима и безопасный показ источников
    analyzer, loader = configured_analyzer
    analyzer.analyze.return_value = ({**RESULT, "risk": risk}, {"transfer": {"history_loaded": 1}})
    response = client.post(reverse("checks:new", args=[kind]), data)
    assert response.status_code == 200 and label in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert response.context["check"].pk is None
    assert response.context["check"].context_snapshot == {}
    assert analyzer.analyze.call_args.args[0] == kind
    assert analyzer.analyze.call_args.args[2] is None
    assert loader.call_count == analyzer.analyze.call_count == 1
    assert "&lt;script&gt;" in response.content.decode() and "<script>" not in response.content.decode()
    assert "<a href=\"https://example.com" not in response.content.decode()
    assert "Гостевой результат не сохранен" in response.content.decode()
    assert Check.objects.count() == User.objects.count() == Session.objects.count() == 0
    assert CheckFeedback.objects.count() == TrainingConsent.objects.count() == 0


@pytest.mark.parametrize("failure", [False, True])
def test_authenticated_analysis_redirects_to_saved_result(client, configured_analyzer, failure):
    # проверяет сохранение и повторный просмотр без нового анализа
    analyzer, loader = configured_analyzer
    user = User.objects.create_user(username="owner")
    client.force_login(user)
    if failure:
        analyzer.analyze.side_effect = RuntimeError("internal details")
    response = client.post(reverse("checks:new", args=["text"]), {"text": "Пример"})
    check = Check.objects.get(user=user)
    assert response.status_code == 302 and response.url == reverse("checks:detail", args=[check.pk])
    assert check.status == (Check.Status.FAILED if failure else Check.Status.COMPLETED)
    assert check.result == (None if failure else RESULT)
    response = client.get(response.url)
    assert response.status_code == 200
    assert loader.call_count == analyzer.analyze.call_count == 1
    assert "internal details" not in response.content.decode()
    assert Check.objects.count() == 1


@pytest.mark.parametrize("failure", [False, True])
def test_guest_failure_and_unknown_risk_are_not_shown_as_safe(client, configured_analyzer, failure, caplog):
    # различает ошибку модели и недостаточность для общего вывода
    analyzer, _ = configured_analyzer
    if failure:
        analyzer.analyze.side_effect = RuntimeError("internal details")
    else:
        analyzer.analyze.return_value = ({**RESULT, "risk": None, "sufficiency": "limited"}, {})
    response = client.post(reverse("checks:new", args=["text"]), {"text": "Пример"})
    assert response.status_code == (503 if failure else 200)
    assert "Явных признаков опасности не обнаружено" not in response.content.decode()
    message = "Не удалось выполнить анализ" if failure else "Общий риск не определен"
    assert message in response.content.decode()
    assert "internal details" not in response.content.decode()
    assert "internal details" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == "checks.services")
    assert Check.objects.count() == Session.objects.count() == 0


def test_loader_failure_does_not_create_check(client, configured_analyzer, caplog):
    # отклоняет недоступные модели до создания проверки
    analyzer, loader = configured_analyzer
    loader.side_effect = OSError("private artifact path")
    response = client.post(reverse("checks:new", args=["text"]), {"text": "Пример"})
    assert response.status_code == 503
    assert "Анализ пока не подключен" in response.content.decode()
    assert "private artifact path" not in response.content.decode()
    assert "private artifact path" not in caplog.text
    assert "OSError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == "checks.views")
    analyzer.analyze.assert_not_called()
    assert Check.objects.count() == Session.objects.count() == 0


def test_get_invalid_input_and_csrf_do_not_load_models(client, configured_analyzer):
    # не запускает модели при просмотре формы и неверном запросе
    analyzer, loader = configured_analyzer
    url = reverse("checks:new", args=["text"])
    assert client.get(url).status_code == 200
    assert client.post(url, {}).status_code == 400
    protected = Client(enforce_csrf_checks=True)
    protected.get(url)
    assert protected.post(url, {"text": "Пример"}).status_code == 403
    loader.assert_not_called()
    analyzer.analyze.assert_not_called()
    assert Check.objects.count() == Session.objects.count() == 0
