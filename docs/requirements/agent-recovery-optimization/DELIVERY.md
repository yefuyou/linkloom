# Agent Recovery 专项中文交付

基线：`49736a7098b23c8b9f021f98f1fe6d3e7c70e755`。范围：两个生产修复点（P0 恢复、P1 passage 覆盖），P2 交付 Profiling 与后续设计。没有新增依赖、运行时、Ledger、证据库或缓存服务。真实验证因两次严格网络未知按授权规则停止；不能宣称所有验收目标全部通过。

## A. 三项问题：Before → Root Cause → Fix → After

| 问题 | Before | Root Cause | Fix | After 与边界 |
|---|---|---|---|---|
| P0 合法 Partial | 旧 LIVE-MISSING 非法 alternate 被拒后丢失可信上游事实；本轮原始基线缺末跳 7 请求后仍失败 | 原恢复提示侧重重试参数；没有把“已搜索、候选已读、无计划 alternate”的子目标识别为合法停止点；结果阶段把已放弃的查询错误永久当失败 | 复用 Progress/RecoveryPolicy，识别耗尽的可达子目标并关闭工具，要求原 Typed Final 的空主答案 + 独立有据 rationale + 精确未知 ID；只允许一次特定 plan-query 参数错误为 null Final 放弃，身份错误立即停止 | 真实新缺失 Canary：5 请求，2 Search/2 Read，保留 Lena Frost、领导未知、Reviewer 通过。预算耗尽仍报 budget_exhausted；伪造 rationale、完整答案及身份缺失仍拒绝。正式同题 after 遇网络未知，因果比较 N/A |
| P1 同文件多段 | 旧 O 两次 Search/Read 都返回负责人行，领导行未获 passage ID | Retriever 每文档只投影一条正文；其后 ContextAssembler 又按 source_ref 去重，带介绍段的 Canary 暴露第二处丢失 | 既有 Retriever 保留有界匹配 passage，仍执行原 top_k；Assembler 按 evidence_id 去重，保留同文件不同 span | 同文件/噪声新案例离线完整；真实同文件 Canary 6 请求得到 Maya Qin，2 Search/3 独立 Read，Reviewer 通过。正式同题 after 尚未发送，不能用不同题混作 A/B |
| P2 大库读取 | 每次调查全库 read_notes 12 次；旧 500 份约 23.65 秒 | 初始化、上下文、检索和每次 Verified Read 都触发全库路径/内容核验与解析；现有索引复用发生在读取之后 | **未实施生产性能优化**。保留 freshness；完成规模测量和最小后续设计；修正性能观测重叠 | 本机负载波动很大，三次重复不能证明因果加速。详见 E 与 PERFORMANCE.md。不能为了缓存命中跳过来源变更检查 |

停止契约与十项审计回答见 [RECOVERY.md](RECOVERY.md)，两层证据遗漏见 [COVERAGE.md](COVERAGE.md)。生产 `max_steps=12` 未增加。

## B. 真实 Gemini 实验

模型 `gemini-3.8-flash`，SDK `google-genai==2.22.0`，并发 1、自动重试 0。13 次逻辑运行，7 个不同 case，**63 次实际生成请求**。Canary 两次尝试合计 18/20 请求。所有实际请求进入既有 append-only Ledger；发送前检查新批次累计请求、费用、时间与输入字节上限。来源仅为合成 Workspace。

| 分类 | 运行数 |
|---|---:|
| 完整正确 | 5（含原基线正常两跳/三跳） |
| 正确 Partial | 1 |
| 正确全拒答 / Conflict Correct / Unsupported / Wrong Answer | 各 0 |
| Runtime Failure | 4（Canary 初次 1、原基线 3） |
| Input Admission Failure | 1 |
| Budget Exhausted | 0 |
| Provider Known Failure | 0 |
| Provider Outcome Uncertain | 2 |

61 请求取得 Usage：输入 **379,399**、输出 **9,506**、Thinking **45,328** tokens；另外 2 请求 Usage 未知，不能记零。已知 Usage 估算 **$0.49017675**，未知请求按每次最坏 **$0.129024** 预留 **$0.258048**，保守累计 **$0.74822475 < $8**。这是估算及预留，不是账单实付。

