"""Escaped reports, formula-safe CSV exports, and comparable run analysis."""
from html import escape
from typing import Any
import csv
import io
import json
import math

from . import NAME, VERSION
from .database import canonical, digest, utc_now
from .services import DomainError


STATUS_LABELS = {
    "pending": "待执行",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def spreadsheet_safe(value: Any) -> str:
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def make_csv(headers: list[str], rows: list[list]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([
            spreadsheet_safe(value) if isinstance(value, str) else value
            for value in row
        ])
    return "\ufeff" + stream.getvalue()


def result_csv(run: dict) -> str:
    result = run.get("result")
    if run["status"] != "completed" or not result:
        raise DomainError("只有完成的任务可以导出结果", 409)
    rows = []
    for index, item in enumerate(result["metrics"]["top_k"], 1):
        rows.append([
            index, item["id"], item["label"], item["score"],
            item["reference"], item["abs_error"], ";".join(item["tags"]),
        ])
    return make_csv(
        ["rank", "id", "label", "score", "reference", "abs_error", "tags"],
        rows,
    )


def result_json(run: dict) -> str:
    if run["status"] != "completed" or not run.get("result"):
        raise DomainError("只有完成的任务可以导出结果", 409)
    envelope = {
        "software": NAME,
        "version": VERSION,
        "exported_at": utc_now(),
        "run_id": run["id"],
        "run_name": run["name"],
        "created_at": run["created_at"],
        "finished_at": run["finished_at"],
        "snapshot_hash": run["snapshot_hash"],
        "result": run["result"],
        "result_sha256": digest(run["result"]),
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False)


def dataset_csv(dataset: dict) -> str:
    rows = [
        [item["id"], item["label"], canonical(item["vector"]), ";".join(item["tags"])]
        for item in dataset["records"]
    ]
    return make_csv(["id", "label", "vector", "tags"], rows)


def audit_csv(rows: list) -> str:
    return make_csv(
        ["seq", "timestamp", "actor", "action", "entity", "detail", "entry_hash"],
        [
            [
                row["seq"], row["timestamp"], row["actor"], row["action"],
                row["entity"], canonical(row["detail"]), row["entry_hash"],
            ]
            for row in rows
        ],
    )


def number(value: Any, digits: int = 6) -> str:
    if value is None:
        return "不适用"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (float, int)):
        if not math.isfinite(value):
            return "无效值"
        return f"{value:.{digits}g}"
    return str(value)


def table(headers: list[str], rows: list[list]) -> str:
    head = "".join(f"<th>{escape(str(item))}</th>" for item in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{escape(str(item))}</td>" for item in row)
        body.append(f"<tr>{cells}</tr>")
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"


def report_html(run: dict) -> str:
    result = run.get("result")
    if run["status"] != "completed" or not result:
        raise DomainError("请先完成任务再预览报告", 409)
    metrics = result["metrics"]
    timing = result["timing"]
    snapshot = run["snapshot"]
    metadata = table(["项目", "值"], [
        ["任务名称", run["name"]],
        ["任务编号", run["id"]],
        ["数据集", snapshot["dataset_name"]],
        ["引擎", result["engine"].upper()],
        ["库版本", result["backend_version"]],
        ["度量", result["metric"]],
        ["参数预设", result["preset"]],
        ["候选向量 / 维度", f'{result["candidate_count"]} / {result["dimension"]}'],
        ["筛选标签", result["tag"] or "不筛选"],
        ["完成时间（UTC）", run["finished_at"]],
    ])
    quality = table(["指标", "实测值"], [
        ["最大绝对误差", number(metrics["max_abs_error"], 10)],
        ["均方根误差", number(metrics["rmse"], 10)],
        ["Recall@K", number(metrics["recall_at_k"])],
        ["Top-1一致", number(metrics["top1_match"])],
        ["误差阈值", number(metrics["tolerance"])],
        ["精度阈值通过", number(metrics["precision_pass"])],
        ["边界间隔", number(metrics["boundary_gap"], 10)],
        ["接近并列边界", number(metrics["near_tie"])],
    ])
    speed = table(["阶段", "耗时或大小"], [
        ["本地预处理", number(result["prepare_ms"]) + " ms"],
        ["密钥与计算上下文准备", number(timing["keygen_ms"]) + " ms"],
        ["向量加密", number(timing["encrypt_ms"]) + " ms"],
        ["评分计算", number(timing["evaluate_ms"]) + " ms"],
        ["结果解密", number(timing["decrypt_ms"]) + " ms"],
        ["明文参考计算", number(timing["reference_ms"]) + " ms"],
        ["总耗时", number(result["total_ms"]) + " ms"],
        ["输入密文序列化大小", str(timing["ciphertext_bytes"]) + " bytes"],
        ["输出密文序列化大小", str(timing["result_bytes"]) + " bytes"],
    ])
    ranking = table(["排名", "记录ID", "标签", "得分", "参考值", "绝对误差"], [
        [
            index, item["id"], item["label"], number(item["score"], 10),
            number(item["reference"], 10), number(item["abs_error"], 8),
        ]
        for index, item in enumerate(metrics["top_k"], 1)
    ])
    title = escape(NAME + " " + VERSION)
    escaped_name = escape(run["name"])
    snapshot_hash = escape(run["snapshot_hash"])
    dataset_hash = escape(result["dataset_hash"])
    boundary = escape(result["security_boundary"])
    measurement = escape(result["measurement_note"])
    parameters = escape(json.dumps(result["parameters"], ensure_ascii=False))
    query = escape(json.dumps(result["query"]))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escaped_name} - 检索评测报告</title>
<style>
body{{font:14px/1.6 sans-serif;color:#182431;background:#eef2f6;margin:0}}
main{{max-width:960px;margin:30px auto;background:white;padding:38px}}
h1{{font-size:25px}} h2{{font-size:18px;margin-top:28px;color:#163a59}}
small{{color:#536273}} table{{border-collapse:collapse;width:100%;margin:12px 0}}
th,td{{border:1px solid #cad3dc;padding:7px 10px;text-align:left}}
th{{background:#edf3f7}} .note{{border-left:4px solid #385e7c;padding:12px}}
code{{word-break:break-all;font-size:12px}} .banner{{font-size:12px;letter-spacing:2px}}
@media print{{body{{background:white}}main{{margin:0;padding:0;max-width:none}}
h2,thead{{break-after:avoid}}tr{{break-inside:avoid}}@page{{size:A4;margin:18mm}}}}
</style>
</head>
<body><main>
<p class="banner">VECTOR RETRIEVAL / EXPERIMENT REPORT</p>
<h1>{escaped_name}</h1><p>{title}</p>
<p class="note">真实运行报告。浏览器可使用打印功能另存为PDF。此报告不代表安全认证。</p>
<h2>1. 任务与数据</h2>{metadata}
<h2>2. 精度与检索质量</h2>{quality}
<p>Recall@K比较明文参考与实测排名的ID集合；近似并列得分可能引起名次交换。</p>
<h2>3. 分阶段测量</h2>{speed}<p>{measurement}</p>
<h2>4. Top-K检索结果</h2>{ranking}
<h2>5. 参数与查询</h2><p><code>{parameters}</code></p>
<p>查询向量：<code>{query}</code></p>
<h2>6. 可追溯性</h2><p>输入快照SHA-256：<code>{snapshot_hash}</code></p>
<p>数据集SHA-256：<code>{dataset_hash}</code></p>
<h2>7. 使用边界</h2><p>{boundary}</p>
<p>本机数据库含原始向量、任务快照与解密结果；请勿录入涉密或生产敏感数据。</p>
<small>由本系统依据已保存任务结果生成；重复实验须另建任务，不能以本报告推断普遍性能。</small>
</main></body></html>"""


def compare_runs(left: dict, right: dict) -> dict:
    if left["status"] != "completed" or right["status"] != "completed":
        raise DomainError("仅支持两个已完成任务的对比", 409)
    a = left["result"]
    b = right["result"]
    dimensions = ["dataset_hash", "candidate_hash", "query", "metric", "top_k_requested"]
    differences = [key for key in dimensions if a.get(key) != b.get(key)]
    if differences:
        raise DomainError(
            "任务输入不一致，不能直接对比：" + ", ".join(differences), 409
        )
    ids_a = {row["id"] for row in a["metrics"]["top_k"]}
    ids_b = {row["id"] for row in b["metrics"]["top_k"]}
    values = [
        ("总耗时（ms）", a["total_ms"], b["total_ms"]),
        ("评分耗时（ms）", a["timing"]["evaluate_ms"], b["timing"]["evaluate_ms"]),
        ("最大绝对误差", a["metrics"]["max_abs_error"], b["metrics"]["max_abs_error"]),
        ("Recall@K", a["metrics"]["recall_at_k"], b["metrics"]["recall_at_k"]),
        ("输入密文字节", a["timing"]["ciphertext_bytes"], b["timing"]["ciphertext_bytes"]),
    ]
    return {
        "left_id": left["id"],
        "right_id": right["id"],
        "left_name": left["name"],
        "right_name": right["name"],
        "compatible": True,
        "overlap_at_k": len(ids_a & ids_b) / a["top_k_requested"],
        "metrics": [
            {"name": name, "left": first, "right": second, "delta": second - first}
            for name, first, second in values
        ],
        "note": "单次本机实测对比，不构成吞吐率、网络性能或安全等级结论。",
    }
