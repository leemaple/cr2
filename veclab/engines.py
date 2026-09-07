"""Actual CKKS evaluation; a missing engine never falls back to plaintext."""
from dataclasses import dataclass
from importlib import metadata
from time import perf_counter
from typing import Callable
import math

from .database import digest
from .vectors import prepare, dot, evaluate_scores

PRESETS = {
    "baseline": {
        "label": "明文参考基线",
        "engine": "plain",
        "degree": None,
        "coeff_bits": [],
        "scale_bits": None,
        "tolerance": 0.0,
    },
    "balanced": {
        "label": "CKKS 标准精度",
        "engine": "ckks",
        "degree": 8192,
        "coeff_bits": [60, 40, 40, 60],
        "scale_bits": 40,
        "tolerance": 0.0001,
    },
    "compact": {
        "label": "CKKS 紧凑精度",
        "engine": "ckks",
        "degree": 8192,
        "coeff_bits": [50, 30, 30, 50],
        "scale_bits": 30,
        "tolerance": 0.01,
    },
}


@dataclass
class EngineTiming:
    keygen_ms: float = 0.0
    encrypt_ms: float = 0.0
    evaluate_ms: float = 0.0
    decrypt_ms: float = 0.0
    reference_ms: float = 0.0
    ciphertext_bytes: int = 0
    result_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "keygen_ms": self.keygen_ms,
            "encrypt_ms": self.encrypt_ms,
            "evaluate_ms": self.evaluate_ms,
            "decrypt_ms": self.decrypt_ms,
            "reference_ms": self.reference_ms,
            "ciphertext_bytes": self.ciphertext_bytes,
            "result_bytes": self.result_bytes,
        }


def engine_status() -> dict:
    try:
        import tenseal
        version = metadata.version("tenseal")
        available = hasattr(tenseal, "ckks_vector")
        error = "" if available else "缺少CKKS接口"
    except (ImportError, OSError, metadata.PackageNotFoundError) as exc:
        version = None
        available = False
        error = type(exc).__name__
    return {
        "ckks_available": available,
        "tenseal_version": version,
        "plain_available": True,
        "error": error,
        "presets": PRESETS,
        "boundary": (
            "本地可信单用户评测；原始向量和查询在本机可见。"
            "CKKS评分后解密排序，不是全密态Top-K或远程隔离部署。"
        ),
    }


def validate_profile(engine: str, preset: str) -> dict:
    if preset not in PRESETS or PRESETS[preset]["engine"] != engine:
        raise ValueError("引擎与参数预设不匹配")
    return dict(PRESETS[preset])


def milliseconds(start: float) -> float:
    return (perf_counter() - start) * 1000.0


def plain_scores(vectors: list[list], query: list) -> tuple[list, EngineTiming]:
    timing = EngineTiming()
    start = perf_counter()
    scores = [dot(vector, query) for vector in vectors]
    timing.evaluate_ms = milliseconds(start)
    return scores, timing


def ckks_scores(
    vectors: list[list], query: list, parameters: dict
) -> tuple[list, EngineTiming, bool]:
    try:
        import tenseal as ts
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "未安装可用的TenSEAL；任务失败，不会改用明文计算。"
        ) from exc
    timing = EngineTiming()
    start = perf_counter()
    context = ts.context(
        ts.SCHEME_TYPE.CKKS,
        poly_modulus_degree=parameters["degree"],
        coeff_mod_bit_sizes=parameters["coeff_bits"],
        n_threads=1,
    )
    context.global_scale = 2 ** parameters["scale_bits"]
    context.generate_galois_keys()
    secret_key = context.secret_key()
    context.make_context_public()
    evaluator_has_secret_key = context.has_secret_key()
    if evaluator_has_secret_key:
        raise RuntimeError("计算上下文意外含有私钥，拒绝执行")
    timing.keygen_ms = milliseconds(start)
    try:
        start = perf_counter()
        encrypted_query = ts.ckks_vector(context, query)
        encrypted_rows = [ts.ckks_vector(context, vector) for vector in vectors]
        timing.encrypt_ms = milliseconds(start)
        timing.ciphertext_bytes = len(encrypted_query.serialize())
        timing.ciphertext_bytes += sum(
            len(row.serialize()) for row in encrypted_rows
        )
        start = perf_counter()
        encrypted_scores = [row.dot(encrypted_query) for row in encrypted_rows]
        timing.evaluate_ms = milliseconds(start)
        timing.result_bytes = sum(len(item.serialize()) for item in encrypted_scores)
        start = perf_counter()
        scores = [item.decrypt(secret_key)[0] for item in encrypted_scores]
        timing.decrypt_ms = milliseconds(start)
        if any(not math.isfinite(value) for value in scores):
            raise RuntimeError("解密得分含非有限值")
        return scores, timing, evaluator_has_secret_key
    finally:
        del secret_key
        del context


def execute_snapshot(snapshot: dict) -> dict:
    start_total = perf_counter()
    profile = snapshot["profile"]
    parameters = validate_profile(profile["engine"], profile["preset"])
    records = snapshot["records"]
    metric = profile["metric"]
    start_prepare = perf_counter()
    query = prepare(snapshot["query"], metric)
    vectors = [prepare(record["vector"], metric) for record in records]
    prepare_ms = milliseconds(start_prepare)
    if any(len(vector) != len(query) for vector in vectors):
        raise ValueError("查询与数据集维度不一致")
    reference_start = perf_counter()
    references = [dot(vector, query) for vector in vectors]
    reference_ms = milliseconds(reference_start)
    evaluator_has_secret_key = None
    if profile["engine"] == "ckks":
        scores, timing, evaluator_has_secret_key = ckks_scores(
            vectors, query, parameters
        )
        backend_version = metadata.version("tenseal")
    else:
        scores, timing = plain_scores(vectors, query)
        backend_version = "python-math-fsum"
    timing.reference_ms = reference_ms
    rows = []
    for record, score, reference in zip(records, scores, references, strict=True):
        rows.append({
            "id": record["id"],
            "label": record["label"],
            "tags": record["tags"],
            "score": score,
            "reference": reference,
            "abs_error": abs(score - reference),
        })
    metrics = evaluate_scores(rows, snapshot["top_k"])
    metrics["precision_pass"] = metrics["max_abs_error"] <= parameters["tolerance"]
    metrics["tolerance"] = parameters["tolerance"]
    return {
        "engine": profile["engine"],
        "backend_version": backend_version,
        "metric": metric,
        "preset": profile["preset"],
        "parameters": parameters,
        "candidate_count": len(records),
        "dimension": len(query),
        "query": snapshot["query"],
        "top_k_requested": snapshot["top_k"],
        "tag": snapshot["tag"],
        "dataset_hash": snapshot["dataset_hash"],
        "candidate_hash": digest(records),
        "snapshot_hash": digest(snapshot),
        "evaluator_has_secret_key": evaluator_has_secret_key,
        "prepare_ms": prepare_ms,
        "timing": timing.to_dict(),
        "total_ms": milliseconds(start_total),
        "metrics": metrics,
        "scores": rows,
        "security_boundary": engine_status()["boundary"],
        "measurement_note": (
            "耗时为本次实测；总耗时含序列化与校验。密文字节不含公钥、"
            "旋转密钥或网络协议开销，不代表真实网络流量。"
        ),
    }
