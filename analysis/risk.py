import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import joblib
import numpy as np
from sklearn.tree import DecisionTreeClassifier

from analysis.features import feature_vector
from analysis.text import TEXT_LABELS
from analysis.transfer import TRANSFER_FEATURES


TEXT_FEATURES = tuple(f"{prefix}_{label}" for prefix in ("text", "previous") for label in TEXT_LABELS)
URL_FEATURES = ("url_score", "brand_mismatch", "has_userinfo", "host_is_ip")
HISTORY_FEATURES = TRANSFER_FEATURES + ("history_loaded", "history_valid", "recipient_provided")
NUMERIC_FEATURES = TEXT_FEATURES + URL_FEATURES + HISTORY_FEATURES
YES_NO_UNKNOWN = ("unknown", "yes", "no")
CATEGORIES = {
    "mode": ("unknown", "text", "url", "audio", "screenshot", "transfer"),
    "purpose": ("unknown", "purchase", "debt", "gift", "own_accounts", "other"),
    "requester": ("unknown", "self", "acquaintance", "seller", "claimed_organization", "other"),
    "channel": ("unknown", "chat", "call", "email", "website", "in_person", "not_applicable", "other"),
    "urgency": YES_NO_UNKNOWN,
    "secrecy": YES_NO_UNKNOWN,
    "changed_details": YES_NO_UNKNOWN,
    "extraction_status": ("unknown", "ok", "partial", "empty", "error"),
}
CONTEXT_FEATURES = ("order_known", "speaker_known", "owner_known")
RISK_FEATURES = (
    NUMERIC_FEATURES + tuple(f"{name}_available" for name in NUMERIC_FEATURES)
    + tuple(f"{name}={option}" for name, options in CATEGORIES.items() for option in options)
    + CONTEXT_FEATURES
)
RISK_SCHEMA_VERSION = "risk-input-v1"
RISK_INDEX = {name: index for index, name in enumerate(RISK_FEATURES)}
RISK_PARAMETERS = {
    "max_depth": 5, "min_samples_leaf": 25, "criterion": "gini",
    "class_weight": "balanced", "random_state": 42,
}
RISK_PROCESSING = {
    "schema_version": RISK_SCHEMA_VERSION, "features": list(RISK_FEATURES),
    "decision_policy": "risk-output-v1", "parameters": RISK_PARAMETERS,
    "packages": {name: version(name) for name in ("numpy", "scikit-learn", "joblib")},
}


def risk_features(mode, *, text=None, url=None, transfer=None, fields=None,
                  extraction_status="unknown", order_known=False):
    # собирает признаки одной целевой реплики для итогового дерева
    fields = fields or {}
    values, refs = {}, {}
    for result, names in (
        (text, TEXT_FEATURES), (url, URL_FEATURES),
        (transfer if mode == "transfer" else None, HISTORY_FEATURES),
    ):
        for name in names:
            values[name] = result["values"].get(name) if result is not None else None
            refs[name] = result.get("source_refs", {}).get(name) if result is not None else None
    categories = {
        name: (fields.get(name) or "unknown") if mode == "transfer" else "unknown"
        for name in CATEGORIES
    }
    categories.update(mode=mode, extraction_status=extraction_status)
    for name, options in CATEGORIES.items():
        if categories[name] not in options:
            raise ValueError(f"Неизвестное значение поля {name}")
    vector = feature_vector(values, NUMERIC_FEATURES)
    vector += [int(categories[name] == option) for name, options in CATEGORIES.items() for option in options]
    vector += [
        int(text is not None and bool(order_known)),
        int(text is not None and text["speaker"] is not None),
        int(text is not None and text["owner"] in ("user", "other")),
    ]
    return {"schema_version": RISK_SCHEMA_VERSION, "vector": vector, "source_refs": refs,
            "transfer": transfer if mode == "transfer" else None, "url": url,
            "speaker": text["speaker"] if text is not None else None}


def risk_matrix(rows):
    # проверяет схему и собирает одинаковый вход дерева
    if any(row["schema_version"] != RISK_SCHEMA_VERSION for row in rows):
        raise ValueError("Несовместимая схема признаков риска")
    matrix = np.asarray([row["vector"] for row in rows], dtype=np.float32)
    if matrix.shape != (len(rows), len(RISK_FEATURES)) or not np.isfinite(matrix).all():
        raise ValueError("Нужны конечные числа в порядке признаков риска")
    return matrix


