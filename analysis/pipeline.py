import re

from analysis.features import Turn
from analysis.result import assemble_result
from analysis.risk import risk_features
from analysis.transfer import transfer_features
from analysis.url import extract_url


PIPELINE_VERSION = "pipeline-v1"


def find_urls(text):
    # находит полные ссылки без открытия сайтов
    urls, invalid = [], False
    for value in re.findall(r"https?://[^\s<>\"'«»“”]+", text, flags=re.IGNORECASE):
        if any(value.count(right) > value.count(left) for left, right in (("(", ")"), ("[", "]"), ("{", "}"))):
            invalid = True
            continue
        try:
            urls.append(extract_url(value)["url"])
        except ValueError:
            invalid = True
    return list(dict.fromkeys(urls)), invalid


class Analyzer:
    def __init__(self, text_model, url_model, risk_model, thresholds):
        # сохраняет готовые модели и пороги показа признаков
        self.text_model = text_model
        self.url_model = url_model
        self.risk_model = risk_model
        self.thresholds = dict(thresholds)

    def analyze(self, kind, data, history=None, *, checked_at, turns=None,
                order_known=True, extraction_status="unknown"):
        # запускает блоки анализа и собирает ответ одной проверки
        if kind not in ("text", "url", "transfer", "audio", "screenshot"):
            raise ValueError("Неизвестный вид проверки")
        if turns is None:
            if kind in ("audio", "screenshot"):
                raise ValueError("Сначала нужно распознать медиа")
            field = "text" if kind == "text" else "message"
            value = data.get(field, "")
            turns = [Turn("input", value, source_span={"field": field})] if value else []
        if kind in ("url", "transfer") and len(turns) > 1:
            raise ValueError("Для ссылки и перевода нужна одна исходная просьба")
        background = "" if kind == "transfer" else data.get("context", "")
        text_result = self.text_model.predict(turns, background=background, order_known=order_known) if turns else None
        transfer = transfer_features(data["amount_kopecks"], data.get("recipient", ""), history,
                                     checked_at=checked_at) if kind == "transfer" else None
        versions, snapshot = {"pipeline": PIPELINE_VERSION}, {}
        if text_result is not None:
            versions["text"] = text_result["analyzer_version"]
        if transfer is not None:
            versions["transfer"] = transfer["analyzer_version"]
            if history is not None:
                snapshot = {"checked_at": checked_at.isoformat(), "transfer": transfer["values"]}
        rows, decisions, url_history, invalid_urls = [], [], {}, False
        targets = text_result["turns"] if text_result is not None else [None]
        for index, target in enumerate(targets):
            urls, invalid = find_urls(turns[index].text) if target is not None else ([], False)
            invalid_urls = invalid_urls or invalid
            if kind == "url":
                urls.insert(0, data["url"])
            if target is not None and order_known and target["speaker"] is not None:
                urls = url_history.get(target["speaker"], []) + urls
                url_history[target["speaker"]] = list(dict.fromkeys(urls))
            urls = list(dict.fromkeys(urls))
            url = self.url_model.predict(urls, expected_site=data.get("expected_site", "")) if urls else None
            if url is not None:
                versions["url"] = url["analyzer_version"]
            row = risk_features(kind, text=target, url=url, transfer=transfer, fields=data,
                                extraction_status=extraction_status, order_known=order_known)
            rows.append(row)
            decisions.append(self.risk_model.predict(row))
        result = assemble_result(rows, decisions, thresholds=self.thresholds, versions=versions)
        if invalid_urls:
            result["limitations"].append("Часть ссылок в исходном тексте не удалось корректно разобрать")
            if result["sufficiency"] == "sufficient":
                result["sufficiency"] = "limited"
            if result["risk"] == 0:
                result["risk"] = None
        return result, snapshot
