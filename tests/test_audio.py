import json
import sys
import wave
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from analysis.audio import AudioRecognizer, read_wav, speaker_for_word, words_to_turns
from analysis.diarization import DIARIZATION_FILES


QUALITY = {"min_word_probability": 0.7, "min_avg_logprob": -1,
           "max_no_speech_probability": 0.6, "max_seconds": 10, "max_turn_gap": 0.5}
VERSIONS = {"whisper": "test-asr", "diarization": "test-speakers"}


def write_wav(path, channels=1, rate=16000):
    # создает короткий звуковой файл без скачивания данных
    samples = np.zeros((rate * 2, channels), dtype="<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())
    return path


def segment(words, text="", start=0, end=1):
    # готовит ответ распознавания с временем и оценками
    return SimpleNamespace(words=[SimpleNamespace(start=left, end=right, word=text, probability=score)
                                  for left, right, text, score in words], text=text,
                           start=start, end=end, avg_logprob=-0.2, no_speech_prob=0.1)


def test_wav_reader_keeps_channels_and_checks_format_and_duration(tmp_path):
    # проверяет каналы и отказ на неподходящий формат
    samples = read_wav(write_wav(tmp_path / "stereo.wav", channels=2), 3)
    assert samples.shape == (32000, 2) and samples.dtype == np.float32
    with pytest.raises(ValueError, match="Длительность"):
        read_wav(tmp_path / "stereo.wav", 1)
    with pytest.raises(ValueError, match="16000"):
        read_wav(write_wav(tmp_path / "other.wav", rate=8000), 3)


def test_word_speaker_requires_whole_unambiguous_coverage():
    # не приписывает слово при наложении или пробеле голосовых интервалов
    assert speaker_for_word(0, 1, [(0, 0.5, "A"), (0.5, 1, "A")]) == ("A", False)
    assert speaker_for_word(0, 1, [(0, 1, "A"), (0.4, 0.6, "B")]) == (None, True)
    assert speaker_for_word(0, 1, [(0, 0.4, "A"), (0.6, 1, "A")]) == (None, False)
    assert speaker_for_word(0, 1, []) == (None, False)


def test_turns_preserve_sources_and_do_not_join_unknown_segments():
    # сохраняет время и не объединяет неизвестных участников разных сегментов
    words = [{"start": index * 0.2, "end": index * 0.2 + 0.15, "text": " слово",
              "probability": 0.9, "speaker": speaker, "channel": None, "segment": index // 2}
             for index, speaker in enumerate(("A", "A", None, None, None, "B"))]
    turns = words_to_turns(words, 0.5)
    assert [turn.speaker for turn in turns] == ["A", None, None, "B"]
    assert turns[0].text == "слово слово" and len(turns[0].source_span["words"]) == 2
    assert turns[0].source_span["start"] == 0
    assert all(turn.owner == "unknown" for turn in turns)


def test_mono_transcription_retains_uncertain_words_and_overlapping_voices(tmp_path, monkeypatch):
    # оставляет сомнительные слова и отдельно отмечает частичный результат
    backend = Mock()
    native = segment([(0, 0.3, "Привет", 0.9), (0.3, 0.8, " срочно", float("nan")),
                      (0.8, 1.2, " переведи", 0.9)])
    native.avg_logprob = native.no_speech_prob = float("nan")
    backend.transcribe.return_value = ([native], None)
    recognizer = AudioRecognizer(backend, Mock(), quality=QUALITY, model_versions=VERSIONS)
    monkeypatch.setattr(recognizer, "_intervals", Mock(return_value=[(0, 1.4, "A"), (0.5, 1, "B")]))
    result = recognizer.recognize(write_wav(tmp_path / "call.wav"))
    assert result["status"] == "partial" and "срочно" in result["extracted_text"]
    assert result["turns"][0].speaker == "A"
    assert result["turns"][1].speaker is None
    assert all(turn.owner == "unknown" for turn in result["turns"])
    assert result["turns"][1].source_span["words"][0]["probability"] is None
    json.dumps([asdict(turn) for turn in result["turns"]], allow_nan=False)
    assert backend.transcribe.call_args.kwargs == {
        "language": "ru", "word_timestamps": True, "vad_filter": True, "condition_on_previous_text": False,
    }


def test_isolated_channels_keep_identity_and_simultaneous_order_unknown(tmp_path):
    # разделяет только явно изолированные каналы и не выдумывает общий порядок
    backend = Mock()
    backend.transcribe.side_effect = [([segment([(0, 0.8, "Первая", 0.9)])], None),
                                      ([segment([(0.2, 1, "Вторая", 0.9)])], None)]
    diarizer = Mock()
    recognizer = AudioRecognizer(backend, diarizer, quality=QUALITY, model_versions=VERSIONS)
    result = recognizer.recognize(write_wav(tmp_path / "stereo.wav", channels=2), isolated_channels=True)
    assert result["status"] == "partial" and result["order_known"] is False
    assert [turn.speaker for turn in result["turns"]] == ["A", "B"]
    assert [turn.source_span["channel"] for turn in result["turns"]] == [0, 1]
    diarizer.assert_not_called()
    failed = recognizer.recognize(write_wav(tmp_path / "mono.wav"), isolated_channels=True)
    assert failed["status"] == "error" and failed["turns"] == []
    empty_backend = Mock()
    empty_backend.transcribe.return_value = ([], None)
    empty = AudioRecognizer(empty_backend, None, quality=QUALITY, model_versions=VERSIONS)
    assert empty.recognize(tmp_path / "stereo.wav", isolated_channels=True)["status"] == "empty"


def test_local_loader_disables_downloads_and_telemetry(tmp_path, monkeypatch):
    # загружает локальные пути без сетевых моделей и телеметрии
    whisper, pyannote = tmp_path / "whisper", tmp_path / "pyannote"
    whisper.mkdir()
    pyannote.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (whisper / name).write_bytes(b"test")
    for name in DIARIZATION_FILES:
        path = pyannote / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    whisper_class, pipeline_class = Mock(), Mock()
    hub_constants = SimpleNamespace(HF_HUB_OFFLINE=False)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(constants=hub_constants))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper_class))
    monkeypatch.setitem(sys.modules, "pyannote.audio", SimpleNamespace(Pipeline=pipeline_class))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(device=Mock(return_value="cpu")))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "1")
    model = AudioRecognizer.from_local(whisper, pyannote, quality=QUALITY, model_versions=VERSIONS)
    whisper_class.assert_called_once_with(str(whisper), device="cpu", compute_type="int8", local_files_only=True)
    pipeline_class.from_pretrained.assert_called_once_with(str(pyannote))
    pipeline_class.from_pretrained.return_value.to.assert_called_once_with("cpu")
    assert sys.modules["faster_whisper"].WhisperModel.return_value is model.transcriber
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["PYANNOTE_METRICS_ENABLED"] == "0"
    assert hub_constants.HF_HUB_OFFLINE is True


