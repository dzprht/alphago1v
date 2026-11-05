import json
from unittest.mock import Mock

import numpy as np
import pytest

from analysis.risk import risk_features
from analysis.runtime import THRESHOLD_NAMES
from ml import calibrate, train_risk


def write_rows(path, rows):
    # сохраняет вымышленные строки для проверки подготовки обучения
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def risk_rows(split="train"):
    # создает три разных класса с независимыми группами
    return [
        {"case_id": f"{split}-case-{risk}", "group_id": f"{split}-group-{risk}", "target_turn_id": None,
         "split": split, "risk": risk, "features": risk_features("url", url={"values": {"url_score": risk / 2}}),
         "prediction_origin": "oof" if split == "train" else "heldout",
         "fold": risk % 2 if split == "train" else None,
         "model_versions": {name: f"{name}-fold-{risk % 2}" if split == "train" else f"{name}-final"
                            for name in ("text", "url")}}
        for risk in (0, 1, 2)
    ]


def calibration_rows(versions):
    # создает оценки с одинаковым лучшим F1 на двух порогах
    return [{"case_id": f"cal-case-{index}", "group_id": f"cal-group-{index}", "target_turn_id": None,
             "split": "calibration", "prediction_origin": "heldout", "model_versions": versions,
             "domains": [f"cal-{index}.example.com"],
             "labels": dict.fromkeys(THRESHOLD_NAMES, label), "scores": dict.fromkeys(THRESHOLD_NAMES, score)}
            for index, (label, score) in enumerate(((1, 0.1), (0, 0.2), (0, 0.3), (1, 0.4), (None, 0.99)))]


@pytest.mark.parametrize("leak", ["case_id", "group_id"])
def test_risk_training_rejects_leakage_before_model_fit(tmp_path, monkeypatch, capsys, leak):
    # запрещает общий случай или группу до запуска обучения
    train = risk_rows()
    validation = risk_rows("validation")
    validation[0][leak] = train[0][leak]
    train_path, validation_path = tmp_path / "train.jsonl", tmp_path / "validation.jsonl"
    write_rows(train_path, train)
    write_rows(validation_path, validation)
    classifier = Mock(side_effect=AssertionError("fit не должен запускаться"))
    monkeypatch.setattr(train_risk, "RiskModel", classifier)
    monkeypatch.setattr("sys.argv", ["train", str(train_path), str(tmp_path / "result"),
                                     "--validation", str(validation_path)])
    with pytest.raises(SystemExit) as error:
        train_risk.main()
    assert error.value.code == 2 and "пересекаются" in capsys.readouterr().err
    classifier.assert_not_called()
    assert not (tmp_path / "result").exists()


def test_risk_reader_counts_unknowns_and_metrics_report_refusals(tmp_path):
    # исключает неизвестную метку и отдельно считает отказы
    rows = risk_rows()
    rows.append({**rows[1], "case_id": "unknown-case", "group_id": "unknown-group", "risk": None})
    path = tmp_path / "train.jsonl"
    write_rows(path, rows)
    features, labels, info = train_risk.read_rows(path, "train")
    assert labels == [0, 1, 2, None] and info["known_labels"] == 3
    assert info["rows"] == 4 and info["folds"] == [0, 1]
    assert info["model_versions"]["text"] == ["text-fold-0", "text-fold-1"]
    model = Mock()
    model.model.predict.return_value = np.array([0, 1, 2])
    model.predict.side_effect = [{"risk": 0}, {"risk": None}, {"risk": 2}]
    metrics = train_risk.validation_metrics(model, features, labels)
    assert metrics["macro_f1"] == metrics["recall_high_risk"] == 1
    assert metrics["decision_coverage"] == pytest.approx(2 / 3)
    assert model.predict.call_count == 3


