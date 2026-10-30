# meeting_v3 开发集评测

ES2002 和 ES2003 均为开发集，结果仅作开发参考；未读取 ES2004 的受保护数据文件；README 并行写入的隔离边界见末节。没有重跑基线或 v2。
全部八场 v3 预测先保存，然后才读取 gold。HIGH+LOW 和仅 HIGH 分别计分；两者不同时独立裁判，完全相同的候选集合可复用本场判断，不从含 LOW 的匹配中删除 LOW 来推算 HIGH 分数。
模型 gemini-3.8-flash，思考 low，输出上限 32768（含思考）；裁判 deepseek-flash。置信度不改变任务、负责人和期限评分规则。

## ES2002

| 指标 | HIGH+LOW | 仅 HIGH |
| --- | ---: | ---: |
| 待办准确率 | 11/11 (100.0%) | 11/11 (100.0%) |
| 待办召回率 | 11/11 (100.0%) | 11/11 (100.0%) |
| 负责人正确率（匹配任务） | 11/11 (100.0%) | 11/11 (100.0%) |
| 期限正确率（匹配且 gold 明确） | 8/8 (100.0%) | 8/8 (100.0%) |
| 期限端到端正确率（全部明确期限 gold） | 8/8 (100.0%) | 8/8 (100.0%) |
| 讨论误判数 / 预测数 | 0/11 (0.0%) | 0/11 (0.0%) |

HIGH+LOW：TP=11，FP=0，FN=0；期限弃答 3/11。

漏掉的任务：

无。

多出的候选：

无。

仅 HIGH：TP=11，FP=0，FN=0；期限弃答 3/11。

漏掉的任务：

无。

多出的候选：

无。
## ES2003

| 指标 | HIGH+LOW | 仅 HIGH |
| --- | ---: | ---: |
| 待办准确率 | 7/7 (100.0%) | 6/6 (100.0%) |
| 待办召回率 | 7/9 (77.8%) | 6/9 (66.7%) |
| 负责人正确率（匹配任务） | 7/7 (100.0%) | 6/6 (100.0%) |
| 期限正确率（匹配且 gold 明确） | 0/0（不适用） | 0/0（不适用） |
| 期限端到端正确率（全部明确期限 gold） | 0/1 (0.0%) | 0/1 (0.0%) |
| 讨论误判数 / 预测数 | 0/7 (0.0%) | 0/6 (0.0%) |

HIGH+LOW：TP=7，FP=0，FN=2；期限弃答 4/7。

漏掉的任务：

- ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面如何运作，并弄清楚用户想要什么。
- ES2003b-c001：Look into the feasibility of a small transmitter and whether an LCD screen requires too much power, based on the design suggested so far；中文：根据目前提出的设计，研究小型发射器的可行性以及LCD屏幕是否耗电过多。

多出的候选：

无。

仅 HIGH：TP=6，FP=0，FN=3；期限弃答 4/6。

漏掉的任务：

- ES2003a-c002：Decide on the basic functionality and how the user interface will work, and figure out what the user wants；中文：确定基本功能以及用户界面如何运作，并弄清楚用户想要什么。
- ES2003b-c001：Look into the feasibility of a small transmitter and whether an LCD screen requires too much power, based on the design suggested so far；中文：根据目前提出的设计，研究小型发射器的可行性以及LCD屏幕是否耗电过多。
- ES2003c-m002：Start writing up the project documentation；中文：开始撰写项目文档。

多出的候选：

无。

## 用量与边界

累计 21/24 次请求；API 用量估算费用 $0.108480822/$0.60。未自动重试。
匹配规则沿用 meeting-task-match/v2：未解析代词负责人判错；编号按本场 AMI 元数据角色换算；明确指向同一次会议的相对时长和事件等价。期限只对 gold 明确写出的任务计分，未知期限单独统计。
LOW 候选引用校验与 HIGH 相同；不确定承诺仅为待审核提议，不自动确认。产品审核页显示置信度和理由，人工核对后才标记本地确认/拒绝，不写入权威决策记忆。
源码与配置冻结元数据见 v3_frozen.json；完整模型响应和转录留在被忽略的 work/。AMI Meeting Corpus / AMI Project / AMI Consortium，CC BY 4.0，来源 https://groups.inf.ed.ac.uk/ami/corpus/ 。

## 评分传输中断与累计预算

首次运行完成全部八场预测和六份有效裁判后，第 15 次请求（ES2002d 全候选裁判）发生 Provider transport/JSON failure。已知费用 $0.105089736；未知请求保留的费用上界 $0.060376500，没有当作免费。
仅恢复评分，所有预测和已保存有效裁判复用；新旧累计 21 次，已知费用 $0.108480822，含未知请求的保守总上界 $0.168857322。真实账户费用不能从失败请求确定。
集合完全相同的 HIGH 口径复用本场全候选判断（仅重映射不透明预测 ID）：['ES2002d', 'ES2003a', 'ES2003b', 'ES2003d']。包含 LOW 的场次仍分别裁判；没有重新调用抽取模型或修改提示词。

## 原诊断漏项与置信度

