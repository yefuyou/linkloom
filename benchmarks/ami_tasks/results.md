# AMI 四场会议待办抽取正式评测

本表使用原评测保存的预测与匹配关系；6条人工抽查已完成，并据人工修正规则统一离线重计分。未修改代码、gold、任务匹配关系或误报类别，没有新增模型请求。

被评测模型：gemini-3.8-flash；thinking_level=low；总输出上限 32,768 token（含思考）。裁判：deepseek-flash。
标准答案 SHA-256：`8df36c0dac65e755dbf0ac8a33dbe4be491b0718a260c604d1f5601d31b64855`；11 条人工 gold，其中 8 条有明确截止时间。

| 指标 | 基线 | LinkLoom |
| --- | ---: | ---: |
| 待办准确率（precision） | 11/11 (100.0%) | 4/7 (57.1%) |
| 待办召回率 | 11/11 (100.0%) | 4/11 (36.4%) |
| 负责人正确率（匹配任务） | 11/11 (100.0%) | 1/4 (25.0%) |
| 截止时间正确率（匹配且 gold 有日期） | 8/8 (100.0%) | 0/2 (0.0%) |
| 截止时间端到端正确率（全部有日期 gold） | 8/8 (100.0%) | 0/8 (0.0%) |
| 讨论误判数 / 全部预测 | 0/11 (0.0%) | 0/7 (0.0%) |

准确率分母为预测数，召回率分母为 11 条 gold；负责人分母为任务匹配数。日期主指标分母为匹配且 gold 明确给出日期的任务数，同时提供全部 8 条有日期 gold 的端到端指标。讨论行的分子是数量，分母只用于说明它占全部预测的比例。

## 基线逐项差异

TP=11，FP=0，FN=0；日期弃答 3/11 条预测，其中匹配有日期 gold 的弃答为 0/8。

漏掉的任务：

无。

多出的预测：

无。

## LinkLoom逐项差异

TP=4，FP=3，FN=7；日期弃答 7/7 条预测，其中匹配有日期 gold 的弃答为 2/2。

漏掉的任务：

- ES2002a / ES2002a-c002：Work on the technical functions and user interface of the remote control；中文：着手进行遥控器的技术功能和用户界面工作。
- ES2002a / ES2002a-c003：Think about what requirements the remote control has to fulfill；中文：思考遥控器必须满足哪些需求。
- ES2002b / ES2002b-c001：Think about the components concept；中文：思考组件概念。
- ES2002b / ES2002b-c002：Think about the user interface；中文：思考用户界面。
- ES2002b / ES2002b-c003：Think about trend watching；中文：思考趋势观察。
- ES2002c / ES2002c-c002：Build a prototype of the remote control with Play-doh；中文：用橡皮泥制作遥控器原型。
- ES2002d / ES2002d-c006：Submit the evaluation criteria；中文：提交评估标准。

多出的预测：

- ES2002d / p-3a048f4281b92cc5 [UNSUPPORTED]：write up our results on the board；中文：在白板上写下我们的结果。裁判理由：该任务是在本次会议中由C在白板上记录评估结果，属于会议进行中的演示/记录行为，非会议后需完成的实质工作，故不构成有效任务。
- ES2002d / p-9706fabaf4cf0770 [UNSUPPORTED]：redraw this；中文：重新绘制这个。裁判理由：'redraw this'指B在会议中重绘原型草图，属于会议进行中的即时操作，非会议后需完成的实质任务。
- ES2002d / p-85e47e1d5170093d [DUPLICATE]：specify the final design in the final report；中文：在最终报告中详细说明最终设计。裁判理由：该预测与p-068cf2d1ec7258fb重复，均指撰写最终报告并包含最终设计细节，金标任务已被匹配一次。

首个基线请求在附加引用精确校验处停止，原始响应未保存；用户明确确认修复后续跑。此前 1 次请求 / $0.006915000 已计入下面累计用量。续跑前固定的 gold 未改变，没有多轮比较择优。

## 请求与费用

总请求数：29/200；按实际 API 用量及公布费率计费 $0.172675374/$2.00，不含先前预标注。

