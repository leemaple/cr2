"""Real application requests with isolated persistent state."""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from conftest import TEST_PASSWORD, make_run
from veclab.database import digest


def test_health_and_security_headers(client):
    response = client.get("/health")
    assert response.json()["version"] == "V1.0"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/api/setup-status").json()["initialized"]
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_authentication_and_csrf(app, client):
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/projects").status_code == 401
        assert anonymous.post("/api/login", json={
            "username": "admin", "password": "wrong",
        }).status_code == 401
    assert client.post("/api/projects", headers={"X-CSRF-Token": "bad"},
                       json={"name": "错误请求"}).status_code == 403
    assert client.post("/api/projects", headers={"Origin": "https://evil.invalid"},
                       json={"name": "跨站请求"}).status_code == 403
    assert client.get("/health", headers={"Host": "evil.invalid"}).status_code == 400
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/projects").status_code == 401


def test_login_lockout(app):
    with TestClient(app) as browser:
        for _ in range(5):
            assert browser.post("/api/login", json={
                "username": "admin", "password": "wrong",
            }).status_code == 401
        assert browser.post("/api/login", json={
            "username": "admin", "password": TEST_PASSWORD,
        }).status_code == 429
    assert app.state.database.verify_audit()["ok"]


def test_cookie_password_change_and_revocation(app, client):
    stored = app.state.database.one("SELECT * FROM sessions")
    token = client.cookies.get("veclab_session")
    assert stored["token_hash"] != token
    response = client.post("/api/password", json={
        "old_password": TEST_PASSWORD, "new_password": "changed-test-password-2026",
    })
    assert response.status_code == 200
    assert client.get("/api/session").status_code == 401
    assert app.state.database.one("SELECT COUNT(*) n FROM sessions")["n"] == 0
    with pytest.raises(ValueError):
        app.state.auth.setup("other", TEST_PASSWORD)


def test_expired_session(app, client):
    with app.state.database.transaction() as conn:
        conn.execute("UPDATE sessions SET expires_at=0")
    assert client.get("/api/session").status_code == 401


def test_oversize_and_unknown_fields(client):
    assert client.post("/api/projects", json={"name": "项目", "extra": 1}).status_code == 422
    response = client.post("/api/projects", content=b"x" * (2 * 1024 * 1024 + 1))
    assert response.status_code == 413