def test_whisper_only_loader_does_not_import_pyannote(tmp_path, monkeypatch):
    # загружает только локальный распознаватель без разделения голосов
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (tmp_path / name).write_bytes(b"test")
    whisper_class = Mock()
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper_class))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(constants=SimpleNamespace(HF_HUB_OFFLINE=False)))
    monkeypatch.setitem(sys.modules, "pyannote.audio", None)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "1")
    model = AudioRecognizer.from_local(tmp_path, quality=QUALITY, model_versions={"whisper": "test-asr"})
    whisper_class.assert_called_once_with(str(tmp_path), device="cpu", compute_type="int8", local_files_only=True)
    assert model.transcriber is whisper_class.return_value and model.diarizer is None
    (tmp_path / "tokenizer.json").unlink()
    with pytest.raises(ValueError):
        AudioRecognizer.from_local(tmp_path, quality=QUALITY, model_versions={"whisper": "test-asr"})


def test_whisper_without_diarization_keeps_unknown_speakers_and_silence(tmp_path):
    # сохраняет слова и время но не выдумывает участников
    backend = Mock()
    backend.transcribe.return_value = ([segment([(0.1, 0.4, "Пришли", 0.9), (0.4, 0.8, " код", 0.9)])], None)
    recognizer = AudioRecognizer(backend, None, quality=QUALITY, model_versions={"whisper": "test-asr"})
    path = write_wav(tmp_path / "voice.wav")
    result = recognizer.recognize(path)
    assert result["status"] == "partial" and result["extracted_text"] == "Пришли код"
    assert result["order_known"] is True
    assert all(turn.speaker is None and turn.owner == "unknown" for turn in result["turns"])
    assert result["turns"][0].source_span["start"] == 0.1
    assert result["turns"][0].source_span["end"] == 0.8
    assert any("Разделение голосов не подключено" in note for note in result["limitations"])
    with_diarization = AudioRecognizer(backend, Mock(), quality=QUALITY, model_versions={"whisper": "test-asr"})
    assert with_diarization.analyzer_version != recognizer.analyzer_version
    backend.transcribe.return_value = ([], None)
    empty = recognizer.recognize(path)
    assert empty["status"] == "empty" and empty["turns"] == []


