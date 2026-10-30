# ES2004 冻结版最终评测

四场会议、6 条冻结标准答案，其中 2 条明确给出截止时间。全部预测只生成一次，全部保存之后才解析标准答案。人工抽查共 10 条，全部同意；完全一致率为 10/10（100%）。
本轮未修改产品代码、评测脚本、提示词、规则、阈值或 gold；仅在临时调用中补传报告所需的 windows 字段，并按授权续跑裁判。

## 对比结果

| 指标 | 基线 | legacy（接线修复后） | v2（冻结） | v3 全部（HIGH+LOW） | v3 仅 HIGH |
| --- | ---: | ---: | ---: | ---: | ---: |
| 待办准确率（TP / 预测数） | 3/7（42.9%） | 0/1（0.0%） | 2/6（33.3%） | 2/6（33.3%） | 1/1（100.0%） |
| 待办召回率（TP / 全部 gold） | 3/6（50.0%） | 0/6（0.0%） | 2/6（33.3%） | 2/6（33.3%） | 1/6（16.7%） |
| 负责人正确率（匹配任务） | 1/3（33.3%） | 0/0（不适用） | 2/2（100.0%） | 2/2（100.0%） | 1/1（100.0%） |
| 截止时间正确率（匹配且 gold 明确） | 2/2（100.0%） | 0/0（不适用） | 2/2（100.0%） | 2/2（100.0%） | 1/1（100.0%） |
| 截止时间端到端正确率（全部明确期限 gold） | 2/2（100.0%） | 0/2（0.0%） | 2/2（100.0%） | 2/2（100.0%） | 1/2（50.0%） |
| 讨论误判数 / 预测数 | 1/7（14.3%） | 0/1（0.0%） | 0/6（0.0%） | 2/6（33.3%） | 0/1（0.0%） |

每格先列分子／分母；0/0 表示不适用，不记为 100%。负责人只在任务匹配后计分。截止时间主指标只计匹配且 gold 明确期限的任务，同时提供以全部 2 条明确期限 gold 为分母的端到端指标。未说明／无法确定的期限是弃答，不算正确，也不算编造。
v3 仅 HIGH 的准确率为 1/1，但召回率为 1/6；其余 5 条 v3 预测均为 LOW。HIGH+LOW 与仅 HIGH 独立裁判；本轮最终没有使用派生口径。

| 系统 | TP | FP | FN | 截止时间弃答 / 预测数 | 匹配且有期限 gold 上弃答 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 基线 | 3 | 4 | 3 | 3/7 | 0/2 |
| legacy（接线修复后） | 0 | 1 | 6 | 1/1 | 0/0 |
| v2（冻结） | 2 | 4 | 4 | 2/6 | 0/2 |
| v3 全部（HIGH+LOW） | 2 | 4 | 4 | 3/6 | 0/2 |
| v3 仅 HIGH | 1 | 0 | 5 | 0/1 | 0/1 |

## 评分与模型配置

沿用冻结的 meeting-task-match/v2：按任务动作、对象、范围和交付物匹配，允许同义表述；每条预测最多匹配一条 gold，每条 gold 最多被匹配一次。任务匹配不受负责人或期限字段影响，字段另行计分。只计会议结束后仍需执行的具体工作。
负责人中的 I、you、we 等代词若未解析为具体人或角色，一律错误；说话人编号按本场 AMI 元数据映射为角色后比较。角色及对应人名／编号可等价，明确共同负责的团队也可匹配。
截止时间保留原文精度；明确指向同一次下次会议的相对时长与事件表述可等价，不借会议日期或技术时间锚推算日历日期。gold 未明确期限时不计期限正确率。
四套系统统一使用 `gemini-3.8-flash`，thinking_level=`low`，输出上限 **32,768 token**（思考和 JSON 共用，沿用此前路由修复后的固定配置）；裁判为 `deepseek-flash`，不启用思考。
基线整场一次读取；legacy 使用已修复的发言格式和角色接线，适配器仍保留原 32 KiB 限制；v2、v3 由可信 Host 配置 192 KiB 上限，整场优先、超限才分大窗口。候选 CSV、人工标注、摘要和 gold 均未进入预测输入。未做跨会议追踪。

