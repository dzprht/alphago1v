import json
from copy import deepcopy

import numpy as np
import pytest

from analysis.risk import RISK_INDEX, RiskModel, risk_features, risk_matrix


def url_row(score):
    # готовит один ответ ссылочного блока для дерева
    return risk_features("url", url={
        "values": {"url_score": score},
        "source_refs": {"url_score": {"url": "https://example.com"}},
    })


@pytest.fixture
def trained_model():
    # обучает маленькое дерево на вымышленных признаках
    rows = [url_row(score) for score in (0, 0.5, 1) for _ in range(30)]
    labels = [risk for risk in (0, 1, 2) for _ in range(30)]
    return RiskModel().fit(rows, labels)


def test_tree_predicts_three_classes_and_explains_actual_path(trained_model):
    # проверяет классы источники и точное прохождение условий
    for score, expected in ((0, 0), (0.5, 1), (1, 2)):
        row = url_row(score)
        result = trained_model.predict(row)
        assert result["risk"] == expected and result["sufficiency"] == "sufficient"
        assert 0 < len(result["conditions"]) <= 5
        for condition in result["conditions"]:
            assert condition["feature"] == "url_score" and condition["substantive"]
            assert condition["source_ref"] == {"url": "https://example.com"}
            passed = condition["value"] <= condition["threshold"]
            assert passed == (condition["operator"] == "<=")
        json.dumps(result, allow_nan=False)
    threshold = float(trained_model.model.tree_.threshold[0])
    close = url_row(float(np.nextafter(threshold, np.inf)))
    result = trained_model.predict(close)
    matrix = risk_matrix([close])
    assert result["risk"] == int(trained_model.model.predict(matrix)[0])
    assert result["conditions"][0]["value"] == float(np.float32(close["vector"][RISK_INDEX["url_score"]]))
    assert result["conditions"][0]["operator"] == "<="


def test_tree_skips_unknown_labels_and_rejects_wrong_inputs(trained_model):
    # проверяет обучение без неизвестной метки и сохранение рабочего дерева
    rows = [url_row(score) for score in (0, 0.5, 1) for _ in range(30)]
    labels = [risk for risk in (0, 1, 2) for _ in range(30)]
    other = RiskModel().fit(rows + [url_row(100)], labels + [None])
    np.testing.assert_array_equal(trained_model.model.tree_.threshold, other.model.tree_.threshold)
    for invalid in ([0] * len(rows), labels[:-1], labels[:-1] + [3]):
        with pytest.raises(ValueError):
            trained_model.fit(rows, invalid)
    assert trained_model.predict(url_row(1))["risk"] == 2
    row = url_row(0.5)
    with pytest.raises(ValueError, match="схема"):
        trained_model.predict({**row, "schema_version": "old"})
    with pytest.raises(ValueError, match="конечные"):
        trained_model.predict({**row, "vector": row["vector"][:-1]})
    row["vector"][0] = float("nan")
    with pytest.raises(ValueError, match="конечные"):
        trained_model.predict(row)
    with pytest.raises(ValueError, match="не обучено"):
        RiskModel().predict(url_row(0.5))


def test_empty_partial_and_failed_extraction_do_not_become_safe_results(trained_model):
    # проверяет отказ от общего риска при плохом или пустом вводе
    empty = trained_model.predict(risk_features("url"))
    assert empty["risk"] is None and empty["sufficiency"] == "insufficient"
    assert empty["conditions"] == []
    for status in ("empty", "error", "partial"):
        row = url_row(0)
        row["vector"][RISK_INDEX["extraction_status=unknown"]] = 0
        row["vector"][RISK_INDEX["extraction_status=" + status]] = 1
        result = trained_model.predict(row)
        assert result["risk"] is None
        assert result["sufficiency"] == ("limited" if status == "partial" else "insufficient")
        assert bool(result["conditions"]) == (status == "partial")
    row = risk_features("transfer", url={"values": {"url_score": 1}})
    result = trained_model.predict(row)
    assert result["risk"] == 2 and result["sufficiency"] == "limited"


def test_high_risk_requires_content_not_only_missingness():
    # запрещает высокий риск только по объему истории или пропуску анкеты
    rows, labels = [], []
    for category, risk in (("yes", 0), ("no", 1), ("unknown", 2)):
        rows.extend([risk_features("transfer", fields={"urgency": category},
                                  transfer={"values": {"amount_log": 1}}) for _ in range(30)])
        labels.extend([risk] * 30)
    model = RiskModel().fit(rows, labels)
    result = model.predict(rows[-1])
    assert result["risk"] is None and result["sufficiency"] == "insufficient"
    assert not any(item["substantive"] for item in result["conditions"])
    rows = [risk_features("transfer", transfer={"values": {
        "amount_log": 1, "outgoing_count_log": count,
    }}) for count in (0, 1, 2) for _ in range(30)]
    model.fit(rows, [risk for risk in (0, 1, 2) for _ in range(30)])
    assert model.predict(rows[-1])["risk"] is None


def test_tree_roundtrip_checks_weights_schema_and_feature_order(tmp_path, trained_model):
    # проверяет сохранение и отказ при измененном артефакте
    path = tmp_path / "risk-model"
    trained_model.save(path, training_info={"kind": "unit-test"})
    row = url_row(0.5)
    loaded = RiskModel.load(path)
    assert loaded.predict(row) == trained_model.predict(row)
    original = json.loads((path / "manifest.json").read_text())
    modified = deepcopy(original)
    modified["processing"]["features"].reverse()
    (path / "manifest.json").write_text(json.dumps(modified))
    with pytest.raises(ValueError, match="Несовместимая"):
        RiskModel.load(path)
    (path / "manifest.json").write_text(json.dumps(original))
    weights = path / "tree.joblib"
    weights.write_bytes(weights.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="Несовместимая"):
        RiskModel.load(path)
    with pytest.raises(FileExistsError):
        trained_model.save(path)
