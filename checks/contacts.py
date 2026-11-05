import json
from pathlib import Path

from analysis.url import brand_signals, extract_url


CONTACTS_PATH = Path(__file__).resolve().parent.parent / "analysis/bank_contacts.json"


def get_bank_contact(data):
    # находит контакт банка только по указанной организации
    if not data.get("url") or not data.get("expected_site"):
        return None
    try:
        brand = brand_signals(extract_url(data["url"]), data["expected_site"])["brand"]
    except ValueError:
        return None
    return json.loads(CONTACTS_PATH.read_text(encoding="utf-8")).get(brand)
