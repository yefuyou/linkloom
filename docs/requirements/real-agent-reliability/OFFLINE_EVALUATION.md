# Real-Agent Reliability：离线验收集与 Runner

本文件描述本 Sprint 新增的离线验收边界。数据全部是合成 Markdown 和合成
问题，共 **25 个 case**：9 个 `dev`，16 个 `frozen`。`frozen` 已冻结文本、
Gold 和 rubric hash，但在代码冻结前不运行。

## 数据与盲测边界

公共输入在 `tests/fixtures/real_agent_reliability/public_dev.json` 和
`public_frozen.json`；评分 Gold 在同目录的 `gold_dev.json` 和
`gold_frozen.json`。Public case 只包含 query、workspace、合成 documents、
场景和类别，不包含 expected、facts、required sources、refusal rule 或
rubric-like 字段。Runner 先读取 Public 并把原始执行报告写入 `work/`，执行
完成后才加载 Gold 并生成评分报告；Gold 不进入 Runtime、FakeModel、controller
或工具输入。

合成案例由参与本 Sprint 的 Codex 团队编写，因此这是有 authoring disclosure
的工程验收集，不是严格的人类盲测。它适合验证边界、grounding、失败分类和
轨迹记录，不能据此声称真实模型的泛化能力。

| 分区 | 数量 | 用途 |
| --- | ---: | --- |
| dev | 9 | 先运行，暴露 fixture、runner 和既有 Runtime 接口问题 |
| frozen | 16 | 冻结 hash 后，待代码冻结再执行 |
| 合计 | **25** | 满足 SPEC 要求的 24–36 个新合成案例 |

`public_frozen.json` 一项对应 A–P，一共覆盖：A 当前正式决策、B as-of
历史决策、C 两跳、D 三跳、E 中英混合、F 陌生名、G 歧义、H 关系变更、I
冲突、J 缺失、K 未审提案、L 来源失效、M 大干扰、N workspace 越权、O
恶意来源、P 预算不足。DEV 集补充同一边界的可快速反馈样例，并包含一个
明确 unsupported 的城市问题。

## Runner 合约

入口是 `tests/integration/run_real_agent_reliability.py`。它只接受 Public
作为执行输入；`--gold` 是执行完成后才加载的评分输入。`--allow-frozen`
是显式的冻结执行开关，当前阶段不能使用。

三个可比较的 baseline 使用同一 Public case：

* **A deterministic**：现有 planner 加小型词法事实抽取；决策 case 使用
  已有 `DecisionDiscoveryFixture` 的确定性 Product Ask 路径。
* **B one-pass summary**：一次本地词法检索后交给离线摘要投影，明确标记
  `model: FakeModel/one-pass-summary`、`fake_model_only: true`、
  `grounding_status: unverified_search_summary`，不执行 verified read。
* **C full agent baseline**：关系 case 复用
  `run_bounded_multihop.py` 的 `SourceOnlyController` 和既有 Runtime；决策
  case 复用 `DecisionDiscoveryFixture` 的 temporal Runtime。C 也只使用
  `FakeModel`/本地 fixture，不代表 live Provider。

unsupported routing、正确拒答、Runtime failure 和 Provider uncertainty
是不同的 `outcome` 类别：`unsupported`、`refusal`、`runtime_failure`、
`provider_uncertain`。预算不足的 DEV-08 明确记录为
`runtime_failure/budget_exhausted`；不把它计成拒答。每行还保留
`reason`，例如 `planning_failed`、`conflicting_evidence` 和
`evidence_insufficient`。

每个 baseline 记录以下可复核字段：`citation_valid`、`claim_supported`、
`verified_read_dependency`、`model_turns`、`tool_calls`、`search_count`、
`read_count`、`context_bytes`、`timing_ms`、`fake_model_only`、`model` 和
`provider_uncertainty`。Runtime C 的 `context_bytes` 来自现有 evidence
context；确定性路径和 temporal fixture 没有模型 evidence context 时为 0，
这代表“不适用/未暴露”，不伪装成 Provider token 统计。关系 C 的最终事实
和引用继续经过已有 `SourceOnlyController`/Runtime grounding；Gold 评分再
逐行比较 exact facts、outcome、reason 和 required source，并单独统计引用
有效性、claim 支持和 verified-read 依赖。

报告顶层 `selected_scenarios` 明确记录当前选中的 C；A/C 决策 case 另写入
`a_vs_c_decision_comparison`。B 只作离线 FakeModel 对照。没有 live Provider、
真实 Vault、网络请求、Memory 写入或生产源码改变。

## 已冻结 hash

Hash 是对应 JSON 原始字节的 SHA-256；rubric 版本写在 Gold 文件中。

| 文件 | 分区 | SHA-256 |
| --- | --- | --- |
| `public_dev.json` | dev | `e90bfe02e758e35e4eba887eac46813676146005dc5fa1e7a5495ecfd8e0fb93` |
| `gold_dev.json` | dev / `rar-offline-rubric-2026-10-15/v1` | `3d2a2d6294575a80b0c1aea92bd2c67b65d30a636c6285e71ff782637572f5f6` |
| `public_frozen.json` | frozen | `7dc7d4505c3f642b83b92bae3fc310138e407096ef1320f7a3eab098a1f6c11c` |
| `gold_frozen.json` | frozen / `rar-offline-rubric-2026-10-15/v1` | `b5d60e383f8f43bac79a7b9e1f25c6dd96bffd06110f841b6eadb2dce0466752` |

