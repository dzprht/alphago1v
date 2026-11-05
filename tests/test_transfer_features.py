from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import log, log1p

import pytest

from analysis.features import feature_vector
from analysis.transfer import TRANSFER_FEATURES, transfer_features


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def operation(days, amount=100, recipient="shop", direction="outgoing"):
    # создает одну операцию для проверки расчетов
    return {"occurred_at": NOW - timedelta(days=days), "amount_kopecks": amount,
            "recipient": recipient, "direction": direction}


def test_transfer_uses_outgoing_history_and_exact_recipient():
    # проверяет все признаки на смешанной истории
    history = [operation(i, i * 100, "shop" if i <= 3 else "other" if i <= 10 else "")
               for i in range(1, 21)]
    history.append(operation(100, 999999, "shop", "incoming"))
    original = deepcopy(history)
    values = transfer_features(1050, " shop ", list(reversed(history)), checked_at=NOW)["values"]
    assert values == pytest.approx({
        "amount_log": log1p(10.5), "outgoing_count_log": log1p(20),
        "history_days": 19, "last_outgoing_days": 1, "amount_percentile": 0.5,
        "amount_median_log_ratio": 0, "recipient_coverage": 0.5,
        "recipient_count_log": log1p(3), "recipient_share": 0.3, "last_recipient_days": 1,
        "history_loaded": 1, "history_valid": 1, "recipient_provided": 1,
        "amount_unusual": 0, "amount_modified_z": 0, "recipient_unseen": 0,
    })
    assert history == original
    unknown = transfer_features(1050, history=history, checked_at=NOW)["values"]
    assert unknown["recipient_provided"] == 0
    assert all(unknown[name] is None for name in ("recipient_count_log", "recipient_share", "last_recipient_days"))
    different = transfer_features(1050, "Shop", history, checked_at=NOW)["values"]
    assert different["recipient_count_log"] == different["recipient_share"] == 0
    assert different["last_recipient_days"] is None


def test_short_history_and_no_known_recipients():
    # проверяет порог истории и неизвестные обозначения получателей
    history = [operation(i / 2, recipient="") for i in range(19)]
    values = transfer_features(100, "shop", history, checked_at=NOW)["values"]
    assert values["amount_percentile"] is values["amount_median_log_ratio"] is None
    assert values["history_days"] == 9
    assert values["last_outgoing_days"] == 0
    assert values["recipient_coverage"] == values["recipient_count_log"] == 0
    assert values["recipient_share"] is values["last_recipient_days"] is None
    vector = feature_vector(values, TRANSFER_FEATURES)
    assert len(vector) == 20
    assert vector[4] == vector[5] == 0
    assert vector[14] == vector[15] == 0
    assert vector[16] == vector[17] == 1


def test_money_comparisons_keep_kopecks_and_fractional_median():
    # проверяет большие суммы и медиану между двумя копейками
    base = 999999999999999970
    history = [operation(i, base + i) for i in range(20)]
    values = transfer_features(base + 9, history=history, checked_at=NOW)["values"]
    assert values["amount_percentile"] == 0.5
    history = [operation(i, 100 + i) for i in range(20)]
    values = transfer_features(110, history=history, checked_at=NOW)["values"]
    assert values["amount_median_log_ratio"] == pytest.approx(log(Decimal(110) / Decimal("109.5")))


def test_missing_history_differs_from_history_without_outgoing():
    # различает отсутствие истории и отсутствие исходящих операций
    missing = transfer_features(100, "shop", checked_at=NOW)["values"]
    assert missing["history_loaded"] == 0
    assert missing["history_valid"] is None
    assert all(missing[name] is None for name in TRANSFER_FEATURES[1:])
    for history in ([], [operation(1, direction="incoming")]):
        values = transfer_features(100, "shop", history, checked_at=NOW)["values"]
        assert values["history_loaded"] == values["history_valid"] == 1
        assert values["outgoing_count_log"] == values["recipient_count_log"] == 0
        assert all(values[name] is None for name in (
            "history_days", "last_outgoing_days", "amount_percentile", "amount_median_log_ratio",
            "recipient_coverage", "recipient_share", "last_recipient_days",
        ))


def test_any_future_row_disables_history_without_removing_rows():
    # выключает историю при будущей дате и требует часовой пояс
    history = [operation(1), operation(-1, direction="incoming")]
    original = deepcopy(history)
    values = transfer_features(100, "shop", history, checked_at=NOW)["values"]
    assert values["history_loaded"] == values["recipient_provided"] == 1
    assert values["history_valid"] == 0
    assert values["amount_log"] == log1p(1)
    assert all(values[name] is None for name in TRANSFER_FEATURES[1:])
    assert history == original
    offset_time = NOW.astimezone(timezone(timedelta(hours=3)))
    assert transfer_features(100, history=[operation(0)], checked_at=offset_time)["values"]["last_outgoing_days"] == 0
    with pytest.raises(ValueError, match="часовой пояс"):
        transfer_features(100, checked_at=NOW.replace(tzinfo=None))


def test_modified_z_uses_strict_upper_boundary_and_ignores_incoming():
    # проверяет границу суммы и неизменность исходной истории
    history = [operation(i, 93255 if i < 10 else 106745) for i in range(20)]
    history.append(operation(1, 999999999, direction="incoming"))
    original = deepcopy(history)
    for amount, flag, score in ((135000, 0, 3.5), (135001, 1, 3.5001), (65000, 0, -3.5)):
        result = transfer_features(amount, "shop", history, checked_at=NOW)
        values = result["values"]
        assert values["amount_unusual"] == flag
        assert values["amount_modified_z"] == pytest.approx(score)
        assert result["source_refs"]["amount_unusual"] == {
            "field": "amount", "statistic": "modified_z", "z": values["amount_modified_z"],
        }
    assert history == original


def test_transfer_flags_keep_unknowns_and_distinguish_recipient_matches():
    # различает нехватку истории нулевой разброс и отсутствие получателя
    for history in (None, [], [operation(i, 100 + i) for i in range(19)],
                    [operation(i, 100000) for i in range(20)],
                    [operation(1), operation(-1, direction="incoming")]):
        values = transfer_features(100001, "shop", history, checked_at=NOW)["values"]
        assert values["amount_unusual"] is values["amount_modified_z"] is None
    history = [operation(1, recipient="shop"), operation(2, recipient="")]
    assert transfer_features(100, " shop ", history, checked_at=NOW)["values"]["recipient_unseen"] == 0
    result = transfer_features(100, "Shop", history, checked_at=NOW)
    assert result["values"]["recipient_unseen"] == 1
    assert result["source_refs"]["recipient_unseen"] == {"field": "recipient", "statistic": "history_matches"}
    for recipient, rows in (("", history), ("shop", None), ("shop", []),
                            ("shop", [operation(1, recipient="")]),
                            ("shop", [operation(1, direction="incoming")]),
                            ("shop", [operation(-1)])):
        result = transfer_features(100, recipient, rows, checked_at=NOW)
        assert result["values"]["recipient_unseen"] is None
        assert "recipient_unseen" not in result["source_refs"]
