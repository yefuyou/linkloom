# M3-Q 独立只读复审

日期：2026-10-21  
分支：`codex/m3q-real-task-investigation`  
精确基线：`67b91a1520f6b129985905702f2a2bd19a631882`

## 范围与方法

本次复审独立检查了当前 worktree 的 `AGENTS.md`、M3-Q `SPEC.md`、`AUDIT.md`、`DELIVERY.md`、当前 diff、新增 `src/linkloom/ui/task_provider.py`、相关 `task_investigation.py` / `investigation.py` / `unified_ask.py` / `ask_admission.py` / `semantic_analysis.py` / Runtime 接线及 M3-Q 单元、集成、DOM 与门禁证据。重点覆盖 Provider 显式配置、来源单次授权、任务执行与业务批准隔离、共享请求/Token/费用/时长预算、未知结果不重试、Workspace/来源隔离、冷读、语义提案不升级为可信关系、无新增业务正则及历史冻结。

未修改源代码、未调用真实或付费 Provider、未提交或推送；本文件是唯一写入文件。

## 结论

- 首轮独立复审：P1 为 0 个，P2 为 1 个。
- 修复后的独立定向复审：P1 为 0 个，P2 为 0 个。
- 当前结论：PASS。P2-01 已由主 Agent 修复并通过本报告记录的定向复核；本报告为独立技术签字。

## P2-01：LIVE 冷读对缺失或不完整 budget journal fail open（已修复并关闭）

位置：`src/linkloom/ui/investigation.py:349-360`，`ProductInvestigationService._validate_task_read`。

触发条件：一个 LIVE 任务已有可被其它检查接受的终态 Runtime artifact，但 `root/provider/budget.json` 缺失；或者 journal 形状合法、`failure` 为 `null`，但 `attempts` 中仍有 `UNCERTAIN` / `PENDING` / `FAILED` / `WITHHELD` 行。

实际行为：代码只在 `budget_path.exists()` 为真时读取 journal；缺失时完全跳过 Provider 失败、未知结果和时长检查。文件存在时只检查 `budget.failure` 和由 `started_at` 算出的 elapsed，不检查 attempts 的终态或预留计数。之后若关系结果复核通过，冷读可以返回已接受的 `PARTIAL`。

可复现证据（无文件写入、无 Provider 调用）：

```text
ProductInvestigationService._validate_task_read(
    None,
    state(updated_at=当前时间),
    {},
    root_without_provider_budget,
)
```

返回正常，无异常；同一 helper 对 `failure: null`、`attempts: [{"outcome": "UNCERTAIN"}]`、未超时的内存 journal 也正常返回。对应实现中的可选分支是 `if budget_path.exists():`，而不是 LIVE 任务要求 journal 存在且所有 attempts 成功。现有正常 fixture `work/http-m3q-release/.../provider/budget.json` 证明成功路径会产生该 journal；因此缺失是持久化损坏、删除或不完整恢复时的 fail-open 分支，而非正常离线 Fake 任务的必要文件。

影响：SPEC 要求失败、超时、`UNCERTAIN` 不得发布成功 `PARTIAL`，冷读不得绕过 Provider outcome；当前冷读可能在没有可验证预算收据时重放并展示旧 Runtime 结果。该缺陷需要按执行种类区分：LIVE 缺失 journal、非法 journal 或任何非 `SUCCEEDED` attempt 应阻断；FAKE/REPLAY 离线结果不应被误要求 LIVE journal。

建议修复方向：在 `read_saved_task` / `_validate_task_read` 对 manifest 的 `execution_kind == LIVE` 强制要求完整 budget journal，并校验 journal 的 schema、attempt outcome、reserved counters、usage 与预算上限；任何缺失、未知、未完成或不一致状态均 fail closed。无需改变 Provider 业务语义或历史冻结输入。

