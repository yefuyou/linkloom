# M2-P4 独立只读复核

日期：2026-10-18
工作区：`D:/webproject/Linkloom/work/linkloom-evaluation-qualification`
基线：`af001a4d968676c30979254ac70dfb750e8f9ad6`
分支：`codex/linkloom-m2-progress`

本复核只针对 `task_relation_store.py`、`task_relation_lifecycle.py` 和
`task_relation_query.py` 的当前候选版本，使用合成观察、显式临时 SQLite
sidecar 和既有 P4 Dev 输入。没有调用 Provider，没有运行 H01-H04，也没有
修改生产代码、测试、fixture、Gold 或原库。

## 结论

修复后的候选版本通过本轮独立 Required 复核。初审发现的两个问题均保留了
失败证据，并由当前代码重新探针确认：

1. 当前来源上下文新增编号复用时，旧的受限 replay 不能继续发布
   `SUPPORTED`。修复后返回 `CURRENT_CONTEXT_CHANGED_REASSESSMENT_REQUIRED`
   并保持临时重评估为 `DIAGNOSTIC_ONLY`。
2. `REPLACED` 不能把完全无关的同谓词 Assessment 标成替代关系。修复前实际
   返回 `ACCEPTED`，旧记录变为 `REPLACED`；修复后返回
   `ValueError: replacement endpoint lineage mismatch`。同端点判定纠错正例和独立
   新版本关系分别由 store/query 测试覆盖；同源版本替代约束按来源锚点静态核查。

## 定向证据

| 边界 | 证据 | 结果 |
| --- | --- | --- |
| 旧 replay 被当前竞争观察推翻 | `work/m2-p4-task-relations/reviewer/initial-probe-output.json`（初审失败）；`recheck-output-r2-20261018.json`（修复后） | 初审 `query_current_supported=true`；修复后 `false`，当前独立评估为 `UNKNOWN`，包含 `CURRENT_CONTEXT_CHANGED_REASSESSMENT_REQUIRED` |
| 生命周期终态、乱序/复活与原地篡改 | `recheck-output-r2-20261018.json` | `RETRACTED` 后 `ACTIVE` 复活被 `invalid lifecycle chain` 拒绝；直接 SQL UPDATE 被 append-only trigger 拒绝；读取不发布当前关系 |
| 无关替代关系 | `recheck-output-r2-20261018.json`（初审失败）；`recheck-scope-output-r3-20261018.json`（修复后） | 初审 `append_unrelated_same_predicate=ACCEPTED`；修复后 `ValueError: replacement endpoint lineage mismatch` |
| 缺少 M1 观察、Source Registry 指向新版本、原文字节变化 | `source-readonly-output-ind-r4-20261018.json` | 分别为 `UNVERIFIABLE`、`STALE`、`UNVERIFIABLE`；三种情形 `current_supported=false` |
| 查询只读 | `source-readonly-output-ind-r4-20261018.json` | `PRAGMA query_only=1`、`total_changes=0`；三次查询后 sidecar 字节均未变化 |
| D04/D07/D08/新版本撤销序列 | `docs/requirements/task-relation-lifecycle/evidence/metrics.json` 及对应 JSON | Dev 实际 SQLite 读回 53/53 atomic checks；Provider 0，holdout 未执行 |

## Required 边界判定

- 存储的 Assessment ID、payload 和 checksum 通过 canonical decode、行级
  完整性校验及 replay 再核验；伪造顶层 stored ID 的 SQL 插入只能得到
  `STORED_RECORD_INTEGRITY_FAILED` 诊断，不能发布。直接 UPDATE/DELETE 由
  append-only trigger 拒绝。hash 是损坏检测，不是数字签名；拥有数据库文件
  替换权限的恶意管理员不在本轮认证边界内。
- 事件按显式前驱、单调记录时间和允许转换校验；重复同一合法事件幂等，终态
  不可复活，撤销不会因重新读取旧 Assessment 恢复。依赖撤销会传递阻断下游
  `REVISES_PLAN` 及比较，历史比较仍保留但当前 delta 置空/阻断。
- 当前来源目录缺失、M1 payload 缺失或提取冲突、artifact version/hash 或
  EvidenceSpan 不匹配时 fail closed；不会相信序列化的 `ACTIVE` 或
  `evidence_validity=true`。
- `SOURCE_CLAIM` 只保留来源声明和 `FORMAL_AUTHORITY_NOT_ESTABLISHED`，不会
  生成或修改正式 Decision Change。`REVIEWED_DECISION` 必须重读审核回执；
  关系撤销不撤销正式业务决策。
- 未来生效、未知生效和明确当前日期保持不同语义；同端点竞争日期返回
  `CONFLICT`，不按最新导入时间、mtime 或 Assessment ID 选胜者。历史 STALE
  edge 仅可查看，不参与新的 current verified chain。

## 测试证据与限制

已有聚焦日志显示 store 3 passed、query 8 passed、context 5 passed、stale
plan 6 passed、state semantics 4 passed；独立 Dev 结果为 53/53。既有 M1/P1/
P2/P3/Decision Memory 回归由主流程记录为 285 passed；本复核未重复运行全套
回归。没有声称 CI、部署、真实 Provider、真实 vault 或 H01-H04 保留集通过。

初审失败输出仍保留在 reviewer 目录，没有通过修改旧 Gold、弱化证据或跳过
状态检查来提高结果。当前复核新增的唯一临时材料是：

- `work/m2-p4-task-relations/reviewer/recheck_scope_r3.py`
- `work/m2-p4-task-relations/reviewer/recheck-scope-output-r3-20261018.json`
- 对应的独立临时 SQLite sidecar

本报告只覆盖关系侧库的有限、显式输入和有界 replay；它不证明全局任务发现、
跨数据库同刻快照或未来 Agent/Planner 接线能力。