冻结后只允许改变 Runner 报告或执行产物；不能为了分数修改 frozen Gold
或公共 case。任何 Gold 变更都必须重新 hash 并重新标记为未冻结。

## Dev 证据

使用已存在的本地 BM25 backend，避免离线 runner 尝试下载 embedding 模型：

```powershell
$env:PYTHONPATH = 'src'
python tests/integration/run_real_agent_reliability.py `
  --dataset tests/fixtures/real_agent_reliability/public_dev.json `
  --gold tests/fixtures/real_agent_reliability/gold_dev.json `
  --output work/real_agent_reliability/dev
```

已完成一次 dev run，执行过程没有真实 Provider 请求：`9/9` selected C
case 通过 Gold 的 exact fact、outcome、reason 和 required-source 评分。
结果类别为：`complete=4`、`partial=2`、`refusal=1`、
`runtime_failure=1`、`unsupported=1`。报告还记录了
`citation_valid=6/9`、`claim_supported=6/9`、
`verified_read_dependency=7/9`，以及每个 baseline 的 turns、tool/search/read、
context bytes 和 timing。原始报告先写入 `raw_report.json`，评分报告写入
`report.json`，均位于忽略的 `work/real_agent_reliability/dev/`。

未运行 frozen；以下命令只在代码冻结、主 Agent 明确开始 frozen 首轮后使用：

```powershell
$env:PYTHONPATH = 'src'
python tests/integration/run_real_agent_reliability.py `
  --dataset tests/fixtures/real_agent_reliability/public_frozen.json `
  --gold tests/fixtures/real_agent_reliability/gold_frozen.json `
  --allow-frozen `
  --output work/real_agent_reliability/frozen
```

## 选定场景与 N/A

本小 runner 的 selected 场景为关系和决策的 C；决策 case 同时保留 A/C
比较，B 对不具备关系 planner 的正式决策查询记为 unsupported，不做虚构的
live 比较。

| 目标 | 本 runner 状态 |
| --- | --- |
| live Gemini / 真实 Provider | N/A；由主 Agent 的 live Canary/acceptance 负责 |
| 10/100/500 source scale、scan/prepare 性能 | N/A；由主 Agent 的性能脚本负责 |
| 真实用户 Vault、发布、Memory 写入 | N/A；本集只用 synthetic fixture |
| 生产修复前后指标 | N/A；本 runner 不改生产源码 |
| A/B/C 离线关系与 decision | 已记录；B 明确为 FakeModel，A/C decision comparison 在 report 中 |

这份离线结果证明的是新数据、Gold 隔离、既有 Runtime grounding 和失败分类
可复现；它不替代主 Agent 负责的真实 Provider、性能、代码冻结后 frozen 首轮
或最多两项生产修复验证。

## 真实 B 对照的限制与入口

主 runner 中的旧 B 是“一次词法检索后的离线摘要”，没有 verified reads，
也没有真实 Provider。因此它不能用来证明完整 Agent 优于 B；它只能说明一个
没有读取核验闭环的摘要对照会产生什么结果。它的 `citation_valid` 和
`claim_supported` 失败计数也不能与完成了 verified-read 的 C 直接做公平的
质量结论。

为避免把这个限制误读成 Agent 优势，新增
`scripts/reliability_rag_baseline.py` 作为独立的真实 B 对照。它只读取
`tests/fixtures/real_agent_reliability_live/public.json` 中已经暴露的
`LIVE-2H` 和 `LIVE-3H`，在相同 workspace 文档范围内先用 Runtime 的 BM25
检索一次 `top_k=8`，再把每个命中交给现有
`RuntimeAgentAdapter._read_verified_note_for_tool` 核验，最后进行一次
Gemini 总结。它不加载 Gold、不预填下一跳实体、不进行第二轮，也不涉及
business approval；A 的关系场景比较记为 N/A。

默认运行只做离线 dry-run 准备，不发送 Provider 请求：

```powershell
$env:PYTHONPATH = 'src;tests/integration'
python scripts/reliability_rag_baseline.py
```

只有显式 `--live --attempt <lowercase-id>` 才允许发送，每个 case 一次、全程
最多 2 次；它复用 `scripts.reliability_probe` 的
`seed`、`prior_live_budget`、`bounded_sdk_client`、`write` 以及既有
`AppendOnlyProviderLedger`，沿用 130000ms timeout、SDK attempts=1、总预算和
90 分钟窗口。请求、归一化响应、ledger、tokens/cost/timing 都只写入被忽略的
`work/real-agent-reliability/live-rag-baseline-*`。本轮只完成并验证了 offline
dry-run，未执行真实 Provider；live one-shot 的 `correct` 仍标记为
`unscored_without_gold`，而 citation support 单独由现有
`assess_claim_text` 核验。


## 最终执行补充（原记录不变）

生产冻结后首次16例为12/16，详见DELIVERY.md与OFFLINE_METRICS.json。
在任何frozen执行前，评分条件已强化为：有facts时同时要求citation_valid、
claim_supported和verified_read_dependency；无facts正确拒答不需要虚构引用。
因此作者旧dev 9/9仅为早期评分记录；最终dev回归8/9（同一service citation字段观测差异）。
A/B两例正式答案对但runner读取不存在的verified字段，首分仍保留。
L的valid:false由准备层过滤，不等同实际source更新失效；既有HTTP测试单独验此边界。

冻结身份保存在tests/fixtures/real_agent_reliability/freeze.json。再次执行
--allow-frozen只能称regression，不是新的独立首次验收。Gold未作任何后改。
