import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError

from accounts.models import User
from checks.models import Check
from checks.services import create_check, finish_check


pytestmark = pytest.mark.django_db


def test_finish_check_saves_independent_snapshot():
    # проверяет сохранение отдельного снимка результата
    user = User.objects.create_user(username="anna")
    check = create_check(user, Check.Kind.AUDIO, {"audio": {"name": "call.wav", "size": 5}})
    result = {"explanations": ["Тестовое объяснение"]}
    context = {"operations": 3}

    finish_check(
        check,
        result=result,
        analyzer_version="test-v1",
        extracted_text="Распознанная реплика",
        context_snapshot=context,
    )
    result["explanations"].append("Изменение после сохранения")
    context["operations"] = 99

    assert check.result == {"explanations": ["Тестовое объяснение"]}
    assert check.context_snapshot == {"operations": 3}
    check.refresh_from_db()
    assert check.status == Check.Status.COMPLETED
    assert check.result == {"explanations": ["Тестовое объяснение"]}
    assert check.context_snapshot == {"operations": 3}
    assert check.extracted_text == "Распознанная реплика"
    assert check.analyzer_version == "test-v1"
    assert check.error_message == ""


@pytest.mark.parametrize("failed", [False, True])
def test_finished_check_cannot_be_overwritten(failed):
    # проверяет запрет повторной записи результата
    user = User.objects.create_user(username="anna")
    check = create_check(user, Check.Kind.TEXT, {"text": "Сообщение"})
    stale_check = Check.objects.get(pk=check.pk)

    if failed:
        finish_check(check, error_message="Не удалось обработать сообщение")
    else:
        finish_check(check, result={"message": "Первый ответ"}, analyzer_version="test-v1")

    for current in [check, stale_check]:
        with pytest.raises(ValidationError):
            finish_check(current, result={"message": "Второй ответ"}, analyzer_version="test-v2")

    assert stale_check.status == Check.Status.PENDING
    assert stale_check.result is None
    check.refresh_from_db()
    if failed:
        assert check.status == Check.Status.FAILED
        assert check.result is None
        assert check.error_message == "Не удалось обработать сообщение"
    else:
        assert check.status == Check.Status.COMPLETED
        assert check.result == {"message": "Первый ответ"}
        assert check.analyzer_version == "test-v1"


@pytest.mark.parametrize("failed", [False, True])
def test_guest_result_stays_in_memory(failed):
    # проверяет завершение гостевой проверки без сохранения
    check = create_check(AnonymousUser(), Check.Kind.TEXT, {"text": "Сообщение"})
    if failed:
        finish_check(check, error_message="Не удалось обработать сообщение")
        assert check.status == Check.Status.FAILED
        assert check.result is None
    else:
        result = {"message": "Тестовый ответ"}
        finish_check(check, result=result, analyzer_version="test-v1")
        result["message"] = "Измененный ответ"
        assert check.status == Check.Status.COMPLETED
        assert check.result == {"message": "Тестовый ответ"}

    assert check.pk is None
    assert check.user_id is None
    assert check.context_snapshot == {}
    assert Check.objects.count() == 0
    with pytest.raises(ValidationError):
        finish_check(check, error_message="Повторная ошибка")


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"result": {}, "analyzer_version": "test-v1"},
        {"result": {"message": "Ответ"}},
        {"result": {"message": "Ответ"}, "error_message": "Ошибка", "analyzer_version": "test-v1"},
        {"result": {"value": float("nan")}, "analyzer_version": "test-v1"},
        {"result": {"message": "Ответ"}, "analyzer_version": "test-v1", "context_snapshot": []},
        {"result": {"message": "Ответ"}, "analyzer_version": "test-v1", "extracted_text": None},
    ],
)
def test_invalid_result_does_not_change_check(values):
    # проверяет отказ без изменения исходной проверки
    user = User.objects.create_user(username="anna")
    check = create_check(user, Check.Kind.TEXT, {"text": "Сообщение"})

    with pytest.raises(ValidationError):
        finish_check(check, **values)

    assert check.status == Check.Status.PENDING
    assert check.result is None
    assert check.error_message == ""
    check.refresh_from_db()
    assert check.status == Check.Status.PENDING
    assert check.result is None
    assert check.error_message == ""


def test_guest_cannot_receive_personal_history_context():
    # проверяет отсутствие личной истории у гостя
    check = create_check(AnonymousUser(), Check.Kind.TRANSFER, {"amount_kopecks": 100})

    with pytest.raises(ValidationError):
        finish_check(
            check,
            result={"message": "Ответ"},
            analyzer_version="test-v1",
            context_snapshot={"operations": 3},
        )

    assert check.status == Check.Status.PENDING
    assert Check.objects.count() == 0
