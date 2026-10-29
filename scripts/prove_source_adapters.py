"""Prove file/HTTP interchangeability through the actual CLI in a disposable test DB.

Uses only the marked project-owned PostgreSQL cluster, never resets an existing database.
Run from the repository: python scripts/prove_source_adapters.py
"""

import json
import os
import subprocess
import sys
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from urban_water.db import PROJECT_ROOT


def main():
    marker = PROJECT_ROOT / "var/dev-postgres/cluster.json"
    state = json.loads(marker.read_text(encoding="utf-8"))
    original = dotenv_values(PROJECT_ROOT / ".env").get("UW_DATABASE_URL", "")
    info = conninfo_to_dict(original)
    if (
        state.get("owner") != "urban-water-synthetic-v1"
        or info.get("host") != "127.0.0.1"
        or info.get("port") != "55432"
        or info.get("dbname") != "urban_water"
    ):
        raise ValueError("proof requires the marked project-owned synthetic cluster")
    run_id = uuid4().hex
    name = f"uw_sources_{run_id[:12]}_test"
    directory = PROJECT_ROOT / "var/proofs/source-adapters" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    env = {
        **os.environ,
        "UW_ENV": "synthetic",
        "UW_DATABASE_URL": make_conninfo(original, dbname=name),
        "UW_STORAGE_ROOT": str(directory / "storage"),
        "UW_STUDY_CODE": "source-adapter-proof",
        "UW_COHORT_CODE": "fixture",
        "UW_TIMEZONE": "Europe/Istanbul",
        "UW_CHUNK_SIZE": "2",
    }
    steps = []

    def command(module, *args):
        result = subprocess.run(
            [sys.executable, "-m", module, *args],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            raise RuntimeError(f"proof step failed: {module} {args[0] if args else ''}")
        value = json.loads(result.stdout)
        steps.append({"module": module, "command": args[0] if args else "", "result": value})
        return value

    with psycopg.connect(original, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        command("urban_water_cli.main", "migrate")
        command("urban_water_cli.main", "register", "examples/sources/registry.json")
        first = command(
            "urban_water_cli.main",
            "collect",
            "meter_readings",
            "--config",
            "configs/sources/local-demo.toml",
        )
        assert command("urban_water_worker.main", "--once")["accepted_rows"] == 3
        before = command("urban_water_cli.main", "summarize", "--grain", "day")
        expected = sum(Decimal(row["observed_liters"]) for row in before["rows"])
        assert expected == Decimal("19.75")
        # Distinct raw bytes, same events and namespace; preserve provenance without adding volume.
        raw = (PROJECT_ROOT / "examples/sources/measurements.csv").read_bytes()
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env["UW_METER_URL"] = f"http://127.0.0.1:{server.server_port}/data"
            second = command(
                "urban_water_cli.main",
                "collect",
                "meter_readings",
                "--config",
                "configs/sources/http-demo.toml",
            )
            assert second["source_id"] != first["source_id"]
            assert command("urban_water_worker.main", "--once")["accepted_rows"] == 3
            duplicate = command(
                "urban_water_cli.main",
                "collect",
                "meter_readings",
                "--config",
                "configs/sources/http-demo.toml",
            )
            assert duplicate["duplicate"] is True
            assert command("urban_water_worker.main", "--once")["state"] == "idle"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        after = command("urban_water_cli.main", "summarize", "--grain", "day")
        assert after == before
        hourly = command("urban_water_cli.main", "summarize", "--grain", "hour")
        assert sum(Decimal(r["observed_liters"]) for r in hourly["rows"]) == expected
        frozen = command("urban_water_cli.main", "freeze", "--grain", "day")
        assert command("urban_water_cli.main", "verify-dataset", frozen["dataset_id"])["verified"]
        proof = {
            "scope": "synthetic CLI + local HTTP + isolated PostgreSQL",
            "observed_liters": str(expected),
            "source_count": len(frozen["sources"]),
            "provider_api_verified": False,
            "steps": steps,
        }
        path = directory / "proof.json"
        path.write_text(json.dumps(proof, indent=2, ensure_ascii=False), encoding="utf-8")
        print(
            json.dumps(
                {"proved": True, "observed_liters": str(expected), "proof": str(path)},
                ensure_ascii=False,
            )
        )
    finally:
        # The name is generated above, CREATE succeeded, and only this script owns the DB.
        with psycopg.connect(original, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
