"""Durable state for the platform: stdlib sqlite3 in WAL mode plus npz model artifacts.

What lives here: registry versions and their history, experiment counters, bandit arms, the
serving windows the rollout guard reads, impressions and feedback, and the alert outbox. Model
artifacts are plain numeric arrays in .npz files, loaded with allow_pickle=False, and each one is
checked against the SHA-256 recorded next to it before use. There is no pickle anywhere.

Passing no directory gives an in-memory store with the same behaviour and no files.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sifa.core.errors import SifaError

SCHEMA_VERSION = 1
DB_NAME = "sifa-state.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
    name TEXT NOT NULL, version INTEGER NOT NULL, stage TEXT NOT NULL, traffic REAL NOT NULL,
    metrics TEXT NOT NULL, created_at TEXT NOT NULL, card TEXT NOT NULL,
    artifact TEXT, sha256 TEXT,
    PRIMARY KEY (name, version)
);
CREATE TABLE IF NOT EXISTS history (
    name TEXT NOT NULL, version INTEGER NOT NULL, seq INTEGER NOT NULL,
    at TEXT NOT NULL, stage TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,
    PRIMARY KEY (name, version, seq)
);
CREATE TABLE IF NOT EXISTS window_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, win TEXT NOT NULL, request_id TEXT,
    clicked INTEGER NOT NULL, probability REAL NOT NULL, latency_ms REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS window_events_request ON window_events (request_id);
CREATE TABLE IF NOT EXISTS impressions (
    request_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, at TEXT NOT NULL,
    model_version INTEGER NOT NULL, stage TEXT NOT NULL, variant TEXT NOT NULL,
    items TEXT NOT NULL, outcome_source TEXT NOT NULL, credited INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS feedback (
    request_id TEXT NOT NULL, item_id TEXT NOT NULL, clicked INTEGER NOT NULL,
    at TEXT NOT NULL, actor TEXT NOT NULL,
    PRIMARY KEY (request_id, item_id)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
    message TEXT NOT NULL, payload TEXT NOT NULL, delivered_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
);
"""


class ArtifactError(SifaError):
    """An artifact is missing, altered or unreadable. It is never loaded in that case."""


