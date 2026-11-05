import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from analysis.whisper import WHISPER_FILES, load_whisper
from ml.prepare_whisper import prepare_whisper


@pytest.fixture
def prepared_whisper(tmp_path, monkeypatch):
    # готовит маленькие файлы вместо настоящих весов
    def download(**options):
        # записывает файлы как при загрузке модели
        for name in WHISPER_FILES:
            (options["local_dir"] / name).write_bytes(name.encode())
        return str(options["local_dir"])

    snapshot = Mock(side_effect=download)
    native = Mock()
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=native))
    directory = tmp_path / "whisper"
    manifest = prepare_whisper(directory)
    return directory, manifest, snapshot, native


def test_prepare_pins_revision_and_loads_verified_local_model(prepared_whisper):
    # проверяет ревизию хеши и загрузку только с диска
    directory, manifest, snapshot, native = prepared_whisper
    snapshot.assert_called_once_with(
        repo_id="Systran/faster-whisper-small",
        revision="536b0662742c02347bc0e980a01041f333bce120",
        local_dir=directory,
        allow_patterns=list(WHISPER_FILES),
    )
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    assert manifest["format"] == "whisper-v1"
    for name in WHISPER_FILES:
        content = (directory / name).read_bytes()
        assert manifest["files"][name] == {
            "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        }
    assert load_whisper(directory) is native.return_value
    native.assert_called_once_with(str(directory), device="cpu", compute_type="int8", local_files_only=True)


@pytest.mark.parametrize("missing", [False, True])
def test_bad_weights_are_rejected_before_native_loading(prepared_whisper, missing):
    # не загружает поврежденные или неполные веса
    directory, manifest, snapshot, native = prepared_whisper
    weights = directory / "model.bin"
    if missing:
        weights.unlink()
    else:
        weights.write_bytes(b"x" * weights.stat().st_size)
    with pytest.raises((ValueError, FileNotFoundError)):
        load_whisper(directory)
    native.assert_not_called()