def test_project_dataset_lifecycle(client, resources):
    project, dataset, _ = resources
    duplicate = client.post("/api/projects", json={"name": project["name"]})
    assert duplicate.status_code == 409
    assert client.get("/api/datasets/unknown").status_code == 404
    assert client.delete("/api/projects/" + project["id"]).status_code == 409
    exported = client.get("/api/datasets/" + dataset["id"] + "/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert client.delete("/api/datasets/" + dataset["id"]).status_code == 200
    assert client.delete("/api/projects/" + project["id"]).status_code == 200


def test_import_and_generated_dataset(client, resources):
    project, _, _ = resources
    payload = {"project_id": project["id"], "name": "合成测试", "dimension": 8,
               "count": 6, "seed": 42}
    response = client.post("/api/datasets/generate", json=payload)
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["content_hash"] == digest(dataset["records"])
    exported = client.get("/api/datasets/" + dataset["id"] + "/export").text
    imported = client.post("/api/datasets/import", json={
        "project_id": project["id"], "name": "CSV回读", "format": "csv", "content": exported,
    })
    assert imported.status_code == 201, imported.text
    assert imported.json()["records"] == dataset["records"]
    payload["count"] = True
    assert client.post("/api/datasets/generate", json=payload).status_code == 422


@pytest.mark.parametrize("change", [
    {"query": [0, 1]}, {"query": [0, 0, 0, 0]}, {"top_k": 4},
    {"tag": "missing"}, {"query": [True, 0, 0, 0]},
])
def test_run_validation(client, resources, change):
    assert make_run(client, resources, **change).status_code in {400, 422}


def test_plain_run_and_all_exports(client, resources):
    response = make_run(client, resources)
    assert response.status_code == 201, response.text
    run = response.json()
    path = "/api/runs/" + run["id"]
    assert client.get(path + "/report").status_code == 409
    completed = client.post(path + "/execute")
    assert completed.status_code == 200, completed.text
    result = completed.json()["result"]
    assert result["engine"] == "plain"
    assert result["scores"][0]["score"] == 1
    assert result["metrics"]["max_abs_error"] == 0
    assert result["metrics"]["precision_pass"]
    assert result["evaluator_has_secret_key"] is None
    assert client.post(path + "/execute").status_code == 409
    assert client.post(path + "/cancel").status_code == 409
    for kind in ("json", "csv", "html"):
        assert client.get(path + "/export/" + kind).status_code == 200
    exported = client.get(path + "/export/json").json()
    assert exported["result_sha256"] == digest(result)
    assert client.get(path + "/export/pdf").status_code == 404
    assert client.delete("/api/datasets/" + resources[1]["id"]).status_code == 409
    assert client.delete("/api/profiles/" + resources[2]["id"]).status_code == 409
    assert client.get("/api/dashboard").json()["counts"]["completed"] == 1


def test_cancel_clone_and_filter(client, resources):
    run = make_run(client, resources, tag="T").json()
    path = "/api/runs/" + run["id"]
    assert len(run["snapshot"]["records"]) == 2
    assert client.post(path + "/cancel").json()["status"] == "cancelled"
    assert client.post(path + "/execute").status_code == 409
    clone = client.post(path + "/clone", json={"name": "新任务"}).json()
    assert clone["id"] != run["id"]
    assert clone["status"] == "pending"
    assert clone["snapshot"]["records"] == run["snapshot"]["records"]
    assert len(client.get("/api/runs?status=cancelled").json()) == 1


def test_archive_prevents_writes(client, resources):
    project, _, _ = resources
    run = make_run(client, resources).json()
    response = client.put("/api/projects/" + project["id"], json={
        "name": project["name"], "description": "归档", "status": "archived",
    })
    assert response.status_code == 200
    assert make_run(client, resources).status_code == 409
    assert client.post("/api/runs/" + run["id"] + "/execute").status_code == 409


def test_snapshot_tamper_is_rejected(app, client, resources):
    run = make_run(client, resources).json()
    with app.state.database.transaction() as conn:
        conn.execute("UPDATE runs SET snapshot_hash='tampered' WHERE id=?", (run["id"],))
    response = client.post("/api/runs/" + run["id"] + "/execute")
    assert response.status_code == 409
    assert "完整性" in response.text


def test_engine_error_is_failure_not_plain_fallback(app, client, resources, monkeypatch):
    import veclab.services
    def failing(snapshot):
        raise RuntimeError("explicit engine failure")
    monkeypatch.setattr(veclab.services, "execute_snapshot", failing)
    run = make_run(client, resources).json()
    response = client.post("/api/runs/" + run["id"] + "/execute").json()
    assert response["status"] == "failed"
    assert response["result"] is None
    assert "explicit engine failure" in response["error"]


def test_single_execution_lock(app, client, resources):
    run = make_run(client, resources).json()
    app.state.service.execution_lock.acquire()
    try:
        response = client.post("/api/runs/" + run["id"] + "/execute")
        assert response.status_code == 409
    finally:
        app.state.service.execution_lock.release()


def test_backup_and_audit_tamper(app, client, resources):
    assert client.get("/api/audit/verify").json()["ok"]
    backup = client.post("/api/backups")
    assert backup.status_code == 201, backup.text
    details = backup.json()
    assert details["integrity"] == "ok"
    with sqlite3.connect(app.state.settings.backups / details["filename"]) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(client.get("/api/backups").json()) == 1
    assert client.get("/api/audit/export").status_code == 200
    with app.state.database.transaction() as conn:
        conn.execute("UPDATE audit SET actor='tampered' WHERE seq=1")
    assert not client.get("/api/audit/verify").json()["ok"]


def test_interrupted_run_recovery(app, client, resources):
    run = make_run(client, resources).json()
    with app.state.database.transaction() as conn:
        conn.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
    app.state.database.recover_interrupted_runs()
    recovered = client.get("/api/runs/" + run["id"]).json()
    assert recovered["status"] == "failed"
    assert app.state.database.verify_audit()["ok"]


def test_report_escaping_and_comparison(client, resources):
    run = make_run(client, resources, name="<script>alert(1)</script>").json()
    path = "/api/runs/" + run["id"]
    client.post(path + "/execute")
    report = client.get(path + "/report").text
    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;" in report
    other = client.post(path + "/clone", json={"name": "同条件对比"}).json()
    client.post("/api/runs/" + other["id"] + "/execute")
    compared = client.post("/api/compare", json={"left": run["id"], "right": other["id"]})
    assert compared.status_code == 200, compared.text
    mismatch = make_run(client, resources, query=[0, 1, 0, 0]).json()
    client.post("/api/runs/" + mismatch["id"] + "/execute")
    assert client.post("/api/compare", json={
        "left": run["id"], "right": mismatch["id"],
    }).status_code == 409