首请求 `2026-10-15T14:07:31Z`，末请求结束 `15:14:08Z`，真实阶段墙钟 **3,997.23 秒（66.62 分钟）**；各 case 调查耗时合计 **617.04 秒**，不含间隔修复、等待和准备。SDK 重试数 0。

逐次试验如下；所有试验都列出，没有只选最好结果：

| 批次 | Case | 请求数 | 秒 | 结果 |
|---|---|---:|---:|---|
| Canary after-01 | 缺失 | 1 | 9.49 | 模型漏 document_count，身份门拒绝 |
| Canary after-01 | 同文件 | 6 | 70.95 | 第 7 次提案发送前被旧 64 KiB 输入上限拦截；原 raw 曾误记 budget，派生报告纠正为 input admission |
| Canary after-02 | 缺失 | 5 | 36.88 | 正确 Partial，Lena Frost 已确认、领导未知 |
| Canary after-02 | 同文件 | 6 | 43.64 | 完整正确，Maya Qin |
| baseline-01 | 两跳 | 5 | 53.37 | 完整正确 |
| baseline-01 | 三跳 | 7 | 65.42 | 完整正确 |
| baseline-01 | 缺末跳 | 7 | 67.17 | 非法备用查询，Runtime Failure |
| baseline-01 | 冲突 | 6 | 83.48 | TEAM_DECISION_CONTRACT_ERROR |
| baseline-01 | 同文件 | 6 | 50.32 | 非法备用查询，Runtime Failure |
| after-01 | 两跳 | 5 | 54.99 | 完整正确 |
| after-01 | 三跳 | 7 | 67.16 | 完整正确 |
| after-01 | 缺末跳 | 1 | 7.14 | ConnectError / TLS，执行结果未知 |
| after-01 | 冲突 | 1 | 7.03 | ConnectError / TLS，执行结果未知；停止整个真实批次 |

这两次异常分类来自原始 SDK 异常类型及 transport 证据，不是因为缺 Usage 就一律判未知。仅有明确的连接建立失败才可判 known；此处 TLS 异常未证明 Provider 未执行，维持保守未知。未换 ID 重跑，不再执行同文件 after 和余下七个独立真实案例。

逐 case token、工具数、费用、候选数、错误码及原始文件 SHA 见 [LIVE_METRICS.json](LIVE_METRICS.json)。完整请求、响应与 Ledger 仅留本地 ignored `work/agent-recovery/`，不上传。

## C. 前后对照

同题、同模型、相同生成参数、同一 adapter 导入合成来源。baseline 在新进程加载经逐文件字节核验的准确基线归档；after 加载当前修复源码。Gold 不进入两侧执行。

| 场景 | 原基线 | 本轮同题 after | 可得结论 |
|---|---|---|---|
| 正常两跳 | 5 请求、53.37s、$0.031647 | 5 请求、54.99s、$0.036584 | 正确性保持；此次延迟/费用上升，样本仅各 1 次 |
| 正常三跳 | 7 请求、65.42s、$0.050670 | 7 请求、67.16s、$0.056108 | 正确性保持；不能声称降本提速 |
| 缺失证据 | 7 请求后 runtime_failure | 首请求网络未知 | 改善幅度 N/A；不同缺失 Canary 已展示合法 Partial |
| 同文件多段 | 6 请求后 runtime_failure | 未发送 | A/B N/A；独立 Canary 成功及离线回归支持修复 |
| 冲突 | 6 请求后 contract failure | 首请求网络未知 | A/B N/A；离线保留上游事实与双方来源，但冻结分类仍失分 |

真实测试证明新恢复路径与多段证据链可以工作，尚未完成三个失败类型的严格真实同题前后闭环，不能推广为生产成功率提升。

## D. 检索效果与代价

旧 O 的第 3 行领导关系在候选物化阶段丢失，两个 Read 只读第 2 行。中间开发测量候选由 1 到 3、上下文 884 到 1,751 bytes、仍 2 Search/2 Read，能得到两跳；该测量包含后来移除的排序实验，明确保留为开发证据，不冒充最终 A/B。

