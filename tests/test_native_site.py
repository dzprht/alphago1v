import os
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client

from accounts.models import User
from checks.forms import TRAINING_CONSENT_VERSION
from checks.models import Check, CheckFeedback, TrainingConsent
from transactions.services import get_user_transactions


MODEL_DIRECTORY = os.getenv("NATIVE_MODELS_DIR")
pytestmark = [pytest.mark.django_db,
              pytest.mark.skipif(not MODEL_DIRECTORY, reason="Нужен локальный полный комплект моделей")]


def test_all_features_with_real_models_and_csrf(settings):
    # проходит весь сайт с настоящими моделями в отдельной тестовой базе
    settings.ANALYZER_DIR = Path(MODEL_DIRECTORY).resolve()
    call_command("check_models")
    client = Client(enforce_csrf_checks=True)
    assert client.get("/").status_code == 200
    client.get("/accounts/login/")

    def post(url, data):
        # передает текущий токен защиты формы
        return client.post(url, {**data, "csrfmiddlewaretoken": client.cookies["csrftoken"].value})

    cases = [
        ("text", {"text": "Срочно переведите деньги на безопасный счет. Никому не говорите."}, True),
        ("url", {"url": "https://www.rewildingargentina.org", "expected_site": "Альфа-Банк"}, False),
        ("transfer", {"amount": "100", "purpose": "unknown", "requester": "claimed_organization"}, True),
    ]
    for kind, data, warning in cases:
        response = post("/checks/new/" + kind + "/", data)
        assert response.status_code == 200
        assert response.context["check"].result["warning"] is warning
    assert Check.objects.count() == User.objects.count() == 0

    password = "PlayfulCedar_7824"
    assert post("/accounts/register/", {"username": "native_owner", "password1": password,
                                       "password2": password}).status_code == 302
    assert post("/accounts/login/", {"username": "native_owner", "password": password}).status_code == 302
    for kind, extension, field in (("qr", "png", "image"), ("screenshot", "png", "image")):
        for name, warning in (("warning", True), ("ordinary", False)):
            with (settings.BASE_DIR / "examples/demo" / (("qr-" if kind == "qr" else "") + name + "." + extension)).open("rb") as stream:
                response = post("/checks/new/" + kind + "/", {field: stream})
            assert response.status_code == 302
            detail = client.get(response.url)
            assert detail.status_code == 200
            check = detail.context["check"]
            assert check.status == "completed" and check.extracted_text
            assert check.result["warning"] is warning

    with (settings.BASE_DIR / "examples/transactions.csv").open("rb") as stream:
        assert post("/transactions/", {"file": stream}).status_code == 302
    assert len(get_user_transactions(User.objects.get(username="native_owner"))) == 3
    manual = {"action": "manual", "manual-replace": "on", "rows-TOTAL_FORMS": "20", "rows-INITIAL_FORMS": "0"}
    for index in range(20):
        manual.update({f"rows-{index}-occurred_at": "2026-10-20T14:30",
                       f"rows-{index}-amount": str(100 + index * 10),
                       f"rows-{index}-direction": "outgoing", f"rows-{index}-recipient": "known_shop"})
    assert post("/transactions/", manual).status_code == 302
    response = post("/checks/new/transfer/", {"amount": "500000", "purpose": "purchase",
                                             "recipient": "new_shop", "changed_details": "yes"})
    assert response.status_code == 302
    check = client.get(response.url).context["check"]
    flags = check.result["combined_signals"][0]["flags"]
    assert flags["D"] == flags["H"] == flags["N"] == 1 and check.result["warning"]
    original = check.result
    assert post(response.url, {"action": "feedback", "outcome": "ordinary"}).status_code == 302
    assert post(response.url, {"action": "consent", "granted": "on",
                              "terms_version": TRAINING_CONSENT_VERSION}).status_code == 302
    assert CheckFeedback.objects.get(check_record=check).outcome == "ordinary"
    assert TrainingConsent.objects.get(check_record=check).granted
    assert post(response.url, {"action": "consent", "terms_version": TRAINING_CONSENT_VERSION}).status_code == 302
    assert not TrainingConsent.objects.get(check_record=check).granted
    check.refresh_from_db()
    assert check.result == original
    assert client.get("/checks/").status_code == 200
    assert post("/transactions/delete/", {}).status_code == 302
    assert get_user_transactions(User.objects.get(username="native_owner")) == []
    assert post("/accounts/logout/", {}).status_code == 302
    assert client.get(response.url).status_code == 302
