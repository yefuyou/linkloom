# AMI 会议记录 → 待办抽取

## ES2003 最终评测（2026-10-30）

冻结版三方结果见 [最终报告](results_es2003_final.md) 和 [结构化结果](results_es2003_final.json)，逐条漏项和多出项均附中文翻译。全部预测只生成一次并先于 gold 内容读取完成；首次运行因 ES2003a / legacy 裁判将两条预测同时匹配到同一 gold 而中止，见 [首次中止报告](results_es2003_attempt1.md) 和 [无效响应原文](judge_es2003a_legacy_invalid_response.txt)。经用户明确授权，仅续跑裁判一次，反馈仅限当前会议的校验错误；未修改 v2、原评测脚本、评分规则或 gold，也未重新生成任何预测。

两次累计 39 次请求、按 API 用量及既有费率计算 $0.246469698，低于 80 次 / $1.50 上限。新增外围评测接线及相关离线测试 107 项通过。[抽查 CSV](spot_check_es2003_final.csv) 从各系统判断中向上取整抽 20%，共 7/31 条，保证第 3 条数据行为 ES2003a / legacy 的实际重判判断。人工抽查已完成，7 条全部同意，人工判断列已填写；离线原因追踪见最终报告的“诊断（不改变成绩）”章节，没有新增模型请求或改变成绩。

## 冻结版同步与验证

已把包含 PR #28/#30 的 origin/master 合入 task-eval，合并和本地改动恢复均无冲突。v2 提示词、规则、阈值已冻结，见 [冻结元数据](v2_frozen.json)。只做了一项通用期限角色修正：纯位置/载体/交付渠道不是期限，真实时间点、时长和事件时点保留；没有加入开发集专用句子规则。

对 ES2002 已有原始响应纯离线重建，清空 1 项非期限，11 条任务及 8 条明确期限的主指标不变；新模型请求 0、费用 $0。新的提示词未经在线重测。人工抽查 CSV 六条均已写“同意”。全量 pytest 3246 passed、31 skipped、0 failed、0 errors；剩余失败为无。历史旧基线失败不再代表本版状态。完整说明见 [冻结版结果](results_v2_phase1.md)。

离线复核入口（输出目录须不存在）：

```powershell
python scripts/eval_tasks.py rebuild-meeting-v2 --data work/ami_tasks_data_es2002 --gold benchmarks/ami_tasks/gold.jsonl --source work/ami_tasks_results_v2_phase1_final --out work/ami_tasks_v2_offline_check
```

该入口不读取 API 凭证或调用模型；gold、来源版本、参会者映射或窗口变动，以及非期限预测变化时，拒绝复用缓存裁判。未读取 ES2003，也未实施第二期。

## v2 第一期抽取器（2026-10-30）

显式配置 `task_extraction_mode=legacy|meeting_v2`，默认 legacy；新逻辑在 `src/linkloom/semantic_ingestion/meeting_tasks_v2.py`，不接产品 UI 或数据库。完整两期设计和本次已授权的第一期边界见 [设计/SPEC](task_extraction_v2_design.md)。Gemini 适配器只增加可信 Host 的 `max_input_bytes` 参数，默认仍为 32 KiB，允许显式设置至 256 KiB；本次 v2 Host 使用 192 KiB。旧请求对象不增加字段，默认线载荷及请求哈希不变。

v2 优先读取整场，超限才按完整发言使用大窗口；候选、会后范围、完成状态和交付物标签同一次返回。模型给出 canonical 发言句柄，Host 复制每段完整原文，分别核验位置、哈希和不可变来源版本。截止时间只有原样 raw_text/kind，resolved_at 恒为 null。简单合并仅针对已解析负责人相同且模型 deliverable_key 相同的候选，保留所有出处；未知负责人不合并。没有日期换算、独立状态/去重模型调用、持久化、UI 或人工审核绑定。

本轮只加载 ES2002a–d；没有读取 ES2003 转录、摘要或标注。通用提示词不包含开发集句子、角色或任务。ES2002 是开发集，结果不能证明泛化能力。结果和费用见 [三方对比](results_v2_phase1.md) / [结构化结果](results_v2_phase1.json)，新的裁判抽查见 [spot_check_v2_phase1.csv](spot_check_v2_phase1.csv)。legacy 使用上一轮已修复说话人接线的结果，基线也复用，二者不重跑。