def test_diarization_intervals_preserve_overlap_and_normalize_labels(monkeypatch):
    # сохраняет обычные интервалы голосов вместе с наложениями
    native = [(SimpleNamespace(start=3, end=4), "track", "voice-2"),
              (SimpleNamespace(start=0, end=2), "track", "voice-1"),
              (SimpleNamespace(start=1, end=1.5), "other", "voice-2")]
    annotation = SimpleNamespace(itertracks=Mock(return_value=native))
    pipeline = Mock(return_value=SimpleNamespace(speaker_diarization=annotation))
    tensor = Mock()
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(from_numpy=Mock(return_value=tensor)))
    recognizer = AudioRecognizer(Mock(), pipeline, quality=QUALITY, model_versions=VERSIONS)
    assert recognizer._intervals(np.zeros(16000, dtype=np.float32)) == [
        (0.0, 2.0, "A"), (1.0, 1.5, "B"), (3.0, 4.0, "B"),
    ]
    annotation.itertracks.assert_called_once_with(yield_label=True)
    pipeline.assert_called_once_with({"waveform": tensor.unsqueeze.return_value, "sample_rate": 16000})


def test_uncovered_word_makes_diarization_partial(tmp_path, monkeypatch):
    # отмечает частичный ответ если голос не покрывает слово целиком
    backend = Mock()
    backend.transcribe.return_value = ([segment([(0, 0.5, "Привет", 0.9)])], None)
    recognizer = AudioRecognizer(backend, Mock(), quality=QUALITY, model_versions=VERSIONS)
    monkeypatch.setattr(recognizer, "_intervals", Mock(return_value=[(0.1, 0.5, "A")]))
    result = recognizer.recognize(write_wav(tmp_path / "gap.wav"))
    assert result["status"] == "partial" and result["turns"][0].speaker is None
    assert any("не удалось определить участника" in note for note in result["limitations"])


def test_missing_transcript_for_voice_interval_is_reported(tmp_path, monkeypatch):
    # сообщает если обнаруженный голос совсем не попал в текст
    backend = Mock()
    backend.transcribe.return_value = ([segment([(0.1, 0.5, "Привет", 0.9)])], None)
    recognizer = AudioRecognizer(backend, Mock(), quality=QUALITY, model_versions=VERSIONS)
    monkeypatch.setattr(recognizer, "_intervals", Mock(return_value=[(0, 0.7, "A"), (1, 1.8, "B")]))
    result = recognizer.recognize(write_wav(tmp_path / "missing.wav"))
    assert result["status"] == "partial" and result["turns"][0].speaker == "A"
    assert any("не попала в расшифровку" in note for note in result["limitations"])
