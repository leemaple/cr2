"""Reference arithmetic, validation, and exchange formats."""
import json
import math
import pytest
from veclab.vectors import (
    finite_vector, normalize, dot, prepare, parse_json, parse_csv,
    validate_records, generate_records, evaluate_scores, dataset_statistics,
)
from veclab.reporting import dataset_csv, spreadsheet_safe, number
from veclab.engines import validate_profile
from veclab.security import validate_password, hash_password, verify_password


@pytest.mark.parametrize("vector", [
    [], [1], [0] * 129, [True, 0], ["1", 0], [math.inf, 0],
    [math.nan, 0], [1.1, 0], [-1.1, 0], None,
])
def test_reject_invalid_vector(vector):
    with pytest.raises(ValueError):
        finite_vector(vector)


def test_dimension_mismatch():
    with pytest.raises(ValueError):
        finite_vector([0, 1], 3)
    with pytest.raises(ValueError):
        dot([1], [1, 2])


def test_reference_math():
    assert dot([.5, -.5], [.5, .5]) == 0
    assert normalize([.3, .4]) == pytest.approx([.6, .8])
    assert prepare([.3, .4], "dot") == [.3, .4]
    with pytest.raises(ValueError):
        prepare([0, 0], "cosine")
    with pytest.raises(ValueError):
        prepare([0, 1], "unknown")


@pytest.mark.parametrize("source", [
    "[]", "{}", "null", "not-json", "[NaN]",
    '[{"id":"a","label":"甲","vector":[0,Infinity]}]',
    '[{"id":"a","label":"甲","vector":[true,0]}]',
])
def test_invalid_json(source):
    with pytest.raises(ValueError):
        parse_json(source)


@pytest.mark.parametrize("source", [
    "id,label\na,b", "id,label,vector,tags\na,b,[]",
    'id,label,vector,tags\na,b,"bad",T',
    'id,label,vector,tags\na,b,"[0,1]",T,extra',
])
def test_invalid_csv(source):
    with pytest.raises(ValueError):
        parse_csv(source)


def test_generator_is_reproducible_and_does_not_change_global_rng():
    import random
    random.seed(33)
    state = random.getstate()
    first = generate_records(8, 6, 2026)
    assert first == generate_records(8, 6, 2026)
    assert first != generate_records(8, 6, 2027)
    assert random.getstate() == state
    assert dataset_statistics(first)["rows"] == 8
    assert parse_json(json.dumps({"records": first})) == first
    assert parse_csv(dataset_csv({"records": first})) == first


def test_duplicate_ids_and_dimensions():
    rows = generate_records(2, 4, 0)
    rows[1]["id"] = rows[0]["id"]
    with pytest.raises(ValueError):
        validate_records(rows)
    rows = generate_records(2, 4, 0)
    rows[1]["vector"] = [0, 1]
    with pytest.raises(ValueError):
        validate_records(rows)


def test_tie_warning_and_deterministic_plain_order():
    scores = [
        {"id": "b", "score": 1., "reference": 1.},
        {"id": "a", "score": 1., "reference": 1.},
    ]
    metrics = evaluate_scores(scores, 1)
    assert metrics["top_k"][0]["id"] == "a"
    assert metrics["near_tie"] is True
    assert metrics["boundary_gap"] == 0
    assert metrics["recall_at_k"] == 1
    assert evaluate_scores(scores, 2)["boundary_gap"] is None
    with pytest.raises(ValueError):
        evaluate_scores(scores, 3)


@pytest.mark.parametrize("text", ["=1+1", "+SUM(A1)", "-2+3", "@SUM(A1)", "  =1"])
def test_csv_formula_protection(text):
    assert spreadsheet_safe(text).startswith("'")


def test_bad_preset_pairs():
    with pytest.raises(ValueError):
        validate_profile("plain", "balanced")
    with pytest.raises(ValueError):
        validate_profile("ckks", "unknown")


@pytest.mark.parametrize("password", ["short", " " * 12, "a" * 129, None])
def test_bad_passwords(password):
    with pytest.raises(ValueError):
        validate_password(password)


def test_password_hash_has_random_salt_and_verification():
    password = "test-only-password-2026"
    left = hash_password(password)
    right = hash_password(password)
    assert left != right
    assert verify_password(password, left)
    assert not verify_password("wrong", left)
    assert not verify_password(password, "bad-hash")
    assert not verify_password(password, "pbkdf2_sha256$1$a$b")
    assert number(None) == "不适用"
    assert number(float("nan")) == "无效值"
