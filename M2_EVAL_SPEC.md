# M2 关系评测与独立 Gold 设计

状态：待确认的离线评测规格，尚未生成 fixture、冻结 Gold 或运行评测。日期：2026-10-18。关系含义以 M2_RELATION_DESIGN.md 为准。这里先确定语义、原文及 Gold，再讨论实现；不得从系统预测反推标签。

## 1. 评测对象及隔离

首层是**固定候选对的关系判定**：输入已人工核对、带原始证据的 M1 TaskObservation、原文、source snapshot、必要的既有决策审核状态。它隔离 M2，不把 M1 抽取失败混成关系错误。之后另做“来源→M1→候选发现→关系→回答”的端到端检查，分别报告抽取/候选召回/关系判定损失，不用固定候选对 Recall 冒充任务发现 Recall。

scripted proposals 只用于离线验证关系校验器与管线：提供未受信任的关系假设/证据，不预置最终 verdict 或 delta 再回显。此类检查不能证明模型的语义抽取或候选发现准确率。若以后评模型质量，recorded responses 必须由只见公开输入的独立运行获得并先固定，再由 evaluator 读取 Gold 评分；本轮不采集响应、不调用 Provider。

仅用全新合成 Markdown/TXT。下述 A/B 等均为**案例内独立来源**，不是生产文件。任务行有可信 speaker 时，fixture 使用规范时间戳发言头；无 speaker 的普通 Markdown 不补负责人。行号是设计锚点；将来 fixture 冻结后计算真实 EvidenceSpan/Unicode 字符范围和 hash，不能手填猜测 offsets。

拆分：D01–D08 为 Dev；H01–H04 为保留验收。不得把同一任务/同一来源版本家族拆到不同集合。案例标题、Gold、拒绝原因、预期差值均不得进入被测模型输入；公开输入只含不带标签含义的 case ID、业务资料、已验证观察、合法候选端点与统一 schema/规则。

**独立性边界：**本文包含全部 12 个案例的拟定 Gold，因而是公开的设计验收集，不能声称对读过本文的开发者盲测。未来被测模型必须用全新上下文，无仓库搜索或 Gold 目录访问，只接收输入 allowlist。开发者可见 Gold 与模型不可见 Gold 是两回事。若需要衡量未见样本泛化，须另由未看系统输出的标注者制作保留变体并封存；那不是本轮已交付的能力或实验。

拟定后续目录分离：`inputs/{dev,holdout}` 与 evaluator-only `gold/{dev,holdout}`，独立 loader、不同路径，不把 Gold 放入检索目录/提示词/recorded response。正式候选工具只读输入目录。将来运行前由人工或独立审阅核对语义与证据，然后记录输入/Gold 各自 hash、split、标注者、版本、冻结时间；不改现有冻结 Gold/旧账本。保留集只在实现/阈值固定后运行一次验收；看过失败再修复时须说明该版本已暴露，不冒充新的盲测。

## 2. Gold 数据与判定单位

每个预先声明的原子问题记录：

```text
gold_version, case_id, split, snapshot_id, workspace_id
subject_ref, predicate, object_ref, scope
verdict: SUPPORTED | REFUTED | UNKNOWN | CONFLICT | NOT_ASSESSABLE
expected_lifecycle, required_premises, acceptable_proof_sets
counter_evidence, forbidden_inferences, rationale
comparison?: status, old/new date+bound+precision, delta_calendar_days
authority_expectation?: REVIEWED_DECISION | SOURCE_CLAIM_ONLY
```

