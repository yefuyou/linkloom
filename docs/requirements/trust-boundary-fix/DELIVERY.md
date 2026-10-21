# SPEC: Final 可信边界最小修复交付

分支：`codex/trust-final-evidence`。生产代码提交：`bf080a9`。基线为最新 master `ca2e775ce17581f8b69b2e1f9daf813a1230fb1c`。PR #6 的精确 Head 为 `13adb03c61df8c882a32ce71004b006f7a94fd9b`；本修复不修改 PR #6 分支。

## 1. 两个 P1 的复现结论

使用审计原始攻击输入、FakeModel、BM25、HF 离线和清空密钥，在 master 与 PR #6 精确快照分别重放。E07 只执行 `search_notes`，没有 `read_verified_note`，却得到 Runtime/user `completed`、TeamDecision `approved`、Reviewer `evidence_sufficient`。E15 成功读取正反两份当前来源；将两份一起交给现有结构化 grounding 得到 `CONTRADICTED`，但 Final 只引用正方，仍得到同样的 approved/evidence_sufficient。两版均复现，故不存在版本差异导致的“无法重现”。完整入口、模块路径、Tool Ledger、可见证据、Final、Reviewer 和结果 JSON 见 [replay-results-summary](evidence/replay-results-summary.json) 及 evidence JSON。

原始审计报告位于本地 `D:/webproject/Linkloom/work/trust-audit-20261021/AUDIT_REPORT.zh-CN.md`。复现脚本只修改路径注入，攻击输入与动作不变；[完整证据归档](evidence/attack-replays.zip) 和 [SHA256 清单](evidence/archive-manifest.json) 保存来源、Tool Ledger、各次模型请求/Final 及 Reviewer。原脚本 `baseline_sha` 是历史常量；PR #6 实际代码版本以 manifest、module_path 和 [代码完整性检查](evidence/replayed-code-integrity.json) 为准。该 Windows Git archive 使用 CRLF，六个关键文件与精确对象仅有换行差异。结果中的 `provider_requests` 是原脚本沿用的 FakeModel 回合计数；真实外部 Provider 请求为 0。

## 2. 根因和最小修复点

P1-A 根因在 Final 的 legacy TeamDecision 支持校验：它只把模型选择引用的 refs 交给 `assess_claim_text`；Runtime 虽然已有当前 run 成功 read 的 Ledger，却没有将同 run 的 read evidence 集合作为冲突观察集。现在 RetrievalAgent 从同一 run/task/agent 的成功 `read_verified_note` 记录建立 bounded `read_evidence`，最终支持仍只由各 claim 自己的引用提供，额外的成功 read 仅在相同实体、关系、项目与有效时间范围内通过既有 `_Fact` / `_fact_status` 否决冲突结论。相同范围判断在原 `assess_claim_text` 和 `evidence_supports_typed_claim` 中共享；显式冲突弃答保留空主结论。

P1-B 根因在旧 Final 协议只要求引用属于 observed evidence，没有要求 source claim 绑定成功 read。现在 Runtime 进入 Final 时明确设置 `require_read_evidence=True`；regular TeamDecision 的所有实质 evidence_refs 必须属于成功 read，ClaimProposal 的 source claim 也受同一约束。search-only 不会获得可信 Final；search→read→Final 仍通过。

没有新增安全框架、Provider、关键词冲突规则、M2 重构或第二套 Runtime。

## 3. Before / After

| 场景 | master / PR #6 Before | 修复分支 After |
|---|---|---|
| E07 search-only | completed / approved / evidence_sufficient | failed / TEAM_DECISION_CONTRACT_ERROR；无 result/review/memory candidate |
| E15 read 正反证、Final 只引正方 | completed / approved / evidence_sufficient | failed / TEAM_DECISION_CONTRACT_ERROR；两次 read 保留在 Ledger |
| search→read→Final | 合法 | 仍 completed / approved / evidence_sufficient |
| 不同有效日期 | 既有事实可独立按日期支持 | 独立日期、项目和事件日期控制，不作全局冲突 |
| 无证据弃答 | 既有合同 | 仍通过 |

修复分支同一原始攻击重放结果：E07/E15 均 PASS（即攻击未获可信结果），见 [fixed-results-green](evidence/fixed-results-green.json)。

## 4. 回归和 CI