最终修复还解决 Assembler 按文件去重的问题。独立同文件 Canary 实际两次 Search 累计返回 7 候选（含跨搜索重复），3 个不同 passage 均独立 Verified Read，得到 Lio Hart → Maya Qin。6 次模型请求，输入 43,531、输出 941、Thinking 4,147 tokens，最大序列化请求 37,917 bytes。没有同题未修复 Canary 的 token 基线，增量 N/A。

候选仍有 Workspace、source_ref、source/index/content version、line range、quote/hash 和 passage ID；没有整文件信任捷径，仍过 read、计划依赖、Grounding 和 Reviewer。top_k、Context item/token 上限保留，噪声与重复 ID 有行为测试。

## E. 性能

所有测量均为离线合成来源，每规模三次，没有 Provider 网络耗时。旧 30 秒 FakeController 在 500 份前置读取期间超时的失败记录保留；可比组仅把 **实验 FakeController** 时间上限设为 180 秒，生产预算未变。

| 规模 | before Snapshot ms | Scan ms | read_notes ms | Retrieval inclusive ms | E2E ms | earlier-after E2E ms |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 109.73 | 126.24 | 1,702.30 | 287.30 | 11,704.68 | 2,905.72 |
| 100 | 528.57 | 246.00 | 11,905.04 | 1,943.98 | 19,757.18 | 7,369.46 |
| 500 | 3,989.65 | 1,580.60 | 110,670.05 | 18,329.98 | 137,034.85 | 115,603.22 |

上述旧观测 retrieval 包含嵌套 read_notes，不能与 read_notes 相加；旧 other 为 N/A。最后修正观测器并补测当前版本（每规模三次成功）的中位数：

| 规模 | Snapshot ms | Scan ms | read_notes ms | Retrieval exclusive ms | Verified Read exclusive ms | Other ms | E2E ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 76.36 | 95.69 | 1,851.07 | 42.26 | 2.23 | 10,622.73 | 12,716.11 |
| 100 | 555.30 | 299.76 | 14,949.61 | 130.06 | 1.90 | 9,477.91 | 25,180.92 |
| 500 | 973.04 | 1,300.18 | 90,085.21 | 737.88 | 2.73 | 10,660.89 | 104,234.32 |

中位数各列独立计算，不能强行相加；逐次分桶可相加。详见 [PERFORMANCE.md](PERFORMANCE.md) 与 [SCALE_METRICS.json](SCALE_METRICS.json)。不同时间本机负载差异明显，最终 500 份三次约 192.45/68.10/104.23 秒，**没有建立因果性能改善**。

瓶颈已确认：每次成功调查 12 次全库 read_notes，2 Search、3 Read；Verified Read 的独占工作仅毫秒量级。保留内容哈希/路径集合检查后，缓存只能先减少解析与对象构造，尚无数据证明收益足够。后续最小设计为任务内不可变快照 + 每边界 freshness 校验，键至少含 workspace/source ID/version/content SHA/index fingerprint/backend/schema。mtime 不能作为唯一依据。本轮不引入缓存或生命周期重构。

## F. 安全与界面

| 边界 | 证据与结果 |
|---|---|
| Workspace、必需 source identity | 原合同与新负例通过；缺 document_count 真实 Canary 被拒，提示修复后成功，没有放松校验 |
| 来源更新、旧 Evidence | source_update/历史日期冻结离线通过；旧 hash/foreign Workspace 负例通过；浏览器更改合成关系后新答案叶青、引用新 artifact，旧陈宁不复用 |
| 未核验 Search Hit、父依赖、假完整结果 | 仍过 Search → Read → Grounding → Reviewer；新增伪造 rationale 与失败后完整 Final 被拒测试 |
| 非法备用工具、重复错误 | 只允许一次狭义计划查询纠错/放弃为 null Final；重复 schema 错误停止；身份/权限/版本错误不可恢复为成功 |
| 缺失与冲突 | 缺失保留可信前缀；冲突显示两方来源、主答案为空；预算耗尽维持失败 |
| Provider permission / UNCERTAIN | 无授权不发外部请求；SDK 自动重试 0；未知不呈现草稿；本批两次未知后停止 |
| Feedback Memory | 原审核、撤销、查询恢复与 HTTP 集成回归通过；Agent 无写 Memory 权限，未审核候选不生效 |
| 正式业务决策 | 普通来源关系仍为 SOURCE_SUPPORTED，不升级业务审批；既有 Unified Ask 正式决策路径回归通过 |
| 来源注入 | 冻结 same-line 指令案例不输出 hijack，不更改工具/Workspace/Memory 权限 |

