"""Validated vector import, reproducible fixtures, and reference math."""
from collections import Counter
from typing import Any
import csv
import io
import json
import math
import random

from pydantic import ValidationError
from .schemas import VectorRecord
from .database import digest

MAX_DIMENSION = 128
MAX_ROWS = 64
MAX_ABS_VALUE = 1.0


def finite_vector(value: list[float], dimension: int | None = None) -> list:
    if not isinstance(value, list) or not 2 <= len(value) <= MAX_DIMENSION:
        raise ValueError("向量维度必须为2至128")
    if dimension is not None and len(value) != dimension:
        raise ValueError(f"向量维度不一致：要求{dimension}维")
    result = []
    for number in value:
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError("向量只能包含实数")
        if not math.isfinite(number) or abs(number) > MAX_ABS_VALUE:
            raise ValueError("向量元素必须是[-1,1]范围内的有限实数")
        result.append(float(number))
    return result


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(math.fsum(x * x for x in vector))
    if norm < 1e-12:
        raise ValueError("余弦相似度不接受零向量或近零向量")
    return [x / norm for x in vector]


def dot(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("点积向量维度不一致")
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def prepare(vector: list[float], metric: str) -> list[float]:
    checked = finite_vector(vector)
    if metric == "cosine":
        return normalize(checked)
    if metric == "dot":
        return checked
    raise ValueError("不支持的相似度度量")


def validate_records(values: list[dict]) -> list[dict]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_ROWS:
        raise ValueError("数据集必须包含1至64条向量记录")
    checked = []
    identifiers = set()
    dimension = None
    for index, item in enumerate(values, 1):
        try:
            record = VectorRecord.model_validate(item).model_dump()
        except ValidationError as exc:
            raise ValueError(f"第{index}条记录格式不正确") from exc
        vector = finite_vector(record["vector"], dimension)
        dimension = len(vector)
        if record["id"] in identifiers:
            raise ValueError(f"第{index}条记录的ID重复")
        identifiers.add(record["id"])
        record["vector"] = vector
        checked.append(record)
    return checked


def parse_json(content: str) -> list[dict]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"JSON不允许{value}")
    try:
        value = json.loads(content, parse_constant=reject_constant)
    except (ValueError, TypeError) as exc:
        raise ValueError("JSON格式不正确或包含非有限数字") from exc
    if isinstance(value, dict) and set(value) == {"records"}:
        value = value["records"]
    return validate_records(value)


def parse_csv(content: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    names = reader.fieldnames or []
    if names != ["id", "label", "vector", "tags"]:
        raise ValueError("CSV表头必须为id,label,vector,tags")
    result = []
    for row_number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"CSV第{row_number}行列数不一致")
        try:
            vector = json.loads(row["vector"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"CSV第{row_number}行向量不是JSON数组") from exc
        result.append({
            "id": row["id"],
            "label": row["label"],
            "vector": vector,
            "tags": [x.strip() for x in row["tags"].split(";") if x.strip()],
        })
        if len(result) > MAX_ROWS:
            raise ValueError("CSV记录数不能超过64")
    return validate_records(result)


def generate_records(count: int, dimension: int, seed: int) -> list[dict]:
    if not 2 <= count <= MAX_ROWS or not 2 <= dimension <= MAX_DIMENSION:
        raise ValueError("合成数据规模超出限制")
    rng = random.Random(seed)
    groups = ["技术资料", "产品说明", "研究笔记"]
    result = []
    for index in range(count):
        vector = [round(rng.uniform(-1, 1), 6) for _ in range(dimension)]
        result.append({
            "id": f"vec-{index + 1:03d}",
            "label": f"合成向量样本 {index + 1:02d}",
            "vector": vector,
            "tags": [groups[index % len(groups)], "合成数据"],
        })
    return validate_records(result)


def dataset_statistics(records: list[dict]) -> dict:
    vectors = [item["vector"] for item in records]
    flattened = [number for vector in vectors for number in vector]
    norms = [math.sqrt(dot(vector, vector)) for vector in vectors]
    tags = Counter(tag for item in records for tag in item["tags"])
    dimension = len(vectors[0])
    column_mean = [
        math.fsum(row[column] for row in vectors) / len(vectors)
        for column in range(dimension)
    ]
    return {
        "rows": len(records),
        "dimension": dimension,
        "minimum": min(flattened),
        "maximum": max(flattened),
        "mean_norm": math.fsum(norms) / len(norms),
        "zero_vectors": sum(norm < 1e-12 for norm in norms),
        "tags": dict(sorted(tags.items())),
        "column_mean": column_mean,
        "content_hash": digest(records),
    }


def rank(scores: list[dict], key: str, top_k: int) -> list[dict]:
    return sorted(scores, key=lambda item: (-item[key], item["id"]))[:top_k]


def evaluate_scores(scores: list[dict], top_k: int) -> dict:
    if not scores or not 1 <= top_k <= len(scores):
        raise ValueError("无有效得分或Top-K超出候选数量")
    reference = rank(scores, "reference", top_k)
    measured = rank(scores, "score", top_k)
    expected = {item["id"] for item in reference}
    observed = {item["id"] for item in measured}
    errors = [abs(item["score"] - item["reference"]) for item in scores]
    rmse = math.sqrt(math.fsum(error * error for error in errors) / len(errors))
    maximum = max(errors)
    ordered = sorted(scores, key=lambda item: (-item["reference"], item["id"]))
    gap = None
    if top_k < len(ordered):
        gap = ordered[top_k - 1]["reference"] - ordered[top_k]["reference"]
    return {
        "max_abs_error": maximum,
        "rmse": rmse,
        "recall_at_k": len(expected & observed) / top_k,
        "top1_match": measured[0]["id"] == reference[0]["id"],
        "boundary_gap": gap,
        "near_tie": gap is not None and gap <= 2 * maximum,
        "top_k": measured,
        "reference_top_k": reference,
    }
