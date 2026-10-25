# 本次能力边界

| 类别 | 能力 | 界限 |
|---|---|---|
| 已实现，正常 Product Host 可组合 | 原任务观察、原文、模型关系候选及 UNKNOWN/CONFLICT | 沿用现有 Runtime，模型建议不等于来源证明 |
| 本专项已实现，显式本地 opt-in | 关系核验入口、持久人工声明、撤回事件、历史、独立 CONTINUE | actor_label 自声明；无认证身份或业务批准 |
| 本专项已实现，满足既有合同后 | canonical SAME_TASK / REVISES_PLAN 捕获、日期比较、新 Runtime 调查 | 必须重验来源与生命周期；日期可比较不表示当前业务计划已批准 |
| 本专项已实现 | 精确请求幂等、revision CAS、pending/failed/committed 审计及故障恢复 | capability 有效期内的精确请求；跨库不是单一 ACID 事务 |
| 本专项已实现 | 相关旧 run 历史 | 仅同空间、同来源/目录 hash、同精确任务 pair；不参与当前 run 的权限/CAS |
| 本专项已实现 | 版本/目录失效后清除详情，跨空间拒绝 | 来源撤回覆盖 Product/catalog；不外推为企业身份权限系统 |
| 实验性验收工具 | 显式离线 SourceStage、Chromium acceptance、故障注入 | 只脚本化模型传输或标明的 adversarial mutation；真实 UI 创建来源和 proof，不是通用模型能力 |
| 未实现/未评估 | 认证用户、RBAC、业务审批、正式业务计划确认 | 本轮未伪造审批身份；业务批准始终未确认 |
| 未评估 | 真实 Provider 任意语言的关系识别质量 | 未调用 Provider、未处理 Key，不扩展有限证明语法 |
| 不支持人为越权补足 | 原青竹示例的含糊身份和日期缺口 | 人工 CHECKED 不能补造 SAME_TASK、REVISES_PLAN、有效日期或反证 |

原验收三个 P2（UX02、UX03、UX04）保留；PR24 的历史关系当前性修复与 PR25 的整体 UI 简化不在本补丁中复制。其集成状态见 [DELIVERY](DELIVERY.md)。