## 冻结与输入校验

- v2 冻结提交：`a8dabf40d9a2613781ded1f55747bb6a8e5a81c2`，逐文件核对 `v2_frozen.json` 哈希及冻结提交内容。
- v3 冻结提交：`7f8397fde1d1c0d21fbde0c7b8db64ebabfb3883`，逐文件核对 `v3_frozen.json` 哈希及冻结提交内容。
- 预测运行 HEAD：`ef8b1db37fa2e2df389db11da92d12aee5c644ef`。
- gold 提交：`03a54d2cd`；文件 `gold_es2004.jsonl`，6 条；SHA-256：`d7b275bef8065bc8deb679529d95386d2b2e32cfaf00887e4f7e840649d51e3b`。
- 运行前只读取 gold 字节计算哈希，不解析或显示内容；39 次预测请求全部完成并保存后才读取 gold 供裁判使用。续跑前后再次核验冻结源码及 gold，均一致。
- 全部预测文件 SHA-256：`2fca2eddedb235d7d5b9b8a92bf42ae1ece89fed37a54379d80ada6699997c1d`，两次续跑均复用该文件。

## AMI 说话人角色

从官方 AMI ZIP 重新读取 `corpusResources/meetings.xml`，未沿用 ES2002 或 ES2003 的映射。成员 SHA-256：`8ab6cdcf03ed863e839418f31c7380392397a2eed892d73b9bc1b438c7ec520a`。转录由同一 ZIP 中本场 words/segments 文件按冻结转换函数重建；未读取人工标注目录。

| 会议 | A | B | C | D |
| --- | --- | --- | --- | --- |
| ES2004a | 用户界面设计师 / User Interface Designer（MEO015） | 项目经理 / Project Manager（FEE013） | 工业设计师 / Industrial Designer（MEE014） | 市场负责人 / Marketing Executive（FEE016） |
| ES2004b | 用户界面设计师 / User Interface Designer（MEO015） | 项目经理 / Project Manager（FEE013） | 工业设计师 / Industrial Designer（MEE014） | 市场负责人 / Marketing Executive（FEE016） |
| ES2004c | 用户界面设计师 / User Interface Designer（MEO015） | 项目经理 / Project Manager（FEE013） | 工业设计师 / Industrial Designer（MEE014） | 市场负责人 / Marketing Executive（FEE016） |
| ES2004d | 用户界面设计师 / User Interface Designer（MEO015） | 项目经理 / Project Manager（FEE013） | 工业设计师 / Industrial Designer（MEE014） | 市场负责人 / Marketing Executive（FEE016） |

## 基线：漏掉与多出

TP=3，FP=4，FN=3。

漏掉的标准任务：

- **ES2004b / ES2004b-m001**：Look into rechargeable batteries with a docking station
  中文：研究带底座的充电电池；负责人：Industrial Designer；期限：未说明。
- **ES2004d / ES2004d-c001**：Fill out the final evaluation questionnaire
  中文：填写最终评估问卷；负责人：All members；期限：未说明。
- **ES2004d / ES2004d-c002**：Suggest to management that speech recognition remain in the product design
  中文：向管理层建议在产品设计中保留语音识别；负责人：Whole team；期限：未说明。

多出的预测：

- **ES2004a / p-c119518445651de2 [WRONG_TASK]**：Prepare and deliver another PowerPoint presentation at the next meeting
  中文：准备并在下次会议上再做一次PowerPoint演示；负责人：B；期限：in half an hour。
  裁判理由：该预测是项目经理在下次会议上做PowerPoint演示，属于会议展示任务，而非黄金任务中要求的各成员为下次会议准备个人工作。
- **ES2004b / p-68bd7b705c0581f9 [DISCUSSION]**：Email extra details/information from market research tables if needed
  中文：根据需要发送市场研究表格的额外细节/信息；负责人：D；期限：未说明。
  裁判理由：预测任务为根据需要发送市场研究表格的额外细节，但该内容仅为提议，未被采纳为具体任务，属于非采纳讨论。
