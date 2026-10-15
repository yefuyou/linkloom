# 教学资料验证索引

日期：2026-10-16（Asia/Shanghai）；源码 `cd668f36300e5bfae7e8c654a54359dc40627d33`。仅新增本目录教材/合成摘要；真实Vault与Provider均未使用。原始运行资料受 `/work/` 忽略，不在Git提交范围。

## A：本次本地执行

所有命令在独立学习Worktree运行，使用该checkout的`src`。主线程显式 `PYTHONPATH=src;tests/integration`、`LINKLOOM_RETRIEVAL_MODE=bm25`；Windows临时目录受限时设置TEMP/TMP到新建的`work/tmp-recovery`，不改源码绕过行为。

| 检查 | 结果与原始记录 |
|---|---|
| Product HTTP 三跳 | POST `/api/product/ask`→轮询→COMPLETED，11轮/3Search/7Read，Reviewer 3引用+subject通过；raw `work/interview-lab/781878f951/three-*`，安全索引 [three-hop-summary.json](three-hop-summary.json) |
| 伪造下一跳 | 同入口，owner依赖改Invented Meridian→sequence3调用失败，TOOL_INVALID_ARGUMENTS，4轮后FAILED/无结论/Reviewer未执行；raw `invented-dependency-*` |
| 浏览器 | 在同一个本机Fixture另发同问题，显示Owen Brook、3跳、11轮、三原文来源及SOURCE_SUPPORTED说明；截图 `work/interview-lab/781878f951/browser-three-hop.jpg`。它是另一个run，不与主HTTP Trace混为一条 |
| 主线程workspace/passage/plan | `python -m pytest -q tests/unit/test_product_evidence_context_scope.py tests/unit/test_recovery_passage_coverage.py tests/unit/test_graceful_partial_recovery.py tests/unit/test_bounded_multihop.py`：14 passed，6个Recovery受sandbox temp WinError5阻断 |
| 主线程Recovery重跑 | 设置work内TEMP/TMP后只重跑`test_graceful_partial_recovery.py`，6 passed；日志 `work/recovery-tests.txt`。Python退出时pyreadline析构警告，进程exit0，无测试失败 |
| 主线程source HTTP | `python -m pytest -q tests/integration/test_unified_investigation_flow.py --basetemp=work/pytest-http-interview`，10 passed / 80.34s，允许本机回环后执行；初次未提权的教学Runner因WinError10013失败，记录未冒充成功 |
| 独立Memory调查 | 15文件、138例首次127 passed/11 failed；其中6个Recovery因漏BM25触发被沙箱拒绝的HuggingFace元数据请求，设BM25重跑6 passed；另5例capture旧夹具失败，详下。无Gemini请求 |
| 独立Feedback HTTP | `python -m pytest -q tests/integration/test_feedback_memory_flow.py`：12 passed，26.37s；sandbox内首次12个setup error为localhost权限，允许回环后通过 |
| 独立正式Temporal HTTP | `test_product_transport_http.py::test_formal_http_runtime_payload_and_ledger_are_unchanged`：1 passed /17.06s；FakeTransport |
| 独立分段恢复 | `test_product_segment_recovery.py::test_targeted_recovery_receipts_and_complete_flow`：1 passed /2.05s |

独立调查原始pytest stdout没有另存文件，仅有本次子任务工具记录与笔记 `work/memory_history_notes.md`；不能拿主线程日志冒充独立日志。笔记最终SHA256 `034be6e267de81d63fa8665da95aac4ec303808657c05a3e83b67d5a508a94ae`。上述命令存在重叠，不合并为一次“全仓通过数”。

独立首次138例的文件为：`test_runtime_agent_memory_capture.py`(6)、`test_agent_memory_candidate.py`(22)、`test_agent_memory_shared_lifecycle.py`(18)、`test_context_assembler.py`(13)、`test_decision_memory_write_policy.py`(9)、`test_graceful_partial_recovery.py`(6)、`test_memory_lifecycle.py`(4)、`test_memory_models.py`(4)、`test_memory_policy.py`(7)、`test_memory_store.py`(10)、`test_product_evidence_context_scope.py`(3)、`test_recovery_passage_coverage.py`(4)、`test_recovery_policy.py`(6)、`test_source_identity_contract.py`(12)、`test_temporal_decision_memory.py`(14)。除首个在`tests/integration/`外均在`tests/unit/`。

