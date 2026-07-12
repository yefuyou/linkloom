# 当前仓库现状

本文件是 2026-07-12 的只读盘点。结论来自仓库文件和已保存产物；全量 `pytest -q` 在本次盘点中超过 64 秒超时，因此不能声称全量套件当前通过。

## 1. 已完成且可复用

| 资产 | 事实状态 | 可复用方式 |
|---|---|---|
| `src/linkloom/scanner.py` | 已实现 Scanner v1 | 冻结为 `VaultIndex` 的唯一生产者；不重写。 |
| `python -m linkloom scan INPUT --output OUTPUT` | 已实现 CLI | 用于以后生成受控 Index。 |
| `tests/unit/test_scanner.py` + `tests/fixtures/sample_vault/` | Scanner 夹具与单测存在 | 继续作为 Scanner 回归测试；不混入 relation 语义。 |
| `.artifacts/scan/` | 已有 Scanner 样例产物 | 仅作示例，不是稳定输入或测试真值。 |
| `relation_eval/` | 可运行的实验评测包 | 逐步迁移/复用其 schema、scorer、evidence validator、报告逻辑。 |
| `tests/fixtures/relation_vault/` | 10 篇 synthetic Markdown | 冻结为 `relation_vault_v1`，用于 WP-0 至 WP-7。 |
| `tests/eval/relation_gold.yaml` | 10 条 note label、6 条 pair 标注 | 冻结为 Gold Dataset；仅 Evaluator 可读取。 |
| `outputs/*mock*` | perfect/noisy/malformed 历史运行产物 | 用作管线行为证据，不是模型效果证据。 |

Scanner 的现有合同：`schema_version`、`note_count`、排序后的 `notes`、排序后的 `warnings`；每条 note 有相对路径、标题、heading、tag、wikilink、原始字节大小和 SHA-256。它会拒绝输出落在输入树内、跳过 symlink、处理坏 frontmatter/非法 UTF-8 并且只写输出目录。

## 2. 已有但只是实验的 relation_eval

`relation_eval/` 已有 `DatasetLoader`、`DocumentInput`、Topic/Relation Prediction schema、Mock Provider、OpenAI-compatible Provider、topic/relation classifier、scorer、evidence validator、Bad Case 分析和 Markdown report。运行器会对 10 文档生成 45 个无序 pair。

它**不是**正式 `src/linkloom/` 模块，且当前有明确实验债务：

- loader 硬编码项目绝对路径；不能作为可发布 Loader；
- perfect Mock 从 Gold 构造答案；100% 只证明评测管线接通；
- noisy/malformed 用于注入错误，不是模型基准；
- config 中的 budget/cache/concurrency 字段未被完整执行；
- malformed 产物可能漏算缺失预测，不能与模型质量分数直接比较；
- 输出含时间戳，适合 Run Manifest，不适合确定性数据合同；
- 已存在 Provider 配置含明文凭据。不得运行、复制或提交；WP-7 前必须移除并轮换凭据。

历史真实 Provider 输出记录为被阻断的 Provider Error（请求数/Token 为 0），不是有效 baseline。

## 3. 关系类型审计

Gold Dataset 的正向类型仅有：

| 类型 | 方向 | MVP 保留 | 含义 |
|---|---|---:|---|
| `concept-to-practice` | 概念/观察 -> 实践或复盘 | 是 | 后者把前者的理解落到可执行验证。 |
| `concept-to-interview` | 概念/指标 -> 面试问题 | 是 | 前者支撑后者的解释或回答。 |
| `practice-to-interview` | 实践记录 -> 面试问题 | 是 | 实践经验支撑面试表达。 |

`false-positive` 出现在 Gold 中，用于表达困难负样本；loader 将其规范化为 `should_link: false`。它**不是** Agent 可输出的真实 `relation_type`。第一版不新增 `supports`、`contradicts`、`same-topic` 等抽象类型，避免改变既有评测合同。

## 4. 未完成主链路

- 基于 `VaultIndex`、校验 hash 后读取正文的正式 Note Loader；
- `src/linkloom` 内的 Provider 接口、Mock、预测 schema 和 prompts；
- 受控的 Topic/Relation 推理入口；
- 与推理隔离的正式 Evaluator；
- 面向用户的 Human Review Report 与只生成不执行的 Change Plan；
- 真实 Provider 的有效、预算受控 baseline；
- 任何写回或真实 Vault 路径。

## 5. 文档与实现冲突

1. Vault Profile 已在 user-facing Roadmap 中改为 optional backlog。本 Master 使用技术工作包来描述 synthetic relation-evaluation spine，不重命名六个产品 Milestone。
2. `AGENTS.md` 要求每个 Milestone 之间人工确认；本轮需求希望 Antigravity 连续施工。采用保守解释：Worker 可连续完成一个已批准 Milestone 的小任务，但进入下一 Milestone 仍需人工门禁。
3. `relation_eval` 的 schema 与未来 `NoteDocument` 不一致：实验 `DocumentInput` 只有 path/content/hash，未来合同还需 Scanner 元数据。迁移时扩展新 schema，不直接把实验 model 当成生产合同。
4. `configs/eval.openai.yaml` 的明文凭据违反“仅环境变量”安全原则；本轮不改配置，但它是 WP-7 阻塞项。

## 6. 冻结与建议起点

冻结：Scanner v1 合同、`relation_vault_v1` 文本、`relation_gold.yaml` 标注、三种 Mock 场景的预期意图。允许以后新增数据集版本，禁止静默改动 v1。

Antigravity 的起点（需明确人类批准后）：WP-0 先写/跑冻结清单与 Gold evidence 校验；不创建第二 Scanner、第二 Gold Dataset 或第二 relation_eval。
