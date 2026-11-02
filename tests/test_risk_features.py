from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite, log1p

import pytest

from analysis.risk import CATEGORIES, NUMERIC_FEATURES, RISK_FEATURES, risk_features
from analysis.transfer import transfer_features
from checks.forms import TransferCheckForm


def test_risk_vector_combines_blocks_without_mixing_questionnaire_and_text():
    # проверяет общий вектор и раздельные источники признаков
    form = TransferCheckForm(data={
        "amount": "10.50", "purpose": "purchase", "recipient": "shop",
        "requester": "seller", "channel": "call", "urgency": "yes", "secrecy": "no",
        "message": "Исходная просьба", "risk": "2",
    })
    fields = form.get_input_data()
    transfer = transfer_features(fields["amount_kopecks"], fields["recipient"], [],
                                 checked_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    text = {
        "turn_id": "now", "speaker": "A", "owner": "unknown",
        "values": {"text_urgency": 0.0, "previous_urgency": 0.6, "risk": 2},
        "source_refs": {
            "text_urgency": {"turn_id": "now", "text": "Исходная просьба"},
            "previous_urgency": {"turn_id": "past", "text": "Прошлая просьба"},
        },
    }
    url = {"values": {"url_score": 0.8, "brand_mismatch": None, "has_userinfo": 0, "host_is_ip": 1},
           "source_refs": {"url_score": {"url": "https://example.com"}}}
    before = deepcopy((fields, text, url, transfer))
    result = risk_features("transfer", fields=fields, text=text, url=url,
                           transfer=transfer, order_known=True)
    named = dict(zip(RISK_FEATURES, result["vector"]))
    assert len(result["vector"]) == len(set(RISK_FEATURES)) == 105
    assert len(NUMERIC_FEATURES) == 31
    assert all(isfinite(value) for value in result["vector"])
    assert named["text_urgency"] == 0 and named["text_urgency_available"] == 1
    assert named["previous_urgency"] == 0.6
    assert named["urgency=yes"] == 1 and named["urgency=no"] == 0
    assert named["mode=transfer"] == named["purpose=purchase"] == named["requester=seller"] == 1
    assert named["changed_details=unknown"] == named["secrecy=no"] == 1
    assert named["url_score"] == 0.8 and named["url_score_available"] == 1
    assert named["brand_mismatch"] == named["brand_mismatch_available"] == 0
    assert named["amount_log"] == log1p(10.5)
    assert named["outgoing_count_log"] == 0 and named["outgoing_count_log_available"] == 1
    assert named["amount_percentile_available"] == 0
    assert named["order_known"] == named["speaker_known"] == 1 and named["owner_known"] == 0
    assert result["source_refs"]["previous_urgency"]["turn_id"] == "past"
    assert result["source_refs"]["url_score"]["url"] == "https://example.com"
    assert "risk" not in result["source_refs"]
    assert (fields, text, url, transfer) == before
    for name, options in CATEGORIES.items():
        assert sum(named[f"{name}={option}"] for option in options) == 1


def test_missing_blocks_and_known_zero_have_different_masks():
    # отличает отсутствие блока от его нулевого результата
    for mode in ("url", "transfer"):
        result = risk_features(mode)
        assert result["vector"][:62] == [0] * 62
        named = dict(zip(RISK_FEATURES, result["vector"]))
        assert named["mode=" + mode] == named["extraction_status=unknown"] == 1
        assert named["order_known"] == named["speaker_known"] == named["owner_known"] == 0
    url = {"values": {"url_score": 0, "brand_mismatch": 0, "has_userinfo": 0, "host_is_ip": 0}}
    named = dict(zip(RISK_FEATURES, risk_features("url", url=url)["vector"]))
    assert named["url_score"] == named["brand_mismatch"] == 0
    assert named["url_score_available"] == named["brand_mismatch_available"] == 1


def test_non_transfer_modes_ignore_financial_fields_and_keep_extraction_status():
    # проверяет частичное распознавание и отсутствие лишней анкеты
    text = {"speaker": None, "owner": "unknown", "values": {"text_money_request": 0.9}}
    plain = risk_features("screenshot", text=text, extraction_status="partial")
    extra = risk_features("screenshot", text=text, extraction_status="partial",
                          fields={"purpose": "purchase", "urgency": "yes", "risk": 2},
                          transfer={"values": {"amount_log": 1000, "history_loaded": 1}})
    assert plain == extra
    named = dict(zip(RISK_FEATURES, plain["vector"]))
    assert named["extraction_status=partial"] == named["purpose=unknown"] == named["urgency=unknown"] == 1
    assert named["order_known"] == named["speaker_known"] == named["owner_known"] == 0
    known_owner = risk_features("audio", text={**text, "speaker": "B", "owner": "user"},
                                extraction_status="ok", order_known=True)
    assert known_owner["vector"][-3:] == [1, 1, 1]


def test_schema_matches_form_choices_and_rejects_invalid_inputs():
    # проверяет категории формы и явный отказ при неверном входе
    form = TransferCheckForm()
    for name, options in CATEGORIES.items():
        if name in form.fields:
            assert set(options) == {value for value, label in form.fields[name].choices if value}
    result = risk_features("transfer", fields={"urgency": "", "secrecy": None})
    named = dict(zip(RISK_FEATURES, result["vector"]))
    assert named["urgency=unknown"] == named["secrecy=unknown"] == 1
    with pytest.raises(ValueError, match="mode"):
        risk_features("typo")
    with pytest.raises(ValueError, match="urgency"):
        risk_features("transfer", fields={"urgency": "typo"})
    with pytest.raises(ValueError, match="extraction_status"):
        risk_features("audio", extraction_status="typo")
    with pytest.raises(ValueError, match="конечными"):
        risk_features("url", url={"values": {"url_score": float("nan")}})