实际浏览器使用本地 FakeModel 合成 Workspace，已验证完整/部分/冲突/失败/未知、Workspace 切换、日期变更、反馈撤销、连续提问及来源更新。反馈仅在合成 Workspace 由 synthetic-browser-tester 建立、批准并撤销；进行中撤销后旧结果清除。未知主标题明确为“本次调查执行结果不确定，没有可核验的最终结论”。上述旧响应竞态另有确定性 DOM 测试；实际浏览器没有冒充真实 Gemini 或 CI。无新安全回归的已知证据；不是形式化安全证明。

## G. 独立验收与局限

| 证据层 | 成绩/状态 |
|---|---|
| 历史首次冻结 | **12/16 原样保留**；A/B 旧 observer 读不存在 verified 字段；不事后改成 14/16 |
| 本轮新冻结首次 FakeModel | **11/12**，9 complete + 3 partial；引用/Read 检查 12/12 |
| 新 Canary 首次离线 | **1/2**，介绍段暴露 Assembler 去重缺口 |
| 已暴露 Canary 回归 | **2/2**，Partial 与同文件完整分别通过 |
| 最终已暴露 12 例回归 | **11/12**，冲突分类/精确 facts 不符冻结 Gold，仍计失败 |
| 真实模型 | 上述 13 运行：5 full、1 partial、4 runtime、1 admission、2 unknown；不与 FakeModel/单测混算 |
| 重要工程回归 | 133 passed；最后来源/Context/observer 合同 47 passed；相关收尾 focused 与 DOM 通过，详见 VALIDATION.md |
| 独立技术审查 | 结论与针对性闭环见 [REVIEW.md](REVIEW.md) |

所有新输入/Gold 原始字节及代码快照分别冻结。作者接触过 Gold，因此是独立新题且执行不见 Gold，**不是严格外部盲测**。冲突题实际为 partial + 已确认负责人 + conflicting_evidence，双方来源齐全；Gold 要求 refusal + 空 facts。该差异不等于错误认定唯一领导，但严格成绩仍不通过，不能修改 Gold 美化。真实第三批七例因停止条件未运行。

## H. 五个可核验的面试故事

1. **保住部分价值，同时保住证据门。** 旧缺失案例中上游 owner 已经 Read，但下游非法 alternate 造成整题失败。Progress 只在可达目标已搜索、候选已读、无计划备用且没有冲突时建议 FINAL_PARTIAL，模型仍要写原 Typed Final。空主答案和独立引用 rationale 通过同一 Validator/Reviewer；真实缺失 Canary 5 请求保留 Lena Frost，伪造 rationale 测试失败。源码：progress.py、recovery_policy.py、retrieval_agent.py；证据：live-canary-after-02。
2. **执行失败不是业务未知。** max_steps 到顶时不能把尚未接受的草稿包装 Partial。新增 budget UI 分类，5 回合不足的负例保持失败；两次 TLS 请求没有结果/Usage，Ledger 保留未知与费用预留，UI 不显示草稿。可用对照讲清 evidence_insufficient、conflicting_evidence、budget_exhausted 与 provider_outcome_uncertain 的状态差异。
3. **多跳漏证可能发生在两个层。** 旧 O 的 Retriever 每文件只物化一段，先丢领导行；修复候选后，带介绍段的新 Canary 又被 ContextAssembler 按 source_ref 去重。最终按 passage evidence_id 去重，保持 top_k、token cap 和逐段 read。真实同文件例 2 Search/3 Read、6 模型请求；源码和 distinct-ID/cap/noise 测试都可复现，不能笼统归为模型不会推理。
4. **拒绝没有 Freshness 契约的缓存。** 规模测量显示 12 次全库读取；看似可缓存，但原边界每次检查内容与路径。只按 mtime 或 workspace 缓存会让新来源/旧引用混用。当前保留正确行为，提出包含 source/version/content/index/backend/schema 的 run-local 快照设计；没有实施无数据支持的大重构。还发现 inclusive 计时双计，保留旧观察并补正确 exclusive 测量。
5. **真实模型实验先管身份、预算与统计。** 复用原 Ledger，每请求发送前预算预留，真实 baseline 精确归档、独立 run ID、同问题参数；Gold 仅后验。13 运行/63 请求只有 61 Usage，费用分已知 $0.49017675 与未知 $0.258048。两跳/三跳 after 费用与延迟略升，失败题 after 因网络 N/A，不能拿小样本选优宣传。这是成本、正确性与安全停止的实证取舍。

