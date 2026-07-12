# Linkloom Master Task Ledger

## 当前总工作包

Linkloom MVP：Scanner -> Loader -> Provider/Schemas -> Topic -> Relation -> Evaluator -> Mock Closure -> Real Baseline -> Human Review（无写回）。

## 当前 Active Work Package

**WP-0：现有资产冻结与合同对齐。** 已执行完成，等待人类确认后进入 WP-1。

## WP-0 完成记录（2026-07-12）

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

## 阻塞

- WP-7：旧密钥必须由人类在 Provider 网关控制台中轮换后，才可进行真实 baseline。
- 跨 WP 连续实施：现有 `AGENTS.md` 仍要求人类在工作包之间确认。

## 下一任务

1. 人类确认 WP-0 完成并轮换旧密钥。
2. 人类批准 WP-1（Read-Only Note Loader）边界后，由 Worker 执行。

## 版本与状态

- Scanner: v1 frozen。
- Dataset: `relation_vault_v1` frozen。
- Provider: Mock 实验可用；真实 Provider baseline blocked（待密钥轮换）。
- Baseline: 无有效真实模型 baseline。
- Human Review: planned WP-8。
- Writeback: forbidden，未来用户产品 Milestone 6 之后另行规划。