| 模型 | 请求数 | 输入 token | 输出 token（含思考） | 用量费用 USD |
| --- | ---: | ---: | ---: | ---: |
| gemini-3.8-flash | 21 | 189443 | 4350 | 0.158394750 |
| deepseek-flash | 8 | 128316 | 7747 | 0.014280624 |

spot_check.csv 固定种子 20261029，按系统从预测判断及未匹配 gold 判断中各抽取向上取整的 20%，共 6/25 条；包含中文翻译、裁判理由和已填写的人工判断列。

## 范围与限制

两套系统只接收原始标准转录。全部预测完成后才读取 gold；摘要、候选和人工标注未进入被评测系统。基线整场一次（SDK 直调以容纳全文），LinkLoom 使用未修改的产品 task_observation 流程及 16 个重叠块；仅合并绝对原文位置相同的工作证据。分块上下文、普通文档发言人识别及英文相对日期规则均可能影响结果。

语料及全量模型输出保留在被忽略的 work/；Git 只提交必要的 gold 引用和抽查引用，不提交整场转录或 ZIP。AMI Meeting Corpus / AMI Project、AMI Consortium，CC BY 4.0：https://groups.inf.ed.ac.uk/ami/corpus/ ；https://creativecommons.org/licenses/by/4.0/ 。

## 人工抽查与统一规则修正

6条中 **5条完全同意，1条部分不同意，完全一致率5/6＝83.3%**。第4条 ES2002c-c003 的任务匹配同意，负责人判定不同意：预测只给代词 I，未解析到具体人或角色；第1、2、3、5、6条均同意。抽查原裁判字段和理由保留，人工意见写入人工判断列。

修正规则来自人工抽查反馈，统一作用于两套系统：编号按该场AMI元数据映射角色后比较；未解析代词一律判错；明确指向下次会议的 in thirty minutes 与 next meeting 等价。已有具体角色名称的同义表达仍按原规则比较。

| 指标 | 基线修正前 | 基线修正后 | LinkLoom修正前 | LinkLoom修正后 |
| --- | ---: | ---: | ---: | ---: |
| 待办准确率 | 11/11（100.0%） | 11/11（100.0%） | 4/7（57.1%） | 4/7（57.1%） |
| 待办召回率 | 11/11（100.0%） | 11/11（100.0%） | 4/11（36.4%） | 4/11（36.4%） |
| 负责人正确率（匹配任务） | 11/11（100.0%） | 11/11（100.0%） | 3/4（75.0%） | 1/4（25.0%） |
| 日期正确率（匹配且gold有日期） | 7/8（87.5%） | 8/8（100.0%） | 0/2（0.0%） | 0/2（0.0%） |
| 日期端到端正确率（全部有日期gold） | 7/8（87.5%） | 8/8（100.0%） | 0/8（0.0%） | 0/8（0.0%） |
| 讨论误判数/全部预测 | 0/11（0.0%） | 0/11（0.0%） | 0/7（0.0%） | 0/7（0.0%） |

统一复查还发现 LinkLoom 的 ES2002d-c005 负责人同样是 I，因此也从正确改为错误；LinkLoom正确负责人由3个降为1个。基线 B/C 编号映射后仍正确。基线 ES2002c-c002 的日期从错改对。只改了这3个字段判定，任务匹配、TP/FP/FN、gold和费用均未改变。

### 每场说话人—角色映射

| 会议 | A | B | C | D | 原始XML起始行 |
| --- | --- | --- | --- | --- | ---: |
| ES2002a | 工业设计师 | 项目经理 | 用户界面设计师 | 市场营销主管 | 231 |
| ES2002b | 工业设计师 | 项目经理 | 用户界面设计师 | 市场营销主管 | 237 |
| ES2002c | 工业设计师 | 项目经理 | 用户界面设计师 | 市场营销主管 | 243 |
| ES2002d | 工业设计师 | 项目经理 | 市场营销主管 | 用户界面设计师 | 249 |

