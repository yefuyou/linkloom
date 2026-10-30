# ES2003 冻结版最终评测

人工抽查已完成：7 条全部同意，样本一致率 7/7（100%），已写入 CSV 人工判断列。标准答案及 v2 均在运行前冻结；未根据本轮结果修改。

运行起点：`80020f11107172bf062a25a22195ca108fe946c8`；冻结提交：`a8dabf40d9a2613781ded1f55747bb6a8e5a81c2`。
gold SHA-256：`fdb653c84c48cfe9041112352614c80fcf0a79e18777ad70d7314150ecaec7b8`。运行前只计算 gold 字节哈希，全部预测保存后才解析 gold 供裁判使用。
模型：gemini-3.8-flash，thinking_level=low，输出上限 32768 token（包含思考和 JSON）；裁判 deepseek-flash。配置和匹配规则沿用冻结脚本。

| 指标 | 基线 | legacy（接线修复后） | v2 |
| --- | ---: | ---: | ---: |
| 待办准确率 | 7/8（87.5%） | 5/8（62.5%） | 5/5（100.0%） |
| 待办召回率 | 7/9（77.8%） | 5/9（55.6%） | 5/9（55.6%） |
| 负责人正确率（匹配任务） | 6/7（85.7%） | 4/5（80.0%） | 4/5（80.0%） |
| 截止时间正确率（匹配且 gold 明确期限） | 0/0（不适用） | 0/0（不适用） | 0/0（不适用） |
| 截止时间端到端正确率（全部明确期限 gold） | 0/1（0.0%） | 0/1（0.0%） | 0/1（0.0%） |
| 讨论误判数 / 全部预测 | 0/8（0.0%） | 1/8（12.5%） | 0/5（0.0%） |

准确率分母为预测数，召回率分母为全部 gold；负责人只在任务匹配后计分。截止时间只在 gold 明确期限的条目计分，同时列出匹配子集和端到端分母；未说明／无法确定单独统计，不算正确或编造。讨论误判行分子为数量。

负责人代词未解析的一律判错；说话人编号按本场 AMI 元数据映射为角色。原文明确指向同一次下次会议的相对时长与事件表述等价，不能据会议技术时间锚推断日历日期。

## AMI 角色映射

来源：官方 AMI ZIP 的 `corpusResources/meetings.xml`，成员 SHA-256 `8ab6cdcf03ed863e839418f31c7380392397a2eed892d73b9bc1b438c7ec520a`。本轮重新读取，与四场预处理转录中的映射逐项核对一致。

| 会议 | A | B | C | D |
| --- | --- | --- | --- | --- |
| ES2003a | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |
| ES2003b | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |
| ES2003c | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |
| ES2003d | User Interface Designer (MEE011) | Project Manager (MEE009) | Marketing Executive (MEE012) | Industrial Designer (MEE010) |

## 基线逐项差异

TP=7，FP=1，FN=2；截止时间弃答 5/8；匹配且有期限 gold 上弃答 0/0。

漏掉的任务：

- ES2003a / ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面如何运作，并弄清楚用户想要什么。
- ES2003c / ES2003c-c001：Build a clay prototype, working closely together；中文：制作黏土原型，并紧密合作。

多出的预测：

- ES2003c / p-84eb7bc33948bbcd [UNSUPPORTED]：Look more at the physical make-up of the remote；中文：进一步研究遥控器的物理构造。裁判理由：预测任务“Look more at the physical make-up of the remote”在会议中无明确采纳或分配，且owner为“未说明”，无法对应任何金标任务，属于无依据的预测。

## legacy（接线修复后）逐项差异

TP=5，FP=3，FN=4；截止时间弃答 8/8；匹配且有期限 gold 上弃答 0/0。

漏掉的任务：

