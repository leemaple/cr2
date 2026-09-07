"""Isolated databases and explicitly non-production test credentials."""
import pytest
from fastapi.testclient import TestClient
from veclab.api import create_app
from veclab.config import Settings

TEST_PASSWORD = "test-only-password-2026"


@pytest.fixture
def app(tmp_path):
    application = create_app(Settings(data_dir=tmp_path))
    application.state.auth.setup("admin", TEST_PASSWORD)
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as browser:
        response = browser.post("/api/login", json={
            "username": "admin", "password": TEST_PASSWORD,
        })
        assert response.status_code == 200, response.text
        browser.headers["X-CSRF-Token"] = response.json()["csrf"]
        yield browser


@pytest.fixture
def resources(client):
    project = client.post("/api/projects", json={
        "name": "测试项目", "description": "隔离测试数据",
    }).json()
    dataset = client.post("/api/datasets", json={
        "project_id": project["id"], "name": "可核对向量",
        "records": [
            {"id": "a", "label": "甲", "vector": [1, 0, 0, 0], "tags": ["T"]},
            {"id": "b", "label": "乙", "vector": [.8, .6, 0, 0], "tags": ["T"]},
            {"id": "c", "label": "丙", "vector": [-1, 0, 0, 0], "tags": ["U"]},
        ],
    }).json()
    profile = client.post("/api/profiles", json={
        "name": "明文核对", "engine": "plain", "preset": "baseline",
        "metric": "cosine",
    }).json()
    return project, dataset, profile


def make_run(client, resources, **changes):
    _, dataset, profile = resources
    payload = {
        "name": "独立检索任务", "dataset_id": dataset["id"],
        "profile_id": profile["id"], "query": [1, 0, 0, 0], "top_k": 2,
    }
    payload.update(changes)
    return client.post("/api/runs", json=payload)
