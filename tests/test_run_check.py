from unittest.mock import Mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile

from accounts.models import User
from checks.models import Check
from checks.services import run_check
from transactions.services import get_user_transactions, import_transactions


pytestmark = pytest.mark.django_db
HEADER = "occurred_at;amount;direction;recipient\n"
DATA = {"amount_kopecks": 100, "purpose": "unknown"}
RESULT = {"status": "ok", "risk": 0, "analyzer_version": "analysis-test"}


def test_run_check_uses_only_owner_history_and_keeps_guest_in_memory():
    # проверяет свою историю и пустой личный контекст гостя
    owner = User.objects.create_user(username="owner")
    other = User.objects.create_user(username="other")
    for user, recipient in ((owner, "my_shop"), (other, "other_shop")):
        import_transactions(user, ContentFile((HEADER + f"2026-10-01T12:00:00Z;1;outgoing;{recipient}\n").encode()))
    analyzer = Mock()
    analyzer.analyze.return_value = (RESULT, {"transfer": {"history_loaded": 1}})
    check = run_check(owner, Check.Kind.TRANSFER, DATA, analyzer)
    assert check.status == Check.Status.COMPLETED and check.result == RESULT
    args, kwargs = analyzer.analyze.call_args
    assert args == (Check.Kind.TRANSFER, DATA, get_user_transactions(owner))
    assert kwargs == {"checked_at": check.created_at}
    assert all(row["recipient"] != "other_shop" for row in args[2])
    check.refresh_from_db()
    assert check.context_snapshot == {"transfer": {"history_loaded": 1}}
    guest = run_check(AnonymousUser(), Check.Kind.TRANSFER, DATA, analyzer)
    assert guest.pk is None and guest.status == Check.Status.COMPLETED
    assert guest.context_snapshot == {} and analyzer.analyze.call_args.args[2] is None
    assert Check.objects.count() == 1


def test_no_upload_differs_from_history_with_only_incoming():
    # отличает отсутствие загрузки от отсутствия исходящих
    user = User.objects.create_user(username="owner")
    analyzer = Mock()
    analyzer.analyze.return_value = (RESULT, {})
    run_check(user, Check.Kind.TRANSFER, DATA, analyzer)
    assert analyzer.analyze.call_args.args[2] is None
    import_transactions(user, ContentFile((HEADER + "2026-10-01T12:00:00Z;1;incoming;employer\n").encode()))
    run_check(user, Check.Kind.TRANSFER, DATA, analyzer)
    assert len(analyzer.analyze.call_args.args[2]) == 1
    assert analyzer.analyze.call_args.args[2][0]["direction"] == "incoming"


@pytest.mark.parametrize("exception", [False, True])
def test_failed_analysis_is_terminal_and_has_no_success_result(exception):
    # сохраняет сбой как ошибку без внутреннего текста исключения
    user = User.objects.create_user(username="owner")
    analyzer = Mock()
    if exception:
        analyzer.analyze.side_effect = RuntimeError("internal-model-details")
    else:
        analyzer.analyze.return_value = ({"status": "error", "analyzer_version": "analysis-test"}, {})
    check = run_check(user, Check.Kind.TEXT, {"text": "Сообщение"}, analyzer)
    check.refresh_from_db()
    assert check.status == Check.Status.FAILED and check.result is None
    assert check.error_message and "internal-model-details" not in check.error_message
