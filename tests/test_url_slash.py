import numpy as np
import pytest


def test_url_model_removes_path_slash_and_preserves_query():
    # убирает последний слеш пути и сохраняет остальные части адреса
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from analysis.selected import CNNModel

    batches = []

    def capture(batch):
        # сохраняет вход сети для проверки
        batches.extend(batch.tolist())
        return torch.zeros(len(batch))

    model = CNNModel.__new__(CNNModel)
    model.batch_size = 32
    model.model = capture
    urls = ["https://sberbank.ru/", "https://sberbank.ru/ru/person/",
            "https://sberbank.ru/ru/person", "https://sberbank.ru/?next=/path/#section/"]
    model.scores(urls)
    decoded = ["".join(chr(value - 1) for value in row if value) for row in batches]
    assert decoded == ["https://sberbank.ru", "https://sberbank.ru/ru/person",
                       "https://sberbank.ru/ru/person", "https://sberbank.ru?next=/path/#section/"]


def test_url_result_keeps_original_address():
    # оставляет исходную ссылку в результате проверки
    from analysis.url import URLModel

    address = "https://www.sberbank.ru/ru/person/"
    model = URLModel()
    model.scores = lambda urls: np.array([0.01])
    result = model.predict([address])
    assert result["urls"][0]["url"] == address
    assert result["source_refs"]["url_score"]["url"] == address
