# LinkLoom

[English](README.md) · [简体中文](README.zh-CN.md)

**Team Decision Recovery / Temporal Decision Intelligence（团队决策恢复 / 时序决策智能）**

LinkLoom 帮助项目团队找回当前有效的决定、它替代了什么，以及支持答案的来源证据。
它把持续变化的会议资料转为可审计的时序决策记忆。

用户旅程：

**Sources → Review → Decision Memory → Ask**

示例：**Birchline → 被 Wrenwell 替代 → 来源证据发生变化 →
Wrenwell 标记为 STALE → Review Attention**

## V1 产品界面

以下截图来自真实 LinkLoom 产品浏览器界面，使用合成会议资料和确定性 fake Provider。
它们展示产品 UI；真实 Gemini 使用于另一场 release acceptance。

<table>
  <tr>
    <th>1. Sources</th>
    <th>2. Review</th>
  </tr>
  <tr>
    <td><a href="docs/assets/v1/01-sources.png"><img src="docs/assets/v1/01-sources.png" alt="已导入会议资料、完成 ingestion 并显示精确证据片段" width="560"></a></td>
    <td><a href="docs/assets/v1/02-review.png"><img src="docs/assets/v1/02-review.png" alt="Birchline 决策候选、日期证据和审核操作" width="560"></a></td>
  </tr>
  <tr>
    <th>3. 时序决策记忆</th>
    <th>4. Ask</th>
  </tr>
  <tr>
    <td><a href="docs/assets/v1/03-decision-memory.png"><img src="docs/assets/v1/03-decision-memory.png" alt="当前 Wrenwell 决定、Birchline 前序决定和证据时间线" width="560"></a></td>
    <td><a href="docs/assets/v1/04-ask.png"><img src="docs/assets/v1/04-ask.png" alt="Ask 返回 Wrenwell、生效日期、被替代值和来源证据" width="560"></a></td>
  </tr>
  <tr>
    <th>5. 来源更新 → Review Attention</th>
    <th>Proposal 保持 supporting-only</th>
  </tr>
  <tr>
    <td><a href="docs/assets/v1/05-review-attention-stale.png"><img src="docs/assets/v1/05-review-attention-stale.png" alt="来源变化后，Review Attention 将 Wrenwell 标记为 STALE" width="560"></a></td>
    <td><a href="docs/assets/v1/02-proposal-safety.png"><img src="docs/assets/v1/02-proposal-safety.png" alt="Kestrel Ledger 作为 proposal 展示，不具权威性" width="560"></a></td>
  </tr>
</table>

[Replacement review](docs/assets/v1/02-review-replacement.png) 展示 Wrenwell 的日期证据和批准操作。
[历史 Ask 截图](docs/assets/v1/04-ask-as-of.png)展示 2026-10-07 查询返回 Birchline。

## 为什么决策恢复不能只看文本相关度

通用检索可以找到看起来相关的段落。恢复团队决定还要保留并展示：

- 决定从何时开始有效；
- 它替代了哪项决定；
- 哪个候选经授权后成为权威记录；
- 支持记录的确切来源和证据片段；
- 来源变化是否使证据过期、需要重新审核。

LinkLoom 将检索与时序状态、审核生命周期结合，让答案能追溯到当前或历史决定。

## 四个产品界面

- **Sources** 将带时间戳的资料作为不可变版本导入，并展示精确证据片段。用户主动更新来源会触发 reconciliation。
- **Review** 展示待人工批准的决定。Kestrel Ledger 这样的 proposal 只作为 supporting evidence，不会成为权威记录。
- **Decision Memory** 展示当前和历史记录、生效时间、supersession 与来源溯源。
- **Ask** 基于已批准的 Decision Memory 回答当前或 as-of 查询，并在可用时显示来源证据。

## 验证范围与限制

V1 release acceptance 针对一条合成会议资料工作流：

- 核心旅程通过真实产品浏览器 UI 完成；
- 另一场验收使用真实 Gemini，执行 6 次 GenerateContent 请求，均首次成功；
- 确定性 release gate 的 408 个测试通过；安装 wheel 后的 no-provider HTTP smoke 通过。

这些结果验证的是有界 release 工作流，不代表普遍抽取准确率、所有 Provider 质量、
held-out 性能或生产就绪。LinkLoom 目前不是已部署的多人服务。

## 快速开始

在 LinkLoom checkout 中运行：

~~~bash
python -m pip install -e .
python -m linkloom.ui --port 8765
~~~

