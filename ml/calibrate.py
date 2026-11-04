import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from analysis.runtime import THRESHOLD_NAMES
from ml.train_risk import read_jsonl


def best_threshold(labels, scores):
    # выбирает максимум F1 и больший порог при равенстве
    labels, scores = np.asarray(labels), np.asarray(scores, dtype=float)
    if labels.shape != scores.shape or labels.ndim != 1 or set(labels) != {0, 1}:
        raise ValueError("Для порога нужны оба класса и по одной оценке на метку")
    if not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any():
        raise ValueError("Оценки должны быть от нуля до единицы")
    order = np.argsort(-scores, kind="stable")
    ends = np.r_[np.flatnonzero(np.diff(scores[order])), len(scores) - 1]
    true_positive = np.cumsum(labels[order], dtype=np.int64)[ends]
    positive_count = int(np.count_nonzero(labels))
    denominators = positive_count + ends + 1
    best = 0
    for index in range(1, len(ends)):
        if int(true_positive[index]) * int(denominators[best]) > int(true_positive[best]) * int(denominators[index]):
            best = index
    tp, predicted_count = int(true_positive[best]), int(ends[best]) + 1
    return float(scores[order[ends[best]]]), {
        "precision": tp / predicted_count, "recall": tp / positive_count,
        "f1": 2 * tp / int(denominators[best]), "known_labels": len(labels),
        "label_counts": dict(Counter(int(label) for label in labels)),
    }


def read_rows(path, versions):
    # читает только независимые оценки калибровочной части
    rows, checksum = read_jsonl(path)
    values = {name: {"labels": [], "scores": [], "unknown": 0, "unavailable_score": 0}
              for name in sorted(THRESHOLD_NAMES)}
    seen, cases, groups = set(), {}, set()
    for number, row in enumerate(rows, 1):
        error = f"Строка {number}: "
        if row.get("split") != "calibration" or row.get("prediction_origin") != "heldout" or row.get("fold") is not None:
            raise ValueError(error + "нужны calibration и heldout")
        if any(not isinstance(row.get(name), str) or not row[name].strip() for name in ("case_id", "group_id")):
            raise ValueError(error + "нужны case_id и group_id")
        if row.get("model_versions") != versions:
            raise ValueError(error + "версии моделей не совпадают с комплектом")
        target = row.get("target_turn_id")
        if target is not None and (not isinstance(target, str) or not target.strip()):
            raise ValueError(error + "неверный target_turn_id")
        key = (row["case_id"], target)
        if key in seen or row["case_id"] in cases and cases[row["case_id"]] != row["group_id"]:
            raise ValueError(error + "повторная цель или разные группы одного случая")
        labels, scores = row.get("labels"), row.get("scores")
        if not isinstance(labels, dict) or not isinstance(scores, dict) or set(labels) != THRESHOLD_NAMES or set(scores) != THRESHOLD_NAMES:
            raise ValueError(error + "labels и scores должны содержать все восемь признаков")
        for name in values:
            label, score = labels[name], scores[name]
            if label is not None and (type(label) is not int or label not in (0, 1)):
                raise ValueError(error + "метка должна быть 0 1 или null")
            if score is not None and (type(score) not in (int, float) or not 0 <= score <= 1):
                raise ValueError(error + "оценка должна быть от нуля до единицы или null")
            if label is None:
                values[name]["unknown"] += 1
            elif score is None:
                values[name]["unavailable_score"] += 1
            else:
                values[name]["labels"].append(label)
                values[name]["scores"].append(score)
        seen.add(key)
        cases[row["case_id"]] = row["group_id"]
        groups.add(row["group_id"])
    return values, {"sha256": checksum, "rows": len(rows), "case_ids": sorted(cases), "group_ids": sorted(groups)}


def calibrate(values):
    # подбирает все пороги до записи итогового файла
    thresholds, metrics = {}, {}
    for name, column in values.items():
        try:
            threshold, metric = best_threshold(column["labels"], column["scores"])
        except ValueError as error:
            raise ValueError(f"Признак {name}: {error}") from None
        thresholds[name] = threshold
        metrics[name] = {**metric, "unknown_labels": column["unknown"],
                         "unavailable_scores": column["unavailable_score"]}
    return thresholds, metrics


def main():
    # сохраняет пороги для точных версий готовых моделей
    parser = argparse.ArgumentParser(description="Калибровка показа признаков без переобучения моделей")
    parser.add_argument("scores", type=Path)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        output = args.output or args.model_dir / "calibration.json"
        if output.exists():
            raise ValueError("Файл результата уже существует")
        manifests = {name: json.loads((args.model_dir / name / "manifest.json").read_text(encoding="utf-8"))
                     for name in ("text", "url", "risk")}
        versions = {name: manifest["analyzer_version"] for name, manifest in manifests.items()}
        values, info = read_rows(args.scores, versions)
        training = manifests["risk"].get("training", {})
        if not isinstance(training.get("train"), dict) or "group_ids" not in training["train"]:
            raise ValueError("У дерева отсутствуют группы обучения для проверки независимости calibration")
        for split in ("train", "validation"):
            part = training.get(split)
            if part and (set(part["group_ids"]) & set(info["group_ids"]) or set(part["case_ids"]) & set(info["case_ids"])):
                raise ValueError("Calibration пересекается с обучением или validation дерева")
        thresholds, metrics = calibrate(values)
        content = {"versions": versions, "thresholds": thresholds, "metrics": metrics, "source": info,
                   "selection": "maximum F1 then higher threshold using exact count comparison"}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({"thresholds": thresholds, "metrics": metrics}, ensure_ascii=False))


if __name__ == "__main__":
    main()
