import csv
from io import StringIO
import json
import socket
from zipfile import ZipFile

import numpy as np
import pytest

from analysis.url import URL_FEATURES, URLModel, brand_signals, extract_url
from ml import prepare_urls, train_url


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # запрещает сетевые обращения во время проверки адресов
    def deny(*args, **kwargs):
        # останавливает попытку обращения в сеть
        raise AssertionError("Проверка URL не должна обращаться в сеть")

    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)


@pytest.fixture
def catalogue():
    # задает отдельный справочник для проверки сравнения доменов
    return {
        "version": "test-v1",
        "brands": [{
            "name": "Test Bank",
            "aliases": ["testbank", "test bank"],
            "host_aliases": ["testbank"],
            "domains": ["testbank.com"],
            "sources": ["https://testbank.com/"],
            "checked_at": "2026-11-01",
        }],
    }


def test_url_features_and_rejected_ambiguous_addresses():
    # проверяет настоящий хост и точные значения признаков
    url = "https://user:secret@Example123.COM.:8443/pay%20now?a=&a=2&flag&&#x"
    info = extract_url(f"  {url}  ")
    assert info["url"] == url and info["host"] == "example123.com"
    assert info["domain"] == "example123.com"
    assert URL_FEATURES == (
        "url_length", "host_length", "path_length", "query_length",
        "host_label_count", "host_digit_fraction", "host_hyphen_count",
        "query_parameter_count", "uses_https", "host_is_ip",
        "has_userinfo", "host_has_punycode",
    )
    assert info["features"] == {
        "url_length": len(url), "host_length": len("example123.com"),
        "path_length": len("/pay%20now"), "query_length": len("a=&a=2&flag&&"),
        "host_label_count": 2, "host_digit_fraction": 3 / len("example123.com"),
        "host_hyphen_count": 0, "query_parameter_count": 3, "uses_https": 1,
        "host_is_ip": 0, "has_userinfo": 1, "host_has_punycode": 0,
    }
    ipv6 = extract_url("http://[2001:0db8::1]/")
    assert ipv6["host"] == ipv6["domain"] == "2001:db8::1"
    assert ipv6["features"]["host_is_ip"] == 1
    assert extract_url("http://127.0.0.1/")["features"]["host_is_ip"] == 1
    for invalid in (
        "ftp://example.com", "https://example.com:99999", "https://example.com:abc",
        "https://exa mple.com", "https://example.com/\npath",
        "https://example.com\\@other.com", "http://127.1", "http://2130706433",
        "http://0x7f000001", "https://[bad]/",
        "https://example.com../",
    ):
        with pytest.raises(ValueError):
            extract_url(invalid)


def test_idn_and_domain_groups_use_local_icann_suffixes():
    # проверяет нормализацию и общую группу поддоменов
    unicode_url = extract_url("https://пример.рф/")
    ascii_url = extract_url("https://XN--E1AFMKFD.XN--P1AI./")
    assert unicode_url["host"] == ascii_url["host"] == "xn--e1afmkfd.xn--p1ai"
    assert unicode_url["domain"] == ascii_url["domain"]
    assert unicode_url["features"]["host_has_punycode"] == 1
    for name in URL_FEATURES:
        if name.startswith("host_"):
            assert unicode_url["features"][name] == ascii_url["features"][name]
    assert extract_url("https://one.shop.example.co.uk/")["domain"] == "example.co.uk"
    assert extract_url("https://two.example.co.uk/")["domain"] == "example.co.uk"
    assert extract_url("https://one.github.io/")["domain"] == "github.io"
    assert extract_url("https://two.github.io/")["domain"] == "github.io"
    assert extract_url("https://site.invalid/")["domain"] is None
    assert extract_url("https://example.com。/")["host"] == "example.com"
    assert extract_url("http://１２７。０。０。１/")["features"]["host_is_ip"] == 1


def test_brand_matching_uses_hostname_boundaries_and_keeps_unknown(catalogue):
    # отличает официальный домен от похожего адреса и неизвестного бренда
    for url, mismatch in (
        ("https://testbank.com", 0), ("https://secure.testbank.com", 0),
        ("https://testbank.com.evil.com", 1), ("https://testbank-login.com", 1),
    ):
        result = brand_signals(extract_url(url), catalogue=catalogue)
        assert result == {
            "brand_mismatch": mismatch, "brand_comparison_available": True,
            "brand": "Test Bank",
        }
    for url in (
        "https://notatestbank.com", "https://evil.com/testbank",
        "https://testbank@evil.com", "https://evil.com/?next=testbank.com",
    ):
        result = brand_signals(extract_url(url), catalogue=catalogue)
        assert result["brand_mismatch"] is None
        assert result["brand_comparison_available"] is False
    explicit = brand_signals(extract_url("https://evil.com"),
                             expected_site="  TEST BANK! ", catalogue=catalogue)
    assert explicit["brand_mismatch"] == 1
    unknown = brand_signals(extract_url("https://evil.com"),
                            expected_site="Unknown Bank", catalogue=catalogue)
    assert unknown["brand_mismatch"] is None
    for expected in ("testbank.com", "https://secure.testbank.com/login"):
        assert brand_signals(extract_url("https://evil.com"), expected, catalogue)["brand_mismatch"] == 1
    assert brand_signals(extract_url("https://evil.com"), "evil.com", catalogue)["brand_mismatch"] is None
    assert brand_signals(extract_url("https://testbank.com。"), catalogue=catalogue)["brand_mismatch"] == 0


