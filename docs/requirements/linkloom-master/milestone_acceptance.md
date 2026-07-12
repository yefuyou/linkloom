# 工作包验收矩阵

| ID | 场景与输入 | 操作 | Expected | Evidence | 当前状态 |
|---|---|---|---|---|---|
| ACC-01 | sample vault | scan twice | Index 字节一致 | 两份 index hash | Scanner 已有证据；本轮未重跑 |
| ACC-02 | index + 未变 fixture | Loader | content hash 一致才输出 NoteDocument | Loader test | Planned WP-1 |
| ACC-03 | traversal/symlink | Loader | 拒绝或跳过且无越界读 | failure test | Planned WP-1 |
| ACC-04 | local Mock | provider call | 无网络、schema 有效 | mock test/trace | 实验存在；产品 Planned WP-2 |
| ACC-05 | one note | topic classifier | schema、证据归属正确 | prediction test | Planned WP-3 |
| ACC-06 | 10 notes | pair generator | 正好 45 无序 pair | generator test | 实验存在；产品 Planned WP-4 |
| ACC-07 | linked pair | relation classifier | source/target/type/evidence 合法 | relation test | Planned WP-4 |
| ACC-08 | inference package | dependency inspection | 不读取 Gold | import/contract test | Planned WP-3 至 WP-5 |
| ACC-09 | perfect Mock | evaluator | 预期满分且标 oracle | metrics/report | 实验历史产物存在 |
| ACC-10 | noisy Mock | evaluator | 出现 FP/FN/方向/类型/证据 Bad Case | bad_cases report | 实验历史产物存在 |
| ACC-11 | malformed Mock | evaluator | 局部 schema error，无全局崩溃 | report/test | 实验历史产物存在；计分语义待修 |
| ACC-12 | valid/invalid evidence | validator | exact substring + 两侧归属 | validator test | 实验存在；产品 Planned WP-5 |
| ACC-13 | real provider | baseline runner | Token/费用/配置版本记录 | manifest | Blocked：凭据与预算门禁 |
| ACC-14 | predictions | review report | 可读 pending items，带证据 | report fixture | Planned WP-8 |
| ACC-15 | 任意 MVP run | immutability | source notes hash 不变 | before/after hashes | Scanner 已有；其余 Planned |

每个阶段完成时，Reviewer 必须给出：实际 changed files、命令与完整结果、样例输出、未运行检查、是否触发 Stop Condition。未运行或超时不等于通过。
