"""Fail-closed checks for the research planning map, not an ingestion validator."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

ROLES = {
    "feature": "Temel özellik",
    "context": "Aday bağlam",
    "gate": "Veri uygunluk kontrolü",
    "review": "Yorum / değerlendirme; eğitim girdisi değil",
}
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
OUTPUT_DEFINITIONS = {
    "profiles": "ProfileResult",
    "forecasts": "Forecast",
    "anomalies": "Anomaly",
    "decisions": "Decision",
    "meter_performance": "MeterPerformance",
}


def load_json(path: Path) -> dict:
    def pairs(values: list[tuple]) -> dict:
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"Non-JSON numeric constant: {value}")

    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant
    )


def _keys(value: object, required: set[str], optional: set[str], where: str) -> None:
    if not isinstance(value, dict) or not required <= value.keys():
        raise ValueError(f"Missing object/properties: {where}")
    if extra := value.keys() - required - optional:
        raise ValueError(f"Unknown properties in {where}: {sorted(extra)}")


def _strings(value: object, where: str, nonempty: bool = False) -> None:
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError(f"Expected string list: {where}")
    if len(set(value)) != len(value) or (nonempty and not value):
        raise ValueError(f"Empty or duplicate values: {where}")


def _identifier(value: object, where: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe diagram identifier: {where}")


def _text(value: object, where: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected nonempty text: {where}")


def validate_experiment(model: dict) -> None:
    design = model.get("experiment_design")
    if design is None:
        return
    if not isinstance(design, dict):
        raise ValueError("Experiment design must be an object")
    kind = design.get("kind")
    common = {"kind", "label", "shared_splits", "separate_reports"}
    if kind == "profile_context_comparison":
        _keys(design, common | {"variant_ids"}, set(), "profile experiment")
        _strings(design["variant_ids"], "variant_ids", True)
        if (
            model["output_family"] != "profiles"
            or len(design["variant_ids"]) < 2
            or not (set(design["variant_ids"]) <= {v["id"] for v in model["variants"]})
        ):
            raise ValueError("Invalid profile comparison variants")
    elif kind == "daily_issuance_comparison":
        _keys(
            design,
            common
            | {
                "local_times",
                "timezone",
                "target_policy",
                "shared_target_days",
                "reuse_later_features",
            },
            set(),
            "daily experiment",
        )
        _strings(design["local_times"], "local_times", True)
        _text(design["timezone"], "experiment timezone")
        if (
            model["output_family"] != "forecasts"
            or len(design["local_times"]) < 2
            or any(
                not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value)
                for value in design["local_times"]
            )
        ):
            raise ValueError("Invalid comparison issuance times")
        if design["reuse_later_features"] is not False or design["shared_target_days"] is not True:
            raise ValueError("Issuance comparison must isolate as_of information and pair targets")
        if design["target_policy"] != "next_local_calendar_day":
            raise ValueError("Daily comparison must keep the same next-day target policy")
    elif kind == "anomaly_scenarios":
        _keys(
            design,
            {
                "kind",
                "label",
                "scenario_families",
                "normal_examples",
                "fault_examples",
                "separate_reports",
                "truth_in_features",
                "normal_changes_are_leaks",
                "device_faults_are_leaks",
                "fault_output_family",
            },
            set(),
            "anomaly experiment",
        )
        for key in ("scenario_families", "normal_examples", "fault_examples"):
            _strings(design[key], key, True)
        if model["output_family"] != "anomalies" or not set(design["scenario_families"]) <= {
            "leak",
            "normal_behavior_change",
            "device_or_transport_fault",
        }:
            raise ValueError("Unknown anomaly scenario family")
        if (
            any(
                design[key] is not False
                for key in (
                    "truth_in_features",
                    "normal_changes_are_leaks",
                    "device_faults_are_leaks",
                )
            )
            or design["fault_output_family"] != "quality"
        ):
            raise ValueError("Anomaly scenarios must separate truth, normal behavior and faults")
    else:
        raise ValueError("Unknown experiment design kind")
    _text(design["label"], "experiment label")
    if design["separate_reports"] is not True or (
        "shared_splits" in design and design["shared_splits"] is not True
    ):
        raise ValueError("Comparison reports/splits must stay separate/paired")


def verify_exports(docs: Path, svg: str) -> None:
    manifest = docs / "model-flow-overview.exports.json"
    if not manifest.is_file():
        raise ValueError("Missing PNG/PDF export manifest")
    record = load_json(manifest)
    if record.get("svg_sha256") != hashlib.sha256(svg.encode()).hexdigest():
        raise ValueError("Stale PNG/PDF export source")
    for ext in ("png", "pdf"):
        path = docs / f"model-flow-overview.{ext}"
        expected = record.get("artifacts", {}).get(ext, {})
        if not path.is_file():
            raise ValueError(f"Missing export: {ext}")
        content = path.read_bytes()
        if (
            not content
            or expected.get("sha256") != hashlib.sha256(content).hexdigest()
            or (expected.get("size_bytes") != len(content))
        ):
            raise ValueError(f"Modified or unverified export: {ext}")


def ancestors(fields: dict, fid: str, stack: frozenset[str] = frozenset()) -> set[str]:
    if fid not in fields or fid in stack:
        raise ValueError(f"Unknown/cyclic derivation input: {fid}")
    result = {fid}
    for parent in fields[fid].get("derived_from", []):
        result |= ancestors(fields, parent, stack | {fid})
    return result


def model_levels(plan: dict) -> dict[str, int]:
    models = {m["id"]: m for m in plan["models"]}
    levels: dict[str, int] = {}

    def visit(mid: str, stack: frozenset[str]) -> int:
        if mid not in models or mid in stack:
            raise ValueError(f"Unknown/cyclic producer model: {mid}")
        if mid not in levels:
            parents = {
                plan["fields"][ancestor]["producer_model"]
                for fid, role in models[mid]["inputs"]
                if role != "review"
                for ancestor in ancestors(plan["fields"], fid)
                if "producer_model" in plan["fields"][ancestor]
            }
            levels[mid] = max((visit(p, stack | {mid}) + 1 for p in parents), default=0)
        return levels[mid]

    for mid in models:
        visit(mid, frozenset())
    return levels


def _semantic_roles(field: dict) -> set[str]:
    ref = field.get("schema_ref", "")
    if ref in {
        "#/$defs/SurveyAnswers/properties/D07",
        "#/$defs/SurveyAnswers/properties/O01",
        "#/$defs/HouseholdEvent",
    }:
        return {"review"}
    if ref in {
        "#/$defs/SurveyResponse/properties/participation_consent",
        "#/$defs/SurveyResponse/properties/installation_consent",
        "#/$defs/SurveyAnswers/properties/K08",
        "#/$defs/Weather/properties/issued_at",
    }:
        return {"gate", "review"}
    if ref in {
        f"#/$defs/{name}"
        for name in (
            "Installation",
            "HouseholdLink",
            "Area",
            "Configuration",
            "Meter",
            "DeviceStatus",
            "Id",
        )
    }:
        return {"gate", "review"}
    return set(ROLES)


def validate(plan: dict, contract: dict) -> None:
    _keys(
        plan,
        {
            "version",
            "status",
            "initial_model",
            "updated_on",
            "contract",
            "sources",
            "fields",
            "models",
            "global_controls",
        },
        set(),
        "plan",
    )
    if plan["status"] != "planning-not-trained":
        raise ValueError("The planning map cannot certify training completion")
    for key in ("version", "contract", "updated_on"):
        _text(plan[key], key)
    if Path(plan["contract"]).name != plan["contract"] or "\\" in plan["contract"]:
        raise ValueError("Contract must be a sibling document")
    date.fromisoformat(plan["updated_on"])
    sources, fields = plan["sources"], plan["fields"]
    if not isinstance(sources, dict) or not sources or not isinstance(fields, dict) or not fields:
        raise ValueError("Sources and fields must be nonempty objects")
    if not isinstance(plan["models"], list) or not plan["models"]:
        raise ValueError("Models must be a nonempty list")
    _strings(plan["global_controls"], "global_controls", True)
    for sid, source in sources.items():
        _identifier(sid, "source")
        _keys(source, {"label", "status", "kind", "schema_definitions"}, set(), sid)
        _text(source["label"], sid + ".label")
        _text(source["status"], sid + ".status")
        if source["kind"] not in {"external", "model_output"}:
            raise ValueError(f"Invalid source kind: {sid}")
        _strings(source["schema_definitions"], sid + ".schema_definitions", True)
        if not set(source["schema_definitions"]) <= contract["$defs"].keys():
            raise ValueError(f"Unknown schema definition for source: {sid}")
    ids = [m.get("id") for m in plan["models"] if isinstance(m, dict)]
    for mid in ids:
        _identifier(mid, "model")
    if len(ids) != len(plan["models"]) or len(set(ids)) != len(ids):
        raise ValueError("Model IDs must be unique")
    if plan["initial_model"] not in ids:
        raise ValueError("Unknown initial model")
    for fid, field in fields.items():
        _identifier(fid, "field")
        _keys(
            field,
            {"source", "label"},
            {
                "schema_ref",
                "selector",
                "derived_from",
                "definition",
                "producer_model",
                "allowed_roles",
            },
            fid,
        )
        _text(field["label"], fid + ".label")
        _identifier(field["source"], fid + ".source")
        if field["source"] not in sources:
            raise ValueError(f"Unknown source: {fid}")
        if ("schema_ref" in field) == ("derived_from" in field):
            raise ValueError(f"Use exactly one schema reference or derivation: {fid}")
        if "schema_ref" in field:
            ref = field["schema_ref"]
            if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
                raise ValueError(f"Invalid local schema reference: {fid}")
            try:
                value = contract
                for part in ref[2:].split("/"):
                    value = value[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as error:
                raise ValueError(f"Unresolved schema reference: {fid}") from error
            if ref.split("/")[2] not in sources[field["source"]]["schema_definitions"]:
                raise ValueError(f"Source/schema mismatch: {fid}")
        else:
            _strings(field["derived_from"], fid + ".derived_from", True)
            if not isinstance(field.get("definition"), str) or not field["definition"].strip():
                raise ValueError(f"Derived field needs a definition: {fid}")
        if "selector" in field:
            names = contract["$defs"]["Weather"]["properties"]["variables"]["items"]["properties"][
                "name"
            ]["enum"]
            if field.get("schema_ref") != "#/$defs/Weather/properties/variables" or (
                field["selector"] not in {f"name={name}" for name in names}
            ):
                raise ValueError(f"Invalid weather selector: {fid}")
        if "allowed_roles" in field:
            _strings(field["allowed_roles"], fid + ".allowed_roles", True)
            if not set(field["allowed_roles"]) <= _semantic_roles(field):
                raise ValueError(f"Field role restriction widens semantic permissions: {fid}")
        produced = sources[field["source"]]["kind"] == "model_output"
        if produced != ("producer_model" in field) or (
            produced and field["producer_model"] not in ids
        ):
            raise ValueError(f"Missing/invalid producer model: {fid}")
        ancestors(fields, fid)
    for question in contract["$defs"]["SurveyAnswers"]["properties"]:
        if (
            question not in fields
            or fields[question].get("schema_ref")
            != (f"#/$defs/SurveyAnswers/properties/{question}")
            or fields[question]["source"] != "survey"
        ):
            raise ValueError(f"Survey field mapping mismatch: {question}")
    for model in plan["models"]:
        mid = model["id"]
        _identifier(mid, "model")
        _keys(
            model,
            {
                "id",
                "label",
                "question",
                "kind",
                "stage",
                "algorithm",
                "baseline",
                "target",
                "inputs",
                "outputs",
                "metrics",
                "split",
                "requires",
                "open",
                "output_family",
                "base_gates",
                "optional_gates",
                "variants",
            },
            {"documentation", "experiment_design"},
            mid,
        )
        for key in (
            "label",
            "question",
            "stage",
            "algorithm",
            "baseline",
            "target",
            "metrics",
            "split",
            "requires",
            "open",
        ):
            _text(model[key], mid + "." + key)
        if not isinstance(model["inputs"], list) or not model["inputs"]:
            raise ValueError(f"Missing model inputs: {mid}")
        if not isinstance(model["variants"], list) or not model["variants"]:
            raise ValueError(f"Missing model variants: {mid}")
        if model["kind"] not in {"learned", "rules", "analytical", "conditional"}:
            raise ValueError(f"Unknown model kind: {mid}")
        if model["output_family"] not in OUTPUT_DEFINITIONS:
            raise ValueError(f"Unknown output family: {mid}")
        _strings(model["outputs"], mid + ".outputs", True)
        roles = {}
        for pair in model["inputs"]:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError(f"Invalid input pair: {mid}")
            fid, role = pair
            if (
                not isinstance(fid, str)
                or not isinstance(role, str)
                or (fid not in fields or role not in ROLES or fid in roles)
            ):
                raise ValueError(f"Invalid or duplicate input: {mid}/{fid}/{role}")
            roles[fid] = role
            if role not in (
                _semantic_roles(fields[fid]) & set(fields[fid].get("allowed_roles", ROLES))
            ):
                raise ValueError(f"Forbidden field role: {mid}/{fid}/{role}")
            if role in {"feature", "context"}:
                for parent in ancestors(fields, fid):
                    ref = fields[parent].get("schema_ref", "")
                    if _semantic_roles(fields[parent]) == {"review"} or ref in {
                        "#/$defs/SurveyResponse/properties/participation_consent",
                        "#/$defs/SurveyResponse/properties/installation_consent",
                    }:
                        raise ValueError(f"Review/admin information leaks through: {mid}/{fid}")
        for key in ("base_gates", "optional_gates"):
            _strings(model[key], mid + "." + key)
        if model["kind"] in {"learned", "conditional"} and "feature" not in roles.values():
            raise ValueError(f"Missing base learning features: {mid}")
        base, optional = set(model["base_gates"]), set(model["optional_gates"])
        context, conditional = set(), set()
        variant_ids = set()
        for variant in model["variants"]:
            _keys(variant, {"id", "label", "context_fields", "gate_fields"}, {"mode"}, mid)
            _identifier(variant["id"], "variant")
            _text(variant["label"], mid + ".variant.label")
            if variant["id"] in variant_ids:
                raise ValueError(f"Duplicate model variant: {mid}")
            variant_ids.add(variant["id"])
            _strings(variant["context_fields"], mid + ".context_fields")
            _strings(variant["gate_fields"], mid + ".gate_fields")
            mode = variant.get("mode", "operational")
            if mode not in {"operational", "scenario"} or (
                mode == "scenario" and model["kind"] != "conditional"
            ):
                raise ValueError(f"Invalid scenario variant: {mid}")
            weather = any(
                "Weather" in sources[fields[fid]["source"]]["schema_definitions"]
                for fid in variant["context_fields"]
                if fid in fields
            )
            if (
                weather
                and mode == "operational"
                and not {"weather_issued", "area"} <= (base | set(variant["gate_fields"]))
            ):
                raise ValueError(f"Missing operational weather availability/area controls: {mid}")
            context |= set(variant["context_fields"])
            conditional |= set(variant["gate_fields"])
        if context != {fid for fid, role in roles.items() if role == "context"}:
            raise ValueError(f"Context variant mapping mismatch: {mid}")
        if (base | optional | conditional) != {f for f, r in roles.items() if r == "gate"}:
            raise ValueError(f"Gate variant mapping mismatch: {mid}")
        if base & optional or base & conditional or optional & conditional:
            raise ValueError(f"Ambiguous gate condition: {mid}")
        if not variant_ids or not any(
            not v["context_fields"] and not v["gate_fields"] for v in model["variants"]
        ):
            raise ValueError(f"Missing base comparison variant: {mid}")
        validate_experiment(model)
    model_levels(plan)
    models = {m["id"]: m for m in plan["models"]}
    for fid, field in fields.items():
        if "producer_model" in field:
            expected = OUTPUT_DEFINITIONS[models[field["producer_model"]]["output_family"]]
            if field.get("schema_ref") != f"#/$defs/{expected}":
                raise ValueError(f"Producer output schema mismatch: {fid}")
