import json
import re
from copy import deepcopy

from django.urls import reverse

from analysis.url import CATALOGUE_PATH, brand_signals, extract_url, normalize_host


def test_catalogue_names_aliases_and_all_domains_are_consistent():
    # проверяет справочник и распознавание всех названий и адресов
    catalogue = json.loads(CATALOGUE_PATH.read_text())
    assert len(catalogue["brands"]) >= 40
    aliases = {}
    for brand in catalogue["brands"]:
        assert brand["domains"] and brand["sources"] and brand["checked_at"]
        assert all(source.startswith("https://") for source in brand["sources"])
        for name in [brand["name"], *brand["aliases"]]:
            key = re.sub(r"[\W_]+", "", name.casefold())
            assert key not in aliases or aliases[key] == brand["name"]
            aliases[key] = brand["name"]
            result = brand_signals(extract_url("https://foreign.example"), name, catalogue)
            assert result["brand"] == brand["name"] and result["brand_mismatch"] == 1
        for domain in brand["domains"]:
            host, is_ip = normalize_host(domain)
            assert not is_ip and not any(char in domain for char in "/:@")
            for candidate, mismatch in ((host, 0), ("login." + host, 0), (host + ".foreign.example", 1)):
                result = brand_signals(extract_url("https://" + candidate), brand["name"], catalogue)
                assert result["brand"] == brand["name"] and result["brand_mismatch"] == mismatch


def test_more_specific_domain_wins_but_ambiguous_name_stays_unknown():
    # различает общий сервис и его отдельный дочерний домен
    catalogue = {"matching_policy": "specific-domain-v2", "brands": [
        {"name": "Portal", "aliases": ["shared"], "host_aliases": ["portal"], "domains": ["portal.example"]},
        {"name": "Portal Bank", "aliases": ["shared"], "host_aliases": ["portal-bank"],
         "domains": ["bank.portal.example"]},
    ]}
    source = deepcopy(catalogue)
    for expected in ("bank.portal.example", "https://bank.portal.example/account"):
        result = brand_signals(extract_url("https://foreign.example"), expected, catalogue)
        assert result["brand"] == "Portal Bank" and result["brand_mismatch"] == 1
    result = brand_signals(extract_url("https://bank.portal.example"), catalogue=catalogue)
    assert result["brand"] == "Portal Bank" and result["brand_mismatch"] == 0
    result = brand_signals(extract_url("https://portal-bank.foreign.example"), catalogue=catalogue)
    assert result["brand"] == "Portal Bank" and result["brand_mismatch"] == 1
    result = brand_signals(extract_url("https://foreign.example"), "shared", catalogue)
    assert result["brand_mismatch"] is None
    assert catalogue == source


def test_old_embedded_catalogue_keeps_its_original_matching_policy():
    # сохраняет прежнее поведение старых сохраненных моделей
    catalogue = {"brands": [
        {"name": "Portal", "aliases": [], "host_aliases": ["portal"], "domains": ["portal.example"]},
        {"name": "Portal Bank", "aliases": [], "host_aliases": ["portal-bank"],
         "domains": ["bank.portal.example"]},
    ]}
    result = brand_signals(extract_url("https://foreign.example"), "bank.portal.example", catalogue)
    assert result["brand_mismatch"] is None
    result = brand_signals(extract_url("https://portal-bank.foreign.example"), catalogue=catalogue)
    assert result["brand_mismatch"] is None


def test_url_form_suggests_supported_organizations_without_requiring_a_choice(client):
    # показывает подсказки справочника и оставляет свободный ввод
    response = client.get(reverse("checks:new", args=["url"]))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'list="expected-organizations"' in html
    assert '<datalist id="expected-organizations">' in html
    for name in ("Т-Банк", "Госуслуги", "Авито"):
        assert f'value="{name}"' in html
