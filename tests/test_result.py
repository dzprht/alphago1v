import json
from copy import deepcopy

import pytest

from analysis.result import assemble_result
from analysis.risk import RiskModel, risk_features


def decision(risk, sufficiency="sufficient"):
    # готовит один ответ дерева без запуска моделей
    return {"risk": risk, "sufficiency": sufficiency, "conditions": [], "analyzer_version": "risk-test"}


def test_result_keeps_sources_and_separates_model_questionnaire_and_url():
    # проверяет источники и отсутствие повторов в признаках и советах
    source = {"turn_id": "past", "fragment_id": 0, "text": "Срочно переведи деньги"}
    text = {"speaker": "A", "owner": "unknown", "values": {"text_urgency": 0.9},
            "source_refs": {"text_urgency": source}}
    url_source = {"url": "https://user@bank.example.com", "host": "bank.example.com"}
    url = {"values": {"url_score": 0.9, "brand_mismatch": 1, "has_userinfo": 1, "host_is_ip": 0},
           "source_refs": {name: url_source for name in ("url_score", "brand_mismatch", "has_userinfo")}}
    first = risk_features("transfer", text=text, url=url, fields={"urgency": "yes"})
    text = {**text, "values": {"previous_urgency": 0.95}, "source_refs": {"previous_urgency": source}}
    second = risk_features("transfer", text=text, url=url, fields={"urgency": "yes"})
    decisions = [decision(1), decision(2)]
    decisions[1]["conditions"] = [{"feature": "previous_urgency", "operator": ">", "threshold": 0.7}]
    original = deepcopy(([first, second], decisions))
    result = assemble_result([first, second], decisions,
                             thresholds={"text_urgency": 0.8, "url_score": 0.8},
                             versions={"text": "text-test", "url": "url-test"})
    assert result["risk"] == 2 and result["status"] == "ok"
    assert [item["feature"] for item in result["findings"]] == [
        "text_urgency", "url_score", "brand_mismatch", "has_userinfo", "urgency=yes",
    ]
    assert result["findings"][0]["source_ref"] == source
    assert result["findings"][0]["origin"] == "model"
    assert result["findings"][2]["origin"] == "url_structure"
    assert result["findings"][-1]["origin"] == "questionnaire"
    assert result["findings"][-1]["source_ref"] == {"field": "urgency"}
    assert len(result["recommendations"]) == len(set(result["recommendations"]))
    assert len(result["limitations"]) == len(set(result["limitations"]))
    assert result["decision_paths"][1]["conditions"] == decisions[1]["conditions"]
    assert ([first, second], decisions) == original
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_incomplete_dialogue_does_not_become_safe_and_tree_output_is_preserved():
    # проверяет максимум диалога и отказ от общего нулевого риска
    row = risk_features("text")
    for risks, expected, sufficiency in (
        ([0, None], None, "limited"), ([2, None], 2, "limited"),
        ([None, None], None, "insufficient"), ([0, 1], 1, "sufficient"),
    ):
        result = assemble_result([row, row], [decision(value, "insufficient" if value is None else "sufficient")
                                             for value in risks], thresholds={}, versions={})
        assert result["risk"] == expected and result["sufficiency"] == sufficiency
    samples = [risk_features("url", url={"values": {"url_score": score},
                                        "source_refs": {"url_score": {"url": "https://example.com"}}})
               for score in (0, 0.5, 1)]
    model = RiskModel().fit([row for row in samples for _ in range(30)],
                            [risk for risk in (0, 1, 2) for _ in range(30)])
    for row, expected in zip(samples, (0, 1, 2)):
        output = model.predict(row)
        result = assemble_result([row], [output], thresholds={"url_score": 0.8}, versions={})
        assert result["risk"] == expected
        assert result["decision_paths"][0]["conditions"] == output["conditions"]


def test_extraction_failure_partial_and_empty_have_no_overall_risk():
    # проверяет отдельные статусы и независимость достаточности от порога
    for status, sufficiency in (("error", "insufficient"), ("empty", "insufficient"), ("partial", "limited")):
        row = risk_features("audio", url={"values": {"url_score": 0.9},
                                         "source_refs": {"url_score": {"url": "https://example.com"}}},
                            extraction_status=status)
        output = decision(None, "limited" if status == "partial" else "insufficient")
        for thresholds in ({}, {"url_score": 0.5}):
            result = assemble_result([row], [output], thresholds=thresholds, versions={})
            assert result["status"] == status and result["risk"] is None
            assert result["sufficiency"] == sufficiency
            assert any("участника" in item for item in result["limitations"])
    result = assemble_result([], [], thresholds={}, versions={})
    assert result["status"] == "empty" and result["risk"] is None
    assert result["sufficiency"] == "insufficient" and not result["findings"]


def test_thresholds_and_model_versions_change_composite_version_without_risk_change():
    # проверяет явные пороги и версию всех компонентов
    row = risk_features("url", url={"values": {"url_score": 0.7},
                                   "source_refs": {"url_score": {"url": "https://example.com"}}})
    without = assemble_result([row], [decision(1)], thresholds={}, versions={"url": "url-one"})
    assert without["risk"] == 1 and not without["findings"]
    assert any("пороги" in item for item in without["limitations"])
    lower = assemble_result([row], [decision(1)], thresholds={"url_score": 0.7}, versions={"url": "url-one"})
    higher = assemble_result([row], [decision(1)], thresholds={"url_score": 0.8}, versions={"url": "url-one"})
    changed = assemble_result([row], [decision(1)], thresholds={"url_score": 0.7}, versions={"url": "url-two"})
    assert len(lower["findings"]) == 1 and not higher["findings"]
    assert lower["risk"] == higher["risk"] == changed["risk"] == 1
    assert len({item["analyzer_version"] for item in (without, lower, higher, changed)}) == 4
    with pytest.raises(ValueError, match="один результат"):
        assemble_result([row], [], thresholds={}, versions={})
    with pytest.raises(ValueError, match="Пороги"):
        assemble_result([row], [decision(1)], thresholds={"url_score": 1.1}, versions={})


def test_transfer_limits_do_not_claim_new_recipient_or_unusual_amount():
    # проверяет честные ограничения истории без новых числовых порогов
    for history, message in (
        ({"history_loaded": 0}, "не загружена"),
        ({"history_loaded": 1, "history_valid": 0}, "будущие даты"),
        ({"history_loaded": 1, "history_valid": 1}, "Недостаточно исходящих"),
    ):
        row = risk_features("transfer", transfer={"values": history})
        result = assemble_result([row], [decision(0, "limited")], thresholds={}, versions={})
        assert result["risk"] == 0 and result["sufficiency"] == "limited"
        assert any(message in item for item in result["limitations"])
        assert not result["findings"]
        assert any("новизне" in item for item in result["limitations"])
        assert any("не гарантирует безопасность" in item for item in result["limitations"])
