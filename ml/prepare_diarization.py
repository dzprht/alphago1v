import argparse
from pathlib import Path

from analysis.artifacts import prepare_snapshot
from analysis.diarization import DIARIZATION_FILES, MODEL_ID, MODEL_REVISION


def prepare_diarization(directory):
    # скачивает закрепленные модели и записывает их хеши
    return prepare_snapshot(directory, MODEL_ID, MODEL_REVISION, DIARIZATION_FILES, "diarization-v1")


def main():
    # готовит модели в выбранной папке и объясняет отказ в доступе
    from huggingface_hub.errors import GatedRepoError

    parser = argparse.ArgumentParser(description="Подготовить локальную pyannote community-1")
    parser.add_argument("directory", nargs="?", default="var/ml/media/pyannote", type=Path)
    args = parser.parse_args()
    try:
        prepare_diarization(args.directory)
    except GatedRepoError:
        parser.exit(1, "Нужно принять условия модели на https://huggingface.co/pyannote/speaker-diarization-community-1 и выполнить hf auth login\n")
    print("Pyannote подготовлен:", args.directory)


if __name__ == "__main__":
    main()