- `acceptable_proof_sets` 是完成同一证明的可选最小证据集合；预测需完整覆盖其中一组。引用更多无关文档不增加分数。引用有效但不蕴含结论，仍判错。
- 同一案例可有多个原子问题；分母按 **端点对 + predicate + scope + snapshot** 计数，不按案例数。规范方向见关系设计。观察/关系重复输出先规范去重，重复次数另报，不能刷 TP。
- 下文 `S/L/I/R/P/Alias` 分别指 SAME_TASK / RELATED_TO / AFFECTED_BY / REVISES_PLAN / IN_PROJECT / PROJECT_ALIAS；`+/-/?/!` 分别是 SUPPORTED / REFUTED / UNKNOWN / CONFLICT。`S(B,A)` 等指观察端点，项目/变更有各自类型。无明确回指的 SAME_TASK 记号只标识该观察对；物化 Gold 时按实际 observation ID 规范方向，而不假定字母 B 的 hash 更大。
- 未列出的关系不是默认负例。若系统主动发布额外确认关系，必须进入额外输出审计；无预先独立 Gold 支持不得计 TP，发布无证据的关系计 FP。不能把整个未标注图空间当 TN。

## 3. 八个 Dev 案例

### D01 明确同一任务，名称变化但未改计划

```text
A.md A1 [2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 SMS-17 的短信接口接入，计划在2026年10月14日前完成。
B.md B1 [2026-10-11T09:00:00+08:00] 陈晨: 我负责星河项目的通知网关接入；本条对应A.md的SMS-17，是同一个任务，仅更换称呼，不修订任务计划。
```

Gold：S(B1,A1)+，证据 A1 的项目/编号及 B1 的明确回指；R(B1,A1)-，证据 B1 的“不修订”。比较 NOT_APPLICABLE，delta=null。B 未写日期，不复制 A 的日期。验证改称不妨碍身份确认，但身份确认不等于计划修订。

### D02 相同负责人及相似描述，明确不同任务

```text
A.md A1 [2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 SMS-21 的生产短信接口联调，计划在2026年10月14日前完成。
B.md B1 [2026-10-11T09:00:00+08:00] 陈晨: 我负责星河项目任务 SMS-22 的沙箱短信接口联调，计划在2026年10月18日前完成；这是独立任务，不是A.md的SMS-21。
```

Gold：S(B1,A1)-，证据 A1+B1 尤其明确否定句。两个独立业务任务的结论在本例有证据；不能将 14→18 输出为延期。本例不要求模型从“编号不同”单独推出不同身份。

### D03 有工作关联，任务身份未定

```text
A.md A1 [2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目的短信接口联调，计划在2026年10月14日前完成。
B.md B1 [2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目的新接口调整，计划在2026年10月18日前完成。
B.md B2 说明：B1的接口调整与A.md的联调工作相关，是否属于同一业务任务尚待核对。
```

Gold：L(B1,A1)+，detail=WORK_LINK，证据 A1+B1+B2；S(B1,A1)?，原因身份待核对。已确认相关与身份未知可同时成立。不能把 RELATED_TO 升级为 SAME_TASK。

### D04 明确身份及计划修订，允许计算边界变化

```text
A.md A1 [2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4 的签名接口联调，计划在2026年10月14日前完成。
B.md B1 [2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4；本条就是A.md的同一任务，现修订其计划，将原定2026年10月14日前完成改为2026年10月18日前完成，本计划替代原计划。
C.md C1 [2026-10-16T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4，仍是B.md的同一任务；本条替代B.md计划，仅在原任务范围内增加回执校验，截止边界不变，仍在2026年10月18日前完成。
```

Gold：S(B1,A1)+；R(B1,A1)+，changed_fields=[deadline]。证据 A1+B1，必须含“同一任务”“将原定…改为…/替代”。Comparison=COMPUTABLE，old=2026-10-14 BEFORE，new=2026-10-18 BEFORE，delta_calendar_days=+4。生效时间未写则 unknown；只能报告计划截止边界后移四个日历日，不能声称实际延期或 10-15 起已生效。

同案例第二步 Gold：S(C1,B1)+、R(C1,B1)+，changed_fields=[scope]，证据 B1+C1；日期比较仍为 COMPUTABLE，BEFORE 18→18，delta=0。这才是有证据的“截止未变”，与日期缺失的 null 不同。两个修订问题分别计分，不自动推导 C→A。

### D05 同一任务仅日期不同，没有修订依据

