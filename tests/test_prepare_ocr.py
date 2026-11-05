import hashlib
import io
import json
import sys
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from analysis import screenshot
from ml.prepare_ocr import prepare_ocr


@pytest.fixture
def prepared_ocr(tmp_path, monkeypatch):
    # заменяет сеть маленькими архивами с тестовыми весами
    archives = {}
    for name, source in screenshot.OCR_MODELS.items():
        content = name.encode()
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(name, content)
        archives[source["url"]] = data.getvalue()
        monkeypatch.setitem(screenshot.OCR_MODELS, name, {**source, "md5": hashlib.md5(content).hexdigest()})

    def download(url, timeout):
        # возвращает архив выбранной модели
        return io.BytesIO(archives[url])

    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=download))
    native = Mock()
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=native))
    monkeypatch.setattr(screenshot, "version", Mock(return_value="1.7.2"))
    path = tmp_path / "ocr"
    manifest = prepare_ocr(path)
    return path, manifest, native


def test_prepared_ocr_keeps_sources_and_loads_only_local_files(prepared_ocr):
    # проверяет хеши источники и локальную загрузку двух сетей
    path, manifest, native = prepared_ocr
    assert json.loads((path / "manifest.json").read_text()) == manifest
    assert manifest["format"] == "easyocr-v1" and manifest["easyocr"] == "1.7.2"
    for name, meta in manifest["files"].items():
        content = (path / name).read_bytes()
        assert meta["sha256"] == hashlib.sha256(content).hexdigest()
        assert meta["md5"] == hashlib.md5(content).hexdigest()
        assert meta["url"].startswith("https://github.com/JaidedAI/EasyOCR/releases/")
    model = screenshot.ScreenshotModel.from_local(path, min_confidence=0.5, max_pixels=4000000)
    assert model.reader is native.return_value
    assert native.call_args.kwargs["download_enabled"] is False


@pytest.mark.parametrize("missing", [False, True])
def test_incomplete_or_changed_ocr_is_rejected(prepared_ocr, missing):
    # запрещает запуск с поврежденными или потерянными весами
    path, manifest, native = prepared_ocr
    weights = path / "cyrillic_g2.pth"
    if missing:
        weights.unlink()
    else:
        data = weights.read_bytes()
        weights.write_bytes(b"X" + data[1:])
    with pytest.raises(ValueError):
        screenshot.ScreenshotModel.from_local(path, min_confidence=0.5, max_pixels=4000000)
    native.assert_not_called()
