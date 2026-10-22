# M3-S 交付：字段改造已验证，完整 LIVE 验收仍有缺口

真实 Gemini 已改善负责人、截止日期、项目和供应商的字段绑定。正常 Product 的明确
场景完成了真实调用与浏览器闭环；歧义场景 F 正确保留 UNKNOWN，但 Product 后续
分别被重规划输入预算和真实超时阻断。因此本轮整体验收为 **PARTIAL**，不宣称全部通过。

## 产品与来源

正常 Product run `ui-b96e71f09bb4` 使用公开合成两次会议、单次来源授权。
Sources导入只保存本地原文；真实Provider配置在Tasks链路，取得Ask单次授权后才发送来源。
冷读Host未配置Provider，所以来源截图的通用入库提取状态显示unavailable；它不代表此前Tasks没有真实请求。
两条“上线核查”观察都保留负责人闻夏，日期分别为 `2034-02-16`、`2034-02-20`，
第一条项目晓汀；第二条没有把上一消息项目名跨消息继承，供应商均未知。
这些字段使用自然表达“交给…掌舵”“仍由…接着做”“交付这一栏现填”，没有增加句式补丁。

- 实际 Runtime 工具：`inspect_task_context`、`query_task_relations`、`analyze_task_semantics`。
- SAME_TASK 和 REVISES_PLAN 模型提案均 SUPPORTED，引用有效；仍未获得 P4 权威。
- Reviewer 接受 `PARTIAL`，其 `evidence_sufficient` 检查的是执行、来源和回执，包含 `SEMANTIC_NOT_EVALUATED`，不代表独立认定模型语义为真。
- UI 展示各次会议负责人、日期角色和精确字段引用，来源正文可打开；没有发布可信“延后4天”，该比较仍被缺少可信关系前提阻断。
- 真实 Chromium `155.0.8059.85`；新进程无 Provider 冷读、来源打开、刷新成功，页面 errors=[]。撤权、来源更新后热/冷读均 BLOCKED，无旧 evidence/trace/task_review；跨 Workspace 返回409。额外 Provider 请求0。

实际证据：[product-clear.json](evidence/product-clear.json)、[字段截图](evidence/cold-fields.png)、
[来源截图](evidence/source-open.png)。本地数据库及完整 Runtime 产物保留于
`work/m3s-live-phase2-20261022/clear/`，安全验收已给自己的合成来源创建新版本，旧结果因此失效。

全部尝试均保留在 [product-attempts.json](evidence/product-attempts.json)：

| 尝试 | 真实结果 |
| --- | --- |
| 最初多会议问题，3请求 | F 只保留后一任务，G 两次后停止，未进 Runtime。修复新 prompt 对历史会议观察的保留要求；失败未删除。 |
| 明确多会议，7请求 | F1/G1/Runtime4/Semantic1，两个观察、两个模型关系提案，Reviewer接受；冷读浏览器补验复用产物，未重发模型。原浏览器脚本的来源标题选择器歧义已在只读补验中纠正。 |
| 歧义多会议，2请求 | F 两个观察 owner/deadline均null；G提出discover后，本地重规划 INPUT_BUDGET_EXCEEDED，未进 Runtime。 |
| 同资料、缩小为字段问题，2请求 | F 正确保留未知；G约30秒 ReadTimeout，记UNCERTAIN并停止付费。没有自动重试，也未把它算成功。 |

## 字段质量

先固定16组独立公开合成输入，含3组由只读审计者在初始实现和离线合同完成后才揭示
预期的 holdout，共17条预期观察、68个字段位。这里 holdout H01–H03 是 **M3-S** 编号，
与冻结的 M2 H01–H04 无关。输入和 Gold 的 SHA 与首次调用前持久化值完全一致。

8次真实 F 批量请求覆盖16组；模型原始输出不改写。
最终绑定器对同一批真实响应进行离线重验，**不是额外 LIVE 响应或新泛化分数**：

| 字段位结果 | 旧路径同输出对照 | 最终新绑定 |
| --- | ---: | ---: |
| 正确接受 | 5 | 31 |
| 正确 UNKNOWN | 26 | 32 |
| REJECTED | 25 | 1 |
| 缺整条观察 | 12 | 4 |
| 已观察错误接受 | 0 | 0 |

