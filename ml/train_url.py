import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_fscore_support

from analysis.url import URL_PROCESSING, URLModel, extract_url


def read_rows(path, split):
    # проверяет метки и зарегистрированные домены до обучения
    raw = Path(path).read_bytes()
    urls, labels, groups, seen = [], [], set(), set()
    for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        error = f"Строка {number}: "
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(error + "неверный JSON") from None
        if not isinstance(row, dict) or row.get("split") != split:
            raise ValueError(error + f"ожидается объект с split={split}")
        if type(row.get("label")) is not int or row["label"] not in (0, 1):
            raise ValueError(error + "label должен быть 0 или 1")
        if not isinstance(row.get("url"), str) or not isinstance(row.get("group"), str):
            raise ValueError(error + "нужны строковые url и group")
        try:
            parsed = extract_url(row["url"])
        except ValueError:
            raise ValueError(error + "неверный URL") from None
        if parsed["domain"] is None or row["group"] != parsed["domain"]:
            raise ValueError(error + "group не совпадает с зарегистрированным доменом")
        if parsed["url"] in seen:
            raise ValueError(error + "URL повторяется")
        seen.add(parsed["url"])
        urls.append(parsed["url"])
        labels.append(row["label"])
        groups.add(row["group"])
    if set(labels) != {0, 1}:
        raise ValueError(f"Часть {split} должна содержать оба класса")
    info = {"sha256": hashlib.sha256(raw).hexdigest(), "rows": len(urls), "groups": len(groups),
            "groups_sha256": hashlib.sha256("\n".join(sorted(groups)).encode()).hexdigest(),
            "label_counts": dict(Counter(labels))}
    return urls, np.asarray(labels), groups, info


def validation_metrics(labels, scores):
    # считает диагностические метрики при фиксированном пороге
    predicted = scores >= 0.5
    precision, recall, f1, _ = precision_recall_fscore_support(
        labels, predicted, average="binary", zero_division=0
    )
    return {"average_precision": float(average_precision_score(labels, scores)),
            "precision": float(precision), "recall": float(recall), "f1": float(f1),
            "false_positive_rate": float(predicted[labels == 0].mean()), "threshold": 0.5}


def read_provenance(path, train_info, validation_info):
    # сверяет хеши файлов и правила обработки ссылок
    raw = Path(path).read_bytes()
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Неверный JSON в описании источника") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("splits"), dict):
        raise ValueError("В описании источника отсутствуют части набора")
    if manifest.get("url_processing") != URL_PROCESSING:
        raise ValueError("Правила обработки URL в источнике отличаются от текущих")
    for name, info in (("train", train_info), ("validation", validation_info)):
        part = manifest["splits"].get(name)
        if info is not None and (not isinstance(part, dict) or part.get("sha256") != info["sha256"]):
            raise ValueError(f"Хеш части {name} не совпадает с описанием источника")
    return {"sha256": hashlib.sha256(raw).hexdigest(),
            **{key: manifest.get(key) for key in ("source", "cleaning", "split_rule")}}


def main():
    # обучает URL модель только на обучающей части
    parser = argparse.ArgumentParser(description="Обучение URL классификатора")
    parser.add_argument("train", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.output_dir.exists():
            raise ValueError("Папка результата уже существует")
        urls, labels, groups, train_info = read_rows(args.train, "train")
        validation = read_rows(args.validation, "validation") if args.validation else None
        if validation is not None and groups & validation[2]:
            raise ValueError("Train и validation пересекаются по зарегистрированным доменам")
        validation_info = validation[3] if validation is not None else None
        source = read_provenance(args.source_manifest, train_info, validation_info) if args.source_manifest else None
        model = URLModel().fit(urls, labels)
        metrics = validation_metrics(validation[1], model.scores(validation[0])) if validation is not None else None
        model.save(args.output_dir, training_info={"train": train_info, "validation": validation_info,
                   "validation_metrics": metrics, "diagnostic_threshold": 0.5, "source_manifest": source})
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(metrics, ensure_ascii=False) if metrics is not None else
          "Модель сохранена без оценки качества поскольку validation не задана")


if __name__ == "__main__":
    main()
