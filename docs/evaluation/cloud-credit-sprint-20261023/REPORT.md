# LinkLoom 真实 Provider 评测冲刺最终记录（2026-10-23）

A–E 已执行到有诊断价值的边界，现已停止新模型请求。本文与 `continuation-analysis-v2/` 是最终结论；此前 `REPORT.md`、`qualification.json` 是只完成 A 时的历史快照，其中 B–E 的 NOT_RUN 已被本次续跑取代，原文件保留。

## 执行与费用

全部串行；沿用用户已确认可用的赠金授权，没有再次读取 Billing Console。每批先固定输入和估算，再以已有 TaskProviderSession 在发送前持久化请求、Token 和费用预留；SDK 每次只尝试一次。一次 B 传输恢复和一次 D 本地参数修复均单独留档，扣除原预留、请求数与已耗时后继续，未重置批次预算。

| 批次 | 请求上限 / Token 上限（输入、输出）/ 总时限 | 费用上限 | 本次预留 | 实际发送 / 收到回复 | 成功回执 Token 费用估算 |
|---|---|---:|---:|---:|---:|
| A | 原预检边界，见历史授权 | $3 | $2.40 | 16 / 16 | $0.119210 |
| B | 24 / 1,500,000、196,608 / 1500 秒 | $6 | $5.25 | 21 / 20 | $0.135157 |
| C | 16 / 800,000、131,072 / 1000 秒 | $4 | $2.50 | 10 / 10 | $0.041110 |
| D | 8 / 400,000、65,536 / 800 秒 | $4 | $3.00 | 5 / 5 | $0.060092 |
| E | 12 / 600,000、98,304 / 1000 秒；实际限定 5 调用 | $4 | $1.25 | 5 / 5 | $0.028025 |
| 合计 | 58 次预留，其中 1 次证明未发送 | $21 计划分配 | **$14.40** | **57 / 56** | **$0.383595** |

