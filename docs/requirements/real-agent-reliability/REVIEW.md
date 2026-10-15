# Real-Agent Reliability 最终独立复核

复核日期：2026-10-15  
复核范围：本 Sprint 的高风险 Required 闭环、当前生产三文件 diff，以及 `scripts/reliability_probe.py` 和 `scripts/reliability_rag_baseline.py` 的实际回归。  
复核身份：独立 Reviewer；不复用或自审本文件之前写过的结论。

本次复核没有调用 Provider，没有读取凭据，没有访问或修改真实 Vault，也没有修改生产代码。只读取了合成 fixture、汇总 ledger/preflight 字段、当前 diff、closure XML 和离线测试结果。

## Required

**无阻断项。Required 闭环客观通过。**

| Required 边界 | 复核证据 | 判断 |
| --- | --- | --- |
| source identity 与 Product workspace / root fingerprint 分离 | `tests/unit/test_source_identity_contract.py` 与 `tests/unit/test_product_evidence_context_scope.py`：**15 passed**。`RetrievalAgent` 传入 Product `workspace_id`，model loop 另外核对 source fingerprint；foreign workspace、错误 fingerprint 和 history 裁剪均有拒绝/保留证据。 | 通过 |
| live 预算、串行和 SDK retry=0 | `final-review-closure.xml` 中 reliability probe 的预算、并发/窗口、媒体/文本 admission、美元上限测试均通过；probe 的 preflight 汇总为 `max_requests=60`、`max_usd=10`、`max_seconds=5400`、`concurrency=1`、`sdk_retries=0`。 | 通过 |
| cross-process OS lock 与 prior-ledger 预算 | closure XML 的 `test_live_lease_blocks_another_process_and_releases_on_close` 通过；各 live attempt 的先前 request 计数进入后续预算，存在未关闭 request 时 fail closed。 | 通过 |
| 固定 synthetic 文件 allowlist + hash | `test_live_dataset_allowlist_blocks_unapproved_text_before_sdk` 通过；live probe 和 RAG baseline 都校验固定 public path/hash 与 freeze 文件，不加载 Gold。 | 通过 |
| canonical ledger/output root | `test_live_rag_cannot_move_its_ledger_outside_canonical_budget_root` 通过；RAG baseline 拒绝 live `output_root` 覆盖。 | 通过 |
| 不自动重发 UNCERTAIN、停止状态可追踪 | 当前汇总为 **50 requests = 47 success + 3 UNCERTAIN**。出现 UNCERTAIN 的 live runs 均已停止；没有观察到自动重发。`STOPPED` 与 `ledger_halted` 的 closure 测试通过。这里的通过含义是 fail closed 和不复发请求，不是 Provider 零 UNCERTAIN。 | 通过 |
| RAG baseline 边界和 grounding | `tests/unit/test_reliability_rag_baseline.py` 的两个固定 relation case、无 tools/verified-hit context、supported/unknown citation 分离测试：**3 passed**。 | 通过 |
| cold resume 不重置 allowance / 不重新消耗 Provider 预算 | `tests/integration/test_m04_production_e2e_resume.py` 的两个 closure case：**2 passed**；旧 fixture 已从 request 复制完整真实 `source_context`。 | 通过 |

closure XML 的总结果为 **11 tests, 0 errors, 0 failures**。本次针对 source identity/context scope 的独立 focused run 另为 **15 passed**。生产 diff 只有三个文件，staged numstat 为 **22 additions / 8 deletions**；`git diff --cached --check` 与工作树 `git diff --check` 均无输出。

预算证据仍需按不同含义读取：已知 usage 估算为 `$0.30025725`，3 个未知 usage 各保留 `$0.129024` 的保守 reservation；按 50 次 reservation 计算为 `$6.4512`，低于 `$10` 上限。没有把这些估算写成 Provider 账单。

## Optional

1. `docs/requirements/real-agent-reliability/SECURITY_AUDIT.md` 中 F-02/F-03 的文字仍按旧审计快照描述“缺失 identity 可被接受”和旧的 workspace/root 混淆。若该文档作为最终当前态交付材料，建议明确标注为 pre-fix/historical，并链接本次 15 个 regression tests；否则读者可能把已修复项误读成仍开放的 finding。该问题不阻断代码或本 Sprint closure。

2. `SingleAgentModelLoop._verified_evidence_context` 仍保留未显式传入双身份时的兼容 fallback；当前生产调用已经同时传 `workspace_id` 和 `source_fingerprint`，本次 15 个 focused tests 覆盖了该路径。未来增加直接调用方时，宜把双身份作为显式 contract，避免 fallback 再次把 Product workspace 与 source root 混用。这是后续接口收紧建议，不是当前已复现的生产回归。

## Closure 与缺失

**客观 closure：PASS（当前 Sprint 的 Required 高风险闭环已闭合，无需追加 Required 修复）。**

这份结论覆盖的是已指定的 focused closure、离线 source/context regression 和 live ledger 的 fail-closed 行为。它不宣称真实 Provider 重新执行、完整历史 suite 全绿或 CI 已验证；本 Reviewer 也没有为了改变这些状态而发起 live 请求。当前三个 UNCERTAIN 记录仍应作为运行事实保留，停止和未知成本 reservation 是其安全收口证据。
