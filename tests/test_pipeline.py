import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from analysis.features import Turn
from analysis.pipeline import Analyzer, find_urls
from analysis.risk import RISK_FEATURES


class TextStub:
    def __init__(self):
        # запоминает обращения к текстовой модели
        self.calls = []

    def predict(self, turns, background="", order_known=True):
        # возвращает готовые оценки с настоящими источниками
        self.calls.append((turns, background, order_known))
        return {"analyzer_version": "text-test", "order_known": order_known, "turns": [
            {"turn_id": turn.turn_id, "speaker": turn.speaker, "owner": turn.owner,
             "values": {"text_urgency": 0.2}, "source_refs": {"text_urgency": {
                 "turn_id": turn.turn_id, "text": turn.text, "source_span": turn.source_span,
             }}} for turn in turns
        ]}


class URLStub:
    def __init__(self):
        # запоминает списки адресов для каждого обращения
        self.calls = []

    def predict(self, urls, expected_site=""):
        # задает разные оценки для проверки связи блоков
        self.calls.append((list(urls), expected_site))
        bad = [url for url in urls if "bad" in url]
        return {"analyzer_version": "url-test", "values": {"url_score": 0.9 if bad else 0.1},
                "source_refs": {"url_score": {"url": (bad or urls)[0]}}}


@pytest.fixture
def analyzer():
    # собирает модели без скачивания весов
    return Analyzer(TextStub(), URLStub(), {"text_urgency": 0.8, "url_score": 0.8})


def test_find_urls_keeps_full_addresses_and_balanced_brackets():
    # проверяет ссылки со скобками и ошибочными портами
    urls, invalid = find_urls('https://example.com/a(b) (https://wrapped.example.com/x). «https://пример.рф» '
                              'https://example.com/reset?token=abc! https://example.com/a; '
                              'https://example.com/a(b) http://bad.com:99999 и bare.example.com')
    assert urls == ["https://example.com/a(b)", "https://пример.рф",
                    "https://example.com/reset?token=abc!", "https://example.com/a;"]
    assert invalid


def test_url_without_message_uses_rules_and_does_not_analyze_background(analyzer):
    # проверяет прямую ссылку без выдуманной реплики
    data = {"url": "https://bad.example.com", "message": "", "expected_site": "Банк",
            "context": "Заметка https://other.example.com"}
    now = datetime.now(timezone.utc)
    result, snapshot = analyzer.analyze("url", data, checked_at=now)
    assert analyzer.text_model.calls == []
    assert analyzer.url_model.calls == [([data["url"]], "Банк")]
    assert result["risk"] is None and result["warning"]
    assert result["combined_signals"][0]["rules"] == ["F"]
    assert result["findings"][0]["source_ref"]["url"] == data["url"]
    assert snapshot == {}
    prepared = analyzer.prepare("url", data, checked_at=now)
    assert len(prepared["rows"]) == 1 and prepared["target_turn_ids"] == [None]
    assert not hasattr(analyzer, "risk_model")


def test_dialogue_links_use_only_current_and_past_of_same_known_speaker(analyzer):
    # запрещает перенос ссылок из будущего и чужих реплик
    turns = [Turn("a", "https://safe.example.com", "A"),
             Turn("b", "https://bad.example.com", "B"), Turn("c", "Ответ", "A"),
             Turn("d", "https://future.example.com", "A")]
    prepared = analyzer.prepare("text", {"context": "BACKGROUND https://background.example.com"},
                                turns=turns, checked_at=datetime.now(timezone.utc))
    assert [call[0] for call in analyzer.url_model.calls] == [
        ["https://safe.example.com"], ["https://bad.example.com"],
        ["https://safe.example.com"], ["https://safe.example.com", "https://future.example.com"],
    ]
    rows = [dict(zip(RISK_FEATURES, row["vector"])) for row in prepared["rows"]]
    assert rows[0]["url_score"] == rows[2]["url_score"] == 0.1
    assert rows[1]["url_score"] == 0.9
    assert analyzer.text_model.calls[0][1].startswith("BACKGROUND")


