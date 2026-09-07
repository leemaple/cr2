# V1.0 实际验证记录（2026-09-07）

软件：密态向量检索与评测管理系统 V1.0。

## 可核对的独立运行

- 代码快照：`c328fcf9fde13c6334af1d4a521ae1be2f5e2283`。
- Actions：https://github.com/leemaple/cr2/actions/runs/34077881623 。
- Artifact：`verified-workbench-evidence`，ID `10002713030`。
- Artifact ZIP：6929409字节，SHA-256 `de651e834754bbe9fad9d2dc01557619d1ce639d63e2eeab163f8a2a1a33ccc6`。
- 环境：Ubuntu 24.04.4 x86-64、Python 3.13.15、TenSEAL 0.3.16、NumPy 2.3.5、Playwright Chromium 143.0.7499.4。

下载并实际检查了tests.xml、tests.log、browser/summary.json、steps.json和46张截图；tested-source.zip中的源文件SHA-256与本地交付代码一致。

## 实际结果

后端：`63 passed, 1 warning in 20.00s`；0失败、0错误、0跳过。warning为Starlette/AnyIO别名弃用提示，不是测试失败。测试包括两个CKKS预设与余弦/点积两种度量的真实密码运算，以及输入校验、鉴权、任务、报告、备份和异常处理。

浏览器：46步完整流程PASS；页面脚本错误0；真实本地HTTP、真实CKKS，无路由mock和静态原型截图。实际下载dataset.csv、result.json、result.csv、audit.csv，实际创建SQLite备份。步骤45仅查看修改口令入口，不提交修改；口令修改另有后端测试。

截图所用CKKS示例：12条8维合成向量，随机种子2026，余弦相似度，首条向量作查询，Top-K=5。最大绝对误差`4.323649677173691e-07`，RMSE `3.492821305864432e-07`，Recall@5为1，检查阈值1e-4通过；评分上下文`evaluator_has_secret_key=false`。这些是该次运行的有界数值结果，不是生产安全认证、语义检索准确率或跨环境性能承诺。

## 交付源程序口径

veclab下14个应用文件：4236个物理行、3940个非空行（含注释、HTML标签和CSS）。按相对路径字典序取前1500及后1500个非空行，源程序Word为60页、每页50行、共3000行。没有用第三方库、测试、工具或空行凑数，没有添加人工行号，没有截断长行。

操作手册由上述46张原始截图逐步编排。申请人身份、联系方式及内部申报Word不上传本公开仓库。

## 已修正问题与证据边界

早期独立运行34077559248的绿色状态无效：实际4项CKKS测试失败，浏览器未完成；原因包括NumPy漏依赖、networkidle等待及CI管道未传递失败退出码。后续显式加入NumPy，改用页面就绪断言，并配置bash pipefail及stderr保全。本文仅采用34077881623的实际完整通过证据，不把历史绿色图标当作测试通过。

CI artifact按工作流保留30天；安装及重现命令见README.md。后续仅修改依赖说明与本验证记录的文档提交，不改变上述应用源码及手册截图快照。登记结论仍由登记机构审查，测试记录不证明未知申请主体的权属。
