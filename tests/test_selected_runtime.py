import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from analysis import runtime


@pytest.fixture
def selected_bundle(tmp_path, monkeypatch):
    # готовит маленький комплект без настоящих весов
    runtime.load_analyzer.cache_clear()
    names = runtime.SELECTED_FILES
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    thresholds = {name: 0.42 for name in runtime.THRESHOLD_NAMES}
    (tmp_path / "thresholds.json").write_text(json.dumps(thresholds))
    manifest = {"format": "selected-models-v1", "files": {}}
    for name in names:
        content = (tmp_path / name).read_bytes()
        manifest["files"][name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    module = ModuleType("analysis.selected")
    module.RuBERTModel = Mock(return_value=SimpleNamespace(analyzer_version="rubert-test"))
    module.CNNModel = Mock(return_value=SimpleNamespace(analyzer_version="cnn-test"))
    monkeypatch.setitem(sys.modules, "analysis.selected", module)
    old_encoder = Mock(side_effect=AssertionError("Старый энкодер не нужен"))
    monkeypatch.setattr(runtime, "E5Encoder", old_encoder)
    yield tmp_path, manifest, thresholds, module, old_encoder
    runtime.load_analyzer.cache_clear()


def test_selected_models_load_once_with_frozen_thresholds(selected_bundle):
    # загружает обе модели один раз и сохраняет пороги
    path, manifest, thresholds, module, old_encoder = selected_bundle
    analyzer = runtime.load_analyzer(path)
    assert runtime.load_analyzer(path) is analyzer
    assert analyzer.text_model is module.RuBERTModel.return_value
    assert analyzer.url_model is module.CNNModel.return_value
    assert analyzer.thresholds == thresholds and not hasattr(analyzer, "risk_model")
    for constructor in (module.RuBERTModel, module.CNNModel):
        constructor.assert_called_once()
        assert constructor.call_args.args[0] == path
        assert isinstance(constructor.call_args.args[1], str)
    old_encoder.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_selected_weights_must_match_manifest(selected_bundle, missing):
    # останавливает загрузку при повреждении или потере весов
    path, manifest, thresholds, module, old_encoder = selected_bundle
    weights = path / "rubert/weights.pt"
    if missing:
        weights.unlink()
    else:
        weights.write_bytes(b"other weights")
    with pytest.raises((ValueError, FileNotFoundError)):
        runtime.load_analyzer(path)
    module.RuBERTModel.assert_not_called()
    module.CNNModel.assert_not_called()
    old_encoder.assert_not_called()


def test_selected_thresholds_are_checked_before_model_loading(selected_bundle):
    # запрещает неполные пороги даже с правильным хешем
    path, manifest, thresholds, module, old_encoder = selected_bundle
    for values in ({}, {**thresholds, "url_score": True}, {**thresholds, "url_score": float("nan")}):
        content = json.dumps(values).encode()
        (path / "thresholds.json").write_bytes(content)
        manifest["files"]["thresholds.json"] = {
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
        }
        (path / "MANIFEST.json").write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="пороги"):
            runtime.load_analyzer(path)
    module.RuBERTModel.assert_not_called()
    module.CNNModel.assert_not_called()
    old_encoder.assert_not_called()
