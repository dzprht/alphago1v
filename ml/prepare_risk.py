import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupKFold

from analysis.features import Turn
from analysis.pipeline import Analyzer, find_urls
from analysis.text import E5Encoder, TextModel, validate_labels
from analysis.url import URLModel, extract_url
from ml.train_risk import read_jsonl
from ml.train_text import prepare_rows, read_rows as read_text_rows
from ml.train_url import read_rows as read_url_rows


SPLITS = ("train", "validation", "calibration", "test")


class CachedEncoder:
    def __init__(self, encoder):
        # сохраняет неизменяемый энкодер и готовые векторы текстов
        self.encoder, self.identity, self.tokenizer = encoder, encoder.identity, encoder.tokenizer
        self.cache = {}

    def encode(self, texts):
        # не считает одинаковые замороженные векторы заново в каждом fold
        missing = list(dict.fromkeys(text for text in texts if text not in self.cache))
        if missing:
            self.cache.update(zip(missing, self.encoder.encode(missing)))
        return np.stack([self.cache[text] for text in texts]) if texts else np.empty((0, 384), dtype=np.float32)


def case_domains(row):
    # собирает домены только для контроля пересечений данных
    data = row.get("input_data", {})
    turns = (row.get("extraction") or {}).get("turns", row.get("turns"))
    texts = [turn.text for turn in turns] if turns else [data.get("text", ""), data.get("message", "")]
    texts += [row.get("background", ""), data.get("context", "")]
    urls = [data["url"]] if row.get("kind") == "url" else []
    for text in texts:
        urls.extend(find_urls(text)[0])
    return {info["domain"] or info["host"] for info in map(extract_url, urls)}


def read_scenarios(path):
    # проверяет целевые сценарии и разделение исходных групп
    rows, checksum = read_jsonl(path)
    seen, cases, groups, domains = set(), {}, {}, {}
    for row in rows:
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("case_id", "group_id")):
            raise ValueError("Сценарию нужны case_id и group_id")
        if row.get("split") not in SPLITS or row.get("kind") not in ("text", "url", "transfer", "audio", "screenshot"):
            raise ValueError("Неверные split или kind сценария")
        if not isinstance(row.get("input_data"), dict):
            raise ValueError("Нужен словарь input_data с проверенными полями")
        if type(row.get("order_known", True)) is not bool:
            raise ValueError("Признак порядка должен быть логическим значением")
        if "risk" not in row or row["risk"] is not None and (type(row["risk"]) is not int or row["risk"] not in (0, 1, 2)):
            raise ValueError("Нужна отдельная метка risk 0 1 2 или null")
        target = row.get("target_turn_id")
        if target is not None and (not isinstance(target, str) or not target):
            raise ValueError("Неверная целевая реплика")
        key = (row["case_id"], target)
        signature = json.dumps({name: row.get(name) for name in (
            "group_id", "split", "kind", "input_data", "turns", "history", "checked_at", "order_known", "extraction",
        )}, sort_keys=True, ensure_ascii=False)
        if key in seen or row["case_id"] in cases and cases[row["case_id"]] != signature:
            raise ValueError("Повторная цель или разные исходники одного случая")
        if row["group_id"] in groups and groups[row["group_id"]] != row["split"]:
            raise ValueError("Группа пересекает части набора")
        seen.add(key)
        cases[row["case_id"]], groups[row["group_id"]] = signature, row["split"]
        if not isinstance(row.get("checked_at"), str):
            raise ValueError("Нужен момент проверки в ISO формате")
        row["checked_at"] = datetime.fromisoformat(row["checked_at"])
        if row["checked_at"].utcoffset() is None:
            raise ValueError("Момент проверки должен содержать часовой пояс")
        row["checked_at"] = row["checked_at"].astimezone(timezone.utc)
        if row.get("turns") is not None:
            row["turns"] = [Turn(**turn) for turn in row["turns"]]
        if row.get("extraction") is not None:
            if row["kind"] not in ("audio", "screenshot") or row["extraction"].get("status") not in ("ok", "partial", "empty", "error"):
                raise ValueError("Неверный выход распознавания медиа")
            if type(row["extraction"].get("order_known")) is not bool or not row["extraction"].get("analyzer_version"):
                raise ValueError("Нужны порядок и версия распознавания")
            row["extraction"]["turns"] = [Turn(**turn) for turn in row["extraction"]["turns"]]
        if row["kind"] in ("audio", "screenshot") and not row.get("extraction"):
            raise ValueError("Для медиа нужны настоящие выходы распознавания")
        source_turns = (row.get("extraction") or {}).get("turns", row.get("turns"))
        if source_turns is None:
            field = "text" if row["kind"] == "text" else "message"
            target_ids = ["input"] if row["input_data"].get(field) else [None]
        else:
            target_ids = [turn.turn_id for turn in source_turns] or [None]
        if target not in target_ids or len(set(target_ids)) != len(target_ids):
            raise ValueError("Цель отсутствует или номера исходных реплик повторяются")
        if row["kind"] in ("url", "transfer") and len(target_ids) > 1:
            raise ValueError("У ссылки и перевода одна целевая просьба")
        if row.get("history") is not None:
            for item in row["history"]:
                if (type(item.get("amount_kopecks")) is not int or item["amount_kopecks"] <= 0
                        or item.get("direction") not in ("incoming", "outgoing") or item.get("currency", "RUB") != "RUB"):
                    raise ValueError("История должна содержать положительные суммы в копейках RUB и направление")
                item["occurred_at"] = datetime.fromisoformat(item["occurred_at"])
                if item["occurred_at"].utcoffset() is None:
                    raise ValueError("Дата истории должна содержать часовой пояс")
                item["occurred_at"] = item["occurred_at"].astimezone(timezone.utc)
        for domain in case_domains(row):
            if domain in domains and domains[domain] != row["split"]:
                raise ValueError("Домен сценария пересекает части набора")
            domains[domain] = row["split"]
    if {row["split"] for row in rows} != set(SPLITS):
        raise ValueError("Нужны заранее разделенные train validation calibration test")
    return rows, {"sha256": checksum, "rows": len(rows), "case_ids": sorted(cases), "group_ids": sorted(groups)}


