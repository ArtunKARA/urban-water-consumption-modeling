"""Content-addressed original bytes. Files become durable before DB acceptance."""

import gzip
import hashlib
import os
import tempfile
from pathlib import Path

from urban_water.settings import Settings


class ObjectCorrupt(ValueError):
    pass


class FileStore:
    def __init__(self, settings: Settings):
        self.root = settings.storage_root.resolve()
        self.max_bytes = settings.max_source_bytes

    def path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("object key escapes storage root")
        return path

    def verify(self, key: str, digest: str, size: int) -> None:
        sha = hashlib.sha256()
        count = 0
        with gzip.open(self.path(key), "rb") as stream:
            while block := stream.read(1024 * 1024):
                count += len(block)
                if count > self.max_bytes:
                    raise ObjectCorrupt("source exceeds configured limit")
                sha.update(block)
        if sha.hexdigest() != digest or count != size:
            raise ObjectCorrupt("source checksum or size mismatch")

    def put(self, source: Path) -> tuple[str, int, str]:
        staging = self.root / "receiving"
        staging.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=staging, suffix=".tmp")
        temporary = Path(name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as output:
                with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as zipped:
                    with source.open("rb") as incoming:
                        while block := incoming.read(1024 * 1024):
                            size += len(block)
                            if size > self.max_bytes:
                                raise ValueError("source exceeds configured limit")
                            digest.update(block)
                            zipped.write(block)
                output.flush()
                os.fsync(output.fileno())
            sha = digest.hexdigest()
            key = f"sources/{sha[:2]}/{sha}.gz"
            destination = self.path(key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Concurrent copies have identical original bytes and deterministic compression.
            if destination.exists():
                self.verify(key, sha, size)
            else:
                os.replace(temporary, destination)
            return sha, size, key
        finally:
            temporary.unlink(missing_ok=True)
