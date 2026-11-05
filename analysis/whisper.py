import json
from pathlib import Path

from analysis.artifacts import artifact_files


MODEL_ID = "Systran/faster-whisper-small"
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
WHISPER_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def load_whisper(directory):
    # проверяет локальные файлы и загружает распознавание на процессор
    directory = Path(directory)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if manifest is not None and (
        manifest["format"] != "whisper-v1"
        or manifest["model_id"] != MODEL_ID or manifest["revision"] != MODEL_REVISION
    ):
        raise ValueError("Несовместимая версия Whisper")
    artifact_files(directory, WHISPER_FILES, manifest["files"] if manifest is not None else None)
    from faster_whisper import WhisperModel

    return WhisperModel(str(directory), device="cpu", compute_type="int8", local_files_only=True)
