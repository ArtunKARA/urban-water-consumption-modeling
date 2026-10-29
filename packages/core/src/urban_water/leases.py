"""Fenced job leases, including long checksum and checkpoint-replay I/O."""

import threading
from contextlib import contextmanager

import psycopg

from urban_water.db import connect
from urban_water.settings import Settings


class LostLease(RuntimeError):
    pass


def owned(conn, job: dict) -> None:
    row = conn.execute(
        """SELECT id FROM job WHERE id=%s AND token=%s AND state='running'
        AND lease_until > clock_timestamp() FOR UPDATE""",
        (job["id"], job["token"]),
    ).fetchone()
    if row is None:
        raise LostLease("worker no longer owns the job")


def renew(settings: Settings, job: dict) -> bool:
    with connect(settings) as conn:
        # A chunk/publication holds this row until commit. Never wait behind it or an
        # unrelated new owner, and never resurrect an expired/replaced token.
        return (
            conn.execute(
                """WITH candidate AS (
            SELECT id FROM job WHERE id=%s AND token=%s AND state='running'
            AND lease_until > clock_timestamp() FOR UPDATE SKIP LOCKED)
            UPDATE job SET lease_until=clock_timestamp()+%s*interval '1 second'
            FROM candidate WHERE job.id=candidate.id RETURNING job.id""",
                (job["id"], job["token"], settings.lease_seconds),
            ).fetchone()
            is not None
        )


@contextmanager
def keep_lease(settings: Settings, job: dict):
    if not renew(settings, job):
        raise LostLease("worker no longer owns the job")
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(min(10, settings.lease_seconds / 4)):
            try:
                renew(settings, job)
            except psycopg.Error:
                # Main processing handles DB recovery. A failed heartbeat cannot extend
                # ownership after its expiry; all writers still check the fenced token.
                continue

    thread = threading.Thread(target=heartbeat, name="uw-job-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=6)
