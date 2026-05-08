from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sites (
                    site_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL,
                    namespace TEXT NOT NULL UNIQUE,
                    domain TEXT NOT NULL,
                    www_mode TEXT NOT NULL,
                    base_domain TEXT NOT NULL,
                    status TEXT NOT NULL,
                    admin_user TEXT NOT NULL,
                    admin_email TEXT NOT NULL,
                    admin_password TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    site_id TEXT,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    return_code INTEGER,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(site_id) REFERENCES sites(site_id)
                );

                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    line TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                """
            )

    @staticmethod
    def utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    def insert_site(self, site: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sites (
                    site_id, slug, namespace, domain, www_mode, base_domain,
                    status, admin_user, admin_email, admin_password, created_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site["site_id"],
                    site["slug"],
                    site["namespace"],
                    site["domain"],
                    site["www_mode"],
                    site["base_domain"],
                    site["status"],
                    site["admin_user"],
                    site["admin_email"],
                    site["admin_password"],
                    site["created_at"],
                    site.get("deleted_at"),
                ),
            )

    def update_site_status(self, site_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE sites SET status=? WHERE site_id=?", (status, site_id))

    def soft_delete_site(self, site_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sites SET status=?, deleted_at=? WHERE site_id=?",
                (status, self.utcnow(), site_id),
            )

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()
        return dict(row) if row else None

    def get_site_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sites WHERE slug=? AND deleted_at IS NULL", (slug,)
            ).fetchone()
        return dict(row) if row else None

    def list_sites(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sites WHERE deleted_at IS NULL ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def insert_job(self, job: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, site_id, type, status, return_code, created_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["job_id"],
                    job.get("site_id"),
                    job["type"],
                    job["status"],
                    job.get("return_code"),
                    job["created_at"],
                    job.get("finished_at"),
                ),
            )

    def update_job(self, job_id: str, status: str, return_code: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, return_code=?, finished_at=? WHERE job_id=?",
                (status, return_code, self.utcnow(), job_id),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                return None
            logs = conn.execute(
                "SELECT created_at, line FROM job_logs WHERE job_id=? ORDER BY id ASC", (job_id,)
            ).fetchall()
        job = dict(row)
        job["logs"] = [dict(l) for l in logs]
        return job

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def append_job_log(self, job_id: str, line: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO job_logs (job_id, created_at, line) VALUES (?, ?, ?)",
                (job_id, self.utcnow(), line),
            )
