import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

from urban_water.backup import create_backup, restore_backup
from urban_water.datasets import freeze, verify_dataset
from urban_water.db import connect, migrate
from urban_water.ingestion import accept, retry
from urban_water.processing import summarize
from urban_water.registry import seed_synthetic
from urban_water.settings import Settings
from urban_water_contracts import Measurement
from urban_water_simulator import generate


def main() -> None:
    parser = argparse.ArgumentParser(description="Urban water synthetic research infrastructure")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("status")
    sub.add_parser("contract")
    seed = sub.add_parser("seed")
    seed.add_argument("--households", type=int, default=2)
    seed.add_argument("--kind", choices=["pulse", "interval", "index"], default="pulse")
    simulator = sub.add_parser("simulate")
    simulator.add_argument("directory", type=Path)
    simulator.add_argument("--households", type=int, default=2)
    simulator.add_argument("--days", type=int, default=2)
    simulator.add_argument("--seed", type=int, default=42)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("source", type=Path)
    reprocess = sub.add_parser("retry")
    reprocess.add_argument("source_id")
    for name in ("summarize", "freeze"):
        command = sub.add_parser(name)
        command.add_argument("--grain", choices=["hour", "day"], default="day")
    verify = sub.add_parser("verify-dataset")
    verify.add_argument("dataset_id")
    for name in ("backup", "restore"):
        command = sub.add_parser(name)
        command.add_argument("directory", type=Path)
        command.add_argument("--pg-bin", type=Path)
        if name == "restore":
            command.add_argument("--storage-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "contract":
            result = Measurement.model_json_schema()
        elif args.command == "simulate":
            result = generate(args.directory, args.households, args.days, args.seed)
        else:
            settings = Settings.from_env()
            if args.command == "migrate":
                result = {"applied": migrate(settings)}
            elif args.command == "seed":
                seed_synthetic(settings, args.households, args.kind)
                result = {"seeded": args.households, "kind": args.kind}
            elif args.command == "ingest":
                result = accept(settings, args.source)
            elif args.command == "retry":
                retry(settings, args.source_id)
                result = {"requeued": args.source_id}
            elif args.command == "summarize":
                result = summarize(settings, args.grain)
            elif args.command == "freeze":
                result = freeze(settings, args.grain)
            elif args.command == "verify-dataset":
                result = verify_dataset(settings, args.dataset_id)
            elif args.command == "backup":
                result = create_backup(settings, args.directory, args.pg_bin)
            elif args.command == "restore":
                dsn = os.environ.get("UW_RESTORE_DATABASE_URL")
                if not dsn:
                    raise ValueError("UW_RESTORE_DATABASE_URL is required")
                target = replace(
                    settings, database_url=dsn, storage_root=args.storage_root.resolve()
                )
                result = restore_backup(target, args.directory, args.pg_bin)
            else:
                with connect(settings) as conn:
                    result = {
                        "jobs": list(
                            conn.execute(
                                "SELECT id,source_id,state,attempts,max_attempts,"
                                "checkpoint,error_code FROM job ORDER BY id"
                            )
                        ),
                        "quality": list(
                            conn.execute(
                                "SELECT source_id,code,count(*) AS count "
                                "FROM quality_issue GROUP BY source_id,code"
                            )
                        ),
                        "measurements": conn.execute(
                            "SELECT count(*) AS n FROM measurement"
                        ).fetchone()["n"],
                    }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        # Do not print DSNs, source content, database query parameters or participant data.
        print(
            json.dumps(
                {
                    "error_code": type(exc).__name__,
                    "hint": "check configuration, job status and documented prerequisites",
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
