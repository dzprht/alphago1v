from datetime import datetime, timedelta, timezone

import pytest

from analysis.result import assemble_result
from analysis.risk import risk_features
from analysis.text import TEXT_LABELS
from analysis.transfer import transfer_features


THRESHOLDS = {"text_" + label: 0.8 for label in TEXT_LABELS} | {"url_score": 0.8}


def text_result(speaker="A", **scores):
    # готовит текстовые оценки с указанием автора и цитаты
    return {"speaker": speaker, "owner": "unknown", "values": scores,
            "source_refs": {name: {"turn_id": speaker or "unknown", "text": "Исходная просьба"}
                            for name in scores}}


@pytest.mark.parametrize("feature,rule", [
    ("text_credentials_request", "C"), ("text_protective_transfer", "P"),
    ("text_remote_access", "R"), ("url_score", "F"),
])
def test_single_strong_signal_gives_banner(feature, rule):
    # показывает предупреждение по одному сильному основанию
    if feature == "url_score":
        row = risk_features("url", url={"values": {feature: 0.9},
                                       "source_refs": {feature: {"url": "https://example.com"}}})
    else:
        row = risk_features("text", text=text_result(**{feature: 0.9}))
    result = assemble_result([row], thresholds=THRESHOLDS, versions={})
    assert result["warning"] and result["risk"] is None
    assert result["combined_signals"][0]["rules"] == [rule]
    assert result["findings"][0]["severity"] == "banner"


@pytest.mark.parametrize("feature,rule", [
    ("text_urgency", "M+U"), ("text_secrecy", "M+S"), ("text_authority_claim", "M+A"),
])
def test_money_request_needs_a_contextual_signal(feature, rule):
    # отличает обычную просьбу об оплате от согласованного сочетания
    row = risk_features("text", text=text_result(text_money_request=0.9))
    result = assemble_result([row], thresholds=THRESHOLDS, versions={})
    assert not result["warning"] and result["findings"][0]["severity"] == "remark"
    row = risk_features("text", text=text_result(text_money_request=0.9, **{feature: 0.9}))
    result = assemble_result([row], thresholds=THRESHOLDS, versions={})
    assert result["warning"] and result["combined_signals"][0]["rules"] == [rule]


@pytest.mark.parametrize("amount,recipient,changed,rules", [
    (1, 0, "yes", ["D+H"]), (0, 1, "yes", ["D+N"]),
    (1, 1, "unknown", []), (None, None, "yes", []),
])
def test_history_flags_require_changed_details_for_banner(amount, recipient, changed, rules):
    # оставляет крупную покупку у нового получателя отдельными замечаниями
    transfer = {"values": {"history_loaded": 1, "history_valid": 1,
                           "amount_unusual": amount, "recipient_unseen": recipient},
                "source_refs": {"amount_unusual": {"field": "amount"},
                                "recipient_unseen": {"field": "recipient"}}}
    row = risk_features("transfer", transfer=transfer, fields={"changed_details": changed})
    result = assemble_result([row], thresholds=THRESHOLDS, versions={})
    assert result["warning"] == bool(rules)
    assert result["combined_signals"][0]["rules"] == rules
    assert result["combined_signals"][0]["flags"]["H"] == amount
    assert result["combined_signals"][0]["flags"]["N"] == recipient
    if not rules:
        assert all(finding["severity"] == "remark" for finding in result["findings"])


def test_dialogue_combines_only_signals_already_assigned_to_the_same_request():
    # не соединяет просьбу одного автора со срочностью другого
    first = risk_features("text", text=text_result("A", text_money_request=0.9))
    for speaker in ("B", None):
        second = risk_features("text", text=text_result(speaker, text_urgency=0.9))
        result = assemble_result([first, second], thresholds=THRESHOLDS, versions={})
        assert not result["warning"]
    joined = risk_features("text", text=text_result("A", previous_money_request=0.9, text_urgency=0.9))
    result = assemble_result([joined], thresholds=THRESHOLDS, versions={})
    assert result["warning"] and result["combined_signals"][0]["rules"] == ["M+U"]
    first_text = text_result("A", text_money_request=0.85)
    first_text["source_refs"]["text_money_request"].update(speaker="A", text="Оплатите покупку")
    second_text = text_result("B", text_money_request=0.95, text_urgency=0.9)
    for source in second_text["source_refs"].values():
        source.update(speaker="B", text="Срочно переведите деньги")
    rows = [risk_features("text", text=text) for text in (first_text, second_text)]
    result = assemble_result(rows, thresholds=THRESHOLDS, versions={})
    money = [finding for finding in result["findings"] if finding["feature"] == "text_money_request"]
    assert [(item["speaker_group"], item["severity"], item["score"], item["model_score"]) for item in money] == [
        ("A", "remark", 0.85, 0.85), ("B", "banner", 0.95, 0.95),
    ]
    assert all(item["source_ref"]["speaker"] == item["speaker_group"] for item in money)


def test_external_requester_supplies_money_request_and_user_answers_do_not_cancel_model():
    # учитывает внешнюю просьбу и сохраняет положительные оценки модели
    for requester in ("acquaintance", "seller", "claimed_organization", "other", "self", "unknown"):
        row = risk_features("transfer", fields={"requester": requester, "urgency": "yes"})
        result = assemble_result([row], thresholds=THRESHOLDS, versions={})
        flags = result["combined_signals"][0]["flags"]
        assert flags["M"] == (None if requester in ("self", "unknown") else 1)
        assert result["warning"] == (requester not in ("self", "unknown"))
    row = risk_features("transfer", text=text_result(text_money_request=0.9, text_urgency=0.9),
                        fields={"requester": "self", "urgency": "yes", "secrecy": "no"})
    result = assemble_result([row], thresholds=THRESHOLDS, versions={})
    signal = result["combined_signals"][0]["values"]["text_urgency"]
    assert signal["score"] == 1 and signal["model_score"] == 0.9
    finding = next(item for item in result["findings"] if item["feature"] == "text_urgency")
    assert finding["origins"] == ["model", "questionnaire"] and result["warning"]


def test_partial_recognition_and_unavailable_history_keep_strong_findings():
    # сохраняет найденную опасную просьбу при неполных данных
    row = risk_features("audio", text=text_result(text_credentials_request=0.9), extraction_status="partial")
    result = assemble_result([row], thresholds=THRESHOLDS, versions={})
    assert result["warning"] and result["status"] == "partial" and result["sufficiency"] == "limited"
    assert result["findings"][0]["feature"] == "text_credentials_request"
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    for history in (None, [{"occurred_at": now + timedelta(days=1), "direction": "outgoing",
                            "amount_kopecks": 100, "recipient": "shop"}]):
        transfer = transfer_features(1, "shop", history, checked_at=now)
        row = risk_features("transfer", text=text_result(text_credentials_request=0.9), transfer=transfer)
        result = assemble_result([row], thresholds=THRESHOLDS, versions={})
        flags = result["combined_signals"][0]["flags"]
        assert flags["H"] is flags["N"] is None
        assert result["warning"] and result["sufficiency"] == "limited"
