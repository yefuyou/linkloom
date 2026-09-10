# Linkloom Master Task Ledger

## 当前总工作包

`MASTER_SPEC_V2.md`：围绕 Team Decision & Action Agent 关闭 Real Model、
Real Agent Loop、Retrieval + Memory + Context Assembly、Business Vertical
Slice、Human-In-The-Loop、Agent Eval、Reliability & Quantification 七个
Portfolio Gate。

## 当前状态（2026-09-10）

- M0.1、M0.2、M0.3、M0.4 均已 `ACCEPTED`；Gate A、Gate B 与 M0 均为
  `COMPLETE`。
- Gate A 的一次受限真实 Gemini production smoke 已通过：两次 provider
  调用、一次本地 `search_notes`、最终 AgentResult 为 `completed`，usage
  可用且 credential-leak check 为 `PASS`。这不宣称 standalone production
  credential factory 或真实 Gemini restart/resume coverage。
- Team Decision Eval Seed 已 `ACCEPTED AS EVAL SEED`：30 cases、6
  workspaces、36 notes；canonical Git/LF SHA-256 为
  `49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`。
  Golden 8 已在
  [GOLDEN_8_FREEZE.md](../m1_team_decision_eval_seed/GOLDEN_8_FREEZE.md)
  **FORMALLY FROZEN**。
- 当前下一产品边界为 M1 Team Decision & Action，仍需遵守其独立批准的
  implementation boundary；本 PR 不包含 M1 production business flow。

## 历史 Active Work Package（2026-09-10 整合校准）

2026-09-10 整合校准：下文各日期条目保留历史状态。最新
`m0_4_production_e2e_resume_acceptance/task.md` 的 2026-09-09 记录明确记载
人类接受 M0.4、M0 Offline CLOSED、Gate B COMPLETE — OFFLINE。
Gate A 仍为 PARTIAL，真实 Provider smoke 为 NOT_RUN。Trajectory Eval 已有
feature-level 独立验收；Team Decision seed 修正后待 focused 独立复审。
当时用户授权分批提交、推送和补充文档，不包含合并 master 或真实 Provider
调用。批次与验证证据见 [整合记录](../../INTEGRATION_STATUS.md)。

**Master SPEC 已按独立 Reviewer `PASS_WITH_FINDINGS`（无 blocker）完成
clarification，并冻结为 `ACCEPTED AND FROZEN — Planning Baseline V2`。**
M0.1 implementation repair 随后也由独立 Reviewer 以
`PASS_WITH_FINDINGS`（无 blocker）复核，并由人类于 2026-08-30 正式接受。
没有 Gate 因此变为 `COMPLETE`。M0.2 Planner 文档现已创建，当前等待人类
批准或调整，不授权 M0.2 production/test Worker。

## M0.1 Acceptance 记录（2026-08-30）

- Status: `ACCEPTED`.
- Independent review: `PASS_WITH_FINDINGS`; blocking findings: none.
- Successful empty-search regression: resolved and re-reviewed.
- Remaining non-blocking findings: deferred to separately approved work.
- Gate boundary: M0.1 does not complete Gate A or Gate B.
- Git boundary: no commit, push, PR, or GitHub write was authorized.

## MASTER_SPEC_V2 Freeze 记录（2026-08-29）

### 完成工作

- 新增 `MASTER_SPEC_V2.md`，明确 Team Decision & Action Agent 产品故事、
  七个 Portfolio Gate、M0-M5 执行顺序、Child SPEC 合同、Non-Goals 和
  Stop Rules。
- 将 M0-M5 标记为从属于 canonical 六个产品 Milestone 的执行顺序，避免
  建立第二套用户路线图。
- 用当前代码和任务记录校准 Gate baseline：Provider、FakeModel Loop、
  ToolRuntime Retrieval、Memory、Writeback、Trajectory Eval、Recovery 均按
  `PARTIAL` 或 `NOT IMPLEMENTED` 表述，没有把存在的模块等同于完整闭环。
- 保留旧 Master SPEC 作为历史 relation-evaluation spine，并通过 README
  明确其历史状态。
- 落实独立 Reviewer 的全部 documentation findings：固定恰好七个 Gate 及
  primary ownership；把 Portfolio Freeze 分离为非 Gate 的最终步骤；校准
  `previous_tool_call`、trajectory worktree evidence、retry 边界、M0.1 hard
  Non-Goals，以及 M1-M4 同一条渐进 vertical slice 的关系。
- 冻结状态来自独立 Reviewer 的 `PASS_WITH_FINDINGS` 和人类明确指令，不是
  Planner 自我验收。Findings 没有改变七个 Gate、M0-M5 顺序、产品方向或
  canonical 六阶段产品路线图，也没有完成任何 Gate。

### 测试与检查

- 文档范围检查：PASS；本任务只新增/修改
  `MASTER_SPEC_V2.md`、`README.md`、`task.md` 三个允许文件。
- 七个 Gate、六个 M0-M5 执行段、十四个 Child SPEC：PASS；实际计数为
  `7 / 6 / 14`。
- 本地 Markdown 链接目标检查：PASS。
- tracked 文档 `git diff --check`：PASS；三个文档 trailing-whitespace
  扫描：PASS。仅出现现有 Git `LF -> CRLF` 工作区提示。
- 产品测试：本 Planner 文档任务不适用，未重跑。
- 历史全仓记录：`458 passed, 2 skipped, 8 failed, 7 errors`；本次文档
  correction/freeze 未重跑该结果。15 个 non-pass 当时被记录为 legacy
  relation-evaluation 绝对路径问题，因此它不是当前验证，也不能用于声称
  full suite 通过。