def plan_folds(scenarios, text_rows, url_rows, folds=5, heldout_splits=("validation",)):
    # исключает проверяемые группы и домены до любого обучения
    scenario_groups = {row["case_id"]: row["group_id"] for row in scenarios}
    if any(row["case_id"] in scenario_groups and scenario_groups[row["case_id"]] != row["group_id"] for row in text_rows):
        raise ValueError("Текст и сценарий одного случая имеют разные группы")
    train = [row for row in scenarios if row["split"] == "train"]
    heldout = [row for row in scenarios if row["split"] != "train"]
    if len({row["group_id"] for row in train}) < folds or folds < 2:
        raise ValueError("Для OOF нужны хотя бы две группы и не меньше групп чем folds")
    text_domains = {row["case_id"]: case_domains(row) for row in text_rows}
    splitter = GroupKFold(n_splits=folds, shuffle=True, random_state=42)
    partitions = [(number, [train[index] for index in indexes])
                  for number, (_, indexes) in enumerate(splitter.split(train, groups=[row["group_id"] for row in train]))]
    partitions.append((None, [row for row in heldout if row["split"] in heldout_splits]))
    plans = []
    for fold, targets in partitions:
        excluded = heldout + (targets if fold is not None else [])
        blocked_groups = {row["group_id"] for row in excluded}
        blocked_cases = {row["case_id"] for row in excluded}
        blocked_domains = set().union(*(case_domains(row) for row in excluded))
        texts = [row for row in text_rows if row["group_id"] not in blocked_groups
                 and row["case_id"] not in blocked_cases and not text_domains[row["case_id"]] & blocked_domains]
        urls = [row for row in url_rows if row["group"] not in blocked_domains]
        validate_labels([row["labels"] for row in texts])
        if {row["label"] for row in urls} != {0, 1}:
            raise ValueError("После исключения доменов в URL fold нужны оба класса")
        plans.append({"fold": fold, "targets": targets, "text_train": texts, "url_train": urls,
                      "excluded_groups": sorted(blocked_groups), "excluded_domains": sorted(blocked_domains)})
    return plans


