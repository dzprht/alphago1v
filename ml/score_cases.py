import argparse
import hashlib
import json
from pathlib import Path

from analysis.pipeline import Analyzer
from analysis.risk import RISK_FEATURES
from analysis.runtime import THRESHOLD_NAMES
from analysis.text import E5Encoder, TextModel
from analysis.url import URLModel
from ml.prepare_risk import case_domains, read_scenarios


def score_cases(scenarios, analyzer, versions, split):
    # получает оценки независимой части без обучения и подбора порогов
    if split not in ("calibration", "test"):
        raise ValueError("Оценки готовятся только для calibration или test")
    selected = [row for row in scenarios if row["split"] == split]
    for row in selected:
        labels = row.get("labels")
        if not isinstance(labels, dict) or set(labels) != THRESHOLD_NAMES or any(
            label is not None and (type(label) is not int or label not in (0, 1)) for label in labels.values()
        ):
            raise ValueError("Для показа признаков нужны восемь независимых labels 0 1 или null")
    if not selected:
        raise ValueError("Выбранная часть не содержит сценариев")
    prepared_cases, result = {}, []
    for row in selected:
        if row["case_id"] not in prepared_cases:
            prepared_cases[row["case_id"]] = analyzer.prepare(
                row["kind"], row["input_data"], row.get("history"), checked_at=row["checked_at"],
                turns=row.get("turns"), order_known=row.get("order_known", True), extraction=row.get("extraction"))
        prepared = prepared_cases[row["case_id"]]
        targets = prepared["target_turn_ids"]
        if row.get("target_turn_id") not in targets:
            raise ValueError("Цель отсутствует в подготовленных репликах")
        index = targets.index(row.get("target_turn_id"))
        values = dict(zip(RISK_FEATURES, prepared["rows"][index]["vector"]))
        scores = {}
        for name in sorted(THRESHOLD_NAMES):
            keys = [name, name.replace("text_", "previous_", 1)] if name.startswith("text_") else [name]
            scores[name] = max((values[key] for key in keys if values[key + "_available"]), default=None)
        result.append({"case_id": row["case_id"], "group_id": row["group_id"],
                       "target_turn_id": row.get("target_turn_id"), "split": split,
                       "prediction_origin": "heldout", "model_versions": versions,
                       "domains": sorted(case_domains(row)),
                       "labels": row["labels"], "scores": scores})
    return result


def main():
    # сохраняет оценки выбранной части без использования теста для калибровки
    parser = argparse.ArgumentParser(description="Оценки признаков для calibration или финального test")
    parser.add_argument("scenarios", type=Path)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--split", choices=("calibration", "test"), default="calibration")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Файл результата уже существует")
        scenarios, source = read_scenarios(args.scenarios)
        encoder = E5Encoder(allow_download=False)
        text = TextModel.load(args.model_dir / "text", encoder)
        url = URLModel.load(args.model_dir / "url")
        analyzer = Analyzer(text, url, {})
        rows = score_cases(scenarios, analyzer, {"text": text.analyzer_version, "url": url.analyzer_version}, args.split)
        raw = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows).encode()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            output.write(raw)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({"rows": len(rows), "split": args.split, "source_sha256": source["sha256"],
                      "output_sha256": hashlib.sha256(raw).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