- **ES2004c / p-c567dd0350bf5e64 [UNSUPPORTED]**：Incorporate the company logo into the design
  中文：将公司标志融入设计中；负责人：A；期限：未说明。
  裁判理由：会议中A只是说会在设计中加入公司标志，但这是对已做设计的陈述/提及，并非明确采纳、分配或承诺的未来工作；且没有明确的任务分配。
- **ES2004d / p-e285c5a74f16400e [WRONG_TASK]**：Delegate remaining catch-up tasks in the role of Project Supervisor
  中文：以项目主管身份分派剩余的追赶任务；负责人：B；期限：about fifty minutes。
  裁判理由：预测任务是项目经理以主管身份分派剩余追赶任务，而金标任务分别是填写最终评估问卷和向管理层建议保留语音识别，二者动作与交付物均不同，无法匹配。

## legacy（接线修复后）：漏掉与多出

TP=0，FP=1，FN=6。

漏掉的标准任务：

- **ES2004a / ES2004a-c001**：Each member prepares their individual work for the next (functional design) meeting
  中文：每位成员为下一次（功能设计）会议准备各自的个人工作；负责人：Whole team；期限：next meeting (in half an hour)。
- **ES2004b / ES2004b-c001**：Research consumers' opinions on instruction manuals
  中文：调研消费者对说明书（使用手册）的看法；负责人：Marketing Executive；期限：before the next meeting。
- **ES2004b / ES2004b-c002**：Look into the company logo and how to incorporate it into the product design
  中文：研究公司标志以及如何将其融入产品设计；负责人：User Interface Designer；期限：未说明。
- **ES2004b / ES2004b-m001**：Look into rechargeable batteries with a docking station
  中文：研究带充电底座的充电电池；负责人：Industrial Designer；期限：未说明。
- **ES2004d / ES2004d-c001**：Fill out the final evaluation questionnaire
  中文：填写最终评估问卷；负责人：All members；期限：未说明。
- **ES2004d / ES2004d-c002**：Suggest to management that speech recognition remain in the product design
  中文：向管理层建议在产品设计中保留语音识别功能；负责人：Whole team；期限：未说明。

多出的预测：

- **ES2004c / p-389d56dd51a0f77d [UNSUPPORTED]**：incorporating in the design
  中文：纳入设计中；负责人：A；期限：未说明。
  裁判理由：预测任务“incorporating in the design”过于笼统，且引文中的“we'll be incorporating in the design”指代不明（未说明具体要纳入什么），属于模糊表述而非明确采纳的具体未来工作；同时该引文本身不构成可执行任务，故判为无依据。

## v2（冻结）：漏掉与多出

TP=2，FP=4，FN=4。

漏掉的标准任务：

- **ES2004b / ES2004b-c002**：Look into the company logo and how to incorporate it into the product design
  中文：研究公司标志以及如何将其融入产品设计；负责人：User Interface Designer；期限：未说明。
- **ES2004b / ES2004b-m001**：Look into rechargeable batteries with a docking station
  中文：研究带底座的充电电池；负责人：Industrial Designer；期限：未说明。
- **ES2004d / ES2004d-c001**：Fill out the final evaluation questionnaire
  中文：填写最终评估问卷；负责人：All members；期限：未说明。
- **ES2004d / ES2004d-c002**：Suggest to management that speech recognition remain in the product design
  中文：向管理层建议在产品设计中保留语音识别；负责人：Whole team；期限：未说明。

多出的预测：

- **ES2004a / p-bb8d645329ed4860 [WRONG_TASK]**：Prepare a PowerPoint presentation for the next meeting
  中文：为下次会议准备一份PowerPoint演示文稿；负责人：Project Manager；期限：half an hour。
  裁判理由：项目经理只是说自己会再做一次PowerPoint演示，属于会议流程/演示性表述，并非被采纳的实质性未来工作任务，且与金标任务不同。
- **ES2004c / p-389d56dd51a0f77d [UNSUPPORTED]**：Incorporate the company logo into the design
  中文：将公司标志纳入设计；负责人：User Interface Designer；期限：未说明。
  裁判理由：会议中没有采纳将公司标志纳入设计的任务；D只是提问，A说会在设计中体现，但未形成明确采纳的待办工作，且无明确负责人。
