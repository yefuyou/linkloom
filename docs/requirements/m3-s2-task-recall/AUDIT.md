# M3-S2 原因与修复边界

实际历史收据与逐字段对照：[original-audit.json](evidence/original-audit.json)。
八份本地原始收据的 SHA256 全部等于 M3-S 已归档 hash，未改写原响应。

| 类别 | 实际证据 | 原因 |
| --- | --- | --- |
| 未提议任务 | F03/F04 原响应 `yJLZarStHZStuMYP29iGmAQ` 仅有 F03 一个 claim，无 F04，投影 failures=[] | F04 明确“迁移排练是本周要做的工作”，后句仅使 owner/deadline 未知。旧提示虽允许 null，没有明确工作存在性和字段确定性分离；模型在任务发现层漏掉，而非投影丢弃。 |
| 日期协议错误 | H02 原响应 `8ZLZavG9MJqOytMP7cHvsAU` task.deadline_text 与 binding.value 都是 `2029-07-06前` | 任务本身保留，日期字段 `_date_value` 报 DEADLINE_INVALID；是纯日期 Token 混入角色词，不是日历程序误拒一个合法 Token。 |
| 投影失败 | 原 8 批在 v2 `_project` 冷重验均无 projection failures | 没有证据将 F04 或 H02 归为消息投影失败。日期字段拒绝与整条任务拒绝不同。 |
| 旧 veto 误拒 | 原 F05/F06 的相同模型提议去掉 v2 声明后，旧 profile 返回 NON_ASSERTED_TASK；v2 各保留一条 | 有限否定/条件作用域将历史或字段上下文扩散到明确当前任务。旧回放保留历史行为，不新增中文补丁。 |

修复是版本化 F 抽取协议：先逐消息盘点实际断言的具体工作，再独立判断四个字段；
字段空缺、候选负责人或暂拟日期不撤销已明确存在的工作。拒绝真实建议、条件工作、
否定、引用示例和纯背景的要求仍然明确。没有用动作词发现任务，也没有程序补造 F04。

日期沿用现有数据模型，明确分开 binding.quote（原文角色引述）、deadline_text /
binding.value（同一个纯日期 Token）、role/state（业务角色及确定性）。v3 Schema
使用现有日期语法约束 Token，保留中文日期原拼写和不完整精度。程序不剥离“前”、
不补年、不修日历值，继续独立校验同一规范消息、精确引用、speaker、Workspace、
来源版本/hash、日历合法性及精度。旧 v1/v2 Schema、提示与 wire shape 不变。

新增固定案例首个 v2 对照还有独立协议故障：模型给 N04 发出未知 claim_type=PROPOSAL，
导致整批 MALFORMED_EXTRACTION；原响应中 N01–N03 的任务已提出，不能把整批失败叫
模型漏提议。v3 对应独立实验在 TLS 阶段 known failure，无可评分输出。均未重试。
其余尚未发送实验通过单独有限会话完成，原失败及预留保留。