来源：官方 `work/ami_tasks_download/ami_public_manual_1.6.2.zip` 内 `corpusResources/meetings.xml` 的 nxt_agent/role 字段。对应本地转换文件为 `work/ami_tasks_data_es2002/transcripts/ES2002[a-d].json` 的 speakers 字段。ES2002d的C/D发生互换，原始XML第250行有明确注释。PM=Project Manager；ID=Industrial Designer；UI=User Interface Designer；ME=Marketing Executive。


## LinkLoom逐条错误分析（只读追踪，不重跑）

证据基于提交8d481e705、已保存转录、Gemini原始响应及各会议LinkLoom中间结果。下面块编号从0开始；字符范围是标准转录中的[start,end)。分块边界由同一提交的纯分块函数只读重建，本轮没有保存完整模型请求体，因此这里证明的是分块输入构造，不宣称有完整网络请求日志。全部10条相关原文至少在一个块中完整存在。

### 7条漏检

| gold_id及中文 | 原文范围 → 完整所在块 | 模型原始输出证据 | 最早问题阶段与校验结论 |
| --- | --- | --- | --- |
| ES2002a-c002，技术功能和用户界面工作 | [16534,17029) → 块1 [11547,19183)；原文“Um for user interface , technical functions ...”（用户界面方面负责技术功能） | provider-response-0004.json:24，只有工业设计与营销2个claims；:34为工业设计，:160为营销，无此任务 | 模型漏提；没有对应claim进入校验，不是负责人过滤或去重删掉 |
| ES2002a-c003，梳理遥控器需求 | [16534,17245) → 块1 [11547,19183)，包含B的前后两段及中间A的“Yep” | provider-response-0004.json:160有PLAN/ASSERTED候选“Um and uh marketing executive , you'll be just thinking ...”（营销主管去思考需求），但拼接了两段B发言 | 引用校验拒绝：task.text在块中出现0次。ES2002a.linkloom.json:21记TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS，proposal_index=1 |
| ES2002b-c001，思考组件概念 | [42264,43205) → 块3 [35244,47036)；原文“Industrial Designer , you're gonna be thinking about the components concept”（工业设计师负责组件概念） | provider-response-0009.json:24只有1个claim，:86为“so I'll be putting the minutes ...”（我要上传纪要） | 模型漏提；没有组件概念候选，未发生该条校验拒绝 |
| ES2002b-c002，思考用户界面 | 同一完整原文及块3；“User Interface Designer gonna be thinking about our user interface”（用户界面设计师负责用户界面） | provider-response-0009.json:86，只有上传纪要候选，无用户界面候选 | 模型漏提；没有对应拒绝规则 |
| ES2002b-c003，趋势观察 | 同一完整原文及块3；“marketing you're gonna be thinking about trend watching”（营销负责观察趋势） | provider-response-0009.json:86，只有上传纪要候选，无趋势观察候选 | 模型漏提；没有对应拒绝规则 |
| ES2002c-c002，用橡皮泥制作遥控器原型 | [48285,48694) → 块4 [46193,51929)；“the designers are gonna be working with Play-doh on that”（设计师将用橡皮泥制作） | provider-response-0015.json:24只有1个claim，:86为“Um and I'll get the the minutes up as soon as possible .”（我会尽快发布纪要） | 模型漏提；并非因“hopefully”被程序过滤，日志没有此任务的NON_ASSERTED_TASK拒绝 |
| ES2002d-c006，提交评估标准 | [31848,32136) → 块2 [23602,35305)，含否定背景、询问及C“I will , yeah”（我会提交） | provider-response-0019.json:24原文为“claims”: [] | 模型未提出任何候选；没有进入负责人绑定、引用或去重。不能据此推断模型内在为何漏掉承诺 |

上表原始响应完整路径均为 `work/ami_tasks_results_continued/provider-response-XXXX.json`。简化校验日志为同目录 `ES2002[a-d].linkloom.json`。六条模型漏提只说明输出缺失，不能从日志证明是注意力、多任务处理或保守判断中的哪一种内部原因。

营销条目的具体拒绝条件在 `src/linkloom/semantic_ingestion/task_observation_discovery.py:284`：

