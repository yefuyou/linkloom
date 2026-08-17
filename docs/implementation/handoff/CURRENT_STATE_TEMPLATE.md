# LinkLoom Anti-Gravity 当前状态交接模板

> 用法：复制到仓库 `docs/implementation/handoff/CURRENT_STATE.md` 后，每个 Phase 只更新这一份当前账本。  
> 规则：只写已由文件、命令、artifact 或 Reviewer 证据证明的事实；计划和推测必须标注。

## 0. 交接元数据

```text
Snapshot date: YYYY-MM-DD
Repository: yefuyou/linkloom
Local checkout: <path or redacted fingerprint>
Revision: <commit/local revision>
Branch: <branch>
Active phase: P1 | P2 | P3 | P4 | P5 | P6 | P7
Phase approval: APPROVED | NOT_APPROVED | BLOCKED
Worker: <name/id>
Reviewer: <name/id or not assigned>
```

## 1. 当前进度

### 已确认事实

- [ ] `src/linkloom/scanner.py` 存在且未修改；Scanner v1 frozen。
- [ ] Scanner CLI 是 `scan INPUT --output OUTPUT`。
- [ ] `tests/fixtures/sample_vault/` 未修改。
- [ ] `relation_eval/` 仍被标为 experimental。
- [ ] `tests/fixtures/relation_vault/` 与 `tests/eval/relation_gold.yaml` 未修改。
- [ ] 当前 Phase 的前置 artifact/schema 存在。
- [ ] Python import 指向当前 checkout，而非机器上的其他 `linkloom` 包。

### 已完成能力（只填已验收）

| 能力 | 状态 | 证据路径/命令 |
|---|---|---|
| Scanner v1 | COMPLETE / BLOCKED | `<...>` |
| P1 walking skeleton | COMPLETE / BLOCKED / NOT_STARTED | `<...>` |
| P2 durable runtime | COMPLETE / BLOCKED / NOT_STARTED | `<...>` |
| P3 event/tracing | COMPLETE / BLOCKED / NOT_STARTED | `<...>` |
| P4 multi-agent | COMPLETE / BLOCKED / NOT_STARTED | `<...>` |
| P5 memory | COMPLETE / BLOCKED / NOT_STARTED | `<...>` |
| P6 evaluation | COMPLETE / BLOCKED / NOT_STARTED | `<...>` |
| P7 synthetic writeback | COMPLETE / BLOCKED / NOT_STARTED | `<...>` |

### 未完成/实验/不可声称

- 实验区：`relation_eval/` 仍然不是正式产品模块。
- perfect Mock：只证明 evaluator plumbing，不证明模型能力。
- 真实 Provider baseline：只有在凭据、预算、cache、retry、synthetic-only 门禁满足后才可声称。
- 真实 Vault：默认未访问；未有独立审批不得访问。
- 写回：没有 exact plan/approval/backup/rollback 证据，不得声称完成。

## 2. 今天只做什么

```text
唯一任务：<一句话，只允许一个 Phase 内的一个垂直切片>
产品结果：<用户能看到什么>
学习结果：<最多两个概念>
面试结果：<一条命令/测试/trace/指标/失败记录>
```

## 3. 当前 Phase 合同

```text
Phase: P?
Unique objective: <copy exact sentence from phase doc>
Non-goals: <copy exact bullets>
Allowed CREATE: <exact paths>
Allowed MODIFY: <exact paths>
DO NOT MODIFY: <exact paths/assets>
Approval boundary: <what human approved>
Stop conditions: <what would block this phase>
```

## 4. 变更文件

### 实际修改

```text
<relative/path/one>
<relative/path/two>
```

### 预计但尚未修改

```text
<relative/path>
```

### Scope guard 检查

- [ ] 没有修改 `src/linkloom/scanner.py`。
- [ ] 没有修改 Scanner fixture。
- [ ] 没有修改 Gold v1 或 relation fixture。
- [ ] 没有修改真实 Vault。
- [ ] 没有新增未批准依赖。
- [ ] 没有执行 GitHub 写操作。
- [ ] 没有因为“顺手清理”触碰无关文件。

## 5. 命令与原始结果

每一条命令都记录完整可复制形式、exit code、关键输出；失败不能只写“失败”。

### 环境确认

```powershell
python --version
python -c "import sys, linkloom; print(sys.executable); print(linkloom.__file__)"
git status --short --branch
git log -3 --oneline --decorate
```

结果：

```text
<paste result; redact secrets and private absolute paths>
```

### 现有基线

```powershell
python -m pytest tests/unit/test_scanner.py -q
```

结果：

