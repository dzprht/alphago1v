import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from analysis.features import Turn
from analysis.risk import risk_features
from ml import prepare_risk


@pytest.fixture
def corpus(tmp_path):
    # готовит сценарии четырех частей и независимые примеры нижних задач
    scenarios = [{"case_id": f"case-{i}", "group_id": f"group-{i}", "kind": "url",
                  "split": "train", "input_data": {"url": f"https://training{i}.com"},
                  "target_turn_id": None, "risk": i % 3, "checked_at": "2026-11-04T12:00:00Z"}
                 for i in range(4)]
    scenarios += [{"case_id": split + str(i), "group_id": split + str(i), "kind": "url",
                   "split": split, "input_data": {"url": f"https://{split}{i}.org"},
                   "target_turn_id": None, "risk": i, "checked_at": "2026-11-04T12:00:00Z"}
                  for split in ("validation", "calibration", "test") for i in range(3)]
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in scenarios))
    texts = [{"case_id": f"external-{i}", "group_id": f"external-{i}", "labels": [i] * 7,
              "turns": [Turn("input", "Обычный текст")]} for i in range(2)]
    texts += [{"case_id": "hidden", "group_id": "unrelated", "labels": [0] * 7,
               "turns": [Turn("input", "https://calibration0.org")]}]
    urls = [{"url": f"https://independent{i}.net", "label": i, "group": f"independent{i}.net"} for i in range(2)]
    urls += [{"url": row["input_data"]["url"], "label": 0,
              "group": row["input_data"]["url"].split("//")[1]} for row in scenarios]
    return path, scenarios, texts, urls


def test_oof_plan_excludes_outer_and_fold_groups_cases_and_domains(corpus):
    # запрещает попадание проверяемых источников в обучение нижних моделей
    path, raw, texts, urls = corpus
    scenarios, info = prepare_risk.read_scenarios(path)
    plans = prepare_risk.plan_folds(scenarios, texts, urls, folds=2)
    assert len(plans) == 3 and plans[-1]["fold"] is None
    seen = []
    for plan in plans:
        blocked = set(plan["excluded_domains"])
        assert all(row["group"] not in blocked for row in plan["url_train"])
        assert all(not prepare_risk.case_domains(row) & blocked for row in plan["text_train"])
        assert all(row["group_id"] not in plan["excluded_groups"] for row in plan["text_train"])
        assert "hidden" not in {row["case_id"] for row in plan["text_train"]}
        if plan["fold"] is not None:
            seen.extend(row["case_id"] for row in plan["targets"])
    assert sorted(seen) == sorted(row["case_id"] for row in raw if row["split"] == "train")
    assert all(row["split"] == "validation" for row in plans[-1]["targets"])
    assert all("calibration0.org" in plan["excluded_domains"] for plan in plans)
    conflicting = [{**texts[0], "case_id": "case-0", "group_id": "wrong"}, *texts[1:]]
    with pytest.raises(ValueError, match="разные группы"):
        prepare_risk.plan_folds(scenarios, conflicting, urls, folds=2)


def test_bad_targets_and_cross_split_domains_fail_before_model_loading(corpus):
    # проверяет ошибки разбиения до тяжелого запуска
    path, raw, texts, urls = corpus
    for change in ("target", "domain", "missing_split"):
        rows = deepcopy(raw)
        if change == "target":
            rows[0]["target_turn_id"] = "absent"
        elif change == "domain":
            rows[-1]["input_data"]["url"] = rows[0]["input_data"]["url"]
        else:
            rows = [row for row in rows if row["split"] != "test"]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with pytest.raises(ValueError):
            prepare_risk.read_scenarios(path)


def test_frozen_encoder_cache_reuses_vectors_without_fitting():
    # переиспользует только неизменяемые векторы без обучения энкодера
    backend = Mock(identity={"model_id": "test"}, tokenizer=Mock())
    backend.encode.side_effect = [np.array([[1, 0], [0, 1]]), np.array([[2, 2]])]
    cached = prepare_risk.CachedEncoder(backend)
    first = cached.encode(["a", "b", "a"])
    second = cached.encode(["b", "c"])
    np.testing.assert_array_equal(first, [[1, 0], [0, 1], [1, 0]])
    np.testing.assert_array_equal(second, [[0, 1], [2, 2]])
    assert backend.encode.call_args_list[0].args == (["a", "b"],)
    assert backend.encode.call_args_list[1].args == (["c"],)


def test_feature_export_preserves_risk_and_marks_fold_versions(corpus, tmp_path, monkeypatch):
    # проверяет запись происхождения без запуска настоящего обучения
    path, raw, texts, urls = corpus
    scenarios, info = prepare_risk.read_scenarios(path)
    plans = prepare_risk.plan_folds(scenarios, texts, urls, folds=2)
    text_models = [Mock(analyzer_version=f"text-{i}") for i in range(3)]
    url_models = [Mock(analyzer_version=f"url-{i}") for i in range(3)]
    for model in text_models + url_models:
        model.fit.return_value = model
    monkeypatch.setattr(prepare_risk, "TextModel", Mock(side_effect=text_models))
    monkeypatch.setattr(prepare_risk, "URLModel", Mock(side_effect=url_models))
    monkeypatch.setattr(prepare_risk, "prepare_rows", Mock(return_value=([{}], [[0] * 7])))
    analyzer = Mock()
    analyzer.prepare.return_value = {"target_turn_ids": [None], "rows": [risk_features("url")],
                                     "invalid_urls": False, "versions": {"pipeline": "test"}}
    monkeypatch.setattr(prepare_risk, "Analyzer", Mock(return_value=analyzer))
    output = tmp_path / "prepared"
    report = prepare_risk.prepare_features(plans, output, SimpleNamespace(tokenizer=None), {"scenarios": info})
    assert report["outputs"]["train"]["rows"] == 4
    train = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
    assert all(row["prediction_origin"] == "oof" for row in train)
    assert {row["fold"] for row in train} == {0, 1}
    assert sorted(row["risk"] for row in train) == [0, 0, 1, 2]
    heldout = [json.loads(line) for line in (output / "validation.jsonl").read_text().splitlines()]
    assert all(row["prediction_origin"] == "heldout" and row["fold"] is None for row in heldout)
    assert all(row["model_versions"] == {"text": "text-2", "url": "url-2"} for row in heldout)
    assert not (output / "test.jsonl").exists() and not (output / "calibration.jsonl").exists()
