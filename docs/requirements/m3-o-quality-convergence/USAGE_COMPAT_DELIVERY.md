# M3-O LIVE Usage 可选字段兼容交付

授权边界：用户本轮 Usage 修复任务 A–D；基线
`adedbb323cf39a417b2048bd9d1fab60145b5718`。仅离线补丁、测试、独立审查和普通提交推送。
分支 `codex/linkloom-m3o-usage-compat`；原 v2 工作树与 Campaign 保留原样。

**USAGE_COMPAT_TECHNICAL_ADMISSION = READY（仅离线）**。
独立只读审查 PASS，无未解决 P1/P2；该结论不是新的真实模型授权。

## 历史 D01-A 的根因

旧 SDK 快照记录 Prompt=1437、Candidates=288、Total=1725；Thinking=null、
ToolUsePrompt=null。normalized P/C/Total 为相同整数；模型版本为 gemini-3.8-flash，
wire_sends=1。v2 Transport 的其余 safe 条件成立，唯一失败条件为 Thinking 必须是
显式非负整数。SDK 类型允许 None；快照没有 HTTP body 和原始 model_fields_set。

因此，**只能确认持久 SDK 快照存在 null，无法区分 wire 字段省略、显式 null、
或 model_dump 默认添加的 null**。SDK 2.22.0 的合成 missing/null 对照有相同 dump，
重建 SDK 模型也不能恢复此前未保存的字段存在性。没有补造原始 Usage。

