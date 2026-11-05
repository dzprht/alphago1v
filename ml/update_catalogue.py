import argparse
import json
from pathlib import Path
import shutil

from analysis.runtime import THRESHOLD_NAMES
from analysis.url import CATALOGUE_PATH, URLModel, url_version


def update_catalogue(source, destination):
    # копирует модели с новым справочником без изменения весов
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("Папка результата уже существует")
    if source == destination or source in destination.parents:
        raise ValueError("Папка результата должна быть вне исходной папки")
    bundle = (source / "calibration.json").is_file()
    url_path = source / "url" if bundle else source
    model = URLModel.load(url_path)
    manifest = json.loads((url_path / "manifest.json").read_text(encoding="utf-8"))
    calibration = None
    if bundle:
        calibration = json.loads((source / "calibration.json").read_text(encoding="utf-8"))
        names = calibration.get("versions", {})
        if not isinstance(names, dict) or set(names) not in ({"text", "url"}, {"text", "url", "risk"}):
            raise ValueError("В комплекте нужны версии text и url")
        versions = {name: json.loads((source / name / "manifest.json").read_text(encoding="utf-8"))[
            "analyzer_version"] for name in names}
        if calibration.get("versions") != versions:
            raise ValueError("Калибровка подготовлена для других версий моделей")
        thresholds = calibration.get("thresholds", {})
        if not isinstance(thresholds, dict) or set(thresholds) != THRESHOLD_NAMES or any(
            type(value) not in (int, float) or not 0 <= value <= 1 for value in thresholds.values()
        ):
            raise ValueError("Нужны восемь числовых порогов от нуля до единицы")
    catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    old_version = model.analyzer_version
    manifest["catalogue"] = catalogue
    manifest["analyzer_version"] = url_version(manifest["model_sha256"], catalogue)
    manifest["catalogue_updates"] = manifest.get("catalogue_updates", []) + [{
        "previous_analyzer_version": old_version,
        "analyzer_version": manifest["analyzer_version"],
        "catalogue_version": catalogue["version"],
        "model_sha256": manifest["model_sha256"],
    }]
    if bundle:
        destination.mkdir(parents=True)
        for name in versions:
            shutil.copytree(source / name, destination / name)
    else:
        shutil.copytree(source, destination)
    output_url = destination / "url" if bundle else destination
    (output_url / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    if calibration is not None:
        calibration["versions"]["url"] = manifest["analyzer_version"]
        (destination / "calibration.json").write_text(
            json.dumps(calibration, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
    return manifest


def main():
    # обновляет справочник в новом каталоге моделей
    parser = argparse.ArgumentParser(description="Обновление справочника организаций без обучения")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        manifest = update_catalogue(args.source, args.destination)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps({"analyzer_version": manifest["analyzer_version"],
                      "catalogue_version": manifest["catalogue"]["version"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
