"""Scientific provenance and transport failure boundaries for reference collection."""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "collect_public_data", SCRIPTS / "collect_public_data.py"
)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_raw_file_tampering_is_rejected(tmp_path):
    raw = tmp_path / "study/raw/data.csv"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"value\n1\n")
    receipt = {"url": "https://example.org/data.csv", **collector.digest_file(raw)}
    raw.with_name("data.csv.receipt.json").write_text(json.dumps(receipt))
    raw.write_bytes(b"value\n2\n")
    with pytest.raises(ValueError, match="changed"):
        collector.collect_file(
            "study", {"path": "data.csv", "url": receipt["url"]}, tmp_path, offline=True
        )


def test_cache_without_receipt_is_not_silently_trusted(tmp_path):
    raw = tmp_path / "study/raw/data.csv"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"value\n1\n")
    with pytest.raises(ValueError, match="no receipt"):
        collector.collect_file(
            "study", {"path": "data.csv", "url": "https://example.org"}, tmp_path, offline=True
        )


@pytest.mark.parametrize("relative", ["../outside", "x/../../outside", "C:/outside"])
def test_library_paths_cannot_escape_root(tmp_path, relative):
    with pytest.raises(ValueError, match="escapes"):
        collector.contained_path(tmp_path, relative)


def test_publisher_checksum_failure_keeps_originals_unpublished(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        url = "https://example.org/data.csv"
        headers = {"Content-Length": "8"}

    monkeypatch.setattr(collector, "urlopen", lambda *a, **k: Response(b"value\n1\n"))
    monkeypatch.setattr(collector.time, "sleep", lambda _: None)
    spec = {"path": "data.csv", "url": Response.url, "md5": "0" * 32}
    with pytest.raises(ValueError, match="md5 mismatch"):
        collector.collect_file("study", spec, tmp_path)
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("data.csv"))


