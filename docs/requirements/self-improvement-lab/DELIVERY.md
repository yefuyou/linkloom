# Evaluation-Driven Self-Improvement Lab

结论 **B：保留可测收益，拒绝正式接纳**。自动闭环已运行；最终 `selected=null`。离线候选通过 Development、Validation 和一次最终集对照，但全仓库回归不绿，因此没有激活生产策略。

## 运行与证据

```powershell
python scripts/self_improvement/run.py --budget 3
# 可选：离线筛选后，由 Gemini 再提出一轮候选；总预算包含真实请求
python scripts/self_improvement/run.py --budget 6 --rounds 2 --live-reflection
```

命令自动完成基线、结构化归因、最多三候选一轮的串行筛选、独立评分、Validation、单个 finalist 的最终对照和最后一次全量回归。原始产物写入 ignored `work/self-improvement-lab/`；Runtime 使用浅路径，避免 Windows 路径长度限制。`--development-only` 是不接触最终集的调试模式。

本轮可移植记录在 [EVIDENCE.json](EVIDENCE.json)，包含 Candidate → Before → After → Regression、查询变化、逐例门槛/成本、目标失败减少、Ledger 观测和代码/输入摘要。源码与合成数据为新增文件；`src/`、原有 Gold、权限、Validator、正式 Memory 均未改变。

## 实际结果

最终测试的 `relation-routing` 将已核验依赖实体的问题词改为关系词，并保留首条及关系关键词命中；每条采用的证据仍经过原 Verified Read、Grounding 和 Runtime。

| 分区（各 6 例） | 任务准确率 | 工具调用 | 模型回合 | 累计请求上下文字节 |
|---|---:|---:|---:|---:|
| Development | 5/6 → 6/6 | 39 → 29 | 45 → 35 | 1,038,822 → 734,403 |
| Validation | 5/6 → 6/6 | 39 → 29 | 45 → 35 | 1,038,586 → 734,289 |
| Final | 5/6 → 6/6 | 39 → 29 | 45 → 35 | 1,039,609 → 734,974 |

三分区的引用/声明支持、父实体绑定及 Workspace 检查均通过；缺失、冲突、跨 Workspace 的正确保留/拒答均为 3/3。上下文是实际序列化 ModelTurnRequest 的 UTF-8 字节，不是付费 token；离线模型回合不计为真实 Provider 请求。

三个候选由 Codex 编写的有限算子根据 DEV Trace 自动实例化。`relation-query` 在 DEV/Validation 改善；`relation-routing` 改善并成为唯一 finalist；`narrow-first` 虽将 DEV 调用降至 26 次，却把正确拒答从 3/3 降至 2/3，自动淘汰。评分器使用原 reliability scorer；生成器不接收 Gold、Validation 或 Final。

本轮直接相关的 Runtime/策略/安全测试 33 项通过；后续强化门槛与 Provider 生命周期测试 11 项通过。全量回归在收集阶段因基线提交缺少 `run_stage_c_context_memory_loop.py` 与 `run_memoryagentbench_g3_pilot.py` 出现 4 个错误。继续执行其余测试到 78% 已有多处失败，停止旁支排查并拒绝接纳；余下测试未完成，其他失败未逐项归因。机器记录保留失败和未完成状态，不宣称全量通过。

首次验收曾遇到 Windows 深目录写产物失败。修复的是实验路径，不是策略、Gold 或安全阈值；旧失败记录保留。最终基线只执行一次并封存，后续复用；finalist 只执行一次。复审只对已封存 raw 重新计算更严格的逐例门槛，没有生成新候选或再次执行最终集。临时的基线复用 CLI 已移除。

## 真实 Provider

实际确认模型为 **gemini-3.8-flash**，没有使用 Gemini 2.5。沙箱探测一次连接建立失败；在可连接环境成功完成兼容性探测和最终只读策略审查。共预留 3 请求 / **$0.60**；有 Usage 的费用估算 **$0.00380475**，缺 Usage 的一次仍保留完整预算。严格 Outcome Uncertain 为 0；串行 OS lease，SDK `attempts=1`（零自动重试）。旧流的请求均已闭合，补记了显式进程退出后批次核对事件，未回放请求。

Gemini 自动提案轮因最初连接失败没有产生候选；成功的最终审查仅是建议，不参与评分或接纳。这里不声称真实模型检索效果已改善。

## Self-Improvement 的程度与人工边界

实现的是**执行反馈驱动的有限策略配置搜索与拒绝闭环**：程序从真实离线 Runtime 的调用记录识别症状，自动构造/隔离执行候选，以独立指标筛选并在回归失败时拒绝接纳。它不是模型权重学习，也不是开放式生产代码自改写。

每分区仅六个合成用例；Gold 对生成器和执行器隔离，但合成集作者见过答案，因此不能宣称严格盲测或真实数据上的可靠泛化。生产激活、扩大算子白名单、修改验收阈值、使用真实数据以及合并分支仍需要人工审核；本轮未自动做这些动作。

方法参考 [DSPy GEPA](https://dspy.ai/3.4.1/diving-deeper/gepa-in-depth/) 的执行反馈反思；未引入 DSPy。预算参考 [Gemini 官方价格](https://ai.google.dev/gemini-api/docs/pricing)，实际按更保守的逐请求上界预留。
