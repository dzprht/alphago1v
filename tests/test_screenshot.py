import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

from analysis import screenshot
from analysis.screenshot import ScreenshotModel


def text_box(left, top, right, bottom, text, score=0.95):
    # готовит одну строку ответа OCR
    return ([[left, top], [right, top], [right, bottom], [left, bottom]], text, score)


def chat_image():
    # рисует два различимых пузыря для проверки геометрии
    image = Image.new("RGB", (300, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((10, 10, 190, 90), radius=8, fill=(220, 235, 250))
    draw.rounded_rectangle((100, 120, 290, 190), radius=8, fill=(210, 250, 210))
    return image


def test_bubble_grouping_preserves_wrapped_message_and_sides():
    # проверяет автора целого пузыря вместо каждой строки
    reader = Mock()
    reader.readtext.return_value = [text_box(120, 135, 260, 150, "Ответ"),
                                   text_box(70, 45, 175, 60, "вторая строка"),
                                   text_box(20, 25, 150, 40, "Первая строка")]
    model = ScreenshotModel(reader, min_confidence=0.7, max_pixels=100000)
    result = model.extract(chat_image())
    assert result["status"] == "ok" and result["order_known"] is True
    assert [turn.text for turn in result["turns"]] == ["Первая строка\nвторая строка", "Ответ"]
    assert [turn.speaker for turn in result["turns"]] == ["A", "B"]
    assert all(turn.owner == "unknown" for turn in result["turns"])
    assert len(result["turns"][0].source_span["boxes"]) == 2
    assert result["turns"][0].source_span["bubble_box"] is not None
    assert reader.readtext.call_args.kwargs == {"detail": 1, "paragraph": False}
    json.dumps(result["turns"][0].source_span, allow_nan=False)


def test_unknown_format_keeps_text_without_inventing_speaker():
    # оставляет участников неизвестными без заметных пузырей
    reader = Mock()
    reader.readtext.return_value = [text_box(20, 20, 180, 40, "Обычный текст")]
    model = ScreenshotModel(reader, min_confidence=0.7, max_pixels=100000)
    result = model.extract(Image.new("RGB", (300, 200), "white"))
    assert result["status"] == "partial" and result["order_known"] is True
    assert result["turns"][0].speaker is None and result["turns"][0].owner == "unknown"
    assert result["turns"][0].text == "Обычный текст"


def test_low_confidence_and_overlapping_messages_are_partial():
    # отмечает плохое распознавание и неопределенный порядок
    reader = Mock()
    reader.readtext.return_value = [text_box(15, 25, 80, 60, "Первое", 0.2),
                                   text_box(160, 35, 240, 65, "Второе")]
    model = ScreenshotModel(reader, min_confidence=0.7, max_pixels=100000)
    result = model.extract(Image.new("RGB", (300, 200), "white"))
    assert result["status"] == "partial" and result["order_known"] is False
    assert len(result["turns"]) == 2
    assert any("низкой" in item for item in result["limitations"])
    assert any("неоднозначен" in item for item in result["limitations"])


def test_empty_oversized_and_failed_ocr_have_no_fabricated_turns():
    # различает пустой текст и техническую ошибку
    reader = Mock()
    reader.readtext.return_value = []
    model = ScreenshotModel(reader, min_confidence=0.7, max_pixels=100000)
    assert model.extract(chat_image())["status"] == "empty"
    reader.readtext.side_effect = RuntimeError("internal details")
    result = model.extract(chat_image())
    assert result["status"] == "error" and result["turns"] == []
    reader.readtext.side_effect = None
    reader.readtext.return_value = [text_box(10, 20, 50, 40, "Текст", float("nan"))]
    assert model.extract(chat_image())["status"] == "error"
    reader.readtext.reset_mock()
    model = ScreenshotModel(reader, min_confidence=0.7, max_pixels=100)
    result = model.extract(chat_image())
    assert result["status"] == "error"
    reader.readtext.assert_not_called()


def test_local_loader_uses_pinned_networks_without_downloads(tmp_path, monkeypatch):
    # проверяет локальные веса и запрет сетевой загрузки
    (tmp_path / "craft_mlt_25k.pth").write_bytes(b"test")
    (tmp_path / "cyrillic_g2.pth").write_bytes(b"test")
    reader = Mock()
    factory = Mock(return_value=reader)
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=factory))
    monkeypatch.setattr(screenshot, "version", Mock(return_value="test"))
    model = ScreenshotModel.from_local(tmp_path, min_confidence=0.7, max_pixels=100000)
    assert model.reader is reader and model.analyzer_version.startswith("screenshot-")
    factory.assert_called_once_with(["ru", "en"], gpu=False, model_storage_directory=str(tmp_path),
                                    download_enabled=False, detect_network="craft", recog_network="cyrillic_g2", verbose=False)
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="локальн"):
        ScreenshotModel.from_local(missing, min_confidence=0.7, max_pixels=100000)
