"""Real TenSEAL tests: no mocking and no plaintext substitute."""
import pytest
from conftest import make_run


@pytest.mark.parametrize("preset", ["balanced", "compact"])
@pytest.mark.parametrize("metric", ["cosine", "dot"])
def test_real_ckks_retrieval(client, resources, preset, metric):
    profile = client.post("/api/profiles", json={
        "name": preset + "-" + metric, "engine": "ckks",
        "preset": preset, "metric": metric,
    })
    assert profile.status_code == 201, profile.text
    run = make_run(client, resources, profile_id=profile.json()["id"]).json()
    completed = client.post("/api/runs/" + run["id"] + "/execute").json()
    assert completed["status"] == "completed", completed.get("error")
    result = completed["result"]
    assert result["engine"] == "ckks"
    assert result["backend_version"] == "0.3.16"
    assert result["evaluator_has_secret_key"] is False
    assert result["timing"]["ciphertext_bytes"] > 0
    assert result["timing"]["result_bytes"] > 0
    assert result["metrics"]["max_abs_error"] < result["parameters"]["tolerance"]
    assert result["metrics"]["recall_at_k"] == 1
    assert result["metrics"]["top1_match"] is True
    assert result["metrics"]["top_k"][0]["id"] == "a"
    assert client.get("/api/engine").json()["ckks_available"]
