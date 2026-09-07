"""Delivery-review regressions; real CKKS at the declared size boundaries."""
import json
import math
import pytest
from pydantic import ValidationError

from veclab.database import digest
from veclab.engines import execute_snapshot
from veclab.reporting import dataset_csv
from veclab.schemas import VectorRecord
from veclab.vectors import (
    evaluate_scores, generate_records, parse_csv, validate_records,
)


@pytest.mark.parametrize("tag", ["a;b", ";", "a;", "a;;b"])
def test_reject_ambiguous_csv_tag(tag):
    with pytest.raises(ValidationError):
        VectorRecord(id="a", label="sample", vector=[1.0, 0.0], tags=[tag])


def test_csv_multiple_tags_roundtrip():
    records = validate_records([
        {"id": "a", "label": "sample", "vector": [1.0, 0.0],
         "tags": ["first", "second"]}
    ])
    assert parse_csv(dataset_csv({"records": records})) == records


@pytest.mark.parametrize("route", ["direct", "json-import"])
def test_ambiguous_tag_rejected_without_partial_write(client, resources, route):
    records = [{"id": "a", "label": "sample", "vector": [1.0, 0.0],
                "tags": ["first;second"]}]
    payload = {"project_id": resources[0]["id"], "name": "rejected-tag"}
    before = client.get("/api/datasets").json()
    if route == "direct":
        payload["records"] = records
        response = client.post("/api/datasets", json=payload)
    else:
        payload.update(format="json", content=json.dumps(records))
        response = client.post("/api/datasets/import", json=payload)
    assert response.status_code == 422, response.text
    assert client.get("/api/datasets").json() == before


def test_equal_scores_have_deterministic_order_and_tie_warning():
    scores = [{"id": key, "score": 1.0, "reference": 1.0}
              for key in ["z", "a", "m"]]
    result = evaluate_scores(scores, 2)
    assert [row["id"] for row in result["top_k"]] == ["a", "m"]
    assert result["near_tie"] is True
    assert result["recall_at_k"] == 1.0
    assert result["boundary_gap"] == 0.0


@pytest.mark.parametrize("preset", ["balanced", "compact"])
@pytest.mark.parametrize("metric", ["cosine", "dot"])
@pytest.mark.parametrize("count,dimension", [(1, 2), (64, 128)])
def test_real_ckks_declared_size_boundaries(preset, metric, count, dimension):
    if count == 1:
        records = [{"id": "only", "label": "only", "vector": [1.0, 0.0],
                    "tags": []}]
    else:
        records = generate_records(count, dimension, 20260907)
    query = records[0]["vector"]
    snapshot = {
        "profile": {"engine": "ckks", "preset": preset, "metric": metric},
        "records": records, "query": query, "top_k": count, "tag": "",
        "dataset_hash": digest(records),
    }
    result = execute_snapshot(snapshot)
    assert result["candidate_count"] == count
    assert result["dimension"] == dimension
    assert result["evaluator_has_secret_key"] is False
    assert result["timing"]["ciphertext_bytes"] > 0
    assert result["metrics"]["precision_pass"] is True
    assert len(result["scores"]) == count
    assert result["metrics"]["recall_at_k"] == 1.0
    for row, record in zip(result["scores"], records, strict=True):
        reference = math.fsum(a * b for a, b in zip(record["vector"], query))
        if metric == "cosine":
            reference /= math.sqrt(math.fsum(a * a for a in record["vector"]))
            reference /= math.sqrt(math.fsum(b * b for b in query))
        assert abs(row["score"] - reference) <= result["parameters"]["tolerance"]