```text
exit code: <0/nonzero>
passed: <...>
failed: <...>
skipped: <...>
blocked/error: <...>
```

### 当前 Phase 单测

```powershell
<exact command>
```

结果：

```text
exit code: <...>
<important output>
```

### 当前 Phase 集成/e2e

```powershell
<exact command>
```

结果：

```text
exit code: <...>
<important output>
```

### Demo

```powershell
<exact command>
```

应看到/实际看到：

```text
<copy short result; link artifact below>
```

## 6. Artifact 与合同证据

| Artifact | Schema/version | Hash/数量 | 结论 |
|---|---|---|---|
| `<path>` | `<...>` | `<...>` | PASS / FAIL / N/A |
| `<path>` | `<...>` | `<...>` | PASS / FAIL / N/A |

### Evidence sample

```text
relative_path: <...>
content_sha256: <...>
line_start: <...>
line_end: <...>
quote: <short exact quote or redacted>
status: verified | stale | invalid
```

### Runtime/trace evidence（P2+）

```text
run_id: <...>
thread_id: <...>
checkpoint_id: <...>
event/trace path: <...>
status transition: <...>
resume result: <...>
duplicate side effects: <0 or evidence>
```

### Evaluation evidence（P6+）

```text
dataset_id/version/hash: <...>
gold changed: NO
inference accessed gold: 0
quality_status: <...>
metrics path: <...>
bad_cases path/counts: <...>
unauthorized_write_count: 0
source_mutation_count: 0
secret_leak_count: 0
```

### Writeback evidence（P7 only）

```text
mode: synthetic_fixture_only
plan_id/sha256: <...>
approval_id: <...>
backup_id/verified: <...>
apply_status: <...>
rollback_status: <...>
byte_for_byte_restored: true | false
real vault touched: NO
```

## 7. 失败、阻塞和未运行检查

### 已失败

| 命令/场景 | 原因 | 是否已修复 | 证据 |
|---|---|---:|---|
| `<...>` | `<...>` | YES / NO | `<...>` |

### 被阻塞

| 阻塞条件 | 需要谁/什么变化 | 是否属于当前 Phase | 下一动作 |
|---|---|---:|---|
| `<...>` | `<...>` | YES / NO | `<...>` |

### 未运行

| 检查 | 未运行原因 | 能否声称完成 |
|---|---|---:|
| `<...>` | `<...>` | NO |

典型环境阻塞必须写清楚，例如：

- `pytest` 临时目录 `PermissionError`；
- `python -m linkloom` 命中了错误的已安装包；
- Windows 非管理员无法创建 symlink；
- provider key/预算/网络门禁未获批准。

## 8. 安全与权限声明

```text
Real vault read: NO | YES (exact approved path + reason)
Real vault write: NO | YES (exact plan/approval; only after gate)
Source fixture changed: NO | YES (explain)
Gold changed: NO | YES (new version + human review)
Network used: NO | YES (approved provider + synthetic-only)
Credentials read: NO | YES (never paste value)
Secrets persisted: 0 | <count + stop reason>
GitHub write: NO
```

只要这里不能填“NO”或给出完整批准证据，状态不能是 COMPLETE。

## 9. Reviewer 结论

```text
Reviewer: <...>
Review date: <...>
SPEC/Phase compliance: PASS | FAIL | BLOCKED
Scope compliance: PASS | FAIL | BLOCKED
Safety compliance: PASS | FAIL | BLOCKED
Tests/evidence completeness: PASS | FAIL | BLOCKED
Final status: COMPLETE | IN_PROGRESS | BLOCKED
Reasons: <objective evidence>
```

Reviewer 只报告，不在 review 阶段静默修改实现；主协调者不能用“我看过了”替代 Reviewer 证据。

## 10. 下一步

只允许一个候选任务：

```text
Next candidate: <one task>
Why this follows: <dependency/product/interview reason>
Required approval: <yes/no and exact scope>
Not executing it in this handoff: YES
```

## 11. Anti-Gravity 启动 acknowledgement 模板

Worker 在开始任何已批准 Phase 前，先原样填一份：

```text
我确认：
1. 当前 Phase 是 <P?>，唯一目标是 <...>。
2. 已有事实是 <...>；实验资产是 <...>；未完成是 <...>。
3. 我将只修改 <exact file list>。
4. 我不会修改 Scanner v1、Gold、relation fixture、真实 Vault 或 GitHub 远端。
5. 我不会把 perfect Mock 当真实模型能力。
6. 我会运行 <test/demo commands>，并把失败原文写入 CURRENT_STATE.md。
7. 如果触发 <stop conditions>，我会停止并报告，而不是扩大 scope。
```

