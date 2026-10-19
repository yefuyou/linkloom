# 离线复现

在仓库根目录使用 Python 3.11+ 和项目现有依赖。以下命令不调用模型，输出文件名须未存在。

```powershell
$env:PYTHONPATH = 'src;.'
python scripts/run_relation_checks.py --stage p4 m3-a-repro tests/unit/test_task_relation_semantic.py tests/unit/test_task_semantic_smoke.py tests/test_score_task_semantic_dev.py

# 独立进程：读取已落盘真实预测后再加载独立 Gold。
python -m scripts.score_task_semantic_dev --inputs tests/fixtures/task_semantic/inputs/dev.json --gold tests/fixtures/task_semantic/gold/dev.json --predictions docs/requirements/m3-llm-semantics/artifacts/predictions.json --output work/m3-a-score-repro.json

# 原 M3 评测框架：缺少生产 Agent Adapter，必须保持 NOT_RUN / NOT_EVALUATED。
python -m evals.m3_task_impact run --output work/m3-a-agent-not-run-repro.json
python -m evals.m3_task_impact score --predictions work/m3-a-agent-not-run-repro.json --output work/m3-a-agent-not-evaluated-repro.json
```

`score_task_semantic_dev` 只评固定端点的语义提案，不是生产 M3 Agent Adapter。
QUOTE 有效、作者 Gold 来源覆盖、关系标签准确及条件性日期计算分开统计。
Fake/scripted 只能进入 TEST_ONLY/diagnostic_metrics；无执行回执或 NOT_RUN 不得产生业务成绩。
本地回执是可审计材料，不是防恶意伪造的远端签名或账单证明。

历史旧规则同题诊断可单独复现：

```powershell
python scripts/diagnose_task_semantic_regex.py --output work/m3-a-regex-repro.json
```

它从 public inputs 构造完整有界 M1 inventory，用原 P1/P2/P3 判定；不读取 Gold，不调用新 proposer。
旧 H01–H04 已曝光，本轮没有将其作为新的保留成绩。新八题全部为作者可见 Dev。

## 原真实运行的审计

实现固定为 `04a446d10494a2f6d61ea1f0a745a3ce9f1e8d0c`。
参见 artifacts/freeze.json 的 17 个执行依赖、输入、schema、请求和 snapshot hash，
evaluation-freeze.json 的输入/Gold hash，predictions.json 的结果、响应 hash、调用和 usage 回执。
主 Agent 在生成前提交实现/固定清单，预测写出后才由独立评分进程加载 Gold；独立 Reviewer
重新计算全部依赖和输入 hash，并回读原文 Unicode 范围。核心文件、输入、Gold、runner 和实际预测
在真实输出后没有改动；后续提交仅增加离线评分器、评审和交付证据。

记录在原独立 Worktree 的 `work/semantic-smoke-01` 中有 attempt/receipt 1–3，按序各调用一次。
本次已停止模型调用。若将来需要新的真实 Smoke，应在冻结实现的独立 Worktree、现有授权凭据和
明确的新运行范围内，创建新的 freeze 与输出目录；它是复跑诊断，不是新的盲测。

```powershell
# 此段会使用真实 Provider，离线复现无需执行。
python scripts/run_task_semantic_smoke.py --freeze work/new-semantic-freeze.json
python scripts/run_task_semantic_smoke.py --execute work/new-semantic-freeze.json --output work/new-semantic-smoke
```

旧归档 freeze 的 HEAD 与文件 byte hash 必须原样匹配；当前交付提交包含后续文档/评分器，
不能把它的 HEAD 冒充原运行版本。换平台/换行符导致 byte hash 改变时必须生成并记录新的清单，
不能手动绕过校验。模板输入全为合成资料，禁止替换为真实敏感资料后静默上传。

## 既有失败复现

```powershell
python scripts/run_relation_checks.py --stage p4 m3-a-provider-baseline tests/unit/test_task_identity_resolution.py tests/integration/test_task_identity_dev.py tests/unit/test_p85_gemini_api_adapter.py tests/unit/test_gemini_nullable_json_schema.py tests/unit/test_product_gemini_sdk_bridge.py
```

预期本环境为 62 passed / 83 failed。失败文件与起点一致，归因见 DELIVERY；这个命令不会访问 Provider。
日志由既有 Windows 临时目录插件保留在独立 Worktree `work/`，没有删除或改写其他 Worktree。
