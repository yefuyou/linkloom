# PR18 真实 Canary：离线门禁阻塞收口

日期：2026-10-23。状态：**BLOCKED_OFFLINE_GATE；真实请求 0；28 块全部 N/E。**

遵照“门禁通过后直接执行；如有阻塞不绕过安全校验”，本轮未启动 Smoke 或正式 Provider。问题来自嵌套引文安全检查，不是赠金或授权。本任务没有查询 Billing Console。

## 固定范围与 Runner

- 生产代码固定为 PR18 `ba51b3586135f1ef52a32b5e9945d14b058bac38`。
- 复用已有 `canary-runner-trust-v2` Worktree，未创建重复项目。
- Runner 冻结集成 SHA：`693ab81c134fcc3af76e4e1861a90f97a2bde734`。相对 PR18 的 `src/linkloom` 唯一增加为原有评测日志工具 `evaluation/canary_trust.py`；其余生产代码与 PR18 Git 内容一致。Windows 工作区 CRLF 的文件字节哈希另有归档，不把它与 Git blob 的 LF 哈希混淆。
- 未修改 F Prompt、Compactor、#18 或 #19 生产逻辑。旧 Runner 的本地 quote patch 与 PR18 合并冲突只选取 PR18 原版，没有手写解决生产行为。
- 原 Runner 只允许 v3 LIVE，PR16 首轮是 null Profile。为满足本次同合同比较，只修 Runner 启动绑定：允许历史 null Profile，保留原离线门禁，并增加对原封存 28 个 ModelTurnRequest 完整相等的启动验证以及 PR18 生产版本检查。
- 已实际核验 28 个 planned request 与 PR16 `actual_requests.jsonl` 完全相同，包含输入、Profile、Schema、model、4096 输出上限、low thinking 和无 temperature。没有在比较中换用新 F Prompt。

计划 ES2002a 8 块、ES2005c 20 块，Gemini 3.8 Flash；沿用更严格的 30 次请求/预留、USD 8、输入 1,400,000 和输出 122,880 Token、总时限 3600 秒、每请求 90 秒、F/SDK 各一次尝试，零自动重试。离线估算 28 次保守计划预留 USD 7；这不是实际已扣除的预留。由于门禁失败，真实 AttemptJournal 未创建，本轮实际请求和实际新增预留均为 0。

Runner 检查结果：发送前请求/源码/来源哈希持久化、原始 SDK/normalized 回执分开保存；有归属的成功/错误与 UNKNOWN 分开分类；UNKNOWN 终止、不自动重发；晚到回执不重写封存 UNKNOWN；预算不自动退款。相关测试通过，失败的引文安全检查不能被这些通过项抵消。

## 阻塞：同种 ASCII 单引号嵌套仍释放内层文本

未修改的已有 Runner 测试：

`tests/unit/test_canary_quote_scopes.py::test_same_ascii_quote_pairs_support_a_nested_quoted_claim`

原文：

```text
同事说：'outer says '安排部署任务' outer task remains inside'。当前任务已安排。
```

在 PR18 生产代码上：

- 原始工作范围 `[17,23)`，文本 `安排部署任务`。
- `quoted_ranges` 返回 `[(4,17), (23,51)]`，内层工作区间裸露。
- `non_asserted(...)=False`。
- 用明确标记的 **scripted F 回复** 喂正常 Discovery，得到 1 个 PARTIAL，没有 NON_ASSERTED_TASK；应拒绝的引文任务进入未确认观察。
- 这不是一次真实模型误报，也不证明模型会生成该候选；它证明现有源引用安全检查在此回归输入下未拒绝候选。实际 Provider 调用仍为 0。

最小重现文件：归档 `current/tonight_blocker_repro.py` 和 `current/tonight-blocker-reproduction.json`。原始失败 JUnit、stdout/stderr、命令、源代码快照均保留。未删测试、未改期望、未给门禁手写 PASS。

原 PR18 的精确 P2：

```text
He wrote: 'cause deliver the layout after reading "'Hello'" tomorrow.'
```

仍被正确识别为外层 `[10,70)`、内层工作拒绝。两者必须同时报告：**PR18 自带 P2 与通用109条测试通过，不代表所有 Runner 既有嵌套引用合同通过**。

## 离线门禁与独立检查

| 检查 | 结果 | 资格 |
|---|---|---|
| 原 PR18 通用引文/嵌套测试 | 109 passed | 本次 quote.xml 中独立类目计数 |
| 既有 Runner 引文测试 | 19 passed / 1 failed | 上述同种 ASCII 嵌套失败 |
| B01 现有工作身份 | 15 passed | 不重复开发身份机制 |
| 引文门禁总计 | **143 passed / 1 failed** | **阻止启动** |
| Runner/历史状态、权限、UNKNOWN、P4、Reviewer 相关合同 | 162 passed | 独立诊断运行；不替代失败门禁 |
| 历史28份回复在 PR18 代码下原合同回放 | PASS | 零 Provider 调用、75项原始 F claims |
| 新28条输入与历史请求完整相等 | PASS | null Profile，不把旧回复改称v3结果 |