- ES2003b-c003（研究遥控器定位系统的扬声器、LED 和发射器）此次匹配为 HIGH；不同负责人的工作按独立候选输出。
- ES2003c-m002（开始撰写项目文档）此次匹配为 LOW：模型认为口语化承诺仍值得报告，但确定性不足。全候选口径计入，仅 HIGH 口径不计入。LOW 仍须通过相同的逐字引用校验并进入人工审核队列。
- 仍漏掉 ES2003a-c002 与 ES2003b-c001，未依据结果继续调提示词、阈值或标准答案。

## 输入核验与恢复边界

所有八场已保存请求与当前 ES2002/ES2003 转录重新离线核对：转换后全文、发言顺序及说话人、参会者角色映射、来源版本、哈希、整场标记、提示词和 schema 均一致，详见结构化结果的 offline_input_verification。八场均一次整场请求，没有窗口截断。v2 源码和两组 gold 的 SHA-256 与原冻结值一致；未读取 ES2004 的转录、摘要或标注。

本轮传输失败后的实际恢复发生在 resume 一致性保护加固之前，原输入一直未变。提交版恢复入口在全部预测完成后保存来源、角色、预测、gold、提示词/schema/规则/配置/模型及相关源码哈希；任何一项改变、或旧目录缺少 resume_manifest.json，都拒绝复用缓存。该加固只做离线测试，没有重新运行模型或改变本次分数。既有运行目录未补造为“当时已有 manifest”。

## 实现与验证

新增 meeting_v3 模式，与冻结 v2 并存；默认 legacy。任务按负责人组和单一交付物拆分，HIGH/LOW 及一句理由同次模型调用返回；保留 v2 的整场/大窗口、多段精确引用、原文期限类型和会后状态处理。未知负责人不合并，同负责人同交付物重复提及合并，任一成员为 LOW 则合并结果仍为 LOW。

产品导入启用 meeting_v3 后，候选在独立本地审核队列显示置信度、理由和全部引用。LOW 不自动确认；需要人工填写审核人及备注后确认或拒绝。源更新/删除使旧条目失效；待审核条目重试刷新已校验字段且不把 LOW 升为 HIGH；已经审核的条目不因重试重开。此审核只保存本地核对状态，不写入权威决策记忆。

产品行为通过 fake-provider 集成测试及双语 DOM 测试验证，未声称真实浏览器界面验收。源码冻结后没有基于开发集结果追加调参；完整验证结果见下节。

## 最终考题隔离说明

未打开或搜索 ES2004 gold 文件与 ES2004 标注工作目录，也未读取 ES2004 转录/摘要/候选。Git 提交标题和文件清单显示另一会话新增了该组标准答案；本次 v3 模型预测、提示词及规则未据此改变。源码在该并行提交出现前已经停止改动。

隔离边界如实记录：发现并行提交前，更新 v3 说明的脚本曾整体读写 README；无法从当时记录确认 ES2004 章节是否已经被另一会话写入，因此不能保证该章节未被程序读入内存。该章节未展示给本会话模型，也未成为开发/评测输入。收到进一步隔离提醒后，不再读取 README，仅更新独立 v3 报告；未修改 ES2004 gold、标注目录或 README 中该章节。

## 最终验证与冻结

冻结源码提交：`7f8397fde1d1c0d21fbde0c7b8db64ebabfb3883`。源码、提示词、schema、配置和旧 v2/gold 的哈希见 v3_frozen.json。提交后保持规则和阈值不变。

最终全量 pytest：**3285 passed、2 failed、31 skipped、1 warning**，耗时 4225.19 秒，**未全绿**。完整日志在忽略的 work/pytest_v3_frozen_final3.log，JUnit 在 work/pytest_v3_frozen_final3.xml。v3 的抽取、开发评测隔离和产品审核集成共 **34/34 项通过**；旧默认模式的双语 DOM 脚本、新 LOW 队列 DOM 脚本以及 JavaScript 语法检查均通过，未做真实浏览器界面验收。

两项失败均来自默认 legacy Host 测试，没有启用 meeting_v3：

- tests/integration/test_m3q_provider_host.py:298，test_normal_host_requires_source_permission_then_runs_actual_adapter：poll 的 30 秒上限后仍为 INVESTIGATING，预期 PARTIAL。
- 同文件 :386，test_m3r_source_bound_product_chain_matches_durable_tool_receipts：poll 返回的未完成投影还没有 task_investigation 字段。

两次后台运行随后都持久化为 completed，Reviewer 为 pass。测试期间另有主工作区全量 pytest 并行运行，存在资源争用；这与时序性失败相符，但不据此断言唯一原因。没有修改默认流程、断言或 30 秒等待上限；两项**原样复测 2 passed，耗时 79.08 秒**，见 work/pytest_v3_host_recheck.log/xml。保留完整全量失败与复测记录，不用复测覆盖全量结果，也不将其写成“最终全量全绿”。

全部源码和两组已授权 gold 在全量结束后重新验哈希，均与冻结值一致。没有合并 master，没有读取或修改最终考题数据文件。ES2004 的 README 并行时序边界如上节记录。

提交前仅去掉 v3 源文件末尾一行多余空白，AST 比较一致；提示词、schema、配置和行为均未改变，未因此重复全量测试。
