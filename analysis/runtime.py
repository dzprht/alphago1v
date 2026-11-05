import json
from functools import lru_cache
from hashlib import file_digest, sha256
from pathlib import Path

from analysis.pipeline import Analyzer
from analysis.text import E5Encoder, TEXT_LABELS, TextModel
from analysis.url import URLModel
from checks.media_limits import MAX_AUDIO_SECONDS, MAX_IMAGE_PIXELS


E5_CACHE = Path(__file__).resolve().parents[1] / "var/ml/cache"
THRESHOLD_NAMES = {"text_" + label for label in TEXT_LABELS} | {"url_score"}
SELECTED_FILES = (
    "rubert/weights.pt", "rubert/config.json", "rubert/tokenizer.json",
    "rubert/tokenizer_config.json", "rubert/special_tokens_map.json", "rubert/vocab.txt",
    "fragment_tokenizer/config.json", "fragment_tokenizer/tokenizer.json",
    "fragment_tokenizer/tokenizer_config.json", "fragment_tokenizer/special_tokens_map.json",
    "fragment_tokenizer/sentencepiece.bpe.model", "url_cnn/weights.pt", "thresholds.json",
)


@lru_cache(maxsize=1)
def load_analyzer(directory):
    # загружает готовые локальные модели один раз на процесс
    if not directory:
        return None
    directory = Path(directory)
    selected = (directory / "MANIFEST.json").is_file()
    if selected:
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        if manifest["format"] != "selected-models-v1":
            raise ValueError("Неизвестный формат выбранных моделей")
        checksums = {}
        for name in SELECTED_FILES:
            path = directory / name
            expected = manifest["files"][name]
            with path.open("rb") as stream:
                checksum = file_digest(stream, "sha256").hexdigest()
            if path.stat().st_size != expected["bytes"] or checksum != expected["sha256"]:
                raise ValueError("Поврежден файл модели " + name)
            checksums[name] = checksum
        calibration = {"thresholds": json.loads((directory / "thresholds.json").read_text(encoding="utf-8"))}
    else:
        calibration = json.loads((directory / "calibration.json").read_text(encoding="utf-8"))
    thresholds = calibration["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != THRESHOLD_NAMES or any(
        type(value) not in (int, float) or not 0 <= value <= 1 for value in thresholds.values()
    ):
        raise ValueError("Нужны пороги всех семи текстовых признаков и URL")
    if selected:
        from analysis.selected import CNNModel, RuBERTModel

        identity = json.dumps({"adapter": "selected-v1", "files": checksums}, sort_keys=True).encode("utf-8")
        version = sha256(identity).hexdigest()[:16]
        return Analyzer(RuBERTModel(directory, "rubert-" + version),
                        CNNModel(directory, "url-cnn-" + version), thresholds)
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