## I. Git、生产改动与复现

独立分支：`codex/linkloom-agent-recovery-optimization`；origin `https://github.com/yefuyou/linkloom.git`；基线如文首。只在本轮工作树开发，原 dirty 工作树与原成果分支未修改。准确交付 Commit、push 回读 SHA 与最终 clean 状态在最终交付消息及本地 ignored `work/agent-recovery/GIT_DELIVERY.json` 记录；提交自身无法在文件中包含自己的 SHA。

生产文件 8 个，`git diff --numstat -- src` 为新增 109、删除 22，**净增 87 行**：

- `agents/retrieval_agent.py`：合法 Partial 提示、狭义错误恢复、预算失败优先。
- `runtime/progress.py`：可信 workspace 与已耗尽子目标观测。
- `runtime/recovery_policy.py`：身份硬停止、一次纠错、FINAL_PARTIAL。
- `runtime/model_loop.py`：传可信 workspace、停止点关闭工具。
- `retrieval_v2/runtime_backend.py`：有界多 passage 物化。
- `context/assembler.py`：按 evidence ID 去重。
- `ui/projection.py`：budget_exhausted 分类。
- `ui/static/product.js`：未知状态精确标题。

新增依赖 **0**。实验适配脚本复用旧 scenario、controller、observer 和 Ledger，属于本次专项复现入口，不是新通用 Harness。没有自动下一轮实验或无人值守任务。

从本工作树运行（Windows SQLite/本机 HTTP 的检查需可写临时目录与 localhost 权限）：

```powershell
$env:PYTHONPATH='src;.'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
& 'D:/Anaconda/python.exe' -m pytest tests/unit/test_graceful_partial_recovery.py tests/unit/test_recovery_passage_coverage.py tests/unit/test_context_assembler.py tests/unit/test_recovery_evaluation.py tests/unit/test_recovery_live_limits.py -q
node tests/ui/unified_ask_dom_test.cjs
& 'D:/Anaconda/python.exe' scripts/recovery_evaluation.py --execute-public tests/fixtures/agent_recovery/public_frozen.json --allow-frozen --code-freeze-manifest work/agent-recovery/fresh-local.manifest.json --output work/agent-recovery/fresh-local.raw.json
& 'D:/Anaconda/python.exe' scripts/recovery_evaluation.py --public tests/fixtures/agent_recovery/public_frozen.json --gold tests/fixtures/agent_recovery/gold_frozen.json --report work/agent-recovery/fresh-local.raw.json --output work/agent-recovery/fresh-local.scored.json
& 'D:/Anaconda/python.exe' scripts/recovery_scale.py --fake-only-deadline 180 --output work/agent-recovery/fresh-scale
& 'D:/Anaconda/python.exe' tests/integration/run_unified_ask_demo.py --source-investigation --port 8797 --db work/agent-recovery/fresh-browser.sqlite
```

真实执行命令曾为 `scripts/recovery_live.py --live --version baseline|after --dataset public_frozen.json --cases AR-F-2HOP,AR-F-3HOP,AR-F-MISSINGLAST,AR-F-CONFLICT,AR-F-SAMEFILE2 --attempt comparison-baseline-01|comparison-after-01`，Canary 使用 `--dataset public_canary.json --canary`。这是已结束实验记录，**不是新调用授权**；本地累计两次未知会拒绝后续发送。

核心问题的答案：**在本轮已验证的有界关系调查中，可以区分证据不足、冲突、执行失败和网络未知；能够保留经 Read/Grounding/Reviewer 接受的可信事实，并对完整答案保持未知。它仍不能保证每次真实模型都能合法收尾；正式缺失/冲突/同文件前后对照尚未闭环，冲突分类验收还有明确差异，P2 未证明安全提速。** 本轮按这份证据与限制交付，不开始下一项功能。
