import argparse
import json
import threading

from urban_water.ingestion import work_once
from urban_water.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Process durable ingestion jobs")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args()
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("poll interval must be between 0.1 and 60 seconds")
    settings = Settings.from_env()
    stop = threading.Event()
    try:
        while True:
            result = work_once(settings)
            if result["state"] != "idle" or args.once:
                print(json.dumps(result), flush=True)
            if args.once:
                if result["state"] in {"failed_attempt", "lease_lost"}:
                    raise SystemExit(1)
                return
            if result["state"] in {"idle", "failed_attempt", "lease_lost"}:
                stop.wait(args.poll_seconds)
    except KeyboardInterrupt:
        print(json.dumps({"state": "stopped"}))


if __name__ == "__main__":
    main()
