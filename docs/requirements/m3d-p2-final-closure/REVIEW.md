# M3-D P2-A/P2-B 最终联合集成独立只读复审

复审目录：`D:\webproject\Linkloom\work\m3d-p2-final-closure`  
基线：`33390e2eeaad71d215e279b2f724cae672ea4f80`；审查 HEAD：`73600459e`。  
范围：基线到 HEAD 的 11 个生产 delta、当前联合测试、CI 入口，以及实际 Product/Runtime 冷读生命周期。未修改生产代码、测试、提交或分支；无付费 Provider，所有 key 为空。

## 结论

- **P1：0**
- **P2：0（生产联合调用链）**
- 联合链结论：**PASS**，以主 Agent 已提供的 63 个独立 P2 通过收据和 2 个 joint Product 测试通过结果为依据。
- 先前提出的 terminal-checkpoint P2 已用真实 Product/Runtime 复现撤销；direct `ToolRuntime` 的孤立磁盘行现象保留为低层接口边界观察，不构成 Product 越权。

## P2-A 生产边界

`TaskObservation` 对 v1/v2 使用不同的身份前缀和重建规则；v2 的 work identity 绑定 workspace、artifact version、evidence、message 和唯一 field evidence，重放不接受模型修改后的 raw/window/field 解释（`src/linkloom/semantic_ingestion/task_observation.py:102-109,178-190,224-250,380-465`）。

Discovery 只释放当前 registry 中 hash/version 完全匹配的来源；同一 raw/message/owner quote 下不同工作由各自 task quote 参与 v2 身份，冲突按 observation ID 隔离，不把一个 work 的观察覆盖另一个 work（`src/linkloom/semantic_ingestion/task_observation_discovery.py:115-118,140-163,275-319`）。

## P2-B Host 认证与依赖边界

`inspect_task_context` 的可用结果必须来自真实 `ToolExecutionRecord`：run/task/agent/tool/workspace、arguments、result identity、当前 source、ObservationRef 全部重放验证；receipt 的 plan/step/result hash/outcome/receipt ID 和完整端点均核验，重复 step 被拒绝（`src/linkloom/tools/task_relations.py:408-463`）。

Query 只读取同一 run/task/agent、query 之前的已完成 inspect 记录，模型提供的 receipt/verified result 不进入信任边界；计划 snapshot hash 与独立注入的 inspection snapshot hash 必须相等（`src/linkloom/tools/task_relations.py:465-527`）。Reviewer 还要求当前 model execution 推导出的 canonical task、同 run Ledger、无 error 的 completed result，并重新执行 inspect/query 复核（`src/linkloom/agents/task_relation_result.py:32-124`）。

因此模型或 trace 伪造 `COMPLETED` 不能直接放行；semantic proposer 仍是未接受 proposal，不能自动提升为 P4（`src/linkloom/tools/task_relations.py:172-296,719`；`src/linkloom/agents/task_relation_result.py:45-51,124`）。

## 联合 Product 证据

联合测试使用同一 `SHARED_TEXT`、message、owner quote，生成两个不同 v2 work；实际 inspect 只返回 A 的完整 `ObservationRef`。A 有真实匹配 P4 时为 `SUPPORTED`，B 缺少 inspect/P4 时为 `BLOCKED`，共享角色或引用不能继承错误 work 的关系（`tests/integration/test_p2_joint_product.py:338-385`）。

测试还验证同 run hot read、持久 cold read 与 fresh subprocess cold read 结果一致，且没有额外 Provider request（`:387-414`）。来源版本变更后，旧 inspection、trace、evidence 和 task review 均被撤权，热读、持久冷读和 fresh cold read 都是 `BLOCKED`（`:421-461`）。

P4 查询只按计划 predicate 与完整 subject/object `ObservationRef` 匹配；没有匹配关系返回 `BLOCKED`，不会从相同 owner/project 等共享字段推断支持（`src/linkloom/tools/task_relations.py:56-114,172-296`）。

## terminal checkpoint 实际复现

脚本：`D:\webproject\Linkloom\work\p2_terminal_uncertainty_repro.py`。它 monkeypatches `RuntimeEngine._save_state`，先真实持久化含 `inspect_task_context=completed` 的 checkpoint，再抛出 post-commit acknowledgement error；只使用 synthetic source、临时 SQLite 和 loopback Product HTTP。

实际观察：

- 注入 1 次；Product 热读为 `BLOCKED`，error=`TOOL_TERMINAL_CHECKPOINT_FAILED`，reviewer 未执行、无结论。
- 最终 Runtime state 为 `failed`，有失败 result artifact；最新 Ledger 中 inspect 为 `pending`。
- 持久 cold read 和 fresh subprocess cold read 均返回 HTTP 200、`BLOCKED`，分别以 `SAVED_RESULT_REVALIDATION_FAILED` 阻断；没有 `SUPPORTED`。
- 原始临时运行目录：`D:\webproject\Linkloom\work\p2-terminal-0zyzo7sd`。
- 可复查脚本输出的机器摘要：`D:\webproject\Linkloom\work\p2-terminal-uncertainty-observed.json`。

撤销原疑点的原因是外层生命周期在 `src/linkloom/runtime/graph.py:800-825,878-906` 捕获不确定工具结果并持久化失败状态；`src/linkloom/ui/investigation.py:328-346` 的 saved/cold read 还要求终态 result artifact 和完整重验证，失败即 BLOCKED。

对照脚本 `D:\webproject\Linkloom\work\p2_terminal_direct_repro.py` 显示：direct `ToolRuntime` caller 确实收到 `TOOL_TERMINAL_CHECKPOINT_FAILED`，但孤立磁盘 Ledger 因“写后抛错”仍是 `completed`，直接喂给 `query_task_relations_from_record` 可得到 `SUPPORTED`。该行没有 Product pointer、failed Runtime state 或 cold-read 入口；它证明的是低层 callback 的 commit-ack 不可观测性，不能作为本生产联合链 P2。

对照临时目录：`D:\webproject\Linkloom\work\p2-terminal-direct-x5hb4z3_`。

## CI 入口

`.github/workflows/offline-pr.yml` 保留原有 M3-D combined/#14 gate（`:196-199`），并新增 P2-A、P2-B 和 joint Product 入口（`:200-216`）。Provider key 环境为空（`:11-15`），joint 测试复用既有 3 秒 client/P2-A 预算，不含付费 Provider 或生产适配。此独立复审确认的是 workflow 静态入口；不把未在本复审中取得的远端 CI 状态冒充为 CI 通过。

## 局限

本报告没有重复执行已由主 Agent 提供收据的独立 63 个 P2 用例和 joint 正常/来源变更用例；未修改当前 worktree 中主 Agent 的未提交 CI/test 证据。复现脚本和临时 SQLite/runtime 材料保留在 `D:\webproject\Linkloom\work\` 供复查。