- **ES2004d / p-03612a4610f47013 [WRONG_TASK]**：Delegate catch-up work in the role of Project Supervisor
  中文：以项目主管身份分配积压的追赶工作；负责人：Project Manager；期限：about fifty minutes。
  裁判理由：该预测是项目经理在会议末尾表示将分配积压工作，属于会议机制/行政安排，并非金标中的任务；与金标任务（填写问卷、建议保留语音识别）无关。
- **ES2004d / p-77c316d62d08bec8 [WRONG_TASK]**：Complete meeting minutes for two of the meetings
  中文：完成两次会议的会议纪要；负责人：Project Manager；期限：未说明。
  裁判理由：该预测是项目经理提及自己已有两次会议的纪要，属于会议机制/行政事务，并非金标任务；与填写问卷或建议保留语音识别无关。

## v3 全部（HIGH+LOW）：漏掉与多出

TP=2，FP=4，FN=4。

漏掉的标准任务：

- **ES2004b / ES2004b-c002**：Look into the company logo and how to incorporate it into the product design
  中文：研究公司标志以及如何将其融入产品设计；负责人：User Interface Designer；期限：未说明。
- **ES2004b / ES2004b-m001**：Look into rechargeable batteries with a docking station
  中文：研究带底座的充电电池；负责人：Industrial Designer；期限：未说明。
- **ES2004d / ES2004d-c001**：Fill out the final evaluation questionnaire
  中文：填写最终评估问卷；负责人：All members；期限：未说明。
- **ES2004d / ES2004d-c002**：Suggest to management that speech recognition remain in the product design
  中文：向管理层建议在产品设计中保留语音识别；负责人：Whole team；期限：未说明。

多出的预测：

- **ES2004a / p-f173b7560949b6b5 [DISCUSSION]**；置信度 LOW：Put meeting information / notes into the shared folder
  中文：将会议信息/笔记放入共享文件夹；负责人：Project Manager；期限：未说明。
  裁判理由：这只是关于是否记笔记以及可能放入共享文件夹的试探性讨论（“or something”），并未被明确采纳为具体任务，属于非采纳性讨论。
- **ES2004b / p-68bd7b705c0581f9 [DISCUSSION]**；置信度 LOW：Email extra details and tables from market research if needed
  中文：如有需要，通过邮件发送市场研究的额外细节和表格；负责人：Marketing Executive；期限：未说明。
  裁判理由：营销主管只是有条件地提出如有需要可邮件发送额外细节，属于提议而非会议采纳的承诺任务。
- **ES2004c / p-c567dd0350bf5e64 [UNSUPPORTED]**；置信度 LOW：Incorporate the company logo into the remote control design.
  中文：将公司标志纳入遥控器设计。；负责人：User Interface Designer；期限：未说明。
  裁判理由：会议中没有采纳将公司标志纳入遥控器设计的任务；仅讨论标志是否明显，且预测引用的“incorporating in the design”并非明确采纳的工作任务，属于无依据任务。
- **ES2004d / p-e285c5a74f16400e [WRONG_TASK]**；置信度 LOW：Delegate and catch up on overdue project work and documentation
  中文：委派并追赶积压的项目工作与文档；负责人：Project Manager；期限：about fifty minutes。
  裁判理由：预测任务是项目经理委派并追赶积压的项目工作与文档，与金标任务（填写最终评估问卷、向管理层建议保留语音识别）均不同；且'about fifty minutes'是追赶时间而非任务截止日期。

## v3 仅 HIGH：漏掉与多出

TP=1，FP=0，FN=5。

漏掉的标准任务：

- **ES2004b / ES2004b-c001**：Research consumers' opinions on instruction manuals
  中文：研究消费者对说明书的看法；负责人：Marketing Executive；期限：before the next meeting。
- **ES2004b / ES2004b-c002**：Look into the company logo and how to incorporate it into the product design
  中文：研究公司标志以及如何将其融入产品设计；负责人：User Interface Designer；期限：未说明。
