# Self-Improvement V2 交付

唯一工程决策：**REJECTED**。闭环实际完成，发现局部离线收益，但没有足够证据可靠接纳；未激活生产策略、未创建PR。

准确基线92c0fd282f8c33dfe29471aa70b9d7cfbf6e1d75，独立分支 `codex/linkloom-self-improvement-hardening`。正式身份 `v2-frozen-experiment-01`；V1成果、Gold和失败记录未改变。

## 结果矩阵

分母为当前支持范围，包含正确partial/拒答/指定安全停止，非完整答案数量。

| 配置 | Dev /29 | Validation /22 | Stress /16 | Final /22 | 工具总计 |
|---|---:|---:|---:|---:|---:|
| Baseline | 23 | 11 | 13 | 10 | 680 |
| V1 routing | 25 | 18 | 15 | 16 | 544 |
| narrow-first | 18 | 15 | 14 | 12 | 333 |
| Codex query-only | 26 | 13 | 14 | 13 | 675 |
| Gemini relation_candidates/6 | 25 | 17 | 15 | 15 | 581 |
| Gemini literal+relation_candidates/4 | 23 | 15 | 14 | 13 | 578 |

96例中7例Unsupported排除；冻结标注有3处span缺陷，原样保留并否决可靠泛化接纳。所有结果均有逐例、类别/标签、Wilson区间、配对差值和八个可复现失败故事，见 [METRICS.json](METRICS.json)。

## 可执行交付

```powershell
# 首次完整流程要求未暴露且已资格检查的新freeze；默认不付费
python scripts/self_improvement/run.py --protocol v2
# 当前数据的独立Final已用过；新实验可复跑开发/验证诊断
python scripts/self_improvement/run.py --protocol v2 --screen-only
# 本机完整实验安全恢复：复用封存结果，无新增执行或付费
python scripts/self_improvement/run.py --protocol v2 --experiment work/v2-frozen-experiment-01
```

`--live` 显式开启真实提案；真实Runtime阶段由 `python -m scripts.self_improvement.v2_live --experiment ... --dataset ...` 执行。它们校验同一身份与已完成配对工程诊断。默认CLI完成Baseline→DEV症状→有限配置→独立评分→Validation/Stress→一次Final→分层回归→机器决策。已暴露Final不会再次成为独立验收；所有raw/Provider材料留在ignored work。

## 七个验收答案

1. **V1有结构性过拟合风险。** 三分区重复六种模板/文档形状，已用测试复现。Gold隔离成立，但不是陌生结构的外部盲测。
2. **V1并非完全无泛化。** 新集在19个不同结构标签的案例上修复Baseline失败，覆盖7大类；也令两个原正确中文案例退化。一个是漏Read，另一个已Read完整链却错误拒答。
3. **Fake与真实收益分开。** Host relation query已接线到原Runtime；强制pending-read路由属于FakeController。真实Gemini只有阅读建议，未实现等价强制策略。
4. **Gemini真正参与。** 两个实际生成配置通过Schema，并完成独立离线评测后被拒绝；不是用Codex规则冒充模型提案。
5. **没有可信真实收益。** 五完整配对任务正确2/5→0/5，工具24→24。Token略降伴随正确性下降，不能算优化。第六题因缺Usage而停止另一策略，标N/A。共64请求，已知用量估算$0.508542，加预留总上界$0.5669505，零严格UNKNOWN、零重放。
6. **自动拒绝有依据。** 逐例退化、数据资格缺陷、Core输入缺口分别否决。Core194与Historical88节点两侧同时通过，候选新增失败0；Full仅2290项收集、4个旧脚本错误，未宣称全仓绿。query-only总正确57/89→66/89且原正确零退化，但仍缺可靠数据/工程与真实迁移证明。
7. **实现的是有界策略配置自改进。** 自动Trace症状→Codex/Gemini受限配置搜索→原Runtime执行→独立评分→双Gate拒绝；不是权重学习、任意源码自修改或自动生产接纳。因果解释、Gold修订与新独立Final、真实语料/重复试验、正式策略接线、历史回归修复、生产启用和合并仍需人工审核。

51项专项测试通过，独立审查及未解决项见 [REVIEW.md](REVIEW.md)；泛化、真实迁移和接纳细节分别见同目录文档。
