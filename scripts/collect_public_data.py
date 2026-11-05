"""Download the pinned public reference library; never execute publisher code.

Raw files are immutable once receipted. Re-running verifies the cache; --offline
verifies without network access. Dataset semantics and reuse gates live in the
versioned catalog, independently of successful transport.
"""

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "research/reference_data/public-source-catalog.v1.json"
LIBRARY = ROOT / "var/data-library"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def contained_path(root, relative):
    target = (root / relative).resolve()
    if Path(relative).is_absolute() or not target.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes library: {relative}")
    return target


def digest_file(path):
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
            md5.update(block)
            size += len(block)
    return {"sha256": sha.hexdigest(), "md5": md5.hexdigest(), "bytes": size}


def validate_file(path, spec, receipt=None):
    actual = digest_file(path)
    if spec.get("bytes") is not None and actual["bytes"] != spec["bytes"]:
        raise ValueError(f"Publisher size mismatch: {path}")
    for algorithm in ("md5", "sha256"):
        if spec.get(algorithm) and actual[algorithm] != spec[algorithm]:
            raise ValueError(f"Publisher {algorithm} mismatch: {path}")
    if receipt and any(actual[key] != receipt[key] for key in actual):
        raise ValueError(f"Receipted raw file changed: {path}")
    return actual


def collect_file(source_id, spec, library=LIBRARY, offline=False):
    raw = contained_path(library, source_id + "/raw")
    path = contained_path(raw, spec["path"])
    receipt_path = path.with_name(path.name + ".receipt.json")
    if path.exists():
        receipt = (
            json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None
        )
        if receipt is None:
            raise ValueError(f"Raw file has no receipt; investigate before reuse: {path}")
        if receipt["url"] != spec["url"]:
            raise ValueError(f"Receipt URL differs from catalog: {path}")
        validate_file(path, spec, receipt)
        return {"source": source_id, "path": str(path.relative_to(library)), **receipt}
    if offline:
        raise FileNotFoundError(path)
    url = spec["url"]
    if urlsplit(url).scheme != "https":
        raise ValueError("Public downloads require HTTPS")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    limit = spec.get("max_bytes", spec.get("bytes", 200_000_000))
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "urban-water-research/0.1"})
            with urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("Refusing an insecure download redirect")
                length = response.headers.get("Content-Length")
                if length and int(length) > limit:
                    raise ValueError("Publisher content exceeds configured size budget")
                size = 0
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(block)
                    if size > limit:
                        raise ValueError("Download exceeds configured size budget")
                    output.write(block)
                final_url = response.url
                content_type = response.headers.get("Content-Type", "")
            actual = validate_file(temporary, spec)
            if path.suffix.lower() == ".zip":
                with temporary.open("rb") as check:
                    if check.read(4) != b"PK\x03\x04":
                        raise ValueError("Expected ZIP, received another payload")
            receipt = {
                "url": url,
                "resolved_url": final_url,
                "downloaded_at": datetime.now(UTC).isoformat(),
                "content_type": content_type,
                # SHA-256 pins were calculated locally during catalog review.
                # Publisher MD5 and local integrity pins are different evidence.
                "publisher_checksum_verified": bool(spec.get("md5")),
                "catalog_sha256_verified": bool(spec.get("sha256")),
                **actual,
            }
            # Refuse to overwrite originals. Single-process collection is required.
            if path.exists():
                raise FileExistsError(path)
            os.replace(temporary, path)
            write_json(receipt_path, receipt)
            return {"source": source_id, "path": str(path.relative_to(library)), **receipt}
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--output", type=Path, default=LIBRARY)
    parser.add_argument("--source", action="append", help="Repeat to select source IDs")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    sources = [s for s in catalog["sources"] if not args.source or s["id"] in args.source]
    if args.source and set(args.source) - {s["id"] for s in sources}:
        parser.error("Unknown source ID")
    files, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {
            pool.submit(collect_file, s["id"], f, args.output, args.offline): (s["id"], f)
            for s in sources
            for f in s.get("files", [])
        }
        for task in as_completed(tasks):
            source, spec = tasks[task]
            try:
                files.append(task.result())
                print(f"verified {source}/{spec['path']}", flush=True)
            except Exception as error:
                failures.append({"source": source, "path": spec["path"], "error": str(error)})
                print(f"FAILED {source}/{spec['path']}: {error}", flush=True)
    manifest = {
        "schema": "urban-water.public-library-receipt.v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "catalog_sha256": digest_file(args.catalog)["sha256"],
        "scope": args.source or "all_catalog_sources",
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "files": sorted(files, key=lambda f: f["path"]),
        "failures": failures,
    }
    manifest_path = args.output / (
        "verification.json" if args.offline else "download-manifest.json"
    )
    write_json(manifest_path, manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("files",)}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
