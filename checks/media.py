from pathlib import Path
from tempfile import TemporaryDirectory


def extract_upload(kind, upload, model):
    # распознает временный файл и удаляет его после обработки
    with TemporaryDirectory(prefix="alpha-check-") as folder:
        path = Path(folder) / ("input.wav" if kind == "audio" else "input.image")
        with path.open("wb") as output:
            for chunk in upload.chunks():
                output.write(chunk)
        return model.recognize(path) if kind == "audio" else model.extract(path)