def test_readers_reject_wrong_split_and_inconsistent_group_models(tmp_path):
    # проверяет единый fold группы и запрет обучения на тесте
    rows = risk_rows()
    path = tmp_path / "rows.jsonl"
    rows[1]["group_id"] = rows[0]["group_id"]
    write_rows(path, rows)
    with pytest.raises(ValueError, match="группа"):
        train_risk.read_rows(path, "train")
    with pytest.raises(ValueError, match="только train"):
        train_risk.read_rows(path, "test")
    rows = risk_rows("validation")
    rows[1]["model_versions"]["url"] = "other-model"
    write_rows(path, rows)
    with pytest.raises(ValueError, match="один итоговый комплект"):
        train_risk.read_rows(path, "validation")
    versions = {"text": "text-final", "url": "url-final", "risk": "risk-final"}
    rows = calibration_rows(versions)
    rows[0]["split"] = "test"
    write_rows(path, rows)
    with pytest.raises(ValueError, match="calibration"):
        calibrate.read_rows(path, versions)
    rows[0]["split"], rows[0]["fold"] = "calibration", 0
    write_rows(path, rows)
    with pytest.raises(ValueError, match="heldout"):
        calibrate.read_rows(path, versions)


def test_calibration_uses_known_labels_and_higher_tied_threshold(tmp_path):
    # выбирает больший порог без влияния неизвестных меток
    versions = {"text": "text-final", "url": "url-final", "risk": "risk-final"}
    path = tmp_path / "calibration.jsonl"
    write_rows(path, calibration_rows(versions))
    values, info = calibrate.read_rows(path, versions)
    thresholds, metrics = calibrate.calibrate(values)
    assert set(thresholds.values()) == {0.4}
    assert info["rows"] == 5
    for metric in metrics.values():
        assert metric["known_labels"] == 4 and metric["unknown_labels"] == 1
        assert metric["f1"] == pytest.approx(2 / 3)
    values["url_score"]["labels"] = [1, 1, 1, 1]
    with pytest.raises(ValueError, match="url_score"):
        calibrate.calibrate(values)


def test_calibration_export_binds_models_and_rejects_training_overlap(tmp_path, monkeypatch, capsys):
    # сохраняет полный комплект порогов только после проверки происхождения
    versions = {"text": "text-final", "url": "url-final"}
    for name, version in versions.items():
        folder = tmp_path / name
        folder.mkdir()
        manifest = {"analyzer_version": version, "training": {
            "selected_group_ids": ["train-group"], "selected_case_ids": ["train-case"],
            "selected_domains": ["train.example.com"],
        }}
        (folder / "manifest.json").write_text(json.dumps(manifest))
    path, output = tmp_path / "scores.jsonl", tmp_path / "calibration.json"
    write_rows(path, calibration_rows(versions))
    monkeypatch.setattr("sys.argv", ["calibrate", str(path), str(tmp_path)])
    text_manifest_path = tmp_path / "text/manifest.json"
    original = json.loads(text_manifest_path.read_text())
    for field, value in (("selected_group_ids", "cal-group-0"), ("selected_case_ids", "cal-case-0"),
                         ("selected_domains", "cal-0.example.com")):
        manifest = json.loads(json.dumps(original))
        manifest["training"][field] = [value]
        text_manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(SystemExit):
            calibrate.main()
        assert "пересекается" in capsys.readouterr().err and not output.exists()
    manifest["training"].pop("selected_domains")
    text_manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SystemExit):
        calibrate.main()
    assert "отсутствует" in capsys.readouterr().err and not output.exists()
    text_manifest_path.write_text(json.dumps(original))
    rows = calibration_rows(versions)
    rows[0]["model_versions"] = {**versions, "text": "wrong-model"}
    write_rows(path, rows)
    with pytest.raises(SystemExit):
        calibrate.main()
    assert "версии" in capsys.readouterr().err and not output.exists()
    write_rows(path, calibration_rows(versions))
    calibrate.main()
    result = json.loads(output.read_text())
    assert result["versions"] == versions and set(result["thresholds"]) == THRESHOLD_NAMES
    assert result["source"]["rows"] == 5
