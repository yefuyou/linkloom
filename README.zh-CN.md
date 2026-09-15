# LinkLoom

[English](README.md) · [简体中文](README.zh-CN.md)

**恢复团队最后真正做出的决定，并说明为什么这个答案值得相信。**

LinkLoom 是一个本地优先的项目决策恢复工具，面向项目负责人、PM、PMO 和团队
成员。它从散落的项目文档、会议记录与决策记录中自主搜索和读取，最后给出严格、
有证据支撑的简报：最终决定、理由、被否决的方案、行动项、未决问题和明确的不确定性。

它不是聊天机器人、文档摘要器，也不是 Agent Trace 控制台。它回答的核心问题只有
一个：**“所以我们最后到底定了什么？”**

![LinkLoom 决策简报与关联的来源证据](output/playwright/success.png)

## 产品会返回什么

- **决定优先。** 页面先展示最终记录的选择，而不是聊天记录。
- **Claim 级证据。** 每个关键结论都能定位到已验证的来源摘录和精确行号。
- **理由与方案分开。** 支撑决定的理由和真正被否决的替代方案不会混在一起。
- **可执行的后续。** 行动、负责人、截止日期和未决问题保留缺失值，不擅自补全。
- **认真处理不确定性。** 已确认、证据不足和运行失败是三种清晰不同的状态。
- **按需查看过程。** 搜索 / 读取过程属于二级来源说明，不抢占产品主界面。

## 运行确定性的产品 Demo

本地预览使用冻结的合成项目记录，不需要 Provider Key，也不会修改源工作区。

```bash
python -m pip install -e .
python -m linkloom.ui --port 8765
```

打开 `http://127.0.0.1:8765`。内置 `mps-001` 夹具只重放两个已记录的问题：一个
成功的 Atlas Lantern 决策恢复案例，以及一个证据不足案例。任意其他问题会明确失败，
不会得到硬编码答案。

其他已截图状态：

| 初始问题 | 搜索与读取中 | 证据不足 |
|---|---|---|
| ![初始提问](output/playwright/initial.png) | ![运行状态](output/playwright/running.png) | ![证据不足](output/playwright/insufficient.png) |

## LinkLoom 如何工作

```text
决策问题
   │
   ▼
持久化 Agent Loop ──► search_notes / read_verified_note
   │                              │
   │                              ▼
   │                         已验证证据
   ▼
严格 TeamDecisionResult
   │
   ├── 结构契约校验
   ├── claim ↔ 已观察证据校验
   └── Provider 无关的 UI 投影
                         │
                         ▼
                  决策简报 + 来源检查器
```

Runtime 已支持真实多轮 Gemini 与 DeepSeek Provider Adapter。每个持久化模型轮次
只拥有一个结构化动作——一次工具调用或 Final；只读 `ToolRuntime` 会在进入下一轮
前校验调用、预算、结果、checkpoint 和证据。宿主程序可以通过 `RuntimeRunBackend`
注入已配置的 `RuntimeEngine`；上面的模块入口仍是隔离的确定性 Demo。

## 如实呈现真实 Provider 证据

目前有三个冻结合成案例的 DeepSeek 首次结果。这是小规模工程评测，不是 Benchmark，
也不是生产成功率。

| Case | 要验证的产品行为 | 基础设施 | 业务结果 |
|---|---|---:|---|
| `mps-001` | 恢复已批准的模型 Provider | PASS | 正确恢复并锚定 Aster A；但 rejected alternative 分类过宽 |
| `aer-002` | 跨文档恢复评测发布边界 | FAIL | 未评测：首轮响应包含多个工具调用 |
| `iti-005` | 不编造缺失的负责人和截止日期 | FAIL | 未评测：首轮响应包含多个工具调用 |

两个受阻案例都保留了第一次终止结果，没有重采样。共同问题是 Provider–Runtime
能力契约不匹配：根据 [DeepSeek Chat Completions 文档](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)，
`tool_choice: auto` 可能一次返回多个工具调用，而当前持久化 Runtime 每轮只接受一个动作。因此这两次运行的 Contract、Grounding、
Decision、Scope 和 Uncertainty 都是 **N/E（未评测）**，不能冒充语义失败。

完整的分层结论、token、成本、问题分类、产物位置和产品含义见
[真实 Provider 证据矩阵](docs/requirements/product_evidence_portfolio_v1/EVALUATION_MATRIX.md)。

## 安全与可信边界

- 当前产品路径只读；检索工具不能重写、重命名、移动、打标签或删除笔记。
- 测试和 Demo 优先使用仓库中的合成工作区，而不是私人 Vault。
- 关键输出只能引用成功工具结果中已观察到的证据；证据缺失或变更时会 fail closed。
- 文档没有提供负责人或截止日期时，对应字段保持 `null`。
- Provider 错误通过稳定安全代码投影；UI 不暴露原始本地路径或不可信错误消息。
- 未来任何真实 Vault 写入都必须先有逐文件 dry-run、精确批准、源版本校验、备份、
  审计和回滚。

## 当前状态与限制

决策恢复 Runtime、严格结果契约、证据投影、真实 Gemini / DeepSeek 多轮基线和
Product UI V1 已实现并完成本地验证，但它还不是已部署的多人应用。

已知限制：

- CLI UI 入口是确定性的本地 Demo；真实产品宿主需要注入已配置 Runtime；
- Demo 只有一个冻结工作区和两个已记录问题；
- DeepSeek Adapter 当前对多工具调用 fail closed；
- `TeamDecisionResult` 还没有 claim 级 `inferred` 标记；
- 长文分页、Auth、Workspace 管理、部署和真实 Vault 写入不在本阶段范围内。

## 验证与项目入口

Product UI 里程碑记录了 48 个 focused / adjacent 测试通过，覆盖
initial / running / success / insufficient / error 五种浏览器状态、响应式证据弹层、
真实合成 Vault 的 Runtime 投影，以及通过 Antigravity 完成的 Gemini 视觉评审。
冻结的 Team Decision Seed 校验覆盖 30 个 cases、6 个 workspaces 和 36 篇 notes。

建议从这些文档开始：

- [产品路线图](docs/PRODUCT_ROADMAP.md)
- [Product UI 设计与交付](docs/requirements/product_ui_v1/DELIVERY.md)
- [Product UI Design Spec](docs/requirements/product_ui_v1/DESIGN_SPEC.md)
- [Team Decision Eval Seed](docs/requirements/m1_team_decision_eval_seed/README.md)
- [真实 Provider 证据矩阵](docs/requirements/product_evidence_portfolio_v1/EVALUATION_MATRIX.md)
- [作品集叙事与架构图内容](docs/requirements/product_evidence_portfolio_v1/PORTFOLIO_NOTES.md)
- [集成状态](docs/INTEGRATION_STATUS.md)

## 许可证

LinkLoom 使用 [MIT License](LICENSE)。