```python
if (not proposal.description_parts or artifact.content.count(proposal.text) != 1
        or any(part not in proposal.text or (work_identity and proposal.text.count(part) != 1)
               for part in proposal.description_parts)):
    fail("TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS")
    continue
```

只读字符串核对：该候选task.text在块中为0次，description_parts中的工作片段在原文中为1次。失败点是完整引用不连续。模型的text把“marketing executive ,”与“you'll be just thinking ...”直接相接，标准转录两者之间实际有换行、A [993.880-994.020]: Yep .、B [1010.020-1019.310]:。两段原文本来都完整在同一个块，文本拼接发生在模型输出，不是分块截断。此检查早于负责人绑定和NON_ASSERTED_TASK分支（同文件:291），因此没有证据把拒绝归因于负责人或非确定性词语。

### 3条多出项

| 多出项（原始描述及中文） | 分块与原始模型输出 | 校验与保留原因 |
| --- | --- | --- |
| specify the final design in the final report；在最终报告中详细说明最终设计 | [347,420)完整在块0；provider-response-0017.json:32；PLAN，work_state=ASSERTED | 来源、描述和工作断言通过。与块4的“write the final report”（provider-response-0021.json:85）按gold是同一最终报告任务；两处原文位置不同，现有去重不做语义归并，因此两条都保留；裁判把前者判DUPLICATE |
| redraw this；当场重新绘图 | [17627,17670)完整在块1 [11917,23838)；provider-response-0018.json:78；ACTION，work_state=ASSERTED | 通过引用及ASSERTED检查。模型声明owner=B/SPEAKER，但普通文档无canonical.speaker，按字段绑定规则负责人会置空；缺负责人使观察为PARTIAL，不会删除工作。ES2002d.linkloom.json:11显示仍保留该任务 |
| write up our results on the board；在白板写下评估结果 | [23645,23801)完整在块1，也完整在块2 [23602,35305)；provider-response-0018.json:98；ACTION，work_state=ASSERTED | 块1提出并保留，块2的provider-response-0019.json为claims=[]。owner=C/SPEAKER同样不匹配普通文档的空speaker，但任务保留为PARTIAL。ES2002d.linkloom.json:17仍有该任务 |

后两项是会中完成的操作，不符合人工gold的“会后尚待完成”口径。现有抽取提示词的原文是：

> Preserve each meeting's original observation: a later plan revision or handover does not erase the earlier task statement.
>
> Only actually asserted work at that message time is ASSERTED.

位置：`src/linkloom/semantic_ingestion/extraction.py:414`及:417。中文意思是保留原始观察，判断“该消息发生时”是否断言了工作；没有要求把会中稍后完成的工作在会末移除。它与“最终待办清单”在口径上不同。

本轮V3的非确定性检查在 `task_observation_discovery.py:291`：

```python
if (proposal.work_state != 'ASSERTED' if field_profile else
        non_asserted(artifact.content, proposal.description_parts, start, end)):
    fail("NON_ASSERTED_TASK")
    continue
```

V3走field_profile分支，直接看模型给的work_state；这3条均为ASSERTED，因此通过。`task_discovery_veto.py`中的中英关键词扫描属于此表达式的else分支，本轮并未使用，不能把本次漏检归因于该扫描器。

去重实际是来源位置身份：`task_observation.py:109`把description证据的字符范围纳入ID；`scripts/eval_tasks.py:653`只合并绝对来源范围完全相同的工作。最终报告在会议开头与结尾两次提及时范围不同，所以不会被当成同一项合并。

### 日期为什么全部为空

这里有三组不同的“数量”：gold中8条有日期；模型共提出8个任务候选，其中1条引用被拒；最终保留7条预测。原始8个候选的deadline_text全是null，日期binding均为value=null、role=UNKNOWN、state=NOT_STATED；最终7条全部显示“未说明”。并非程序把模型已经抽出的8个日期删掉。

原始证据：provider-response-0004.json:30/:154（2条）、0009:89、0015:89、0017:81、0018:80/:97（2条）、0021:41。尤其0015:86的task.text已经包含“as soon as possible”（尽快），但:89仍输出deadline_text=null，说明日期不是因原文未进入输入而丢失。