打开 http://127.0.0.1:8765/。本地数据库默认位于
~/.linkloom/product.sqlite。UI 默认可以启动，但必须显式启用 Provider 路径后才能完成语义抽取。

若要启用现有 Gemini 路径，在未安装 Google GenAI SDK 时安装仓库已有的
smoke extra；再通过 shell 的 secret manager 设置 GEMINI_API_KEY，并显式 opt-in：

~~~bash
python -m pip install -e ".[smoke]"
python -m linkloom.ui --port 8765 --allow-external-provider
~~~

启用后，导入的来源文本会发送给 Gemini 做语义抽取。Provider 仍通过启动参数和环境配置，
应用内暂不提供 Provider 设置。

无需 key 的确定性预览可打开
http://127.0.0.1:8765/legacy-ask。该页面回放较早的合成决策简报，
不覆盖 V1 的 Sources、Review、Decision Memory 和 Ask 工作流。

## 产品数据流

~~~text
原始资料
  → 不可变来源版本 + 精确证据片段
  → 语义候选
  → 人工审核与授权
  → 时序决策记忆

Ask
  → 时序查询与有界检索
  → 证据组装
  → 有依据的答案

来源更新
  → 证据清单
  → reconciliation
  → Review Attention 中的 STALE 记录

Runtime Agent 结果
  → Reviewer PASS
  → AgentMemoryCandidate
  → 独立审核与授权
  → DecisionMaterializer
  → 时序决策记忆
~~~

通用 Agent Memory 与时序决策记忆相互独立。[60–90 秒 Demo 指南](docs/V1_DEMO.md)、
[Release Notes 草稿](docs/V1_RELEASE_NOTES_DRAFT.md)、[V1.1 backlog](docs/V1_1_BACKLOG.md)
和[产品路线图](docs/PRODUCT_ROADMAP.md)提供更多信息。

## Indexing 索引

| 索引 | 用途与实现 |
|---|---|
| Lexical | BM25 倒排索引，匹配精确名称、ID、日期和术语；复用 `rank-bm25`。 |
| Dense | 本地多语言 embedding，`VectorIndex` 接口后使用 exact cosine；当前小数据集不引入 ANN。 |
| Directory-aware | `DirectoryIndex` 保存父子路径、目录文档、摘要/概览、hash、版本和 summary dirty 状态。 |
| Temporal Decision | SQLite 保存当前/历史决定、supersession、证据来源、action 与 owner 关系。 |

Hybrid 固定融合 BM25 与 Dense 各自最多 20 个候选，使用 RRF（`k=60`）、稳定排序和
证据身份去重。workspace 和可选 directory scope 在排序前应用。目录 scope 选择可配置；
当前冻结评测语料只有平铺目录，不能证明深层目录导航的效果。

## Incremental Update 增量更新

`ContextManifest`、`FileChangeEvent` 和 `IndexUpdateCoordinator` 定义 CREATE、MODIFY、
DELETE、MOVE。hash 未变化的修改是 no-op；内容变化时只更新受影响索引，并将祖先摘要
标记为 dirty。

```text
文件事件 → Manifest / Hash 检查 → 定位受影响资源
         → 更新受影响索引 → 标记祖先摘要 dirty

周期性 Reconcile → 对比 source inventory、manifest 和 indexes
                 → 检测缺失/过期/orphan/hash drift → 修复
```

索引 Reconciliation 是可调用的确定性任务，不是已部署的 scheduler。V1 产品 UI 支持用户主动更新
Sources：创建新的不可变资料版本，通过 exact evidence identity 找出受影响记录，执行 Decision Memory
reconciliation，并在 Review Attention 显示 STALE 记录。这是用户触发的产品流程；任意外部文件系统修改或删除
不会被自动监听，当前没有 OS 级 filesystem watcher。

## Development Evaluation 开发评测

以下有界开发结果只描述对应的冻结运行，不是 held-out 证明、泛化结论或生产成功率：

- V1 Flat BM25 Top-5：5/92（5.43%）；V1 Temporal：2/92（2.17%）。
- 固定 lexical multi-hop pilot：4/16；schema-constrained planner pilot：12/16。
- V2 development run：39/92（42.39%）。
- 在该 V2 设置中，Reader context 比 Flat 少约 68%。

历史 V2 运行协议与限制保存在本地评测证据中。这些数字不证明干净 held-out 表现或普遍检索提升。

## Temporal Decision Memory 时序决策记忆

