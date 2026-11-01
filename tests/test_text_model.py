import json
from pathlib import Path

import numpy as np
import pytest

from analysis.features import Turn, feature_vector
from analysis.text import TEXT_LABELS, TextModel, prepare_fragments
from ml import train_text


class CharacterTokenizer:
    def __call__(self, text, add_special_tokens=True, return_offsets_mapping=False):
        # заменяет токены символами для проверки границ
        result = {"input_ids": list(range(len(text)))}
        if add_special_tokens:
            result["input_ids"] = [-1] + result["input_ids"] + [-2]
        if return_offsets_mapping:
            result["offset_mapping"] = [(i, i + 1) for i in range(len(text))]
        return result


class SmallEncoder:
    tokenizer = CharacterTokenizer()
    identity = {"model_id": "test/character-encoder", "revision": "1"}

    def encode(self, texts):
        # строит небольшие векторы без загрузки модели
        vectors = np.zeros((len(texts), 384))
        for row, text in enumerate(texts):
            for character in text:
                vectors[row, ord(character) % 384] += 1
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1)


@pytest.fixture
def training_rows():
    # готовит разные известные и неизвестные метки
    turns = [Turn(str(i), chr(1072 + i) * 8) for i in range(8)]
    fragments = prepare_fragments(turns, CharacterTokenizer(), order_known=False)
    labels = np.array([[(i + j) % 2 for j in range(7)] for i in range(8)], dtype=object)
    labels[0, 0] = None
    return fragments, labels


def test_feature_contract_keeps_unknown_separate_from_zero():
    # отличает отсутствие признака от неизвестного значения
    assert TEXT_LABELS == (
        "urgency", "money_request", "credentials_request", "secrecy",
        "remote_access", "authority_claim", "protective_transfer",
    )
    assert feature_vector({"no": 0, "unknown": None, "yes": 0.4},
                          ["no", "unknown", "yes", "missing"]) == [
        0, 0, 0.4, 0, 1, 0, 1, 0,
    ]
    with pytest.raises(ValueError):
        feature_vector({"bad": float("nan")}, ["bad"])


def test_fragments_keep_tail_offsets_and_only_past_context():
    # проверяет длинный текст и отсутствие будущих реплик
    tokenizer = CharacterTokenizer()
    past = [Turn(str(i), f"PAST{i}") for i in range(6)]
    text = "я" * 500 + "TAIL"
    turn = Turn("target", text, "A", source_span={"start": 1, "end": 2})
    turns = past + [turn]
    fragments = prepare_fragments(turns, tokenizer, background="NOTE")
    extended = prepare_fragments(turns + [Turn("future", "FUTURE")], tokenizer,
                                 background="NOTE")
    assert fragments == [item for item in extended if item["turn_id"] != "future"]
    parts = [item for item in fragments if item["turn_id"] == "target"]
    assert parts[0]["start"] == 0 and parts[-1]["end"] == len(text)
    assert "TAIL" in parts[-1]["target"] and "TAIL" not in parts[0]["context"]
    for index, part in enumerate(parts):
        assert part["text"] == text[part["start"]:part["end"]]
        assert part["source_span"] == turn.source_span
        assert len(tokenizer(part["target"])["input_ids"]) <= 512
        assert len(tokenizer(part["context"])["input_ids"]) <= 512
        assert part["target"].startswith("query: ")
        assert part["context"].startswith("query: ")
        assert "BACKGROUND: NOTE" in part["context"]
        assert "NOTE" not in part["target"]
        assert "PAST0" not in part["context"] and "PAST1" not in part["context"]
        assert all(f"PAST{i}" in part["context"] for i in range(2, 6))
        if index:
            assert parts[index - 1]["end"] - part["start"] == 64
    unordered = prepare_fragments(turns, tokenizer, order_known=False)
    assert all("PAST" not in item["context"] for item in unordered if item["turn_id"] == "target")
    crowded = prepare_fragments([Turn("old", "OLD" * 300), turn], tokenizer)
    for item in crowded:
        if item["turn_id"] == "target":
            assert item["text"] in item["context"] and "OLD" not in item["context"]


def test_training_excludes_unknown_only_from_its_own_head(training_rows):
    # проверяет обучение только по известным меткам
    fragments, labels = training_rows
    model = TextModel(SmallEncoder()).fit(fragments, labels)
    changed = [dict(item) for item in fragments]
    changed[0].update(target="query: другой текст", context="query: другой текст")
    other = TextModel(SmallEncoder()).fit(changed, labels)
    np.testing.assert_array_equal(model.heads[0].coef_, other.heads[0].coef_)
    np.testing.assert_array_equal(model.heads[0].intercept_, other.heads[0].intercept_)
    assert not np.allclose(model.heads[1].coef_, other.heads[1].coef_)
    scores = model.scores(fragments)
    assert scores.shape == (8, 7) and np.all((scores >= 0) & (scores <= 1))
    labels[:, 6] = 0
    with pytest.raises(ValueError):
        TextModel(SmallEncoder()).fit(fragments, labels)


