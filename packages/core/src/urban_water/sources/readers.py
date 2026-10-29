"""Bounded raw acquisition. Readers do not parse, publish or call models."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from urban_water.sources.config import SourceSpec


class AcquisitionError(ValueError):
    pass


class SourceReader(Protocol):
    version: str

    def acquire(self, spec: SourceSpec, max_bytes: int) -> AbstractContextManager[Path]: ...


class LocalFileReader:
    version = "local-file/1"

    @contextmanager
    def acquire(self, spec: SourceSpec, max_bytes: int) -> Iterator[Path]:
        if not spec.path:
            raise AcquisitionError("local path must be resolved before acquisition")
        path = Path(spec.path)
        if not path.is_file() or path.stat().st_size > max_bytes:
            raise AcquisitionError("source is missing or exceeds the byte budget")
        yield path


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpFileReader:
    version = "http-file/1"

    @contextmanager
    def acquire(self, spec: SourceSpec, max_bytes: int) -> Iterator[Path]:
        url = os.environ.get(spec.url_env or "")
        try:
            parts = urlsplit(url or "")
            permitted = parts.scheme == "https" or (
                parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
            )
            if (
                not permitted
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.fragment
            ):
                raise AcquisitionError("use HTTPS; HTTP is allowed only for loopback tests")
        except ValueError:
            raise AcquisitionError("invalid source URL") from None
        headers = {"Accept-Encoding": "identity"}
        if spec.token_env:
            token = os.environ.get(spec.token_env)
            if not token or "\r" in token or "\n" in token:
                raise AcquisitionError("missing or invalid source token")
            headers["Authorization"] = f"Bearer {token}"
        with tempfile.TemporaryDirectory(prefix="uw-source-") as directory:
            path = Path(directory) / "response.bin"
            try:
                request = Request(url, headers=headers)
                with build_opener(_NoRedirect()).open(
                    request, timeout=spec.timeout_seconds
                ) as response:
                    if (
                        response.status != 200
                        or response.headers.get("Content-Encoding", "identity") != "identity"
                    ):
                        raise AcquisitionError("expected one uncompressed complete HTTP object")
                    length = response.headers.get("Content-Length")
                    if length is not None and (int(length) < 0 or int(length) > max_bytes):
                        raise AcquisitionError("HTTP source exceeds the byte budget")
                    size = 0
                    with path.open("xb") as output:
                        while block := response.read(min(65536, max_bytes - size + 1)):
                            size += len(block)
                            if size > max_bytes:
                                raise AcquisitionError("HTTP source exceeds the byte budget")
                            output.write(block)
                    if length is not None and size != int(length):
                        raise AcquisitionError("incomplete HTTP source")
            except (URLError, OSError, ValueError) as exc:
                if isinstance(exc, AcquisitionError):
                    raise
                # URLs, query tokens, authorization and response bodies must not enter logs.
                raise AcquisitionError("HTTP acquisition failed") from None
            yield path


def get_reader(name: str) -> SourceReader:
    readers = {"local_file_v1": LocalFileReader, "http_file_v1": HttpFileReader}
    if name not in readers:
        raise ValueError("unsupported reader version")
    return readers[name]()
