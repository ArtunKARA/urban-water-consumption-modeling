"""Counterexamples for the planning contract and generated research diagrams."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "contracts"


@pytest.fixture(scope="module")
def api():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT / "scripts"))
        checks = importlib.import_module("model_flow_validation")
        poster = importlib.import_module("model_flow_overview")
    return SimpleNamespace(
        checks=checks,
        poster=poster,
        plan=checks.load_json(DOCS / "model-flow-map.v0.1.json"),
        contract=checks.load_json(DOCS / "research-data-contract.v0.1.schema.json"),
    )


def mutate(plan, case):
    profile = plan["models"][0]
    forecast = next(m for m in plan["models"] if m["id"] == "F01")
    anomaly = next(m for m in plan["models"] if m["id"] == "A01")
    if case == "derived_report_leak":
        plan["fields"]["report_copy"] = {
            "source": "survey",
            "label": "Copied report",
            "derived_from": ["D07"],
            "definition": "Copy participant report",
        }
        plan["fields"]["report_copy_twice"] = {
            "source": "survey",
            "label": "Second copy",
            "derived_from": ["report_copy"],
            "definition": "Second copy of report",
        }
        profile["inputs"].append(["report_copy_twice", "feature"])
    elif case == "consent_feature":
        profile["inputs"].append(["consent", "feature"])
    elif case == "aliased_consent":
        plan["fields"]["permission_copy"] = copy.deepcopy(plan["fields"]["consent"])
        profile["inputs"].append(["permission_copy", "feature"])
    elif case == "derived_consent":
        plan["fields"]["permission_feature"] = {
            "source": "survey",
            "label": "Copied consent",
            "derived_from": ["consent"],
            "definition": "Use consent as numeric feature",
        }
        profile["inputs"].append(["permission_feature", "context"])
    elif case == "survey_ref_swap":
        plan["fields"]["H01"]["schema_ref"] = "#/$defs/SurveyAnswers/properties/G01"
    elif case == "wrong_source":
        plan["fields"]["H01"]["source"] = "weather"
    elif case == "unsafe_identifier":
        plan["fields"]["odd:id"] = copy.deepcopy(plan["fields"]["H01"])
    elif case == "field_typo":
        plan["fields"]["raw"]["derived_fom"] = ["D07"]
    elif case == "root_typo":
        plan["statuz"] = "trained"
    elif case == "training_claim":
        plan["status"] = "trained"
    elif case == "weather_selector":
        plan["fields"]["rain"]["selector"] = "name=humidity"
    elif case == "unknown_ref":
        plan["fields"]["raw"]["schema_ref"] = "#/$defs/NotAType"
    elif case == "ambiguous_derivation":
        plan["fields"]["raw"]["derived_from"] = ["device"]
    elif case == "missing_derivation_parent":
        plan["fields"]["shape"]["derived_from"] = ["missing_parent"]
    elif case == "derivation_cycle":
        plan["fields"]["liters"]["derived_from"] = ["shape"]
    elif case == "missing_definition":
        del plan["fields"]["shape"]["definition"]
    elif case == "producer_cycle":
        anomaly["inputs"].append(["anomaly_output", "feature"])
    elif case == "unknown_producer":
        plan["fields"]["anomaly_output"]["producer_model"] = "A99"
    elif case == "wrong_producer_family":
        plan["fields"]["anomaly_output"]["producer_model"] = "F01"
    elif case == "unclassified_gate":
        forecast["variants"][-1]["gate_fields"] = []
    elif case == "context_role_mismatch":
        forecast["variants"][-1]["context_fields"].append("daily_history")
    elif case == "ambiguous_gate":
        anomaly["optional_gates"].append("coverage")
    elif case == "invalid_kind":
        profile["kind"] = "learnt"
    elif case == "empty_algorithm":
        profile["algorithm"] = ""
    elif case == "unknown_output":
        profile["output_family"] = "profile"
    elif case == "duplicate_input":
        profile["inputs"].append(["shape", "feature"])
    elif case == "duplicate_model":
        plan["models"][1]["id"] = profile["id"]
    elif case == "invalid_pair_type":
        profile["inputs"].append(["shape", []])
    elif case == "missing_base_variant":
        forecast["variants"] = forecast["variants"][1:]
    elif case == "misplaced_weather_gate":
        forecast["variants"][1]["gate_fields"] = forecast["variants"][-1]["gate_fields"]
        forecast["variants"][-1]["gate_fields"] = []
    elif case == "weather_required_for_base":
        forecast["variants"][0]["gate_fields"] = ["weather_issued", "area"]
    elif case == "no_core_features":
        profile["inputs"] = [pair for pair in profile["inputs"] if pair[1] != "feature"]
    else:
        raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "derived_report_leak",
        "consent_feature",
        "aliased_consent",
        "derived_consent",
        "survey_ref_swap",
        "wrong_source",
        "unsafe_identifier",
        "field_typo",
        "root_typo",
        "training_claim",
        "weather_selector",
        "unknown_ref",
        "ambiguous_derivation",
        "missing_derivation_parent",
        "derivation_cycle",
        "missing_definition",
        "producer_cycle",
        "unknown_producer",
        "wrong_producer_family",
        "unclassified_gate",
        "context_role_mismatch",
        "ambiguous_gate",
        "invalid_kind",
        "empty_algorithm",
        "unknown_output",
        "duplicate_input",
        "duplicate_model",
        "invalid_pair_type",
        "missing_base_variant",
        "misplaced_weather_gate",
        "weather_required_for_base",
        "no_core_features",
    ],
)
def test_bad_plan_revisions_fail_closed(api, case):
    plan = copy.deepcopy(api.plan)
    mutate(plan, case)
    with pytest.raises(ValueError):
        api.checks.validate(plan, api.contract)


@pytest.mark.parametrize("text", ['{"same":1,"same":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_noncanonical_json_is_not_silently_read(api, tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        api.checks.load_json(path)


def test_current_plan_and_weather_optional_variants(api):
    api.checks.validate(api.plan, api.contract)
    for model in api.plan["models"]:
        if model["id"] in {"F01", "F02"}:
            history = next(v for v in model["variants"] if v["id"] == "history")
            assert not history["context_fields"] and not history["gate_fields"]
            assert "weather_issued" not in model["base_gates"]
            assert "area" not in model["base_gates"]


@pytest.mark.parametrize("reordered", [False, True])
def test_poster_preserves_all_edges_roles_and_producers(api, reordered):
    plan = copy.deepcopy(api.plan)
    if reordered:
        plan["models"].reverse()
    root = ET.fromstring(api.poster.render_overview(plan))
    elements = {element.get("id"): element for element in root.iter() if element.get("id")}
    represented = []
    for element in root.iter():
        if "data-field-ids" in element.attrib:
            represented.extend(json.loads(element.get("data-field-ids")))
        elif "data-field-id" in element.attrib:
            represented.append(element.get("data-field-id"))
    assert sorted(represented) == sorted(plan["fields"])
    actual = {}
    for edge in root.iter():
        if edge.get("data-model") and edge.get("data-field"):
            mid, fid, role = (edge.get(k) for k in ("data-model", "data-field", "data-role"))
            actual[(mid, fid)] = role
            assert edge.get("data-to") == ("output-" if role == "review" else "model-") + mid
            producer = plan["fields"][fid].get("producer_model")
            assert edge.get("data-from") == ("output-" + producer if producer else "field-" + fid)
    expected = {(m["id"], fid): role for m in plan["models"] for fid, role in m["inputs"]}
    assert actual == expected
    assert api.checks.model_levels(plan)["V01"] > api.checks.model_levels(plan)["A01"]
    assert "source-upstream" not in elements


@pytest.fixture
def exported(api, tmp_path):
    svg = "<svg/>"
    record = {"svg_sha256": hashlib.sha256(svg.encode()).hexdigest(), "artifacts": {}}
    for ext in ("png", "pdf"):
        data = ("original-" + ext).encode()
        (tmp_path / f"model-flow-overview.{ext}").write_bytes(data)
        record["artifacts"][ext] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    (tmp_path / "model-flow-overview.exports.json").write_text(json.dumps(record))
    return tmp_path, svg


def test_export_hashes_accept_unchanged_files(api, exported):
    api.checks.verify_exports(*exported)


@pytest.mark.parametrize("damage", ["png", "pdf", "missing_manifest", "stale_svg", "missing_pdf"])
def test_export_damage_is_detected(api, exported, damage):
    path, svg = exported
    if damage in {"png", "pdf"}:
        (path / f"model-flow-overview.{damage}").write_bytes(b"replaced")
    elif damage == "missing_manifest":
        (path / "model-flow-overview.exports.json").unlink()
    elif damage == "missing_pdf":
        (path / "model-flow-overview.pdf").unlink()
    else:
        svg = "<svg>new</svg>"
    with pytest.raises(ValueError):
        api.checks.verify_exports(path, svg)


def validator(api, name):
    return Draft202012Validator(
        {"$defs": api.contract["$defs"], "$ref": f"#/$defs/{name}"}, format_checker=FormatChecker()
    )


def period():
    return {"start": "2026-01-01T00:00:00+03:00", "end": "2026-01-02T00:00:00+03:00"}


def anomaly():
    return {
        "anomaly_id": "a1",
        "target": {"type": "household", "id": "H1"},
        "period": period(),
        "candidate_type": "unusual_consumption",
        "model_version": "a01-v1",
        "score": "0.5",
        "score_method": "negative_score_samples",
        "score_direction": "higher_is_more_anomalous",
        "verification_state": "unreviewed",
        "evidence_refs": [],
        "status": "ready",
    }


def test_contract_and_scored_anomaly_are_valid(api):
    Draft202012Validator.check_schema(api.contract)
    validator(api, "Anomaly").validate(anomaly())


@pytest.mark.parametrize(
    "change",
    [
        {"score": None},
        {"score_method": None},
        {"score_direction": None},
        {"status": "failed"},
        {"model_version": None},
        {"verification_state": "confirmed"},
    ],
)
def test_ambiguous_anomaly_results_are_rejected(api, change):
    record = anomaly() | change
    with pytest.raises(ValidationError):
        validator(api, "Anomaly").validate(record)


def test_unavailable_anomaly_has_no_fabricated_score(api):
    record = anomaly() | {
        "status": "insufficient_data",
        "score": None,
        "score_method": None,
        "score_direction": None,
        "model_version": None,
    }
    validator(api, "Anomaly").validate(record)


def performance():
    return {
        "performance_id": "e1",
        "target": {"type": "meter", "id": "M1"},
        "period": period(),
        "curve_id": "curve1",
        "basis": "validated_curve",
        "volume_basis": "reference",
        "error_definition": "relative_to_reference",
        "weighted_signed_error_percent": "25",
        "status": "ready",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"volume_basis": "unknown"},
        {"error_definition": "unknown"},
        {"basis": "unavailable"},
        {"curve_id": None},
        {"status": "insufficient_data"},
        {"status": "failed"},
    ],
)
def test_unjustified_meter_performance_is_rejected(api, change):
    with pytest.raises(ValidationError):
        validator(api, "MeterPerformance").validate(performance() | change)


def test_reference_based_and_unavailable_meter_outputs(api):
    check = validator(api, "MeterPerformance")
    check.validate(performance())
    check.validate(performance() | {"basis": "reference_measurement", "curve_id": None})
    check.validate(
        performance()
        | {
            "status": "insufficient_data",
            "basis": "unavailable",
            "weighted_signed_error_percent": None,
            "volume_basis": "unknown",
            "error_definition": "unknown",
        }
    )


def test_regional_target_has_explicit_period_type(api):
    record = {
        "prediction_id": "f3",
        "target": {"type": "area", "id": "R1"},
        "issued_at": "2025-12-31T00:00:00+03:00",
        "target_period": period(),
        "horizon_type": "regional_period",
        "model_version": "f03-v1",
        "predicted_liters": "100",
        "lower_liters": None,
        "upper_liters": None,
        "status": "ready",
    }
    check = validator(api, "Forecast")
    check.validate(record)
    with pytest.raises(ValidationError):
        check.validate(record | {"target": {"type": "household", "id": "H1"}})


def test_ready_profile_cannot_have_no_profile(api):
    record = {
        "profile_result_id": "p1",
        "target": {"type": "household", "id": "H1"},
        "period": period(),
        "model_version": "p01-v1",
        "profile_label": "c1",
        "status": "ready",
    }
    check = validator(api, "ProfileResult")
    check.validate(record)
    with pytest.raises(ValidationError):
        check.validate(record | {"profile_label": None})
    with pytest.raises(ValidationError):
        check.validate(record | {"status": "failed"})


def test_curve_retains_flow_and_error_basis(api):
    record = {
        "curve_id": "c1",
        "technology": "test",
        "model_code": None,
        "basis": "assumed_scenario",
        "flow_basis": "reference",
        "error_definition": "relative_to_reference",
        "points": [{"flow_liters_per_hour": "10", "signed_error_percent": "-5"}],
        "source_ref": {"source_id": "s1", "known_at": "2026-01-01T00:00:00Z"},
    }
    check = validator(api, "MeterErrorCurve")
    check.validate(record)
    with pytest.raises(ValidationError):
        check.validate(record | {"flow_basis": "guess"})


def test_source_acquisition_clock_is_distinct_and_timezone_aware(api):
    record = {
        "source_id": "s1",
        "source_type": "meter",
        "source_schema_version": "v1",
        "known_at": "2026-01-20T00:00:00Z",
    }
    check = validator(api, "Source")
    check.validate(record)
    check.validate(record | {"known_at": None})
    with pytest.raises(ValidationError):
        check.validate(record | {"known_at": "2026-01-20T00:00:00"})


def test_evaluation_task_and_horizon_agree(api):
    record = {
        "experiment_id": "f3",
        "split_id": "test",
        "task": "forecasting",
        "horizon_type": "regional_period",
        "metrics": [{"name": "MAE", "value": "5", "unit": "L"}],
        "sample_counts": {
            "eligible_households": 0,
            "used_households": 0,
            "eligible_periods": 2,
            "used_periods": 2,
        },
        "status": "ready",
    }
    check = validator(api, "Evaluation")
    check.validate(record)
    for changes in [{"task": "profiling"}, {"horizon_type": None}, {"status": "failed"}]:
        with pytest.raises(ValidationError):
            check.validate(record | changes)
    check.validate(
        record | {"status": "failed", "metrics": [{"name": "MAE", "value": None, "unit": "L"}]}
    )


def test_model_rename_does_not_leave_hardcoded_upstream_edges(api):
    plan = copy.deepcopy(api.plan)
    next(m for m in plan["models"] if m["id"] == "A01")["id"] = "A02"
    plan["initial_model"] = "A02"
    plan["fields"]["anomaly_output"]["producer_model"] = "A02"
    plan["sources"]["upstream"]["label"] = "A02 output"
    api.checks.validate(plan, api.contract)
    root = ET.fromstring(api.poster.render_overview(plan))
    edge = next(e for e in root.iter() if e.get("data-field") == "anomaly_output")
    assert edge.get("data-from") == "output-A02"
    assert edge.get("data-to") == "model-V01"


def test_contract_identity_is_carried_into_poster(api):
    a = api.poster.render_overview(api.plan, "contract-a")
    b = api.poster.render_overview(api.plan, "contract-b")
    assert a != b
    root = ET.fromstring(a)
    metadata = next(e for e in root.iter() if e.tag.endswith("metadata"))
    assert json.loads(metadata.text)["contract_sha256"] == "contract-a"


def test_regional_only_plan_does_not_require_a_derived_layer(api):
    plan = copy.deepcopy(api.plan)
    plan["models"] = [m for m in plan["models"] if m["id"] == "F03"]
    plan["initial_model"] = "F03"
    plan["fields"] = {
        fid: field
        for fid, field in plan["fields"].items()
        if "derived_from" not in field and "producer_model" not in field
    }
    del plan["sources"]["upstream"]
    api.checks.validate(plan, api.contract)
    root = ET.fromstring(api.poster.render_overview(plan))
    represented = [e.get("data-field-id") for e in root.iter() if e.get("data-field-id")]
    assert sorted(represented) == sorted(plan["fields"])


def test_user_decisions_match_experiment_protocols(api):
    models = {m["id"]: m for m in api.plan["models"]}
    protocols = tomllib.loads(
        (ROOT / "research/experiments/protocols.toml").read_text(encoding="utf-8")
    )
    daily = models["F01"]["experiment_design"]
    assert daily["local_times"] == ["18:00", "23:00"]
    assert daily["local_times"] == protocols["context_forecasting"]["daily_issuance"]["local_times"]
    assert daily["timezone"] == "Europe/Istanbul"
    assert daily["reuse_later_features"] is False
    assert ["intraday_history", "feature"] in models["F01"]["inputs"]
    assert "coverage" in api.plan["fields"]["intraday_history"]["derived_from"]
    profile = models["P01"]["experiment_design"]
    assert profile["variant_ids"] == ["consumption", "household"]
    assert (
        profile["variant_ids"]
        == protocols["distributed_profiling"]["context_comparison"]["variants"]
    )
    anomaly = models["A01"]["experiment_design"]
    assert set(anomaly["scenario_families"]) == {
        "leak",
        "normal_behavior_change",
        "device_or_transport_fault",
    }
    assert (
        anomaly["scenario_families"]
        == protocols["anomaly_verification"]["synthetic_scenarios"]["families"]
    )
    assert protocols["status"] == "planned-not-executed"


@pytest.mark.parametrize(
    "model_id,key,value",
    [
        ("F01", "local_times", ["18:00", "18:00"]),
        ("F01", "local_times", ["18:00", "25:00"]),
        ("F01", "reuse_later_features", True),
        ("F01", "shared_target_days", False),
        ("F01", "separate_reports", False),
        ("P01", "variant_ids", ["consumption", "missing"]),
        ("P01", "shared_splits", False),
        ("A01", "truth_in_features", True),
        ("A01", "normal_changes_are_leaks", True),
        ("A01", "device_faults_are_leaks", True),
        ("A01", "fault_output_family", "anomalies"),
    ],
)
def test_experiment_comparisons_keep_isolation(api, model_id, key, value):
    plan = copy.deepcopy(api.plan)
    next(m for m in plan["models"] if m["id"] == model_id)["experiment_design"][key] = value
    with pytest.raises(ValueError):
        api.checks.validate(plan, api.contract)
