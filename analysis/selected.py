import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch
from torch import nn
from transformers import AutoConfig, AutoModel, AutoTokenizer

from analysis.text import TEXT_LABELS, TextModel
from analysis.url import URLModel


class DualTiny(nn.Module):
    def __init__(self, config):
        # создает энкодер и общую голову семи признаков
        super().__init__()
        self.encoder = AutoModel.from_config(config, trust_remote_code=False)
        self.dropout = nn.Dropout(0.1)
        self.head = nn.Linear(config.hidden_size * 2, len(TEXT_LABELS))

    def forward(self, batch):
        # соединяет представления целевой реплики и контекста
        hidden = self.encoder(**batch).last_hidden_state[:, 0]
        count = hidden.shape[0] // 2
        return self.head(self.dropout(torch.cat([hidden[:count], hidden[count:]], dim=1)))


class CharacterCNN(nn.Module):
    def __init__(self):
        # создает свертки для коротких частей адреса
        super().__init__()
        self.embedding = nn.Embedding(130, 32, padding_idx=0)
        self.convolutions = nn.ModuleList([
            nn.Conv1d(32, 64, kernel, padding=kernel // 2) for kernel in (3, 5, 7)
        ])
        self.output = nn.Sequential(nn.Linear(192, 64), nn.ReLU(), nn.Dropout(0.2), nn.Linear(64, 1))

    def forward(self, values):
        # собирает найденные части адреса в одну оценку
        hidden = self.embedding(values).transpose(1, 2)
        parts = [torch.relu(layer(hidden)).amax(dim=2) for layer in self.convolutions]
        return self.output(torch.cat(parts, dim=1)).squeeze(1)


class RuBERTModel(TextModel):
    def __init__(self, directory, analyzer_version):
        # загружает готовый руберт и два локальных токенизатора
        directory = Path(directory)
        torch.set_num_threads(1)
        local = {"local_files_only": True, "trust_remote_code": False}
        fragment_tokenizer = AutoTokenizer.from_pretrained(directory / "fragment_tokenizer", **local)
        super().__init__(SimpleNamespace(tokenizer=fragment_tokenizer))
        self.tokenizer = AutoTokenizer.from_pretrained(directory / "rubert", **local)
        config = AutoConfig.from_pretrained(directory / "rubert", **local)
        state = torch.load(directory / "rubert/weights.pt", map_location="cpu", weights_only=True)
        self.batch_size = state["batch_size"]
        self.model = DualTiny(config)
        self.model.load_state_dict(state["state_dict"], strict=True)
        self.model.eval().requires_grad_(False)
        self.analyzer_version = analyzer_version

    @torch.inference_mode()
    def scores(self, fragments):
        # возвращает семь оценок каждого фрагмента без обучения
        if not fragments:
            return np.empty((0, len(TEXT_LABELS)), dtype=np.float32)
        scores = []
        for start in range(0, len(fragments), self.batch_size):
            rows = fragments[start:start + self.batch_size]
            strings = [row["target"] for row in rows] + [row["context"] for row in rows]
            batch = self.tokenizer(strings, padding=True, truncation=True, max_length=512, return_tensors="pt")
            scores.append(torch.sigmoid(self.model(batch)).numpy())
        return np.concatenate(scores)


class CNNModel(URLModel):
    def __init__(self, directory, analyzer_version):
        # загружает готовую сеть и действующий справочник организаций
        super().__init__()
        state = torch.load(Path(directory) / "url_cnn/weights.pt", map_location="cpu", weights_only=True)
        self.batch_size = state["metadata"]["batch_size"]
        self.model = CharacterCNN()
        self.model.load_state_dict(state["state_dict"], strict=True)
        self.model.eval().requires_grad_(False)
        catalogue = json.dumps(self.catalogue, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.analyzer_version = analyzer_version + "-" + hashlib.sha256(catalogue).hexdigest()[:12]

    @torch.inference_mode()
    def scores(self, urls):
        # кодирует исходные адреса и возвращает оценки фишинга
        if isinstance(urls, str):
            raise ValueError("Нужен список URL")
        encoded = np.zeros((len(urls), 256), dtype=np.int64)
        for index, url in enumerate(urls):
            values = [ord(char) + 1 if ord(char) < 128 else 129 for char in url.strip()[:256]]
            encoded[index, :len(values)] = values
        if not len(encoded):
            return np.empty(0, dtype=np.float32)
        scores = []
        for start in range(0, len(encoded), self.batch_size):
            batch = torch.from_numpy(encoded[start:start + self.batch_size])
            scores.append(torch.sigmoid(self.model(batch)).numpy())
        return np.concatenate(scores)
