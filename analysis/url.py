import hashlib
import ipaddress
import json
import re
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import idna
import numpy as np
import tldextract
from catboost import CatBoostClassifier, Pool


URL_FEATURES = (
    "url_length", "host_length", "path_length", "query_length",
    "host_label_count", "host_digit_fraction", "host_hyphen_count",
    "query_parameter_count", "uses_https", "host_is_ip", "has_userinfo", "host_has_punycode",
)
URL_PARAMETERS = {
    "iterations": 400, "depth": 5, "learning_rate": 0.05,
    "loss_function": "Logloss", "auto_class_weights": "Balanced",
    "random_seed": 42, "thread_count": 2, "verbose": False, "allow_writing_files": False,
}
URL_PROCESSING = {
    "version": "url-input-v1",
    "features": list(URL_FEATURES),
    "host": "idna-uts46-std3-lower-no-final-dot",
    "url_length": "original-without-outer-spaces",
    "query_count": "parse_qsl-keep-blank-values-ignore-empty-fields",
    "private_suffixes": False,
    "psl_sha256": hashlib.sha256(files("tldextract").joinpath(".tld_set_snapshot").read_bytes()).hexdigest(),
    "packages": {name: version(name) for name in ("catboost", "idna", "tldextract")},
}
SUFFIXES = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=(), include_psl_private_domains=False)
CATALOGUE_PATH = Path(__file__).with_name("brands.json")


def normalize_host(host):
    # приводит домен или адрес к одному написанию
    if ":" not in host:
        try:
            host = idna.uts46_remap(host, std3_rules=True, transitional=False)
        except idna.IDNAError:
            raise ValueError("Неверный домен") from None
    if host.endswith(".."):
        raise ValueError("Неверный hostname")
    host = host.removesuffix(".").lower()
    if not host or "%" in host:
        raise ValueError("Неверный hostname")
    try:
        return str(ipaddress.ip_address(host)), True
    except ValueError:
        if re.fullmatch(r"[0-9]+|0x[0-9a-f]+", host.rsplit(".", 1)[-1]):
            raise ValueError("Неоднозначная запись IP адреса") from None
    try:
        return idna.encode(host, uts46=True, std3_rules=True).decode("ascii"), False
    except idna.IDNAError:
        raise ValueError("Неверный домен") from None


def extract_url(url):
    # разбирает адрес и считает признаки без сетевых запросов
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Нужен непустой URL")
    if any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("URL содержит запрещенные символы")
    url = url.strip()
    if any(char.isspace() for char in url):
        raise ValueError("URL содержит пробелы")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Нужен полный HTTP или HTTPS адрес")
        # проверка порта выполняется только при чтении этого поля
        parsed.port
        host, is_ip = normalize_host(parsed.hostname)
    except ValueError:
        raise ValueError("Некорректный HTTP или HTTPS адрес") from None
    domain = host if is_ip else SUFFIXES(host).top_domain_under_public_suffix or None
    features = {
        "url_length": len(url), "host_length": len(host),
        "path_length": len(parsed.path), "query_length": len(parsed.query),
        "host_label_count": len(host.split(".")),
        "host_digit_fraction": sum(char.isdigit() for char in host) / len(host),
        "host_hyphen_count": host.count("-"),
        "query_parameter_count": len(parse_qsl(parsed.query, keep_blank_values=True)),
        "uses_https": int(parsed.scheme == "https"), "host_is_ip": int(is_ip),
        "has_userinfo": int(parsed.username is not None),
        "host_has_punycode": int(any(label.startswith("xn--") for label in host.split("."))),
    }
    return {"url": url, "host": host, "domain": domain, "features": features}


def matches_domains(host, domains):
    # сравнивает домены по границе метки
    return any(
        host == domain or host.endswith("." + domain)
        for domain in (normalize_host(item)[0] for item in domains)
    )


def brand_signals(info, expected_site="", catalogue=None):
    # сравнивает распознанный бренд с подтвержденными доменами
    if catalogue is None:
        catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    if not isinstance(expected_site, str):
        raise ValueError("Название организации должно быть строкой")
    expected_site = expected_site.strip()
    expected = re.sub(r"[\W_]+", "", expected_site.casefold())
    expected_host = None
    if "." in expected_site or "://" in expected_site:
        try:
            value = expected_site if "://" in expected_site else "https://" + expected_site
            expected_host = extract_url(value)["host"]
        except ValueError:
            pass
    decoded = info["host"] if info["features"]["host_is_ip"] else idna.decode(info["host"])
    specific = catalogue.get("matching_policy") == "specific-domain-v2"
    matches = []
    for brand in catalogue["brands"]:
        priority = (False, 1)
        if expected:
            matched = expected in {
                re.sub(r"[\W_]+", "", alias.casefold()) for alias in [brand["name"]] + brand["aliases"]
            }
            if expected_host:
                domains = [domain for domain in brand["domains"] if matches_domains(expected_host, [domain])]
                matched = bool(domains)
                if specific and matched:
                    priority = (True, max(len(normalize_host(domain)[0]) for domain in domains))
        else:
            aliases = [alias for alias in brand["host_aliases"] if re.search(
                r"(?:^|[.\-])" + re.escape(alias.casefold()) + r"(?=$|[.\-])", decoded
            )]
            domains = [domain for domain in brand["domains"] if specific and matches_domains(info["host"], [domain])]
            matched = bool(aliases or domains)
            if specific and domains:
                priority = (True, max(len(normalize_host(domain)[0]) for domain in domains))
            elif specific and aliases:
                priority = (False, max(map(len, aliases)))
        if matched:
            matches.append((priority, brand))
    best = max((priority for priority, brand in matches), default=None)
    selected = [brand for priority, brand in matches if priority == best]
    if len(selected) != 1:
        return {"brand_mismatch": None, "brand_comparison_available": False, "brand": None}
    brand = selected[0]
    matched = matches_domains(info["host"], brand["domains"])
    return {"brand_mismatch": int(not matched), "brand_comparison_available": True, "brand": brand["name"]}


