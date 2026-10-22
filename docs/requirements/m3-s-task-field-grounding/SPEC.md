# M3-S：任务字段语义与精确证据

用户本轮完整 M3-S 指令为批准边界；基线 `417595a2ffc4e02566b7a0dd6e96f143f472e5ca`。

沿用现有 F、TaskProposal、TaskFieldEvidence、TaskObservation 与 Runtime。
新 LIVE F profile 要求模型为 owner/deadline/project/vendor 提供有界角色、断言状态、
字段值、精确角色引用及独立任务工作引用；程序只验证当前规范消息、值、引用、
发言人、版本及日期数值，不再要求匹配旧中文业务句式。

- 只有明确当前角色且 ASSERTED 的字段可以落地；歧义、历史、条件、撤回等保持未知。
- 字段角色仍是模型解释，引用验证不等于语义证明；普通观察始终 SUPPORTING_ONLY。
- SPEAKER 只由规范解析器提供，且必须有同一条任务的角色引用；不能凭发言人自动分配责任。
- 不跨消息继承字段，不从时间戳补年；保留 DAY/MONTH_DAY/MONTH/UNKNOWN。
- 新绑定必须参与序列化与 verify 的确定性回读；旧记录不加默认字段，不改变旧哈希或 REPLAY。
- 不改变 P4、业务审批、Provider 权限和失败不重试的边界；不新增业务正则。

验收：原始 F 字段归因；新旧对照；固定12–16组独立合成资料和3条实现后揭示预期的holdout；
代表性真实 F 调用与正常 Product HTTP/Chromium LIVE；字段证据/冷读/更新撤权安全；独立复审P1/P2=0。

预算不重置：已知47请求、保守预留$13.93；用户已明确将本轮请求上限由24提高到25，最多$8，项目累计不超过$50。
计划8次代表性F（覆盖多个分离消息案例）及2次Product，串行、请求前落盘，不重试UNCERTAIN。
计费项目/实际账单如不能核验，明确保留限制。

实施顺序：证据归因 → 字段绑定合同RED/GREEN → 新profile/Schema/Product接线 → 回归 → 固定案例LIVE → Chromium → 独立复审 → commit/非强推。