```text
A.md A1 [2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-5 的接口联调，计划在2026年10月14日前完成。
B.md B1 [2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-5 的接口联调，计划在2026年10月18日前完成。
```

Gold：S(B1,A1)+，作用域内明确同编号，无其他冲突；R(B1,A1)?。日期值有分歧，但没有说明修改、纠错、替代或新旧计划顺序；较晚发言不补足修订依据。Comparison=BLOCKED，NO_REVISION_EVIDENCE，delta=null。14、18 均可作为各观察原文计划列出。

### D06 供应商变更的直接影响，有正式决策前提

```text
A.md A0 [2026-10-10T10:00:00+08:00] 林悦: 星河项目正式选择云桥科技为短信供应商，自2026年10月10日起生效。
A.md A1 [2026-10-15T10:00:00+08:00] 林悦: 星河项目短信供应商由云桥科技改为星帆通信，自2026年10月15日起生效，替代原选择。
B.md B1 [2026-10-15T10:05:00+08:00] 陈晨: 我负责星河项目任务 ADAPT-2 的签名参数适配；因为A.md记载的从云桥科技改用星帆通信，此任务必须调整签名参数，计划在2026年10月18日前完成。
```

fixture 前提：A0、A1 依次经**现有正式审核路径**在全新临时库生成有效回执及替代链，均为合成资料，不能仅设置一个 approved=true。Gold：I(B1,DecisionChange_A1)+，证据 A1+B1 的具体因果/适配句及合法回执绑定，替代链另回读 A0。负责人陈晨、截止 18 日均回读 B1；没有旧任务计划，不计算日期变化。

### D07 原五行语义边界：不能补齐项目或制造延期

下列是用于跨文档验收的**新合成拷贝**，不修改 M1 五行输入、ID 或历史证据。前两行放 A，后三行放 B；这项编排本身不提供跨文件项目归属。

```text
A.txt A1 [2026-10-10T09:00:00+08:00] 林悦: 星河项目今天正式确定，短信服务供应商选择云桥科技，自2026年10月10日起生效。选择原因是报价较低，且支持国内短信发送。
A.txt A2 [2026-10-10T09:05:00+08:00] 陈晨: 我负责星河项目的短信接口接入，计划在2026年10月14日前完成联调。
B.txt B1 [2026-10-15T10:00:00+08:00] 林悦: 星河项目的短信服务供应商现正式改为星帆通信，自2026年10月15日起生效，替代2026年10月10日选择云桥科技的决定。原因是云桥科技在联调中多次延迟，星帆通信的到达率更稳定。
B.txt B2 [2026-10-15T10:05:00+08:00] 陈晨: 我继续负责短信接口接入，将按新供应商星帆通信的接口调整，计划在2026年10月18日前完成。
B.txt B3 [2026-10-15T10:10:00+08:00] 周宁: 我建议以后考虑增加邮件通知作为备用渠道，但这只是建议，本次会议尚未批准，也没有确定上线日期。
```

Gold：P(A2,星河项目)+；P(B2,星河项目)?；S(B2,A2)?；L(B2,A2)?；R(B2,A2)?；I(B2,SourceChange_B1)?。B2 的供应商及适配原文是已知字段事实，但不能唯一绑定具体项目变更；同一文档相邻不足。Comparison=BLOCKED，delta=null。A2/B2 owner=陈晨，deadline 各为 2026-10-14/18，B2.project 保持 null。未审核 A1/B1 时 formal decisions=0；B3 不生成获批变更或确定上线日期。UNKNOWN 的理由需指出缺少哪些桥接证据，而非“没有关系”。

### D08 显式项目别名与有界上下文

```text
A.md A1 项目说明：本工作区的“银梭”是“星河项目”的内部代号，两者在本期短信接入资料中指同一项目。
B.md B1 # 银梭
B.md B2 本节短信接入任务仅属于银梭。
B.md B3 [2026-10-16T09:00:00+08:00] 陈晨: 我负责签名参数适配，计划在2026年10月18日前完成。
B.md B4 # 天河项目
B.md B5 本节任务仅属于天河项目。
B.md B6 [2026-10-16T09:10:00+08:00] 陈晨: 我负责重试参数适配，计划在2026年10月19日前完成。
```

