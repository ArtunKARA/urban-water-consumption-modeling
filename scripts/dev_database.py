"""Private local synthetic cluster. Never connects to or modifies the existing PG service.

Usage: uv run python scripts/dev_database.py start|stop|status|create-test-db|create-restore-db
"""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var" / "dev-postgres"
DATA = VAR / "data"
STATE = VAR / "cluster.json"
PORT = 55432


def binary(name: str) -> str:
    directory = os.getenv("UW_PG_BIN")
    if directory:
        candidate = Path(directory) / (name + (".exe" if os.name == "nt" else ""))
    elif os.name == "nt":
        candidates = sorted(
            Path("C:/Program Files/PostgreSQL").glob(f"*/bin/{name}.exe"), reverse=True
        )
        candidate = candidates[0] if candidates else Path(name)
    else:
        candidate = Path(shutil.which(name) or name)
    if not candidate.is_file():
        raise ValueError(f"set UW_PG_BIN to a directory containing {name}")
    return str(candidate)


def run(name: str, *args: str, check=True):
    # On Windows a daemon can inherit a pipe handle and keep communicate() blocked.
    # Real file handles let pg_ctl return as soon as readiness is confirmed.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run([binary(name), *args], check=check, stdout=output, stderr=output)
        output.seek(0)
        return subprocess.CompletedProcess(
            result.args, result.returncode, stdout=output.read().decode(errors="replace")
        )


def start() -> None:
    VAR.mkdir(parents=True, exist_ok=True)
    if not STATE.exists():
        if DATA.exists() or (ROOT / ".env").exists():
            raise ValueError(
                "existing data or .env has no project cluster marker; refusing overwrite"
            )
        password = secrets.token_urlsafe(32)
        fd, name = tempfile.mkstemp(dir=VAR, suffix=".pw")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(password)
            run(
                "initdb",
                "-D",
                str(DATA),
                "-U",
                "urban_water",
                "--encoding=UTF8",
                "--locale=C",
                "--auth-local=scram-sha-256",
                "--auth-host=scram-sha-256",
                "--pwfile",
                name,
            )
        finally:
            Path(name).unlink(missing_ok=True)
        STATE.write_text(
            json.dumps({"owner": "urban-water-synthetic-v1", "port": PORT, "password": password}),
            encoding="utf-8",
        )
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state["owner"] != "urban-water-synthetic-v1" or state["port"] != PORT:
        raise ValueError("cluster marker mismatch")
    if run("pg_ctl", "-D", str(DATA), "status", check=False).returncode != 0:
        run(
            "pg_ctl",
            "-D",
            str(DATA),
            "-l",
            str(VAR / "postgres.log"),
            "-o",
            f"-h 127.0.0.1 -p {PORT} -c max_connections=25",
            "-w",
            "start",
        )
    dsn = f"postgresql://urban_water:{quote(state['password'])}@127.0.0.1:{PORT}/urban_water"
    # Password is never passed on the process command line or printed.
    import psycopg
    from psycopg import sql

    with psycopg.connect(dsn.rsplit("/", 1)[0] + "/postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='urban_water'").fetchone():
            conn.execute(
                sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier("urban_water"))
            )
    env_file = ROOT / ".env"
    if not env_file.exists():
        env_file.write_text(
            f"UW_ENV=synthetic\nUW_DATABASE_URL={dsn}\n"
            "UW_STORAGE_ROOT=var/synthetic\nUW_TIMEZONE=Europe/Istanbul\n",
            encoding="utf-8",
        )
    print(f"Synthetic PostgreSQL ready on 127.0.0.1:{PORT}; credentials in ignored .env")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["start", "stop", "status", "create-test-db", "create-restore-db"]
    )
    action = parser.parse_args().action
    if action == "start":
        start()
        return
    if not STATE.exists():
        raise ValueError("no project-owned cluster marker")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if state["owner"] != "urban-water-synthetic-v1":
        raise ValueError("not a project-owned cluster")
    if action in {"stop", "status"}:
        args = ["-D", str(DATA), action]
        if action == "stop":
            args += ["-m", "fast", "-w"]
        result = run("pg_ctl", *args, check=False)
        print(result.stdout)
        raise SystemExit(result.returncode)
    import psycopg
    from psycopg import sql

    name = "urban_water_test" if action == "create-test-db" else "urban_water_restore"
    with psycopg.connect(
        host="127.0.0.1",
        port=PORT,
        dbname="postgres",
        user="urban_water",
        password=state["password"],
        autocommit=True,
    ) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    print(f"{name} ready (existing contents preserved)")


if __name__ == "__main__":
    main()