修复实现：新增 `validate_task_provider_receipt`。LIVE 终态在 `_validate_task_read` 中强制读取
`provider/budget.json`；缺失、JSON/schema 错误、`failure` 非空、attempt 非 `SUCCEEDED`、序号或
request/response hash 形状错误、请求/Token/费用预留计数不一致、`ModelUsage.total_tokens` 或输入/输出
usage 超出预留、费用或总时长超限，均抛出异常并由热读/冷读转为 BLOCKED。校验还使用 Runtime
完成时间，而不是读时钟。运行中 snapshot 不调用该终态回执校验，因此合法的 `PENDING` 不会被误判为
完成；`FAKE` / `REPLAY` 也不要求 LIVE journal。

首轮 RED 已保存为 `work/m3q-receipt-red.xml`：正式 HTTP 用例在移走 LIVE journal 后，热读实际返回
`PARTIAL` 而非预期的 `BLOCKED`（1 failed）。修复后的独立 focused 复核使用空
`GEMINI_API_KEY` / `DEEPSEEK_API_KEY`、Git 外 basetemp 和 synthetic transport，选择器为：

```text
tests/unit/test_m3q_provider_budget.py::test_completed_provider_receipt_revalidates_budget_and_usage
tests/unit/test_m3q_provider_budget.py::test_cold_read_uses_execution_completion_time_not_later_read_time
tests/integration/test_m3q_provider_host.py::test_live_hot_and_cold_reads_require_complete_provider_receipts
```

结果：`14 passed in 23.39s`。7 种 journal 损坏场景的正式 HTTP 热读与冷读均 BLOCKED，未产生
额外 transport 请求、evidence、trace 或 task review；6 种预算/usage/序号篡改和完成时限单测也均
按预期拒绝。未调用真实或付费 Provider。

## 已确认通过的边界

1. `compose_task_provider` 只接受显式 `GeminiProviderAdapter`、固定 `gemini-3.8-flash` 与 `TaskProviderLimits`；CLI 的任务配置要求 `--allow-external-provider`、任务开关及完整请求/输入/输出/费用/时长预算。任务配置使用 `extractor=None`，配置阶段不会发送来源。
2. LIVE Ask 要求当前 Workspace、`source_context` 快照 hash 和问题 hash 的精确授权；每个阶段和外层 Runtime 绑定同一 `TaskProviderSession`。请求 reservation 在 transport 前持久化并 fsync；请求超时/异常/未知结果不可重试，迟到响应不发布。
3. Provider usage 对 input、output、`total_tokens` 都与本次 reservation 上限比较；实际账单保持 `null`，未知费用没有冒充结算。未发现共享预算绕过或第二个 model loop。
4. 热读/冷读重新验证 Workspace、来源内容/版本、证据撤权和 Runtime 关系结果；冷读使用 ReadOnlyAdapter，不调用 Provider。语义 `SUPPORTED` 等结果保持 proposal-only，不写 P4、不自动批准业务关系。
5. 本轮 diff 未新增业务正则；H01-H04 历史冻结失败未被改写或冒充修复。

## 验证证据与限制

- Core：`work/gates/core-final.xml`，原选择器在 Git 外 synthetic basetemp 重跑，303 passed、2 deselected、无失败。
- 最新 guard：`work/m3q-final-guards-latest.xml`，28 passed；此前 duration/cold-read 组和正常 Host/合同批次保留在 DELIVERY 证据中。
- DELIVERY 记录的 Task workflow、HTTP、契约、Legacy、Frozen、DOM/JS、Chromium 验收均已核对；Chrome errors 为空。
- 真实付费 Provider 请求为 0，不能据此评价线上模型质量、账号可用性或实际账单。
- 旧 P85 Gemini 2.5/temperature=0 批次的历史失败保持原样，不作为本轮修复结果。
- 未重复大规模测试；本次复审只做了针对冷读 journal 的最小只读复现和静态/合同核对。

## 复核状态

P2-01 已完成修复并独立定向复核关闭。当前审查范围内没有剩余 P1/P2；允许主 Agent 按原授权继续
普通 commit/非强制 push 条件核对。真实 Provider/付费模型仍未调用，状态保持
`REAL_PROVIDER_NOT_EVALUATED`；本次没有重跑无关的大套件，也没有将 H01-H04 历史冻结失败改写为通过。
