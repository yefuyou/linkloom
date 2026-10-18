# LinkLoom M2-P4 交付

已实现显式路径 SQLite 关系侧库、不可变判定与比较、追加生命周期、当前证据和依赖闭包重验、只读项目/观察/历史查询及有限计划投影。完整模块说明、真实 SQLite JSON、失败与独立审阅记录见 [P4 DELIVERY](docs/requirements/task-relation-lifecycle/DELIVERY.md)。[P3 历史交付](docs/requirements/task-revisions/DELIVERY.md) 与 [P2 历史交付](docs/requirements/task-relations-p2/DELIVERY.md) 保持原内容。

Dev 生命周期独立验收 **53/53 原子检查、4/4 案例家族**；P4 聚焦 **29 passed**，既有 M1/P1/P2/P3/Decision Memory/正式审核相关回归 **285 passed**，独立审阅 **PASS**。实际结果记录在 [P4 DELIVERY](docs/requirements/task-relation-lifecycle/DELIVERY.md)。这是固定输入的离线语义和工程验收，不是候选发现、真实 Provider 或生产部署结论。

D04 比较 +4，撤销身份关系后当前比较 BLOCKED/null，历史 +4 保留；D07 仍 UNKNOWN；D08 别名撤销使下游派生项目关系退出当前确认视图；新的 Redwood 版本序列显示 ACTIVE/VERIFIED → ACTIVE/STALE → RETRACTED，旧字节可读也不会复活。未来计划不覆盖明示已生效计划；生效未知不冒充当前已生效。真实 Provider 和保留集运行均为 0。

固定交付分支：`codex/linkloom-m2-progress`。M2 最终整体验收尚需冻结实现后的独立保留验收及端到端候选/关系效果区分；M3 尚需工作区授权、来源快照装配及只读工具适配契约。本轮停在 P4，已有无关工作区修改保留。