[官方 UsageMetadata 定义](https://ai.google.dev/api/generate-content#UsageMetadata)
给出 Total=Prompt+ToolUsePrompt+Thinking+Candidates；cached prompt 已包含在 Prompt。
由非负计数和 Total 可得 D01-A 的 Thinking+ToolUsePrompt=0，所以两项各自上界均为 0。
这是 **DERIVED** 的合同结论，不是 Provider 显式报告的 0，也不是云端账单证明。
新合同离线诊断能建立安全 Token/费用上界；旧 v2 仍永久为 UNKNOWN_STOP。

旧账本 SHA-256 保持
`20eb138bf5ed36f68f6754c2ac332468249ef8d20907088a531cf77b37f21550`。
只读 SQLite mode=ro、显式关闭连接；没有修改旧状态、预留、门、请求或旁路产物。
精简元数据见 `usage-evidence/history-diagnosis.json`，不含真实响应正文或 Grant。

## 新合同与严格边界

`account_usage(record, freeze)` 是现有 A/B 模块中的唯一用量函数，Transport 与
VERIFY 均调用它。原 SDK Usage、原 SDK 响应、normalized Usage 保持原样；
额外 `usage_accounting` 单独持久化，VERIFY 重新计算并与记录比较。

- Prompt、Candidates、Total 必须是显式非负整数；拒绝 bool、浮点、字符串、缺失。
  normalized 的这三个字段必须逐项、逐类型匹配 SDK 快照，快照与 record Usage 也必须相同。
- Thinking/ToolUsePrompt 可以缺失或 null；有值时仍须是非负整数。保留 SDK
  字典中的 ABSENT/NULL/INTEGER 状态，不冒充 wire 字段状态。
- R=Total−Prompt−Candidates。减去已报告的可选项后，负数或矛盾直接 UNKNOWN。
  两项均已知须与 R 相等；只缺一项可唯一 DERIVED；两项均缺且 R=0 可各 DERIVED 0；
  两项均缺且 R>0 则各 BOUNDED[0,R]，并保留联合约束 Thinking+ToolUsePrompt=R。
- provenance 为 REPORTED / DERIVED / BOUNDED / UNKNOWN；`safe` 和 `reason` 独立。
  已知但超上限的分项不会伪装为未知，但同样拒绝并写 UNKNOWN_STOP。
- 最坏输入 `Prompt+ToolUsePrompt.upper` ≤ 65536；最坏生成
  `Candidates+Thinking.upper` ≤ 3072，并同时服从原 billable_output_token_cap=8192。
  不会以预留中的 8192 替代真实生成上限 3072。
- Decimal 费用上界计入已知 reported/derived 分项；仍未分配的联合残差只计一次，
  使用输入/输出较高费率。上界必须 ≤ USD 0.079872。26 次 / USD 3 与原全额预留不变，
  不因估算较低而退款、增加请求或恢复额度。

账本、模型版本、唯一 wire、Schema、来源、证据门、锁和禁止重试的合同均未放宽。
UNKNOWN 或任一不安全条件仍停止，不跳过请求，不补发不确定结果。

## 版本与修改范围

新实验 `m3o-ab/v3`；新预算命名空间 `~/.linkloom/m3o-ab-v3/budget.sqlite3`。
本轮不创建该生产账本或真实 Grant。旧 v1/v2 的 Manifest 与全部冻结数据字节保留。

新版规范化 Manifest SHA-256：
`ad81805d7a1b93ec0ec835e302e13a1e941d796dc224d9f341d28f094f54fe3d`。
Manifest 文件字节 SHA-256：
`a97a8659bc458ed5c9b80713d126d7472dbe969ecffbfa0f6712959dd043d10e`。
父 Manifest 为 v2 `a8991788425ef021459bf1fa4c933cccca906cd913a515171813d25cc2dd6c03`。

执行源码仅修改：

- `task_profile_ab.py`：共享 Usage 合同、Transport 接受条件、独立 accounting 记录及 v3 身份。
- `task_profile_checkpoint.py`：VERIFY/_send_pair 复用并重算同一 accounting。

未修改 Provider adapter、ModelUsage、评分器、CLI、F/G/Host、Prompt、Schema 或模型参数。
没有建立另一套 Harness/Runtime/数据库服务。v3 的六份 sources/config/labels/groups/rubric/schema
逐字节复制 v2，来源顺序与 26 个请求内容不变；control 只改预算命名空间并绑定 Usage 合同，
新增 usage_contract.json。代码指纹变化如实记入新 Manifest。

v2 的可执行复现入口仍是原始 SHA adedbb3，v1 是 29ba35f；当前 v3 loader 不冒充
旧实现。隔离原工作树 `work/m3oc` 仍保持 v2 HEAD 及旧 Campaign 文件。

## 验证与独立审查

已记录 RED：旧执行器拒绝可安全约束的可选字段；另以单测纠正已唯一推导的
tool-use 应按输入费率计价、不能误计为未知高费率。初始 GREEN 27 项通过。
随后原始 Usage 与 accounting 回执的宽松数字相等问题分别记录 2/3 项 RED，
均改为类型敏感 canonical JSON digest 比较；不会将 100.0 当成 100 或将 1 当成 True。
最终受影响回归、故障矩阵与独立审查结果见 `usage-evidence/verification.json`
和 `USAGE_COMPAT_REVIEW.md`；未重新处理无关的全量历史失败。

环境诊断如实保留：初始 191 项批次 187 通过、4 项 M3-L 集成失败，trace 为
MODEL_ARTIFACT_WRITE_FAILED。相同沙箱改用更短的新临时根后，精准 5 项通过，
最终完整 100 项关联回归通过；没有修改 Canary/Runtime 源码或断言。
最终 A/B 批次有 5 个子进程在 35/55 秒 wall-clock 限制处超时，其余 94 项通过。
离线故障子进程的等待时间仅在测试中改为 120/180 秒，覆盖 SDK 初始化和本地 fsync；
生产 HTTP timeout 130000ms、重试次数、Token/费用上限和所有故障断言均不变。
精确复测覆盖全部 8 个崩溃点及跨进程锁，不将测试超时伪装为合同逻辑通过。

最终证据：A/B 三文件的 99 个不同用例由完整批次的 94 PASS 与故障精确组的
9 PASS 覆盖，后者包含此前 5 个超时用例；另有 M3-L/M3-N 关联回归 100 PASS。
去重后共 199 个用例均有最终通过记录，不将重叠批次累加，也不声称单个全量批次全绿。
编译及 git diff --check 通过。XML 的哈希、命令与中间失败均保留在 verification.json。
独立 Reviewer 已核验代码、Manifest、旧数据保护和最终测试证据，最终结论 PASS；
所有范围内问题已关闭，未解决 P1/P2 为 0。未运行真实 Provider 的语义质量评估。

官方 SDK MockTransport 的同一路径模拟：FIRST_PAIR 2 次、VERIFY 0 次、
CONTINUE_REMAINING 24 次、完成后只读恢复 0 次；同一临时账本中 26 个唯一 identity
全部 COMPLETE，门 PASS、Campaign COMPLETED。固定总预留 USD 2.076672，低于 USD 3。
两项可选字段仍为原始 null，保守 accounting 为 BOUNDED；并未把模拟响应当成真实模型效果。

MockTransport 始终走现有 LIVE 分阶段路径，使用合成密钥、临时账本与外网阻断插件；
离线通过不代表真实 Provider 质量或最终账单。历史 D01-A 只用于只读合同诊断，
不回填为 v3 COMPLETE，也不混入新实验模型成绩。

## 下一次真实运行的条件

本轮真实 Provider/countTokens 均为 0。没有新的 LIVE 授权。旧 v1/v2 Grant 不适用于 v3。
未来若重新发送 D01-A，属于 **v3 的新请求**，不能复用旧结果或额度，必须单独明确列明，
等待用户对最终 v3 Manifest、26 次/$3、有效期及所有来源重新授权。本轮完成离线交付后停止。