def test_transfer_uses_original_request_and_one_time_without_full_csv_snapshot(analyzer):
    # проверяет анкету и точные расчеты по переданной истории
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    history = [{"occurred_at": now - timedelta(days=i), "amount_kopecks": 100 * i,
                "recipient": "shop", "direction": "outgoing"} for i in range(1, 21)]
    data = {"amount_kopecks": 1000, "purpose": "purchase", "recipient": "shop",
            "message": "Просьба https://safe.example.com", "urgency": "yes", "context": "Не исходная просьба"}
    original = deepcopy((data, history))
    result, snapshot = analyzer.analyze("transfer", data, history, checked_at=now)
    turns, background, _ = analyzer.text_model.calls[0]
    assert len(turns) == 1 and turns[0].text == data["message"] and background == ""
    assert turns[0].source_span == {"field": "message"}
    prepared = analyzer.prepare("transfer", data, history, checked_at=now)
    assert len(prepared["rows"]) == 1 and prepared["target_turn_ids"] == ["input"]
    assert prepared["snapshot"] == snapshot
    assert snapshot["checked_at"] == now.isoformat()
    assert snapshot["transfer"]["last_outgoing_days"] == 1
    assert snapshot["transfer"]["amount_percentile"] == 0.5
    assert set(snapshot) == {"checked_at", "transfer"}
    assert "shop" not in json.dumps(snapshot)
    assert (data, history) == original
    assert any(item["feature"] == "text_urgency" and item["origin"] == "questionnaire"
               for item in result["findings"])
    assert result["risk"] is None and not result["warning"]


def test_unknown_order_invalid_links_and_prepared_media_remain_limited(analyzer):
    # проверяет неполное распознавание и неверные ссылки
    turns = [Turn("a", "https://safe.example.com", "A"),
             Turn("b", "https://bad.example.com https://broken.com:99999", "A")]
    result, _ = analyzer.analyze("audio", {}, turns=turns, order_known=False,
                                 extraction_status="partial", checked_at=datetime.now(timezone.utc))
    assert [call[0] for call in analyzer.url_model.calls] == [["https://safe.example.com"], ["https://bad.example.com"]]
    assert result["risk"] is None and result["status"] == "partial"
    assert result["warning"] and result["sufficiency"] == "limited"
    assert any("Часть ссылок" in item for item in result["limitations"])
    result, _ = analyzer.analyze("text", {"text": "Ссылка https://broken.com:99999"},
                                 checked_at=datetime.now(timezone.utc))
    assert result["risk"] is None and result["sufficiency"] == "limited"
    assert not result["warning"]
    with pytest.raises(ValueError, match="распознать"):
        analyzer.analyze("audio", {}, checked_at=datetime.now(timezone.utc))


def test_media_and_training_use_same_prepared_features_without_tree(analyzer):
    # связывает распознанные реплики с обучением и обычной проверкой
    extraction = {"turns": [Turn("voice-1", "Распознанная просьба", "A", source_span={"start": 1, "end": 2})],
                  "status": "partial", "order_known": False, "analyzer_version": "audio-test",
                  "extracted_text": "Распознанная просьба", "limitations": ["Тестовое ограничение"]}
    now = datetime.now(timezone.utc)
    prepared = analyzer.prepare("audio", {}, checked_at=now, extraction=extraction)
    assert not hasattr(analyzer, "risk_model")
    assert prepared["target_turn_ids"] == ["voice-1"]
    assert prepared["versions"]["extraction"] == "audio-test"
    result, _ = analyzer.analyze("audio", {}, checked_at=now, extraction=extraction)
    values = dict(zip(RISK_FEATURES, prepared["rows"][0]["vector"]))
    assert values["text_urgency"] == result["combined_signals"][0]["values"]["text_urgency"]["model_score"]
    assert result["risk"] is None and result["extracted_text"] == extraction["extracted_text"]
    assert result["policy"] == "warning-rules-v1" and not result["warning"]
    assert "Тестовое ограничение" in result["limitations"]
