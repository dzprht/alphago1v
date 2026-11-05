from datetime import timezone
from decimal import Decimal
from math import log, log1p
from statistics import median


TRANSFER_FEATURES = (
    "amount_log", "outgoing_count_log", "history_days", "last_outgoing_days",
    "amount_percentile", "amount_median_log_ratio", "recipient_coverage",
    "recipient_count_log", "recipient_share", "last_recipient_days",
)
MIN_HISTORY = 20
TRANSFER_VERSION = "transfer-v2"


def transfer_features(amount_kopecks, recipient="", history=None, *, checked_at):
    # считает статистику истории и флаги перевода
    if checked_at.utcoffset() is None:
        raise ValueError("Момент проверки должен содержать часовой пояс")
    checked_at = checked_at.astimezone(timezone.utc)
    recipient = recipient.strip()
    values = dict.fromkeys(TRANSFER_FEATURES)
    values.update(
        amount_log=log1p(Decimal(amount_kopecks) / 100),
        history_loaded=int(history is not None),
        history_valid=None if history is None else int(
            all(row["occurred_at"] <= checked_at for row in history)
        ),
        recipient_provided=int(bool(recipient)),
        amount_unusual=None, amount_modified_z=None, recipient_unseen=None,
    )
    if values["history_valid"]:
        outgoing = [row for row in history if row["direction"] == "outgoing"]
        known = [row for row in outgoing if row["recipient"]]
        n = len(outgoing)
        values["outgoing_count_log"] = log1p(n)
        if n:
            dates = [row["occurred_at"] for row in outgoing]
            values["history_days"] = (max(dates) - min(dates)).total_seconds() / 86400
            values["last_outgoing_days"] = (checked_at - max(dates)).total_seconds() / 86400
            values["recipient_coverage"] = len(known) / n
        if n >= MIN_HISTORY:
            amounts = [row["amount_kopecks"] for row in outgoing]
            middle = median([Decimal(amount) for amount in amounts])
            values["amount_percentile"] = sum(amount <= amount_kopecks for amount in amounts) / n
            values["amount_median_log_ratio"] = log(Decimal(amount_kopecks) / middle)
            mad = median([abs(Decimal(amount) - middle) for amount in amounts])
            if mad:
                score = Decimal("0.6745") * (Decimal(amount_kopecks) - middle) / mad
                values["amount_modified_z"] = float(score)
                values["amount_unusual"] = int(score > Decimal("3.5"))
        if recipient:
            matches = [row for row in known if row["recipient"] == recipient]
            values["recipient_count_log"] = log1p(len(matches))
            if known:
                values["recipient_share"] = len(matches) / len(known)
                values["recipient_unseen"] = int(not matches)
            if matches:
                last = max(row["occurred_at"] for row in matches)
                values["last_recipient_days"] = (checked_at - last).total_seconds() / 86400
    refs = {}
    if values["amount_unusual"] is not None:
        refs["amount_unusual"] = {"field": "amount", "statistic": "modified_z", "z": values["amount_modified_z"]}
    if values["recipient_unseen"] is not None:
        refs["recipient_unseen"] = {"field": "recipient", "statistic": "history_matches"}
    return {"analyzer_version": TRANSFER_VERSION, "values": values, "source_refs": refs}
