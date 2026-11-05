from copy import deepcopy
import json
import shutil

import numpy as np
import pytest

from analysis.runtime import THRESHOLD_NAMES
from analysis.url import URLModel
from ml import update_catalogue


@pytest.fixture
def source_url(tmp_path, monkeypatch):
    # готовит старую модель и новый проверяемый справочник
    catalogue = {"version": "old", "brands": [{
        "name": "Old Bank", "aliases": ["oldbank"], "host_aliases": ["oldbank"],
        "domains": ["oldbank.example.com"], "sources": ["https://oldbank.example.com"],
        "checked_at": "2026-11-01",
    }]}
    updated = deepcopy(catalogue)
    updated["version"] = "new"
    updated["brands"].append({
        "name": "New Bank", "aliases": ["newbank"], "host_aliases": ["newbank"],
        "domains": ["newbank.example.com"], "sources": ["https://newbank.example.com"],
        "checked_at": "2026-11-05",
    })
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(updated))
    monkeypatch.setattr(update_catalogue, "CATALOGUE_PATH", path)
    urls = [f"https://shop{i}.example.com/home" if i % 2 == 0 else
            f"http://user@verify-{i}.example.net/login?code=&next=1" for i in range(8)]
    model = URLModel(catalogue).fit(urls, [i % 2 for i in range(8)])
    source = tmp_path / "source-url"
    model.save(source, training_info={"source": "unit test"})
    return source, urls, model, updated


def test_catalogue_export_preserves_source_weights_and_scores(source_url, tmp_path):
    # проверяет новый справочник без изменения исходника и прогнозов
    source, urls, original, catalogue = source_url
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    destination = tmp_path / "updated-url"
    manifest = update_catalogue.update_catalogue(source, destination)
    updated = URLModel.load(destination)
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before
    assert (destination / "model.cbm").read_bytes() == before["model.cbm"]
    np.testing.assert_array_equal(updated.scores(urls), original.scores(urls))
    assert updated.catalogue == catalogue
    assert updated.analyzer_version != original.analyzer_version
    assert manifest["training"] == {"source": "unit test"}
    assert manifest["catalogue_updates"][-1]["previous_analyzer_version"] == original.analyzer_version
    assert manifest["catalogue_updates"][-1]["analyzer_version"] == updated.analyzer_version
    url = ["https://newbank.example.com"]
    assert original.predict(url, "New Bank")["values"]["brand_mismatch"] is None
    assert updated.predict(url, "New Bank")["values"]["brand_mismatch"] == 0
    with pytest.raises(ValueError, match="существует"):
        update_catalogue.update_catalogue(source, destination)
    with pytest.raises(ValueError, match="вне исходной"):
        update_catalogue.update_catalogue(source, source / "nested")


@pytest.mark.parametrize("legacy", [False, True])
def test_bundle_update_preserves_thresholds_and_rejects_invalid_inputs(source_url, tmp_path, legacy):
    # проверяет версии комплекта пороги и отказ до создания результата
    url_source, urls, original, catalogue = source_url
    source = tmp_path / "bundle"
    source.mkdir()
    shutil.copytree(url_source, source / "url")
    versions = {"url": original.analyzer_version, "text": "text-test"}
    if legacy:
        versions["risk"] = "risk-test"
    for name in versions.keys() - {"url"}:
        (source / name).mkdir()
        (source / name / "manifest.json").write_text(json.dumps({"analyzer_version": versions[name]}))
        (source / name / "weights.bin").write_bytes(name.encode())
    calibration = {"versions": versions, "thresholds": {name: 0.8 for name in THRESHOLD_NAMES},
                   "selection": "original selection", "metrics": {"preserved": True}}
    calibration_path = source / "calibration.json"
    for name in versions:
        stale = deepcopy(calibration)
        stale["versions"][name] = "stale"
        calibration_path.write_text(json.dumps(stale))
        destination = tmp_path / ("stale-" + name)
        with pytest.raises(ValueError, match="других версий"):
            update_catalogue.update_catalogue(source, destination)
        assert not destination.exists()
    for index, thresholds in enumerate(({}, {**calibration["thresholds"], "url_score": float("nan")},
                                        {**calibration["thresholds"], "url_score": 1.1})):
        calibration_path.write_text(json.dumps({**calibration, "thresholds": thresholds}))
        destination = tmp_path / f"thresholds-{index}"
        with pytest.raises(ValueError, match="восемь"):
            update_catalogue.update_catalogue(source, destination)
        assert not destination.exists()
    calibration_path.write_text(json.dumps(calibration))
    before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    destination = tmp_path / "updated-bundle"
    update_catalogue.update_catalogue(source, destination)
    changed = json.loads((destination / "calibration.json").read_text())
    expected = deepcopy(calibration)
    expected["versions"]["url"] = URLModel.load(destination / "url").analyzer_version
    assert changed == expected
    for name in versions.keys() - {"url"}:
        assert (destination / name / "manifest.json").read_bytes() == before[name + "/manifest.json"]
        assert (destination / name / "weights.bin").read_bytes() == name.encode()
    assert (destination / "url/model.cbm").read_bytes() == before["url/model.cbm"]
    assert {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()} == before
    bad = tmp_path / "broken-url"
    shutil.copytree(url_source, bad)
    (bad / "model.cbm").write_bytes(before["url/model.cbm"] + b"changed")
    with pytest.raises(ValueError, match="Несовместимая"):
        update_catalogue.update_catalogue(bad, tmp_path / "broken-output")
    assert not (tmp_path / "broken-output").exists()