Gold：Alias(银梭,星河项目)+，证据 A1；P(B3,星河项目)+，证据 A1+B1+B2+B3；P(B6,星河项目)-，证据 B4+B5+B6 的独占上下文。B3/B6 的 M1.project 仍为空，派生关系不回写。标题作用域不能跨 B4 泄漏。不能仅靠“银梭”和“星河”词形确认别名。

## 4. 四个保留验收案例（evaluator-only Gold 设计）

### H01 缺负责人、缺年份，但修订身份明确

```text
A.md A1 星河项目任务 SDK-8：计划在10月14日前完成签名校验，负责人待定。
B.md B1 星河项目任务 SDK-8：本条为A.md的同一任务，替代其原计划，改为10月18日前完成签名校验，负责人仍待定。
```

元数据故意提供 document_date=2026-10-15、import time=2026-10-18，均不得补年。Gold：S(B1,A1)+；R(B1,A1)+；owner=null，deadline=null，deadline_precision=MONTH_DAY，角色 TASK_DEADLINE。Comparison=NOT_COMPARABLE，delta=null；不把 unknown 年份误当成本年或忽略缺日期而丢弃关系。

### H02 未审核变更、明确无影响与无证据推测

```text
A.md A1 [2026-10-15T10:00:00+08:00] 林悦: 星河项目今天正式决定把短信供应商从云桥科技改成星帆通信。
B.md B1 [2026-10-15T10:05:00+08:00] 陈晨: 我负责星河项目任务 AUTH-3 的兼容性验证；由于A.md记载的供应商更换，此任务必须补测签名兼容性，计划在2026年10月18日前完成。
B.md B2 [2026-10-15T10:06:00+08:00] 陈晨: 我负责星河项目任务 AUTH-4 的日志归档，计划在2026年10月18日前完成；AUTH-4无需因A.md的此次供应商更换做任何调整。
B.md B3 [2026-10-15T10:07:00+08:00] 陈晨: 我负责星河项目任务 OPT-5 的缓存优化，计划在2026年10月18日前完成。
```

fixture 前提：没有正式审核回执。Gold：I(B1,SourceChange_A1)+；I(B2,SourceChange_A1)-；I(B3,SourceChange_A1)?。证据分别为直接原因句、明确无影响句、仅同项目/日期而缺因果。authority=SOURCE_CLAIM_ONLY；查询已批准变更应为 AUTHORITY_NOT_ESTABLISHED，formal decisions=0。“正式决定”不能绕过审核；SOURCE_CLAIM 的关系支持也不是正式批准。

### H03 新旧资料冲突，更新时间不能消解

```text
A.md A1 [2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-9 的回调联调，计划在2026年10月14日前完成。
A.md A2 B.md的18日版本是草稿，不替代本条14日计划。
B.md B1 [2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-9 的回调联调，计划在2026年10月18日前完成。
B.md B2 本条是执行计划，替代A.md的14日计划。
```

两来源均 CURRENT、同等来源权威，无后续确认；B 的资料更新时间故意更晚。Gold：S(B1,A1)+；R(B1,A1)!，正证 A1+B1+B2、反证 A2；Comparison=BLOCKED/CONFLICT，delta=null，当前计划不能选择唯一日期。不得按 source mtime、event_time 或 ingestion_time 自动裁决。

### H04 来源改版、抽取/资料纠错与关系撤销

```text
A.md/v1 A1 [2026-11-01T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-6 的回调接入，计划在2026年11月20日前完成。
B.md/v1 B1 [2026-11-02T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-6，与A.md是同一任务；本条替代原计划，将2026年11月20日前完成改为2026年11月22日前完成。
B.md/v2 B1 [2026-11-03T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-6，与A.md是同一任务；更正B.md/v1，22日是资料更新时间的误录，任务计划未曾修订，仍在2026年11月20日前完成。
```

