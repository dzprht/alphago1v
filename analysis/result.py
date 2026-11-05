import hashlib
import json

from analysis.risk import RISK_FEATURES, RISK_SCHEMA_VERSION, TEXT_FEATURES, URL_FEATURES
from analysis.text import TEXT_LABELS


POLICY_VERSION = "warning-rules-v1"
QUESTIONNAIRE_CUES = {
    "urgency=yes": ("text_urgency",),
    "secrecy=yes": ("text_secrecy",),
    "requester=claimed_organization": ("text_authority_claim", "text_money_request"),
    "requester=acquaintance": ("text_money_request",),
    "requester=seller": ("text_money_request",),
    "requester=other": ("text_money_request",),
}
FLAG_FEATURES = dict(zip("UMCSRAP", ("text_" + label for label in TEXT_LABELS)))
FLAG_FEATURES.update(F="url_score", H="amount_unusual", N="recipient_unseen", D="changed_details=yes")
WARNING_REASONS = {
    "C": "Есть просьба раскрыть секрет или использовать его по чужой инструкции",
    "P": "Предложен перевод для защиты или разблокировки денег",
    "R": "Есть просьба установить программу или предоставить доступ",
    "F": "Модель обнаружила признаки фишингового адреса",
    "M+U": "Денежная просьба сопровождается срочностью",
    "M+S": "Денежная просьба сопровождается требованием секретности",
    "M+A": "Деньги просит человек который представился сотрудником организации",
    "D+H": "Реквизиты менялись и сумма нетипично велика для вашей истории",
    "D+N": "Реквизиты менялись и получатель не найден в загруженной истории",
}
FINDINGS = {
    "text_urgency": ("Возможное давление срочностью", "Возьмите паузу и проверьте просьбу без участия собеседника"),
    "text_money_request": ("Просьба передать деньги", "Проверьте получателя и основание платежа"),
    "text_credentials_request": ("Возможная просьба передать секретные данные", "Не передавайте собеседнику пароли и коды подтверждения"),
    "text_secrecy": ("Возможная просьба сохранить разговор в тайне", "Обсудите ситуацию с человеком, которому доверяете"),
    "text_remote_access": ("Возможная просьба установить программу или предоставить доступ", "Проверьте источник программы и запрошенные разрешения"),
    "text_authority_claim": ("Собеседник, возможно, представляется сотрудником организации", "Свяжитесь с организацией по самостоятельно найденному контакту"),
    "text_protective_transfer": ("Возможная просьба перевести деньги для их защиты", "Проверьте эту просьбу через официальный контакт банка"),
    "url_score": ("Возможные признаки фишингового адреса по оценке модели", "Найдите нужный сайт самостоятельно через известный официальный адрес"),
    "brand_mismatch": ("Домен отсутствует в нашем подтвержденном списке сайтов организации", "Уточните адрес сайта через независимый источник"),
    "has_userinfo": ("Часть адреса перед знаком @ не является доменом сайта", "Проверьте настоящий домен после знака @"),
    "host_is_ip": ("Вместо доменного имени указан IP адрес", "Перед переходом проверьте, кому принадлежит этот адрес"),
    "host_has_punycode": ("В адресе используется международное имя домена в записи punycode", "Проверьте написание настоящего домена сайта"),
    "changed_details=yes": ("Вы указали, что реквизиты менялись", "Подтвердите реквизиты через независимый контакт получателя"),
    "amount_unusual": ("Сумма нетипично велика для загруженной истории", "Проверьте сумму и основание платежа"),
    "recipient_unseen": ("Получатель не найден в загруженной истории", "Независимо подтвердите обозначение и реквизиты получателя"),
}


def combine_text_signals(values, thresholds):
    # объединяет оценки модели с положительными ответами анкеты
    reported = set()
    if values["mode=transfer"]:
        for field, features in QUESTIONNAIRE_CUES.items():
            if values[field]:
                reported.update(features)
    signals = {}
    for label in TEXT_LABELS:
        key = "text_" + label
        scores = [values[name] for name in (key, "previous_" + label) if values[name + "_available"]]
        model_score = max(scores, default=None)
        user_reported = key in reported
        threshold = thresholds.get(key)
        signals[key] = {
            "score": max(scores + ([1.0] if user_reported else []), default=None),
            "model_score": model_score, "user_reported": user_reported,
            "detected": user_reported or (model_score is not None and threshold is not None
                                          and model_score >= threshold),
        }
    return signals