本次 Gemini 每场只请求一次，共 4 次；首次 Host 校验只接受 speaker ID，误拒了同一可信 roster 中的 participant ID。已通过通用、精确且拒绝歧义的 ID 映射修正；4 个原请求及原模型响应完全复用，没有重新抽取或改写模型任务/引文。只为预测发生变化的三场补充 DeepSeek 裁判，未变化的一场裁判复用。累计 **11/60 次、$0.058264752/$1**，包含之前的 3 次失效裁判费用；Gemini 4 次、DeepSeek 7 次。全部运行材料保留在忽略的 work/ 下。

执行入口如下；本次已经完成，不要重复运行：

```powershell
python scripts/eval_tasks.py evaluate-linkloom --task-extraction-mode meeting_v2 --data work/ami_tasks_data_es2002 --gold benchmarks/ami_tasks/gold.jsonl --source work/ami_tasks_results_speaker_wiring --reference benchmarks/ami_tasks/results_speaker_wiring.json --out work/ami_tasks_results_v2_phase1 --live
```

下面保留原始评测的数据、署名和方法说明。

第一版评测只处理 ES2002a–d。ES2003a–d 已另行完成标注并冻结为 9 条标准答案，尚未运行评测，详见下方“ES2003a–d 标注与冻结”。英文人工 gold 已确认为 11 条并固定到 gold.jsonl。首个基线请求在附加引用校验处停止，已用 1 次请求 / $0.006915；用户随后明确确认修复后续跑，累计费用和次数沿用，gold 未变。完整评测已完成：累计 29 次请求、按 API 用量计费 $0.172675374。结果见 [results.md](results.md) / [results.json](results.json)，人工抽查见 [spot_check.csv](spot_check.csv)。分数是裁判初判，报告单列了日期等价和误报类别的人工复核疑点。不改产品实现，不接触笔记库，不创建预算账本、封存或预检流程。

## 数据确认（2026-10-29）

