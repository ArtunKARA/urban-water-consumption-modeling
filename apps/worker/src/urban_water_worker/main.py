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
    run_worker(settings, args.once, args.poll_seconds)


def run_worker(
    settings: Settings,
    once: bool = False,
    poll_seconds: float = 1,
    stop: threading.Event | None = None,
) -> None:
    stop = stop or threading.Event()
    reconnect_delay = poll_seconds
    try:
        while not stop.is_set():
            result = work_once(settings)
            if result["state"] != "idle" or once:
                print(json.dumps(result), flush=True)
            if once:
                if result["state"] in {"failed_attempt", "lease_lost", "connection_unavailable"}:
                    raise SystemExit(1)
                return
            if result["state"] == "connection_unavailable":
                stop.wait(reconnect_delay)
                reconnect_delay = min(30, reconnect_delay * 2)
            else:
                reconnect_delay = poll_seconds
                if result["state"] in {"idle", "failed_attempt", "lease_lost"}:
                    stop.wait(poll_seconds)
    except KeyboardInterrupt:
        print(json.dumps({"state": "stopped"}))


if __name__ == "__main__":
    main()