def warning_rules(flags):
    # находит согласованные сочетания внутри одной просьбы
    rules = [name for name in ("C", "P", "R", "F") if flags[name] == 1]
    if flags["M"] == 1:
        rules += ["M+" + name for name in ("U", "S", "A") if flags[name] == 1]
    if flags["D"] == 1:
        rules += ["D+" + name for name in ("H", "N") if flags[name] == 1]
    return rules


def assemble_result(rows, *, thresholds, versions):
    # собирает предупреждения по правилам и сохраняет их основания
    if any(value is not None and (type(value) not in (int, float) or not 0 <= value <= 1)
           for value in thresholds.values()):
        raise ValueError("Пороги должны быть от нуля до единицы")
    findings, recommendations, limitations, combined_signals, seen = [], [], [], [], set()
    statuses, coverage = set(), []
    limited = False
    for index, row in enumerate(rows):
        values = dict(zip(RISK_FEATURES, row["vector"]))
        sources = row.get("source_refs", {})
        signals = combine_text_signals(values, thresholds)
        transfer = row.get("transfer") or {}
        history = transfer.get("values", {})
        flags = {
            code: int(signals[feature]["detected"]) if signals[feature]["user_reported"] or (
                signals[feature]["model_score"] is not None and thresholds.get(feature) is not None
            ) else None for code, feature in FLAG_FEATURES.items() if feature in signals
        }
        flags.update(
            F=int(values["url_score"] >= thresholds["url_score"]) if values["url_score_available"]
            and thresholds.get("url_score") is not None else None,
            H=history.get("amount_unusual"), N=history.get("recipient_unseen"),
            D=int(values["changed_details=yes"]) if values["mode=transfer"]
            and not values["changed_details=unknown"] else None,
        )
        rules = warning_rules(flags)
        combined_signals.append({"target_index": index, "values": signals, "flags": flags, "rules": rules})
        banner_features = {FLAG_FEATURES[code] for rule in rules for code in rule.split("+")}
        coverage.append(any(value is not None for value in flags.values()))
        for status in ("ok", "partial", "empty", "error"):
            if values["extraction_status=" + status]:
                statuses.add(status)
        candidates = []
        for name in TEXT_FEATURES + URL_FEATURES:
            if not values[name + "_available"]:
                continue
            key = name.replace("previous_", "text_", 1)
            value = values[name]
            origin = "model" if key.startswith("text_") or key == "url_score" else "url_structure"
            if origin == "model":
                threshold = thresholds.get(key)
                if threshold is None:
                    limitations.append("Для части признаков еще не определены пороги показа")
                    limited = True
                    continue
                if value < threshold:
                    continue
            elif not value:
                continue
            candidates.append((key, value, origin, sources.get(name)))
        if values["mode=transfer"]:
            for field, features in {**QUESTIONNAIRE_CUES, "changed_details=yes": ("changed_details=yes",)}.items():
                if values[field]:
                    candidates.extend((feature, 1, "questionnaire", {"field": field.split("=", 1)[0]})
                                      for feature in features)
            for name in ("amount_unusual", "recipient_unseen"):
                if history.get(name) == 1:
                    candidates.append((name, 1, "history", transfer.get("source_refs", {}).get(name)))
        for url in (row.get("url") or {}).get("urls", []):
            if url["features"]["host_has_punycode"]:
                candidates.append(("host_has_punycode", 1, "url_structure", {
                    "url": url["url"], "host": url["host"],
                }))
        for key, value, origin, source in candidates:
            if source is None:
                limitations.append("Для части признаков недоступно место в исходном материале")
                limited = True
                continue
            group = None
            if key.startswith("text_"):
                group = "transfer" if values["mode=transfer"] else row.get("speaker") or f"target-{index}"
            identity = (key, group, json.dumps(source, ensure_ascii=False, sort_keys=True))
            severity = "banner" if key in banner_features else "remark"
            existing = next((item for item in findings if item["feature"] == key
                             and item["speaker_group"] == group), None)
            if existing is not None and severity == "banner":
                existing["severity"] = "banner"
            if existing is not None and key in signals:
                for name in ("score", "model_score"):
                    existing[name] = max((value for value in (existing[name], signals[key][name])
                                          if value is not None), default=None)
            if identity in seen:
                continue
            seen.add(identity)
            description, recommendation = FINDINGS[key]
            evidence = {"origin": origin, "source_ref": source, "value": value}
            if existing is not None:
                existing["evidence"].append(evidence)
                existing["origins"] = list(dict.fromkeys(existing["origins"] + [origin]))
                if len(existing["origins"]) > 1:
                    existing["origin"] = "combined"
            else:
                finding = {"feature": key, "description": description, "origin": origin, "source_ref": source,
                           "origins": [origin], "evidence": [evidence], "severity": severity,
                           "speaker_group": group}
                if key in signals:
                    finding.update(score=signals[key]["score"], model_score=signals[key]["model_score"])
                findings.append(finding)
            recommendations.append(recommendation)
        if values["url_score_available"]:
            limitations.append("Сайт не открывался: его содержимое и перенаправления не проверены")
        if values["mode=transfer"]:
            if not values["history_loaded_available"] or not values["history_loaded"]:
                limitations.append("История операций не загружена: персональное сравнение недоступно")
                limited = True
            elif not values["history_valid"]:
                limitations.append("В истории есть будущие даты: персональные признаки не использованы")
                limited = True
            else:
                if history.get("amount_unusual") is None:
                    limitations.append("Для сравнения суммы нужны минимум 20 исходящих операций и ненулевой MAD")
                    limited = True
                if history.get("recipient_unseen") is None:
                    limitations.append("Получатель не указан или в истории нет обозначенных получателей")
                    limited = True
        if values["mode=audio"] or values["mode=screenshot"]:
            for name, message in (
                ("order_known", "Порядок реплик определен не полностью"),
                ("speaker_known", "Не для всех реплик удалось определить участника"),
                ("owner_known", "Не установлено, какие реплики принадлежат пользователю"),
            ):
                if not values[name]:
                    limitations.append(message)
                    limited = True
    status = "error" if "error" in statuses else "empty" if not rows or "empty" in statuses else "partial" if "partial" in statuses else "ok"
    sufficiency = "insufficient" if not any(coverage) else "limited" if limited or not all(coverage) else "sufficient"
    if status in ("error", "empty", "partial"):
        sufficiency = "limited" if status == "partial" and any(coverage) else "insufficient"
        limitations.append({"error": "Не удалось распознать материал полностью",
                            "empty": "Нет пригодного материала для общей оценки",
                            "partial": "Материал распознан частично: показаны только найденные основания"}[status])
    recommendations.insert(0, "Проверьте просьбу через независимый контакт до выполнения действия")
    limitations.append("Отсутствие найденных признаков не гарантирует безопасность")
    components = {"blocks": versions, "policy": POLICY_VERSION}
    identity = json.dumps({"components": components, "thresholds": thresholds,
                           "schema": RISK_SCHEMA_VERSION}, sort_keys=True, allow_nan=False)
    return {
        "policy": POLICY_VERSION, "status": status, "risk": None, "sufficiency": sufficiency,
        "warning": any(item["rules"] for item in combined_signals), "findings": findings,
        "warning_reasons": list(dict.fromkeys(WARNING_REASONS[rule]
                                             for item in combined_signals for rule in item["rules"])),
        "combined_signals": combined_signals,
        "recommendations": list(dict.fromkeys(recommendations)), "limitations": list(dict.fromkeys(limitations)),
        "analyzer_version": "analysis-" + hashlib.sha256(identity.encode()).hexdigest()[:16], "components": components,
    }
