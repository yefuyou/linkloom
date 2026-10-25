# LinkLoom

[English](README.md) · [简体中文](README.zh-CN.md)

**找回当前有效的决定、它经历的变更，以及支持它的原始证据。**

LinkLoom 是一个本地优先的团队知识助手，面向持续变化的会议记录与 Markdown
来源。你可以查找已审核的决策、查看变更历史，并结合原始证据调查任务记录。

## 从会议记录到有据可查的回答

周一会议选择了 **Birchline**，后来的会议决定从 10 月 8 日起改用 **Wrenwell**，
第三份记录只是*提议*采用 Kestrel Ledger。今天批准使用哪家供应商？10 月 7 日
用的又是哪家？记录了什么行动、由谁负责，哪段原文支持这次变更？

LinkLoom 将提案与已批准决定分开，保留生效时间、替代关系与来源证据。导入的来源
更新后，如果证据不再匹配，相关决定会进入待复核状态。涉及原因或来源冲突的问题
可以进入调查路径。

## 看看实际产品

以下为使用合成会议资料的早期**离线演示**截图。
当前操作与两种演示方式见[产品演示指南](docs/V1_DEMO.md)。

<table>
  <tr><th>1 · 导入来源</th><th>2 · 审核候选</th></tr>
  <tr>
    <td><a href="docs/assets/v1/01-sources.png"><img src="docs/assets/v1/01-sources.png" alt="Sources：导入会议与版本化证据片段" width="560"></a></td>
    <td><a href="docs/assets/v1/02-review.png"><img src="docs/assets/v1/02-review.png" alt="Review：Birchline 候选的带日期原文与批准操作" width="560"></a></td>
  </tr>
  <tr><th>3 · 查看决策历史</th><th>4 · 带着证据提问</th></tr>
  <tr>
    <td><a href="docs/assets/v1/03-decision-memory.png"><img src="docs/assets/v1/03-decision-memory.png" alt="Decision Memory：Wrenwell 替代 Birchline 的时间线" width="560"></a></td>
    <td><a href="docs/assets/v1/04-ask.png"><img src="docs/assets/v1/04-ask.png" alt="Ask：Wrenwell 当前决定、生效日期、前序决定与来源原文" width="560"></a></td>
  </tr>
  <tr><th>5 · 发现证据过期</th><th>提案保持为支持材料</th></tr>
  <tr>
    <td><a href="docs/assets/v1/05-review-attention-stale.png"><img src="docs/assets/v1/05-review-attention-stale.png" alt="待复核决策：来源变化后 Wrenwell 标记为 STALE" width="560"></a></td>
    <td><a href="docs/assets/v1/02-proposal-safety.png"><img src="docs/assets/v1/02-proposal-safety.png" alt="Kestrel Ledger 提案仅作支持材料，不是已批准决定" width="560"></a></td>
  </tr>
</table>

还可以查看[替代决定的批准界面](docs/assets/v1/02-review-replacement.png)和
[历史日期的回答](docs/assets/v1/04-ask-as-of.png)。

## 现在能做什么

| 能力 | master 中的实际行为 |
|---|---|
| 导入与提取 | 将带时间戳的文本存为不可变来源版本，保留精确证据片段；语义提取需明确启用 Gemini。 |
| 审核决定 | 查看、批准或拒绝候选；批准与落库保存授权记录。提案只作支持材料。 |
| 回溯决策历史 | 在按工作区隔离的 SQLite 中查询当前或指定日期状态、生效时间、前序决定、证据与授权收据；有证据时记录行动和负责人。 |
| 统一提问 | 已审核状态走确定性查询；原因与冲突问题走明确启用的时序调查 Agent。 |
| 调查来源关系 | 支持“项目 → 负责人 → 直属领导”和“别名 → 项目 → 负责人 → 直属领导”的有界 search/read 计划；不支持的问法要求澄清。 |
| 保留已核验的部分结果 | 来源关系调查无法确认最终关系时，可保留带引用的上游事实并列出缺失的跳；结果仍须通过 证据与结果校验。 |
| 跟踪来源变化 | 在产品中明确更新或删除来源时，对账证据并将受影响的当前已落库决定显示在 待复核决策；没有自动文件系统监听。 |

## 如何协作

```mermaid
flowchart TD
    S["来源文档"] --> V["版本化来源 + 精确证据"]
    V --> I["语义摄取 · LLM 提取"]
    I --> C["候选 · 尚非已批准事实"]
    C --> H["人工审核 + 授权"]
    H --> M["时序决策记忆"]
    U["用户提问"] --> A["Unified Ask · 有界路由"]
    M --> D["当前 / 指定日期查询 · Python"]
    A --> D
    A --> R["只读 Agent 调查"]
    V --> R
    M -. "时序调查中读取已审核状态" .-> R
    R --> G["证据校验 + 确定性 Reviewer"]
    D --> O["回答 + 证据 + 不确定性"]
    G --> O
```

[产品架构图](docs/architecture/PRODUCT_ARCHITECTURE.md)展开已审核事实、来源调查与
证据过期处理。[Runtime 技术架构图](docs/architecture/RUNTIME_ARCHITECTURE.md)
区分真正调用 LLM 的部分和执行确定性 Python 合约的组件。

## 快速开始

需要 **Python 3.11+**，在终端执行：

```bash
git clone https://github.com/yefuyou/linkloom.git
cd linkloom
python -m venv .venv
```

macOS/Linux 使用 `source .venv/bin/activate` 激活环境；PowerShell 使用
`.\.venv\Scripts\Activate.ps1`。然后执行：

```bash
python -m pip install -e .
python -m linkloom.ui --port 8765 --database work/product.sqlite
```

