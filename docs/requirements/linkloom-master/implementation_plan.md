# 实施计划

每个工作包（WP）内 Worker 可连续完成小任务；WP 是技术执行单元，不会重命名用户产品 Milestone。跨 WP 前仍须满足 `AGENTS.md` 的人工门禁。各阶段遵循 [milestone_acceptance.md](milestone_acceptance.md)。

## WP-0 资产冻结与合同对齐

**Goal**：锁定可复现输入，消除实施前不确定性。  
**Inputs/Outputs**：Scanner v1、relation_vault_v1、Gold v1 -> freeze record 与当前测试记录。  
**Allowed files**：本 Master task、未来 WP-0 feature docs；不改 fixture/Gold/Scanner。  
**Tasks**：核对 10 note / 45 pair；验证 Gold evidence；记录 Scanner index schema；记录完整测试结果或超时。  
**Acceptance**：冻结版本、关系类型、复用边界有证据。  
**Stop**：需改 Gold、Scanner 合同、relation 文本或真实路径。  
**Non-goals**：新业务功能。  
**Handoff evidence**：命令、结果、hash/版本清单。

## WP-1 Read-Only Note Loader

**Goal**：把 Scanner Index 安全变成可推理的正文。  
**Inputs/Outputs**：VaultIndex + synthetic root -> NoteDocument[] / CONTENT_CHANGED。  
**Allowed files**：`src/linkloom/loader.py`、对应测试和 feature docs。  
**Tasks**：index schema、relative path、symlink、UTF-8、raw-byte hash 检查；稳定排序。  
**Tests/Commands**：新增 loader unit tests；Scanner + Loader integration command。  
**Acceptance**：hash 一致才加载，traversal/变化拒绝，零源修改。  
**Stop**：需要真实 Vault、改 Scanner 合同、加数据库。  
**Non-goals**：模型和 Gold。  
**Handoff evidence**：样例 NoteDocument、失败输出、输入 hash 对比。

## WP-2 Provider And Schemas

**Goal**：建立可替换、可校验的预测边界。  
**Inputs/Outputs**：NoteDocument/pair -> validated predictions。  
**Allowed files**：`src/linkloom/schemas.py`、`providers/`、tests/config examples。  
**Tasks**：生产 schema、Provider interface、local Mock、prompt/version metadata、cache interface。  
**Tests/Commands**：Mock 不联网、schema 边界、配置加载。  
**Acceptance**：没有 Gold import；错误结构化返回。  
**Stop**：真实 key、网络、多个 provider framework。  
**Non-goals**：真实 baseline。  
**Handoff evidence**：Mock trace 与 schema rejection。

## WP-3 Topic Classification

**Goal**：对单 note 给出受证据约束的主题判断。  
**Inputs/Outputs**：NoteDocument -> TopicPrediction。  
**Allowed files**：`src/linkloom/agents/topic_classifier.py`、topic prompt、对应 tests 与 feature docs。  
**Tasks**：冻结 topic prompt、分类边界、Mock tests、预测元数据。  
**Tests/Commands**：`python -m pytest tests/test_topic_scorer.py -q`（迁移后替换为产品测试）；本地 mock topic runner。  
**Acceptance**：证据在本 note；Gold 对推理不可见。  
**Stop**：新增主题体系或标签写回。  
**Non-goals**：关系判断。  
**Handoff evidence**：一条通过与一条 schema/evidence 拒绝的预测样例。

## WP-4 Candidate Pairs And Relation Classification

**Goal**：对候选笔记对提出有方向、有类型、有双侧证据的关系。  
**Inputs/Outputs**：10 docs -> 45 pairs -> RelationPrediction。  
**Allowed files**：`src/linkloom/agents/relation_classifier.py`、candidate generator、relation prompt、tests 与 feature docs。  
**Tasks**：确定性无序 pair、三种类型、双侧证据。  
**Tests/Commands**：pair-count、direction/type/schema tests；local mock relation runner。  
**Acceptance**：45 pair、source/target 合法、`false-positive` 不可输出。  
**Stop**：向量库、GraphRAG、写 Wikilink。  
**Non-goals**：大 vault 性能优化。  
**Handoff evidence**：45 pair 清单摘要、一个正向和一个负向 prediction。

## WP-5 Evaluator

**Goal**：证明预测是变好还是变坏。  
**Inputs/Outputs**：Gold + predictions -> metrics/bad cases/report。  
**Allowed files**：`src/linkloom/evaluation/**`、reporting tests、feature docs；Gold 只读。  
**Tasks**：Gold loader、normalization、scorers、evidence validation、报告。  
**Tests/Commands**：gold validation、topic/relation scorer、evidence validator tests；evaluation runner with mock predictions。  
**Acceptance**：Evaluator 不改 prediction；错误可分类；Gold error 可报告。  
**Stop**：修改 Gold 让分数变高。  
**Non-goals**：复杂语义 judge。  
**Handoff evidence**：metrics.json、bad_cases.json、report.md。

## WP-6 Mock Closure

**Goal**：验证完整管线对正确、错误和坏结构的表现。  
**Inputs/Outputs**：perfect/noisy/malformed -> 三套可解释结果。  
**Allowed files**：Mock provider、runner、test/output fixture、feature docs。  
**Tasks**：对齐旧 experiment、测试输出目录、端到端 tests。  
**Tests/Commands**：三次 `python -m linkloom.runner.evaluate --provider mock --scenario <...>`；全量产品 tests。  
**Acceptance**：perfect 满分；noisy 产生预期 Bad Case；malformed 局部失败不崩。  
**Stop**：把 oracle score 当模型成绩。  
**Non-goals**：真实模型。  
**Handoff evidence**：三个 RunManifest、三份报告及断言结果。

## WP-7 Real Provider Baseline

**Goal**：获得第一份可复现且预算受控的真实模型结果。  
**Inputs/Outputs**：frozen dataset/config/env -> manifest/predictions/metrics/cost。  
**Allowed files**：Provider adapter、safe config template、runner、tests、feature docs；不得改 Gold/fixtures。  
**Tasks**：OpenAI-compatible adapter、env-only credential、budget/cache/retry、3-run baseline。  
**Tests/Commands**：provider contract tests；approved environment command only after budget gate; three recorded runs。  
**Acceptance**：Token/成本真实记录；失败不冒充分数。  
**Stop**：明文 key、预算不执行、真实 Vault。  
**Non-goals**：生产部署。  
**Handoff evidence**：3 个 manifest、成本汇总、失败/重试记录。

## WP-8 Human Review Report

**Goal**：让用户审查建议而非让 Agent 直接修改。  
**Inputs/Outputs**：validated predictions -> ReviewItem[] + ChangePlan preview。  
**Allowed files**：`src/linkloom/review/**`、report fixtures/tests、feature docs。  
**Tasks**：pending/approve/reject 表达、证据/置信度、差异预览。  
**Tests/Commands**：review report snapshot test；ChangePlan schema test；source hash before/after check。  
**Acceptance**：清晰可读、只生成不执行、源笔记不变。  
**Stop**：任何实际 writeback。  
**Non-goals**：Future Safe Writer。  
**Handoff evidence**：人类可读 report、pending items、无写入 hash 对比。
