import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import precision_recall_fscore_support

from analysis.features import Turn
from analysis.text import E5Encoder, TEXT_LABELS, TextModel, prepare_fragments, validate_labels


def read_rows(path, split):
    # проверяет строки и сохраняет сведения об исходном файле
    raw = Path(path).read_bytes()
    rows, seen, cases = [], set(), {}
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        error = f"Строка {line_number}: "
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(error + "неверный JSON") from None
        if not isinstance(row, dict) or row.get("split") != split:
            raise ValueError(error + f"ожидается объект с split={split}")
        if any(not isinstance(row.get(key), str) or not row[key].strip()
               for key in ("case_id", "group_id", "target_turn_id")):
            raise ValueError(error + "нужны непустые идентификаторы случая группы и реплики")
        labels, evidence = row.get("labels"), row.get("evidence")
        if not isinstance(labels, list) or len(labels) != len(TEXT_LABELS) or any(
            value is not None and (type(value) is not int or value not in (0, 1)) for value in labels
        ):
            raise ValueError(error + "labels должен содержать семь значений 0 1 или null")
        if not isinstance(evidence, list) or len(evidence) != len(TEXT_LABELS) or any(
            value is not None and not isinstance(value, str) for value in evidence
        ):
            raise ValueError(error + "evidence должен содержать семь строк или null")
        if any(label == 1 and (not quote or not quote.strip()) for label, quote in zip(labels, evidence)):
            raise ValueError(error + "для положительной метки нужна цитата")
        turns = row.get("turns")
        if not isinstance(turns, list) or not turns or any(
            not isinstance(turn, dict) or not isinstance(turn.get("turn_id"), str)
            or not turn["turn_id"].strip() or not isinstance(turn.get("text"), str)
            or not turn["text"].strip() for turn in turns
        ):
            raise ValueError(error + "нужны непустые реплики с идентификатором и текстом")
        turn_ids = [turn["turn_id"] for turn in turns]
        if len(set(turn_ids)) != len(turn_ids) or row["target_turn_id"] not in turn_ids:
            raise ValueError(error + "целевая реплика отсутствует или идентификаторы повторяются")
        fragment_id = row.setdefault("fragment_id", 0)
        if type(fragment_id) is not int or fragment_id < 0:
            raise ValueError(error + "fragment_id должен быть неотрицательным целым числом")
        if not isinstance(row.get("background", ""), str) or type(row.get("order_known", True)) is not bool:
            raise ValueError(error + "неверный тип background или order_known")
        key = (row["case_id"], row["target_turn_id"], fragment_id)
        if key in seen:
            raise ValueError(error + "повторная разметка того же фрагмента")
        case = (row["group_id"], turns, row.get("background", ""), row.get("order_known", True))
        if row["case_id"] in cases and cases[row["case_id"]] != case:
            raise ValueError(error + "один случай содержит разные исходные данные")
        cases[row["case_id"]] = case
        seen.add(key)
        try:
            row["turns"] = [Turn(**turn) for turn in turns]
        except (TypeError, ValueError):
            raise ValueError(error + "неверные поля реплики") from None
        rows.append(row)
    if not rows:
        raise ValueError("Файл не содержит размеченных примеров")
    return rows, {"sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows),
                  "groups": sorted({row["group_id"] for row in rows}), "cases": len(cases)}


def prepare_rows(rows, tokenizer):
    # выбирает размеченные фрагменты и проверяет цитаты
    cases, fragments = {}, []
    for number, row in enumerate(rows, 1):
        if row["case_id"] not in cases:
            cases[row["case_id"]] = prepare_fragments(
                row["turns"], tokenizer, background=row.get("background", ""),
                order_known=row.get("order_known", True),
            )
        fragment = next((item for item in cases[row["case_id"]]
                         if item["turn_id"] == row["target_turn_id"]
                         and item["fragment_id"] == row["fragment_id"]), None)
        if fragment is None:
            raise ValueError(f"Пример {number}: выбранный фрагмент отсутствует")
        if any(label == 1 and quote not in fragment["text"]
               for label, quote in zip(row["labels"], row["evidence"])):
            raise ValueError(f"Пример {number}: положительная цитата не входит в выбранный фрагмент")
        fragments.append(fragment)
    return fragments, np.asarray([row["labels"] for row in rows], dtype=float)


def validation_metrics(labels, scores):
    # считает диагностические метрики только по известным меткам
    result = {}
    for index, name in enumerate(TEXT_LABELS):
        known = np.isfinite(labels[:, index])
        truth = labels[known, index].astype(int)
        values = (None, None, None)
        if known.any():
            values = precision_recall_fscore_support(
                truth, scores[known, index] >= 0.5, average="binary", zero_division=0
            )[:3]
        result[name] = dict(zip(("precision", "recall", "f1"), values))
        result[name].update(known_count=int(known.sum()), positive_support=int(truth.sum()))
    return result


def main():
    # обучает классификаторы и сохраняет результат без вывода исходных текстов
    parser = argparse.ArgumentParser(description="Обучение семи текстовых классификаторов")
    parser.add_argument("train", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--download", action="store_true", help="Разрешить загрузку E5")
    args = parser.parse_args()
    try:
        train, train_info = read_rows(args.train, "train")
        validation, validation_info = read_rows(args.validation, "validation") if args.validation else ([], None)
        if validation and (set(train_info["groups"]) & set(validation_info["groups"])
                           or {row["case_id"] for row in train} & {row["case_id"] for row in validation}):
            raise ValueError("Train и validation пересекаются по группам или случаям")
        validate_labels([row["labels"] for row in train])
        encoder = E5Encoder(allow_download=args.download)
        fragments, labels = prepare_rows(train, encoder.tokenizer)
        validation_data = prepare_rows(validation, encoder.tokenizer) if validation else None
        model = TextModel(encoder).fit(fragments, labels)
        metrics = validation_metrics(validation_data[1], model.scores(validation_data[0])) if validation_data else None
        model.save(args.output_dir, training_info={"train": train_info, "validation": validation_info,
                   "validation_metrics": metrics, "diagnostic_threshold": 0.5})
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print("Модель сохранена с диагностическими метриками validation" if validation else
          "Модель сохранена без оценки качества поскольку validation не задана")


if __name__ == "__main__":
    main()
