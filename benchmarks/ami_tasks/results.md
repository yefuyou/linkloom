# AMI 四场会议待办抽取正式评测

本表使用一次完整评测的预测；模型裁判结果尚待人工抽查，不是最终人工裁决。

被评测模型：gemini-3.8-flash；thinking_level=low；总输出上限 32,768 token（含思考）。裁判：deepseek-flash。
标准答案 SHA-256：`8df36c0dac65e755dbf0ac8a33dbe4be491b0718a260c604d1f5601d31b64855`；11 条人工 gold，其中 8 条有明确截止时间。

| 指标 | 基线 | LinkLoom |
| --- | ---: | ---: |
| 待办准确率（precision） | 11/11 (100.0%) | 4/7 (57.1%) |
| 待办召回率 | 11/11 (100.0%) | 4/11 (36.4%) |
| 负责人正确率（匹配任务） | 11/11 (100.0%) | 3/4 (75.0%) |
| 截止时间正确率（匹配且 gold 有日期） | 7/8 (87.5%) | 0/2 (0.0%) |
| 截止时间端到端正确率（全部有日期 gold） | 7/8 (87.5%) | 0/8 (0.0%) |
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

spot_check.csv 固定种子 20261029，按系统从预测判断及未匹配 gold 判断中各抽取向上取整的 20%，共 6/25 条；包含中文翻译、裁判理由和空白人工判断列。

## 范围与限制

两套系统只接收原始标准转录。全部预测完成后才读取 gold；摘要、候选和人工标注未进入被评测系统。基线整场一次（SDK 直调以容纳全文），LinkLoom 使用未修改的产品 task_observation 流程及 16 个重叠块；仅合并绝对原文位置相同的工作证据。分块上下文、普通文档发言人识别及英文相对日期规则均可能影响结果。

语料及全量模型输出保留在被忽略的 work/；Git 只提交必要的 gold 引用和抽查引用，不提交整场转录或 ZIP。AMI Meeting Corpus / AMI Project、AMI Consortium，CC BY 4.0：https://groups.inf.ed.ac.uk/ami/corpus/ ；https://creativecommons.org/licenses/by/4.0/ 。

## 人工复核重点（保留裁判初判）

- **日期等价**：ES2002c-c002，基线预测 `in thirty minutes`，gold 为 `next meeting`。裁判判为不等价，但同段原文明说 `the next meeting's gonna be in thirty minutes`（下次会议将在三十分钟后）。这可能与固定规则的等价表达条款冲突；表中仍保留原裁判 7/8，若人工确认等价则基线日期正确率应为 8/8。未改 gold 或裁判原始输出。
- **误报类别**：LinkLoom 的“在白板上写下结果”“重新绘制这个”都有原文依据，但属于会中完成操作。裁判将二者归为 UNSUPPORTED；保留该标签和 FP 计分，不能据此称其为无中生有。讨论误判 0 仅表示没有 DISCUSSION 标签，不表示没有误报。另有 1 条重复预测，因此多出共 3 条。
- **本地解析处理**：重复项的 gold_id 是重复关系指针，保存到 duplicate_of_gold_id 后计分 gold_id 为空，仍算 FP，没有重复得分；所有 28 份续跑响应已保存。最终计分/报告从已有输出离线重建，未增加模型请求。
- **来源校验限制**：ES2002a 的一个 LinkLoom 分块出现 TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS 拒绝，coverage_complete=false；其他块的结构覆盖标记不代表语义上已找全。
