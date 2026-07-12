# Relation Eval Package

Status: experimental evaluation harness for future Milestone 3, "Can Connect".

This package is not the Milestone 1 Scanner and is not yet a user-facing
linkloom feature. It is a synthetic-data laboratory for evaluating two future
behaviors:

- whether a note belongs to a controlled topic;
- whether two notes deserve a suggested relation, with evidence and direction.

It does not write tags, links, folders, or note content.

## What This Experiment Proves

- The mock runs prove that the loader, schema validation, scorer, evidence
  checks, bad-case report, and Markdown report can work together.
- The noisy run proves the reports can surface injected false positives, false
  negatives, direction errors, type errors, and evidence errors.
- The malformed run proves the runner records invalid structured output instead
  of crashing.

The perfect mock reads the gold dataset to create intentional expected answers.
Its 100% score proves evaluation-pipeline plumbing, not model intelligence.

## What It Does Not Prove Yet

- It does not prove a real model can infer useful relations.
- It does not prove linkloom can safely scan an arbitrary Obsidian vault.
- It does not prove a model should create or apply tags.
- It does not yet enforce request budgets, caching, or concurrency limits from
  the config file.
- Malformed predictions are reported as bad cases, but current aggregate scores
  do not yet penalize every omitted prediction. Do not compare malformed-mode
  metrics with model-quality metrics.

## Provider Safety

`configs/eval.mock.yaml` is intentionally local-only and uses `mock` by
default. It contains no API key or remote endpoint.

Do not place keys in config files. A future provider-backed run must receive its
credentials from environment variables, use only synthetic fixtures until its
privacy boundary is reviewed, and record provider failures separately from
model-quality scores.

The recorded provider-backed attempt under `outputs/` was blocked and produced
no valid model predictions. It is failure evidence, not a real-model benchmark.

## Tag Governance Direction

The user-facing tag model should be deliberately conservative:

1. maintain a user-owned allowed-tag list and definitions;
2. let AI propose only an existing allowed tag, with confidence and source
   evidence;
3. send low-confidence, ambiguous, or new-label ideas to review instead of
   applying them;
4. allow tag writes only in the later human-confirmed mutation milestone.

This harness can later evaluate that policy with positive cases, false-positive
cases, and rejected-tag cases. It must never become an automatic tag writer.

## 核心设计原则 (Core Design Principles)
1. **隔离性 (Isolation)**: 推理模块 (Inference/Agent) 与评分模块 (Evaluator/Scorer) 严格隔离。推理逻辑仅能访问文档正文和路径，严禁读取任何 Standard Answer (Gold) 字段。
2. **纯本地 Mock 模式**: 本阶段默认使用 Mock Provider，无需任何 API 密钥或网络请求，以保证 100% 确定性、零费用、零延迟。

## 模块分工
- `relation_eval/schemas.py`: 基于 Pydantic v2 定义输入/输出模型规范。
- `relation_eval/config.py`: 加载和校验评估全局 YAML 配置文件。
- `relation_eval/dataset/`: 负责读取 Markdown 文档并解析 Golden Dataset，统一将 `false-positive` 关系类型规范化为 `should_link: false`。
- `relation_eval/providers/`: 定义模型提供者接口，`MockProvider` 实现了三种测试场景：
  - `perfect`: 完美的 1.0 得分场景。
  - `noisy`: 注入确定性噪音（用于验证 Scorer 是否能够准确发现假阳性/假阴性/方向与类型错误）。
  - `malformed`: 输出格式异常/参数越界（用于验证系统容错和 Schema 健壮性）。
- `relation_eval/agents/`: 封装推理 Agent 行为，隔离数据源。
- `relation_eval/evaluation/`: 包含评分器和 Substring 证据校验器，分析并归因 Bad Cases。
- `relation_eval/reporting/`: 生成对人类友好的 Markdown 评测报告。

## 运行命令 (CLI commands)

### 运行完美评分场景 (Perfect Mode)
```bash
python -m relation_eval.runner.run_evaluation --config configs/eval.mock.yaml --mock-scenario perfect
```

### 运行带有错误噪音的场景 (Noisy Mode)
```bash
python -m relation_eval.runner.run_evaluation --config configs/eval.mock.yaml --mock-scenario noisy
```

### 运行异常数据的场景 (Malformed Mode)
```bash
python -m relation_eval.runner.run_evaluation --config configs/eval.mock.yaml --mock-scenario malformed
```

## 测试命令 (Automated Tests)
运行完整的自动化测试套件：
```bash
pytest
```
或者运行特定的测试：
```bash
pytest tests/test_dataset_loader.py
pytest tests/test_mock_provider.py
pytest tests/test_topic_scorer.py
pytest tests/test_relation_scorer.py
pytest tests/test_evidence_validator.py
pytest tests/test_evaluation_runner.py
```