- **ES2004b / ES2004b-m001**：Look into rechargeable batteries with a docking station
  中文：研究带底座的充电电池；负责人：Industrial Designer；期限：未说明。
- **ES2004d / ES2004d-c001**：Fill out the final evaluation questionnaire
  中文：填写最终评估问卷；负责人：All members；期限：未说明。
- **ES2004d / ES2004d-c002**：Suggest to management that speech recognition remain in the product design
  中文：向管理层建议在产品设计中保留语音识别功能；负责人：Whole team；期限：未说明。

多出的预测：

无。

## 两次中止、诊断与最终处理

第一次中止发生在全部四套系统预测保存、基线／legacy／v2 裁判完成之后：生成 v2 中间报告时，临时调用未传 evaluation_scope.windows，冻结报告函数在 scripts/eval_tasks.py:1143 抛出 KeyError。用户授权后，仅在临时调用中补传窗口数，复用了全部预测及基线、legacy、v2 的有效裁判；没有重新生成预测或修改仓库中的代码文件。
第二次中止发生在 ES2004a／v3 仅 HIGH 的裁判响应校验（累计第 53 次请求）。该场只有一个 HIGH 预测，合法 ID 为 `p-4d1ca8a01ce8b3a1`。无效响应具体条目：

| 响应条目 | prediction_id | category | gold_id | owner_correct | deadline_correct | 问题 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | p-4d1ca8a01ce8b3a1 | MATCH | ES2004a-c001 | true | true | 该预测的首次判断 |
| 2 | p-4d1ca8a01ce8b3a1 | MATCH | null | true | true | 重复同一预测 ID；MATCH 的 gold_id 为空 |

具体类型是**重复预测 ID**，不是未知 ID 或非法类别（MATCH 属于合法枚举）。第二条同时违反 MATCH 必须指向本场合法 gold 的规则。冻结校验器首先在 scripts/eval_tasks.py:922 拒绝重复 ID，尚未走到其后的 MATCH/gold 检查。
无效原始响应完整保存在 [provider-response-0053.json](../../work/ami_tasks_results_es2004_final/continuation_1/judge_v3/provider-response-0053.json)，SHA-256：`64ea3d3e3f8d4ec664ac390e60bc0c6893c282a1558351666fd70b065b4a9aae`。
用户随后授权该份判断重判一次。临时调用向裁判反馈了重复条目的行号、唯一合法预测 ID、第二条 MATCH/null 的错误，以及既有“每条预测恰好一行／一对一匹配”的规则。反馈只涉及 ES2004a 本场，不提供其他会议的判断，也不提供该场全部候选口径的判断。

**最终采用独立重判结果。** 第 54 次请求通过原冻结校验器：唯一 HIGH 预测匹配 ES2004a-c001，负责人和期限均正确。没有启用从全部候选筛选 HIGH 的备用方案；其他会议两种口径也按各自候选集合独立裁判。其余有效预测及裁判均复用。
成功重判的原始响应见 [provider-response-0054.json](../../work/ami_tasks_results_es2004_final/continuation_2/judge_v3/provider-response-0054.json)，SHA-256：`1ea83b9a604df658999e8b5f304393308b95cee93ae0cc182abb962e6e60e895`。之后剩余会议裁判全部通过，未出现其他错误。

## 请求与费用

| 阶段 | 状态 | 新增请求 | 费用 USD |
| --- | --- | ---: | ---: |
| 首次运行 | 漏传 windows，中止 | 51 | 0.375731088 |
| 第一次授权续跑 | HIGH 裁判重复 ID，中止 | 2 | 0.000825066 |
| 第二次授权续跑 | 指定响应重判一次，并完成其余裁判 | 7 | 0.002238246 |
| 累计 | 完成 | **60/100** | **0.378794400 / 2.00** |

| 模型 | 请求 | 输入 token | 输出 token（含思考） | 费用 USD |
| --- | ---: | ---: | ---: | ---: |
| gemini-3.8-flash | 39 | 457876 | 5371 | 0.363548250 |
| deepseek-flash | 21 | 303657 | 9080 | 0.015246150 |

