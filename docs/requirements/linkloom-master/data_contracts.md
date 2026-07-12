# 数据合同

所有路径均为 POSIX 风格、相对批准根目录的路径；不记录私人绝对路径。确定性合同不得带时间戳，Run Manifest 可以带时间戳。

## 1. VaultIndex（已冻结）

```json
{"schema_version":1,"note_count":1,"notes":[{"relative_path":"a.md","title":"A","headings":[{"level":1,"text":"A","line":1}],"tags":["agent"],"wikilinks":["B"],"size_bytes":12,"content_sha256":"<64 lowercase hex>"}],"warnings":[]}
```

字段来自 Scanner v1，不能在本 MVP 破坏性修改。`content_sha256` 是原始字节 SHA-256；Loader 必须据此检测变化。

## 2. NoteDocument（WP-1）

```json
{"relative_path":"a.md","title":"A","content":"# A\n...","headings":[],"tags":[],"wikilinks":[],"size_bytes":12,"content_sha256":"..."}
```

它由 `VaultIndex` 的相对路径定位、按 UTF-8 读取正文、重新计算 raw-byte hash 后生成。hash 不同输出 `CONTENT_CHANGED`，该文档不得进入推理。路径 resolve 后越界、symlink、非法 UTF-8 均为显式失败，不回退到其他文件。

### Fixture path normalization

生产 `NoteDocument.relative_path` 永远相对 Vault 根目录（例如 `0112-随笔.md`）。Gold v1 目前存的是相对项目根目录的 fixture 路径（例如 `tests/fixtures/relation_vault/0112-随笔.md`），这是旧实验 loader 为适配其硬编码项目根目录产生的表示。WP-1 必须在 **Evaluator 边界** 增加纯路径适配器：用固定 `gold_fixture_prefix + relative_path` 映射到 Gold key，拒绝重复映射；不得改变 Scanner 输出或 Gold v1。生产推理只使用 Vault-root-relative path，只有 Evaluator 进行 fixture path normalization。

## 3. TopicPrediction（WP-2/WP-3）

```json
{"note_path":"a.md","predicted_topic_ids":["agent-evaluation"],"should_link_to_agent_evaluation":true,"confidence":0.82,"evidence":["原文片段"],"reason":"简短理由","provider":{"name":"mock","model":"perfect"},"prompt_version":"topic_v1"}
```

`evidence` 必须来自该 note；置信度范围为 0-1。`provider` 和 `prompt_version` 是追溯字段，不允许影响确定性 Scanner 输出。

## 4. RelationPrediction（WP-4）

```json
{"left_note_path":"a.md","right_note_path":"b.md","should_link":true,"source":"a.md","target":"b.md","relation_type":"concept-to-practice","confidence":0.77,"evidence":{"source":["片段 A"],"target":["片段 B"]},"reason":"为何建议连接","provider":{"name":"mock","model":"noisy"},"prompt_version":"relation_v1"}
```

当 `should_link=false` 时，`source`、`target`、`relation_type` 必须为 null；当为 true 时三者必填且 source/target 必须是该 pair 的两端。Agent 不能输出 `false-positive`。

## 5. Gold Dataset（已冻结 v1）

Gold Note：`note_path`、`expected_topic_ids`、`note_type`、`should_link_to_agent_evaluation`、`evidence`、`difficulty`、`rationale`。

Gold Relation Pair：`source`、`target`、`should_link`、`relation_type`、两侧 `evidence`、`rationale`、`difficulty`（新增版本才可加）。`false-positive` 是 v1 YAML 的困难负样本编码，Evaluator 规范化为 `should_link=false`，不属于预测关系类型。

## 6. Metrics 与 BadCase

Metrics 至少记录 topic 的 TP/TN/FP/FN、accuracy/precision/recall/F1/FPR/FNR；relation 的 pair precision/recall/F1、direction/type accuracy；evidence valid rate。

BadCase：`case_type`、`affected_paths`、`expected`、`actual`、`evidence_issue`、`run_id`、`severity`、`message`。类型固定为 topic FP/FN、relation FP/FN、direction/type/evidence/schema/provider/Gold data error。

## 7. ReviewItem 与 ChangePlan

ReviewItem：`id`、prediction、evidence、confidence、reason、status(pending/approved/rejected)、reviewer_note。MVP 只产生 pending。

ChangePlan：`plan_id`、`target_file`、`expected_hash`、`proposed_insertion`、`reason`、`evidence`、`preview_diff`、`approval_status`。MVP 只能生成，不能执行；将来 Writer 必须先校验 `expected_hash`。

## 8. RunManifest

`run_id`、dataset version/hash、provider/model、prompt versions、document/pair counts、request/Token/estimated cost、config hash、code revision、started/finished timestamps、outcome。时间戳只属于 run 记录；Prediction、Gold 与 VaultIndex 不能因时钟而变化。