SQLite 将业务状态演化与文档组织分开：决定记录可关联来源证据、前序决定以及由该决定
创建且有负责人的行动项；按 subject 支持 current、历史和 `as_of` 查询。部分唯一索引防止
同一 workspace/subject 出现两个 current truth。

V1 验收示例中的时序链为 **Birchline → Wrenwell**。Ask 当前状态返回
Wrenwell；查询 2026-10-07 则返回 Birchline。时间线保留两条来源证据和
supersession 边界。来源修改改变 Wrenwell 的 evidence identity 后，该记录会显示
为 STALE。候选仍须通过验证、审核和明确授权才能物化。

## Runtime V2

provider-neutral Runtime V2 实现由宿主程序注入配置好的 `RuntimeEngine`。有来源依据且成功的团队结果可捕获为
持久化 AgentMemoryCandidate，供后续审核。Reviewer PASS 本身不等于物化授权；候选捕获也不会使其成为权威记忆。

每个 model turn 持久化一个结构化 proposal：最终结果，或有序的 tool-call list。Runtime 会
校验并按序执行调用，支持有界 proposal、checkpoint 和 resume，同时保留 provider continuation、
tool ledger、trace、token/cost 和 evidence visibility。检索 backend 可配置，默认 Hybrid；只读
`search_decision_memory` 先校验 workspace 授权，再查记忆并记录命中数和延迟。

## Grounding 与安全

关键 claim 只能引用 Agent 已通过成功检索/读取工具实际观察到的来源证据。验证会检查证据
身份、source hash 和 quote；过期或不可用的 evidence fail closed。Decision Memory 可以帮助
定位当前状态，但不能替代原始来源成为 Grounded Fact。

LinkLoom 将导入资料和决定状态写入本地产品数据库，不会重写、重命名、移动、打标签或删除原始来源文件。
workspace 专属 adapter 在检索前固定 workspace 范围；记忆工具在查询前校验授权 workspace。真实 Vault 写回和多人部署
不属于 V1 范围。

## Evaluation 与 Provider 状态

归档评测报告保留各自协议与历史结果。这些是开发证据，不是 held-out 证明或当前 Provider 成功率。
2026-10-08 的 V1 浏览器验收记录了 6 次真实 Gemini GenerateContent 调用，6 次均首次成功；这只是一个合成
工作流运行，不代表普遍 Provider 质量或生产就绪。

## Governed Learning 治理式学习

现有 Experience / Reflection 与受治理的 Strategy 机制和 Retrieval、Decision Truth 分离。
Experience 可为策略提案提供上下文；策略审批和 rollout 仍受控制。Strategy effectiveness
实验及后续扩展在本 Sprint 冻结或不属于范围，因此不宣称已测得业务效果。参见
[Portfolio Notes](docs/requirements/product_evidence_portfolio_v1/PORTFOLIO_NOTES.md)。

## LangGraph Reference

[`examples/langgraph_decision_agent/`](examples/langgraph_decision_agent/) 提供真实可执行的
确定性参考：typed `StateGraph`、reducer、conditional routing、tool node、内存 checkpointer、
`thread_id`、跨 thread Store 和 interrupt/resume 人工审批。它不迁移生产 Runtime V2，不调用真实
模型或用户 Vault。

[Runtime V2 ↔ LangGraph 映射](docs/architecture/LANGGRAPH_MAPPING.md)记录了 build-vs-buy 边界：
自研 Runtime 用于验证 multi-action durability、partial resume、provider continuation 和
evidence visibility。未来可重新评估是否把通用编排交给 LangGraph，同时将 evidence、workspace
security 和 decision semantics 留在应用层。

## 当前状态与验证

V1 产品 UI 提供 Sources、Review、时序决策记忆和 Ask；用户主动更新来源会触发 exact-evidence reconciliation，
并将受影响决定显示在 Review Attention。本地 release 验收和检查是有界证据，不代表已通过 CI 或已经部署。
产品尚未部署为多人服务。语料是小规模合成数据，不验证深层目录 topology；
确定性旧版 Demo 不调用实时模型。

- [Team Decision eval seed](docs/requirements/m1_team_decision_eval_seed/README.md)
- [Golden8 合成验收记录](docs/requirements/m1_1_team_decision_action/REVIEW_GATE.md)
- [Portfolio Notes](docs/requirements/product_evidence_portfolio_v1/PORTFOLIO_NOTES.md)
- [集成状态](docs/INTEGRATION_STATUS.md)
- [产品路线图](docs/PRODUCT_ROADMAP.md)

## 许可证

LinkLoom 使用 [MIT License](LICENSE)。