def risk_version(checksum):
    # связывает версию дерева с весами и обработкой
    content = json.dumps([checksum, RISK_PROCESSING], sort_keys=True).encode()
    return "risk-" + hashlib.sha256(content).hexdigest()[:16]


class RiskModel:
    def __init__(self):
        # готовит место для итогового дерева
        self.model = None
        self.analyzer_version = "risk-unsaved"

    def fit(self, rows, labels):
        # обучает дерево только на известных метках риска
        matrix = risk_matrix(rows)
        labels = np.asarray(labels, dtype=object)
        if labels.shape != (len(rows),) or not set(labels).issubset({0, 1, 2, None}):
            raise ValueError("Нужна одна метка 0 1 2 или None на строку")
        known = np.array([label is not None for label in labels])
        targets = np.asarray(labels[known], dtype=int)
        if set(targets) != {0, 1, 2}:
            raise ValueError("Для обучения нужны все три класса риска")
        model = DecisionTreeClassifier(**RISK_PARAMETERS)
        model.fit(matrix[known], targets)
        self.model = model
        self.analyzer_version = "risk-unsaved"
        return self

    def predict(self, row):
        # возвращает риск и условия дерева без процентов мошенничества
        if self.model is None:
            raise ValueError("Дерево риска еще не обучено")
        matrix = risk_matrix([row])
        vector = matrix[0]
        names = TEXT_FEATURES + URL_FEATURES + (
            "amount_log", "amount_percentile", "amount_median_log_ratio",
        )
        if vector[RISK_INDEX["recipient_share_available"]]:
            names += ("recipient_count_log", "recipient_share", "last_recipient_days")
        content = {name for name in names if vector[RISK_INDEX[name + "_available"]]}
        for name, options in CATEGORIES.items():
            if name not in ("mode", "extraction_status") and not vector[RISK_INDEX[name + "=unknown"]]:
                content.update(f"{name}={option}" for option in options if option != "unknown")
        risk, sufficiency, conditions = None, "insufficient", []
        failed = vector[RISK_INDEX["extraction_status=error"]] or vector[RISK_INDEX["extraction_status=empty"]]
        if not failed and content:
            predicted = int(self.model.predict(matrix)[0])
            tree = self.model.tree_
            for node in self.model.decision_path(matrix).indices:
                column = tree.feature[node]
                if column < 0:
                    continue
                name, value, threshold = RISK_FEATURES[column], float(vector[column]), float(tree.threshold[node])
                source = row.get("source_refs", {}).get(name)
                if source is None and "=" in name and name in content:
                    source = {"field": name.split("=", 1)[0]}
                conditions.append({
                    "feature": name, "value": value, "operator": "<=" if value <= threshold else ">",
                    "threshold": threshold, "source_ref": source, "substantive": name in content,
                })
            if predicted == 0 or any(item["substantive"] for item in conditions):
                risk, sufficiency = predicted, "sufficient"
                if vector[RISK_INDEX["mode=transfer"]] and not vector[RISK_INDEX["amount_percentile_available"]]:
                    sufficiency = "limited"
            if vector[RISK_INDEX["extraction_status=partial"]]:
                risk, sufficiency = None, "limited"
        return {"risk": risk, "sufficiency": sufficiency, "conditions": conditions,
                "analyzer_version": self.analyzer_version}

    def save(self, path, training_info=None):
        # сохраняет свое дерево и описание обучения
        if self.model is None:
            raise ValueError("Нельзя сохранить необученное дерево")
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        weights = path / "tree.joblib"
        joblib.dump(self.model, weights)
        checksum = hashlib.sha256(weights.read_bytes()).hexdigest()
        self.analyzer_version = risk_version(checksum)
        manifest = {"analyzer_version": self.analyzer_version, "processing": RISK_PROCESSING,
                    "tree_sha256": checksum, "training": training_info or {}}
        (path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path):
        # проверяет совместимость и загружает свое дерево
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        checksum = hashlib.sha256((path / "tree.joblib").read_bytes()).hexdigest()
        if (manifest["processing"] != RISK_PROCESSING or manifest["tree_sha256"] != checksum
                or manifest["analyzer_version"] != risk_version(checksum)):
            raise ValueError("Несовместимая версия дерева риска")
        model = cls()
        model.model = joblib.load(path / "tree.joblib")
        if (not isinstance(model.model, DecisionTreeClassifier) or list(model.model.classes_) != [0, 1, 2]
                or model.model.n_features_in_ != len(RISK_FEATURES)):
            raise ValueError("Неверное дерево риска")
        model.analyzer_version = manifest["analyzer_version"]
        return model