- 第一版选择 ES2002a–d，共 4 场，是同一遥控器设计 trial 的连续会议；实际解析已验证四场参与者 ID 一致。ES2003a–d 的后续标注与冻结见下方专节。[官方会议 ID 说明](https://groups.inf.ed.ac.uk/ami/corpus/meetingids.shtml)解释 a/b/c/d 的顺序。
- AMI corpus 及标注采用 **CC BY 4.0**。[官方许可证](https://groups.inf.ed.ac.uk/ami/corpus/license.shtml)。署名：**AMI Meeting Corpus，AMI Project / AMI Consortium**；[语料来源](https://groups.inf.ed.ac.uk/ami/corpus/)、[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。本项目对转录做了下述格式转换，并增加模型候选和中文辅助翻译，未将这些派生内容标为 AMI 官方标注。
- 人工摘要有 ABSTRACT、ACTIONS、DECISIONS、PROBLEMS/ISSUES，采用 **NITE/NXT XML**；摘要句子与抽取式摘要、对话行为、转录词存在链接。[官方标注说明](https://groups.inf.ed.ac.uk/ami/corpus/annotation.shtml)。ACTIONS 是概括性文字，不能当作完整的任务/负责人/截止时间 gold；空 action 区段也不证明转录没有待办。Individual Actions 是手势等行为标注，不是待办。
- 只需[人工标注 v1.6.2 ZIP](https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip)。[官方页面](https://groups.inf.ed.ac.uk/ami/download/)标为 22 MB；实测 **22,887,865 字节（21.83 MiB）**，Last-Modified 为 2017-04-10。下载已完成；SHA-256 见同目录 ami_public_manual_1.6.2.zip.sha256。无需自动标注、音视频、幻灯片。
- 从包内读取 words/<meeting>.<A–D>.words.xml 16 份、segments/<meeting>.<A–D>.segments.xml 16 份、abstractive/<meeting>.abssumm.xml 4 份、corpusResources/meetings.xml 1 份，共 37 份 XML。根据 XML 词引用重建发言，保留 speaker、相对秒数、原始 segment/word ID；同一发言的词以单空格连接，发言按词起始时间排序。非词语声音标签不进入文本；校验所有 w 词都已覆盖。**精确引用针对这一确定性转录文本**，原始 XML 在 ZIP 中可回查。
- 摘要实际为 actions/decisions 下带 nite:id 的 sentence；空 actions 中的 *NA* 不计为 action。metadata 使用 nxt_agent 表示 A–D，角色 PM/ID/UI/ME 转成英文角色名。缺文件、丢词、成员变化或格式不兼容直接报错，不用合成数据替代。这是英文情景会议，小样本结果仅代表所选 4 场。
- ZIP、整场转录、预标注候选和全量模型输出在被 .gitignore 忽略的 work/ 下，不提交语料。按用户本轮明确要求，Git 保存 gold.jsonl（11 条标准任务及必要原文引用）、抽查表、结果报告，以及下载/评测脚本、ZIP 校验值、说明、空模板和离线测试。

## 脚本结构与输入边界

scripts/eval_tasks.py 无参数或 plan 命令只打印方案，完全离线。只用标准库和现有项目依赖，不改安装配置。

| 命令 | 用途 | 真实模型请求 |
| --- | --- | --- |
| plan | 显示方案 | 0 |
| download | 下载官方 ZIP 到全新目录 | 0 |
| prepare | 本地 XML 转成 transcripts/ 与独立 labels/ | 0 |
| preannotate --live | DeepSeek 逐场列候选，并统一补全引用中文译文 | 4 + 1 |
| freeze-gold | keep=y 行应用人工改写后生成固定 JSONL | 0 |
| evaluate --live | 两套 Gemini 系统出预测，然后 DeepSeek 匹配评分 | 4 + 16 + 8，失败不自动重试 |

基线每场一次，把整场转录给 Gemini，输出任务、负责人、截止时间和引用。LinkLoom 直接调用现有 ProviderNeutralSemanticExtractor → TaskObservationDiscoveryService，启用当前产品使用的 TaskDiscoveryProfile.TASK_RECALL_V3；保留提示词、字段证据校验、拒绝逻辑、VALID/PARTIAL 输出及上限，并通过现有 GeminiProviderAdapter 请求模型。

代码已确认产品 GeminiProviderAdapter 只支持 **gemini-3.8-flash**。两套系统统一此型号、thinking_level=low、不设置 temperature，输出总上限 **32,768 token**。依据：[官方型号页](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)列出 low/medium/high 和 65,536 输出上限；[官方思考说明](https://ai.google.dev/gemini-api/docs/thinking)说明 max_output_tokens 同时包含思考和可见输出，过小可能只有思考而无 JSON。32,768 是留出思考和结构化输出余量的配置，不是已实测的思考需求；截断仍报错，不能当成空结果。两套使用相同 SDK、工具 schema 转换和生成参数，无自动重试。

产品适配器另有 32 KiB 输入上限，ES2002b–d 的全文超过该上限。因此基线复用其 SDK 客户端与 schema mapper，直接以同型号、low 和相同输出上限提交整场；不经过该输入大小限制。LinkLoom 的小块使用原适配器。不提高或修改产品上限。该对比仅覆盖任务抽取，不覆盖完整调查 agent 或生产 UI。

LinkLoom 按完整发言分块，每块最多 12,000 字符 / 20,000 UTF-8 字节，重叠两条发言，不截断全文。只合并重叠块内**绝对原文位置相同**的工作证据；语义重复交裁判判定，重复预测仍算 FP。基线保持整场一次。分块可能丢失长距离指代/跨块上下文，需随结果报告。超过 20 条 claim、字段校验拒绝等保留为 coverage/diagnostics，不宣称抽取完整。

两套被评测系统均只读取 transcripts/ 的会议 ID 与原文，不接收 AMI 摘要、候选 CSV、keep=n 行、人工改写或 gold。全部预测保存后才打开人工 CSV。DeepSeek 预标注和裁判可读对应资料；它们不是被评测系统。

转录只保留原始相对秒数，不伪造 ISO 会议日期。现有普通文档解析和日期词法对英文 speaker 指代、next meeting 等相对截止时间支持有限；本轮不翻译、不修产品规则，限制会真实影响字段正确率。

基线的评测字段是任务、负责人、截止时间；附加 quote 的拼写/空格不一致保留为 quote_validation 诊断，不重写、不删除该任务，也不直接判为 FP，由裁判用完整转录核对。gold、人工预标注引用和裁判引用仍要求精确来源子串。所有后续真实响应先写到被忽略的结果目录再校验，避免因本地校验错误丢失已付费输出；首次失败发生在这一保存机制补齐之前，没有可恢复的响应正文。修复仅影响评测输出处理，不改变产品实现和 gold。

## 预标注与人工 gold

使用 deepseek-flash（官方当前映射 DeepSeek-V4.1-Flash-1017）。候选包含会议 ID、精确连续原文、英文任务、负责人、截止时间、对应 AMI action 句子 ID 及原文（无对应则空）；未知负责人/日期为“未说明”。DeepSeek 选择起止发言 ID，脚本直接复制原文，记录其字符偏移；不依赖模型重写转录。另有“引用原文的中文翻译”“任务的中文翻译”两列；引用按唯一英文片段合并做一次完整翻译。**中文只辅助核对，gold 以英文原文为准，翻译不进入被评测系统或裁判 gold。**

candidates.template.csv 只有表头。生成的 CSV 另有 keep、修改后的任务、修改后的负责人、修改后的截止时间、备注、候选 ID、引用起止位置。人工另存 gold CSV：每行填 y/n；修改列留空表示沿用原字段，要清空负责人/日期则填“未说明”；修改后的任务仍用英文。

人工必须逐场完整阅读转录，补充 DeepSeek 漏项；新行使用唯一 candidate_id，填写真实引用并置 keep=y，没有 AMI action 可空。复合动作拆成独立行，本场内去重。**只审核候选而不补漏，得到的是候选池召回率，不能当作完整待办召回率。** 官方摘要只辅助核对。

本版实际标注流程：**DeepSeek 预标注 16 条 → 人工依据标注规则逐条核对、改写字段、合并重复、排除会中已完成事项及未接受建议 → AMI 人工摘要覆盖检查（7/7 条有效 action 都有候选对应）→ 由另一个模型（本会话 Codex，与 DeepSeek/Gemini 不同）通读四场全文补查（无新增）→ 人工最终确认 11 条**。keep=n 的 5 条不进入 gold。ES2002a/b/c/d 分别为 3/4/2/2 条，其中 8 条明确给出截止时间。

freeze-gold 对任务、负责人、截止时间逐列使用“修改后的字段非空则采用，否则使用原字段”，保留 meeting_id、quote、notes，并保留原候选 ID 为 gold_id。JSONL 不包含中文辅助翻译、keep=n 行或未采纳候选。冻结后的标准答案 SHA-256 为 `8df36c0dac65e755dbf0ac8a33dbe4be491b0718a260c604d1f5601d31b64855`；只读使用，不在结果出来后调整。原文引用均与标准转录精确匹配。

## ES2003a–d 标注与冻结（2026-10-30）

标注流程：**DeepSeek 预标注 11 条 → 人工逐条核对、改写字段与合并重复 → 上下文核实（期限、指代对象）→ 另一个模型 Codex 通读四场全文补查 3 条 → 人工最终确认 9 条**。主候选表保留 8 条，补查表保留 1 条；其余条目不进入标准答案。AMI 人工摘要的 3 条有效 action 仅辅助核对，不作为完整 gold。

最终人工核对将 ES2003a-m001 的用户需求确认合入 ES2003a-c002；ES2003b-c001 按上下文指向用户界面设计师 A（没有点名，保留推断说明），与工业设计师 D 自己确认的 ES2003b-c003 分开保留；ES2003b-c002 通过点名与连续回应确认负责人为营销主管。ES2003c-c001 的原型工作由两位设计师共同承担；c001、c002 未明确说出完成期限，最终均为“未说明”。

补查项 ES2003c-m001 因本条无明确执行人，由人工决定排除；ES2003c-m002 保留，任务修正为 `Start writing up the project documentation`，负责人 `Project Manager`，截止时间“未说明”。这是一条候选的人工取舍，不改变上文允许缺失负责人/日期的通用标注口径。

冻结文件为 [gold_es2003.jsonl](gold_es2003.jsonl)，ES2003a/b/c/d 分别为 **2/3/3/1 条**，共 **9 条**；其中 1 条明确给出期限，即 ES2003a-c002 的 `next meeting (in thirty minutes)`。格式与 ES2002 的 gold.jsonl 一致：仅合并两份 CSV 中 keep=y 的条目，任务、负责人和截止时间逐列优先采用非空的“修改后的字段”，否则使用原字段；保留原候选 ID 为 gold_id，以及 meeting_id、精确原文 quote 和备注 notes，不包含中文辅助列或 keep=n 行。全部 9 条引用及字符偏移已与准备好的标准转录核对。

SHA-256：`fdb653c84c48cfe9041112352614c80fcf0a79e18777ad70d7314150ecaec7b8`。标准答案冻结后只读使用，不在评测结果出来后调整。转录原文、中间 CSV、AMI 原始摘要和模型响应留在被忽略的 `work/ami_tasks_annotation_es2003/`，不提交。本轮只冻结标注，未运行评测，未新增模型请求。

## ES2004a–d 标注与冻结（2026-10-30）

标注流程：**DeepSeek 预标注 11 条 → 人工逐条核对与字段修正 → 上下文核实（营销主管收到的说明书研究答复、公司标志请求的指代对象、PowerPoint 安排及会议结尾分工）→ 另一个模型 Codex 通读四场全文补查 2 条 → 人工最终确认 6 条**。最终人工 CSV 的 keep=y 已核对为指定的 5 条：ES2004a-c001、ES2004b-c001、ES2004b-c002、ES2004d-c001、ES2004d-c002；补查表另保留 ES2004b-m001。

ES2004b-m001 根据项目经理 B 的“your field”与工业设计师 C 的接受回应，保留为 `Look into rechargeable batteries with a docking station`，负责人 `Industrial Designer`，截止时间“未说明”；后续会议的回顾仅作为上下文补强，不重复计为新任务。ES2004a-m001 的下一次 PowerPoint 汇报由人工判为会议流程、不是会后交付物，keep=n。其他未采纳候选按人工判断排除；AMI 人工摘要仅辅助核对，不代替完整转录与人工取舍，也不据此改写通用标注规则。

冻结文件为 [gold_es2004.jsonl](gold_es2004.jsonl)，ES2004a/b/c/d 分别为 **1/3/0/2 条**，共 **6 条**，其中 2 条给出明确期限：a-c001 的 `next meeting (in half an hour)` 与 b-c001 的 `before the next meeting`。d-c001 引文中的“五十分钟”指赶进度，未作为问卷期限；其最终期限保留“未说明”。

格式与已有 gold 文件一致：仅合并两份 CSV 中 keep=y 的条目，任务、负责人和截止时间逐列优先采用非空的“修改后的字段”，否则使用原字段；保留原候选 ID 为 gold_id，以及 meeting_id、精确原文 quote 和人工备注 notes，不包含中文辅助列或 keep=n 行。全部 6 条引用及字符偏移已与准备好的标准转录核对。

SHA-256：`d7b275bef8065bc8deb679529d95386d2b2e32cfaf00887e4f7e840649d51e3b`。冻结后只读使用，不在评测结果出来后调整。转录原文、AMI 原始摘要、中间 CSV 和模型响应继续留在被忽略的 `work/ami_tasks_annotation_es2004/`，不提交。本轮仅冻结标准答案，未运行评测，未新增模型请求。

## 固定匹配规则：meeting-task-match/v2

规则原文在脚本 MATCH_RULES，裁判统一使用：

1. 仅在同一场会议内匹配。待办须为截至本场结束明确采纳、指派或承诺、仍待会后完成的具体工作；缺负责人/日期仍可算。依据人工标注口径，排除未采纳建议、条件设想、问题、取消/拒绝、历史已完成事项、会中已完成的演示/白板/列举请求、纯需求和没有执行义务的产品决策，以及寒暄/会议操作。
2. 动作、对象、范围、交付物一致才匹配；允许同义改写，仅主题相同不算。笼统复合预测不强行匹配狭窄子任务。任务匹配不依赖负责人/日期，这两项独立评分。
3. 一对一：先最大化有效匹配数，再选语义和原文证据最强者，仍同分按 prediction_id、gold_id 字典序选。重复预测不重复得分，多余条目算 FP；一个预测不能覆盖多条 gold。
4. 负责人别名须能由本场原文/speaker 元数据证明，不凭岗位常识推断，不把汇报者当负责人。gold 写角色（如 Project Manager/项目经理）时，输出该角色或对应人名均正确。截止时间接受等价日期或等价相对表述，保留精度；“下次会前”不补日历日期，会议时间不算截止时间。**只对 gold 明确给出截止时间的条目计分**；系统输出“未说明/无法确定”等单列为弃答，不算编造，也不算正确。gold 未给日期时不计截止时间正确率。
5. 每条预测返回 MATCH / DISCUSSION / DUPLICATE / UNSUPPORTED / WRONG_TASK、匹配 gold_id、负责人/日期正确与否、理由和精确原文。程序拒绝遗漏预测、多对一、未知 ID、非原文引用、非法字段加分；无效输出停止，不自动修补/重试。
6. 隐去系统名，使用固定随机种子的无含义预测 ID，逐场逐系统交 DeepSeek。裁判同时返回中文任务/引用翻译及中文理由；翻译只用于核验。保留全部判断，并按系统以种子 20261029 随机抽取向上取整的 **20%** 到 spot_check.csv。抽样总体包括预测判断和由匹配结果推导的未匹配 gold（FN），附任务、字段、理由、引用和空白“同意 / 不同意”列；报告写明实际抽样分子和分母。模型裁判仍可能出错，抽查结果未回填前不能声称最终人工准确率。

正式裁判末份响应给 DUPLICATE 保留了其对应 gold_id，语义是“重复于已匹配任务”，不代表第二次匹配。本地解析把该指针保留为 duplicate_of_gold_id，将计分 gold_id 置空，仍计 FP，原始响应不改。全部模型请求结束后仅从已保存预测/裁判输出重新生成计分和报告，没有再次调用模型。

## 指标

- “准确率”按 precision = TP/(TP+FP)，recall = TP/(TP+FN)，另报 F1 和 TP/FP/FN。开放抽取没有合理 TN，不报分类 accuracy。
- 负责人正确率在匹配任务上算，报告已说明/未说明分母，并报端到端字段召回。截止时间报告“匹配且 gold 有日期”的正确率，以及“全部 gold 有日期”的端到端正确率，后者包括漏检；单报所有预测中的日期弃答数及匹配的有日期任务中的弃答数，弃答不给正确分，不当作编造。
- 讨论误判数是 DISCUSSION 类 FP；幻觉、重复等也算 FP，但不都叫讨论误判。
- 输出逐场和总体 micro 指标；零分母为 null。CrossMeetingTracker 仅有 Protocol，无实现或调用；逐场重置，不用前会推断后会。

## 请求数和成本

价格查于 2026-10-29，单位为美元/百万 token：[Gemini 官方](https://ai.google.dev/gemini-api/docs/pricing)3.8 Flash 标准价至 2026-12-31 为输入 0.75、输出（含思考）3.75；[DeepSeek 官方](https://api-docs.deepseek.com/quick_start/pricing/)预算上界按高峰/缓存未命中输入 0.30、输出 1.20，关闭 thinking；实耗按 API 返回缓存命中/未命中 token 和请求时段计算。DeepSeek 低峰命中/未命中/输出分别为 0.003/0.15/0.60。费用是实际 API 用量乘公布费率，不是供应商账户发票；高峰时段或节假日费率变化应重新核对。

- 本版预标注 **4 次候选 + 1 次完整引用翻译**；硬上限 **10 次且 $0.20**。此次实际共 5 次，用量计费 **$0.014960262**，保守费用预留累计 $0.1599378；没有调用 Gemini 或裁判。
- 完整评测 = **4 次基线 + 16 次分块（2/4/5/5）+ 8 次裁判，28 次**；加上经用户确认前的首个失败请求，累计 **29 次**。正式限 **200 次且 $2.00**，不得沿用旧版 Flash-Lite 成本估计。32,768 是 Gemini 思考及可见输出共用上限，并非每次必然消耗；根据实际 token 结算计数，若下一次最大费用可能突破剩余额度则停止，不擅自提额。裁判同一请求附带报告/抽查所需中文内容，不另重跑抽取。
- 本次 Gemini 21 次 / $0.158394750（含首次失败），DeepSeek 8 次 / $0.014280624；合计 **29 次 / $0.172675374**，不包含预标注的 $0.014960262。抽查总体为 11 条基线预测 + 7 条 LinkLoom 预测 + 7 条 LinkLoom 漏检判断，共 25 条；按系统 20% 向上取整抽取 3+3=6 条（6/25=24%，小样本取整），没有人为替换随机样本。
- 每进程两个简单内存计数器：请求数、已消费/未知结果的费用上界。HTTP 前按序列化输入 UTF-8 字节数+1024（保守高估 token）和最大输出 token 检查并预留；拿到可靠 usage 后按实际用量结算，释放未使用的预留（Gemini 包含思考 token）。失败/未知用量仍占用预留，不自动重试。上界始终按高峰价检查，固定总额不变，无 CLI 提额参数。此次 5 次执行在改为按 usage 结算之前完成，summary.review.json 中保留的是当时累计最大预留，实际费用另列。
- 不自动恢复或重复启动。失败如需重跑，先看终端/结果中的计数并汇报累计消耗。计数器约束本次单进程，不能控制其他进程或账户消费；没有持久化预算账本。

## 使用顺序

输出目录须不存在，脚本不覆盖已有文件。相对 work/ 路径仅是示例。API key 只从 GEMINI_API_KEY / DEEPSEEK_API_KEY 环境变量读取，不读写 .env，不打印 key。

```powershell
python scripts/eval_tasks.py plan
python scripts/eval_tasks.py download --out work/ami_tasks_download
python scripts/eval_tasks.py prepare --archive work/ami_tasks_download/ami_public_manual_1.6.2.zip --out work/ami_tasks_data_es2002
python scripts/eval_tasks.py preannotate --data work/ami_tasks_data_es2002 --out work/ami_tasks_annotation --live
# 人工审核并完整补漏、最终确认之后（目标文件已存在时不会覆盖）：
python scripts/eval_tasks.py freeze-gold --csv work/ami_tasks_annotation/candidates.review.csv --out benchmarks/ami_tasks/gold.jsonl
python scripts/eval_tasks.py evaluate --data work/ami_tasks_data_es2002 --gold benchmarks/ami_tasks/gold.jsonl --out work/ami_tasks_results --live
# 只用已有预测/裁判生成报告，不访问模型（实际完成的结果位于 work/ami_tasks_results_final）：
python scripts/eval_tasks.py score-saved --data work/ami_tasks_data_es2002 --gold benchmarks/ami_tasks/gold.jsonl --source work/ami_tasks_results_continued --out work/ami_tasks_results_final --prior-failure work/ami_tasks_results/failure.json
```

此次人工核对入口为 work/ami_tasks_annotation/candidates.review.csv，16 条英文候选；它已用用户提交的人工核对版本替换。未来完整运行 preannotate 会直接生成含完整翻译的 candidates.csv。不要重复运行已完成的付费步骤；本次正式结果目录只允许创建一次，失败保留已完成预测和已知费用，不自动重试或重启。

| 会议 | 转录词数 | 转录字符数（含 speaker/秒数） | AMI actions | 候选数 |
| --- | ---: | ---: | ---: | ---: |
| ES2002a | 3,105 | 19,183 | 3 | 3 |
| ES2002b | 8,110 | 47,036 | 3 | 4 |
| ES2002c | 8,757 | 51,929 | 1 | 3 |
| ES2002d | 9,075 | 58,506 | 0 | 6 |

离线验证覆盖 XML 转换、引用、输入隔离、现有抽取接线、匹配约束、指标及费用停止条件。小 XML/模型桩仅用于单元测试，不是评测语料；模型候选仍待人工审核，不能据此宣称抽取效果。
