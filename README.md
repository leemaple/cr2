# 密态向量检索与评测管理系统 V1.0

独立编写的本地单用户检索评测工作台。以 HEVEC 的密态向量检索应用方向为参考，未复制其实现。使用第三方 TenSEAL 的真实 CKKS 运算，不将第三方库或测试代码计入应用源程序鉴别材料。

## 功能

项目空间、JSON/CSV向量数据导入、可复现合成数据、CKKS/明文检索配置、不可变任务快照、同态评分、明文基线、Top-K排名、精度与耗时对比、HTML报告、JSON/CSV导出、审计链校验、SQLite备份和管理员会话管理。

## 快速运行

已验证目标为 Linux x86_64、Python 3.13、TenSEAL 0.3.16。依赖安装需要联网。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m veclab setup
python -m veclab doctor
python -m veclab serve
```

在浏览器打开 `http://127.0.0.1:8765`。管理员默认名称为admin，口令由部署者在终端设置，至少12字符。没有默认口令。第二次启动仅需激活环境后运行serve。也可执行 `bash run.sh`。Windows提供run.bat作为启动便利脚本，但本次不宣称Windows整机运行已验证。

可选环境变量：`VECLAB_DATA` 为本机数据目录，默认data；`VECLAB_PORT` 为监听端口；始终绑定127.0.0.1。不应暴露到公网。`VECLAB_INIT_PASSWORD` 仅为自动化初始化预留，设置完后清除，不要提交到仓库。

## 数据与计算边界

每个数据集1至64条记录，2至128维，元素为[-1,1]有限实数；余弦模式拒绝零或近零向量。数据集和配置创建后不可改写。JSON输入为记录数组，每条包含id、label、vector、tags。CSV固定表头为id,label,vector,tags，vector是JSON数组，tags用分号分隔。

示例：
```json
[
  {"id":"a","label":"向量A","vector":[1,0,0,0],"tags":["示例"]},
  {"id":"b","label":"向量B","vector":[0.8,0.6,0,0],"tags":["示例"]}
]
```

CKKS标准精度使用8192/[60,40,40,60]/2^40，误差阈值1e-4；紧凑精度使用8192/[50,30,30,50]/2^30，阈值1e-2。分数由加密查询与加密候选向量做真实密文点积后解密。计算上下文不含私钥；密钥仅在本次进程内使用。Top-K在解密后排序。缺少引擎会失败，不会回退成明文或伪造密文。

原始向量、查询、任务快照和解密结果在可信本机进程及数据库中可见。这不是远程不可信服务端隔离方案，不提供全密态Top-K、嵌入模型、LLM服务、生产级安全认证或HEVEC性能复现。误差通过只是本次数据上的数值检查。耗时包含本次运行环境影响，密文字节不包含密钥与协议开销。

## 验证

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q --junitxml=test-results.xml
python -m playwright install chromium
python tools/browser_test.py --output evidence/browser
```

浏览器验证使用真实本地HTTP和实际CKKS，不拦截API或使用静态原型图。每个手册操作对应一张真实截图，步骤说明和图像SHA-256保存在steps.json。使用一次性测试数据库和测试口令，不把数据库/口令带入部署。

## 备份与限制

系统维护页创建SQLite备份并检查完整性。备份含本机原始业务数据和口令哈希，必须按业务敏感等级管理。没有在线还原入口。人工恢复必须先停止服务并保留当前目录副本。审计哈希链只检验现存链一致性，缺少外部锚定，不能证明管理员未重写或截断全部记录。导出CSV对可能触发公式的文本加单引号，含此类文本的CSV往返可能产生有意的安全转义。

## 申请材料

源程序鉴别文档从veclab目录内实际源文件按固定顺序抽取前1500与后1500个非空源代码行，60页、每页50行，不添加行号，不复制第三方代码。完整应用源程序超过3000行；鉴别页不等于完整仓库。操作手册基于真实浏览器验证。著作权人、开发方式、完成/发表事实与权利来源须由申请主体核实；自动化生成、运行通过和行数满足不能保证登记获准。

HEVEC及第三方库边界见THIRD_PARTY_NOTICES.md。请勿上传涉密数据、个人联系方式、私钥、账号口令或公司证照。
