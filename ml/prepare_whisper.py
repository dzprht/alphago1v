import argparse
from pathlib import Path

from analysis.artifacts import prepare_snapshot
from analysis.whisper import MODEL_ID, MODEL_REVISION, WHISPER_FILES


def prepare_whisper(directory):
    # скачивает закрепленные веса и записывает хеши файлов
    return prepare_snapshot(directory, MODEL_ID, MODEL_REVISION, WHISPER_FILES, "whisper-v1")


def main():
    # подготавливает веса в указанной папке
    parser = argparse.ArgumentParser(description="Скачать Whisper small для локального распознавания")
    parser.add_argument("directory", nargs="?", default="var/ml/media/whisper-small", type=Path)
    args = parser.parse_args()
    prepare_whisper(args.directory)
    print("Whisper подготовлен:", args.directory)


if __name__ == "__main__":
    main()