不变的 `junit_pass` 实际拒绝上述失败 XML：`test evidence contains failure/error/skip`。未生成有效 launch gate、未使用跳过或豁免方式构造 paid Client。原来用于新增 null Profile 支持的 RED 为 1 fail/8 pass，Runner 修复后9 pass，原始记录也保留。

独立复核若有成功产物见归档 `current/tonight-independent-review.md`；它只核对阻塞，不代表真实语义验收。未执行的能力不提高资格。

## 28份封存回复回放结果

精确相同的原 F 回复：75 claims，其中 FACT18、PLAN5、QUESTION9、PROPOSAL23、OPINION14、SPECULATION1、DECISION5。

普通 F/Discovery 链路在 PR18 下为 VALID0、PARTIAL4；5项任务提案中4项精确引用/parts校验通过，1项 TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS。旧代码的4项 NON_ASSERTED_TASK 过度遮罩不再出现。**4 PARTIAL 不是4个正确任务**：其中包含已知会议时间误报。

已知误报：`ES2002a:chunk:0006:claim:0`，`the next meeting's gonna be in thirty minutes`。这是旧模型已输出的会议事件 PLAN；PR18 修复遮罩后它也变得可见，仍为 PARTIAL、SUPPORTING_ONLY、UNCONFIRMED、deadline_role UNKNOWN、deadline null。原始模型误报增量为0；该已知误报的可见增量为+1。未将它算作真实任务恢复。

## 本轮七个问题的回答

| 问题 | 可证明结论 |
|---|---|
| 黏土模型任务是否恢复？ | **离线回放恢复接纳**：ES2005c chunk0017、`work on giving me a model in clay` 不再被引号遮罩拒绝。**新真实模型结果 N/E**。 |
| 工业设计、营销、界面工作是否找到？ | 新真实结果 N/E。原28份普通F回复没有这些工作对应的任务提案；角色 FACT 不等于工作任务。程序修复不能创造未提出的任务。 |
| 语音识别决定是否识别？ | 新真实结果 N/E。原回复有 speech recognition 的 FACT/PROPOSAL，没有该采纳事项的 DECISION；其他5条 DECISION 不冒充语音决定。 |
| F 模型漏抽多少？ | 新样本无调用，不给新增计数或正式召回率。历史可定位的三个角色工作与语音决定是诊断缺失；没有穷尽任务Gold，不能给整体漏抽率。 |
| Grounding/Discovery 拒绝多少？ | 原回复重放剩1项精确文本/描述拒绝；旧4项 NON_ASSERTED_TASK 消失。新真实样本 N/E。 |
| 新任务误报？ | 新真实样本 N/E。已知会议时间误报随修复变为可见，必须单列；额外的嵌套引文最小回归可释放脚本候选，是当前启动阻塞。 |
| 变化来自模型还是程序？ | 本次回放复用逐条相同的75项 F claims，因此接纳/遮罩变化来自程序；没有新模型回复，不能声称真实输出改善。 |

PR16、`f26191487f459d7af7016898422b9bbcfa1a5cee` 保持历史对照资格。后者包含其他 Profile 的诊断，不与本轮 null Profile 直接混成统一召回成绩。全部为 DEV_DIAGNOSTIC；无正式语义 Precision/Recall/F1 或业务批准正确率。

## 下一轮优先级

1. **启动阻塞：** 在单独获授权的生产修复任务中处理同种 ASCII 引号嵌套，保留现有失败原文作为回归，同时保证 PR18 原 P2、真实引文拒绝和引文外工作可见。不得按 AMI 场景硬编码。本 Canary 任务不修改该逻辑。
2. 修复后重新固定明确生产 SHA，重跑本次失败门禁和同28份回复；不需要 Provider 即可先证明程序拒绝恢复。门禁全过后再执行预先固定的28次真实 Canary，不补跑失败请求或重复 Smoke。
3. 真实 Canary 后才评估普通F对工业/营销/界面与语音决定的遗漏，以及会议时间误报。把模型没有提出、提出但引文不合法、Discovery拒绝、人工诊断正确与否分列；之后如需修改Prompt另立对照，不在本轮混入v3或#19。

## 保存与复现

`STATUS.json` 明确记录零调用/N/E；`evidence.zip` 包含原始失败、测试日志、源码快照、28个历史原始回执与seal、输入来源、离线计划及回放。`EVIDENCE_MANIFEST.json` 给逐文件字节哈希，历史源文件未覆盖。没有本轮 Provider 请求/响应文件，因为门禁前即停止。

校验归档（标准库、零Provider）：

```powershell
python docs/evaluation/ami_canary_pr18_20261023/verify_archive.py
```

完整代码为归档 `source/runner-693ab81.zip`；在该固定 Runner 环境中复现失败：

```powershell
$env:PYTHONPATH='src;.'
python -m pytest tests/unit/test_canary_quote_scopes.py::test_same_ascii_quote_pairs_support_a_nested_quoted_claim -q
```

预期为失败，而非通过。没有运行 `--execute`。没有购买额度、调整付款配置、修改生产上限、提交/合并到 master。