- ES2003a / ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面的工作方式，并弄清楚用户想要什么。
- ES2003b / ES2003b-c001：Look into the feasibility of a small transmitter and whether an LCD screen requires too much power, based on the design suggested so far；中文：根据目前提出的设计，研究小型发射器的可行性以及LCD屏幕是否耗电过多。
- ES2003b / ES2003b-c002：See what people think about the minimalist approach with a joystick controlling basic functions and mobile-like manipulation；中文：了解人们对这种极简方案（用摇杆控制基本功能、像操作手机一样操作）的看法。
- ES2003c / ES2003c-c002：Look at product evaluation.；中文：研究产品评估。。

多出的预测：

- ES2003a / p-145d7d661374aae6 [WRONG_TASK]：get that out to everybody；中文：把内容发给大家。裁判理由：该预测仅截取“把内容发给大家”这一片段，属于同一项写作任务的下游动作，与金标任务“撰写并发送”相比范围过窄，且与另一预测重复指向同一金标，按一对一规则不匹配。
- ES2003c / p-00fc400f194709a0 [DISCUSSION]：look into the costs of those；中文：调查这些的成本。裁判理由：该句为讨论批量采购成本，属于一般性讨论，未明确采纳为具体任务，故为讨论。
- ES2003c / p-67844f233517f666 [WRONG_TASK]：load it up；中文：加载它。裁判理由：该句为会议中加载演示文稿的操作，属于会议机制，不是实质性未来工作，故为错误任务。

## meeting_v2（冻结版）逐项差异

TP=5，FP=0，FN=4；截止时间弃答 4/5；匹配且有期限 gold 上弃答 0/0。

漏掉的任务：

- ES2003a / ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面如何运作，并弄清楚用户想要什么。
- ES2003b / ES2003b-c003：Look into the speaker, LED and transmitter for locating the remote；中文：研究用于定位遥控器的扬声器、LED和发射器。
- ES2003c / ES2003c-c001：Build a clay prototype, working closely together；中文：制作一个黏土原型，并紧密合作。
- ES2003c / ES2003c-m002：Start writing up the project documentation；中文：开始撰写项目文档。

多出的预测：

无。

## 请求、费用与抽查

请求 39/80；按 API 实际 token 用量和沿用费率计算 $0.246469698/$1.50（用量估算，不是账户账单）。失败请求的未知费用保守保留。

| 模型 | 请求 | 输入 token | 输出 token（含思考） | 费用 USD |
| --- | ---: | ---: | ---: | ---: |
| gemini-3.8-flash | 26 | 285989 | 5228 | 0.234096750 |
| deepseek-flash | 13 | 149290 | 8293 | 0.012372948 |

## 首次中止与唯一一次裁判续跑

首次运行在 ES2003a / legacy 裁判处中止：两条预测 p-145d7d661374aae6 和 p-752e663f76945fc4 都被判为 MATCH，且均指向 ES2003a-c001，违反同一 gold 最多匹配一条预测的既定规则。首次结果见 [中止报告](results_es2003_attempt1.md)。
无效响应原文完整保存在 [裁判响应原文](judge_es2003a_legacy_invalid_response.txt)，UTF-8 内容 SHA-256 为 `745e83eedf046d8a432cdce5a22452efaf4eaae01e6be0a0f6b6d48de0dfa115`。原始提供商响应封装仍在被忽略的首次运行目录中。
用户随后明确授权只续跑裁判一次。重判提示仅追加 ES2003a 当前响应的校验错误、冲突的预测 ID/gold ID 和既有一对一规则原文，没有发送其他会议的判断结果。所有预测和已通过校验的 ES2003a 基线裁判响应均复用；没有新增 Gemini 调用。重判及余下判断均通过同一个冻结校验器，未自动修复裁判结论。

| 执行 | 状态 | 请求数 | 费用 USD |
| --- | --- | ---: | ---: |
| 首次运行 | 裁判不合规，中止 | 28 | 0.235667634 |
| 唯一一次裁判续跑 | 完成 | 11 | 0.010802064 |
| 累计 | 完成 | 39 | 0.246469698 |

抽查保证包含 ES2003a / legacy 的实际重判判断，位于 CSV 数据行 [3]（不含表头）。固定随机种子，在该系统样本内置换一个位置满足此条件，总样本量保持各系统向上取整的 20%。

