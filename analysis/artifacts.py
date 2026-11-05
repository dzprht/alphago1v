import json
from hashlib import file_digest
from pathlib import Path


def artifact_files(directory, names, expected=None):
    # считает хеши файлов и сверяет их с сохраненным описанием
    files = {}
    for name in names:
        path = Path(directory) / name
        if not path.is_file():
            raise ValueError("Не найден локальный файл модели " + name)
        with path.open("rb") as stream:
            checksum = file_digest(stream, "sha256").hexdigest()
        files[name] = {"sha256": checksum, "bytes": path.stat().st_size}
        if expected is not None and any(files[name][key] != expected[name][key] for key in ("sha256", "bytes")):
            raise ValueError("Поврежден файл модели " + name)
    return files


def prepare_snapshot(directory, model_id, revision, names, format_name):
    # скачивает закрепленный комплект и записывает его описание
    from huggingface_hub import snapshot_download

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=model_id, revision=revision, local_dir=directory, allow_patterns=list(names))
    manifest = {"format": format_name, "model_id": model_id, "revision": revision,
                "files": artifact_files(directory, names)}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
