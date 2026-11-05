from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from analysis.result import assemble_result
from analysis.risk import risk_features


@pytest.mark.parametrize("feature,field,answer", [
    ("text_urgency", "urgency", "yes"),
    ("text_secrecy", "secrecy", "yes"),
    ("text_authority_claim", "requester", "claimed_organization"),
])
def test_user_can_supply_a_signal_without_model_text_or_threshold(feature, field, answer):
    # показывает положительный ответ даже без текстовой модели
    row = risk_features("transfer", fields={field: answer})
    before = deepcopy(row)
    result = assemble_result([row], thresholds={}, versions={})
    signal = result["combined_signals"][0]["values"][feature]
    assert signal == {"score": 1, "model_score": None, "user_reported": True, "detected": True}
    finding = next(item for item in result["findings"] if item["feature"] == feature)
    assert finding["origin"] == "questionnaire"
    assert finding["source_ref"] == {"field": field}
    assert result["risk"] is None
    assert result["warning"] == (field == "requester")
    html = render_to_string("checks/result.html", {"check": SimpleNamespace(
        status="completed", result=result, extracted_text="", analyzer_version="test",
    )})
    assert "Указано вами" in html
    assert ("Перепроверьте перед выполнением действия" if result["warning"] else "Есть сведения для перепроверки") in html
    assert "Явных признаков опасности не обнаружено" not in html
    assert row == before


@pytest.mark.parametrize("answer,score,expected_origins", [
    ("yes", 0.2, ["questionnaire"]),
    ("yes", 0.9, ["model", "questionnaire"]),
    ("no", 0.9, ["model"]),
    ("unknown", 0.9, ["model"]),
])
def test_merge_preserves_model_score_sources_and_both_positive_origins(answer, score, expected_origins):
    # сохраняет максимум и оба источника без подавления модели
    source = {"turn_id": "request", "text": "Срочно переведите <script>"}
    text = {"speaker": None, "owner": "unknown", "values": {"text_urgency": score},
            "source_refs": {"text_urgency": source}}
    row = risk_features("transfer", text=text, fields={"urgency": answer})
    before = deepcopy(row)
    result = assemble_result([row], thresholds={"text_urgency": 0.8}, versions={})
    signal = result["combined_signals"][0]["values"]["text_urgency"]
    assert signal["score"] == (1 if answer == "yes" else score)
    assert signal["model_score"] == score and signal["detected"]
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["origins"] == expected_origins
    assert [item["origin"] for item in finding["evidence"]] == expected_origins
    html = render_to_string("checks/result.html", {"check": SimpleNamespace(
        status="completed", result=result, extracted_text="", analyzer_version="test",
    )})
    assert ("Указано вами" in html) == (answer == "yes")
    assert ("Обнаружено в сообщении" in html) == (score >= 0.8)
    if score >= 0.8:
        assert "&lt;script&gt;" in html and "<script>" not in html
        assert finding["evidence"][0]["source_ref"] == source
    assert row == before


def test_merge_keeps_unknowns_separate_fields_and_extraction_refusal():
    # не превращает пропуски и другие поля в текстовые сигналы
    row = risk_features("transfer", fields={"urgency": "no", "changed_details": "yes"})
    result = assemble_result([row], thresholds={}, versions={})
    assert result["combined_signals"][0]["values"]["text_urgency"]["score"] is None
    assert [item["feature"] for item in result["findings"]] == ["changed_details=yes"]
    row = risk_features("audio", fields={"urgency": "yes"}, extraction_status="partial")
    result = assemble_result([row], thresholds={}, versions={})
    assert not result["findings"] and not result["warning"]
    assert result["risk"] is None and result["sufficiency"] == "insufficient"