@dataclass(frozen=True, slots=True)
class StoredImpression:
    request_id: str
    user_id: str
    model_version: int
    stage: str
    variant: str
    items: tuple[str, ...]
    outcome_source: str
    credited: bool


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Store:
    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self._lock = threading.RLock()
        if directory is None:
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            (directory / "artifacts").mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(
                directory / DB_NAME, check_same_thread=False, timeout=30.0
            )
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            row = self._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(row["value"]) > SCHEMA_VERSION:
                raise SifaError(
                    f"state was written by a newer schema ({row['value']}); refusing to open it"
                )

    @property
    def schema_version(self) -> int:
        return int(self.get_meta("schema_version") or 0)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._db:
            yield self._db

    # -- meta and key/value ------------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def put(self, key: str, value: Any) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))

    # -- artifacts ---------------------------------------------------------------------------
    def save_artifact(
        self, filename: str, arrays: dict[str, np.ndarray]
    ) -> tuple[str, str] | None:
        """Write arrays atomically; returns (filename, sha256), or None for an in-memory store."""
        if self.directory is None:
            return None
        target = self.directory / "artifacts" / filename
        scratch = target.with_suffix(f".tmp{os.getpid()}.npz")
        np.savez(scratch, **arrays)  # type: ignore[arg-type]
        os.replace(scratch, target)
        return filename, file_sha256(target)

    def load_artifact(self, filename: str, sha256: str) -> dict[str, np.ndarray]:
        if self.directory is None:
            raise ArtifactError("an in-memory store holds no artifacts")
        path = self.directory / "artifacts" / filename
        if not path.is_file():
            raise ArtifactError(f"artifact {filename} is missing")
        if file_sha256(path) != sha256:
            raise ArtifactError(f"artifact {filename} does not match its recorded hash; refused")
        with np.load(path, allow_pickle=False) as archive:
            return {name: archive[name] for name in archive.files}

    # -- registry ----------------------------------------------------------------------------
    def save_version(self, version: Any, artifact: tuple[str, str] | None) -> None:
        with self.transaction() as db:
            previous = db.execute(
                "SELECT artifact, sha256 FROM versions WHERE name=? AND version=?",
                (version.name, version.version),
            ).fetchone()
            file, digest = artifact or (
                (previous["artifact"], previous["sha256"]) if previous else (None, None)
            )
            db.execute(
                "INSERT OR REPLACE INTO versions VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    version.name, version.version, version.stage.value, version.traffic,
                    json.dumps(version.metrics), version.created_at.isoformat(),
                    json.dumps(version.card, default=str), file, digest,
                ),
            )
            db.execute(
                "DELETE FROM history WHERE name=? AND version=?", (version.name, version.version)
            )
            db.executemany(
                "INSERT INTO history VALUES (?,?,?,?,?,?,?)",
                [
                    (version.name, version.version, seq, at.isoformat(), stage.value, reason, actor)
                    for seq, ((at, stage, reason), actor) in enumerate(
                        zip(version.history, version.actors, strict=True)
                    )
                ],
            )

    def has_artifact(self, name: str, version: int) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT artifact FROM versions WHERE name=? AND version=?", (name, version)
            ).fetchone()
        return bool(row and row["artifact"])

    def load_versions(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM versions WHERE name=? ORDER BY version", (name,)
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                history = self._db.execute(
                    "SELECT at, stage, reason, actor FROM history WHERE name=? AND version=? "
                    "ORDER BY seq",
                    (name, row["version"]),
                ).fetchall()
                out.append({**dict(row), "history": [dict(h) for h in history]})
        return out

    # -- serving windows ---------------------------------------------------------------------
    def add_window_event(
        self, window: str, request_id: str | None, clicked: bool, probability: float, latency: float
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO window_events (win, request_id, clicked, probability, latency_ms) "
                "VALUES (?,?,?,?,?)",
                (window, request_id, int(clicked), probability, latency),
            )

    def credit_window_click(self, request_id: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE window_events SET clicked=1 WHERE request_id=?", (request_id,))

    def clear_window(self, window: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM window_events WHERE win=?", (window,))

    def promote_window(self) -> None:
        """The canary window becomes the live one."""
        with self.transaction() as db:
            db.execute("DELETE FROM window_events WHERE win='live'")
            db.execute("UPDATE window_events SET win='live' WHERE win='canary'")

    def load_window(self, window: str, limit: int) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._db.execute(
                "SELECT request_id, clicked, probability, latency_ms FROM window_events "
                "WHERE win=? ORDER BY id DESC LIMIT ?",
                (window, limit),
            ).fetchall()
        return list(reversed(rows))

    def trim_windows(self, keep: int) -> None:
        with self.transaction() as db:
            for window in ("live", "canary"):
                db.execute(
                    "DELETE FROM window_events WHERE win=? AND id NOT IN "
                    "(SELECT id FROM window_events WHERE win=? ORDER BY id DESC LIMIT ?)",
                    (window, window, keep),
                )

    # -- impressions and feedback ------------------------------------------------------------
    def add_impression(
        self, request_id: str, user_id: str, at: str, model_version: int, stage: str,
        variant: str, items: list[str], outcome_source: str,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO impressions (request_id, user_id, at, model_version, stage, variant,"
                " items, outcome_source) VALUES (?,?,?,?,?,?,?,?)",
                (request_id, user_id, at, model_version, stage, variant, json.dumps(items),
                 outcome_source),
            )

    def get_impression(self, request_id: str) -> StoredImpression | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM impressions WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        return StoredImpression(
            row["request_id"], row["user_id"], row["model_version"], row["stage"], row["variant"],
            tuple(json.loads(row["items"])), row["outcome_source"], bool(row["credited"]),
        )

    def mark_credited(self, request_id: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE impressions SET credited=1 WHERE request_id=?", (request_id,))

    def add_feedback(
        self, request_id: str, item_id: str, clicked: bool, at: str, actor: str
    ) -> bool:
        """True when this (request, item) pair is new; a repeat changes nothing."""
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO feedback VALUES (?,?,?,?,?)",
                (request_id, item_id, int(clicked), at, actor),
            )
            return cursor.rowcount == 1

    def feedback_summary(self) -> dict[str, int]:
        with self._lock:
            imp = self._db.execute("SELECT COUNT(*) AS n FROM impressions").fetchone()["n"]
            fb = self._db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(clicked),0) AS c FROM feedback"
            ).fetchone()
        return {"impressions": imp, "feedback_events": fb["n"], "clicks": fb["c"]}

    def prune_impressions(self, older_than_iso: str) -> int:
        with self.transaction() as db:
            db.execute(
                "DELETE FROM feedback WHERE request_id IN "
                "(SELECT request_id FROM impressions WHERE at < ?)", (older_than_iso,)
            )
            return db.execute("DELETE FROM impressions WHERE at < ?", (older_than_iso,)).rowcount

    # -- alerts ------------------------------------------------------------------------------
    def add_alert(self, at: str, kind: str, message: str, payload: dict[str, Any]) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO alerts (at, kind, message, payload) VALUES (?,?,?,?)",
                (at, kind, message, json.dumps(payload, default=str)),
            )
            return int(cursor.lastrowid or 0)

    def pending_alerts(self, max_attempts: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM alerts WHERE delivered_at IS NULL AND attempts < ? ORDER BY id",
                (max_attempts,),
            ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def mark_alert(self, alert_id: int, delivered_at: str | None) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE alerts SET attempts = attempts + 1, delivered_at = ? WHERE id=?",
                (delivered_at, alert_id),
            )

    def all_alerts(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def archive(self) -> None:
        """Move a stale state directory's database aside so a fresh one can be built."""
        if self.directory is None:
            return
        self.close()
        stamp = time.strftime("%Y%m%dT%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            path = self.directory / f"{DB_NAME}{suffix}"
            if path.exists():
                os.replace(path, self.directory / f"{DB_NAME}{suffix}.stale-{stamp}")