本地最终新增攻击/控制集：**14 passed**（两个原始 Runtime 攻击、正常 search→read、冲突、日期/项目/缺省项目、typed 引文截短、合法 PARTIAL、无证据弃答和不同 read ID）。可用原有合同 **49 passed**（TeamDecision 33、Grounding 16）。其余 21 项历史回放文件未随 master 提供，另有 1 项 master 原 relation-roles 断言落后于既有 alias 合同；原失败未改写。

完整 Core 首轮 **282 passed，2 deselected**；Legacy **4 passed**；Frozen Ledger **5 passed**。独立审查后的范围修正通过最终 14 项和 TeamDecision 33 项，最终 Head 的完整 Core 由远端 CI 再次执行。PR #6 原版本定向基准 22 passed；隔离的 PR #6 精确快照叠加修复后，完整任务专项 **543 passed**，严格真实 Product HTTP / FakeModel PASS。最终范围修正后的组合定向为 **39 passed / 2 HTTP 超时**；两项均在初始 `http_ask` 的 `urlopen(timeout=3)` 阶段超时，尚未进入失效证据断言。随后原样隔离复查 **2 passed**，timeout 和断言未修改；最终严格 HTTP/FakeModel 再次 **PASS**（只读冷读且业务批准=false）。这两个超时不是一次全绿记录，原始失败及后续闭合证据均保留。

原始 RED：master **2 failed / 1 passed**，PR #6 **2 failed / 1 passed**；同一集成测试在修复树通过。不同项目/事件日期控制先 2 RED 失败，独立审查的缺省项目控制再 1 RED 失败，均随后修复。未删断言、未 skip/xfail、未修改 Gold/Seal/Ledger。早期 runner 参数/命令筛选错误、缺失历史文件及 Windows 临时目录及两项 HTTP timeout 记录均保留在本地 work/；它们不被当成已通过的产品验收。

远端 CI 会在 Draft PR 创建后记录最终 Head；不运行真实 Provider/countTokens/LIVE。

## 5. 独立审查

独立只读 Reviewer 最终结论：**APPROVE Draft PR，无开放 P1/P2**，见 [REVIEW.md](REVIEW.md)。Reviewer 复查了 P1-A/P1-B、项目/日期隔离、typed 引文截短、合法 PARTIAL/弃答及 read receipt，确认无额外 I/O/Provider、无正则补丁。初次临时目录受限及慢集成尝试如实注明，未宣称 Reviewer 自己运行完整 HTTP。该审查针对修复树，不能解释为未改动的 PR #6 当前 Head 已关闭两个 P1；后者仍需先整合修复。

## 6. 对 PR #6 的影响和剩余阻塞

PR #6 的远端分支和内容保持不变。本修复 PR 目标 master，完成后 PR #6 仍需先整合这张可信边界修复并验证新的 Head，之后才考虑合并；在可信修复未合入前，PR #6 的跨会议调查可信性声明保持阻塞。P4 有限只读关系调查仍可作为其自身边界，但不能替代这两个共享 Final 入口修复。

## Changed files

- `src/linkloom/agents/claim_grounding.py`：复用原结构化事实的同范围反证检查。
- `src/linkloom/agents/team_decision.py`：Final read receipt contract、完整观察集 grounding。
- `src/linkloom/agents/retrieval_agent.py`：收集当前 run 成功 read evidence 并接入最终检查。
- `tests/integration/test_final_evidence_trust.py`：保留 E07/E15 原始攻击及 search→read 控制。
- `tests/unit/test_final_trust_boundary.py`：冲突、成功 read、日期和无证据合同。
- `.github/workflows/offline-pr.yml`：将两个新回归文件加入原 Core job。
- 本目录 SPEC、计划、证据和 REVIEW。

## Recommendation

在该独立 Draft PR 的 CI 成功、独立审查通过后，再让 PR #6 以新的可信边界重验。当前不建议把 PR #6 直接合入 master。


## 复现最终回归

```powershell
$env:PYTHONPATH='src;.;tests/integration'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
python -m pytest -q tests/integration/test_final_evidence_trust.py tests/unit/test_final_trust_boundary.py
```

[verification.json](evidence/verification.json) 列出每个批次及 SHA；批次有重叠，不作相加或全仓库覆盖声明。Draft PR、最终 Head 和远端 Actions 收据由最终交付消息给出。
