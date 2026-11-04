import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from analysis import runtime


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    # готовит локальные описания и подменяет тяжелую загрузку
    runtime.load_analyzer.cache_clear()
    models = {}
    for name in ("text", "url", "risk"):
        path = tmp_path / name
        path.mkdir()
        models[name] = SimpleNamespace(analyzer_version=name + "-test")
        (path / "manifest.json").write_text(json.dumps({"analyzer_version": models[name].analyzer_version}))
    config = {"thresholds": {name: 0.8 for name in runtime.THRESHOLD_NAMES},
              "versions": {name: model.analyzer_version for name, model in models.items()}}
    (tmp_path / "calibration.json").write_text(json.dumps(config))
    encoder = Mock()
    monkeypatch.setattr(runtime, "E5Encoder", encoder)
    for cls, name in ((runtime.TextModel, "text"), (runtime.URLModel, "url"), (runtime.RiskModel, "risk")):
        monkeypatch.setattr(cls, "load", Mock(return_value=models[name]))
    yield tmp_path, config, models, encoder
    runtime.load_analyzer.cache_clear()


def test_runtime_loads_once_and_keeps_downloads_disabled(bundle):
    # проверяет общий комплект и повторное использование моделей
    path, config, models, encoder = bundle
    first = runtime.load_analyzer(path)
    assert runtime.load_analyzer(path) is first
    assert first.text_model is models["text"] and first.url_model is models["url"]
    assert first.risk_model is models["risk"] and first.thresholds == config["thresholds"]
    encoder.assert_called_once_with(allow_download=False, cache_dir=runtime.E5_CACHE)
    runtime.TextModel.load.assert_called_once_with(path / "text", encoder.return_value)
    runtime.URLModel.load.assert_called_once_with(path / "url")
    runtime.RiskModel.load.assert_called_once_with(path / "risk")


def test_wrong_calibration_is_rejected_before_loading_encoder_and_can_be_fixed(bundle):
    # запрещает чужие версии и дает повторить загрузку после исправления
    path, config, models, encoder = bundle
    original = json.dumps(config)
    config["versions"]["url"] = "url-other"
    (path / "calibration.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="других версий"):
        runtime.load_analyzer(path)
    encoder.assert_not_called()
    (path / "calibration.json").write_text(original)
    assert runtime.load_analyzer(path).url_model is models["url"]


def test_missing_or_bad_thresholds_fail_before_model_loading(bundle):
    # проверяет полный набор порогов без неявных значений
    path, config, models, encoder = bundle
    for thresholds in ({}, {**config["thresholds"], "url_score": float("nan")},
                       {**config["thresholds"], "url_score": 1.1}):
        (path / "calibration.json").write_text(json.dumps({**config, "thresholds": thresholds}))
        with pytest.raises(ValueError, match="пороги"):
            runtime.load_analyzer(path)
    encoder.assert_not_called()
    (path / "risk/manifest.json").unlink()
    (path / "calibration.json").write_text(json.dumps(config))
    with pytest.raises(FileNotFoundError):
        runtime.load_analyzer(path)
    encoder.assert_not_called()


def test_unconfigured_runtime_does_not_load_models(bundle):
    # оставляет анализ выключенным без указанного комплекта
    path, config, models, encoder = bundle
    assert runtime.load_analyzer("") is None
    encoder.assert_not_called()
