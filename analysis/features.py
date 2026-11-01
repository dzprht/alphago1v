from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Turn:
    turn_id: str
    text: str
    speaker: str | None = None
    owner: str = "unknown"
    source_span: dict | None = None

    def __post_init__(self):
        # проверяет поля исходной реплики
        if not isinstance(self.turn_id, str) or not self.turn_id.strip():
            raise ValueError("Нужен непустой номер реплики")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Реплика должна содержать текст")
        if self.owner not in ("user", "other", "unknown"):
            raise ValueError("Неизвестная роль владельца реплики")
        if self.speaker is not None and (
            not isinstance(self.speaker, str) or not self.speaker.strip() or self.speaker == "unknown"
        ):
            raise ValueError("Нужен идентификатор говорящего или None при неизвестности")
        if self.source_span is not None and not isinstance(self.source_span, dict):
            raise ValueError("Место в источнике должно быть словарем")


def feature_vector(values, names):
    # собирает значения и маски для итоговой модели
    selected = [values.get(name) for name in names]
    if any(value is not None and not isfinite(value) for value in selected):
        raise ValueError("Признаки должны быть конечными числами или None")
    return [0.0 if value is None else float(value) for value in selected] + [
        int(value is not None) for value in selected
    ]
