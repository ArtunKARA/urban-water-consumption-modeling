"""Prepare the pinned hihAigua field counters for the existing source interface.

Default is local preparation, not database import. --require-canonical exits 2
while either numeric volume unit is unresolved. It cannot assign a unit or bypass
the source policy. Prepared source points use the existing mapped_csv_v1 parser.
"""

import argparse
import json
from pathlib import Path

import urban_water.sources.hihaigua_reference as reference
from urban_water.db import PROJECT_ROOT
from urban_water.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=reference.POLICY)
    parser.add_argument("--library", type=Path, default=reference.LIBRARY)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-canonical", action="store_true")
    parser.add_argument("--import-component", choices=["water", "hotWater"])
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.import_component and not args.env_file:
        parser.error("--import-component requires an explicit --env-file")
    fingerprint = (
        reference.sha256(args.policy)[:12] + "-" + reference.sha256(Path(reference.__file__))[:12]
    )
    directory = args.output or PROJECT_ROOT / "var/research/hihaigua/v2/prepared" / fingerprint
    manifest = reference.prepare_reference(directory, library=args.library, policy_path=args.policy)
    pointer = PROJECT_ROOT / "var/research/hihaigua/v2/latest.json"
    reference.write_json(
        pointer,
        {
            "directory": str(directory.resolve()),
            "manifest_sha256": reference.sha256(directory / "manifest.json"),
        },
    )
    ready = all(c["canonical_ready"] for c in manifest["components"].values())
    report = {
        "status": "canonical_source_points_prepared" if ready else "prepared_unit_review_required",
        "directory": manifest["directory"],
        "manifest": str(directory / "manifest.json"),
        "field_households": manifest["field_households"],
        "native_field_rows_scanned": manifest["native_field_rows_scanned"],
        "components": {
            c: {k: v for k, v in item.items() if k != "streams"}
            for c, item in manifest["components"].items()
        },
        "canonical_imported": False,
        "model_trained": False,
        "whole_household_total_available": False,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.import_component:
        if not manifest["components"][args.import_component]["canonical_ready"]:
            print("Publication blocked: component volume unit evidence is pending.")
            raise SystemExit(2)
        imported = reference.publish_component(
            Settings.from_env(args.env_file),
            directory,
            args.import_component,
            policy_path=args.policy,
        )
        print(json.dumps(imported, indent=2, ensure_ascii=False))
    if args.require_canonical and not ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
