import argparse
from collections import Counter
import csv
import hashlib
from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

from analysis.url import URL_PROCESSING, extract_url


SOURCE_URL = "https://archive.ics.uci.edu/static/public/967/phiusiil%2Bphishing%2Burl%2Bdataset.zip"
SOURCE_SHA256 = "0a639fd03aea6308c5b1c10c92aa23c2ce1505447a9137271865cd0badc9a59a"
CSV_NAME = "PhiUSIIL_Phishing_URL_Dataset.csv"
SPLITS = ("train", "validation", "calibration", "test")


def download_source():
    # загружает только официальный архив и проверяет его содержимое по хешу
    path = Path("var/ml/urls/source.zip")
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_SHA256:
            raise ValueError("Сохраненный архив не совпадает с официальным хешем")
        return path
    with urlopen(SOURCE_URL, timeout=30) as response:
        raw = response.read(20_000_001)
    if len(raw) > 20_000_000 or hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Загруженный архив не совпадает с ожидаемым размером или хешем")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".zip.tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return path


def split_for_domain(domain):
    # назначает одну часть всем адресам одного зарегистрированного домена
    bucket = int(hashlib.sha256(f"42:{domain}".encode()).hexdigest(), 16) % 100
    return "train" if bucket < 70 else SPLITS[1 + (bucket - 70) // 10]


def prepare_dataset(source, output_dir):
    # очищает адреса без открытия сайтов и сохраняет разделенные данные
    raw = Path(source).read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    splits, seen = {name: [] for name in SPLITS}, {}
    cleaning = Counter(input_rows=0, duplicate_url=0, invalid_url=0, unknown_public_suffix=0)
    with ZipFile(BytesIO(raw)) as archive:
        if CSV_NAME not in archive.namelist():
            raise ValueError("В архиве отсутствует ожидаемый CSV PhiUSIIL")
        with archive.open(CSV_NAME) as stream:
            reader = csv.DictReader(TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
            if not {"URL", "label"}.issubset(reader.fieldnames or []):
                raise ValueError("В CSV нужны колонки URL и label")
            for row in reader:
                cleaning["input_rows"] += 1
                if row.get("label") not in ("0", "1") or not isinstance(row.get("URL"), str):
                    raise ValueError("В исходном CSV обнаружена неверная метка или строка URL")
                url, label = row["URL"], 1 - int(row["label"])
                try:
                    parsed = extract_url(url)
                except ValueError:
                    cleaning["invalid_url"] += 1
                    continue
                url = parsed["url"]
                if url in seen:
                    if seen[url] != label:
                        raise ValueError("Один исходный URL имеет противоречащие метки")
                    cleaning["duplicate_url"] += 1
                    continue
                seen[url] = label
                if parsed["domain"] is None:
                    cleaning["unknown_public_suffix"] += 1
                    continue
                split = split_for_domain(parsed["domain"])
                splits[split].append({"url": parsed["url"], "label": label,
                                      "group": parsed["domain"], "split": split})
    for name, rows in splits.items():
        if {row["label"] for row in rows} != {0, 1}:
            raise ValueError(f"Часть {name} должна содержать оба класса")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    summaries = {}
    for name, rows in splits.items():
        data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
        groups = sorted({row["group"] for row in rows})
        target = output_dir / f"{name}.jsonl"
        temporary = target.with_suffix(".jsonl.tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        summaries[name] = {"rows": len(rows), "groups": len(groups),
                           "label_counts": dict(Counter(row["label"] for row in rows)),
                           "sha256": hashlib.sha256(data).hexdigest(),
                           "groups_sha256": hashlib.sha256("\n".join(groups).encode()).hexdigest()}
    cleaning["retained_rows"] = sum(len(rows) for rows in splits.values())
    manifest = {"source": {"url": SOURCE_URL, "sha256": source_hash, "bytes": len(raw),
                "official_sha256_match": source_hash == SOURCE_SHA256, "csv_member": CSV_NAME,
                "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "label_mapping": {"0": 1, "1": 0}}, "cleaning": dict(cleaning),
                "split_rule": {"key": "42:<ICANN registered domain or canonical IP>",
                               "bucket": "int(sha256(key).hexdigest(), 16) % 100",
                               "ranges": {"train": [0, 69], "validation": [70, 79],
                                          "calibration": [80, 89], "test": [90, 99]}},
                "url_processing": URL_PROCESSING, "splits": summaries}
    (output_dir / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main():
    # готовит набор по явному источнику без вывода адресов из данных
    parser = argparse.ArgumentParser(description="Подготовка PhiUSIIL без посещения сайтов")
    parser.add_argument("output_dir", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="Локальный ZIP PhiUSIIL")
    source.add_argument("--download", action="store_true", help="Скачать официальный ZIP UCI")
    args = parser.parse_args()
    try:
        manifest = prepare_dataset(download_source() if args.download else args.source, args.output_dir)
    except (ValueError, OSError, BadZipFile, csv.Error) as error:
        parser.error(str(error))
    print(json.dumps({"cleaning": manifest["cleaning"], "splits": manifest["splits"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
