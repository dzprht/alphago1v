import json
from functools import lru_cache
from pathlib import Path

from analysis.pipeline import Analyzer
from analysis.risk import RiskModel
from analysis.text import E5Encoder, TEXT_LABELS, TextModel
from analysis.url import URLModel


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
    if set(thresholds) != THRESHOLD_NAMES or any(not 0 <= value <= 1 for value in thresholds.values()):
        raise ValueError("Нужны пороги всех семи текстовых признаков и URL")
    versions = {
        name: json.loads((directory / name / "manifest.json").read_text(encoding="utf-8"))["analyzer_version"]
        for name in ("text", "url", "risk")
    }
    if calibration["versions"] != versions:
        raise ValueError("Пороги подготовлены для других версий моделей")
    encoder = E5Encoder(allow_download=False, cache_dir=E5_CACHE)
    return Analyzer(TextModel.load(directory / "text", encoder), URLModel.load(directory / "url"),
                    RiskModel.load(directory / "risk"), thresholds)
