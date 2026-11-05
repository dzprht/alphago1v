import builtins
import json
from unittest.mock import Mock, sentinel

import pytest

from analysis.runtime import load_media_model
from checks.media_limits import MAX_AUDIO_SECONDS, MAX_IMAGE_PIXELS


@pytest.fixture(autouse=True)
def clear_media_cache():
    # очищает загруженные распознаватели между проверками
    load_media_model.cache_clear()
    yield
    load_media_model.cache_clear()


def test_missing_media_configuration_does_not_import_recognizers(tmp_path, monkeypatch):
    # оставляет медиа выключенными без загрузки тяжелых библиотек
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        # запрещает импорт распознавания без настроек
        if name in ("analysis.audio", "analysis.screenshot", "easyocr", "faster_whisper", "pyannote.audio"):
            raise AssertionError("Распознавание не должно загружаться")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert load_media_model("audio", "") is None
    assert load_media_model("screenshot", tmp_path) is None
    (tmp_path / "media.json").write_text(json.dumps({"audio": None}))
    assert load_media_model("audio", tmp_path) is None


@pytest.mark.parametrize("with_diarization", [False, True])
def test_media_loads_relative_weights_with_server_limits_and_caches_each_kind(tmp_path, monkeypatch, with_diarization):
    # передает локальные пути и сохраняет отдельные модели двух видов
    from analysis.audio import AudioRecognizer
    from analysis.screenshot import ScreenshotModel

    quality = {"min_word_probability": 0.5, "max_no_speech_probability": 0.6,
               "min_avg_logprob": -1.0, "max_turn_gap": 0.8, "max_seconds": 999}
    versions = {"whisper": "small-fixed", "diarization": "fixed"}
    (tmp_path / "media.json").write_text(json.dumps({
        "audio": {"whisper_dir": "whisper", **({"diarization_dir": "voices"} if with_diarization else {}),
                  "quality": quality, "model_versions": versions},
        "screenshot": {"directory": "ocr", "min_confidence": 0.6, "max_pixels": 999999999},
    }))
    audio = Mock(return_value=sentinel.audio)
    screenshot = Mock(return_value=sentinel.screenshot)
    monkeypatch.setattr(AudioRecognizer, "from_local", audio)
    monkeypatch.setattr(ScreenshotModel, "from_local", screenshot)
    assert load_media_model("audio", tmp_path) is sentinel.audio
    assert load_media_model("screenshot", tmp_path) is sentinel.screenshot
    assert load_media_model("audio", tmp_path) is sentinel.audio
    assert load_media_model("screenshot", tmp_path) is sentinel.screenshot
    audio.assert_called_once_with(tmp_path / "whisper", tmp_path / "voices" if with_diarization else None,
                                  quality={**quality, "max_seconds": MAX_AUDIO_SECONDS}, model_versions=versions)
    screenshot.assert_called_once_with(tmp_path / "ocr", min_confidence=0.6, max_pixels=MAX_IMAGE_PIXELS)


@pytest.mark.parametrize("kind", ["audio", "screenshot"])
def test_configured_media_without_weights_is_an_error(tmp_path, kind):
    # не заменяет отсутствующие веса успешным ответом
    config = {"audio": {"whisper_dir": "absent", "diarization_dir": "absent",
                        "quality": {}, "model_versions": {}},
              "screenshot": {"directory": "absent", "min_confidence": 0.6}}
    (tmp_path / "media.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="локальн"):
        load_media_model(kind, tmp_path)
