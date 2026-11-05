import json
from functools import lru_cache
from pathlib import Path

from analysis.pipeline import Analyzer
from analysis.text import E5Encoder, TEXT_LABELS, TextModel
from analysis.url import URLModel
from checks.media_limits import MAX_AUDIO_SECONDS, MAX_IMAGE_PIXELS


E5_CACHE = Path(__file__).resolve().parents[1] / "var/ml/cache"
THRESHOLD_NAMES = {"text_" + label for label in TEXT_LABELS} | {"url_score"}


@lru_cache(maxsize=1)
def load_analyzer(directory):
    # загружает готовые локальные модели один раз на процесс
    if not directory:
        return None
    directory = Path(directory)
    calibration = json.loads((directory / "calibration.json").read_text(encoding="utf-8"))
    thresholds = calibration["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != THRESHOLD_NAMES or any(
        type(value) not in (int, float) or not 0 <= value <= 1 for value in thresholds.values()
    ):
        raise ValueError("Нужны пороги всех семи текстовых признаков и URL")
    versions = {
        name: json.loads((directory / name / "manifest.json").read_text(encoding="utf-8"))["analyzer_version"]
        for name in ("text", "url")
    }
    declared = calibration.get("versions", {})
    if not isinstance(declared, dict) or set(declared) not in ({"text", "url"}, {"text", "url", "risk"}) or any(
        declared[name] != version for name, version in versions.items()
    ):
        raise ValueError("Пороги подготовлены для других версий моделей")
    encoder = E5Encoder(allow_download=False, cache_dir=E5_CACHE)
    return Analyzer(TextModel.load(directory / "text", encoder), URLModel.load(directory / "url"), thresholds)


@lru_cache(maxsize=2)
def load_media_model(kind, directory):
    # загружает настроенное распознавание только из локальных файлов
    if not directory:
        return None
    directory = Path(directory)
    path = directory / "media.json"
    if not path.is_file():
        return None
    config = json.loads(path.read_text(encoding="utf-8")).get(kind)
    if not config:
        return None
    if kind == "audio":
        from analysis.audio import AudioRecognizer

        return AudioRecognizer.from_local(
            directory / config["whisper_dir"], directory / config["diarization_dir"],
            quality={**config["quality"], "max_seconds": MAX_AUDIO_SECONDS},
            model_versions=config["model_versions"],
        )
    if kind == "screenshot":
        from analysis.screenshot import ScreenshotModel

        return ScreenshotModel.from_local(directory / config["directory"],
                                          min_confidence=config["min_confidence"], max_pixels=MAX_IMAGE_PIXELS)
    raise ValueError("Неизвестный вид медиа")
