import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import User
from checks.forms import TRAINING_CONSENT_VERSION
from checks.models import Check, CheckFeedback, TrainingConsent
from checks.services import (
    create_check,
    finish_check,
    has_training_consent,
    save_check_feedback,
    save_training_consent,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def saved_check():
    # создает проверку с явно тестовым результатом
    user = User.objects.create_user(username="anna")
    text = "<script>alert('test')</script>"
    check = create_check(user, Check.Kind.TEXT, {"text": text})
    finish_check(
        check,
        result={"explanations": [text]},
        analyzer_version="test-v1",
        extracted_text=text,
    )
    return check


def test_history_and_result_show_only_own_unchanged_data(client, saved_check):
    # проверяет свою историю и безопасный показ сохраненного ответа
    check = saved_check
    other_user = User.objects.create_user(username="ivan")
    other = create_check(other_user, Check.Kind.URL, {"url": "https://example.com"})
    original = Check.objects.values().get(pk=check.pk)
    client.force_login(check.user)

    response = client.get(reverse("checks:history"))
    assert response.status_code == 200
    assert list(response.context["checks"]) == [check]
    assert reverse("checks:detail", args=[other.pk]) not in response.content.decode()
    response = client.get(reverse("checks:detail", args=[check.pk]))
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert check.input_data["text"] not in response.content.decode()
    assert "&lt;script&gt;" in response.content.decode()
    assert response.context["consent_form"].initial["granted"] is False
    assert Check.objects.values().get(pk=check.pk) == original
    assert CheckFeedback.objects.count() == TrainingConsent.objects.count() == 0


def test_empty_history_has_clear_message(client):
    # проверяет пустую историю после первого входа
    client.force_login(User.objects.create_user(username="anna"))

    response = client.get(reverse("checks:history"))

    assert response.status_code == 200
    assert "Проверок пока нет" in response.content.decode()


def test_feedback_and_consent_http_are_separate_and_use_csrf(saved_check):
    # проверяет отзыв и отдельный отзыв согласия через формы
    check = saved_check
    original = Check.objects.values().get(pk=check.pk)
    url = reverse("checks:detail", args=[check.pk])
    client = Client(enforce_csrf_checks=True)
    client.force_login(check.user)
    client.get(url)
    token = client.cookies["csrftoken"].value

    response = client.post(
        url,
        {"action": "feedback", "outcome": "fraud", "granted": "on", "csrfmiddlewaretoken": token},
    )
    assert response.status_code == 302
    assert response.url == url
    assert CheckFeedback.objects.get(check_record=check).outcome == "fraud"
    assert TrainingConsent.objects.count() == 0

    response = client.post(
        url,
        {
            "action": "consent",
            "granted": "on",
            "terms_version": TRAINING_CONSENT_VERSION,
            "csrfmiddlewaretoken": token,
        },
    )
    assert response.status_code == 302
    assert has_training_consent(check.user, check.pk)
    response = client.get(url)
    assert response.context["consent_form"].initial["granted"] is True
    assert "полная история CSV" in response.content.decode()

    response = client.post(
        url,
        {"action": "feedback", "outcome": "ordinary", "csrfmiddlewaretoken": token},
    )
    assert response.status_code == 302
    assert has_training_consent(check.user, check.pk)
    response = client.post(
        url,
        {
            "action": "consent",
            "terms_version": TRAINING_CONSENT_VERSION,
            "csrfmiddlewaretoken": token,
        },
    )
    assert response.status_code == 302
    assert not has_training_consent(check.user, check.pk)
    assert CheckFeedback.objects.get(check_record=check).outcome == "ordinary"
    assert Check.objects.values().get(pk=check.pk) == original


def test_invalid_forms_keep_feedback_and_consent(client, saved_check):
    # проверяет ошибки форм без потери прежних решений
    check = saved_check
    save_check_feedback(check.user, check.pk, {"outcome": "unknown", "comment": "Пока не ясно"})
    save_training_consent(
        check.user, check.pk, {"granted": True, "terms_version": TRAINING_CONSENT_VERSION}
    )
    old_feedback = CheckFeedback.objects.values().get(check_record=check)
    old_consent = TrainingConsent.objects.values().get(check_record=check)
    client.force_login(check.user)
    url = reverse("checks:detail", args=[check.pk])

    response = client.post(url, {"action": "feedback", "outcome": "invalid"})
    assert response.status_code == 200
    assert "outcome" in response.context["feedback_form"].errors
    response = client.post(url, {"action": "consent", "terms_version": "old"})
    assert response.status_code == 200
    assert "terms_version" in response.context["consent_form"].errors
    assert response.context["training_allowed"] is True
    assert CheckFeedback.objects.values().get(check_record=check) == old_feedback
    assert TrainingConsent.objects.values().get(check_record=check) == old_consent


def test_saved_pages_require_login_and_hide_other_users(client, saved_check):
    # проверяет вход и владельца при чтении и изменении
    check = saved_check
    detail_url = reverse("checks:detail", args=[check.pk])
    assert client.get(reverse("checks:history")).status_code == 302
    assert client.get(detail_url).status_code == 302
    assert client.post(detail_url, {"action": "feedback", "outcome": "fraud"}).status_code == 302

    client.force_login(User.objects.create_user(username="ivan"))
    assert client.get(detail_url).status_code == 404
    for action in ["feedback", "consent"]:
        assert client.post(detail_url, {"action": action}).status_code == 404
    assert CheckFeedback.objects.count() == TrainingConsent.objects.count() == 0


@pytest.mark.parametrize("failed", [False, True])
def test_pending_and_failed_checks_cannot_receive_feedback(client, failed):
    # проверяет показ незавершенного анализа без формы отзыва
    user = User.objects.create_user(username="anna")
    check = create_check(user, Check.Kind.TEXT, {"text": "Сообщение"})
    if failed:
        finish_check(check, error_message="Ошибка обработки")
    client.force_login(user)
    url = reverse("checks:detail", args=[check.pk])

    response = client.get(url)
    assert response.status_code == 200
    assert 'name="action"' not in response.content.decode()
    if failed:
        assert "Ошибка обработки" in response.content.decode()
    for action in ["feedback", "consent"]:
        assert client.post(url, {"action": action}).status_code == 400
    assert CheckFeedback.objects.count() == TrainingConsent.objects.count() == 0


def test_http_methods_csrf_and_unknown_action(client, saved_check):
    # проверяет запрет записи через неверный метод или без токена
    check = saved_check
    client.force_login(check.user)
    url = reverse("checks:detail", args=[check.pk])
    assert client.post(reverse("checks:history")).status_code == 405
    assert client.get(url, {"action": "consent", "granted": "on"}).status_code == 200
    assert client.post(url, {"action": "other"}).status_code == 400

    protected_client = Client(enforce_csrf_checks=True)
    protected_client.force_login(check.user)
    for action in ["feedback", "consent"]:
        assert protected_client.post(url, {"action": action}).status_code == 403
    assert CheckFeedback.objects.count() == TrainingConsent.objects.count() == 0
