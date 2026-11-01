import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression


TEXT_LABELS = (
    "urgency",
    "money_request",
    "credentials_request",
    "secrecy",
    "remote_access",
    "authority_claim",
    "protective_transfer",
)
MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
PROCESSING = {
    "version": "text-input-v1",
    "chunk_tokens": 256,
    "overlap_tokens": 64,
    "context_turns": 4,
    "max_tokens": 512,
    "prefix": "query: ",
    "pooling": "mean",
    "normalize": True,
    "input_features": 768,
}


def prepare_fragments(turns, tokenizer, background="", order_known=True):
    # делит реплики и готовит контекст без будущих сообщений
    if not isinstance(background, str) or not isinstance(order_known, bool):
        raise ValueError("Неверный контекст или признак порядка реплик")
    ids = [turn.turn_id for turn in turns]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("Нужны разные непустые номера реплик")
    fragments = []
    prefix = PROCESSING["prefix"]
    for index, turn in enumerate(turns):
        offsets = tokenizer(
            turn.text, add_special_tokens=False, return_offsets_mapping=True
        )["offset_mapping"]
        offsets = [(start, end) for start, end in offsets if end > start]
        if not offsets:
            raise ValueError("В реплике нет распознаваемых токенов")
        step = PROCESSING["chunk_tokens"] - PROCESSING["overlap_tokens"]
        for part, start in enumerate(range(0, len(offsets), step)):
            end = min(start + PROCESSING["chunk_tokens"], len(offsets))
            left, right = offsets[start][0], offsets[end - 1][1]
            text = turn.text[left:right]
            target = f"{prefix}TARGET {turn.speaker or 'unknown'}: {text}"
            context = [f"BACKGROUND: {background}"] if background.strip() else []
            if order_known:
                context.extend(
                    f"{previous.speaker or 'unknown'}: {previous.text}"
                    for previous in turns[max(0, index - PROCESSING["context_turns"]):index]
                )
            query = prefix + "\n".join(context + [target.removeprefix(prefix)])
            while context and len(tokenizer(query)["input_ids"]) > PROCESSING["max_tokens"]:
                context.pop(0)
                query = prefix + "\n".join(context + [target.removeprefix(prefix)])
            if len(tokenizer(target)["input_ids"]) > PROCESSING["max_tokens"]:
                raise ValueError("Целевой фрагмент превышает лимит энкодера")
            fragments.append({
                "turn_id": turn.turn_id,
                "fragment_id": part,
                "speaker": turn.speaker,
                "owner": turn.owner,
                "source_span": turn.source_span,
                "start": left,
                "end": right,
                "text": text,
                "target": target,
                "context": query,
            })
            if end == len(offsets):
                break
    return fragments


class E5Encoder:
    identity = {"model_id": MODEL_ID, "revision": MODEL_REVISION}

    def __init__(self, allow_download=False, cache_dir="var/ml/cache"):
        # загружает закрепленную версию энкодера на процессор
        from transformers import AutoModel, AutoTokenizer

        options = {
            "revision": MODEL_REVISION,
            "cache_dir": str(cache_dir),
            "local_files_only": not allow_download,
            "trust_remote_code": False,
        }
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, **options)
        self.model = AutoModel.from_pretrained(MODEL_ID, use_safetensors=True, **options)
        self.model.to("cpu").eval().requires_grad_(False)

    def encode(self, texts):
        # получает нормализованные векторы без обучения энкодера
        import torch

        vectors = []
        for start in range(0, len(texts), 16):
            batch = self.tokenizer(
                texts[start:start + 16], padding=True, truncation=False, return_tensors="pt"
            )
            if batch["input_ids"].shape[1] > PROCESSING["max_tokens"]:
                raise ValueError("Вход превышает лимит энкодера")
            with torch.inference_mode():
                hidden = self.model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1)
                pooled = (hidden * mask).sum(1) / mask.sum(1)
                vectors.append(torch.nn.functional.normalize(pooled, p=2, dim=1).numpy())
        return np.concatenate(vectors) if vectors else np.empty((0, 384), dtype=np.float32)


def encode_fragments(fragments, encoder):
    # соединяет векторы целевого текста и контекста
    count = len(fragments)
    texts = [item["target"] for item in fragments] + [item["context"] for item in fragments]
    vectors = np.asarray(encoder.encode(texts))
    if vectors.shape != (count * 2, 384) or not np.isfinite(vectors).all():
        raise ValueError("Энкодер вернул неверные векторы")
    return np.concatenate((vectors[:count], vectors[count:]), axis=1)


def validate_labels(labels):
    # проверяет метки и наличие двух классов для каждого признака
    labels = np.asarray(labels, dtype=float)
    if labels.ndim != 2 or labels.shape[1] != len(TEXT_LABELS) or not len(labels):
        raise ValueError("Нужны семь меток для каждого примера")
    if not np.all(np.isnan(labels) | (labels == 0) | (labels == 1)):
        raise ValueError("Метки должны быть 0 1 или None")
    for column, name in enumerate(TEXT_LABELS):
        known = labels[~np.isnan(labels[:, column]), column]
        if set(known) != {0, 1}:
            raise ValueError(f"Для {name} нужны оба класса")
    return labels