固定三个 snapshot，作为同一案例的状态序列，不能拆分到 Dev：

| snapshot | 输入目录/事件 | 原子 Gold 与比较 |
| --- | --- | --- |
| h04-s0 | A/v1、B/v1 当前，无 B/v2 | S(B/v1,A)+、R(B/v1,A)+；COMPUTABLE，BEFORE 20→22，+2；这是当时资料支持的判断 |
| h04-s1 | A/v1、B/v2 当前；旧 B/v1 可历史读回 | 旧 S/R 判定 lifecycle=STALE，不得发布为当前；S(B/v2,A)+、R(B/v2,A)-；新比较 NOT_APPLICABLE；当前 delta=null |
| h04-s2 | s1 加显式纠错事件：撤销旧 R，指向 B/v2 的纠错证据 | 旧 R lifecycle=RETRACTED；依赖旧 R 的比较不可用；新判定不变。历史保留 s0 的原判定及后来撤销，不声称旧 +2 仍是真实延期 |

追加事件的 recorded_at 不是计划生效时间。v2 产生新 observation ID；同一版本重复导入不得新增观察/判定；重开序列化后状态一致。不得通过把旧 B/v1 的证据 ref 指到 B/v2 来“修复”关系。读回旧字节可以成功但不能证明当前有效。

## 5. 覆盖补充与证据核验

上述 12 个语义案例之外，只增加同案例的**机械扰动断言**，不新增或重标业务 Gold：调换导入顺序、序列化往返、相同版本重放、同名跨 workspace、一个关键 proof 字符/hash 改错、来源目录缺失、撤销后重复重放。同样不调用 Provider。

- 有效 proof 必须按原 artifact 读回 Unicode `[start,end)`，验证 version/hash/workspace/field role；对 owner 需要原文责任角色及可信 speaker，不由关系推断补齐。
- 分别核验两端证据和桥接证据。D08 去掉别名/上下文证据时，结果须失去项目确认，不能靠旧缓存保持 SUPPORTED。
- 不同时间角色：H01 的资料/导入日期和 H04 的资料更新时间不得成为 deadline。D04 的 event_time 不成为修订生效时间。
- 非法引用返回 NOT_ASSESSABLE/INVALIDATED；关系证据过期返回 STALE。不能靠将所有错误都变成 UNKNOWN 掩盖工程缺陷。
- 结果演示必须展示至少 D04、D07、D08、H04 的实际结构化判定、引用原文、状态及日期计算/阻断原因，不能只交测试数量。本轮未产生这些系统输出。

## 6. 评分口径

### 6.1 Relation Precision / Recall

在固定 snapshot 和预声明原子问题集合 U 上，Gold 的 SUPPORTED 为正例。预测正例仅指可发布的 `ASSESSED/SUPPORTED/ACTIVE`。方向、predicate、端点、scope、snapshot 及完整必要 proof 都对，才是 TP。

- `Precision = TP / (TP + FP)`。Gold 为 REFUTED、UNKNOWN、CONFLICT、NOT_ASSESSABLE 或生命周期不可用时仍发布确认关系，均为 FP；证据不充分、跨版本偷换也为 FP。
- `Recall = TP / (TP + FN)`。每个 Gold 正例没被正确确认都是 FN，包括仅候选、弃答、错方向、错关系或错误 proof。错误确认一个 Gold 正例同时产生 FP 与 FN。
- 各 predicate 分开报，再报 micro；macro 只平均有 Gold 正例的类型，并列明覆盖。D/H 分开报。分母为 0 报 N/A，不能用 100%。反证正确率单独报告，REFUTED 不计正例 TP。

固定对 Recall 的分母只含这些已提供端点。端到端候选发现 Recall 则以案例所有 Gold 正例为分母，未被召回的正例也计 FN；另报 candidate recall，使检索缺失可定位。