本轮DATE OUTPUT CONTRACT明确写：

> Date-token syntax is YYYY-MM-DD, Y年M月D日/号, M月D日/号, Y年M月 or M月.

位置：`src/linkloom/semantic_ingestion/extraction.py:490`。允许ISO或中文年月日，不包含in thirty minutes、in the thirty minutes after lunch、next meeting、as soon as possible。`extraction.py:503`又在deadline_text schema中设置DATE_TOKEN_PATTERN；该正则在 `task_observation.py:19`。即使提出英文相对日期，`task_observation.py:281`也会走DEADLINE_INVALID。这是本轮相对日期需求与现有日期合约的明确不兼容。

实际数据流是：模型给null/NOT_STATED → `task_observation.py:347`跳过非ASSERTED日期绑定，保持初始空值 → `scripts/eval_tasks.py:660`把空值显示为“未说明”。没有已输出日期被进一步校验拒绝的日志。8条带日期gold中，6条连任务本身都漏掉；仅工业设计任务和“尽快发布纪要”匹配成功，这2条的日期也在模型输出时已为空。

### 是否主要面向中文结构化记录

实现约束明显偏结构化记录，日期格式明显偏中文/ISO；但提示词本身是英文，不能据此说整个系统只支持中文，也不能推断模型训练材料。

- 当前实际使用的V3提示词是英文（extraction.py:469），包含“Keep distinct tasks separate”等通用工作抽取要求。
- 中文/ISO日期限制是本轮实际走到的合约与schema（extraction.py:490/:503；task_observation.py:19/:281），直接影响这组英文相对日期。
- 本轮适配把每块作为manual_text送入（scripts/eval_tasks.py:637）。`document_text.py:169`只在内容包含以ISO日期开头的头部时进入带说话人的解析器；AMI的“A [秒数-秒数]:”格式落入普通文档，每块只有一个ordinal=0的canonical message（document_text.py:185），speaker=None（:33）。因此输入中虽能肉眼看到B/C，字段绑定拿不到规范化说话人。
- `task_observation.py:357`要求SPEAKER绑定满足canonical.speaker==value。普通文档的None无法匹配B或C，追加OWNER_EVIDENCE_INVALID；SOURCE_TEXT字面代词I却能满足quote包含value而被保留，所以人工抽查会发现“I未解析但曾获负责人正确分”。这也是基线B/C可按AMI映射，LinkLoom部分owner为空或只有I的具体原因。
- 历史非模型绑定分支含“负责人/owner/assigned to”“项目/project”等标签规则（task_observation.py:308/:319），体现结构化记录偏好；但本轮field_bindings不为空，走:417的_model_fields分支，没有走这些旧短语规则。不能用旧分支解释本轮具体拒绝。

负责人缺失本身不会删除任务：`task_observation.py:468`将有描述但缺字段/有字段问题的观察标为PARTIAL；`task_observation_discovery.py:254`返回PARTIAL；`scripts/eval_tasks.py:652`同时收集VALID和PARTIAL。因此上面6条模型未提任务，不是因为负责人不完整被整条过滤。

### 同一段多个任务是否只保留一个

没有“只留一个”的固定限制。提示词明确写“one concrete work statement per task. Keep distinct tasks separate”（extraction.py:413）；claims上限是20（:148），投影器逐条遍历claims（task_observation_discovery.py:263）。实证是ES2002d块1原始提出2条（0018.json:24），最终两条均保留（ES2002d.linkloom.json:11/:17）。

本次ES2002a同段3项分工只提出工业设计与营销，营销又被引用校验拒绝，剩1项；ES2002b同段含3项分工及纪要，只提出纪要1项。这是模型候选覆盖不足和一次引用拒绝形成的结果，不是一条上限。程序不会自动把原文里未被模型列成claim的其它动作补出来。

完整TaskObservation字段问题明细及请求体未在本轮落盘，现有日志只保留原始响应、最终预测及简化拒绝信息。上文OWNER_EVIDENCE_INVALID/PARTIAL路径是按原始字段和代码逐条件推导，未伪称为已经保存的字段错误日志。没有新增模型请求或修改任何代码。
