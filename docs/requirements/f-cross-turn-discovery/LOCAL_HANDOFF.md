# 交给本地 Codex：F A/B 实验

线上Work只准备并移交实验，不获取/配置/请求Key、不调用Provider、不续写#22账本。
本分支的模型A/B状态仍为NOT_RUN；提交、推送表示交接完成，不表示生产准入。

## 导入独立本地工作树

在本地LinkLoom仓库运行。将 `<REMOTE_SHA>` 替换为交接消息里的精确提交，不使用浮动master。
新目录必须不存在；现有本地Canary和其他工作树保持原样。

```powershell
git fetch origin codex/f-cross-turn-discovery
git worktree add --detach ../linkloom-f-ab-local <REMOTE_SHA>
cd ../linkloom-f-ab-local
python scripts/verify_f_ab_handoff.py
```

新增Git属性只覆盖本专项文件，保留Windows和Unix上的相同字节。校验脚本只需Python
3.11+标准库，不安装SDK，不读环境变量或密钥，不读/写预算，不联网。
哈希不符先停止，不能重新生成Manifest把差异盖掉。

离线测试使用已有开发环境；环境不足时由本地Codex按pyproject的dev依赖准备隔离环境。

```powershell
$env:PYTHONPATH = 'src;.'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q tests/unit/test_f_ab_preparation.py tests/unit/test_f_cross_turn_candidate.py
```

预期39项通过。测试fixture是公开AMI原生转写与历史原始模型提案，不是Gold；
既有合成负例和反事实测试答案保持原样。授权范围仍不包含改答案来通过测试。

## 文件入口

| 文件 | 用途 |
| --- | --- |
| `ab-preparation-v1/candidate.diff` | 原38行生产补丁 |
| `ab-preparation-v1/code-freeze.json` | 默认OFF、ON版本、源码和目标运行时哈希 |
| `ab-preparation-v1/requests/<pair>/OFF.json` / `ON.json` | 20份原样冻结请求 |
| `ab-preparation-v1/PLAN.json` | 每臂请求指纹、输入索引、预算快照和旧准备状态 |
| `ab-preparation-v1/MANIFEST.json` | 原冻结包字节哈希及准备器哈希 |
| `ab-preparation-v1/RESULTS_TEMPLATE.json` | 分层结果模板，NOT_RUN/null |
| `ab-preparation-v1/REVIEW_TEMPLATE.json` | 未填写的逐臂统计模板，无标准答案 |
| `ab-preparation-v1/preparation-tests.xml` | 原39项测试回执 |
| `AB_PREPARATION.md` | 输入/任务与决策契约/统计边界说明 |
| `HANDOFF_MANIFEST.json` | 本次实际提交文件的完整SHA-256清单 |
| `DELIVERY_AUDIT.json` | 定向凭据/隐私/Gold检查结果 |

未交付官方摘要、qrels、Evaluator-only Gold、预登记标准答案文件、逐例Gold矩阵、历史
大ZIP或账本副本。请求里的内容是公开AMI会议原生输入，保留公共语料原话、角色和原生ID。
不要把旧CASE_DESIGN/Gold复制进模型输入或执行包。

## 给本地 Codex 的执行交接指令

先只做导入、校验与本地执行准备；线上Work不再等待Key。

1. 使用上面的精确SHA和MANIFEST检查包，核对20份请求和38行补丁。不要在本工作树重复应用
   candidate.diff，因为补丁已经包含在分支中。仅在独立固定Canary运行时上叠加时才使用该补丁。
2. `prepare_f_ab.py`只是离线准备器，没有真实运行入口。不要把它当作收费Runner，也不要尝试
   把原Canary Runner加开关改造成新调度器。需要接入本地已有Provider执行层时，先准备最小
   执行适配及离线校验，再依据本地任务授权运行；本交接没有声称存在一键真实执行命令。
3. 本地凭据沿用本机已有配置。不要打印、上传、提交或把凭据写进实验包；线上不接收Key。
4. 不续写#22账本、不覆盖或resume旧Canary。PLAN里的28次/USD 7预留和理论余额只是封存
   快照，不是可消费预算。先与本地现有预算授权核对本批分配；完整20请求需原保守规则下
   约USD 5预留，不能用旧剩余两次槽位运行全套，也不能自行新开费用池或按估价退款。
   若本地尚无本批有效预算分配，只完成适配和计划，将这项信息交还用户确认，不发送请求。
5. 原F候选基线为1bfd，目标Canary运行时代码固定为
   `1ade9b2b359b0249ed21678ad09e6aa4d5e82c83`。用另一个隔离执行目录承载相同运行时加这38行
   增量；两臂Grounding/Discovery/Reviewer必须相同，禁止拿本分支较旧的text_scopes结果
   冒充新Canary结果。Extractor基线Git对象相同，已验证OFF请求一致；源码CRLF/LF差异见
   RUNTIME_BYTE_AUDIT.json，严格身份校验按原字节，不能归一化来源或重写旧Manifest。
6. 两臂沿用同一模型和输入配置：gemini-3.8-flash、4096输出token、low thinking、无
   temperature、同profile/schema，F/SDK各一次，无Compactor。不能改输入提示答案，不能
   换成Gemini 2.5，不能自动重试UNKNOWN。若模型/参数不可用，停止并报告，不静默换模型。
7. 严格发送冻结的OFF/ON请求，保留独立的精确请求、原始SDK响应、规范化输出、
   response ID、实际model_version、finish reason、usage和原子发送前预算预留。
   旧OFF响应只作历史参照，不能替代新配对实验。若执行适配改变请求，先离线报告差异，
   不通过重算指纹把未授权差异消掉。
8. 每臂分别统计任务/决策候选、解析损失、task精确引用、Discovery接纳与拒绝、可核验
   真实工作和明显误报。general决定没有逐claim引用字段时该引用统计为N/E，另做原文核验。
   task-only的0 DECISION不是决策召回分母；开发案例不能称正式Precision/Recall。
9. PARTIAL增加本身不算收益；必须增加来源可核验的真实任务，且无新增明显误报。
   否则不提交生产PR。禁止修改生产PR、合并master、放宽Guard或改历史成绩/测试答案。

本地完成真实实验后另行交付结果；本次线上任务在独立分支推送、远端哈希回读后即停止。
