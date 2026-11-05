"""Synthetic native-format fixtures prove mechanics; public unit evidence stays pending."""

import csv
import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import urban_water.sources.hihaigua_reference as reference
from urban_water.datasets import freeze, verify_dataset
from urban_water.db import connect, migrate
from urban_water.ingestion import work_once
from urban_water.processing import summarize
from urban_water.settings import Settings
from urban_water.sources import collect
from urban_water.sources.config import load_source
from urban_water.sources.normalizers import get_normalizer
from urban_water.sources.registration import register_file

RECEIPT = datetime(2026, 11, 4, tzinfo=UTC)
START = datetime(2025, 1, 1, tzinfo=UTC)


def native(value, minute=0, entity="water0", **changes):
    instant = START + timedelta(minutes=minute)
    raw = {
        "entity_id": entity,
        "key": "volume",
        "ts": str((instant - reference.EPOCH) // timedelta(milliseconds=1)),
        "ts_dt": instant.replace(tzinfo=None).isoformat(sep=" "),
        "value": value,
    }
    return {**raw, **changes}


def parsed(value, minute=0, prior=None, unit=None, **changes):
    return reference.prepare_row(
        native(value, minute, **changes),
        home="0",
        device={"asset": "site0"},
        component="water",
        member="installation_0/ts.csv",
        source_row=minute + 1,
        receipt=RECEIPT,
        unit=unit,
        prior=prior,
        gap_ms=630000,
    )


def csv_bytes(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode()


@pytest.fixture
def native_cache(tmp_path):
    import zipfile

    library = tmp_path / "raw"
    library.mkdir()
    archive = library / "hihAigua_dataset.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for home in ["0", "1"]:
            prefix = "hihAigua_dataset/installation_" + home
            devices = [
                [kind + home, home, "site0", kind]
                for kind in ("water", "hotWater", "electricity", "heatCoolingLoadMeter")
            ]
            z.writestr(prefix + "/devs.csv", csv_bytes(reference.DEVICE_HEADER, devices))
            water_rows = (
                [("10", 0), ("10", 7), ("", 14), ("12", 21), ("11", 42), ("11.4", 49)]
                if home == "0"
                else [("20", 0), ("20.1", 7)]
            )
            rows = [
                list(native(value, minute, "water" + home).values()) for value, minute in water_rows
            ]
            rows += [
                list(native("32.001999999999995", 0, "hotWater" + home).values()),
                list(native("32.01", 7, "hotWater" + home).values()),
            ]
            # volume is not sufficient: these non-water devices must never become demand.
            rows += [
                list(native("999", 0, "heatCoolingLoadMeter" + home).values()),
                list(native("500", 0, "electricity" + home).values()),
            ]
            z.writestr(prefix + "/ts.csv", csv_bytes(reference.NATIVE_HEADER, rows))
        z.writestr("hihAigua_dataset/lab-generated/ts.csv", b"not field data")
        z.writestr("../must-not-extract.txt", b"untrusted extra member")
    policy = json.loads(reference.POLICY.read_text(encoding="utf-8"))
    policy.update(
        archive_sha256=reference.sha256(archive),
        archive_bytes=archive.stat().st_size,
        household_ids=["0", "1"],
    )
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    archive.with_name(archive.name + ".receipt.json").write_text(
        json.dumps({"sha256": policy["archive_sha256"], "downloaded_at": RECEIPT.isoformat()}),
        encoding="utf-8",
    )
    return library, path, policy


def verified_fixture_policy(native_cache):
    library, path, policy = native_cache
    # This unit is a fixture definition, never a claim about the public release.
    policy["components"]["water"]["unit_review"] = {
        "status": "verified",
        "unit": "m3",
        "evidence_url": "https://example.test/fixture",
        "evidence_quote": "Controlled test fixture defines counter numbers in cubic metres.",
    }
    path.write_text(json.dumps(policy), encoding="utf-8")
    return library, path


def test_pending_units_preserve_decimal_and_block_canonical_points(native_cache, tmp_path):
    library, policy, _ = native_cache
    folder = tmp_path / "prepared"
    manifest = reference.prepare_reference(folder, library=library, policy_path=policy)
    assert manifest["field_households"] == 2
    assert manifest["components"]["water"]["rows"] == 8
    assert manifest["components"]["hotWater"]["rows"] == 4
    assert not manifest["model_ready"] and not manifest["whole_household_total_available"]
    assert not list(folder.rglob("sources.toml"))
    assert not list(folder.rglob("counter-readings.csv"))
    assert not (tmp_path / "must-not-extract.txt").exists()
    rows = pq.read_table(folder / "hotWater/counters.parquet").to_pylist()
    assert rows[0]["value_source"] == "32.001999999999995"
    assert rows[0]["counter_value"] == Decimal("32.001999999999995")
    assert rows[0]["contract_value"] == Decimal("32.002")
    assert all(r["unit"] is None for r in rows)
    assert {r["entity_id"] for r in rows} == {"hotWater0", "hotWater1"}
    assert reference.prepare_reference(folder, library=library, policy_path=policy) == manifest


def test_missing_and_zero_have_different_meanings_and_no_missing_bridge():
    a = parsed("10")
    zero = parsed("10", 7, prior=a)
    missing = parsed("", 14, prior=zero)
    following = parsed("12", 21, prior=missing)
    assert zero["counter_delta_source"] == 0
    assert missing["counter_value"] is None and "missing_counter" in missing["quality_flags"]
    assert following["counter_delta_source"] is None
    assert following["delta_status"] == "missing_counter_barrier"


def test_reset_and_gap_are_preserved_without_daily_reset_or_zero_fill():
    a = parsed("12", 21)
    reset = parsed("11", 42, prior=a)
    assert reset["counter_value"] == 11
    assert reset["counter_delta_source"] == -1
    assert {"counter_decrease", "observation_gap"} <= set(reset["quality_flags"])
    across_midnight = parsed("13", 24 * 60, prior=parsed("12", 24 * 60 - 7))
    assert across_midnight["counter_delta_source"] == 1


def test_epoch_is_authoritative_and_naive_clock_conflict_is_visible():
    row = parsed("1", ts_dt="2025-01-01 01:00:00")
    assert row["occurred_at"] == START
    assert "text_epoch_clock_mismatch" in row["quality_flags"]
    assert row["ts_dt_source"] == "2025-01-01 01:00:00"


def test_identity_does_not_depend_on_value_or_source_row():
    assert parsed("10")["event_id"] == parsed("11")["event_id"]
    reversed_time = parsed("9", 0, prior=parsed("10", 7))
    assert reversed_time["counter_delta_source"] is None
    assert "nonincreasing_timestamp" in reversed_time["quality_flags"]


def test_quantization_is_bounded_and_independent_of_callers_decimal_rounding():
    with localcontext() as context:
        context.rounding = ROUND_UP
        row = parsed("32.001999999999995")
        tie = parsed("0.0000000005")
    assert row["contract_value"] == Decimal("32.002000000")
    assert abs(row["contract_adjustment_source"]) <= Decimal("0.0000000005")
    assert tie["contract_value"] == 0  # explicit HALF_EVEN, not inherited ROUND_UP
    assert "canonical_precision_adjusted" in row["quality_flags"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "bad"])
def test_unusable_counters_remain_in_source_rows(value):
    row = parsed(value)
    assert row["value_source"] == value
    assert row["counter_value"] is None
    assert "invalid_counter" in row["quality_flags"]


def test_rounding_at_contract_boundary_does_not_destroy_source_counter():
    row = parsed("999999999999999.9999999999")
    assert row["counter_value"] == Decimal("999999999999999.9999999999")
    assert row["contract_value"] is None
    assert "canonical_counter_out_of_range" in row["quality_flags"]


def test_claimed_unit_requires_source_evidence(native_cache):
    _, path, policy = native_cache
    review = policy["components"]["water"]["unit_review"]
    review.update(status="verified", unit="m3")
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="explicit source evidence"):
        reference.load_policy(path)


