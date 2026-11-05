import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, recall_score

from analysis.risk import RiskModel, risk_matrix


def read_jsonl(path):
    # читает объекты из файла и считает хеш исходных данных
    raw = Path(path).read_bytes()
    rows = []
    for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"Строка {number}: неверный JSON") from None
        if not isinstance(row, dict):
            raise ValueError(f"Строка {number}: нужен объект")
        rows.append(row)
    if not rows:
        raise ValueError("Файл не содержит примеров")
    return rows, hashlib.sha256(raw).hexdigest()


def read_rows(path, split):
    # проверяет группы и источник прогнозов для обучения
    if split not in ("train", "validation"):
        raise ValueError("Обучение принимает только train и validation")
    rows, checksum = read_jsonl(path)
    features, labels, seen, cases, groups = [], [], set(), {}, {}
    versions = {"text": set(), "url": set()}
    fold_models = {}
    origin = "oof" if split == "train" else "heldout"
    for number, row in enumerate(rows, 1):
        error = f"Строка {number}: "
        if row.get("split") != split or row.get("prediction_origin") != origin:
            raise ValueError(error + f"нужны split={split} и prediction_origin={origin}")
        if any(not isinstance(row.get(name), str) or not row[name].strip() for name in ("case_id", "group_id")):
            raise ValueError(error + "нужны case_id и group_id")
        target = row.get("target_turn_id")
        if target is not None and (not isinstance(target, str) or not target.strip()):
            raise ValueError(error + "target_turn_id должен быть строкой или null")
        label = row.get("risk")
        if "risk" not in row or label is not None and (type(label) is not int or label not in (0, 1, 2)):
            raise ValueError(error + "risk должен быть 0 1 2 или null")
        model_versions = row.get("model_versions")
        if not isinstance(model_versions, dict) or set(model_versions) != {"text", "url"} or any(
            not isinstance(value, str) or not value.strip() for value in model_versions.values()
        ):
            raise ValueError(error + "нужны версии text и url моделей")
        fold = row.get("fold")
        if split == "train" and (type(fold) is not int or fold < 0) or split == "validation" and fold is not None:
            raise ValueError(error + "у OOF нужен номер fold а у heldout его быть не должно")
        signature = (fold, tuple(sorted(model_versions.items())))
        if fold is not None:
            if fold in fold_models and fold_models[fold] != signature[1]:
                raise ValueError(error + "один fold содержит разные нижние модели")
            fold_models[fold] = signature[1]
        case = (row["group_id"], signature)
        if row["case_id"] in cases and cases[row["case_id"]] != case:
            raise ValueError(error + "один случай отнесен к разным группам или моделям")
        if row["group_id"] in groups and groups[row["group_id"]] != signature:
            raise ValueError(error + "одна группа отнесена к разным folds или моделям")
        key = (row["case_id"], target)
        if key in seen:
            raise ValueError(error + "повторная целевая реплика")
        if not isinstance(row.get("features"), dict):
            raise ValueError(error + "нужен результат risk_features в features")
        seen.add(key)
        cases[row["case_id"]], groups[row["group_id"]] = case, signature
        features.append(row["features"])
        labels.append(label)
        for name in versions:
            versions[name].add(model_versions[name])
    risk_matrix(features)
    known = [label for label in labels if label is not None]
    if set(known) != {0, 1, 2}:
        raise ValueError(f"Часть {split} должна содержать три известных класса риска")
    folds = sorted(fold_models)
    if split == "train" and len(folds) < 2:
        raise ValueError("Для OOF нужны хотя бы два folds")
    if split == "validation" and any(len(values) != 1 for values in versions.values()):
        raise ValueError("Validation должна использовать один итоговый комплект нижних моделей")
    info = {"sha256": checksum, "rows": len(rows), "known_labels": len(known),
            "label_counts": dict(Counter(known)), "case_ids": sorted(cases), "group_ids": sorted(groups),
            "prediction_origin": origin, "folds": folds,
            "fold_model_versions": {str(fold): dict(values) for fold, values in sorted(fold_models.items())},
            "model_versions": {name: sorted(values) for name, values in versions.items()}}
    return features, labels, info


def validation_metrics(model, rows, labels):
    # считает качество и долю ответов без отказа
    known = np.asarray([label is not None for label in labels])
    selected = [row for row, available in zip(rows, known) if available]
    targets = np.asarray([label for label in labels if label is not None])
    predicted = model.model.predict(risk_matrix(selected))
    answers = [model.predict(row) for row in selected]
    accepted = np.asarray([answer["risk"] is not None for answer in answers])
    return {"known_labels": len(selected), "macro_f1": float(f1_score(targets, predicted, average="macro")),
            "recall_high_risk": float(recall_score(targets == 2, predicted == 2, zero_division=0)),
            "confusion_matrix": confusion_matrix(targets, predicted, labels=[0, 1, 2]).tolist(),
            "decision_coverage": float(accepted.mean()),
            "accepted_macro_f1": float(f1_score(targets[accepted],
                [answer["risk"] for answer in answers if answer["risk"] is not None],
                labels=[0, 1, 2], average="macro", zero_division=0)) if accepted.any() else None}


def main():
    # обучает дерево на заранее рассчитанных признаках
    parser = argparse.ArgumentParser(description="Обучение дерева по готовым OOF признакам")
    parser.add_argument("train", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--validation", type=Path)
    args = parser.parse_args()
    try:
        if args.output_dir.exists():
            raise ValueError("Папка результата уже существует")
        rows, labels, train = read_rows(args.train, "train")
        validation = read_rows(args.validation, "validation") if args.validation else None
        if validation is not None:
            info = validation[2]
            if set(train["group_ids"]) & set(info["group_ids"]) or set(train["case_ids"]) & set(info["case_ids"]):
                raise ValueError("Train и validation пересекаются по группам или случаям")
        model = RiskModel().fit(rows, labels)
        metrics = validation_metrics(model, validation[0], validation[1]) if validation is not None else None
        model.save(args.output_dir, training_info={"train": train,
                   "validation": validation[2] if validation is not None else None, "validation_metrics": metrics})
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(metrics, ensure_ascii=False) if metrics is not None else "Дерево сохранено без оценки качества")


if __name__ == "__main__":
    main()
