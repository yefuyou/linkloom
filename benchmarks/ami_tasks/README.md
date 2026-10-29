# AMI 会议记录 → 待办抽取

第一版只处理 ES2002a–d。ES2003a–d 保留为后续计划，当前没有转换、预标注或评分。正式评测须等英文人工 gold 完成后另行运行；当前只完成下载、转换及 DeepSeek 预标注。不改产品实现，不接触笔记库，不创建预算账本、封存或预检流程。

## 数据确认（2026-10-29）

- 第一版选择 ES2002a–d，共 4 场，是同一遥控器设计 trial 的连续会议；实际解析已验证四场参与者 ID 一致。ES2003a–d 暂不处理。[官方会议 ID 说明](https://groups.inf.ed.ac.uk/ami/corpus/meetingids.shtml)解释 a/b/c/d 的顺序。
- AMI corpus 及标注采用 **CC BY 4.0**。[官方许可证](https://groups.inf.ed.ac.uk/ami/corpus/license.shtml)。署名：**AMI Meeting Corpus，AMI Project / AMI Consortium**；[语料来源](https://groups.inf.ed.ac.uk/ami/corpus/)、[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。本项目对转录做了下述格式转换，并增加模型候选和中文辅助翻译，未将这些派生内容标为 AMI 官方标注。
- 人工摘要有 ABSTRACT、ACTIONS、DECISIONS、PROBLEMS/ISSUES，采用 **NITE/NXT XML**；摘要句子与抽取式摘要、对话行为、转录词存在链接。[官方标注说明](https://groups.inf.ed.ac.uk/ami/corpus/annotation.shtml)。ACTIONS 是概括性文字，不能当作完整的任务/负责人/截止时间 gold；空 action 区段也不证明转录没有待办。Individual Actions 是手势等行为标注，不是待办。
- 只需[人工标注 v1.6.2 ZIP](https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip)。[官方页面](https://groups.inf.ed.ac.uk/ami/download/)标为 22 MB；实测 **22,887,865 字节（21.83 MiB）**，Last-Modified 为 2017-04-10。下载已完成；SHA-256 见同目录 ami_public_manual_1.6.2.zip.sha256。无需自动标注、音视频、幻灯片。
- 从包内读取 words/<meeting>.<A–D>.words.xml 16 份、segments/<meeting>.<A–D>.segments.xml 16 份、abstractive/<meeting>.abssumm.xml 4 份、corpusResources/meetings.xml 1 份，共 37 份 XML。根据 XML 词引用重建发言，保留 speaker、相对秒数、原始 segment/word ID；同一发言的词以单空格连接，发言按词起始时间排序。非词语声音标签不进入文本；校验所有 w 词都已覆盖。**精确引用针对这一确定性转录文本**，原始 XML 在 ZIP 中可回查。
- 摘要实际为 actions/decisions 下带 nite:id 的 sentence；空 actions 中的 *NA* 不计为 action。metadata 使用 nxt_agent 表示 A–D，角色 PM/ID/UI/ME 转成英文角色名。缺文件、丢词、成员变化或格式不兼容直接报错，不用合成数据替代。这是英文情景会议，小样本结果仅代表所选 4 场。
- ZIP、转录、候选、中文翻译及未来人工 gold 都只在被 .gitignore 忽略的 work/ 下，不提交语料。Git 只保存下载/评测脚本、ZIP 校验值、说明、空模板及离线测试。

## 脚本结构与输入边界

scripts/eval_tasks.py 无参数或 plan 命令只打印方案，完全离线。只用标准库和现有项目依赖，不改安装配置。

| 命令 | 用途 | 真实模型请求 |
| --- | --- | --- |
| plan | 显示方案 | 0 |
| download | 下载官方 ZIP 到全新目录 | 0 |
| prepare | 本地 XML 转成 transcripts/ 与独立 labels/ | 0 |
| preannotate --live | DeepSeek 逐场列候选，并统一补全引用中文译文 | 4 + 1 |
| evaluate --live | 两套 Gemini 系统出预测，然后 DeepSeek 匹配评分 | 4 + K + 至多 8，本轮未运行 |

基线每场一次，把整场转录给 Gemini，输出任务、负责人、截止时间和引用。LinkLoom 直接调用现有 ProviderNeutralSemanticExtractor → TaskObservationDiscoveryService，启用当前产品使用的 TaskDiscoveryProfile.TASK_RECALL_V3；保留提示词、字段证据校验、拒绝逻辑、VALID/PARTIAL 输出及上限，并通过现有 GeminiProviderAdapter 请求模型。

代码已确认产品 GeminiProviderAdapter 只支持 **gemini-3.8-flash**。两套系统统一此型号、thinking_level=low、不设置 temperature，输出总上限 **32,768 token**。依据：[官方型号页](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)列出 low/medium/high 和 65,536 输出上限；[官方思考说明](https://ai.google.dev/gemini-api/docs/thinking)说明 max_output_tokens 同时包含思考和可见输出，过小可能只有思考而无 JSON。32,768 是留出思考和结构化输出余量的配置，不是已实测的思考需求；截断仍报错，不能当成空结果。两套使用相同 SDK、工具 schema 转换和生成参数，无自动重试。

产品适配器另有 32 KiB 输入上限，ES2002b–d 的全文超过该上限。因此基线复用其 SDK 客户端与 schema mapper，直接以同型号、low 和相同输出上限提交整场；不经过该输入大小限制。LinkLoom 的小块使用原适配器。不提高或修改产品上限。该对比仅覆盖任务抽取，不覆盖完整调查 agent 或生产 UI。

LinkLoom 按完整发言分块，每块最多 12,000 字符 / 20,000 UTF-8 字节，重叠两条发言，不截断全文。只合并重叠块内**绝对原文位置相同**的工作证据；语义重复交裁判判定，重复预测仍算 FP。基线保持整场一次。分块可能丢失长距离指代/跨块上下文，需随结果报告。超过 20 条 claim、字段校验拒绝等保留为 coverage/diagnostics，不宣称抽取完整。

两套被评测系统均只读取 transcripts/ 的会议 ID 与原文，不接收 AMI 摘要、候选 CSV、keep=n 行、人工改写或 gold。全部预测保存后才打开人工 CSV。DeepSeek 预标注和裁判可读对应资料；它们不是被评测系统。

转录只保留原始相对秒数，不伪造 ISO 会议日期。现有普通文档解析和日期词法对英文 speaker 指代、next meeting 等相对截止时间支持有限；本轮不翻译、不修产品规则，限制会真实影响字段正确率。

## 预标注与人工 gold

使用 deepseek-flash（官方当前映射 DeepSeek-V4.1-Flash-1017）。候选包含会议 ID、精确连续原文、英文任务、负责人、截止时间、对应 AMI action 句子 ID 及原文（无对应则空）；未知负责人/日期为“未说明”。DeepSeek 选择起止发言 ID，脚本直接复制原文，记录其字符偏移；不依赖模型重写转录。另有“引用原文的中文翻译”“任务的中文翻译”两列；引用按唯一英文片段合并做一次完整翻译。**中文只辅助核对，gold 以英文原文为准，翻译不进入被评测系统或裁判 gold。**

candidates.template.csv 只有表头。生成的 CSV 另有 keep、修改后的任务、修改后的负责人、修改后的截止时间、备注、候选 ID、引用起止位置。人工另存 gold CSV：每行填 y/n；修改列留空表示沿用原字段，要清空负责人/日期则填“未说明”；修改后的任务仍用英文。

人工必须逐场完整阅读转录，补充 DeepSeek 漏项；新行使用唯一 candidate_id，填写真实引用并置 keep=y，没有 AMI action 可空。复合动作拆成独立行，本场内去重。**只审核候选而不补漏，得到的是候选池召回率，不能当作完整待办召回率。** 官方摘要只辅助核对。

## 固定匹配规则：meeting-task-match/v2

规则原文在脚本 MATCH_RULES，裁判统一使用：

1. 仅在同一场会议内匹配。待办须为截至本场结束明确采纳、指派或承诺的具体未来工作；缺负责人/日期仍可算。排除未采纳建议、条件设想、问题、取消/拒绝、历史已完成事项、纯需求及没有执行义务的产品决策。实质性会中工作指令可算，寒暄/会议操作不算。
2. 动作、对象、范围、交付物一致才匹配；允许同义改写，仅主题相同不算。笼统复合预测不强行匹配狭窄子任务。任务匹配不依赖负责人/日期，这两项独立评分。
3. 一对一：先最大化有效匹配数，再选语义和原文证据最强者，仍同分按 prediction_id、gold_id 字典序选。重复预测不重复得分，多余条目算 FP；一个预测不能覆盖多条 gold。
4. 负责人别名须能由本场原文/speaker 元数据证明，不凭岗位常识推断，不把汇报者当负责人。gold 写角色（如 Project Manager/项目经理）时，输出该角色或对应人名均正确。截止时间接受等价日期或等价相对表述，保留精度；“下次会前”不补日历日期，会议时间不算截止时间。**只对 gold 明确给出截止时间的条目计分**；系统输出“未说明/无法确定”等单列为弃答，不算编造，也不算正确。gold 未给日期时不计截止时间正确率。
5. 每条预测返回 MATCH / DISCUSSION / DUPLICATE / UNSUPPORTED / WRONG_TASK、匹配 gold_id、负责人/日期正确与否、理由和精确原文。程序拒绝遗漏预测、多对一、未知 ID、非原文引用、非法字段加分；无效输出停止，不自动修补/重试。
6. 隐去系统名，使用固定随机种子的无含义预测 ID，逐场逐系统交 DeepSeek。保留全部判断，并按系统随机抽取向上取整的 **20%** 到 audit_20pct.csv。抽样总体包括预测判断和未匹配 gold（FN），附任务、字段、理由、引用、人工核验列。模型裁判仍可能出错。

## 指标

- “准确率”按 precision = TP/(TP+FP)，recall = TP/(TP+FN)，另报 F1 和 TP/FP/FN。开放抽取没有合理 TN，不报分类 accuracy。
- 负责人正确率在匹配任务上算，报告已说明/未说明分母，并报端到端字段召回。截止时间报告“匹配且 gold 有日期”的正确率，以及“全部 gold 有日期”的端到端正确率，后者包括漏检；单报所有预测中的日期弃答数及匹配的有日期任务中的弃答数，弃答不给正确分，不当作编造。
- 讨论误判数是 DISCUSSION 类 FP；幻觉、重复等也算 FP，但不都叫讨论误判。
- 输出逐场和总体 micro 指标；零分母为 null。CrossMeetingTracker 仅有 Protocol，无实现或调用；逐场重置，不用前会推断后会。

## 请求数和成本

价格查于 2026-10-29，单位为美元/百万 token：[Gemini 官方](https://ai.google.dev/gemini-api/docs/pricing)3.8 Flash 标准价至 2026-12-31 为输入 0.75、输出（含思考）3.75；[DeepSeek 官方](https://api-docs.deepseek.com/quick_start/pricing/)预算上界按高峰/缓存未命中输入 0.30、输出 1.20，关闭 thinking；实耗按 API 返回缓存命中/未命中 token 和请求时段计算。DeepSeek 低峰命中/未命中/输出分别为 0.003/0.15/0.60。费用是实际 API 用量乘公布费率，不是供应商账户发票；高峰时段或节假日费率变化应重新核对。

- 本版预标注 **4 次候选 + 1 次完整引用翻译**；硬上限 **10 次且 $0.20**。此次实际共 5 次，用量计费 **$0.014960262**，保守费用预留累计 $0.1599378；没有调用 Gemini 或裁判。
- 正式 = **4 次基线 + 16 次分块（2/4/5/5）+ 至多 8 次裁判，最多 28 次**。本轮未执行，正式仍限 **200 次且 $2.00**，不得沿用旧版 Flash-Lite 成本估计。32,768 是总输出上限，并非每次必然消耗；根据实际 token 结算计数，若下一次最大费用可能突破剩余额度则停止，不擅自提额。
- 每进程两个简单内存计数器：请求数、已消费/未知结果的费用上界。HTTP 前按序列化输入 UTF-8 字节数+1024（保守高估 token）和最大输出 token 检查并预留；拿到可靠 usage 后按实际用量结算，释放未使用的预留（Gemini 包含思考 token）。失败/未知用量仍占用预留，不自动重试。上界始终按高峰价检查，固定总额不变，无 CLI 提额参数。此次 5 次执行在改为按 usage 结算之前完成，summary.review.json 中保留的是当时累计最大预留，实际费用另列。
- 不自动恢复或重复启动。失败如需重跑，先看终端/结果中的计数并汇报累计消耗。计数器约束本次单进程，不能控制其他进程或账户消费；没有持久化预算账本。

## 使用顺序

输出目录须不存在，脚本不覆盖已有文件。相对 work/ 路径仅是示例。API key 只从 GEMINI_API_KEY / DEEPSEEK_API_KEY 环境变量读取，不读写 .env，不打印 key。

```powershell
python scripts/eval_tasks.py plan
python scripts/eval_tasks.py download --out work/ami_tasks_download
python scripts/eval_tasks.py prepare --archive work/ami_tasks_download/ami_public_manual_1.6.2.zip --out work/ami_tasks_data_es2002
python scripts/eval_tasks.py preannotate --data work/ami_tasks_data_es2002 --out work/ami_tasks_annotation --live
# 人工审核并完整补漏，另存 gold.csv 之后：
python scripts/eval_tasks.py evaluate --data work/ami_tasks_data_es2002 --gold work/ami_tasks_annotation/gold.csv --out work/ami_tasks_results --live
```

此次人工核对入口为 work/ami_tasks_annotation/candidates.review.csv，16 条英文候选；该文件相对初始 candidates.csv 只补全了中文引用翻译。未来完整运行 preannotate 会直接生成含完整翻译的 candidates.csv。不要重复运行已完成的付费步骤；恢复/补充调用须沿用本轮已有请求和费用计数。

| 会议 | 转录词数 | 转录字符数（含 speaker/秒数） | AMI actions | 候选数 |
| --- | ---: | ---: | ---: | ---: |
| ES2002a | 3,105 | 19,183 | 3 | 3 |
| ES2002b | 8,110 | 47,036 | 3 | 4 |
| ES2002c | 8,757 | 51,929 | 1 | 3 |
| ES2002d | 9,075 | 58,506 | 0 | 6 |

离线验证覆盖 XML 转换、引用、输入隔离、现有抽取接线、匹配约束、指标及费用停止条件。小 XML/模型桩仅用于单元测试，不是评测语料；模型候选仍待人工审核，不能据此宣称抽取效果。
