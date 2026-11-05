import hashlib
import json
import os
import wave
from math import isfinite
from pathlib import Path

import numpy as np

from analysis.features import Turn
from analysis.diarization import load_diarization
from analysis.whisper import load_whisper


SAMPLE_RATE = 16000


def read_wav(path, max_seconds):
    # читает короткую запись без изменения исходного файла
    with wave.open(str(path), "rb") as audio:
        channels, rate, width, frames = audio.getnchannels(), audio.getframerate(), audio.getsampwidth(), audio.getnframes()
        if channels not in (1, 2) or width != 2 or rate != SAMPLE_RATE or audio.getcomptype() != "NONE":
            raise ValueError("Нужен WAV PCM 16 бит 16000 Гц с одним или двумя каналами")
        if not 0 < frames / rate <= max_seconds:
            raise ValueError("Длительность записи вне допустимого диапазона")
        raw = audio.readframes(frames)
    if len(raw) != frames * channels * width:
        raise ValueError("Запись обрезана")
    return np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, channels) / 32768


def speaker_for_word(start, end, intervals):
    # определяет голос только при однозначном покрытии всего слова
    matches = [(max(start, left), min(end, right), speaker) for left, right, speaker in intervals
               if left < end and right > start]
    voices = {speaker for left, right, speaker in matches}
    if len(voices) != 1:
        return None, len(voices) > 1
    covered = start
    for left, right, speaker in sorted(matches):
        if left > covered + 1e-6:
            return None, False
        covered = max(covered, right)
    return (next(iter(voices)) if covered >= end - 1e-6 else None), False


def words_to_turns(words, max_gap):
    # объединяет соседние слова одного голоса с сохранением времени
    groups = []
    for word in sorted(words, key=lambda item: (item["start"], item["channel"] or 0, item["end"])):
        previous = groups[-1][-1] if groups else None
        same = previous is not None and previous["speaker"] == word["speaker"] and previous["channel"] == word["channel"]
        if same and word["speaker"] is None:
            same = previous["segment"] == word["segment"]
        if same and 0 <= word["start"] - previous["end"] <= max_gap:
            groups[-1].append(word)
        else:
            groups.append([word])
    turns = []
    for index, group in enumerate(groups):
        text = "".join(word["text"] for word in group).strip()
        if not text:
            continue
        turns.append(Turn(str(index), text, group[0]["speaker"], owner="unknown", source_span={
            "kind": "audio", "channel": group[0]["channel"],
            "start": group[0]["start"], "end": max(word["end"] for word in group),
            "words": [{key: word[key] for key in ("start", "end", "text", "probability")} for word in group],
        }))
    return turns


