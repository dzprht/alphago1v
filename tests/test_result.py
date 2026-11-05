import json
from copy import deepcopy

import pytest

from analysis.result import assemble_result
from analysis.risk import risk_features


def test_result_preserves_sources_and_combines_evidence_without_duplicates():
    # сохраняет цитаты анкету и адреса без повторных карточек
    source = {"turn_id": "past", "text": "Срочно переведи деньги"}
    text = {"speaker": "A", "owner": "unknown", "values": {"text_urgency": 0.9},
            "source_refs": {"text_urgency": source}}
    url_source = {"url": "https://user@bank.example.com", "host": "bank.example.com"}
    url = {"values": {"url_score": 0.9, "brand_mismatch": 1, "has_userinfo": 1},
           "source_refs": {name: url_source for name in ("url_score", "brand_mismatch", "has_userinfo")}}
    row = risk_features("transfer", text=text, url=url, fields={"urgency": "yes"})
    before = deepcopy(row)
    result = assemble_result([row, row], thresholds={"text_urgency": 0.8, "url_score": 0.8},
                             versions={"text": "text-test", "url": "url-test"})
    assert result["policy"] == "warning-rules-v1" and result["risk"] is None
    assert result["warning"] and result["status"] == "ok"
    assert [item["feature"] for item in result["findings"]] == [
        "text_urgency", "url_score", "brand_mismatch", "has_userinfo",
    ]
    finding = result["findings"][0]
    assert finding["source_ref"] == source and finding["origin"] == "combined"
    assert finding["origins"] == ["model", "questionnaire"] and finding["score"] == 1
    assert finding["evidence"][1]["source_ref"] == {"field": "urgency"}
    assert result["findings"][1]["severity"] == "banner"
    assert result["findings"][2]["origin"] == "url_structure"
    assert result["findings"][2]["severity"] == "remark"
    assert len(result["recommendations"]) == len(set(result["recommendations"]))
    assert row == before
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_thresholds_and_versions_change_the_result_without_inventing_a_risk_class():
    # применяет точный порог и сохраняет версию правил вместе с моделями
    row = risk_features("url", url={"values": {"url_score": 0.7},
                                   "source_refs": {"url_score": {"url": "https://example.com"}}})
    without = assemble_result([row], thresholds={}, versions={"url": "one"})
    assert not without["warning"] and without["sufficiency"] == "insufficient"
    assert without["combined_signals"][0]["flags"]["F"] is None
    assert any("пороги" in item for item in without["limitations"])
    lower = assemble_result([row], thresholds={"url_score": 0.7}, versions={"url": "one"})
    higher = assemble_result([row], thresholds={"url_score": 0.8}, versions={"url": "one"})
    changed = assemble_result([row], thresholds={"url_score": 0.7}, versions={"url": "two"})
    assert lower["warning"] and not higher["warning"]
    assert all(item["risk"] is None for item in (without, lower, higher, changed))
    assert len({item["analyzer_version"] for item in (without, lower, higher, changed)}) == 4
    for value in (1.1, float("nan"), True):
        with pytest.raises(ValueError, match="Пороги"):
            assemble_result([row], thresholds={"url_score": value}, versions={})


def test_partial_media_keeps_positive_evidence_and_empty_media_stays_insufficient():
    # сохраняет найденную ссылку при неполном распознавании
    row = risk_features("audio", url={"values": {"url_score": 0.9},
                                     "source_refs": {"url_score": {"url": "https://example.com"}}},
                        extraction_status="partial")
    result = assemble_result([row], thresholds={"url_score": 0.8}, versions={})
    assert result["warning"] and result["risk"] is None
    assert result["status"] == "partial" and result["sufficiency"] == "limited"
    assert any("участника" in item for item in result["limitations"])
    result = assemble_result([], thresholds={}, versions={})
    assert result["status"] == "empty" and result["sufficiency"] == "insufficient"
    assert not result["warning"] and not result["findings"]


def test_unavailable_history_does_not_turn_into_positive_flags():
    # оставляет недостаточные данные неизвестными вместо опасных признаков
    for history, message in (({"history_loaded": 0}, "не загружена"),
                             ({"history_loaded": 1, "history_valid": 0}, "будущие даты"),
                             ({"history_loaded": 1, "history_valid": 1}, "20 исходящих")):
        row = risk_features("transfer", transfer={"values": history})
        result = assemble_result([row], thresholds={}, versions={})
        assert result["risk"] is None and result["sufficiency"] == "insufficient"
        assert not result["warning"] and not result["findings"]
        flags = result["combined_signals"][0]["flags"]
        assert flags["H"] is None and flags["N"] is None
        assert any(message in item for item in result["limitations"])
        assert any("не гарантирует безопасность" in item for item in result["limitations"])
