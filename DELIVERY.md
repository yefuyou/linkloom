# LinkLoom M2-P3 交付

已实现 SAME_TASK 证据重验 → REVISES_PLAN → RevisionComparison。完整模块说明、逐题结果、D04 两次修订及 D05/D07 阻断的真实 JSON、失败与独立审阅记录，见 [P3 DELIVERY](docs/requirements/task-revisions/DELIVERY.md)。[P2 历史交付](docs/requirements/task-relations-p2/DELIVERY.md) 保持原内容。

Dev 完整匹配 **12/12**，修订 Precision / Recall **7/7、7/7**，可计算子集 **5/5**；无据修订、虚假日期变化均为 0，非法/过期身份放行 **0/5**。P3 聚焦 **44 passed**，相关既有回归 **241 passed**；以 P2 已提交基线加本轮发布清单重建隔离副本，合并复现 **285 passed**。详见 [metrics.json](docs/requirements/task-revisions/evidence/metrics.json)。

D04 截止边界后移 +4；范围修订、截止明确未变为 0。D05 无修订证据、D07 身份未证实均 BLOCKED / null。未推断实际延期；缺年份、边界不兼容或有效冲突不能计算。真实 Provider 和保留集运行均为 0。

固定交付分支：`codex/linkloom-m2-progress`。P4 尚需独立关系侧库、追加式生命周期事件、依赖失效及只读当前/历史查询；本轮已停在 P3。已有无关工作区修改保留。