最终没有观察到程序 False Reject；剩余1个REJECTED是模型把日期输出为 `2029-07-06前`，
不满足 date-token 格式，不能算程序误拒。另4个字段位是 F04 整条任务漏提取，不能
把它们算正确UNKNOWN。旧对照25个REJECTED中含这1个格式问题；其余24个为旧字段
规则拒绝。旧路径另有两条任务被有限否定/条件 veto 整体移除，不是模型没有提议任务。

最初新绑定也误拒了3个字段（F02 vendor、F03 owner/deadline）：角色引用与工作引用
位于独立短句。已放宽为两份精确、同消息、同任务的见证并做来源重验；没有修改模型
响应或预期。F04遗漏和日期输出格式仍保留为模型/协议缺口。
只有小样本中观察到的 False Accept=0，不能推断任意模型角色解释都正确。

证据：[原始LIVE字段结果](evidence/field-live-results.json)、[最终离线重验](evidence/field-revalidation.json)、
[原故障归因](AUDIT.md)。接受字段均可重建精确来源、版本/hash/offset，SPEAKER还要求规范解析头部；
未从会议时间补年。MONTH_DAY/MONTH保留不完整精度。

## Provider 与预算

`gemini-3.8-flash`、`google-genai 2.22.0`、low thinking；SDK attempts=1。
用户明确将本轮上限由24提高到25，$8本轮/$50项目上限不变。新请求全为合成来源、串行、请求前落盘。

| 范围 | 请求 | SUCCEEDED | 已知FAILED | UNCERTAIN | 预算预留 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 独立 F 对照 | 8 | 8 | 0 | 0 | $2.00 |
| 四次 Product 尝试 | 14 | 13 | 0 | 1 | $3.50 |
| 本轮 | 22 | 21 | 0 | 1 | $5.50 |
| 项目累计 | 69 | 60 | 5 | 4 | 保守$19.43 |

阶段分布：F12/G5/Runtime4/Semantic1。21次有usage的请求：输入91,028、候选输出13,235、
SDK total105,465 tokens；total含其他计数，不等于前两项相加。超时1次usage未知，不能当0。
程序配置费率$1/$5每百万输入/候选输出的已知部分算式为约$0.157203，**不是已核实Google价格
或账单估算全额**，不含未知超时和可能的额外思考计费。账单、促销赠金扣减及计费项目均无法独立核验。
预留不等于扣费；超时的$0.25预留未释放。项目预留余额$30.57，本轮余额$2.50、余3次，但
UNCERTAIN后已停止付费，未使用余额制造请求。没有 Gemini2.5 新调用。

详见 [live-summary.json](evidence/live-summary.json)、四份campaign预算/授权记录及 [manifest](evidence/manifest.json)。
原始未删日志仍在四个 `work/m3s-live*-20261022/` 目录；归档只含公开合成资料，未提交数据库或密钥，
不需要的 Provider opaque continuation 在字段归档中省略，原文件hash和回执hash保留。

## 工程与未完成项

- 分支 `codex/m3s-task-field-grounding`，固定基线417595a2；最终提交SHA及远端回读见本轮交付消息。
- Task/Frozen受影响回归615通过；最终提示词/字段/旧序列化及Product历史兼容115通过，包含原Legacy门禁，批次有重叠、不累加为覆盖率。专项篡改/预算2通过；JS语法、Ask DOM通过。远端CI未运行。
- 独立代码复审P1/P2=0，详见 [REVIEW.md](REVIEW.md)。没有改历史Gold/Frozen评分或P4，没有另一套Runtime/Planner。
- M3-R远端仍417595a2，master仍8f9227d3；PR#6保持OPEN/Draft=true，Head67b91a15。未创建PR。

下一轮最多三项：完成歧义Product LIVE（本轮被UNCERTAIN强制停止）；缩减G重规划的重复端点/来源载荷使有界输入可用；
改善F对“任务明确、字段未知”的召回和日期token协议。正式关系批准不在本轮范围。