抽查固定种子 20261029，各系统分别向上取整抽 20%，共 7/31 条。包括预测判断与未匹配 gold，附中文翻译、裁判理由；人工判断列的 7 条均已填写“同意”。

## 执行与边界

全部预测只生成一次；未自动重试或择优选结果。裁判续跑仅限报告中明确披露的授权范围。所有系统的全部预测完成并保存之后，才读取标准答案进行评分。预测输入仅来自转录及原始角色元数据；候选 CSV、AMI 摘要和 gold 未进入被评测系统。baseline 整场读取；legacy 保持 32 KiB 适配器上限和发言接线；v2 使用冻结的可信 Host 192 KiB 上限。

未修改冻结的 v2 源码、提示词、规则、阈值和 gold，原评测脚本也未修改。ES2002 开发集结果不在本轮重跑。完整原始响应、转录和中间结果留在忽略的 work/；仓库只收结果、必要的抽查引用和本轮评测接线。

AMI Meeting Corpus / AMI Project / AMI Consortium；来源 https://groups.inf.ed.ac.uk/ami/corpus/ ，CC BY 4.0：https://creativecommons.org/licenses/by/4.0/ 。

抽查文件：[spot_check_es2003_final.csv](spot_check_es2003_final.csv)。新增评测接线及相关离线测试共 107 项通过；本轮未重跑全量 pytest，冻结版全量测试结果保持在 v2_frozen.json 中。

## 诊断（不改变成绩）

本节只读取已保存的模型响应、请求、候选和裁判结果，不重新抽取、不重新裁判。新增模型请求 0 次，新增费用 $0；累计仍为 39 次、$0.246469698。v2、gold、评分规则及本报告原有成绩均未改变。下面区分“模型没有单列任务”“模型生成时将任务捆绑”和“Host 在模型输出之后过滤”，不把它们混为同一原因。

### 原始候选与过滤统计

统计单位为原始工具响应 `args.tasks` 中的一项。证据位于 `work/ami_tasks_results_es2003_final/prediction_runs/v2/`；四份提供商响应 `provider-response-0023.json` 至 `provider-response-0026.json` 的工具参数，已分别与 ES2003a–d 的 `*.v2-discovery.json` 中 `responses[0]` 逐项核对一致。这些完整响应继续留在被忽略的 work/，不提交整场转录。

| 会议 | 原始 tasks 条数 | 状态排除 excluded | 校验拒绝 rejected | Host 去重减少条数 | 最终保留 |
| --- | ---: | ---: | ---: | ---: | ---: |
| ES2003a | 1 | 0 | 0 | 0 | 1 |
| ES2003b | 2 | 0 | 0 | 0 | 2 |
| ES2003c | 1 | 0 | 0 | 0 | 1 |
| ES2003d | 1 | 0 | 0 | 0 | 1 |
| 合计 | 5 | 0 | 0 | 0 | 5 |

**原始输出中的候选被 Host 过滤规则排除的数量为 0/5。** 5 条均为 `adopted=true`、`scope=AFTER_MEETING`、`end_state=PENDING`；所有 `member_ids` 均只有 1 个，`validation_issues` 均为空。原因分类如下：未采纳 0、会中范围或范围不确定 0、已完成/取消/完成状态不确定 0、引用/来源校验失败 0、负责人或其他字段校验失败 0、去重合并减少 0。没有实际发生的排除理由可列举；模型未输出的潜在任务不属于这 5 条，不能算作 Host 过滤。

用于核对的冻结规则原文：

