"""SQLite transactions, immutable run snapshots, and audit chaining."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
import hashlib
import json
import os
import sqlite3
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:16]


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username),
    csrf TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    subject TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    locked_until REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    records_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, name)
);
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    engine TEXT NOT NULL CHECK(engine IN ('plain','ckks')),
    preset TEXT NOT NULL,
    metric TEXT NOT NULL CHECK(metric IN ('cosine','dot')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    dataset_id TEXT NOT NULL REFERENCES datasets(id),
    profile_id TEXT NOT NULL REFERENCES profiles(id),
    status TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_datasets_project ON datasets(project_id);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit(action);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES (?,?)",
                ("schema_version", "1"),
            )
            db.commit()
        if os.name == "posix":
            self.path.chmod(0o600)

    def recover_interrupted_runs(self) -> int:
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE runs SET status='failed',error=?,finished_at=? "
                "WHERE status='running'",
                ("进程中断；请复制任务重新执行。", utc_now()),
            ).rowcount
            if changed:
                self.audit(
                    db, "system", "run.recover", "runtime",
                    {"interrupted": changed},
                )
            return changed

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.read() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        with self.read() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row else None

    @staticmethod
    def audit(
        db: sqlite3.Connection,
        actor: str,
        action: str,
        entity: str,
        detail: dict,
    ) -> str:
        previous = db.execute(
            "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = previous[0] if previous else "0" * 64
        timestamp = utc_now()
        payload = {
            "timestamp": timestamp,
            "actor": actor,
            "action": action,
            "entity": entity,
            "detail": detail,
            "previous_hash": prev_hash,
        }
        entry_hash = digest(payload)
        db.execute(
            "INSERT INTO audit(timestamp,actor,action,entity,detail_json,"
            "previous_hash,entry_hash) VALUES (?,?,?,?,?,?,?)",
            (
                timestamp, actor, action, entity, canonical(detail),
                prev_hash, entry_hash,
            ),
        )
        return entry_hash

    def verify_audit(self) -> dict:
        rows = self.all("SELECT * FROM audit ORDER BY seq")
        previous = "0" * 64
        for row in rows:
            try:
                payload = {
                    "timestamp": row["timestamp"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "entity": row["entity"],
                    "detail": json.loads(row["detail_json"]),
                    "previous_hash": row["previous_hash"],
                }
                valid = row["previous_hash"] == previous
                valid = valid and digest(payload) == row["entry_hash"]
            except (ValueError, TypeError):
                valid = False
            if not valid:
                return {"ok": False, "count": len(rows), "bad_seq": row["seq"]}
            previous = row["entry_hash"]
        return {
            "ok": True,
            "count": len(rows),
            "head": previous,
            "limitation": "本地一致性校验，不是签名或外部可信时间戳。",
        }

    def backup(self, destination: Path) -> dict:
        if destination.exists():
            raise ValueError("备份文件已存在")
        with self.read() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                status = target.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                target.close()
        if status != "ok":
            destination.unlink(missing_ok=True)
            raise ValueError("数据库备份完整性检查失败")
        if os.name == "posix":
            destination.chmod(0o600)
        data = destination.read_bytes()
        return {
            "filename": destination.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "integrity": status,
            "created_at": utc_now(),
        }
