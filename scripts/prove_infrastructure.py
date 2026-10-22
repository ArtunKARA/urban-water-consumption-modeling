"""Run public CLI -> worker -> notebook -> backup -> restore against disposable databases.

Keeps proof files under var/proofs; only newly-created proof databases are removed.
Requires the notebooks extra and CREATEDB permission in the synthetic development cluster.
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import nbformat
import psycopg
from jupyter_client import KernelManager
from nbclient import NotebookClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from urban_water.datasets import code_sha
from urban_water.db import PROJECT_ROOT
from urban_water.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    run_id = uuid4().hex[:12]
    directory = PROJECT_ROOT / "var" / "proofs" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    dbnames = [f"uw_proof_{run_id}_test", f"uw_proof_{run_id}_restore"]
    created = []
    steps = []

    def command(name: str, module: str, args: list[str], env: dict) -> dict:
        started = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", module, *args],
            env=env,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError(f"proof step failed: {name}; exit={result.returncode}")
        value = json.loads(result.stdout)
        steps.append({"step": name, "seconds": round(time.perf_counter() - started, 4)})
        return value

    try:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            version = conn.info.server_version
            for name in dbnames:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created.append(name)
        primary = replace(
            settings,
            database_url=make_conninfo(settings.database_url, dbname=dbnames[0]),
            storage_root=directory / "source-storage",
        )
        env = {
            **os.environ,
            "UW_ENV": "synthetic",
            "UW_DATABASE_URL": primary.database_url,
            "UW_STORAGE_ROOT": str(primary.storage_root),
            "PYTHONIOENCODING": "utf-8",
            "UW_STUDY_CODE": "synthetic-pilot",
            "UW_COHORT_CODE": "fixture",
        }

        def cli(name: str, *args: str, override=None):
            return command(name, "urban_water_cli.main", list(args), override or env)

        cli("migrate", "migrate")
        cli("seed", "seed")
        fixture = cli("simulate", "simulate", str(directory / "fixture"))
        accepted = cli("accept", "ingest", fixture["source"])
        worker = command("worker", "urban_water_worker.main", ["--once"], env)
        assert worker["accepted_rows"] == 2208 and worker["state"] == "succeeded"
        duplicate = cli("duplicate", "ingest", fixture["source"])
        assert duplicate["duplicate"] and duplicate["source_id"] == accepted["source_id"]
        daily = cli("daily", "summarize", "--grain", "day")
        hourly = cli("hourly", "summarize", "--grain", "hour")
        expected = [Decimal("54"), Decimal("54"), Decimal("56.4"), Decimal("56.4")]
        assert [Decimal(r["observed_liters"]) for r in daily["rows"]] == expected
        assert len(hourly["rows"]) == 96
        assert sum(Decimal(r["observed_liters"]) for r in hourly["rows"]) == Decimal("220.8")
        frozen = cli("freeze", "freeze")
        cli("verify-dataset", "verify-dataset", frozen["dataset_id"])

        started = time.perf_counter()
        notebook = nbformat.read(PROJECT_ROOT / "notebooks/01_observed_volume.ipynb", as_version=4)
        manager = KernelManager(kernel_name="python3")
        manager.kernel_spec.argv = [
            sys.executable,
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ]
        try:
            NotebookClient(
                notebook,
                timeout=60,
                km=manager,
                resources={"metadata": {"path": str(PROJECT_ROOT)}},
            ).execute(env=env)
        finally:
            if manager.has_kernel:
                manager.shutdown_kernel(now=True)
        nbformat.write(notebook, directory / "executed_notebook.ipynb")
        steps.append({"step": "notebook", "seconds": round(time.perf_counter() - started, 4)})

        cli("backup", "backup", str(directory / "backup"))
        restore_dsn = make_conninfo(settings.database_url, dbname=dbnames[1])
        restore_env = {**env, "UW_RESTORE_DATABASE_URL": restore_dsn}
        cli(
            "restore",
            "restore",
            str(directory / "backup"),
            "--storage-root",
            str(directory / "restored-storage"),
            override=restore_env,
        )
        restored_env = {
            **env,
            "UW_DATABASE_URL": restore_dsn,
            "UW_STORAGE_ROOT": str(directory / "restored-storage"),
        }
        assert cli("restored-daily", "summarize", override=restored_env) == daily
        cli("restored-dataset", "verify-dataset", frozen["dataset_id"], override=restored_env)
        report = {
            "status": "passed",
            "run_id": run_id,
            "environment": "synthetic",
            "completed_at": datetime.now(UTC).isoformat(),
            "postgres_version": version,
            "code_sha256": code_sha(),
            "pulse_count": 2208,
            "observed_liters": "220.8",
            "daily_rows": 4,
            "hourly_rows": 96,
            "duplicate_did_not_add_volume": True,
            "notebook_executed": True,
            "restore_results_equal": True,
            "dataset_id": frozen["dataset_id"],
            "steps": steps,
            "scope": "first infrastructure package; no model performance or capacity claim",
            "cleanup": "only newly-created proof databases removed; proof files retained",
        }
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"status": "passed", "report": str(directory / "report.json")}, indent=2))
    finally:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            for name in reversed(created):
                conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