成功回执共 334,273 输入 Token、359,695 总 Token；Provider 响应延迟合计约 291 秒，并非用户端墙钟总时间。费用估算包含报告的 thinking Token；原始空计数保留。估算使用 Flash 输入/输出 $0.75/$3.75、Argon $2/$10 每百万 Token 的[官方费率](https://ai.google.dev/gemini-api/docs/pricing)。这些数字不是账单实扣或赠金扣减证明，二者保持 UNKNOWN；B 失败请求没有 Token 回执，未假定免费，其预留继续计入 $14.40。

B 首次 TLS 失败保留为失败及首遍 N/E。D 第二次预留被通用 Session 标为 UNCERTAIN；离线重放证明 SDK 映射器在进入 wire 前拒绝 temperature=0，且只有第一份 wire 请求。原账本不改写，另存 `prewire-resolution.json`，完整保留 $1 初始预留。修复后的独立评测显式设置 temperature=None、thinking=low，并复用已成功的小请求预检。没有重复付费探测。

## 固定代码与实现边界

- 检查时远端 master：`46b819fa39ace26de2c5470f7438b56df523c560`，包含 M3-R/M3-S 主线，但缺少后续 S1/S2 与 M3-D 集成。
- B–E 运行冻结候选：`d0c64361ce0613d021d5d950954f7b0da6d2fabe`，包含 M3-D、answer-first、S1 bounded replan 与 S2 task-recall/v3。原 M3-D 基点为 `33390e2eeaad71d215e279b2f724cae672ea4f80`。
- SDK `google-genai 2.22.0`，Python `3.13.5`。实际响应的 model_version 与 response_id 逐请求保存；新评测只使用 `gemini-3.8-flash` 和通过预检的 `gemini-4-argon`。
- 沿用 F Discovery、G Planner、Product、Runtime、SemanticProposer、Reviewer 和已有 Compactor/Recovery 协议。新增内容限评测脚本、遥测及独立模型接口适配；没有建立 Agent 调度循环，也没有修改生产实现、模型白名单或上下文默认上限。
- master/candidate 代码 ZIP、输入快照及最终文件清单均含 SHA-256。现有主工作区有用户此前的未提交改动，本次没有提交、推送或操作 PR，没有修改 README 成绩或覆盖历史实验。

## A：Product LIVE

运行三个公开合成 Product 案例：master 清晰任务、master 缺字段、候选版同源缺字段。清晰任务和候选缺字段均实际经过 F → G → Runtime → SemanticAnalysis → Reviewer；master 缺字段在 G 的重规划输入预算阶段 BLOCKED，候选版对同一来源进入完整链路。

真实模型提出过 SAME_TASK、明确计划修订和非修订关系；修订方向为新计划指向旧计划。缺负责人、缺日期继续 UNKNOWN。Product 原文与 UNKNOWN 展示已做浏览器冷读验收，刷新与打开证据没有新增 Provider 请求。

这证明所列案例里的模型行为与产品链路。Reviewer 的 evidence_sufficient 只证明来源、回执、生命周期和证据结构检查通过；它明确包含 SEMANTIC_NOT_EVALUATED，并未独立确认业务关系。结果保留 PARTIAL/BLOCKED，未提升为业务批准。

## B：任务抽取

两个合成来源包含 8 个场景、9 个期望任务。恢复尝试中模型提出全部 9 项，Discovery 实际保留 7 项。首组合成数据首次请求仍记 N/E，恢复结果不改称首次通过。

| 场景 | 观察结果 / 失败阶段 |
|---|---|
| 同一句两项不同负责人任务 | 模型正确提出两项；Discovery 报 OBSERVATION_EXTRACTION_CONFLICT，使两项均未入有效结果。这是程序过滤问题，不能记成模型漏抽。 |
| 无负责人、无日期 | 索引重建、回滚演练保留，字段 UNKNOWN。 |
| 负责人更换 | 证书轮换绑定新负责人许诺及明确日期，未继续绑定旧负责人。 |
| 模糊日期 | 镜像同步没有补造绝对日期。 |
| 同任务重复出现 | 证书盘点只保留一个任务。 |
| 同文不同项目身份 | 两条上线核查保留不同项目。预置期望写“雁栖/青禾”，实际原文绑定“雁栖项目/青禾项目”；严格字面字段检查为 5/9，另两条是评测标签变体，不能冒充生产语义失败，也不事后改标签提升分数。 |
| 进入 G | 独立 B08 经正常 Product 完成 7 次真实调用，两个缺字段任务进入 G/Runtime/SemanticAnalysis，Reviewer 接受，最终 PARTIAL，负责人/日期 UNKNOWN。不能推论所有任务提案均能进入 G。 |

AMI 使用原始说话人、相对时间、原始词与 dialogue-act 映射，没有伪造绝对时间。ES2002a 全部 8 块、ES2002b 前 3/18 块共 11 次请求。Flash 在 a-6 提出两项、仅一项被接纳；营销要求任务虽提出，却因 TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS 被拒；界面设计任务未提出。其他已跑块未发现有效任务，不表示真实任务不存在。ES2002b 未跑部分全部 N/E。

官方人工标签是 AMI Manual Annotations 1.7 的摘要句 → dialogue-act 链接，适用正式检索指标，不是穷尽的任务/负责人/日期 Gold。本批没有检索实验，不能输出任务抽取正式 P/R/F1。将其用于来源对齐诊断：ES2002a 的三条人工行动摘要证据均在模型输入中；模型覆盖工业设计、营销两项，最终仅工业设计进入有效结果。ES2002b 的三条行动证据均不在已跑前缀，不能记漏抽。Gold 来源、文件哈希及映射逐条保存，均未进入模型输入。

## C：跨会议语义

10 个上下文、20 个关系提案，全部 **DEV_DIAGNOSTIC**。M2 H01–H04 复用原始资料；H04 两个来源版本拆开判断，不能声称完成原历史生命周期重放，也不更改 M2 历史 0/4 成绩。AMI 因有效端点不足，没有强凑跨会议关系对照，此项 N/E。

| 案例 | 模型 verdict |
|---|---|
| H01 | SAME_TASK SUPPORTED；REVISES_PLAN SUPPORTED，新 → 旧 |
| H02 三项影响关系 | SUPPORTED、REFUTED、UNKNOWN |
| H03 | SAME_TASK SUPPORTED；REVISES_PLAN CONFLICT，保留正反证 |
| H04 v1 / v2 | v1 同任务且修订；v2 同任务但修订 REFUTED |
| 同负责人不同工作 | SAME_TASK REFUTED，没有该例的假阳性合并 |
| 只有进展 / 已撤回建议 | SAME_TASK SUPPORTED；REVISES_PLAN REFUTED |
| 对立纪要 / 缺失引用 | 修订 CONFLICT / 关系 UNKNOWN |

19/20 提案通过精确引文结构校验。H03 SAME_TASK 的引文将实际换行写成字面 `\\n`，被正确拒绝；H03 修订冲突的引文通过。19/20 是证据校验通过数，不是语义准确率。模型提案均不直接 publishable，没有自动合并任务。

## D：较强模型 Silver

Gemini 4 Argon 经 models.get 与真实函数调用预检后，对同样的两块 AMI 输入及 H03、同负责人不同工作两个关系上下文独立生成，不读取 Flash 输出。仅标记 **MACHINE_SILVER**。

四个最终任务视图/verdict 组合与 Flash 一致，但这个一致性掩盖了差异：Argon 在 a-6 提出三项，Flash 两项；Argon 多提出了界面设计，随后界面和营销两项仍被相同 Grounding 边界拒绝，最终只剩工业设计。Argon 的 H03 两项引用均因精确引文问题被拒，Flash 仅拒一项。不能把一致结果称为 Gold 正确，不能宣称较强模型解决了当前失败。

真实 AMI 跨会议任务身份/计划修订仍缺足够有效端点，未获得独立关系正确性证明。

## E：真实输入压缩对照

使用 ES2002a 原生第 5–7 块构造明确标记的检索历史回放，不冒充生产 Runtime 已发生的历史。三组均使用既有 WorkingContext、确定性受保护字段复制及 EvidenceRecovery；LLM Compactor 先单独验证，再执行实际压缩。完整 60,579 字符会议超过现有 SDK 单输入 32 KiB 上限，完整无压缩组 N/E；本次使用可复现的有界基线，未抬高任何生产限制。

| 组别 | 读取请求输入 Token | 读取请求字节 | 待办保留 / 两条必要证据覆盖 | 压缩加读取 Provider 延迟 | 路径 Token 费用估算 |
|---|---:|---:|---|---:|---:|
| 有界不压缩 | 12,851 | 27,018 | 是 / 2/2 | 3.115 秒 | $0.010647 |
| 当前确定性压缩 | 2,168 | 5,601 | 是 / 2/2 | 3.461 秒 | $0.002635 |
| LLM 提示压缩 | 2,292 | 6,243 | 是 / 2/2 | 7.233 秒 | $0.012613 |

确定性方案读取输入 Token 下降约 83.1%，LLM 方案约 82.2%；单次小样本延迟不能排序模型性能。表中 LLM 路径包含实际压缩调用；另有一次独立协议验证 $0.002130，计入批次总额。单次读取场景中 LLM 压缩总成本高于不压缩，因此没有证据支持替换当前确定性方案。

这里 UNKNOWN 是输出协议约束，受保护问题完整复制也是工程完整性检查，不能记成独立语义判断。进一步对照官方关联的原始行动话语：不压缩组保有四条完整话语，两个压缩组只剩两条；未被发现/列入保护清单的营销任务原文均丢失。**必要证据 2/2 不代表所有任务证据无损**。这暴露的是任务覆盖与保护清单依赖，不能以压缩率掩盖。

## 已验证与仍未证明

真实 Provider 已验证：在所列诊断样例中能抽出任务、绑定更换后的负责人、保留未知日期、区分部分任务身份与修订、表达冲突/反证/缺证；正常 Product 已实际消费 F/G/SemanticAnalysis 回执，展示原文，并接受 Reviewer 检查。较强模型与 LLM Compactor 都有真实成功回执。

仍仅证明工程链路：Reviewer 的业务语义正确性、受保护字段复制、精确引文校验、压缩后两条指定证据恢复。尚未证明独立测试集准确率、完整 AMI 任务召回、真实跨会议任务合并正确率、全会议长上下文无损或 LLM 压缩优越性。

## 下一步最值得解决的三项

1. 修复 Discovery 同一句多任务的冲突身份边界：以任务描述和字段绑定区分不同工作，避免一个冲突连带剔除两项正确提案；先用已保存请求/回复离线复现。
2. 修复 AMI 跨话语任务与角色证据绑定、精确引用问题，并让完整任务覆盖驱动压缩保护清单。先复用本次工业设计/界面/营销三组人工行动链接定位模型遗漏与 Grounding 拒绝，避免“更强模型同样只剩一项”的伪成功。
3. 补齐独立语义验收数据与 Reviewer 的评测资格边界：将正确引用、关系判断与业务批准分别验证，建立未参与开发的任务身份/修订方向/反证集。当前 DEV_DIAGNOSTIC 和 MACHINE_SILVER 不能充当最终 Gold。

## 复现与材料

离线复算，无 Provider 请求（输出目录必须不存在）：

```powershell
D:/Anaconda/python.exe work/cloud-credit-sprint-20261023/analyze_batch_a.py --output work/cloud-credit-sprint-20261023/replay-a
D:/Anaconda/python.exe work/cloud-credit-sprint-20261023/analyze_continuation.py --output work/cloud-credit-sprint-20261023/replay-b-e
```

`run_batch_a*.py`、`run_batch_b.py`、`run_batch_c.py`、`run_batch_d.py`、`run_batch_e.py` 是真实执行脚本；固定代码 ZIP/输入/模型/参数均保留。它们采用一次性目录、共享累计预算与到期停止条件，不能直接覆盖已跑目录。最终 STOP 防止误发后续请求；本次没有安排自动补跑。

主要结果：`continuation-analysis-v2/aggregate.json`、`batch-summary.csv`、`requests.json`、逐案例诊断、Gold 来源快照；原始完整请求/回复在 `work/ce-{b,c,d,e}-20261023*/`，A 在本目录 `batch-a/` 与 `a3/`。原文、模型提案、规范化回复、工具回执、Grounding/Reviewer 和失败全部保留。

离线检查：独立模型适配测试 2/2、原生长历史/压缩保护/SDK 预检 1/1；A 的预算与候选契约检查分别 17/17、9/9。Windows 默认临时路径失败、初始缺模块 RED、本地参数失败等原始材料均保留，未改写成通过。
