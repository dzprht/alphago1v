import argparse
import json
import shutil
import tempfile
import urllib.request
import zipfile
from hashlib import file_digest
from pathlib import Path

from analysis.artifacts import artifact_files
from analysis.screenshot import OCR_MODELS


def prepare_ocr(directory):
    # скачивает два OCR файла и сохраняет их хеши
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, source in OCR_MODELS.items():
        path = directory / name
        if not path.is_file():
            with tempfile.TemporaryDirectory(prefix="alpha-ocr-") as temporary:
                archive = Path(temporary) / "model.zip"
                with urllib.request.urlopen(source["url"], timeout=60) as response, archive.open("wb") as output:
                    shutil.copyfileobj(response, output)
                with zipfile.ZipFile(archive) as archive:
                    path.write_bytes(archive.read(name))
        with path.open("rb") as stream:
            checksum = file_digest(stream, "md5").hexdigest()
        if checksum != source["md5"]:
            path.unlink()
            raise ValueError("Не совпал исходный хеш OCR " + name)
    files = {name: {**meta, **OCR_MODELS[name]} for name, meta in artifact_files(directory, OCR_MODELS).items()}
    manifest = {"format": "easyocr-v1", "easyocr": "1.7.2", "files": files}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    # подготавливает OCR в указанной папке
    parser = argparse.ArgumentParser(description="Скачать локальные веса EasyOCR ru/en")
    parser.add_argument("directory", nargs="?", default="var/ml/media/easyocr", type=Path)
    args = parser.parse_args()
    prepare_ocr(args.directory)
    print("OCR подготовлен:", args.directory)


if __name__ == "__main__":
    main()
