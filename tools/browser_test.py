"""Drive the real loopback HTTP application and capture one image per step.

Requires Playwright Chromium; no mocked routes or synthetic screenshots.
The disposable database and password are used only by this verification run.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evidence/browser")
    parser.add_argument("--browser-path", default="")
    arguments = parser.parse_args()
    output = Path(arguments.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    shots = output / "screenshots"
    shots.mkdir(exist_ok=True)
    downloads = output / "downloads"
    downloads.mkdir(exist_ok=True)
    steps = []
    page_errors = []
    password = "browser-test-only-2026"
    with tempfile.TemporaryDirectory(prefix="veclab-browser-") as temporary:
        env = os.environ.copy()
        env["VECLAB_DATA"] = temporary
        env["VECLAB_PORT"] = "8766"
        env["VECLAB_INIT_PASSWORD"] = password
        setup = subprocess.run(
            [sys.executable, "-m", "veclab", "setup"], cwd=ROOT,
            env=env, capture_output=True, text=True, check=True,
        )
        (output / "setup.log").write_text(setup.stdout, encoding="utf-8")
        env.pop("VECLAB_INIT_PASSWORD")
        server_log = open(output / "http-server.log", "w", encoding="utf-8")
        server = subprocess.Popen(
            [sys.executable, "-m", "veclab", "serve"], cwd=ROOT,
            env=env, stdout=server_log, stderr=subprocess.STDOUT,
        )
        base = "http://127.0.0.1:8766"
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("HTTP server exited before readiness")
                    time.sleep(.1)
            else:
                raise RuntimeError("HTTP server did not become ready")
            with sync_playwright() as playwright:
                launch = {"headless": True}
                if arguments.browser_path:
                    launch["executable_path"] = arguments.browser_path
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 960},
                    device_scale_factor=1,
                    locale="zh-CN", timezone_id="Asia/Shanghai",
                    accept_downloads=True,
                )
                page = context.new_page()
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.set_default_timeout(20000)

                def capture(title, action, expected, target=None):
                    view = target or page
                    view.wait_for_timeout(180)
                    view.evaluate("document.fonts.ready")
                    index = len(steps) + 1
                    filename = f"step-{index:02d}.png"
                    path = shots / filename
                    view.screenshot(path=str(path), animations="disabled")
                    steps.append({
                        "step": index, "title": title, "action": action,
                        "expected": expected, "file": "screenshots/" + filename,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "url": view.url,
                    })
                    (output / "steps.json").write_text(
                        json.dumps(steps, ensure_ascii=False, indent=2), encoding="utf-8",
                    )
                    print(f"STEP {index:02d} PASS: {title}", flush=True)

                def nav(route):
                    page.locator(f'[data-route="{route}"]').click()
                    page.wait_for_timeout(250)

                def action(name):
                    return page.locator(f'[data-action="{name}"]').first

                def saved():
                    page.locator("#form-save").click()
                    expect(page.locator("#editor-dialog")).not_to_be_visible()
                    page.wait_for_timeout(200)

                def get_json(path):
                    response = context.request.get(base + path)
                    assert response.status == 200, response.text()
                    return response.json()

                page.goto(base, wait_until="networkidle")
                capture("打开登录页面", "在浏览器打开部署地址，确认软件全称与V1.0版本。",
                        "出现本地工作台登录表单；尚未登录不能访问业务数据。")
                page.fill("#login-user", "admin")
                capture("输入管理员用户名", "在用户名框输入部署初始化时设置的admin账号。",
                        "用户名显示为admin，口令框仍为空。")
                page.fill("#login-password", password)
                capture("输入管理员口令", "输入本机自行设置的管理员口令；示例口令仅用于本次测试。",
                        "口令以掩码显示，不在页面或导出日志中展示原文。")
                page.click("#login-submit")
                expect(page.locator("#workspace")).to_be_visible()
                expect(page.locator("#engine-badge")).to_contain_text("就绪")
                capture("登录并检查工作台", "点击登录系统，检查项目、向量、任务及配置统计。",
                        "进入工作台，右上角显示CKKS引擎就绪。")
                nav("projects")
                capture("进入项目空间", "点击左侧项目空间。", "显示项目列表和新建项目入口。")
                action("new-project").click()
                capture("打开项目编辑器", "点击新建项目。", "弹出项目名称与项目说明表单。")
                page.fill("#field-name", "密态知识向量检索评测")
                capture("填写项目名称", "项目名称填写“密态知识向量检索评测”。",
                        "名称完整显示，尚未提交保存。")
                page.fill("#field-description", "使用合成向量比较真实CKKS与明文基线的得分、排名和耗时。")
                capture("填写项目说明", "填写本次实验目的，不录入业务敏感信息。",
                        "名称和说明均填写完成，可以保存。")
                saved()
                capture("保存项目", "点击保存项目。", "项目列表显示新项目，状态为进行中。")
                project = get_json("/api/projects")[0]
                nav("datasets")
                capture("进入向量数据页面", "点击左侧向量数据。", "可以选择导入数据或生成合成数据集。")
                action("new-dataset").click()
                capture("打开合成数据表单", "点击合成数据集。", "显示所属项目、名称、维度、数量和随机种子。")
                page.select_option("#field-project", project["id"])
                page.fill("#field-name", "知识检索合成向量-12x8")
                capture("选择项目并命名数据集", "选择刚建立的项目，将数据集命名为“知识检索合成向量-12x8”。",
                        "数据将归入指定项目，名称可用于后续任务选择。")
                page.fill("#field-dimension", "8")
                page.fill("#field-count", "12")
                page.fill("#field-seed", "2026")
                capture("设置合成规模和种子", "设置8维、12条记录、随机种子2026。",
                        "参数在允许范围内；合成数据不是实际文本嵌入。")
                saved()
                capture("生成并保存数据集", "点击生成并保存。", "列表显示12条×8维和内容摘要。")
                dataset = get_json("/api/datasets")[0]
                action("view-dataset").click()
                capture("查看数据集详情", "点击该数据集的查看按钮。",
                        "显示规模、内容SHA-256、统计信息和记录明细。")
                page.click("#dialog-close")
                with page.expect_download() as event:
                    action("export-dataset").click()
                event.value.save_as(downloads / "dataset.csv")
                capture("导出向量数据", "关闭详情，点击数据集行内的导出。",
                        "浏览器实际下载CSV文件，并显示导出提示。")
                action("import-dataset").click()
                page.select_option("#field-project", project["id"])
                page.fill("#field-name", "JSON格式样例向量")
                capture("准备JSON导入", "点击导入数据，选择项目并填写新名称，保留JSON示例内容。",
                        "示例含三个4维向量，提供id、label、vector和tags字段。")
                saved()
                capture("校验并导入JSON", "点击校验并导入。",
                        "列表新增3条×4维数据集；不覆盖已有12条×8维数据。")
                nav("profiles")
                action("new-profile").click()
                page.fill("#field-name", "CKKS标准精度-余弦")
                page.select_option("#field-engine", "ckks")
                page.select_option("#field-metric", "cosine")
                page.select_option("#field-preset", "balanced")
                capture("配置真实CKKS检索", "进入参数配置并新建配置：真实CKKS、余弦相似度、balanced。",
                        "显示8192、[60,40,40,60]和2^40，明确不回退明文计算。")
                saved()
                capture("保存CKKS配置", "点击保存配置。", "参数列表出现真实CKKS配置。")
                profiles = get_json("/api/profiles")
                ckks = next(item for item in profiles if item["engine"] == "ckks")
                action("new-profile").click()
                page.fill("#field-name", "明文参考基线-余弦")
                page.select_option("#field-engine", "plain")
                capture("配置明文基线", "再次新建配置，选择明文参考基线与余弦相似度。",
                        "预设自动切换baseline，不生成密文，与CKKS明确区分。")
                saved()
                capture("保存明文基线", "点击保存配置。", "列表同时显示CKKS与明文两种配置。")
                plain = next(item for item in get_json("/api/profiles") if item["engine"] == "plain")
                nav("runs")
                action("new-run").click()
                capture("打开检索任务表单", "进入检索任务，点击新建任务。",
                        "表单包括任务名、数据集、配置、查询向量、Top-K和标签。")
                page.fill("#field-name", "CKKS余弦检索-首次评测")
                page.select_option("#field-dataset", dataset["id"])
                page.select_option("#field-profile", ckks["id"])
                capture("选择任务输入与配置", "命名任务，选择12条×8维数据集及CKKS标准精度配置。",
                        "查询维度提示为8维，配置为真实CKKS。")
                action("sample-query").click()
                expect(page.locator("#field-query")).not_to_have_value("")
                capture("填入首次核对查询", "点击使用数据集首条向量。",
                        "查询框填入完整8维JSON数组，适合验证自相似检索。")
                page.fill("#field-top_k", "5")
                page.select_option("#field-tag", "")
                capture("设置Top-K与筛选", "将返回数量设为5，标签选择不筛选。",
                        "从全部12个候选中返回5个结果。")
                saved()
                capture("创建不可变任务快照", "点击创建任务。",
                        "列表出现待执行任务，输入与配置快照已固定保存。")
                run = get_json("/api/runs")[0]
                action("execute-run").click()
                expect(page.locator("#busy-overlay")).not_to_be_visible(timeout=90000)
                expect(page.locator("#run-table")).to_contain_text("已完成")
                capture("执行真实同态检索", "点击任务行中的运行，并等待本次计算返回。",
                        "任务变为已完成；真实执行密钥准备、加密、密文点积和解密校验。")
                completed = get_json("/api/runs/" + run["id"])
                assert completed["result"]["engine"] == "ckks"
                assert completed["result"]["evaluator_has_secret_key"] is False
                assert completed["result"]["metrics"]["precision_pass"]
                (output / "ckks-result.json").write_text(
                    json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                action("view-run").click()
                expect(page.locator("#result-tab-content")).to_be_visible()
                capture("查看Top-K结果", "点击已完成任务的详情，查看Top-K排名。",
                        "显示Recall@5、最大绝对误差、总耗时及前五个向量得分。")
                page.locator('[data-action="result-tab"][data-id="metrics"]').click()
                page.locator("#result-tab-content").scroll_into_view_if_needed()
                capture("查看精度与耗时", "切换至精度与耗时页签。",
                        "显示误差阈值、精度结论、分阶段耗时与密文字节统计。")
                page.locator('[data-action="result-tab"][data-id="snapshot"]').click()
                page.locator("#result-tab-content").scroll_into_view_if_needed()
                capture("查看输入与可追溯性", "切换至输入与可追溯性页签。",
                        "显示查询、输入及候选摘要、引擎版本和安全边界。")
                with context.expect_page() as popup:
                    action("preview-report").click()
                report_page = popup.value
                report_page.wait_for_load_state("networkidle")
                capture("预览结果报告", "点击预览报告，在新页面查看结果报告。",
                        "报告包含软件名称、任务信息、实际结果和边界说明。", report_page)
                report_page.close()
                for kind, label in (("json", "JSON"), ("csv", "CSV")):
                    with page.expect_download() as event:
                        action("export-" + kind).click()
                    event.value.save_as(downloads / ("result." + kind))
                    capture("导出" + label + "结果", "点击导出" + label + "。",
                            "浏览器下载本次任务结果；文件来自后端已保存的实际计算。")
                action("clone-run").click()
                page.fill("#field-name", "明文余弦检索-同条件对照")
                page.select_option("#field-profile", plain["id"])
                capture("复制为明文对照任务", "点击复制任务，命名副本并选择明文参考基线配置。",
                        "保留原数据集、查询、Top-K和标签，只切换计算配置。")
                saved()
                action("execute-run").click()
                expect(page.locator("#busy-overlay")).not_to_be_visible(timeout=30000)
                expect(page.locator("#run-table")).to_contain_text("明文余弦检索-同条件对照")
                capture("执行明文对照", "保存副本后点击其运行按钮。",
                        "明文任务完成；两种引擎的结果分别保留。")
                runs = get_json("/api/runs")
                plain_run = next(item for item in runs if item["engine"] == "plain")
                assert get_json("/api/runs/" + plain_run["id"])["status"] == "completed"
                nav("compare")
                page.select_option("#compare-left", run["id"])
                page.select_option("#compare-right", plain_run["id"])
                capture("选择同条件任务进行对比", "进入对比评测，左侧选择CKKS任务，右侧选择明文对照。",
                        "两个任务的数据、查询、度量和Top-K保持一致。")
                page.locator('#compare-form button[type="submit"]').click()
                expect(page.locator("#compare-result")).not_to_be_empty()
                capture("查看对比评测结果", "点击开始对比。",
                        "显示任务条件匹配结果以及误差、Recall@K和耗时差异。")
                nav("audit")
                capture("查看审计记录", "点击左侧审计记录。",
                        "可查看创建、运行、完成和导出等真实操作记录。")
                page.select_option("#audit-filter", "run.")
                capture("筛选任务操作记录", "在审计筛选中选择任务类操作。",
                        "列表聚焦任务创建、开始和完成等操作。")
                action("verify-audit").click()
                expect(page.locator("#audit-verification")).to_contain_text("通过")
                capture("核对本地审计链", "点击校验哈希链。",
                        "本地现存链一致性检查通过；不代表外部签名或防管理员重写。")
                with page.expect_download() as event:
                    action("export-audit").click()
                event.value.save_as(downloads / "audit.csv")
                capture("导出审计记录", "点击导出CSV。",
                        "下载最近最多1000条审计记录，便于留存核对。")
                nav("system")
                capture("检查系统维护页面", "点击系统维护。",
                        "显示运行引擎、版本、部署边界及备份入口。")
                action("create-backup").click()
                expect(page.locator("#backup-notice")).to_contain_text("integrity_check=ok")
                capture("创建数据库备份", "点击创建备份。",
                        "备份成功且SQLite完整性检查为ok，列表显示文件名和摘要。")
                action("change-password").click()
                capture("查看修改口令入口", "点击修改口令，确认当前、新口令及确认框。",
                        "提示新口令至少12字符；保存会撤销全部会话。本步骤不修改口令。")
                page.click("#dialog-close")
                page.click("#logout")
                expect(page.locator("#login-screen")).to_be_visible()
                capture("安全退出系统", "关闭口令表单，点击右上角退出登录。",
                        "返回登录页，原会话失效，口令框已清空。")
                assert not page_errors, page_errors
                summary = {
                    "status": "PASS", "steps": len(steps), "page_errors": page_errors,
                    "browser": browser.version,
                    "transport": "real HTTP on 127.0.0.1:8766; no route mocks",
                    "crypto": "real TenSEAL 0.3.16; evaluator context contains no secret key",
                    "downloaded_files": sorted(item.name for item in downloads.iterdir()),
                }
                (output / "summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                context.close()
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            server_log.close()


if __name__ == "__main__":
    main()