- 会后范围、采纳及完成状态：[meeting_tasks_v2.py:193](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L193) 为 `return self.adopted and self.scope == "AFTER_MEETING" and self.end_state == "PENDING"`。第 391 行分别返回满足此条件的 `candidates` 和不满足条件的 `excluded`；本轮后者全部为空。
- 引用校验：[meeting_tasks_v2.py:249](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L249) 为 `if not span.verify(artifact): raise ValueError("SOURCE_EVIDENCE_INVALID")`；缺失工作依据、负责人依据或截止时间精确原文时，还会分别产生 `WORK_WITNESS_MISSING`、`OWNER_WITNESS_MISSING`、`DEADLINE_NOT_EXACT_SOURCE`（第 252–257 行）。第 387 行把校验异常放进 `rejected`；本轮全部为空。
- Host 去重：[meeting_tasks_v2.py:269](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L269) 的说明为 `No semantic calls: equal resolved principals AND the model's equal label.`；关键分组表达式为 `key = (candidate.owner_ids, label) if candidate.owner_ids else ("unknown", position)`（第 273 行）。只合并已解析负责人和规范化交付物标签均相同的候选，未知负责人不合并。本轮每个保留候选都只有一个原始成员，没有 Host 合并。

上述零排除结论有保存的中间结果支持；不能据此推断模型内部从未放弃其他想法。四场 `coverage_complete=true` 也只是模型的覆盖声明，不能证明没有漏项。

### ES2003b-c003：内容已提出，但生成时混入另一任务

标准任务：`Look into the speaker, LED and transmitter for locating the remote`，中文：**研究用于定位遥控器的扬声器、LED 和发射器**；负责人 D／工业设计师。

输入完整：`ES2003b.v2-request-0.json` 的 `DATA.whole_meeting=true`，包含全部 292 条 canonical 发言。相关发言 ordinal 264–266 均在同一个整场请求内：

> D：So I should just look at um the speaker , the speaker and an L_E_D_ . And
>
> B：Speaker and L_E_D_ for locating , um ,
>
> D：Yeah , and a transmitter .

中文：D：“所以我应该研究扬声器和一个 LED，还有……”；B：“用于定位的扬声器和 LED……”；D：“对，还有一个发射器。”

**模型提出了相关内容，但没有把 c003 单列为独立候选。** 原始 `provider-response-0024.json:75` 的第一条任务为：

> Investigate the technical feasibility of incorporating a speaker, LED, transmitter, and LCD screen (power requirements) into the remote control design

中文：**研究将扬声器、LED、发射器和 LCD 屏幕（功耗要求）纳入遥控器设计的技术可行性。** 其 `owner_ids=["D"]`、`deliverable_key="feasibility_study_electronics"`，状态为已采纳、会后、待完成。它同时引用 ordinal 249（小型发射器/LCD 功耗研究）和 264、265、266、268（D 的扬声器/LED/发射器确认）。

原始响应第 27 行的 `reason` 也直接把两组分工串在一起：

> Speaker B asks to look into the feasibility of the small transmitter and LCD screen power/electronics, and Speaker D explicitly confirms adopting the task of looking into the speaker, LED, and transmitter.

中文：B 要求研究小型发射器及 LCD 屏幕功耗/电子部分的可行性，而 D 明确确认承担研究扬声器、LED 和发射器的任务。因此，捆绑已经存在于模型原始输出，发生在 Host 去重之前。

该候选随后通过全部校验并保留为 `meeting_task_1797eebe8c190161cfa846a74828b85d`，`member_ids` 仅含它自身；`ES2003b.v2-discovery.json:258` 和第 259 行分别为 `excluded=[]`、`rejected=[]`。**没有会后范围、完成状态、引用或去重规则拒绝它。**

