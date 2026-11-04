"""Download, audit and ingest the pinned Aalborg reference into its own persistent DB.

Run python scripts/run_aalborg.py [--prepare-only] [--offline]. Does not reset any DB.
Requires the project-owned local PostgreSQL cluster for the ingestion phase.
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from urban_water.datasets import freeze, verify_dataset
from urban_water.db import PROJECT_ROOT, connect, migrate
from urban_water.ingestion import retry, work_once
from urban_water.processing import summarize
from urban_water.settings import Settings
from urban_water.sources import collect
from urban_water.sources.aalborg_reference import prepare_reference, write_bytes, write_json
from urban_water.sources.registration import register_file

DIRECTORY = PROJECT_ROOT / "var/research/aalborg/v1"
DATABASE = "urban_water_aalborg_research"


def environment(directory: Path) -> Settings:
    cluster = json.loads(
        (PROJECT_ROOT / "var/dev-postgres/cluster.json").read_text(encoding="utf-8")
    )
    original = dotenv_values(PROJECT_ROOT / ".env").get("UW_DATABASE_URL", "")
    info = conninfo_to_dict(original)
    if (
        cluster.get("owner") != "urban-water-synthetic-v1"
        or info.get("host") != "127.0.0.1"
        or info.get("port") != "55432"
        or info.get("dbname") != "urban_water"
    ):
        raise ValueError("runner requires the marked project-owned PostgreSQL cluster")
    marker = directory / "environment.json"
    declaration = {
        "owner": "urban-water-aalborg-reference-v1",
        "database": DATABASE,
        "storage_root": str(directory / "storage"),
        "environment": "public_research",
    }
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != declaration:
        raise ValueError("reference environment ownership marker differs")
    with psycopg.connect(original, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(83621003)")
        try:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (DATABASE,)
            ).fetchone()
            if exists and not marker.exists():
                raise ValueError("refusing to adopt an existing unmarked research database")
            if not exists:
                conn.execute(
                    sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(DATABASE))
                )
            write_json(marker, declaration, immutable=True)
        finally:
            conn.execute("SELECT pg_advisory_unlock(83621003)")
    settings = Settings(
        database_url=make_conninfo(original, dbname=DATABASE),
        storage_root=directory / "storage",
        environment="public_research",
        study_code="aalborg-15180141",
        cohort_code="daily-2022",
        timezone="Europe/Copenhagen",
    )
    values = {
        "UW_ENV": settings.environment,
        "UW_DATABASE_URL": settings.database_url,
        "UW_STORAGE_ROOT": str(settings.storage_root),
        "UW_STUDY_CODE": settings.study_code,
        "UW_COHORT_CODE": settings.cohort_code,
        "UW_TIMEZONE": settings.timezone,
    }
    content = "# Private local research environment. Do not commit.\n"
    content += "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items())
    write_bytes(directory / "research.env", content.encode())
    return settings


def run(directory: Path = DIRECTORY, *, offline=False, prepare_only=False) -> dict:
    directory = directory.resolve()
    prepared = prepare_reference(directory, offline=offline)
    if prepare_only:
        return {
            "prepared": True,
            "directory": str(directory),
            "source_rows": prepared["audit"]["source_rows"],
            "households": prepared["audit"]["exported_buildings"],
            "total_liters": prepared["audit"]["source_total_liters"],
            "source_config": prepared["source_config"],
            "audit": str(directory / "source-audit.json"),
        }
    settings = environment(directory)
    applied = migrate(settings)
    registered = register_file(settings, Path(prepared["registry"]))
    accepted = collect(settings, Path(prepared["source_config"]), "meter_readings")
    with connect(settings) as conn:
        job = conn.execute(
            "SELECT j.*,s.state AS source_state FROM job j JOIN source s "
            "ON s.id=j.source_id WHERE j.source_id=%s",
            (accepted["source_id"],),
        ).fetchone()
    if job["source_state"] != "published":
        if job["state"] == "failed":
            retry(settings, accepted["source_id"])
        outcome = work_once(settings)
        if outcome["state"] != "succeeded":
            raise RuntimeError(f"reference ingestion incomplete: {outcome['state']}")
    with connect(settings) as conn:
        counts = conn.execute(
            "SELECT count(*) AS records,count(*) FILTER (WHERE "
            "received_at IS NOT NULL) AS receipt_records FROM provenance WHERE source_id=%s",
            (accepted["source_id"],),
        ).fetchone()
        quality = conn.execute(
            "SELECT count(*) AS n FROM quality_issue WHERE source_id=%s", (accepted["source_id"],)
        ).fetchone()["n"]
    audit = prepared["audit"]
    if counts["records"] != audit["source_rows"] or quality:
        raise ValueError("source-side row inventory and published records differ")
    before = summarize(settings, "day")
    hourly = summarize(settings, "hour")
    expected = Decimal(audit["source_total_liters"])
    for report in (before, hourly):
        if (
            sum(Decimal(r["observed_liters"]) for r in report["rows"]) != expected
            or Decimal(report["unallocated_liters"]) != 0
        ):
            raise ValueError("source volume was lost or duplicated during aggregation")
        totals = {}
        for row in report["rows"]:
            alias = row["household_code"]
            totals[alias] = totals.get(alias, Decimal(0)) + Decimal(row["observed_liters"])
        for item in audit["buildings"].values():
            if totals.get(item["household_alias"]) != Decimal(item["total_liters"]):
                raise ValueError("household mapping changed source totals")
    duplicate = collect(settings, Path(prepared["source_config"]), "meter_readings")
    if duplicate != {"source_id": accepted["source_id"], "duplicate": True}:
        raise ValueError("repeated reference import was not idempotent")
    if summarize(settings, "day") != before:
        raise ValueError("duplicate delivery changed observed volumes")
    daily_dataset = freeze(settings, "day")
    hourly_dataset = freeze(settings, "hour")
    for dataset in (daily_dataset, hourly_dataset):
        if not verify_dataset(settings, dataset["dataset_id"])["verified"]:
            raise ValueError("reference dataset verification failed")
    report = {
        "reference": "doi:10.5281/zenodo.15180141",
        "environment": settings.environment,
        "database": DATABASE,
        "source_id": accepted["source_id"],
        "source_rows": counts["records"],
        "households": len(audit["buildings"]),
        "total_liters": str(expected),
        "daily_rows": len(before["rows"]),
        "hourly_rows": len(hourly["rows"]),
        "quarantined_rows": quality,
        "unallocated_liters": before["unallocated_liters"],
        "duplicate_verified": True,
        "dataset_verified": True,
        "applied_migrations": applied,
        "registered": registered,
        "value_semantics": "upstream_processed_volume",
        "receipt_basis": "research_archive_import",
        "coverage_status": "unknown",
        "trained": False,
        "datasets": {"day": daily_dataset["dataset_id"], "hour": hourly_dataset["dataset_id"]},
        "outputs": {
            "day": str(Path(daily_dataset["directory"]) / "observed_volume.parquet"),
            "hour": str(Path(hourly_dataset["directory"]) / "observed_volume.parquet"),
        },
        "source_config": prepared["source_config"],
        "registry": prepared["registry"],
        "audit": str(directory / "source-audit.json"),
        "environment_file": str(directory / "research.env"),
    }
    write_json(directory / "import-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    result = run(offline=args.offline, prepare_only=args.prepare_only)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