class AudioRecognizer:
    def __init__(self, transcriber, diarizer, *, quality, model_versions):
        # сохраняет готовые модели и настройки распознавания
        self.transcriber, self.diarizer = transcriber, diarizer
        self.quality = dict(quality)
        if not 0 <= quality["min_word_probability"] <= 1 or not 0 <= quality["max_no_speech_probability"] <= 1:
            raise ValueError("Пороги вероятностей должны быть от нуля до единицы")
        if quality["max_seconds"] <= 0 or quality["max_turn_gap"] < 0:
            raise ValueError("Нужны положительная длительность и неотрицательный интервал")
        identity = json.dumps({"models": model_versions, "quality": quality, "policy": "audio-v2",
                               "diarization_enabled": diarizer is not None,
                               "format": "wav-pcm16-16khz"}, sort_keys=True, allow_nan=False)
        self.analyzer_version = "audio-" + hashlib.sha256(identity.encode()).hexdigest()[:16]

    @classmethod
    def from_local(cls, whisper_dir, diarization_dir=None, *, quality, model_versions):
        # загружает только заранее сохраненные модели без телеметрии
        if diarization_dir is not None and not (Path(diarization_dir) / "config.yaml").is_file():
            raise ValueError("Нужен локальный каталог pyannote")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
        from huggingface_hub import constants

        constants.HF_HUB_OFFLINE = True
        transcriber = load_whisper(whisper_dir)
        diarizer = load_diarization(diarization_dir) if diarization_dir is not None else None
        return cls(transcriber, diarizer, quality=quality, model_versions=model_versions)

    def _intervals(self, waveform):
        # получает обычные интервалы голосов с сохранением наложений
        import torch

        output = self.diarizer({"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": SAMPLE_RATE})
        intervals = [(float(segment.start), float(segment.end), speaker)
                     for segment, _, speaker in output.speaker_diarization.itertracks(yield_label=True)]
        intervals.sort(key=lambda item: item[0])
        labels = {speaker: chr(65 + index) for index, speaker in enumerate(dict.fromkeys(item[2] for item in intervals))}
        return [(left, right, labels[speaker]) for left, right, speaker in intervals]

    def _words(self, waveform, intervals, channel, fixed_speaker):
        # распознает слова и отмечает сомнительные участки записи
        segments, _ = self.transcriber.transcribe(waveform, language="ru", word_timestamps=True,
                                                 vad_filter=True, condition_on_previous_text=False)
        words, partial, limitations = [], False, []
        duration = len(waveform) / SAMPLE_RATE
        for index, segment in enumerate(segments):
            if (not all(isfinite(value) for value in (segment.avg_logprob, segment.no_speech_prob))
                    or segment.avg_logprob < self.quality["min_avg_logprob"]
                    or segment.no_speech_prob > self.quality["max_no_speech_probability"]):
                partial = True
                limitations.append("Есть участки с низкой надежностью распознавания речи")
            if not segment.words:
                if segment.text.strip():
                    partial = True
                    limitations.append("Для части текста нет времени отдельных слов")
                    if 0 <= segment.start < segment.end <= duration:
                        words.append({"start": float(segment.start), "end": float(segment.end), "text": segment.text,
                                      "probability": None, "speaker": None, "channel": channel, "segment": index})
                continue
            for word in segment.words:
                if not word.word.strip():
                    continue
                if not 0 <= word.start < word.end <= duration:
                    partial = True
                    limitations.append("Часть слов пропущена из за некорректных временных отметок")
                    continue
                speaker, overlap = (fixed_speaker, False) if fixed_speaker else speaker_for_word(word.start, word.end, intervals)
                if overlap:
                    partial = True
                    limitations.append("Часть слов нельзя однозначно отнести к одному голосу")
                probability = float(word.probability)
                if not isfinite(probability):
                    probability = None
                if probability is None or probability < self.quality["min_word_probability"]:
                    partial = True
                    limitations.append("Есть участки с низкой надежностью распознавания речи")
                words.append({"start": float(word.start), "end": float(word.end), "text": word.word,
                              "probability": probability, "speaker": speaker,
                              "channel": channel, "segment": index})
        return words, partial, limitations

    def recognize(self, path, *, isolated_channels=False):
        # преобразует запись в реплики и отдельно сообщает об ошибках
        limitations, words, partial = [], [], False
        try:
            samples = read_wav(path, self.quality["max_seconds"])
            if isolated_channels and samples.shape[1] != 2:
                raise ValueError("Для изолированных каналов нужна стереозапись")
            if isolated_channels:
                for channel in range(2):
                    found, uncertain, notes = self._words(samples[:, channel], [], channel, chr(65 + channel))
                    words.extend(found)
                    partial = partial or uncertain
                    limitations.extend(notes)
            else:
                waveform = samples.mean(axis=1)
                intervals = self._intervals(waveform) if self.diarizer is not None else []
                words, partial, limitations = self._words(waveform, intervals, None, None)
                if any(not any(word["start"] < right and word["end"] > left for word in words)
                       for left, right, speaker in intervals):
                    partial = True
                    limitations.append("Часть обнаруженной речи не попала в расшифровку")
            turns = words_to_turns(words, self.quality["max_turn_gap"])
        except Exception:
            return {"status": "error", "turns": [], "order_known": False,
                    "analyzer_version": self.analyzer_version, "extracted_text": "",
                    "limitations": ["Не удалось обработать аудиозапись"]}
        order_known = bool(turns) and all(left.source_span["end"] <= right.source_span["start"]
                                          for left, right in zip(turns, turns[1:]))
        if turns and not order_known:
            partial = True
            limitations.append("Одновременные реплики не имеют однозначного общего порядка")
        if turns and not isolated_channels and self.diarizer is None:
            partial = True
            limitations.append("Разделение голосов не подключено участники записи неизвестны")
        elif any(turn.speaker is None for turn in turns):
            partial = True
            limitations.append("Для части речи не удалось определить участника")
        if turns:
            limitations.append("Метки голосов не устанавливают владельца записи")
        return {"status": "empty" if not turns else "partial" if partial else "ok", "turns": turns,
                "order_known": order_known, "analyzer_version": self.analyzer_version,
                "extracted_text": "\n".join(turn.text for turn in turns),
                "limitations": list(dict.fromkeys(limitations))}