Gemini 共 39 次：基线 4、legacy 27、v2 4、v3 4；DeepSeek 共 21 次，包含无效响应和唯一一次授权重判。60 份提供商响应均保留，请求序号 1–60 连续且无重复。费用按 API token 用量和沿用费率计算，为用量估算，非账户账单；失败校验的请求也计入费用及上限。

## 裁判人工抽查

抽查文件：[spot_check_es2004_final.csv](spot_check_es2004_final.csv)。五种评分口径分别从预测判断与漏检判断中抽取向上取整的 20%，固定种子 20261029，共 **10/43** 条。附英文任务、中文翻译、原文引用及翻译、字段判断、裁判理由，人工判断列已按用户反馈全部填写“同意”。
在各口径样本数不变的前提下，约束随机抽样包含 ES2004a／v3 仅 HIGH 的实际重判判断，位于 CSV **第 9 条数据记录**（不含表头；引用中的换行可能占多行文本）。用户已完成核对：10 条全部同意；本轮据此写入人工判断列，评分不变。

## 隔离说明

1. 根据用户确认，README 的 ES2004 章节曾写入具体任务描述、负责人、期限和短原文引用。开发期间曾有脚本整体处理 README，该章节可能被程序读入，但未显示给开发者，也未作为提示词、规则或模型输入。发现隔离提醒后未再读取该章节；本轮未读取 README 的 ES2004 章节或人工标注目录。
2. 对冻结提交 7f8397fde 的提示词、规则和代码进行了不区分大小写的固定字符串检索；覆盖冻结源码，以及 src/、scripts/、tests/ 全部文件，不搜索 README、gold 或人工标注目录。用户指定的 12 个词均为零命中，最终汇总时再次检索结果一致：

| 检索词 | 命中数 |
| --- | ---: |
| rechargeable | 0 |
| docking | 0 |
| battery | 0 |
| batteries | 0 |
| logo | 0 |
| instruction manual | 0 |
| speech recognition | 0 |
| voice recognition | 0 |
| questionnaire | 0 |
| shared folder | 0 |
| PowerPoint | 0 |
| your field | 0 |

3. v3 冻结之后，用户提示词出现了 ES2004 的部分任务示例。用户明确确认这一暴露发生在冻结后，并授权继续。此后本轮未修改任何代码：所有预测均由 Gemini 调用冻结版本生成；两次续跑只复用预测、补齐临时报告参数或按授权继续裁判。源码与 gold 的前后哈希一致，预测文件哈希不变。
本报告保留上述事实，不将本轮描述为“从未存在任何 ES2004 内容暴露”。

## 来源与文件边界