评分阶段，保存的 `work/ami_tasks_results_es2003_judge_continuation/judge_v2/ES2003b.linkloom.judge.json:5` 将对应预测 `p-cda96757304fa696` 匹配到 **ES2003b-c001**，而非 c003；第 9 行的既有裁判理由明确指出预测“合并了扬声器和 LED（属于 c003）”，并把负责人判错。既定一对一规则原文为 `one prediction matches at most one gold task and each gold task at most once`（[eval_tasks.py:56](../../scripts/eval_tasks.py#L56)）。同一预测没有再匹配 c003，因此 c003 在现有成绩中为 FN。本节仅追踪既有映射，不更改或重新评价该裁判结论。

基线则单独输出 `Look into the speaker, LED, and transmitter components for remote location functionality`（中文：研究用于遥控器定位功能的扬声器、LED 和发射器组件），负责人 D，保存的裁判判断 `p-c082735076d8f5bf` 匹配 c003。

提示词没有要求把这些工作捆绑。相反，[第 26–27 行](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L26) 要求 `Inventory independent deliverables separately, including explicit assignments and first-person commitments.`（独立交付物分别列出，包括明确分工和第一人称承诺）；[第 60–61 行](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L60) 要求 `Different obligations must use different keys. A shared topic does not make obligations identical.`（不同义务必须使用不同标签，同一主题不代表同一义务）。本项证据支持“生成时把两项分工写成一条”，不支持“严格过滤把已有 c003 候选删掉”。

### ES2003c-m002：未生成独立的文档撰写候选

标准任务：`Start writing up the project documentation`，中文：**开始撰写项目文档**；负责人 B／项目经理。

输入完整：`ES2003c.v2-request-0.json` 的 `DATA.whole_meeting=true`，包含全部 346 条 canonical 发言。ordinal 318 是 B 的同一段分工总结，其中精确存在：

> I'm I'd better start writing up a hell of a lot of crap .

中文：**我……我最好开始写一大堆东西了。** gold 以这段第一人称自我承担为依据；本节不扩大任务或推断期限。

**模型没有提出文档撰写候选。** 原始 `provider-response-0025.json:79` 的 `tasks` 只有一项：`Look into product evaluation methodology for testing the remote control prototype`（中文：研究用于测试遥控器原型的产品评估方法），`deliverable_key="product_evaluation_plan"`，负责人 `MEE012`（营销主管）。它在第 58 行引用了 **同一条 ordinal 318**，在第 81 行的 `reason` 只解释其中“你去研究产品评估”的分工及所推断的期限，没有解释为什么遗漏紧邻的 B 的文档撰写承诺。

因此，承诺原文没有因分块或输入格式丢失，同一发言也已经被另一候选引用；但文档任务没有进入原始 `tasks`，没有机会接受后续 Host 校验。`ES2003c.v2-discovery.json:213` 和第 214 行的 `excluded=[]`、`rejected=[]`，唯一保留候选只有一个 `member_id`。**不是会后范围、完成状态、引用校验或去重删掉了它。** 基线单独输出 `Write up meeting notes/documentation`（中文：撰写会议记录/文档），负责人 B，保存的裁判判断 `p-2e8126c87ed26ceb` 匹配 m002。

实际发送的提示词中，有可能让模型面对对象含糊的自我承担时偏保守的要求包括：

- [第 23 行](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L23)：`Extract concrete adopted obligations from this ONE meeting.` —— 只抽取本场具体、已采纳的义务。
- [第 27–28 行](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L27)：`Do not invent obligations from topic discussion, requirements, unadopted suggestions or job conventions.` —— 不从主题讨论、需求、未采纳建议或岗位惯例中编造义务。
- [第 34–35 行](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L34)：`Include at least one work witness and explain why the references concern the same obligation.` —— 每项至少提供一条工作依据，并解释引用为何属于同一义务。
- [第 53 行](../../src/linkloom/semantic_ingestion/meeting_tasks_v2.py#L53)：`Only adopted work remaining after the meeting belongs in the pending todo list.` —— 只有已采纳、会后仍待完成的工作进入待办。

“a hell of a lot of crap”没有明确命名交付物，“I'd better”又是自我催促语气；上述具体性、采纳及证据要求**可能**让模型未把它列为独立承诺。这是基于措辞与提示词的解释假设，不能从保存响应确认真实原因：没有针对该项的排除理由，也没有 `UNCERTAIN`/`COMPLETED` 等候选状态记录。

同时，提示词第 26–27 行明确要求列出第一人称承诺，第 38 行为 `Resolve first-person undertakings to their canonical speaker`（把第一人称承担解析到对应说话人），第 39–40 行还说明负责人缺失或含糊不应抹去真实工作。因此不能断言提示词禁止抽这条任务，也不能把漏项归因于负责人或截止时间必须齐全。现有证据能确定的阶段是：**模型未把同段中的文档承诺生成为独立候选，Host 没有过滤该候选。**
