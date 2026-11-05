import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from analysis.diarization import DIARIZATION_FILES, MODEL_ID, MODEL_REVISION, load_diarization
from ml.prepare_diarization import prepare_diarization


@pytest.fixture
def prepared_diarization(tmp_path, monkeypatch):
    # готовит маленький комплект вместо настоящих весов
    def download(**options):
        # создает файлы как при скачивании модели
        for name in DIARIZATION_FILES:
            path = options["local_dir"] / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())

    snapshot = Mock(side_effect=download)
    pipeline = Mock()
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot))
    monkeypatch.setitem(sys.modules, "pyannote.audio", SimpleNamespace(Pipeline=pipeline))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(device=Mock(return_value="cpu")))
    directory = tmp_path / "voices"
    manifest = prepare_diarization(directory)
    return directory, manifest, snapshot, pipeline


def test_prepared_diarization_pins_all_local_assets_and_uses_cpu(prepared_diarization):
    # проверяет источник все веса и локальную загрузку на процессор
    directory, manifest, snapshot, pipeline = prepared_diarization
    snapshot.assert_called_once_with(repo_id=MODEL_ID, revision=MODEL_REVISION, local_dir=directory,
                                     allow_patterns=list(DIARIZATION_FILES))
    assert manifest["revision"] == "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    assert set(manifest["files"]) == set(DIARIZATION_FILES)
    for name in DIARIZATION_FILES:
        content = (directory / name).read_bytes()
        assert manifest["files"][name] == {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    assert load_diarization(directory) is pipeline.from_pretrained.return_value
    pipeline.from_pretrained.assert_called_once_with(str(directory.resolve()))
    pipeline.from_pretrained.return_value.to.assert_called_once_with("cpu")


@pytest.mark.parametrize("missing", [False, True])
def test_diarization_checks_assets_before_native_loading(prepared_diarization, missing):
    # останавливает загрузку поврежденных или неполных моделей
    directory, manifest, snapshot, pipeline = prepared_diarization
    weights = directory / "embedding/pytorch_model.bin"
    if missing:
        weights.unlink()
    else:
        content = weights.read_bytes()
        weights.write_bytes(b"x" + content[1:])
    with pytest.raises(ValueError):
        load_diarization(directory)
    pipeline.from_pretrained.assert_not_called()
