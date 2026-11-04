import hashlib
import json
from collections import deque
from importlib.metadata import version
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from analysis.features import Turn


LAYOUT_VERSION = "bubble-layout-v1"


def box_rect(box):
    # находит края рамки распознанного текста
    points = np.asarray(box, dtype=float)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("Неверная рамка OCR")
    return [float(points[:, 0].min()), float(points[:, 1].min()),
            float(points[:, 0].max()), float(points[:, 1].max())]


def detect_bubbles(image):
    # ищет отдельные заполненные области сообщения
    width, height = image.size
    scale = min(1, 256 / width)
    pixels = np.asarray(image.resize((max(1, round(width * scale)), max(1, round(height * scale)))))
    colors = pixels.astype(int) // 16
    colors = colors[:, :, 0] * 256 + colors[:, :, 1] * 16 + colors[:, :, 2]
    background = int(np.bincount(colors.ravel()).argmax())
    visited = np.zeros(colors.shape, dtype=bool)
    rows, cols = colors.shape
    bubbles = []
    for y in range(rows):
        for x in range(cols):
            if visited[y, x] or colors[y, x] == background:
                continue
            color, queue = colors[y, x], deque([(x, y)])
            visited[y, x] = True
            count, left, right, top, bottom = 0, x, x, y, y
            while queue:
                px, py = queue.popleft()
                count += 1
                left, right = min(left, px), max(right, px)
                top, bottom = min(top, py), max(bottom, py)
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if 0 <= nx < cols and 0 <= ny < rows and not visited[ny, nx] and colors[ny, nx] == color:
                        visited[ny, nx] = True
                        queue.append((nx, ny))
            area = (right - left + 1) * (bottom - top + 1)
            if count >= 40 and right - left >= cols * 0.12 and right - left < cols * 0.92 and count / area >= 0.65:
                bubbles.append([left * width / cols, top * height / rows,
                                (right + 1) * width / cols, (bottom + 1) * height / rows])
    return bubbles


def group_text(rows, bubbles, width):
    # собирает строки внутри пузырей и определяет стороны
    groups = {}
    for index, row in enumerate(rows):
        left, top, right, bottom = row["rect"]
        candidates = [(position, box) for position, box in enumerate(bubbles)
                      if box[0] - 2 <= left and box[1] - 2 <= top
                      and box[2] + 2 >= right and box[3] + 2 >= bottom]
        if candidates:
            position, bubble = min(candidates, key=lambda item: (item[1][2] - item[1][0]) * (item[1][3] - item[1][1]))
            key = ("bubble", position)
        else:
            key, bubble = ("unknown", index), None
        if key not in groups:
            groups[key] = {"rows": [], "bubble": bubble, "speaker": None}
        groups[key]["rows"].append(row)
    groups = list(groups.values())
    sides = set()
    for group in groups:
        bubble = group["bubble"]
        if bubble is not None:
            gap = (width - bubble[2]) - bubble[0]
            if abs(gap) >= width * 0.1:
                group["speaker"] = "A" if gap > 0 else "B"
                sides.add(group["speaker"])
    if sides != {"A", "B"}:
        for group in groups:
            group["speaker"] = None
    return groups


def ordered_lines(rows):
    # ставит строки сообщения сверху вниз и слева направо
    lines = []
    for row in sorted(rows, key=lambda item: (item["rect"][1], item["rect"][0])):
        middle = (row["rect"][1] + row["rect"][3]) / 2
        if lines and abs(middle - lines[-1][0]) <= (row["rect"][3] - row["rect"][1]) / 2:
            lines[-1][1].append(row)
        else:
            lines.append((middle, [row]))
    return [sorted(line, key=lambda item: item["rect"][0]) for middle, line in lines]