def test_native_alias_collisions_are_rejected(native_cache):
    _, path, policy = native_cache
    policy["household_ids"] = ["0", "00"]
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="aliases would collide"):
        reference.load_policy(path)


def test_archive_tampering_is_rejected_before_preparation(native_cache, tmp_path):
    library, path, policy = native_cache
    with (library / policy["archive"]).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="pinned release"):
        reference.prepare_reference(tmp_path / "prepared", library=library, policy_path=path)
    assert not (tmp_path / "prepared").exists()


def test_prepared_artifact_tampering_is_detected(native_cache, tmp_path):
    library, path, _ = native_cache
    output = tmp_path / "prepared"
    reference.prepare_reference(output, library=library, policy_path=path)
    (output / "water/counters.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        reference.prepare_reference(output, library=library, policy_path=path)


def test_verified_fixture_uses_existing_mapped_csv_contract(native_cache, tmp_path):
    library, policy = verified_fixture_policy(native_cache)
    output = tmp_path / "prepared"
    manifest = reference.prepare_reference(output, library=library, policy_path=policy)
    assert manifest["components"]["water"]["canonical_ready"]
    assert not manifest["components"]["hotWater"]["canonical_ready"]
    assert not (output / "hotWater/sources.toml").exists()
    spec = load_source(output / "water/sources.toml", "meter_readings")
    assert spec.normalizer == "mapped_csv_v1" and spec.constants["unit"] == "m3"
    assert spec.receipt_basis == "research_archive_import"
    with Path(spec.path).open("rb") as stream:
        rows = list(
            get_normalizer(spec.contract, spec.parser, spec.options).rows(stream, 65536, 100)
        )
    assert len(rows) == 8
    assert sum(r.record is None for r in rows) == 1
    assert rows[0].record.kind == "index"
    assert rows[0].record.received_at == RECEIPT
    assert rows[0].record.value == Decimal("10")


def test_pending_unit_blocks_publication_before_any_database_access(native_cache, tmp_path):
    library, policy, _ = native_cache
    output = tmp_path / "prepared"
    reference.prepare_reference(output, library=library, policy_path=policy)
    settings = Settings(database_url="must-not-connect", storage_root=tmp_path / "storage")
    with pytest.raises(ValueError, match="volume unit evidence is pending"):
        reference.publish_component(settings, output, "water", policy_path=policy)
    # A modified preparation status cannot override the authoritative source policy.
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["components"]["water"]["canonical_ready"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="volume unit evidence is pending"):
        reference.publish_component(settings, output, "water", policy_path=policy)


@pytest.mark.postgres
def test_guarded_component_import_is_repeatable(native_cache, tmp_path, restore_settings):
    library, policy = verified_fixture_policy(native_cache)
    output = tmp_path / "prepared"
    reference.prepare_reference(output, library=library, policy_path=policy)
    settings = replace(
        restore_settings,
        environment="public_research",
        study_code="hihaigua-21704780-water",
        cohort_code="field-water-v2",
        timezone="UTC",
    )
    migrate(settings)
    first = reference.publish_component(settings, output, "water", policy_path=policy)
    before = summarize(settings, "hour")
    second = reference.publish_component(settings, output, "water", policy_path=policy)
    assert first["status"] == "published_with_quality_issues"
    assert first["accepted_rows"] == 7 and first["quarantined_rows"] == 1
    assert not first["duplicate"] and second["duplicate"]
    assert not first["model_ready"] and first["coverage_status"] == "unknown"
    assert summarize(settings, "hour") == before
    assert verify_dataset(settings, second["dataset"]["dataset_id"])["verified"]


@pytest.mark.postgres
def test_components_cannot_be_published_into_a_shared_study_database(
    native_cache, tmp_path, restore_settings
):
    library, policy_path = verified_fixture_policy(native_cache)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["components"]["hotWater"]["unit_review"] = dict(
        policy["components"]["water"]["unit_review"]
    )
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    output = tmp_path / "prepared"
    reference.prepare_reference(output, library=library, policy_path=policy_path)
    cold = replace(
        restore_settings,
        environment="public_research",
        study_code="hihaigua-21704780-water",
        cohort_code="field-water-v2",
        timezone="UTC",
    )
    migrate(cold)
    register_file(cold, output / "water/registry.json")
    hot = replace(cold, study_code="hihaigua-21704780-hotWater", cohort_code="field-hotWater-v2")
    with pytest.raises(ValueError, match="separate database"):
        reference.publish_component(hot, output, "hotWater", policy_path=policy_path)
    with connect(cold) as conn:
        assert conn.execute("SELECT count(*) AS n FROM stream").fetchone()["n"] == 2


@pytest.mark.postgres
def test_fixture_ingests_and_freezes_through_common_layer(native_cache, tmp_path, restore_settings):
    library, policy = verified_fixture_policy(native_cache)
    output = tmp_path / "prepared"
    reference.prepare_reference(output, library=library, policy_path=policy)
    settings = replace(
        restore_settings,
        environment="public_research",
        study_code="hihaigua-fixture",
        cohort_code="field-water",
        timezone="UTC",
    )
    migrate(settings)
    register_file(settings, output / "water/registry.json")
    accepted = collect(settings, output / "water/sources.toml", "meter_readings")
    job = work_once(settings)
    assert job["state"] == "partial" and job["accepted_rows"] == 7
    assert job["quarantined_rows"] == 1
    with connect(settings) as conn:
        issues = conn.execute(
            "SELECT code FROM quality_issue WHERE source_id=%s", (accepted["source_id"],)
        ).fetchall()
    assert [q["code"] for q in issues] == ["invalid_contract"]
    before = summarize(settings, "hour")
    assert before["coverage_status"] == "unknown"
    assert before["diagnostics"]["negative_index_delta"] == 1
    assert sum(Decimal(r["observed_liters"]) for r in before["rows"]) == Decimal("2500")
    assert collect(settings, output / "water/sources.toml", "meter_readings")["duplicate"]
    assert summarize(settings, "hour") == before
    frozen = freeze(settings, "hour")
    assert verify_dataset(settings, frozen["dataset_id"])["verified"]