def artifact_version(checksum, encoder_identity):
    # считает версию весов энкодера и обработки
    content = json.dumps(
        {"heads": checksum, "encoder": encoder_identity, "processing": PROCESSING}, sort_keys=True
    ).encode("utf-8")
    return "text-" + hashlib.sha256(content).hexdigest()[:16]


class TextModel:
    def __init__(self, encoder):
        # сохраняет энкодер и место для обученных классификаторов
        self.encoder = encoder
        self.heads = []
        self.analyzer_version = "text-unsaved"

    def fit(self, fragments, labels):
        # обучает каждый признак только по известным меткам
        labels = validate_labels(labels)
        if len(labels) != len(fragments):
            raise ValueError("Число фрагментов и строк разметки должно совпадать")
        vectors = encode_fragments(fragments, self.encoder)
        heads = []
        for column in range(len(TEXT_LABELS)):
            known = ~np.isnan(labels[:, column])
            head = LogisticRegression(C=1, max_iter=1000, class_weight="balanced", random_state=42)
            heads.append(head.fit(vectors[known], labels[known, column]))
        self.heads = heads
        self.analyzer_version = "text-unsaved"
        return self

    def scores(self, fragments):
        # возвращает семь оценок для каждого фрагмента
        if len(self.heads) != len(TEXT_LABELS):
            raise ValueError("Текстовая модель еще не обучена")
        if not fragments:
            return np.empty((0, len(TEXT_LABELS)))
        vectors = encode_fragments(fragments, self.encoder)
        return np.column_stack([head.predict_proba(vectors)[:, 1] for head in self.heads])

    def predict(self, turns, background="", order_known=True):
        # собирает текущие и предыдущие признаки с исходными фрагментами
        fragments = prepare_fragments(turns, self.encoder.tokenizer, background, order_known)
        scores = self.scores(fragments)
        results = []
        history = {}
        for turn in turns:
            rows = [index for index, item in enumerate(fragments) if item["turn_id"] == turn.turn_id]
            previous = history.get(turn.speaker, {}) if order_known and turn.speaker else {}
            values, refs, current = {}, {}, {}
            for column, name in enumerate(TEXT_LABELS):
                row = max(rows, key=lambda index: scores[index, column])
                item = fragments[row]
                value = float(scores[row, column])
                ref = {key: item[key] for key in (
                    "turn_id", "fragment_id", "start", "end", "text", "speaker", "owner", "source_span"
                )}
                values[f"text_{name}"] = value
                refs[f"text_{name}"] = ref
                old = previous.get(name)
                values[f"previous_{name}"] = old[0] if old else None
                refs[f"previous_{name}"] = old[1] if old else None
                current[name] = old if old and old[0] >= value else (value, ref)
            if order_known and turn.speaker:
                history[turn.speaker] = current
            results.append({
                "turn_id": turn.turn_id, "speaker": turn.speaker, "owner": turn.owner,
                "values": values, "source_refs": refs,
            })
        return {
            "status": "ok" if results else "empty",
            "analyzer_version": self.analyzer_version,
            "order_known": order_known,
            "turns": results,
        }

    def save(self, path, training_info=None):
        # сохраняет классификаторы и описание их версии
        if len(self.heads) != len(TEXT_LABELS):
            raise ValueError("Нельзя сохранить необученную модель")
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        weights = path / "heads.joblib"
        joblib.dump(self.heads, weights)
        checksum = hashlib.sha256(weights.read_bytes()).hexdigest()
        self.analyzer_version = artifact_version(checksum, self.encoder.identity)
        manifest = {
            "analyzer_version": self.analyzer_version,
            "encoder": self.encoder.identity,
            "labels": list(TEXT_LABELS),
            "processing": PROCESSING,
            "heads_sha256": checksum,
            "packages": {name: version(name) for name in ("scikit-learn", "numpy", "joblib")},
            "training": training_info or {},
        }
        (path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path, encoder):
        # загружает свои веса и проверяет совместимость входов
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        weights = (path / "heads.joblib").read_bytes()
        checksum = hashlib.sha256(weights).hexdigest()
        if (
            manifest["labels"] != list(TEXT_LABELS)
            or manifest["processing"] != PROCESSING
            or manifest.get("encoder") != encoder.identity
            or manifest["heads_sha256"] != checksum
            or manifest["analyzer_version"] != artifact_version(checksum, encoder.identity)
            or manifest["packages"] != {
                name: version(name) for name in ("scikit-learn", "numpy", "joblib")
            }
        ):
            raise ValueError("Несовместимая версия текстовой модели")
        model = cls(encoder)
        model.heads = joblib.load(path / "heads.joblib")
        if len(model.heads) != len(TEXT_LABELS) or any(
            not isinstance(head, LogisticRegression)
            or list(head.classes_) != [0, 1]
            or head.n_features_in_ != PROCESSING["input_features"]
            for head in model.heads
        ):
            raise ValueError("Неверный набор классификаторов")
        model.analyzer_version = manifest["analyzer_version"]
        return model
