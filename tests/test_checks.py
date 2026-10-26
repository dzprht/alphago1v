import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404

from accounts.models import User
from checks.models import Check
from checks.services import create_check, get_user_check, get_user_checks


pytestmark = pytest.mark.django_db


def test_new_check_has_no_result():
    # проверяет сохранение новой проверки
    user = User.objects.create_user(username="anna")
    data = {"text": "Сообщение для проверки"}

    check = create_check(user, Check.Kind.TEXT, data)
    check.refresh_from_db()

    assert check.user == user
    assert check.kind == Check.Kind.TEXT
    assert check.input_data == data
    assert check.status == Check.Status.PENDING
    assert check.result is None
    assert check.context_snapshot == {}
    assert check.analyzer_version == ""


def test_checks_are_only_visible_to_owner():
    # проверяет доступ только к своим проверкам
    user = User.objects.create_user(username="anna")
    other_user = User.objects.create_user(username="ivan")
    first = create_check(user, Check.Kind.TEXT, {"text": "Первое сообщение"})
    second = create_check(user, Check.Kind.TEXT, {"text": "Второе сообщение"})
    other = create_check(other_user, Check.Kind.URL, {"url": "https://example.com"})

    assert list(get_user_checks(user)) == [second, first]
    assert get_user_check(user, first.pk) == first
    with pytest.raises(Http404):
        get_user_check(user, other.pk)
    with pytest.raises(Http404):
        get_user_check(user, other.pk + 1)


def test_anonymous_user_cannot_access_checks():
    # проверяет запрет доступа без входа
    owner = User.objects.create_user(username="anna")
    check = create_check(owner, Check.Kind.TEXT, {"text": "Сообщение"})
    anonymous = AnonymousUser()

    with pytest.raises(PermissionDenied):
        create_check(anonymous, Check.Kind.TEXT, {"text": "Другое сообщение"})
    with pytest.raises(PermissionDenied):
        get_user_checks(anonymous)
    with pytest.raises(PermissionDenied):
        get_user_check(anonymous, check.pk)
    assert Check.objects.count() == 1


@pytest.mark.parametrize("field", ["kind", "status"])
def test_database_rejects_unknown_check_values(field):
    # проверяет допустимые значения в базе
    user = User.objects.create_user(username="anna")
    values = {"user": user, "kind": Check.Kind.TEXT, "input_data": {"text": "Сообщение"}}
    values[field] = "unknown"

    with pytest.raises(IntegrityError), transaction.atomic():
        Check.objects.create(**values)