class ScreenshotModel:
    def __init__(self, reader, *, min_confidence, max_pixels, analyzer_version="screenshot-unsaved"):
        # сохраняет OCR и выбранные ограничения обработки
        if not 0 <= min_confidence <= 1 or max_pixels <= 0:
            raise ValueError("Нужны допустимые порог OCR и лимит изображения")
        self.reader = reader
        self.min_confidence = min_confidence
        self.max_pixels = max_pixels
        self.analyzer_version = analyzer_version

    @classmethod
    def from_local(cls, directory, *, min_confidence, max_pixels):
        # загружает только подготовленные локальные веса OCR
        directory = Path(directory)
        weights = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.glob("*.pth"))}
        if not weights:
            raise ValueError("Нужны локальные веса OCR")
        import easyocr

        reader = easyocr.Reader(["ru", "en"], gpu=False, model_storage_directory=str(directory),
                                download_enabled=False, detect_network="craft", recog_network="cyrillic_g2", verbose=False)
        identity = json.dumps({"weights": weights, "layout": LAYOUT_VERSION,
                               "easyocr": version("easyocr"), "min_confidence": min_confidence,
                               "max_pixels": max_pixels}, sort_keys=True)
        return cls(reader, min_confidence=min_confidence, max_pixels=max_pixels,
                   analyzer_version="screenshot-" + hashlib.sha256(identity.encode()).hexdigest()[:16])

    def extract(self, source):
        # распознает изображение и сохраняет рамки исходных реплик
        limitations, turns = [], []
        result = {"status": "error", "turns": turns, "order_known": False,
                  "analyzer_version": self.analyzer_version, "limitations": limitations, "extracted_text": ""}
        try:
            image = source.copy() if isinstance(source, Image.Image) else Image.open(source)
            if image.width * image.height > self.max_pixels:
                raise ValueError("Изображение превышает лимит")
            image = ImageOps.exif_transpose(image).convert("RGB")
            raw = self.reader.readtext(np.asarray(image), detail=1, paragraph=False)
            rows = []
            for box, text, confidence in raw:
                text = text.strip()
                if text:
                    confidence = float(confidence)
                    if not np.isfinite(confidence) or not 0 <= confidence <= 1:
                        raise ValueError("Неверная оценка OCR")
                    rows.append({"text": text, "confidence": confidence, "rect": box_rect(box),
                                 "box": [[float(x), float(y)] for x, y in box]})
            if not rows:
                result["status"] = "empty"
                limitations.append("На изображении не удалось прочитать текст")
                return result
            groups = group_text(rows, detect_bubbles(image), image.width)
            groups.sort(key=lambda group: min(row["rect"][1] for row in group["rows"]))
            order_known, previous_bottom = True, None
            partial = any(row["confidence"] < self.min_confidence for row in rows)
            if partial:
                limitations.append("Часть текста распознана с низкой оценкой OCR")
            for index, group in enumerate(groups):
                lines = ordered_lines(group["rows"])
                ordered = [row for line in lines for row in line]
                top = min(row["rect"][1] for row in ordered)
                bottom = max(row["rect"][3] for row in ordered)
                if previous_bottom is not None and top < previous_bottom:
                    order_known = False
                previous_bottom = max(previous_bottom or bottom, bottom)
                text = "\n".join(" ".join(row["text"] for row in line) for line in lines)
                span = {"boxes": [row["box"] for row in ordered], "bubble_box": group["bubble"],
                        "ocr_scores": [row["confidence"] for row in ordered],
                        "image_size": list(image.size), "coordinate_frame": "exif_transposed"}
                turns.append(Turn(str(index), text, speaker=group["speaker"], owner="unknown", source_span=span))
            if any(turn.speaker is None for turn in turns):
                partial = True
                limitations.append("Оформление не позволяет надежно разделить участников")
            if not order_known:
                partial = True
                limitations.append("Порядок сообщений неоднозначен")
            limitations.append("Расположение пузырей не устанавливает владельца переписки")
            result.update(status="partial" if partial else "ok", order_known=order_known,
                          extracted_text="\n".join(turn.text for turn in turns))
        except Exception:
            result.update(status="error", turns=[], order_known=False, extracted_text="")
            limitations.append("Не удалось обработать изображение")
        return result
