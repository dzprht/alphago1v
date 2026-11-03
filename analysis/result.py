import hashlib
import json

from analysis.risk import RISK_FEATURES, RISK_SCHEMA_VERSION, TEXT_FEATURES, URL_FEATURES


FINDINGS = {
    "text_urgency": ("Возможное давление срочностью", "Возьмите паузу и проверьте просьбу без участия собеседника"),
    "text_money_request": ("Возможная просьба передать деньги", "Проверьте получателя и основание платежа"),
    "text_credentials_request": ("Возможная просьба передать секретные данные", "Не передавайте собеседнику пароли и коды подтверждения"),
    "text_secrecy": ("Возможная просьба сохранить разговор в тайне", "Обсудите ситуацию с человеком, которому доверяете"),
    "text_remote_access": ("Возможная просьба предоставить удаленный доступ", "Не предоставляйте доступ к устройству по просьбе незнакомого собеседника"),
    "text_authority_claim": ("Собеседник, возможно, представляется сотрудником организации", "Свяжитесь с организацией по самостоятельно найденному контакту"),
    "text_protective_transfer": ("Возможная просьба перевести деньги для их защиты", "Проверьте эту просьбу через официальный контакт банка"),
    "url_score": ("Возможные признаки фишингового адреса по оценке модели", "Найдите нужный сайт самостоятельно через известный официальный адрес"),
    "brand_mismatch": ("Домен отсутствует в нашем подтвержденном списке сайтов организации", "Уточните адрес сайта через независимый источник"),
    "has_userinfo": ("В адресе есть часть перед знаком @, которая не является доменом сайта", "Проверьте настоящий домен после знака @"),
    "host_is_ip": ("Вместо доменного имени указан IP адрес", "Перед переходом проверьте, кому принадлежит этот адрес"),
    "urgency=yes": ("Вы указали, что вас торопят", "Возьмите паузу и проверьте просьбу без участия собеседника"),
    "secrecy=yes": ("Вы указали, что перевод просят сохранить в тайне", "Обсудите ситуацию с человеком, которому доверяете"),
    "changed_details=yes": ("Вы указали, что реквизиты менялись", "Подтвердите реквизиты через независимый контакт получателя"),
}


def assemble_result(rows, decisions, *, thresholds, versions):
    # собирает общий ответ из готовых прогнозов и их источников
    if len(rows) != len(decisions):
        raise ValueError("Для каждой строки нужен один результат дерева")
    if any(value is not None and not 0 <= value <= 1 for value in thresholds.values()):
        raise ValueError("Пороги должны быть от нуля до единицы")
    findings, recommendations, limitations, paths, seen = [], [], [], [], set()
    statuses = set()
    for index, (row, decision) in enumerate(zip(rows, decisions)):
        values = dict(zip(RISK_FEATURES, row["vector"]))
        sources = row.get("source_refs", {})
        for status in ("ok", "partial", "empty", "error"):
            if values["extraction_status=" + status]:
                statuses.add(status)
        paths.append({"target_index": index, "conditions": decision["conditions"]})
        for name in TEXT_FEATURES + URL_FEATURES + ("urgency=yes", "secrecy=yes", "changed_details=yes"):
            key = name.replace("previous_", "text_", 1)
            source = sources.get(name)
            if "=" in name:
                if not values["mode=transfer"] or not values[name]:
                    continue
                value, origin, source = 1, "questionnaire", {"field": name.split("=", 1)[0]}
            else:
                if not values[name + "_available"]:
                    continue
                value = values[name]
                origin = "model" if key.startswith("text_") or key == "url_score" else "url_structure"
                if origin == "model":
                    threshold = thresholds.get(key)
                    if threshold is None:
                        limitations.append("Для части признаков еще не определены пороги показа")
                        continue
                    if value < threshold:
                        continue
                elif not value:
                    continue
            if source is None:
                limitations.append("Для части признаков недоступно место в исходном материале")
                continue
            identity = (key, json.dumps(source, ensure_ascii=False, sort_keys=True))
            if identity in seen:
                continue
            seen.add(identity)
            label, recommendation = FINDINGS[key]
            findings.append({"feature": key, "description": label, "origin": origin, "source_ref": source})
            recommendations.append(recommendation)
        if values["url_score_available"]:
            limitations.append("Сайт не открывался: его содержимое и перенаправления не проверены")
        if values["mode=transfer"]:
            if not values["history_loaded_available"]:
                limitations.append("Сравнение с историей операций недоступно")
            elif not values["history_loaded"]:
                limitations.append("История операций не загружена: персональное сравнение недоступно")
            elif not values["history_valid"]:
                limitations.append("В истории есть будущие даты: персональные признаки не использованы")
            elif not values["amount_percentile_available"]:
                limitations.append("Недостаточно исходящих операций для сравнения суммы")
            if not values["recipient_share_available"]:
                limitations.append("По этой истории нельзя надежно судить о новизне получателя")
        if values["mode=audio"] or values["mode=screenshot"]:
            for name, message in (
                ("order_known", "Порядок реплик определен не полностью"),
                ("speaker_known", "Не для всех реплик удалось определить участника"),
                ("owner_known", "Не установлено, какие реплики принадлежат пользователю"),
            ):
                if not values[name]:
                    limitations.append(message)
    known = [decision["risk"] for decision in decisions if decision["risk"] is not None]
    risk = max(known) if known else None
    missing = len(known) != len(decisions)
    sufficiency = "insufficient" if not known else "limited" if missing or any(
        decision["sufficiency"] != "sufficient" for decision in decisions
    ) else "sufficient"
    if missing:
        limitations.append("Для части материала недостаточно данных для оценки")
        if risk == 0:
            risk = None
    status = "error" if "error" in statuses else "empty" if not rows or "empty" in statuses else "partial" if "partial" in statuses else "ok"
    if status in ("error", "empty", "partial"):
        risk = None
        sufficiency = "limited" if status == "partial" and (known or any(
            decision["sufficiency"] == "limited" for decision in decisions
        )) else "insufficient"
        limitations.append({
            "error": "Не удалось распознать материал: общий риск не определен",
            "empty": "Нет пригодного материала для общей оценки",
            "partial": "Материал распознан частично: общий риск не определен",
        }[status])
    recommendations.insert(0, "Проверьте просьбу через независимый контакт до выполнения действия")
    limitations.append("Отсутствие найденных признаков не гарантирует безопасность")
    components = {"blocks": versions, "risk": sorted({decision["analyzer_version"] for decision in decisions})}
    identity = json.dumps({"components": components, "thresholds": thresholds,
                           "schema": RISK_SCHEMA_VERSION, "policy": "result-v1"}, sort_keys=True, allow_nan=False)
    return {
        "status": status, "risk": risk, "sufficiency": sufficiency, "findings": findings,
        "recommendations": list(dict.fromkeys(recommendations)), "limitations": list(dict.fromkeys(limitations)),
        "decision_paths": paths, "analyzer_version": "analysis-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
        "components": components,
    }