5个现存失败均在 `tests/integration/test_runtime_agent_memory_capture.py`：

- `test_runtime_team_decision_is_captured_as_durable_non_authoritative_candidate`
- `test_runtime_store_initialization_failure_does_not_fail_successful_answer`
- `test_runtime_capture_failure_does_not_fail_successful_team_decision`
- `test_runtime_does_not_capture_when_grounding_or_reviewer_fails[grounding]`
- `test_cold_recovery_reuses_persistent_store_and_replay_is_idempotent`

旧Fake在第75行只给source_context.intent，被当前完整source identity gate拒绝，尚未到capture。资料只记录未修；候选builder和共享授权生命周期的绿测不替代上述端到端失败。

## B：仅静态核对历史结果

- 两组真实Gemini：`docs/requirements/real-agent-reliability/LIVE_METRICS.json`与`docs/requirements/agent-recovery-optimization/LIVE_METRICS.json`，对应DELIVERY的tokens/请求/费用/时间/Canary。当前无新真实调用、费用或成功率估计。
- 四Bug的旧状态来自历史审计/DELIVERY；另用 `git show 49736a7 -- src/linkloom/runtime/model_loop.py src/linkloom/tools/runtime.py` 与 `git show cd668f3 -- src/linkloom/context/assembler.py src/linkloom/retrieval_v2/runtime_backend.py` 对照旧/新代码。该Git阅读没有切换或改写其他Worktree。
- 原始首次冻结12/16、新冻结首次11/12保留；不重新评分、不改Gold、不合入RC2、不改PR #3。

## 文档交付检查

源码链接由当前文件AST符号行号（JS用function声明）生成；内部链接检查包括文件存在、源码#L指向声明、六文件数量及25题。篇幅按中文汉字和英文单词近似计数，均在任务约定上限内；05为短速查卡，未生成打印PDF。

Mermaid：先查本机与离线缓存，没有解析器；后从官方npm仓库下载固定`mermaid@11.4.1`、`jsdom@26.0.0`，仅放ignored `work/mermaid-check`，禁用安装脚本，无生产依赖变动。官方`mermaid.parse`首次发现时序图消息中的分号会分隔语句，修复后两图均PASS（flowchart-v2、sequence）；日志`work/mermaid-parse-results.json`。这是实际语法解析，未声称在所有Markdown平台视觉渲染一致。

源码/文件链接178项检查通过（含源码声明行），全部源引用由当前文件生成；新增文本无本机绝对路径、凭据或原始Provider请求。临时npm工具、原始Trace、SQLite、截图均留ignored work，不进Git。

## 独立教材核验：PASS

独立审查者只读核验，未改教材/生产/测试、未调用Provider。初审要求修正学习时数、handler返回值层级和Memory测试排除范围；主线程修正并收窄成本措辞后，独立复查PASS。原报告保留于ignored `work/independent-material-review.md`。

| 抽查要求 | 实际完成 |
|---|---|
| 至少10源码链接/行号 | 20项函数、调用点与当前合同一致 |
| 完整三跳 | raw与摘要：11 requests/model executions、10 Ledger calls、39 events；5个raw摘要SHA一致 |
| 3工具I/O | alias Search、E1 Read、owner依赖Search，另核伪造依赖错误 |
| 2 Evidence/Reviewer门 | 已观察ID/来源hash；Final主张与Reviewer引用/subject检查 |
| 2真实Bug位置 | ws/root过滤、source identity必需字段；另核双层passage修复 |
| 2组真实指标 | Reliability与Recovery机器摘要的请求、token、费用、时间及样本限制 |
| 2 Memory边界 | 来源调查关闭Memory/capture；Feedback人确认/作用域与物化durable authorization |
| 可读性/真实性 | 六文件篇幅、25题、12.5h训练；无Harness冒充默认产品、审批/认证/成绩夸大 |

**教材准备与独立核验已完成；学习者的口述、画图及模拟面试均未验收。** 5个旧capture集成失败仍保留。本地测试不等于CI、部署或生产验收。