def prepare_features(plans, output_dir, encoder, source_info):
    # сохраняет OOF признаки и отдельный итоговый комплект нижних моделей
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {name: [] for name in SPLITS}
    reports = []
    for plan in plans:
        fold = plan["fold"]
        folder = output_dir / (f"fold-{fold}" if fold is not None else "final")
        fragments, labels = prepare_rows(plan["text_train"], encoder.tokenizer)
        text = TextModel(encoder).fit(fragments, labels)
        url = URLModel().fit([row["url"] for row in plan["url_train"]], [row["label"] for row in plan["url_train"]])
        info = {key: plan[key] for key in ("fold", "excluded_groups", "excluded_domains")}
        info.update(source=source_info, text_rows=len(plan["text_train"]), url_rows=len(plan["url_train"]),
                    selected_group_ids=sorted({row["group_id"] for row in plan["text_train"]}),
                    selected_case_ids=sorted({row["case_id"] for row in plan["text_train"]}),
                    selected_domains=sorted({row["group"] for row in plan["url_train"]}))
        text.save(folder / "text", training_info=info)
        url.save(folder / "url", training_info=info)
        analyzer = Analyzer(text, url, None, {})
        versions = {"text": text.analyzer_version, "url": url.analyzer_version}
        prepared_cases = {}
        for scenario in plan["targets"]:
            if scenario["case_id"] not in prepared_cases:
                prepared_cases[scenario["case_id"]] = analyzer.prepare(
                    scenario["kind"], scenario["input_data"], scenario.get("history"),
                    checked_at=scenario["checked_at"], turns=scenario.get("turns"),
                    order_known=scenario.get("order_known", True), extraction=scenario.get("extraction"))
            prepared = prepared_cases[scenario["case_id"]]
            matches = [index for index, target in enumerate(prepared["target_turn_ids"]) if target == scenario.get("target_turn_id")]
            if len(matches) != 1:
                raise ValueError("Цель сценария отсутствует в выходе анализатора")
            outputs[scenario["split"]].append({
                "case_id": scenario["case_id"], "group_id": scenario["group_id"], "split": scenario["split"],
                "target_turn_id": scenario.get("target_turn_id"), "risk": scenario["risk"],
                "features": prepared["rows"][matches[0]], "model_versions": versions, "fold": fold,
                "prediction_origin": "oof" if fold is not None else "heldout",
                "preparation": {"invalid_urls": prepared["invalid_urls"], "versions": prepared["versions"]},
            })
        reports.append({**info, "model_versions": versions})
    files = {}
    for split, rows in outputs.items():
        if rows:
            raw = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows).encode()
            (output_dir / f"{split}.jsonl").write_bytes(raw)
            files[split] = {"rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}
    report = {"source": source_info, "partitions": reports, "outputs": files}
    (output_dir / "preparation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    # готовит признаки только по явно указанным размеченным файлам
    parser = argparse.ArgumentParser(description="OOF признаки риска с исключением групп и доменов")
    parser.add_argument("scenarios", type=Path)
    parser.add_argument("text_train", type=Path)
    parser.add_argument("url_train", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--heldout-splits", nargs="+", choices=SPLITS[1:], default=["validation"])
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        if args.output_dir.exists():
            raise ValueError("Папка результата уже существует")
        scenarios, scenario_info = read_scenarios(args.scenarios)
        texts, text_info = read_text_rows(args.text_train, "train")
        urls, labels, _, url_info = read_url_rows(args.url_train, "train")
        url_rows = [{"url": value, "label": int(label), "group": extract_url(value)["domain"]}
                    for value, label in zip(urls, labels)]
        plans = plan_folds(scenarios, texts, url_rows, args.folds, args.heldout_splits)
        encoder = CachedEncoder(E5Encoder(allow_download=args.download))
        report = prepare_features(plans, args.output_dir, encoder,
                                   {"scenarios": scenario_info, "text": text_info, "url": url_info})
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(report["outputs"], ensure_ascii=False))


if __name__ == "__main__":
    main()
