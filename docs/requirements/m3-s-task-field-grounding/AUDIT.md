# M3-S 原始故障归因

基线为 M3-R `417595a2ffc4e02566b7a0dd6e96f143f472e5ca`。
实际输入、模型 TaskProposal、usage、response ID、旧观察与原文件 hash 见
[prior-f-attribution.json](evidence/prior-f-attribution.json)，不是用源码推测替代真实收据。
原件保留在 `work/m3r/work/m3r-live-product-20261022/work/investigations/15b4874f8bc2/preparation/`。

| 消息/字段 | 原文及模型提议 | 原确定性处理/最终值 | 归因 |
| --- | --- | --- | --- |
| A owner | speaker 林枫；“负责项目上线检查任务。”；模型 owner=林枫 | OWNER_UNGROUNDED / null | 模型沿用 speaker/省略主语，但没有明确第一人称或姓名角色引文。旧绑定不接受；不能宣布这是已有充分负责人证据的纯误拒。 |
| B owner | “林枫继续负责项目上线检查。”；模型 owner=林枫 | OWNER_UNGROUNDED / null | 模型理解正确且原文有明确负责人；旧 `_owner_role_match` 不接受“继续负责”，属于有限句式误拒。 |
| A deadline | “原计划的截止日期是2029-06-20；这一天是会议记录日期，不是截止日期。”；模型 task.deadline_text=null | UNKNOWN | 原文自相矛盾，任务字段没有选择一个日期，不能强行补全。模型另外提取了两条相冲突 FACT，不能用其代替任务截止证据。 |
| B deadline | “新的计划日期提到2029-06-24…普通提及2029-06-26；原文没有写明哪一个日期是正式截止日期” | 模型 null，最终 UNKNOWN | 来源没有明确任务截止角色；正确保留未知，非格式/正则故障。 |
| A/B project | “项目上线检查”是工作描述；模型 project=null | null | 无明确项目实体；不能从泛称造一个项目。 |
| A/B vendor | 没有供应商表述；模型 vendor=null | null | 无字段证据。 |

A/B response ID 分别为 `QkvZavaDAs3fytMP-aToqQs`、`RkvZarSbAYCfuMYPydK7qQk`。
原 Product 没有显式任务 profile，使用通用提取 prompt。任务观察已丢失的字段不能由
后续 SAME_TASK 提案补救。本轮不修改这些历史结果。

新实现给正常 LIVE 启用 `task-field-grounding/v2`：模型提供四个有界字段声明，包含
角色、状态、候选值、精确角色引用、工作引用和 canonical message ordinal；程序核验
同一消息、字段值、精确 offset、speaker、版本/hash/权限及日历数值。独立短句的角色
引用无需重复整个工作名称，但两个引用必须各自唯一地位于同一任务原文及规范消息。

旧业务正则仅服务旧 profile、历史序列化和 REPLAY；新 LIVE 不再靠这些正则决定角色。
新增的日期格式处理只判断月份/日历合法性，不判断自然语言含义。没有新增中文业务正则或案例特判。

`TaskObservation.verify()` 是来源绑定重验，不是语义真值判断或存储认证。若同时篡改
scalar 和模型声明，一个内部一致的错误角色仍可能通过单体来源重验；保存计划、工具
回执的外部 `ObservationRef.payload_hash` 必须同时匹配。专项冷读篡改测试证明旧结果会
被阻断。所有新观察仍为 SUPPORTING_ONLY，UI 明示字段角色属于模型解释，未获正式业务确认。
