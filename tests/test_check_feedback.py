import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404

from accounts.models import User
from checks.forms import TRAINING_CONSENT_VERSION, TrainingConsentForm
from checks.models import Check, CheckFeedback, TrainingConsent
from checks.services import (
    create_check,
    finish_check,
    get_check_feedback,
    has_training_consent,
    save_check_feedback,
    save_training_consent,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def completed_check():
    # создает свою проверку с тестовым результатом
    user = User.objects.create_user(username="anna")
    check = create_check(user, Check.Kind.TEXT, {"text": "Сообщение"})
    finish_check(
        check,
        result={"test": True},
        analyzer_version="test-v1",
        extracted_text="Тестовый текст",
        context_snapshot={"count": 2},
    )
    return check


def test_feedback_is_editable_and_does_not_change_prediction(completed_check):
    # проверяет исправление одного отзыва без изменения прогноза
    check = completed_check
    original = Check.objects.values().get(pk=check.pk)
    assert get_check_feedback(check.user, check.pk) is None

    for outcome in ["unknown", "ordinary", "fraud"]:
        form = save_check_feedback(
            check.user,
            check.pk,
            {
                "outcome": outcome,
                "comment": f"  {outcome}  ",
                "granted": True,
                "terms_version": TRAINING_CONSENT_VERSION,
            },
        )
        assert form.is_valid(), form.errors
        feedback = get_check_feedback(check.user, check.pk)
        assert feedback.outcome == outcome
        assert feedback.comment == outcome
        assert feedback.updated_at is not None
        assert CheckFeedback.objects.filter(check_record=check).count() == 1

    assert Check.objects.values().get(pk=check.pk) == original
    assert not has_training_consent(check.user, check.pk)
    assert TrainingConsent.objects.count() == 0


@pytest.mark.parametrize("data", [{}, {"outcome": "safe"}])
def test_invalid_feedback_keeps_previous_value(completed_check, data):
    # проверяет сохранность отзыва при неверном исходе
    check = completed_check
    save_check_feedback(check.user, check.pk, {"outcome": "ordinary"})
    previous = CheckFeedback.objects.values().get(check_record=check)

    form = save_check_feedback(check.user, check.pk, data)

    assert not form.is_valid()
    assert "outcome" in form.errors
    assert CheckFeedback.objects.values().get(check_record=check) == previous


def test_consent_is_separate_optional_and_revocable(completed_check):
    # проверяет отдельное включение и отзыв согласия
    check = completed_check
    original = Check.objects.values().get(pk=check.pk)
    assert TrainingConsentForm().fields["granted"].initial is False
    assert not has_training_consent(check.user, check.pk)
    assert TrainingConsent.objects.count() == 0

    form = save_training_consent(
        check.user,
        check.pk,
        {"granted": "on", "terms_version": TRAINING_CONSENT_VERSION},
    )
    assert form.is_valid(), form.errors
    assert has_training_consent(check.user, check.pk)
    consent = TrainingConsent.objects.get(check_record=check)
    assert consent.granted is True
    assert consent.terms_version == TRAINING_CONSENT_VERSION
    assert consent.updated_at is not None
    assert get_check_feedback(check.user, check.pk) is None

    save_check_feedback(check.user, check.pk, {"outcome": "fraud"})
    assert TrainingConsent.objects.get(check_record=check).updated_at == consent.updated_at
    assert has_training_consent(check.user, check.pk)

    form = save_training_consent(
        check.user, check.pk, {"terms_version": TRAINING_CONSENT_VERSION}
    )
    assert form.is_valid(), form.errors
    consent.refresh_from_db()
    assert consent.granted is False
    assert not has_training_consent(check.user, check.pk)
    assert TrainingConsent.objects.count() == 1
    assert get_check_feedback(check.user, check.pk).outcome == "fraud"
    save_check_feedback(check.user, check.pk, {"outcome": "unknown", "comment": "Уточнение"})
    assert not has_training_consent(check.user, check.pk)
    assert Check.objects.values().get(pk=check.pk) == original


@pytest.mark.parametrize("version", [None, "old-version"])
def test_invalid_terms_version_does_not_change_consent(completed_check, version):
    # проверяет отказ при отсутствующих или устаревших условиях
    check = completed_check
    save_training_consent(
        check.user,
        check.pk,
        {"granted": True, "terms_version": TRAINING_CONSENT_VERSION},
    )
    previous = TrainingConsent.objects.values().get(check_record=check)

    form = save_training_consent(check.user, check.pk, {"terms_version": version})

    assert not form.is_valid()
    assert "terms_version" in form.errors
    assert TrainingConsent.objects.values().get(check_record=check) == previous


def test_consent_does_not_cover_other_checks_or_new_terms(completed_check):
    # проверяет границы согласия и смену версии условий
    check = completed_check
    other = create_check(check.user, Check.Kind.TEXT, {"text": "Другая ситуация"})
    finish_check(other, result={"test": True}, analyzer_version="test-v1")
    consent = TrainingConsent.objects.create(check_record=check, granted=True, terms_version="old")

    assert not has_training_consent(check.user, check.pk)
    consent.terms_version = TRAINING_CONSENT_VERSION
    consent.save()
    assert has_training_consent(check.user, check.pk)
    assert not has_training_consent(check.user, other.pk)


@pytest.mark.parametrize("anonymous", [True, False])
def test_feedback_and_consent_require_owner(completed_check, anonymous):
    # проверяет запрет чтения и записи для посторонних
    check = completed_check
    if anonymous:
        user = AnonymousUser()
        expected_error = PermissionDenied
    else:
        user = User.objects.create_user(username="ivan")
        expected_error = Http404

    with pytest.raises(expected_error):
        get_check_feedback(user, check.pk)
    with pytest.raises(expected_error):
        has_training_consent(user, check.pk)
    with pytest.raises(expected_error):
        save_check_feedback(user, check.pk, {"outcome": "fraud"})
    with pytest.raises(expected_error):
        save_training_consent(
            user, check.pk, {"granted": True, "terms_version": TRAINING_CONSENT_VERSION}
        )
    assert CheckFeedback.objects.count() == 0
    assert TrainingConsent.objects.count() == 0


@pytest.mark.parametrize("failed", [False, True])
def test_feedback_and_consent_need_completed_result(failed):
    # проверяет запрет записи без готового результата
    user = User.objects.create_user(username="anna")
    check = create_check(user, Check.Kind.TEXT, {"text": "Сообщение"})
    if failed:
        finish_check(check, error_message="Ошибка обработки")

    with pytest.raises(ValidationError):
        save_check_feedback(user, check.pk, {"outcome": "unknown"})
    with pytest.raises(ValidationError):
        save_training_consent(
            user, check.pk, {"granted": True, "terms_version": TRAINING_CONSENT_VERSION}
        )
    assert CheckFeedback.objects.count() == 0
    assert TrainingConsent.objects.count() == 0


def test_database_rejects_second_feedback_and_unknown_outcome(completed_check):
    # проверяет ограничения таблицы отзывов
    check = completed_check
    CheckFeedback.objects.create(check_record=check, outcome="unknown")
    with pytest.raises(IntegrityError), transaction.atomic():
        CheckFeedback.objects.create(check_record=check, outcome="ordinary")
    CheckFeedback.objects.filter(check_record=check).delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        CheckFeedback.objects.create(check_record=check, outcome="safe")