def test_prediction_keeps_source_of_each_aggregated_signal(monkeypatch, catalogue):
    # проверяет адрес источника для максимума и отдельных признаков
    model = URLModel(catalogue=catalogue)

    def scores(urls):
        # задает разные оценки для проверки объединения
        return np.array([0.2, 0.85, 0.1])

    monkeypatch.setattr(model, "scores", scores)
    urls = ["https://testbank.com", "http://user@testbank-login.com", "http://127.0.0.1"]
    result = model.predict(urls)
    assert result["analyzer_version"]
    assert len(result["urls"]) == 3
    assert result["values"] == {
        "url_score": 0.85, "brand_mismatch": 1, "brand_comparison_available": True,
        "has_userinfo": 1, "host_is_ip": 1,
    }
    for name in ("url_score", "brand_mismatch", "has_userinfo"):
        assert result["source_refs"][name]["url"] == urls[1]
        assert result["source_refs"][name]["host"] == "testbank-login.com"
    assert result["source_refs"]["host_is_ip"]["url"] == urls[2]


def test_native_model_roundtrip_preserves_catalogue_and_rejects_tampering(tmp_path, catalogue):
    # проверяет нативные веса справочник и контроль целостности
    urls = [f"https://shop{i}.example.com/home" if i % 2 == 0
            else f"http://user@verify-{i}.example.net/login?code=&next=1" for i in range(8)]
    model = URLModel(catalogue=catalogue).fit(urls, [i % 2 for i in range(8)])
    path = tmp_path / "url-model"
    model.save(path, training_info={"purpose": "unit test"})
    loaded = URLModel.load(path)
    np.testing.assert_array_equal(model.scores(urls), loaded.scores(urls))
    assert loaded.predict(urls) == model.predict(urls)
    assert loaded.predict(["https://testbank.com"])["values"]["brand_mismatch"] == 0
    weights = list(path.glob("*.cbm"))
    assert len(weights) == 1
    weights[0].write_bytes(weights[0].read_bytes() + b"changed")
    with pytest.raises(ValueError):
        URLModel.load(path)


def test_preparation_inverts_labels_and_keeps_domains_in_one_split(tmp_path):
    # проверяет очистку меток и раздельные домены во всех частях
    rows = []
    index = 0
    for split in prepare_urls.SPLITS:
        for label in (0, 1):
            while True:
                domain = f"sample{index}.com"
                index += 1
                if prepare_urls.split_for_domain(domain) == split:
                    break
            rows.append({"URL": f"https://{domain}/one", "label": str(label), "HTML": "ignored"})
            rows.append({"URL": f"https://sub.{domain}/two", "label": str(label), "HTML": "ignored"})
    rows += [dict(rows[0]), {**rows[0], "URL": "  " + rows[0]["URL"] + "  "},
             {"URL": "ftp://sample.com", "label": "0"},
             {"URL": "https://sample.invalid", "label": "1"}]
    data = StringIO()
    writer = csv.DictWriter(data, fieldnames=["URL", "label", "HTML"])
    writer.writeheader()
    writer.writerows(rows)
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr(prepare_urls.CSV_NAME, data.getvalue())
    output = tmp_path / "data"
    manifest = prepare_urls.prepare_dataset(archive, output)
    assert manifest["cleaning"]["duplicate_url"] == 2
    assert manifest["cleaning"]["invalid_url"] == 1
    assert manifest["cleaning"]["unknown_public_suffix"] == 1
    groups = set()
    original = {row["URL"]: int(row["label"]) for row in rows[:16]}
    for split in prepare_urls.SPLITS:
        urls, labels, current, _ = train_url.read_rows(output / f"{split}.jsonl", split)
        assert not groups.intersection(current)
        groups.update(current)
        assert all(label == 1 - original[url] for url, label in zip(urls, labels))
    train_url.read_provenance(
        output / "source_manifest.json", manifest["splits"]["train"], manifest["splits"]["validation"]
    )


def test_training_rejects_domain_leakage_before_fitting(tmp_path, monkeypatch, capsys):
    # запрещает пересечение поддоменов до обучения классификатора
    paths = []
    for split in ("train", "validation"):
        rows = [{"url": f"https://{split}{i}.example.com/path", "label": i,
                 "group": "example.com", "split": split} for i in (0, 1)]
        path = tmp_path / f"{split}.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        paths.append(path)

    def unexpected_model():
        # запрещает создание модели при неверном разбиении
        raise AssertionError("Классификатор не должен создаваться")

    monkeypatch.setattr(train_url, "URLModel", unexpected_model)
    monkeypatch.setattr("sys.argv", ["train", str(paths[0]), str(tmp_path / "model"),
                                     "--validation", str(paths[1])])
    with pytest.raises(SystemExit) as error:
        train_url.main()
    assert error.value.code == 2
    assert "пересекаются" in capsys.readouterr().err