### 6.2 False Merge Rate

只统计实际发布 SAME_TASK 的规范化唯一观察对，禁止以物理合并次数为口径（M2 本来就不物理合并）。

```text
hard_false_merge = SAME_TASK 确认而 Gold=REFUTED 的对数
unsupported_merge = SAME_TASK 确认而 Gold=UNKNOWN/CONFLICT/不可核验 的对数
False Merge Rate = (hard_false_merge + unsupported_merge) / 全部发布的 SAME_TASK 对数
```

同时报 hard_false_merge 的原始计数/比例及 unsupported_merge 计数。某 Gold SAME_TASK+ 但 proof 失效的发布也归 unsupported_merge。错误方向另按关系评分处罚。完全不确认任何对则 FMR=N/A，同时正例 Recall 为 0，不能过关。

### 6.3 Revision Accuracy

预声明 10 个修订问题：D01；D04 的 B→A、C→B；D05；D07；H01；H03；H04 的 s0/s1/s2。每题精确匹配以下整体结果才得 1 分：

`R 的 verdict/lifecycle + comparison status + 正确身份/修订前提 + 必要证据 + 可比时的 old/new 日期、精度、边界、delta；不可比/阻断时 delta=null`。

`Revision Accuracy = 完全正确的修订问题数 / 全部预声明修订问题数`。另报 Gold 修订 SUPPORTED 子集准确率，以及 COMPUTABLE 子集的日期/差值准确率，避免大量 UNKNOWN 题抬高总分。明确未变且可比的修订应输出 delta=0；未知、无修订、冲突、过期均不能输出 0。正负方向必须正确，不用数值接近给部分分。

### 6.4 歧义、未知与否定

- Gold UNKNOWN：允许候选或已判定 UNKNOWN，必须说明缺口且不发布确认/否定；候选未判定可获“不乱断言”安全分，但状态分类题不算正确。UNKNOWN 不进入正例 Recall 分母，却会惩罚无根据的肯定。
- Gold CONFLICT：需保留正反证并返回 CONFLICT；只说 UNKNOWN 不获状态正确分。Gold REFUTED：需有反证，不能用检索无结果代替。
- 对 UNKNOWN 发布 REFUTED 不会作为正关系 FP，但计 `unsupported_negative_count`，属于硬门槛失败。负面幻觉不能躲过 Precision。
- 单独报 unknown/conflict 状态准确率、unknown 上的无据确认率、无据否定数及答案覆盖率；`not found` 不等于 UNKNOWN 的业务理由。工程异常计数独立呈现。

### 6.5 必须同时满足的验收门槛

这是小型工程验收集，不据此宣称统计泛化能力。按 Dev 与保留集分别展示分子/分母，不仅百分比。

1. `hard_false_merge=0`、`unsupported_merge=0`、无据因果确认=0、虚假截止变更/差值=0、unsupported_negative_count=0、审核越权=0、失效/跨 workspace 证据泄漏=0。
2. 有定义的各关系 Precision=1.0；总体关系 Recall≥0.90，且每个有正例的关系类型至少确认一个正例。不能靠全部弃答通过。
3. Revision Accuracy=1.0；所有 UNKNOWN/CONFLICT/生命周期安全题状态与阻断理由正确。D04 的差值 0、H01 缺年及资料/事件时间不能作截止日期的断言全部正确。
4. 证据读回、幂等、版本变化及撤销序列全部通过；M1、Decision Memory、正式审核、供应商替代链的既有相关离线回归通过。
5. 若某实现切口只覆盖 SAME_TASK，则只报告该切口的子集结果，不能宣布上述 M2 整体验收通过。

错误归因按顺序保留：输入抽取/来源无效 → 候选未召回 → 关系语义错误 → 生命周期错误 → 修订/日期错误 → 回答越界；一次错误可触发多项安全计数，但规范化原子问题只计一次 TP/FP/FN。通过后也只能回答“所检范围内有证据支持的结果”，不能承诺全项目影响已穷尽。
