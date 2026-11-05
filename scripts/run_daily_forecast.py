"""Run the predeclared daily benchmark on a frozen canonical hourly snapshot."""

import argparse
import json
from pathlib import Path

from urban_water.db import PROJECT_ROOT
from urban_water_research.daily_benchmark import run_benchmark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Canonical hourly observed_volume.parquet")
    parser.add_argument(
        "--protocol", type=Path, default=PROJECT_ROOT / "research/experiments/aalborg-daily-v1.toml"
    )
    parser.add_argument(
        "--output-root", type=Path, default=PROJECT_ROOT / "var/experiments/aalborg-daily-v1"
    )
    args = parser.parse_args()
    path = args.input
    if path is None:
        imported = json.loads(
            (PROJECT_ROOT / "var/research/aalborg/v1/import-report.json").read_text(
                encoding="utf-8"
            )
        )
        path = Path(imported["outputs"]["hour"])
    print(
        json.dumps(
            run_benchmark(path, args.protocol, args.output_root),
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
