import json
from pathlib import Path

from analysis.artifacts import artifact_files


MODEL_ID = "pyannote/speaker-diarization-community-1"
MODEL_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
DIARIZATION_FILES = (
    "config.yaml", "embedding/pytorch_model.bin", "segmentation/pytorch_model.bin",
    "plda/plda.npz", "plda/xvec_transform.npz",
)


def load_diarization(directory):
    # проверяет локальные модели и загружает разделение голосов на процессор
    directory = Path(directory).resolve()
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if manifest is not None and (
        manifest["format"] != "diarization-v1"
        or manifest["model_id"] != MODEL_ID or manifest["revision"] != MODEL_REVISION
    ):
        raise ValueError("Несовместимая версия pyannote")
    artifact_files(directory, DIARIZATION_FILES, manifest["files"] if manifest is not None else None)
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(str(directory))
    pipeline.to(torch.device("cpu"))
    return pipeline