def url_matrix(urls):
    # собирает одинаковый порядок признаков для обучения и прогноза
    if isinstance(urls, str):
        raise ValueError("Нужен список URL")
    return np.asarray(
        [[info["features"][name] for name in URL_FEATURES] for info in map(extract_url, urls)],
        dtype=float,
    ).reshape(-1, len(URL_FEATURES))


def url_version(checksum, catalogue):
    # связывает версию модели с обработкой и справочником
    content = json.dumps(
        {"model": checksum, "processing": URL_PROCESSING, "catalogue": catalogue},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    return "url-" + hashlib.sha256(content).hexdigest()[:16]


class URLModel:
    def __init__(self, catalogue=None):
        # сохраняет справочник и место для классификатора
        if catalogue is None:
            catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
        self.catalogue = json.loads(json.dumps(catalogue, ensure_ascii=False))
        self.model = None
        self.analyzer_version = "url-unsaved"

    def fit(self, urls, labels):
        # обучает классификатор только по признакам строки
        labels = np.asarray(labels)
        if labels.ndim != 1 or len(labels) != len(urls) or set(labels) != {0, 1}:
            raise ValueError("Нужны URL и оба класса 0 и 1")
        matrix = url_matrix(urls)
        model = CatBoostClassifier(**URL_PARAMETERS)
        model.fit(Pool(matrix, labels, feature_names=list(URL_FEATURES)))
        self.model = model
        self.analyzer_version = "url-unsaved"
        return self

    def scores(self, urls):
        # возвращает оценки фишинга для переданных адресов
        if self.model is None:
            raise ValueError("URL модель еще не обучена")
        matrix = url_matrix(urls)
        if not len(matrix):
            return np.empty(0)
        return self.model.predict_proba(Pool(matrix, feature_names=list(URL_FEATURES)))[:, 1]

    def predict(self, urls, expected_site=""):
        # объединяет оценки ссылок и сохраняет источники максимумов
        scores = self.scores(urls)
        rows = []
        for url, score in zip(urls, scores):
            info = extract_url(url)
            info.update(brand_signals(info, expected_site, self.catalogue))
            info["score"] = float(score)
            rows.append(info)
        values = dict.fromkeys(("url_score", "brand_mismatch", "has_userinfo", "host_is_ip"))
        refs = dict.fromkeys(values)
        for name in values:
            candidates = [row for row in rows if name != "brand_mismatch" or row[name] is not None]
            if not candidates:
                continue
            key = "score" if name == "url_score" else name
            best = max(candidates, key=lambda row: row[key] if key in row else row["features"][key])
            value = best[key] if key in best else best["features"][key]
            if name == "brand_mismatch" and not value and len(candidates) != len(rows):
                continue
            values[name] = value
            refs[name] = {"url": best["url"], "host": best["host"], "brand": best["brand"]}
        values["brand_comparison_available"] = int(values["brand_mismatch"] is not None)
        refs["brand_comparison_available"] = refs["brand_mismatch"]
        return {
            "status": "ok" if rows else "empty", "analyzer_version": self.analyzer_version,
            "values": values, "source_refs": refs, "urls": rows,
        }

    def save(self, path, training_info=None):
        # сохраняет нативные веса и описание модели
        if self.model is None:
            raise ValueError("Нельзя сохранить необученную URL модель")
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        weights = path / "model.cbm"
        self.model.save_model(str(weights))
        checksum = hashlib.sha256(weights.read_bytes()).hexdigest()
        self.analyzer_version = url_version(checksum, self.catalogue)
        manifest = {
            "analyzer_version": self.analyzer_version, "model_sha256": checksum,
            "processing": URL_PROCESSING, "parameters": URL_PARAMETERS,
            "catalogue": self.catalogue, "training": training_info or {},
        }
        (path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path):
        # проверяет версию и загружает свои веса CatBoost
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        checksum = hashlib.sha256((path / "model.cbm").read_bytes()).hexdigest()
        if (
            manifest["processing"] != URL_PROCESSING or manifest["parameters"] != URL_PARAMETERS
            or manifest["model_sha256"] != checksum
            or manifest["analyzer_version"] != url_version(checksum, manifest["catalogue"])
        ):
            raise ValueError("Несовместимая версия URL модели")
        model = cls(manifest["catalogue"])
        model.model = CatBoostClassifier()
        model.model.load_model(str(path / "model.cbm"))
        if list(model.model.classes_) != [0, 1] or model.model.feature_names_ != list(URL_FEATURES):
            raise ValueError("Неверные классы или признаки URL модели")
        model.analyzer_version = manifest["analyzer_version"]
        return model
