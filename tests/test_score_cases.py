from unittest.mock import Mock

import pytest

from analysis.risk import risk_features
from analysis.runtime import THRESHOLD_NAMES
from ml.score_cases import score_cases


def test_scores_keep_unknown_and_skip_other_splits_without_tree_prediction():
    # сохраняет недоступные оценки отдельно от настоящего нуля
    labels = {name: None for name in THRESHOLD_NAMES}
    labels["url_score"] = 0
    rows = [{"case_id": "cal", "group_id": "cal", "target_turn_id": None,
             "split": "calibration", "kind": "url", "input_data": {"url": "https://example.com"},
             "checked_at": None, "labels": labels},
            {"case_id": "test", "split": "test"}]
    analyzer = Mock()
    analyzer.prepare.return_value = {"target_turn_ids": [None], "rows": [risk_features("url", url={
        "values": {"url_score": 0},
    })]}
    versions = {"text": "text-test", "url": "url-test", "risk": "risk-test"}
    result = score_cases(rows, analyzer, versions, "calibration")
    assert len(result) == 1 and result[0]["case_id"] == "cal"
    assert result[0]["scores"]["url_score"] == 0
    assert result[0]["scores"]["text_urgency"] is None
    assert result[0]["labels"] == labels and result[0]["model_versions"] == versions
    analyzer.prepare.assert_called_once()
    analyzer.analyze.assert_not_called()
    analyzer.prepare.return_value = {"target_turn_ids": [None], "rows": [risk_features("text", text={
        "values": {"text_urgency": 0.1, "previous_urgency": 0.8, "previous_secrecy": 0},
        "speaker": "A", "owner": "unknown",
    })]}
    scores = score_cases(rows, analyzer, versions, "calibration")[0]["scores"]
    assert scores["text_urgency"] == 0.8 and scores["text_secrecy"] == 0
    assert scores["url_score"] is None
    with pytest.raises(ValueError, match="calibration"):
        score_cases(rows, analyzer, versions, "train")
    with pytest.raises(ValueError, match="восемь"):
        score_cases([{**rows[0], "labels": {}}], analyzer, versions, "calibration")