def test_download_budget_is_enforced_before_publication(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        url = "https://example.org/data.csv"
        headers = {}

    monkeypatch.setattr(collector, "urlopen", lambda *a, **k: Response(b"too much data"))
    monkeypatch.setattr(collector.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="size budget"):
        collector.collect_file(
            "study", {"path": "data.csv", "url": Response.url, "max_bytes": 3}, tmp_path
        )
    assert not list(tmp_path.rglob("data.csv"))


def test_local_pin_is_not_reported_as_publisher_checksum(tmp_path, monkeypatch):
    import hashlib

    class Response(io.BytesIO):
        url = "https://example.org/data.csv"
        headers = {}

    payload = b"value\n1\n"
    monkeypatch.setattr(collector, "urlopen", lambda *a, **k: Response(payload))
    spec = {"path": "data.csv", "url": Response.url, "sha256": hashlib.sha256(payload).hexdigest()}
    result = collector.collect_file("study", spec, tmp_path)
    assert result["catalog_sha256_verified"] is True
    assert result["publisher_checksum_verified"] is False


@pytest.fixture
def preparation(monkeypatch):
    pytest.importorskip("pyarrow")
    monkeypatch.setitem(sys.modules, "collect_public_data", collector)
    spec = importlib.util.spec_from_file_location(
        "prepare_public_data", SCRIPTS / "prepare_public_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_and_zero_remain_distinct_with_row_lineage(tmp_path, preparation):
    import pyarrow.parquet as pq

    path = tmp_path / "table.parquet"
    profile = preparation.prepare_csv(io.BytesIO(b"id,value\na,0\nb,\n"), path, "study", "raw.csv")
    rows = pq.read_table(path).to_pylist()
    assert [row["value"] for row in rows] == ["0", ""]
    assert [row["_source_row"] for row in rows] == [1, 2]
    assert {row["_source_id"] for row in rows} == {"study"}
    assert profile["rows"] == 2
    assert profile["empty_string_counts"]["value"] == 1


@pytest.mark.parametrize(
    "member", ["../outside.csv", "/outside.csv", "C:/outside.csv", "a\\..\\outside.csv"]
)
def test_archive_member_escape_is_rejected(preparation, member):
    with pytest.raises(ValueError, match="Unsafe archive"):
        preparation.safe_member(member)


def test_ragged_csv_is_not_silently_truncated(tmp_path, preparation):
    with pytest.raises(ValueError, match="Ragged CSV"):
        preparation.prepare_csv(
            io.BytesIO(b"id,value\na,1,extra\n"), tmp_path / "table.parquet", "study", "raw.csv"
        )


def test_lab_labels_and_automatic_predictions_have_distinct_roles(preparation):
    assert (
        preparation.table_role("hihaigua-v2", "x/lab-generated/labels.csv")
        == "manual_controlled_lab_labels"
    )
    assert preparation.table_role(
        "ciws-utah-v2026-11-04", "x/Results/LabelledEvents.csv"
    ).startswith("predicted")
    assert preparation.table_role("ciws-utah-v2026-11-04", "x/TrainingDataset_SS.csv").startswith(
        "publisher_manual"
    )
    assert (
        preparation.table_role("illinois-ctgan-balanced", "events.csv")
        == "synthetic_event_features"
    )


def test_regional_releases_and_grains_remain_separate(preparation):
    snapshots = [
        {"file": "annual-old.xlsx", "sheet": "a", "rows": [["district", 2015], ["ADALAR", 3]]},
        {
            "file": "annual-new.xlsx",
            "sheet": "a",
            "rows": [["district", "2015 (m3)"], ["ADALAR ", 4]],
        },
        {
            "file": "monthly.xlsx",
            "sheet": "m",
            "rows": [
                ["district", 202301, 202302, None],
                ["ADALAR", 0, None, None],
                ["GENEL TOPLAM", 10, 20, 30],
            ],
        },
    ]
    rows = preparation.regional_records(snapshots)
    assert len(rows) == 6
    assert [(r["source_file"], r["volume_m3"]) for r in rows if r["period"] == "2015"] == [
        ("annual-old.xlsx", 3),
        ("annual-new.xlsx", 4),
    ]
    assert [
        r["volume_m3"] for r in rows if r["grain"] == "month" and r["entity_kind"] == "district"
    ] == [0, None]
    assert sum(r["entity_kind"] == "publisher_total" for r in rows) == 2
    assert {r["geography"] for r in rows} == {"Istanbul"}


def test_regional_unknown_period_is_not_invented(preparation):
    with pytest.raises(ValueError, match="Unrecognized regional period"):
        preparation.regional_records(
            [{"file": "a.xlsx", "sheet": "a", "rows": [["district", "unknown"], ["ADALAR", 4]]}]
        )


def test_external_reporting_area_is_not_relabelled_as_istanbul_district(preparation):
    rows = preparation.regional_records(
        [
            {
                "file": "old.xlsx",
                "sheet": "data",
                "rows": [["district", 2015], ["KOCAELİ", 4], ["GEBZE-İSKİ", 2]],
            }
        ]
    )
    assert {row["entity_kind"] for row in rows} == {"external_reporting_area"}
    assert {row["geography"] for row in rows} == {"unresolved_external_reporting_area"}


def test_counter_quality_does_not_bridge_missing_values_or_reverse_time(preparation, monkeypatch):
    import numpy as np

    spec = importlib.util.spec_from_file_location(
        "audit_public_data", SCRIPTS / "audit_public_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.counter_quality(
        np.array([0, 420_000, 840_000, 1_260_000, 1_680_000, 1_600_000]),
        np.array([10.0, np.nan, 4.0, 6.0, 2.0, 1.0]),
    )
    assert result["counter_decreases"] == 1
    assert result["nonfinite_or_missing_values"] == 1
    assert result["nonincreasing_timestamps"] == 1
    assert result["valid_adjacent_pairs"] == 2


def test_public_field_inventory_matches_planning_fields():
    root = SCRIPTS.parent
    planning = json.loads(
        (root / "docs/contracts/model-flow-map.v0.1.json").read_text(encoding="utf-8")
    )
    coverage = json.loads(
        (root / "research/reference_data/public-data-field-coverage.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(coverage["fields"]) == set(planning["fields"])
    assert coverage["field_count"] == 51
    assert coverage["fields"]["G01"]["status"] == "unavailable"
    assert coverage["fields"]["curve"]["status"] == "unavailable"
    assert coverage["fields"]["weather_issued"]["status"] == "unavailable"