打开[本地界面](http://127.0.0.1:8765/)，创建工作区，查看 Sources、Review、
Decision Memory 和 Ask。**启动不需要密钥。** 新数据库还没有已批准决定，提取与
Agent 调查默认关闭。上面的数据库路径将演示数据留在当前检出目录；省略
`--database` 时默认使用 `~/.linkloom/product.sqlite`。

无需密钥的跨会议任务演示：

```bash
python -m linkloom.ui --port 8765 --database work/task-demo.sqlite --offline-task-demo
```

在“提问”中选择任务调查，询问多场会议是否描述同一任务、各条记录中的负责人或截止日期是什么。
结果会标明“离线演示”，展示来源记录、AI 提案与待确认事项。日期含义不明时保持待确认，
模型提案仍需独立核验。此模式不会调用外部 AI，不能与外部服务启动参数一起使用。
当前界面暂不支持任务关系审批。外部 AI 的独立授权配置见
[任务调查配置说明](docs/requirements/m3-q-real-task-investigation/SPEC.md)。

要体验完整的来源 → 审核 → 回答流程，安装 Provider 扩展，并在环境中配置
`GEMINI_API_KEY`：

```bash
python -m pip install -e ".[smoke]"
python -m linkloom.ui --port 8765 --database work/product.sqlite --allow-external-provider
```

导入[合成会议样例](tests/fixtures/v1_release_acceptance_meeting.txt)，审核 Birchline
和 Wrenwell，然后询问 **“What supplier are we currently using?”**，或选择
**2026-10-07** 查看历史状态。批准前的证据与日期检查见[演示指南](docs/V1_DEMO.md)。

要同时启用原因、冲突与来源关系调查，使用两个权限开关启动（先停止前一个服务）。
最小安装请先选择 BM25：macOS/Linux 执行 `export LINKLOOM_RETRIEVAL_MODE=bm25`，
PowerShell 执行 `$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'`。默认混合模式则需要
`python -m pip install -e ".[retrieval]"`，首次使用可能下载本地嵌入模型。

```bash
python -m linkloom.ui --port 8765 --database work/product.sqlite --allow-external-provider --allow-temporal-agent
```

启用后来源文本会发送给配置的 Gemini 模型，可能产生 Provider 费用；调查还需在
Ask 中逐次授权。产品启动器固定所配置的模型，界面内没有 Provider 设置。
无需密钥的**旧版回放**位于 [legacy Ask](http://127.0.0.1:8765/legacy-ask)，
它不执行当前的来源摄取和审核流程。

## 值得看的工程设计

- **时间与授权都进入数据模型。** 决策有效期、替代关系、证据和授权收据共同持久化；
  提取置信度本身不能使候选成为已批准事实。
- **单一模型循环，明确工具边界。** 模型提出工具调用或结构化 Final；Python 在执行前
  校验参数、工作区、权限与预算，持久化提案和工具结果以支持受控恢复。
- **检索与上下文各司其职。** BM25、可选本地稠密检索与混合融合寻找证据；有预算的
  上下文组装处理范围、去重和过期来源，记忆提示不会变成引用。
- **回答经过证据检查。** Grounding 校验实际观察过的来源身份、哈希与原文；确定性
  Reviewer 把关 Agent 结果。已集成的 Recovery 可让证据耗尽的关系计划以核验后的
  部分结果结束，并区分证据不足与执行结果不确定。
- **来源变化会影响决策状态。** 对账将当前证据清单与落库时的出处比较，可标记
  STALE 或 NEEDS_REVALIDATION，不会悄悄把新候选提升为替代事实。

## 当前状态与限制

实现基线：master `95a125ce4d1b2867102217e1d0430044fbf4f739`，
已包含有界 Recovery 集成。

本地产品已实现 Sources、Review、Temporal Decision Memory 和 Unified Ask。
仓库内的发布证据覆盖合成资料工作流，包含历史 Gemini 验收记录；它们不能证明普遍
提取准确率、独立留出集检索表现或生产就绪程度。协议与限制见
[证据矩阵](docs/requirements/product_evidence_portfolio_v1/EVALUATION_MATRIX.md)。

路由、关系规划和 Grounding 只支持有界的问法与断言类型。调查只读，不能批准决定。
来源更新由用户触发；当前已落库决定的过期状态可进入关注视图，专门的过期重审界面尚未实现。
Runtime 已有 checkpoint/resume，但产品调查任务使用临时、进程内状态，这里不承诺
UI 跨进程恢复，该能力仍未支持。

**PARTIAL** 来源关系回答只保留独立核验且带引用的事实与明确缺口，缺失的最终答案
保持为 null，也不代表已获业务批准。权限或身份校验失败、重复工具错误会终止运行；
在有效 Final 产生前耗尽模型轮次或 Provider 请求额度仍属失败，Provider 执行结果不确定单独呈现。
Recovery 不会把执行失败变成部分成功，也不会自动重试。

这是本地应用，尚非已部署的多用户服务。导入副本与决策状态保存在本地，明确启用的
Provider 请求会离开本机。当前产品流程不会重写原始 Markdown/Obsidian 文件，
真实知识库回写仍在当前产品边界之外。

## 开发者入口

先看[源码阅读导航](docs/README.md)，再看
[产品架构图](docs/architecture/PRODUCT_ARCHITECTURE.md)和
[Runtime 技术架构图](docs/architecture/RUNTIME_ARCHITECTURE.md)。
[Roadmap](docs/PRODUCT_ROADMAP.md)维护产品方向，
[Agent 规则](AGENTS.md)约束贡献边界。历史架构、评估与
[LangGraph 参考映射](docs/architecture/LANGGRAPH_MAPPING.md)保留在开发者导航中。

## 许可证

[MIT](LICENSE)。