AMI Meeting Corpus / AMI Project / AMI Consortium。来源：[AMI Corpus](https://groups.inf.ed.ac.uk/ami/corpus/)，许可：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。转录转换沿用冻结脚本：按原始词 XML 和发言段关联重建正文，保留词序、发言人及相对秒数；产品解析所需 ISO 时间仅为技术锚，不是日历期限。
完整语料、原始响应和中间结果留在被忽略的 work/ami_tasks_results_es2004_final/；提交仅包含新增结果 JSON、报告和抽查 CSV，不包含整场转录、ZIP、摘要或人工标注目录。

## 结论

人工抽查结果：spot_check_es2004_final.csv 共 10 条，用户全部同意，完全一致率为 **10/10（100%）**。本轮只填写人工判断并离线汇总，不新增模型调用，不改变预测、gold 或评分规则。

### 两组保留集汇总：ES2003 + ES2004

两组共 8 场会议、**15 条标准答案**。下面按 TP、FP、FN 相加计算微平均，不对两个数据集的百分比取平均。ES2003 对基线、legacy、v2 是保留集，但已用于 v3 开发；因此不把 ES2003 纳入 v3 的保留集汇总。计数来自 [ES2003 最终结果](results_es2003_final.json) 与 [ES2004 最终结果](results_es2004_final.json)。

| 系统 | TP | FP | FN | 召回率（TP / gold） | 准确率（TP / 预测） |
| --- | ---: | ---: | ---: | ---: | ---: |
| 基线 | 10 | 5 | 5 | 10/15（66.7%） | 10/15（66.7%） |
| legacy（接线修复后） | 5 | 4 | 10 | 5/15（33.3%） | 5/9（55.6%） |
| v2（冻结） | 7 | 4 | 8 | 7/15（46.7%） | 7/11（63.6%） |

### v3 单组保留集：仅 ES2004

| 系统／口径 | TP | FP | FN | 召回率（TP / gold） | 准确率（TP / 预测） |
| --- | ---: | ---: | ---: | ---: | ---: |
| 基线（同组参照） | 3 | 4 | 3 | 3/6（50.0%） | 3/7（42.9%） |
| v3 全部（HIGH+LOW） | 2 | 4 | 4 | 2/6（33.3%） | 2/6（33.3%） |
| v3 仅 HIGH | 1 | 0 | 5 | 1/6（16.7%） | 1/1（100.0%） |

v2、v3 在开发集上曾追平基线；v3 也在开发集准确率上超过基线。具体而言，ES2002 开发集的基线、v2 和 v3 全候选均为召回 11/11、准确 11/11；ES2003 用于 v3 开发后，v3 全候选召回 7/9，与基线 7/9 持平，准确率 7/7，高于基线 7/8。依据分别为 [v2 开发结果](results_v2_phase1.json) 和 [v3 开发结果](results_v3_dev.json)。
**保留集上，v2、v3 的召回率均低于基线。** v2 两组合并为 7/15，低于基线 10/15；v3 唯一保留集 ES2004 的全候选召回 2/6、仅 HIGH 为 1/6，均低于同组基线 3/6。开发集成绩未直接推广到保留集。

### 置信度的发现

- ES2004 上，v3 的 **4 条误报全部为 LOW**；HIGH 预测共 1 条，匹配正确，准确率 **1/1（100%）**。5 条 LOW 中仍有 1 条正确，不能把 LOW 直接当成无效任务。
- ES2003 开发集上，HIGH 预测共 6 条，全部匹配正确，准确率 **6/6（100%）**。这里的“正确”指待办任务匹配，不能据此推断所有字段均正确。
- 样本量很小，尤其 ES2004 只有 1 条 HIGH；ES2003 还是开发集。这只能作为置信度分流、优先人工复核 LOW 候选的初步信号，不能据此宣称 HIGH 的正确率有保证。

### 生成阶段诊断

两组保留集合计召回率：基线 **10/15（67%）**、v2 **7/15（47%）**、legacy **5/15（33%）**（按整数百分比取整；精确计数及准确率见上表）。

已有 [ES2003 v2 离线诊断](results_es2003_final.md#诊断不改变成绩) 显示：四场原始候选共 5 条，Host 状态排除、引用／字段校验拒绝、去重减少均为 **0 条**，最终保留 5 条。因此，所追踪的两条“v2 漏掉而基线找到”的任务问题发生在生成阶段：不同负责人的分工被模型写进同一候选，未分别列出；另一条随口说出的文档撰写承诺没有被单列。对具体性和采纳状态的要求可能让模型对随口承诺过于保守，但保存响应不能证实模型的内部原因。

“过滤排除 0 条”仅指上述 ES2003 v2 原始候选统计，不代表 ES2004 或 v3 的过滤统计。上述诊断不改变既有成绩，也不新增模型调用。

### 产品取向

以当前模型能力，在本次数据和固定配置下，**简单整场读取的基线在保留集待办抽取召回上最强**。系统的价值应集中在抽取之后的引用与来源校验、人工审核、风险分级和跨会议追踪。当前已实现的校验和审核机制应帮助用户核实、修正候选；跨会议追踪仍是后续方向，本轮仅预留接口，未评测其效果。
v3 继续保持显式配置才启用，默认 task_extraction_mode=legacy，不因本轮结论切换默认流程。
结果 JSON 中 response_audit.spot_check_sha256 记录的是人工填写前的 CSV 快照；本轮仅更新人工判断列，其余 CSV 单元格与模型成绩保持不变。