def test_prediction_preserves_maximum_source_and_unknown_history(monkeypatch):
    # проверяет историю говорящего и источник максимума
    model = TextModel(SmallEncoder())

    def scores(fragments):
        # задает оценки фрагментов для проверки объединения
        values = {"past": 0.2, "other": 0.95, "now": 0.3, "future": 1.0}
        return np.array([[values[item["turn_id"]] +
                          (0.6 if item["turn_id"] == "past" and item["fragment_id"] else 0)] * 7
                         for item in fragments])

    monkeypatch.setattr(model, "scores", scores)
    turns = [Turn("past", "а" * 300, "A"), Turn("other", "б", "B"),
             Turn("now", "в", "A"), Turn("future", "г", "A")]
    result = model.predict(turns)["turns"]
    assert result[0]["values"]["text_urgency"] == pytest.approx(0.8)
    assert result[0]["source_refs"]["text_urgency"]["fragment_id"] == 1
    assert result[2]["values"]["previous_urgency"] == pytest.approx(0.8)
    assert result[2]["source_refs"]["previous_urgency"]["turn_id"] == "past"
    assert result[2]["source_refs"]["previous_urgency"]["fragment_id"] == 1
    assert result[1]["values"]["previous_urgency"] is None
    assert result[:3] == model.predict(turns[:3])["turns"]
    unknown = [Turn(turn.turn_id, turn.text) for turn in turns]
    for output in (model.predict(unknown), model.predict(turns, order_known=False)):
        assert all(turn["values"][f"previous_{label}"] is None
                   for turn in output["turns"] for label in TEXT_LABELS)


def test_artifacts_roundtrip_and_reject_modified_weights(tmp_path, training_rows):
    # проверяет сохранение модели и контроль целостности
    fragments, labels = training_rows
    model = TextModel(SmallEncoder()).fit(fragments, labels)
    path = tmp_path / "text-model"
    model.save(path, training_info={"purpose": "unit test"})
    loaded = TextModel.load(path, SmallEncoder())
    np.testing.assert_array_equal(model.scores(fragments), loaded.scores(fragments))
    turns = [Turn("one", "срочно"), Turn("two", "обычно")]
    assert loaded.predict(turns) == model.predict(turns)
    assert loaded.predict([])["status"] == "empty"
    different = SmallEncoder()
    different.identity = {"model_id": "test/other-encoder", "revision": "1"}
    with pytest.raises(ValueError):
        TextModel.load(path, different)
    weights = path / "heads.joblib"
    weights.write_bytes(weights.read_bytes() + b"changed")
    with pytest.raises(ValueError):
        TextModel.load(path, SmallEncoder())


def test_training_rejects_group_leakage_before_loading_encoder(tmp_path, monkeypatch, capsys):
    # запрещает обучение с пересечением групп до загрузки модели
    source = Path(__file__).parents[1] / "examples/text_train.jsonl"
    row = json.loads(source.read_text(encoding="utf-8").splitlines()[0])
    row["split"] = "validation"
    row["case_id"] = "separate_case_with_same_group"
    validation = tmp_path / "validation.jsonl"
    validation.write_text(json.dumps(row), encoding="utf-8")

    def unexpected_encoder(**kwargs):
        # запрещает загрузку весов при неверном разбиении
        raise AssertionError("Энкодер не должен загружаться")

    monkeypatch.setattr(train_text, "E5Encoder", unexpected_encoder)
    monkeypatch.setattr("sys.argv", ["train", str(source), str(tmp_path / "model"),
                                     "--validation", str(validation)])
    with pytest.raises(SystemExit) as error:
        train_text.main()
    assert error.value.code == 2
    assert "пересекаются" in capsys.readouterr().err
    assert not (tmp_path / "model").exists()


def test_training_checks_quotes_and_counts_only_known_validation_labels():
    # проверяет цитаты и отсутствие выдуманных метрик для неизвестных меток
    source = Path(__file__).parents[1] / "examples/text_train.jsonl"
    rows, info = train_text.read_rows(source, "train")
    fragments, labels = train_text.prepare_rows(rows, CharacterTokenizer())
    assert len(fragments) == info["rows"] == 14
    assert np.isnan(labels[12, 0])
    rows[1]["evidence"][0] = "Цитата из другой реплики"
    with pytest.raises(ValueError, match="цитата"):
        train_text.prepare_rows(rows, CharacterTokenizer())
    labels[:, 0] = np.nan
    metrics = train_text.validation_metrics(labels, np.zeros(labels.shape))
    assert metrics["urgency"]["known_count"] == 0
    assert metrics["urgency"]["f1"] is None
    assert metrics["money_request"]["known_count"] == 14