### 学习点

1. Provider adapter、持久化 FakeModel Loop 和 production real-provider
   Agent Loop 是三种不同完成状态，不能合并包装。
2. Memory 引用和安全 Writeback 基础已经存在，但前者尚未进入 model-visible
   context，后者尚未接入 Team Decision & Action 业务链。

### 面试证据

- 一份从业务问题映射到七个可验收 Gate 的总控 SPEC。
- 一张基于代码事实的“已有基础 / 缺失闭环”状态表。
- 一条可解释的 `model -> tool -> observation -> model -> final -> proposal
  -> approval` 目标链路及安全边界。
- 明确不夸大的能力边界：无 production real-provider loop、无完整 Context
  Assembly、无业务 Outcome Eval、无量化 Reliability baseline。

### 剩余问题

- 独立 Reviewer 已给出 `PASS_WITH_FINDINGS` 且无 blocker；documentation
  clarifications 已关闭，但这不代表任何 Portfolio Gate 已完成。
- P8.5 correction 尚需独立 re-review，真实 Provider smoke 为 `NOT_RUN`。
- 当前工作树含并发未提交 Runtime/Retrieval/Evaluation 修改，本记录不归因或
  接受这些修改；其中 trajectory 的 `41 / 23 executable / 18
  NOT_IMPLEMENTED` 仅是实验性 worktree evidence，不是冻结能力。
- 全仓测试仍有 legacy absolute-path non-pass，后续必须单独修复或正式隔离。

### 唯一获授权下一任务

唯一获授权的下一任务是审阅
[M0.2 SPEC](../m0_2_durable_real_provider_loop/SPEC.md)、
[implementation plan](../m0_2_durable_real_provider_loop/implementation_plan.md)
和 [task record](../m0_2_durable_real_provider_loop/task.md)，并由人类明确
批准或调整。M0.2 Worker 必须等待；M0.3 不得提前开始。

## 历史 WP-0 完成记录（2026-07-12）

### 凭据清理

- `configs/eval.openai.yaml` 中的明文 `api_key` 已替换为 `null`。
- `base_url` 同步置为 `null`，运行时均通过环境变量回退。
- 工作树中 `sk-` 凭据模式全量扫描结果：**0 处命中**。
- 旧密钥**必须**由人类在对应 Provider 网关控制台中删除并重新生成。从文件中移除不等于已完成轮换。

### 环境变量与 .gitignore

- 新增 `.env.example`，仅含占位值 `your-api-key-here`。
- `.gitignore` 已包含 `.env`、`.env.*`（排除真实凭据）并显式放行 `!.env.example`。
- `.gitignore` 新增 `.artifacts/` 和 `outputs/` 排除生成产物。

### 资产冻结清单

| 资产 | 路径 | 状态 |
|---|---|---|
| Scanner v1 | `src/linkloom/scanner.py` | frozen，不重写 |
| Scanner CLI | `python -m linkloom scan` | frozen |
| Scanner 单测 | `tests/unit/test_scanner.py`（9 tests） | 8 passed, 1 skipped |
| sample_vault | `tests/fixtures/sample_vault/`（5 files） | frozen |
| 综合结构测试 | `tests/fixtures/sample_vault/综合结构测试.md` | 存在，886 bytes |
| test_mixed_structure_parsing | `tests/unit/test_scanner.py:225` | 存在，PASSED |
| relation_vault_v1 | `tests/fixtures/relation_vault/`（10 docs） | frozen |
| Gold Dataset | `tests/eval/relation_gold.yaml` | frozen，未修改 |
| relation_eval 实验包 | `relation_eval/` | 实验可用，未迁移 |
| Mock configs | `configs/eval.mock.yaml` | 可用 |
| OpenAI config | `configs/eval.openai.yaml` | api_key=null，已清理 |
| 凭据隔离测试 | `tests/test_credential_isolation.py`（4 tests） | 4 passed |
| 历史 outputs | `outputs/` | 实验产物，仅证明管线行为 |

### 全量测试基线（2026-07-12 实际运行）

```
collected 28 items
27 passed, 1 skipped in 50.89s
```

跳过项：`test_symbolic_link_is_skipped_when_supported`（Windows 非管理员无 symlink 权限）。

失败项：无。超时项：无。

## 已完成（事实）

- Scanner v1、CLI、sample vault、Scanner unit tests 已存在并全部通过。
- `relation_vault_v1`（10 docs）、Gold Dataset（6 pair labels）和实验 relation_eval 已存在并全部通过。
- perfect/noisy/malformed 历史输出已存在；它们只证明实验管线行为。
- 本 Master Requirements 已作为项目级规划建立。
- WP-0 凭据清理、环境变量回退、.gitignore 更新和凭据隔离测试已完成。

## 历史阻塞（2026-07-12）

- WP-7：旧密钥必须由人类在 Provider 网关控制台中轮换后，才可进行真实 baseline。
- 跨 WP 连续实施：现有 `AGENTS.md` 仍要求人类在工作包之间确认。

## 历史下一任务（已由 V2 校准取代）

1. 人类确认 WP-0 完成并轮换旧密钥。
2. 人类批准 WP-1（Read-Only Note Loader）边界后，由 Worker 执行。

## 历史版本与状态（2026-07-12）

- Scanner: v1 frozen。
- Dataset: `relation_vault_v1` frozen。
- Provider: Mock 实验可用；真实 Provider baseline blocked（待密钥轮换）。
- Baseline: 无有效真实模型 baseline。
- Human Review: planned WP-8。
- Writeback: forbidden，未来用户产品 Milestone 6 之后另行规划。
