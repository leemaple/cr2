"""Application use cases with transactional state and explicit boundaries."""
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any
import json
import sqlite3

from .database import Database, canonical, digest, new_id, utc_now
from .engines import execute_snapshot, validate_profile, engine_status
from .schemas import (
    ProjectCreate, ProjectUpdate, DatasetCreate, DatasetGenerate,
    DatasetImport, ProfileCreate, RunCreate, RunClone,
)
from .vectors import (
    validate_records, parse_json, parse_csv, generate_records,
    dataset_statistics, finite_vector, prepare,
)


class DomainError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def require(row: Any, message: str = "记录不存在") -> Any:
    if row is None:
        raise DomainError(message, 404)
    return row


class Workbench:
    def __init__(self, db: Database, backups: Path):
        self.db = db
        self.backups = backups
        self.execution_lock = BoundedSemaphore(1)

    def dashboard(self) -> dict:
        counts = {}
        for table in ("projects", "datasets", "profiles", "runs"):
            counts[table] = self.db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
        counts["completed"] = self.db.one(
            "SELECT COUNT(*) AS n FROM runs WHERE status='completed'"
        )["n"]
        counts["failed"] = self.db.one(
            "SELECT COUNT(*) AS n FROM runs WHERE status='failed'"
        )["n"]
        counts["vectors"] = self.db.one(
            "SELECT COALESCE(SUM(row_count),0) AS n FROM datasets"
        )["n"]
        recent = self.list_runs()[:5]
        return {
            "counts": counts,
            "recent_runs": recent,
            "engine": engine_status(),
            "recent_audit": self.audit_list(limit=6),
        }

    def list_projects(self) -> list:
        return self.db.all(
            "SELECT p.*, (SELECT COUNT(*) FROM datasets d "
            "WHERE d.project_id=p.id) AS dataset_count,"
            "(SELECT COUNT(*) FROM runs r WHERE r.project_id=p.id) "
            "AS run_count FROM projects p ORDER BY p.created_at DESC,p.id"
        )

    def get_project(self, identifier: str) -> dict:
        return require(self.db.one(
            "SELECT * FROM projects WHERE id=?", (identifier,)
        ))

    def create_project(self, actor: str, model: ProjectCreate) -> dict:
        identifier = new_id("prj")
        now = utc_now()
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO projects VALUES (?,?,?,?,?,?)",
                    (identifier, model.name, model.description, "active", now, now),
                )
                self.db.audit(
                    conn, actor, "project.create", identifier,
                    {"name": model.name},
                )
        except sqlite3.IntegrityError as exc:
            raise DomainError("项目名称已存在", 409) from exc
        return self.get_project(identifier)

    def update_project(
        self, actor: str, identifier: str, model: ProjectUpdate
    ) -> dict:
        try:
            with self.db.transaction() as conn:
                require(conn.execute(
                    "SELECT id FROM projects WHERE id=?", (identifier,)
                ).fetchone())
                if model.status == "archived":
                    running = conn.execute(
                        "SELECT 1 FROM runs WHERE project_id=? "
                        "AND status='running'", (identifier,),
                    ).fetchone()
                    if running:
                        raise DomainError("运行中的项目不能归档", 409)
                conn.execute(
                    "UPDATE projects SET name=?,description=?,status=?,"
                    "updated_at=? WHERE id=?",
                    (
                        model.name, model.description, model.status,
                        utc_now(), identifier,
                    ),
                )
                self.db.audit(
                    conn, actor, "project.update", identifier,
                    {"status": model.status, "name": model.name},
                )
        except sqlite3.IntegrityError as exc:
            raise DomainError("项目名称已存在", 409) from exc
        return self.get_project(identifier)

    def delete_project(self, actor: str, identifier: str) -> None:
        with self.db.transaction() as conn:
            require(conn.execute(
                "SELECT id FROM projects WHERE id=?", (identifier,)
            ).fetchone())
            if conn.execute(
                "SELECT 1 FROM datasets WHERE project_id=?", (identifier,)
            ).fetchone():
                raise DomainError("项目存在数据集，请使用归档而非删除", 409)
            conn.execute("DELETE FROM projects WHERE id=?", (identifier,))
            self.db.audit(conn, actor, "project.delete", identifier, {})

    def list_datasets(self, project_id: str = "") -> list:
        sql = (
            "SELECT d.id,d.project_id,d.name,d.dimension,d.row_count,"
            "d.content_hash,d.created_at,p.name AS project_name "
            "FROM datasets d JOIN projects p ON p.id=d.project_id"
        )
        params = ()
        if project_id:
            sql += " WHERE d.project_id=?"
            params = (project_id,)
        return self.db.all(sql + " ORDER BY d.created_at DESC,d.id", params)

    def get_dataset(self, identifier: str) -> dict:
        row = require(self.db.one(
            "SELECT * FROM datasets WHERE id=?", (identifier,)
        ))
        row["records"] = json.loads(row.pop("records_json"))
        row["statistics"] = dataset_statistics(row["records"])
        return row

    def create_dataset(self, actor: str, model: DatasetCreate) -> dict:
        records = validate_records([row.model_dump() for row in model.records])
        identifier = new_id("ds")
        content_hash = digest(records)
        try:
            with self.db.transaction() as conn:
                project = require(conn.execute(
                    "SELECT status FROM projects WHERE id=?", (model.project_id,)
                ).fetchone(), "所属项目不存在")
                if project["status"] != "active":
                    raise DomainError("已归档项目不能新增数据集", 409)
                conn.execute(
                    "INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?)",
                    (
                        identifier, model.project_id, model.name,
                        len(records[0]["vector"]), len(records),
                        canonical(records), content_hash, utc_now(),
                    ),
                )
                self.db.audit(
                    conn, actor, "dataset.create", identifier,
                    {"rows": len(records), "sha256": content_hash},
                )
        except sqlite3.IntegrityError as exc:
            raise DomainError("同一项目的数据集名称不能重复", 409) from exc
        return self.get_dataset(identifier)

    def generate_dataset(self, actor: str, model: DatasetGenerate) -> dict:
        records = generate_records(model.count, model.dimension, model.seed)
        return self.create_dataset(actor, DatasetCreate(
            project_id=model.project_id, name=model.name, records=records,
        ))

    def import_dataset(self, actor: str, model: DatasetImport) -> dict:
        parser = parse_json if model.format == "json" else parse_csv
        records = parser(model.content)
        return self.create_dataset(actor, DatasetCreate(
            project_id=model.project_id, name=model.name, records=records,
        ))

    def delete_dataset(self, actor: str, identifier: str) -> None:
        with self.db.transaction() as conn:
            require(conn.execute(
                "SELECT id FROM datasets WHERE id=?", (identifier,)
            ).fetchone())
            if conn.execute(
                "SELECT 1 FROM runs WHERE dataset_id=?", (identifier,)
            ).fetchone():
                raise DomainError("数据集已被任务引用，不能删除", 409)
            conn.execute("DELETE FROM datasets WHERE id=?", (identifier,))
            self.db.audit(conn, actor, "dataset.delete", identifier, {})

    def list_profiles(self) -> list:
        return self.db.all("SELECT * FROM profiles ORDER BY created_at DESC,id")

    def create_profile(self, actor: str, model: ProfileCreate) -> dict:
        validate_profile(model.engine, model.preset)
        identifier = new_id("cfg")
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO profiles VALUES (?,?,?,?,?,?)",
                    (
                        identifier, model.name, model.engine, model.preset,
                        model.metric, utc_now(),
                    ),
                )
                self.db.audit(
                    conn, actor, "profile.create", identifier,
                    {"engine": model.engine, "preset": model.preset},
                )
        except sqlite3.IntegrityError as exc:
            raise DomainError("配置名称已存在", 409) from exc
        return require(self.db.one(
            "SELECT * FROM profiles WHERE id=?", (identifier,)
        ))

    def delete_profile(self, actor: str, identifier: str) -> None:
        with self.db.transaction() as conn:
            require(conn.execute(
                "SELECT id FROM profiles WHERE id=?", (identifier,)
            ).fetchone())
            if conn.execute(
                "SELECT 1 FROM runs WHERE profile_id=?", (identifier,)
            ).fetchone():
                raise DomainError("配置已被任务引用，不能删除", 409)
            conn.execute("DELETE FROM profiles WHERE id=?", (identifier,))
            self.db.audit(conn, actor, "profile.delete", identifier, {})

    def create_run(self, actor: str, model: RunCreate) -> dict:
        dataset = self.get_dataset(model.dataset_id)
        profile = require(self.db.one(
            "SELECT * FROM profiles WHERE id=?", (model.profile_id,)
        ), "配置不存在")
        query = finite_vector(model.query, dataset["dimension"])
        records = dataset["records"]
        if model.tag:
            records = [item for item in records if model.tag in item["tags"]]
        if not records:
            raise DomainError("标签筛选后没有候选记录")
        if model.top_k > len(records):
            raise DomainError("Top-K不能超过筛选后的候选数量")
        prepare(query, profile["metric"])
        for item in records:
            prepare(item["vector"], profile["metric"])
        snapshot = {
            "dataset_id": dataset["id"],
            "dataset_name": dataset["name"],
            "dataset_hash": dataset["content_hash"],
            "profile": profile,
            "records": records,
            "query": query,
            "top_k": model.top_k,
            "tag": model.tag,
        }
        identifier = new_id("run")
        with self.db.transaction() as conn:
            project = require(conn.execute(
                "SELECT status FROM projects WHERE id=?",
                (dataset["project_id"],),
            ).fetchone())
            if project["status"] != "active":
                raise DomainError("归档项目不能新增任务", 409)
            conn.execute(
                "INSERT INTO runs(id,name,project_id,dataset_id,profile_id,"
                "status,snapshot_json,snapshot_hash,created_at) "
                "VALUES (?,?,?,?,?,'pending',?,?,?)",
                (
                    identifier, model.name, dataset["project_id"],
                    dataset["id"], profile["id"], canonical(snapshot),
                    digest(snapshot), utc_now(),
                ),
            )
            self.db.audit(
                conn, actor, "run.create", identifier,
                {"snapshot_hash": digest(snapshot)},
            )
        return self.get_run(identifier)

    def list_runs(self, project_id: str = "", status: str = "") -> list:
        sql = (
            "SELECT r.id,r.name,r.project_id,r.dataset_id,r.profile_id,"
            "r.status,r.error,r.created_at,r.finished_at,"
            "d.name AS dataset_name,p.name AS profile_name,p.engine "
            "FROM runs r JOIN datasets d ON d.id=r.dataset_id "
            "JOIN profiles p ON p.id=r.profile_id WHERE 1=1"
        )
        params = []
        if project_id:
            sql += " AND r.project_id=?"
            params.append(project_id)
        if status:
            sql += " AND r.status=?"
            params.append(status)
        return self.db.all(sql + " ORDER BY r.created_at DESC,r.id", tuple(params))

    def get_run(self, identifier: str) -> dict:
        row = require(self.db.one(
            "SELECT * FROM runs WHERE id=?", (identifier,)
        ))
        row["snapshot"] = json.loads(row.pop("snapshot_json"))
        result_json = row.pop("result_json")
        row["result"] = json.loads(result_json) if result_json else None
        return row

    def execute_run(self, actor: str, identifier: str) -> dict:
        if not self.execution_lock.acquire(blocking=False):
            raise DomainError("已有任务运行，请稍后重试", 409)
        claimed = False
        try:
            with self.db.transaction() as conn:
                row = require(conn.execute(
                    "SELECT * FROM runs WHERE id=?", (identifier,)
                ).fetchone())
                if row["status"] != "pending":
                    raise DomainError("仅待执行任务可运行；重复实验请复制任务", 409)
                project = require(conn.execute(
                    "SELECT status FROM projects WHERE id=?", (row["project_id"],)
                ).fetchone())
                if project["status"] != "active":
                    raise DomainError("归档项目不能运行任务", 409)
                snapshot = json.loads(row["snapshot_json"])
                if digest(snapshot) != row["snapshot_hash"]:
                    raise DomainError("任务快照完整性校验失败", 409)
                conn.execute(
                    "UPDATE runs SET status='running',started_at=? WHERE id=?",
                    (utc_now(), identifier),
                )
                self.db.audit(conn, actor, "run.start", identifier, {})
                claimed = True
            result = execute_snapshot(snapshot)
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE runs SET status='completed',result_json=?,"
                    "finished_at=?,error=NULL WHERE id=?",
                    (canonical(result), utc_now(), identifier),
                )
                self.db.audit(
                    conn, actor, "run.complete", identifier,
                    {"engine": result["engine"], "result_hash": digest(result)},
                )
            return self.get_run(identifier)
        except DomainError:
            raise
        except Exception as exc:
            if not claimed:
                raise
            message = str(exc)[:400] or type(exc).__name__
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE runs SET status='failed',error=?,finished_at=? "
                    "WHERE id=?", (message, utc_now(), identifier),
                )
                self.db.audit(
                    conn, actor, "run.fail", identifier,
                    {"error_type": type(exc).__name__},
                )
            return self.get_run(identifier)
        finally:
            self.execution_lock.release()

    def clone_run(self, actor: str, identifier: str, model: RunClone) -> dict:
        original = self.get_run(identifier)
        snapshot = original["snapshot"]
        return self.create_run(actor, RunCreate(
            name=model.name,
            dataset_id=original["dataset_id"],
            profile_id=model.profile_id or original["profile_id"],
            query=snapshot["query"],
            top_k=snapshot["top_k"],
            tag=snapshot["tag"],
        ))

    def cancel_run(self, actor: str, identifier: str) -> dict:
        with self.db.transaction() as conn:
            row = require(conn.execute(
                "SELECT status FROM runs WHERE id=?", (identifier,)
            ).fetchone())
            if row["status"] != "pending":
                raise DomainError("只能取消尚未运行的任务", 409)
            conn.execute(
                "UPDATE runs SET status='cancelled',finished_at=? WHERE id=?",
                (utc_now(), identifier),
            )
            self.db.audit(conn, actor, "run.cancel", identifier, {})
        return self.get_run(identifier)

    def audit_list(self, action: str = "", limit: int = 200) -> list:
        limit = max(1, min(limit, 1000))
        sql = "SELECT * FROM audit"
        params = []
        if action:
            sql += " WHERE action LIKE ? ESCAPE '\\'"
            safe = action.replace("\\", "\\\\").replace("%", "\\%")
            safe = safe.replace("_", "\\_")
            params.append(safe + "%")
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self.db.all(sql, tuple(params))
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json"))
        return rows

    def record_export(self, actor: str, entity: str, format_name: str) -> None:
        with self.db.transaction() as conn:
            self.db.audit(
                conn, actor, "report.export", entity, {"format": format_name}
            )

    def create_backup(self, actor: str) -> dict:
        self.backups.mkdir(parents=True, exist_ok=True)
        filename = new_id("backup") + ".sqlite3"
        result = self.db.backup(self.backups / filename)
        with self.db.transaction() as conn:
            self.db.audit(conn, actor, "system.backup", filename, result)
        return result

    def list_backups(self) -> list:
        import hashlib
        result = []
        for path in sorted(self.backups.glob("backup_*.sqlite3"), reverse=True):
            if path.is_file() and not path.is_symlink():
                data = path.read_bytes()
                result.append({
                    "filename": path.name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                })
        return result
