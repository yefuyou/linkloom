# LinkLoom v2 | 产品与工程终版规格说明

> **版本**：Final Spec v2.0 · 2026-11-19  
> **性质**：产品与工程实施基线，适合交给 Kiro / Codex / Gemini 做 PR 规划与开发；**不是已完成实现的声明**。  
> **整合依据**：原《LinkLoom v2 综合产品与架构 Spec v1.0》、Gemini 改写稿《LinkLoom v2 Technical Specification v1.1》、既有产品决议及 `master` / `feat/multi-agent` 只读源码与评测盘点。  
> **代码核查快照**：`master` = `b54e118e65a31a6eaf0e6899b9f49c76c33afc89`；`feat/multi-agent` = `21df480089bc9ea8fa4a9c0cb9ca5cefb6cd1caa`；PR #116、#117、#119 均仍为 Open，不能写作已合并。  
> **本文约定**：`[确认]` 为用户已经明确确认的需求；`[终版方案]` 为此次整合确定的目标产品/工程设计；`[已有基础]` 表示已核对源码存在，但**不等于**已通过最新 CI 或真实 Provider 验收；`[实施验证]` 为开发前必须就具体代码实现验证的有限问题。  
> **本轮操作**：只读调研与生成本地规格文档；不改动、不推送、不合并 LinkLoom 仓库；不运行收费模型或宣称新实验成绩。

---

## 0. 一页摘要：到底要做什么

**一句话定位**：LinkLoom 是**跨会议决策与待办记忆工作台**，解决“某次会上做了什么决定、之后怎么改的、任务谁负责、为何延期、当前还算不算数”的连续问题。不是单场转写软件，也不是展示多个 Agent 头像的聊天玩具。

**首版范围 B `[确认]`**：只做**文本会议跨会议闭环**：

`粘贴/上传纪要 → 来源版本 → AI 提取草稿 → 人工确认 → Decision/Task/Glossary 正式记录 → 跨会议演变视图 → 统一 Ask → 原文证据 → 可核查 Trace/费用`。

首版不做音频上传/ASR/实时录音/音频时间轴；不把真实企业 RBAC、多平台派发、完整模型路由比价或大型实时分析平台作为上线前置条件。

### 0.1 必须遵守的 12 条不变量

1. **普通纪要与结构化发言文本均可导入**；缺失 speaker、发言时间、会议日、负责人、期限或因果依据一律标记未知，不编造。`[确认]`
2. **会议日期可空**，有明确的「选择日期 / 填充今天 / 清空」按钮；填今天是用户主动操作，不是自动回填导入日。`[确认]`
3. **重复正文提醒，由用户选择“查看已有”或“仍独立导入”**；网络重发依 request ID 保证幂等。`[确认 + 终版方案]`
4. **任何 AI 提取仅产生 Draft**。Decision、Task、Glossary 正式授权必须由用户明确确认；**不允许“高置信度自动审批”**。支持批量选择确认但逐条经过校验。`[确认]`
5. **内容已确认不等于生效时间已确定**。无日期的正式决议允许存在，`AS_OF` 和 `CURRENT` 不得假造时间顺序。`[确认]`
6. 任务跨会更新先给候选关联提示，**未经审核者同意不得自动合并**；新任务与旧任务事件各自有历史收据。
7. **证据来源必须是 SourceVersion 的原文**；Hash/偏移一致是必要条件，不代表推论因果已成立。
8. **只读图谱/索引是投影，不是第三个决议权威库**；DecisionStore 与 TaskStore 各自承载正式业务状态。
9. **对外只有一个 Ask 行为契约**；确定性直查不必用模型，复杂调查复用 LangGraph，Router 的已验证信息不重复解释。
10. **Host 负责权限、Scope、Schema、工具与写边界**；模型、会议原文、检索结果都不能给自己授权。
11. **Trace 不造数**：预留预算、Provider 报告用量、估算费用与真实账单分开；未知就是未知。
12. **复用现有成熟能力，不新建并行架构**；先完成用户闭环，再基于真实质量/耗时/成本数据优化。

### 0.2 首版交付成功长什么样

用户可以在一个 Workspace 中导入三次会议；审核并确认从供应商 A 改为 B、一个联调任务负责人和期限发生变化；询问“现在用谁”“之前为什么改”“某天状态如何”“延期是否因为换供应商”，得到有来源的确定结论或明确的未知/冲突。打开一次真实 Ask，可以看到工具/模型调用、状态、预算以及可追溯的失败原因。**不能用编造 Demo 回答代替真实执行。**

---

# 第一部分 · 产品设计（先说明用户体验，再说明技术）

## 1. 信息架构：三主视图与一个设置抽屉

采纳 Gemini v1.1 简化导航的方向，但**“例外优先展示”不等于“仅审两种例外”**。

| 主视图 | 用户要完成的事 | 内部区域或页内切换 | 必须呈现的空/异常状态 |
|---|---|---|---|
| **① 会议与审核** | 导入、查看来源、提取、修正与确认 | 会议列表；导入/预览；会议详情；原文与候选并排；审核队列 | 无来源、解析失败、部分成功、未配置模型、证据模糊、来源版本失效、审核权限不足 |
| **② 决议与任务演变** | 看当前结果、历史变更、待办进展 | 决议卡片/时间线；任务看板/TaskEvent；“时间未知”“待核验”单独区域；来源下钻 | 无正式权威、冲突、来源撤回、时间未知、跨会议关联待确认 |
| **③ Ask 工作台** | 提问、调查、引用跳转、看运行状态 | 统一问答；结果卡；证据列表；可展开 Agent Telemetry Drawer | 澄清、拒答、部分调查、模型不可用、超预算、来源不可用、恢复中 |
| **全局设置抽屉** | 管理 Workspace、术语、偏好与 Provider | Workspace 切换、Glossary、工作区显示/回答偏好、模型连接配置 | 权限与连接错误、撤销成功、未保存变更 |

- Workspace **当前身份/工作区名称须始终可见**，切换入口可以在顶区，设置抽屉提供更多管理项；不为视觉极简掩盖重要的隔离边界。
- 审核页面可把**冲突、任务修订、证据不足、时间未知、来源失效、权限失败**等项目置顶，但所有候选均能被用户查看与主动确认；正常项不自动写入正式权威。
- 视觉上优先复用现有 Web UI，页面数和 API 由现有实现映射，**不必重搭前端框架**。

### 1.1 三主视图布局草图（逻辑示意，不是已实现截图）

```text
顶部： [Workspace: XXX ▾]   [会议与审核] [演变看板] [Ask]   [设置 ⚙]

会议与审核
 ├── 会议列表 / 最近导入 / 待审核总量
 ├── [粘贴/上传] → 来源预览与元数据 → 导入并提取
 └── 会议详情：左侧原文 ↔ 右侧摘要/Decision/Task/Glossary 候选
             [编辑] [确认] [忽略] [查看旧记录] [批量确认已选]

演变看板
 ├── [当前] [历史] [时间未知/待核验]
 ├── Decision 卡：当前值 / 时间 / 被替代关系 / 原文依据
 └── Task 卡：负责人 / 期限 / 状态 / 每次 TaskEvent 来源

Ask
 ├── 提问输入 + Workspace Scope + 可选明确时间
 ├── 回答：确定结论 / 证据 / 冲突 / 仍未知
 └── [调用链 ▾] Supervisor/Worker/Tool/Checkpoint/Token/预算/失败
```

## 2. 主流程 A：文本会议导入

### 2.1 输入、字段、含义

| 用户输入/系统字段 | 必填 | 业务规则 |
|---|---|---|
| `workspace_id` | 是 | 来自 Host 当前可信上下文；提交时重新校验，不信任页面传入其他 Workspace |
| 标题 `title` | 是 | 手填；文件名可预填建议；只改标题不重抽正文 |
| 会议日 `meeting_date` | 否 `[确认]` | 未填为 `null`；允许**填充今天**、手选、清空；记录用户选择的日期来源 |
| 会议正文 `content` | 是 | 粘贴或 UTF-8 `.md` / `.txt`；保存原文，不能因缺发言时间戳拒绝 |
| `input_kind` | 系统生成 | `paste/file`；不作为会议事实证据 |
| `source_id` / `version_id` | 系统生成 | 来源身份稳定、正文版本不可变 |
| `content_sha256` | 系统生成 | 对提交后保留的原文做稳定 Hash；用于**完全相同正文**提示，不用相似度强合并 |
| `request_id` | 客户端提交 | 同一创建操作重发应返回既有结果；同 ID 不可提交另一种正文 |

首版可继承现有标题 180 字符、正文 256 KiB 限制，且 UI 明示。长文档高级切片优化放 B2；后台已有分段/尝试机制应复用。

### 2.2 格式与 Evidence

- 普通段落纪要：记录原文行号、字符区间、`quote_sha256`；`speaker/spoken_at` 留空，不填入伪时间轴。
- 结构化发言：仅提取原文本来存在的 speaker、时间、时区；部分行缺字段时允许这些字段单独为 `null`。
- 引用定位：Host 在**原始 SourceVersion** 上确认原话位置并生成可验证引用；模型只能提出 quote/线索。若重复语句造成无法定位，进入“引用位置待确认”，不能悄悄取第一处。
- 原文与摘要分离：**摘要是浏览信息，不是正式结论的证据**。
- 未来音频只扩展证据来源 `start_ms/end_ms`，B1 不新建音频或 Utterance 物理表。

### 2.3 重复检测 `[确认]`

同一 Workspace 的 Hash 完全相同，出现两个选择：**“查看已有会议”** 与 **“仍作为独立会议导入”**。用户选择独立导入后产生新的 Source，与原 Source 不共享审核结果；不能因为文件名相同就合并，亦不能把不同 Workspace 的来源暴露给用户。

技术区分：

- **业务查重**：在实际创建前提示。提示不应消耗最终创建 `request_id`。
- **请求幂等**：带 `request_id + payload_fingerprint` 的真实创建；网络丢包或双击重复到达时返回同一 `source_id`，不弹第二次提示；同 ID 不同 payload 返回冲突。
- **新版本**：仅用户从**既有会议详情点编辑**时产生；从“新建会议”进来即使正文类似，也是另一 Source。

### 2.4 日期、正文与标题修改：首版只用一个修订锁

**决定采用 `[终版方案]`：SourceVersion 只管正文，Source 级一个 `revision` 统管标题/日期/正文的一次编辑；只追加最小审计记录。** 不增加独立 MetadataRevision 业务域，也不维护两个乐观锁。

| 变更 | 内容版本 | 修改审计 / 影响 |
|---|---|---|
| 只改标题 | 不变 | 增加 Source `revision`，不调用模型 |
| 只改 `meeting_date` | 不变 | 增加 Source `revision`；只找可证明依赖该会议日期的派生项并标记待核对；不自动改业务期限 |
| 只改正文 | 生成新不可变版本 | 原版本关联候选失效/重核，正式记录依证据生命周期复核，不静默删除 |
| 同时改标题/日期/正文 | 单请求/单事务 | `expected_revision` 一次校验后提交完整结果；不可多接口半途更新 |
| 完全无变化 | 不变 | 不重抽、不重复计费 |
| 并发更新 | 不提交 | `409 VERSION_CONFLICT`；UI 保留未保存的编辑内容 |

**日期变化不等于决策生效日变化**。`meeting_date`、`ingested_at`、`effective_from`、`deadline` 必须是不同业务字段。旧版记录来自“明天/下周一”且确实依赖会议日期时，先在审核 UI 显示待核验影响；B1 不做一整套自动重算器。没有时间依赖的决定、任务不受影响。

### 2.5 导入后的状态和错误恢复

| 可见状态 | 语义 | 用户操作 |
|---|---|---|
| 编辑中 | 尚未创建 Source | 修改、取消 |
| 待处理 | 来源已持久化，等待提取 | 查看来源、离开页面 |
| 提取中 | 异步按来源版本处理 | 查看进度/已完成片段 |
| 草稿就绪 | 有待审候选，但没有自动权威 | 前往审核 |
| 部分成功 | 一些段落有效，一些失败或跳过 | 查看已完成候选；对合格失败片段重试 |
| 提取失败 | 来源仍保存 | 查看错误、配置模型、有限重试 |
| 版本失效/待核验 | 来源变化导致候选或正式引用需重新验证 | 进入影响列表，不覆盖旧版权威 |

故障要求：空正文/乱码/超限不落盘；网络响应丢失先按幂等查询；数据已保存但队列失败允许恢复排队；Provider 结果未知**不盲重发**；失败/部分成功保留合法的原文和已成功分段，不随重试重复创建 Source 或重计已确认任务。

## 3. 主流程 B：AI 提取草稿与统一人审

### 3.1 AI 产物只做四类

1. **会议摘要**：阅读辅助，不进入正式权威。
2. **Decision Draft**：主体、决策槽位/关系、目标值、原文证据、来源版本、可选生效时间与时间依据。
3. **Task Draft**：行动描述、可选 Owner/Deadline/Status、原文证据、可能关联的正式 Task ID。
4. **Glossary Draft**：术语、别名/规范名称、解释及来源；确认后用于检索/实体消歧，不自动覆盖正式业务事实。

现有 `fact_memory/` 不删除，但 B1 不把 Fact 做成第四套必须审核的新模块；避免半接入、重复记忆权威。

### 3.2 页面交互

- 左侧原文，右侧摘要和候选卡；点击任何字段的引用均能定位到原句。
- 每条候选有**编辑、确认、忽略、标记待核验**，显示证据是否可验证、来源版本、相似正式记录、冲突原因和字段未知状态。
- 默认按“需关注”排序，但**正常候选一样需要明确选择并确认**；可以多选批量确认，批量执行逐条结果，任何单项校验失败都不影响其他已成功确认项的审计一致性。
- LLM 输出的“confidence”只用于提示，不得作为越过人工审核或证据校验的依据。
- 来源已变或权限已收紧时，确认按钮不可越过 Host 核验；草稿不能在后台自动转为正式 Decision/Task/Profile。

### 3.3 Decision 审核

| 情况 | 交互及权威行为 |
|---|---|
| 从原文明确得出决议 | 审核者确认内容，生成正式 Decision 审核收据 |
| 仅是建议、猜测或待商议 | 保持待审/驳回，不伪造成“已决定” |
| 生效时间未知 | **允许确认内容**；可“留空 / 手选 / 填充今天”，不强制补日 `[确认]` |
| 可能替代已有决议 | 展示旧值和来源；审核者明确选择替代/独立/待核验 |
| 同一主体冲突 | 展示双方证据；不从“最后导入”推断 CURRENT |
| 已确认记录的来源失效 | 保留审计收据并标记复核；不静默删掉、也不默认回退旧 CURRENT |

**“填充今天”**是用户对具体字段的主动填写。在审核决议时，它表示**用户指定的生效日**，需独立记录 `time_basis=USER_TODAY`，不是来源记载的生效日。

### 3.4 Task 审核与跨会议身份

- 抽取的新 Task 必须经人审写入正式 `TaskStore/TaskEvent`，不能只落在旧 `ll_meeting_task_review` 旁路并自称正式任务。
- 先复用 `SameTaskHintService` 提供相似/已关联任务提示；用户选择**新建任务 / 关联既有任务并追加事件 / 暂不确定**。
- 关联已有任务时可更新 Owner、Deadline、Status，但所有变化要有来源和收据；缺失字段保持未知，不用会议日、提取人、默认状态编造历史事实。
- `TaskMergeService` 与已有 TaskEvent 负责正式合并/修订，**相似度高不代表用户已经批准合并**。
- 对明确只有计划、没有执行进度的原文，不自动推断任务“已完成”。

### 3.5 Glossary 与工作区 Profile

- 保留 Tier 2 长期记忆的概念，B1 只做**术语别名与 Workspace 问答/展示偏好**最小闭环。
- 术语候选也须人工确认后方可成为稳定规范；提供查看、编辑、撤销和跨请求复用。
- Glossary 可帮助召回和实体候选，但**不能跨 Workspace 合并实体，也不能替代正式 Decision 证据**。
- 个人身份、员工私人资料和组织级 RBAC 后置；“项目规则已拍板”属于 Decision，不能仅藏入 Profile 偏好中。

## 4. 主流程 C：跨会议演变看板

| 子视图 | 默认显示 | 点击展开 | 未知与冲突处理 |
|---|---|---|---|
| **会议列表** | 标题、会议日或“未知”、处理状态、待审核数 | SourceVersion、原文、提取历史、差异 | 来源撤销仍保留授权范围内的审计状态 |
| **决议** | 有充分依据的当前权威项 | 时间有效区间、替代链、确认收据、原文引用 | “时间未知 / 时态未决”**单独区域**，不混入按时间排序的 CURRENT |
| **任务** | 已确认任务、Owner、期限、当前状态 | 跨会议 TaskEvent、关联会议、每次更改依据 | 未知字段原样显示；冲突关联待审核，不自动并单 |

Graph 和 Timeline 作为**已确认记录的只读投影**。图上出现 A→B 关系不等于模型拥有新的写权限；Graph 事实源仍是 DecisionStore / TaskStore。把“来源修订”和“后续会议真的改了决策”区分显示。

## 5. 主流程 D：统一 Ask

### 5.1 三种典型问题

- **直接查询**：“银杏当前支付网关值是什么？”“这项任务现在谁负责？”
- **时态与历史**：“10 月 15 日之后决议改过几次？”“当时由谁负责？”
- **跨会议调查与因果**：“改供应商之后为何延期？确实有因果关系吗？”

### 5.2 结果卡片必须包含

1. **结论状态**：`COMPLETE / PARTIAL / UNKNOWN / CLARIFY / CONFLICT / BLOCKED`（外层状态映射，具体枚举兼容现有代码）。
2. **答案正文**：区分已证实事实、必要的推断和未知；引用显式编号。
3. **证据列表**：Source、版本、原文 Quote、可见范围、跳转位置。
4. **时间上下文**：CURRENT 或 AS_OF 及其已验证范围，避免导入时间冒充业务时间。
5. **冲突/缺口**：多条权威有争议时两边都展示，不挑一个最像的。
6. **调用链抽屉**：仅在确有运行记录时显示真实 Router、Supervisor、Worker、Tool、Reviewer、预算和失败，不通过假进度或假数据填充。

**来源失效**：不继续引用已撤回/越权来源，也不自动回退到已被替代的旧决议；如果因此不能确定 CURRENT，返回“需复核”而不是伪确定值。

---

# 第二部分 · 领域与 Agent 工程

## 6. 领域数据、来源权威与双层记忆

### 6.1 数据对象及单一权威关系

```text
Workspace
 ├─ Source (会议元数据; one revision)
 │   ├─ SourceVersion (不可变原文及 Hash)
 │   │   ├─ Segments / EvidenceSpans (来源位置; speaker/time 可空)
 │   │   └─ ExtractionRun → Draft Candidates
 │   └─ SourceUpdateAudit (最小只追加变更日志)
 ├─ DecisionMemory (正式决议单一逻辑权威)
 │   ├─ Dated Decision → TemporalDecisionStore / supersedes / AS_OF
 │   └─ Confirmed Undated Decision → 审核收据与权威身份; 尚无可确定生效时间
 ├─ TaskStore → Task / TaskEvent / Approval Receipt
 ├─ ProfileMemory → Confirmed Glossary + Workspace Preferences
 └─ Derived: BM25 / Dense / TemporalGraph / UI Projections

Runtime only: LangGraph Checkpoint + Worker Receipts + ToolLedger + Provider Budget + Trace
```

**两个持久业务记忆层**：

- **Tier 1 Episodic/Temporal**：来源、决议、任务、历史事件、审批收据、时间与替代关系。
- **Tier 2 Persistent/Profile**：经审查的术语、别名、偏好、适用范围及撤销。首版仅最小子集；不是第三份业务决议库。

### 6.2 无日期正式决议：不使用 Gemini 的 UndatedPolicyDomain

**根本区分**：一条“11 月会议确认采用 Wrenwell，但不知何时生效”的决定，与一条“不带生效日期的企业通用政策”不是同一业务分类。**时间未知只是 Decision 的一个属性，不是 Policy 类型。**

逻辑状态：

| 内容审核 | 时间依据 | 时态查询资格 | 展示 |
|---|---|---|---|
| 未确认 | 任意 | 否 | Draft |
| 已确认 | `UNKNOWN` | 不能确定 AS_OF/CURRENT | Confirmed · Time unknown |
| 已确认 | 来源明确或用户确认日期 | 依据现有时态校验 | Dated Decision |
| 被替代/撤销/证据失效 | 任意 | 按历史与当前资格区分 | 历史/待复核，不静默回滚 |

**终版逻辑方案**：正式 Decision 领域只有一个审核入口和权威身份。已有 `TemporalDecisionStore` 保留 `valid_from NOT NULL` 和现有时态不变量。若需要最小辅助表持久化“内容已确认，时间未知”，该表必须属于 **DecisionMemory 同一逻辑聚合**，并通过单次审核事务、稳定 `decision_ref`、状态约束、独立审核收据及原文 Evidence 强制关联。其行**不能同时当作 Temporal CURRENT 行**。以后补日期只能通过受审修订/迁移，保留旧确认收据并原子切换到可查的时态投影。具体表名、索引及迁移 SQL 以实际 schema 对照实现，但不能把 `valid_from` 简单改 NULL 并破坏旧查询。`[实施验证 P0]`

**查询语义**：

- `AS_OF(t)`：只计入在 t 时点有足够证据和有效区间的记录；未知日期项可作为“存在已确认内容但生效日未知”的附注，不用于断言“当时就是它”。
- `CURRENT`：有唯一、可核查的时态权威时返回明确当前值；若存在时间未定内容可能改变当前判断，则给出证据与“当前待核验”，不凭导入、审核或文档排列顺序下结论。
- `supersedes`：明确替代关系可人审记录，但替代的**业务事实**与生效时间轴分别验证；未知日期不能假造 `valid_to`。
- 记录撤销：不自动把被它替代的旧值复活为 CURRENT。

### 6.3 现有资产复用表

| 对象/代码 | 终版动作 | 理由 |
|---|---|---|
| `ui/product.py` 的 Source/Version、导入、重试 | **EXTEND** | 接普通文本、可空日期、重复提示、单 revision；复用 `ll_ingestion_segment/attempt` |
| `semantic_ingestion/` 的 EvidenceSpan | **REUSE + EXTEND** | 保持 char/line/quote Hash 校验；支持没有 speaker/time 的来源 |
| `decision_memory/` | **REUSE + EXTEND** | 正式 Decision 与时间未知受审状态，不改写已有时态事实 |
| `tasks/`, `tasks/same_task_hints.py`, `tasks/merge.py` | **REUSE / CONNECT** | 已有任务关联与人工合并基础；不要再写另一套匹配服务 |
| `ui/meeting_task_review.py` | **MIGRATE WRITE PATH** | 现有审核旁路不等于正式 TaskStore，历史数据保留 |
| `memory/` 生命周期与 `ui/feedback_memory.py` | **EXTEND** | 最小 Glossary / Workspace Preference 的审批与撤销 |
| `indexing/`, `retrieval_v2/` | **REUSE** | BM25/Hybrid/Dense 已有，优化依基线证据，不盲加重排 |
| `temporal_graph/` | **REUSE AS READ-ONLY PROJECTION** | 可复建关系视图，不能成为另一份决定权威 |
| `fact_memory/`, `experience/` | **KEEP OPTIONAL / CLEAN ONLY AFTER REFERENCES** | 不按文件夹名盲删功能与历史数据 |
| `orchestration/`, `runtime/`, `tools/` | **REUSE + THIN ONLY AFTER PARITY** | LangGraph、Runtime、ToolLedger、回执/恢复已有能力 |
| `ui/branch_synthesis.py` | **REUSE** | 已有确定性证据/Schema 合并，不额外生成 LLM 成本 |
| `observability/`、`ui/task_provider.py` | **EXTEND PROJECTION** | 已有 events、usage、预算与 trace，仅补统一关联与结算显示 |

## 7. Ask Runtime、Context Engineering 与 Tool Calling

### 7.1 真实架构：先路由，后按需编排

```text
User Ask
    │
    ▼
Host: 可信 Workspace / 操作权限 / 输入与来源资格
    │
    ├── 明确的结构化精确查询 ──────────► Verified DB Lookup (0 Router LLM)
    │
    └── 自然语言请求
           │
           ▼
   Structured Router (一次语义理解，输出主体、意图、时间、候选)
           │
           ▼
   Host Admission (实体、权限、时态、Schema、只读、安全边界)
           ├── DIRECT / AS_OF ──────────► Verified DB / Temporal Lookup
           ├── CLARIFY / UNSUPPORTED ───► 明确澄清 / 不支持 / 失败
           └── INVESTIGATE
                  ▼
         LangGraph Supervisor (复用 Router 结果)
                  ▼
         受约束 Task DAG + 并发/预算 Gate
                  ▼
         Decision / Task / Evidence Workers (现有 Runtime + ToolLedger)
                  ▼
         Source / Evidence / Claim Reviewer
                  ▼
         Deterministic Synthesizer (`branch_synthesis.py`)
                  ▼
         Answer + Evidence + Unknowns + Trace
```

**注意**：LangGraph 主工作流可以有恢复与重新规划循环；**无环约束针对单轮提交的任务依赖 DAG**，不是要求整个状态机绝对无循环。Router 解析意图但不为 Supervisor 重做 DAG；Supervisor 不无故二次 Selector。是否需要模型语义 Review 要依据实际失败，Hash 验证本身不能证明自然语言因果推论为真。

### 7.2 路由与 Host Gate

- **Router**：对自然语言判别 Current / As-of / Evolution / Task / Investigate / Clarify 类业务动作，输出结构化范围和实体候选；这些是**产品概念**，不强制代码引入另一个与现有枚举冲突的 API。
- **Host**：验证真实 Workspace、模型工具白名单、Schema、引用来源、时间参数与授权；模型自称“管理员”不得改变权限。
- **内容安全**：确切权限/工具执行阻断由确定性机制实现，可计为 0 LLM 调用；**不能宣称确定性代码零 Token 识别全部 Prompt Injection**。恶意文本判别需要分层测试，不能靠“查到了某个关键词”声称防护完毕。
- **失败**：Router 无法有效解析时提供澄清/受限失败，不用旧 regex/Legacy 隐式改绑 CURRENT；当前 #119 尚待集成审查。

### 7.3 Supervisor 与 Worker 责任边界

| 节点 | 做什么 | 什么时候需要模型 | 输出/失败保护 |
|---|---|---|---|
| Direct Lookup | 精确授权读正式记录 | 不需要 | 来源版本有效、时态语义成立 |
| Supervisor | 为复杂请求拆解、派任务、受限再规划 | 需要语义拆题时 | Task ID、依赖、派发理由、预算约束 |
| Scheduler | 依赖波次与并发派发 | 不需要 | 已完成回执复用、无重复执行 |
| Worker | 检索、调用只读工具、验证证据、生成候选陈述 | 需要解释/判断时 | ToolLedger、EvidenceRefs、状态回执 |
| Reviewer | 引用/Schema/事实冲突/时态检查 | 确定性为主，复杂语义可选模型 | 未通过则拒答、重查或显示缺口 |
| Synthesizer | 合并已核验结果、冲突与未知 | **已有实现为确定性** | 不替无证据的结论背书 |

不能把所有上述逻辑节点写成“固定 3 次模型请求”。每次真实 Provider 调用才算一次模型调用。Worker 调用多个只读工具并不自然等于多个模型调用。

### 7.4 Context Engineering：保留可复查的证据，而不是机械截短

- 复用 `context/assembler.py`、`runtime/context_engineering.py` 和 `WorkingContext`，明确用户目标、权限范围、时间条件、未解决问题与预算。
- 将已核验的来源证据放入 Prompt，可将大工具响应变为 Artifact Ref，需要时通过只读工具再读取；摘要和推断不能偷偷取代原文。
- 压缩优先移除重复、无关的操作日志与无效工具输出，保留原始用户意图、确切 EvidenceRef、否定证据、冲突状态和未完成子目标。
- 限制应通过当前已有的可配置 Context Budget、字节/token 估算及 provider 实际上限实现，**不凭空新设“每段必须 ≤512 Tokens”** 的硬约束。
- Context 复用和缓存若命中权限/版本变化，必须重新校验；Token/缓存统计可能由供应商缺失，不能补 0。

### 7.5 Runtime 与工具可靠性

- 每次 ToolCall 的 `tool_id/arguments` 应按强 Schema 验证并约束 Scope；写操作原则上不暴露给 Ask Worker。`ToolLedger` 保留 call ID、执行状态、归一化回执、错误和幂等身份。
- 对同一个搜索反复调用，如果 Query/Scope/证据没有产生新的信息增益，Host 按预算/循环保护抑制，而不是固定“任何第九步都错误”。
- 请求明确失败且幂等只读时，可按已配置有限退避；**Provider 超时且结果未知，保留 `PROVIDER_UNCERTAIN`，不盲目重发**；写操作尤其依赖业务幂等键与回执核对。
- LangGraph Checkpoint 和 Worker 已接受回执共同保障恢复，不能仅凭 `checkpoint` 存在就宣称 exactly-once 外部执行。
- 不再重复造一个 Coordinator、ToolLedger、BudgetManager；任何删层应先逐职责验证功能等价与回归测试。

## 8. Observability：把已有数据串成可验证的一次 Ask

### 8.1 已有落盘资产 `[已有基础]`

| 层 | 实际字段/载体 | 终版增量 |
|---|---|---|
| `observability/events.py` + `events.jsonl` | `event_id/run_id/thread_id/seq/event_type/node_name/status/duration_ms/input_ref/output_ref/error/attributes`、脱敏信息 | 统一关联 Router/多 Agent 阶段、事件与人工审核 |
| `runtime/checkpoint.py` | `checkpoints.sqlite`、状态、Attempts、AgentTurns、ModelExecutions、ToolLedger、Usage | 不替换 checkpoint；为 UI 提供只读聚合 |
| `orchestration/runs.py` | `langgraph.sqlite`、`orchestration.json`、任务 DAG/Blackboard/Interrupt | Trace 查阅、权限核验与恢复一致 |
| `ui/task_provider.py` | `budget.json`：请求预留数/Token/费用、attempt、请求与响应 Hash、Usage、outcome | 从原回执关联实际 Usage，不再把预留计为实际 |
| `orchestration/trace.py` | 只读组装 Supervisor、Worker Attempts 与预算预留 | 接入 Router 阶段、Provider 实际 token/费用状态与评测 case |
| `ui/static/product.js` | 已有调用链视图 | 继续作为 Ask 内的展开抽屉，而不是另造独立平台 |

既有入口 `GET /api/product/ask/runs/{run_id}/trace` 已存在于 `feat/multi-agent`。**但其页面当前主要展示预留 Token/费用**，不是完整真实费用账单。

### 8.2 统一逻辑 Trace Schema（目标契约，不代表这些字段都已经持久化）

```json
{
  "schema_version": "ask-telemetry/v1",
  "run_id": "<existing_run_id>",
  "workspace_id": "<authorized_workspace_id>",
  "status": "COMPLETE|PARTIAL|UNKNOWN|FAILED",
  "total_duration_ms": null,
  "provider_calls": {
    "attempted": null,
    "completed": null,
    "failed": null,
    "uncertain": null
  },
  "usage": {
    "reported_input_tokens": null,
    "reported_output_tokens": null,
    "reported_total_tokens": null,
    "reported_cached_input_tokens": null,
    "reasoning_tokens": null,
    "reserved_input_upper_bound": null,
    "reserved_output_upper_bound": null
  },
  "cost": {
    "estimated_actual_usage_usd": null,
    "reserved_upper_bound_usd": null,
    "pricing_basis": null,
    "billing_status": "KNOWN_ESTIMATE|PARTIAL|UNKNOWN"
  },
  "nodes": [],
  "failure_codes": [],
  "eval_case_id": null
}
```

> **这些 null 是契约示例，不是实际测量。** 真实页面用读取到的值显示；无法获得的 Usage 要用“未报告/未知”，不得用假 Token、假耗时、假模型名或“0 美元”占位。

### 8.3 产品页：调用链抽屉

- 概览：总耗时、请求尝试/成功/失败/未知、真实报告 Token、费用估算/预留上限，标注模型和价格快照。
- 轨迹：Router、Host、Supervisor、Worker、工具调用、Reviewer、Synthesizer 的实际执行事件；某节点没执行就不显示假节点。
- 工具：显示脱敏后的工具 ID、参数摘要/Hash、Outcome、Evidence ID、失败原因；需要查看原文时仍走权限查询。
- 恢复：展示 checkpoint、分支 `REUSED/UNCERTAIN/CONTINUABLE/WITHHELD` 等状态。
- 成本：分开 `Provider reported usage`、`price-based estimate`、`reserved ceiling`；cache token 属于输入子集，不可额外加进 total。
- 若 Agent 不涉及模型，显示“本次无 Provider 调用”而不是虚构 Router 和 Token。

### 8.4 统计口径

- **LLM Call Count**：真实 Provider 尝试次数，包括失败/结果未知；通过成功回执统计成功数。
- **Total Latency**：Ask 接收到终态的壁钟耗时；并行分支耗时不能简单求和充当端到端耗时。`queue/retrieval/provider/tool/review` 可在有数据时分列。
- **Usage**：以 Provider 回包为准；仅有预算预留时不宣称真实 Token。
- **Cost Per Success**：同一评测批次**全部尝试请求的已知估算费用 + 不确定预算上界（分别显示）** / 成功且通过验证的 Case 数。分母为 0 时返回 `N/A`，不记录为 0 成本。
- **Privacy**：不把原文、API Keys、敏感信息直接吐给一般日志和 Trace；工作区授权和来源生命周期同样适用于 trace 查看。

## 9. Unified Agent Evals：整合，不重建

### 9.1 仓库现有 Gold 与 Runner 的正确口径

| 数据资产 | 真实规模 | 类型及注意事项 |
|---|---:|---|
| `benchmarks/datasets/m1_team_decision_eval_seed/dataset.jsonl` | 30 Case | 6 Workspace 跨会议测试；`golden8_manifest.json` 是其中 8 条冻结子集，**不能称“30 Frozen”** |
| `tests/eval/trajectory_eval_v1/cases.jsonl` | 41 Case | Tool / 参数 / 预算 / checkpoint 等**确定性轨迹评测**，现有 grader 不调用 Judge |
| `tests/fixtures/real_agent_reliability/public_dev.json` + `public_frozen.json` | 9 Dev + 16 Frozen | Gold 分文件存放，不重复计算；不是“25 个 Live” |
| `tests/fixtures/real_agent_reliability_live/public.json` + 对抗夹具 | 5 + 1 | 真实 Provider 相关路径的公开案例、对抗用例；与 Frozen 的独立性须按 manifest 检查 |
| `benchmarks/ami_tasks/gold*.jsonl` | 11 + 9 + 6 | 真实 AMI 会议待办抽取与人工核验/LLM Judge，属于任务抽取专项 |
| `tests/routing/*.jsonl` | 60 + 13 + 7 | 路由与时间/实体绑定专项 |

已有 `evaluation/runner.py`、`evaluation/trajectory/{runner,grader,metrics}.py`、`scripts/eval_routing.py`、`scripts/eval_tasks.py`、检索与规模 Benchmark。**真实模型评测**与**离线确定性单测**分开；已有 AMI DeepSeek Judge，不表示每个 Agent E2E 场景已采用 LLM Judge。

### 9.2 不丢掉已知的负面结果

- Retrieval V2 离线 30 Case 的 Dense MRR = 0.9500、Hybrid MRR = 0.9333、BM25 MRR = 0.8694；这不是最终回答正确率。
- AMI ES2003 + ES2004 留存正式对照：Baseline Task Recall **10/15**、LinkLoom v2 **7/15**；不能拿开发集 11/11 对外宣称全量最优。
- LongMemEval Oracle 50 题，新旧对照 **21/50 → 28/50**；使用 Oracle 来源，不能声称完整检索链路提升同等幅度。
- `feat/multi-agent` 新增编排不等于已经有**单 Agent vs 多 Agent 同题有效成本/完成率报告**。该对照目前尚欠缺。

### 9.3 最小统一评测记录，而不是新造框架

利用现有 Runner/Scorer，增加一个薄的**Result Adapter/Index**，将已有运行记录对齐，而不是重写所有评测脚本。统一 `case_id, dataset_sha256, split, code_sha, run_id, config_fingerprint, provider/model, outcome, scored_denominator, scorer_version, trace_ref, latency_ms, usage_state, cost_state, failure_reason`，允许某指标 `NOT_EVALUATED`。同一 Case 的历史结果不覆盖，新报告明确原始来源。

评分分层：

1. **L0 确定性**：Schema、工具参数、Host 权限、引用 Hash、来源版本、路由/任务标签、工具轨迹、安全与预算。
2. **L1 任务结果 Gold**：当前/历史答案、Task/Decision 关系、应弃答项、证据召回和冲突识别；明确样本分母。
3. **L2 语义 Judge（可选）**：只对无法靠确定性对照解决的事实陈述/因果忠实度做 blind Judge。Judge 要给出依据、拒答选项、与少量人工校准，不把“引用 Hash 通过”当忠实度 100%。

### 9.4 可比较实验：最小优先顺序

**首先只做一组高价值受控对照**，不强制立即花钱完成 Gemini 改写稿中的三轮昂贵实验。

- **C0：现有单 Agent vs 多 Agent**，冻结相同来源、同一 Case、相近可用预算、模型与评分器，比较完成率、Grounding 合规、弃答正确率、P50/P95、尝试调用数、费用及失败归因。若模型配置不同，明确分为架构差异与模型差异两次实验。
- **C1：Router→Supervisor 重复理解前后**，基于改动真正发生的 PR 做可比的 Trace/调用量和质量回归，避免重复模型调用无质量收益。
- **C2：按需模型分级或成本优化**，仅当 C0/C1 结果出现成本瓶颈后启动；不宣称质量损失 <1%、成本下降 >50% 或延迟下降 >40% 是已经验证的成绩。

实验纪律：冻结 Gold、保留组别与旧结果、标明 Provider 真实请求及费用未知项；没有运行结果时写**“仅有脚本，无留存数据”**。优先重放与离线校验，真实 Provider 只跑必要的少量样本；正式数据和开发集严格分离。

---

# 第三部分 · 安全、发布与验收

## 10. Security & Reliability

### 10.1 授权与资料保护

- B1 支持可信 Workspace 隔离；Demo 成员切换只是模拟 UI，**不宣称正式多租户用户 RBAC**。真实认证与多组织成员管理 B3 再做。
- Check 工作区权限必须发生在检索、Source/Evidence 读取、Tool Calling、Trace、Profile 和回执访问前；不只是在生成最终回答时隐藏来源。
- 会议文本、工具结果中的“替我审批”“忽略权限”视为不可信数据；模型侧默认只读，正式业务写操作从人工审核接口发出。
- 外部模型调用必须有显式授权/配置，Trace 脱敏；密钥不随模型日志输出，供应商数据驻留和隐私策略应考虑 Workspace 敏感度。

### 10.2 故障语义与降级

| 故障 | 行为 |
|---|---|
| Provider 429/503、连接明确失败 | 依据已配置策略做**有限重试或切换**，要求原请求安全可重发 |
| Provider 超时且结果未知 | 记录 `UNCERTAIN` 与预留费用，不自动盲重试；恢复前核对回执 |
| 检索索引不可用 | 允许经过权限和来源校验的 read-only BM25/其他已有回退；没有足够证据就返回 UNKNOWN，**不能强制生成 Direct Answer** |
| Worker 超时 | 依据实际 deadline 终止或交付已验证的部分结果，不引入凭空 `>3s` 的固定阈值 |
| 已完成 Worker 重启 | 先复用 accepted receipt，防止重新付费；不把未知外部结果当作未发生 |
| Source 更新或权限收紧 | 已引用数据进入重新验证流程；不能悄悄沿用旧版本 |
| 任务/决议人工确认 DB 失败 | 原子事务回滚，避免事实已改但审核收据未生成 |
| 用户取消 | 不再派发新分支；已发送调用按已知/未知回执记录；不声称取消能撤回已结算账单 |

**对外发任务**（飞书、Slack、GitHub Issue 等）后置：Confirm 只确认本地权威，Outbox 外发需要单独同意、幂等和未知结果保护；B1 不自动发送。

## 11. API 契约与兼容策略

现有路由优先扩展。以下部分为**逻辑行为要求**，路径是否新增以 `server.py` 兼容性核对为准，禁止为了文档命名大规模替换现有 API。

| 操作 | 当前/目标路径示例 | 输入输出与安全契约 |
|---|---|---|
| 导入 | 已有 `POST /api/sources/import` | `request_id/title/content/meeting_date?`；完全重复先提示，确认独立导入再提交；保存成功返回 Source/Version + 处理中状态，保持旧 HTTP `202` 兼容 |
| 来源详情 | 已有 `GET /api/sources/{id}` | 当前 SourceVersion、原文、状态与分段结果；Workspace 授权 |
| 来源更新 | 目标 `PATCH /api/sources/{id}`；兼容现有 `PUT` | `expected_revision`；标题/日期/正文原子编辑；冲突 409，无变化不重抽 |
| 重试 | 已有 `POST /api/sources/{id}/retry` | 只恢复失败阶段/分段；未知结果与 Provider 预算须受保护 |
| 统一审核 | 兼容 `GET /api/review/candidates` 等，Task/Glossary 扩展 | Candidate + Evidence + Action；确认严格进入正式 Store，不让旧旁路冒充正式 |
| 决议历史 | 已有 Decision 读取/历史路由，扩展未定时视图 | dated/undated、supersedes、stale/permission 显示 |
| 任务历史 | 复用现有任务读取路径 | TaskEvent + Evidence + 审核收据 |
| Ask | 已有 `POST /api/product/ask` | 可信 Scope、结构化结果、明确终态与 Evidence |
| Ask Trace | `GET /api/product/ask/runs/{id}/trace`（集成分支已有） | 按 Workspace 控制；Provider reported vs reserved 独立显示 |
| Resume/Cancel | 尽量复用现有 `runs/{id}` 相关路由 | Checkpoint、权限/预算再校验；有异常或不可恢复明确原因 |
| Profile/Glossary | 在现有 feedback/memory 能力上增量 | Confirmed、scope、revoke、audit，不允许静默模型写入 |

建议错误码：`SOURCE_DUPLICATE`（业务提示）、`REQUEST_ID_CONFLICT`、`VERSION_CONFLICT`、`SOURCE_STALE`、`EVIDENCE_AMBIGUOUS`、`WORKSPACE_DENIED`、`PROVIDER_UNCERTAIN`、`BUDGET_EXHAUSTED`、`REVIEW_REQUIRED`；这些是**目标语义映射**，不要求无条件新增同名常量。

## 12. 开发交付顺序：先产品闭环，复用成熟基础设施

### Batch 1：文本会议闭环（必须完成）

| PR 组 | 内容 | 依赖/验收 |
|---|---|---|
| **B1-0 分支收敛** | 对照 `master/feat/multi-agent`，审查 #116 Workspace、#117 跨分支冲突、#119 语义路由的实现与 CI；确认后按授权合入 | 不能把 Open PR 写成已合入；不自动操作 Git |
| **B1-1 Text Source** | 普通/结构化文本导入、日期可空与填今天、重复提醒、幂等、Source revision、原文证据 | I01-I12；旧 SourceVersion/分段重试不回归 |
| **B1-2 Ingestion + Review** | Decision/Task/Glossary 候选、来源定位、人工确认/批量确认、Task 正式落库/TaskEvent | 不自动批准；旧 Task Review 迁移路径清晰 |
| **B1-3 Undated Decision** | 同一 DecisionMemory 领域表达正式未定时决议、审核收据、CURRENT/AS_OF 保守语义 | P0 存储索引/迁移验证；时态记录不被破坏 |
| **B1-4 Evolution + Profile** | 三主视图、跨会议 Decision/Task 看板、Glossary 与偏好最小确认/撤销 | 原文点击准确，未知时间独立展示 |
| **B1-5 Unified Ask** | Router/Host/Graph 工作链路收敛，现有恢复和工具日志接线 | 当前/历史/因果缺口；避免重复调用与错绑 |
| **B1-6 Trace + Eval Link** | 已有 Trace 的 Router 串联、真实用量与预留分离、Result Adapter | 不造 Token/费用；复用已冻结 Gold/轨迹 Scorer |
| **B1-7 集成验收** | 12 条代表性用户 E2E 场景 + 受影响的自动化回归；少量必须的真实 Provider 验证 | 跨会闭环能演示；真实回执可追溯 |

推荐按数据依赖拆 PR，**不是要求八个组都独立新建一套子系统**。B1-0 是审查与集成门槛，任何合并必须得到明确授权。

### Batch 2：由实际质量/成本证据驱动的增强

- 日期相对语义更智能的局部再计算、复杂并发 Diff。
- 更长文本规模与高级 Context/Artifact Readback 优化。
- SSE 断线恢复、主动取消体验，以及有需求时的更深 Trace 数据可视化。
- 单/多 Agent 实验后的模型分级路由，**先看真实成本与成功率**。
- 只读 MCP 便利查询、更多合格的外部评测样本。

### Batch 3：首版范围明确后置

- 音频上传、ASR、实时录音、speaker diarization、音频引用定位。
- 真实企业成员登录/RBAC、多组织协作与高敏配置。
- 外部任务 Outbox/回执、飞书/Slack/GitHub Issues 等授权派发。

## 13. 验收矩阵：24 项首版检查点，组合成 12 个用户 E2E

**计数约束**：沿用 v1.0 的 E01-E28，总计 **28 个检查点**，其中 **B1 24 个**（E01-E22、E24、E27），**B2 4 个**（E23、E25、E26、E28）；E05/E06 的高级部分也属于 B2。**不等于需要 28 次真实模型调用**。

| ID | 首版重点检查 |
|---|---|
| E01-E02 | 无日期/无发言时间的普通纪要与结构化纪要都能保存并追溯，未知不编造 |
| E03-E04 | 正文重复提醒二选一；同 request_id 重放不生成双份 |
| E05-E06 | 标题/日期更新最小影响、正文新版本、单 revision 409、旧引用核验 |
| E07 | 部分提取和失败恢复，复用有效分段回执 |
| E08-E09 | Decision/Task 人审成为正式权威，绝不自动审批 |
| E10 | 同一 Task 的 Owner/Deadline/Status 跨会议修订，TaskEvent 可追溯 |
| E11-E12 | 审内容可不补日期；用户手动“填今天”可改可清 |
| E13 | 未定时 Decision 不冒充 `CURRENT/AS_OF` |
| E14 | Glossary/Workspace Preference 批准、撤销、重启后状态一致 |
| E15-E16 | 决议替代与因果追问，证据不足拒绝错误因果 |
| E17-E19 | Workspace 隔离、自然语言路由、无 Legacy 错绑 |
| E20-E21 | 既有 LangGraph 多分支、依赖、Checkpoint 恢复不重复执行 |
| E22 | 恶意原文、失效引用、撤销不污染/回滚当前权威 |
| E24 | 真实 Provider 账本与预留账本分离；未知费用不写 0 |
| E27 | 引用点击定位精确来源区间 |

**十二条代表性 E2E**（作为交付演示组合，不改写已有 Gold）：

1. 普通纪要无日期导入，确认无日期决议，问指定历史日期必须表达未知。
2. 结构化纪要导入，点击 Decision/Task 证据定位原话。
3. 完全重复内容两种用户选择，双击网络提交只产生一次 Source。
4. 改标题/日期不重抽全体，改正文创建新版本，并发更新产生 409。
5. 提取失败/部分成功，重试恢复且不覆盖已验证结果。
6. 会议 A/B 同一决议替代，缺日期时当前状态保持可解释的不确定性。
7. 三场会议对同一任务修改 Owner、Deadline 与 Status，留下 TaskEvent 历史。
8. Glossary/偏好经审核与撤销，重启后仍能生效/撤销。
9. 精确当前/历史自然语言查询，路由错误不回旧正则强绑。
10. 多 Agent 调查供应商更换与延期原因；无可靠因果依据时返回缺口。
11. 同名跨 Workspace、来源撤回与恶意纪要注入不能泄露或污染权威。
12. 并行任务恢复：已完成 Worker 复用回执，Trace 显示真实尝试、用量/未知和结果。

**发布证据**：每项标注测试文件及本次输出；真实模型样本仅针对无法靠录制回放/确定性测试验证的环节运行，保留 Prompt/Code SHA、Provider 模型、Judge 版本、失败用量未知和旧数据对照。不能用“测试已编写”冒充“全部跑通”。

## 14. 演示故事板（4～6 分钟，须真实运行）

- **开场**：单次会议纪要无法回答跨会变更；LinkLoom 以来源证据与人审形成可追踪记忆。
- **流程一**：导入会议 A 的普通文本（可无时间），看 AI 候选并人工确认；新项目术语需确认后复用。
- **流程二**：会议 B 替换供应商，会议 C 更新同一任务负责人/期限，演变看板展示事件链、旧证据与审核收据。
- **流程三**：问当前供应商、某日历史状态、延期是否因供应商更换；明确哪些结论有证据，哪些尚未证实。
- **流程四**：展开一次真实 Run 调用链，展示 Router、ToolLedger、并行 Worker、Checkpoint、预算预留与 Provider 使用量，有 Unknown 就显示 Unknown。
- **收束**：展示冻结 Evals 与真实测试不足项，解释可靠性、质量和成本的工程权衡，避免用假截图、假模型或开发集高分夸大结果。

---

# 附录 A · v1.1 主要修正与保留项

| Gemini v1.1 内容 | 终版处理 | 为什么 |
|---|---|---|
| 三主视图 + 设置抽屉 | **采纳** | 更清晰的产品信息架构，可复用现有页面 |
| 只看两种例外、高置信度自动确认 | **否决** | 违反已确认的人审权威规则，也遗漏证据、时间、权限等异常 |
| DatedDecisionDomain + UndatedPolicyDomain | **不采用拆域** | 日期未知的已确认决议不等于长期 Policy；保留单一 DecisionMemory 权威 |
| LangGraph 为“DAG 状态机” | **澄清** | 任务依赖 DAG 无环，Graph 执行流程允许有边界循环/恢复 |
| Router/Host/Worker/Reviewer/Synth 固定序列与模型 | **修订** | 精确查询可确定性直查；Reviewer 多为 Host 校验；Synth 现有实现不额外调 LLM |
| Host 零成本识别所有注入 | **收窄** | 0 Token 的是权限/Schema 等确定性守卫，不能保证所有语义注入均被零成本识别 |
| 单 EvidenceSpan ≤512 tokens | **不采用硬值** | 现有 Context Budget 及来源恢复上限已存在，无此统一实测依据 |
| 1.24s/1,420 tokens/$0.0032 演示 Trace | **移除虚构数字** | 用户明确要求真实可观测性，未实测不写成真实记录 |
| 30 Frozen + 25 Real & Live | **修订统计口径** | 30 题中有 8 条冻结子集；RAR 与 Live 要区分 Dev/Frozen/Live |
| Judge 忠实度评分是已完成标准能力 | **修订状态** | 当前主要轨迹评分是确定性的，LLM Judge 已用于 AMI Task，而非所有 E2E |
| 三轮全强模型/消融/分层路由 | **改为成本敏感的渐进实验** | 先跑一组同题对比；不以强模型统一覆盖整个产品作为必需前置 |
| 成本 -50%、延迟 -40%、质量损失 <1% | **删除既定目标表述** | 没有新实测，不能预设结果，也不应为了数字歪曲实验 |
| Worker >3s 回退 Direct Answer | **否决** | 固定阈值缺依据，未经证据验证的直答会破坏 Grounding |
| session+segment+action 作为所有写工具幂等键 | **修订** | 创建、审核、任务事件及未来外发需要按各自业务身份/版本/授权定义幂等语义 |
| 轻量 UI、Trace Drawer、复用 Eval | **保留并具体化** | 与实际已有 Trace、Eval 资产一致，避免重新造平台 |

# 附录 B · 实施前仅需完成的三项有限代码核验（不再逐题询问用户）

**P0-1** `decision_memory/models.py`、`store.py`、`reconciliation.py`：验证已确认但时间未知记录的持久化位置与审核收据、唯一性、旧 CURRENT/AS_OF 兼容和补日期事务。交付**最小数据迁移及 3 个不可替代测试**，不是新一轮数据库重构。

**P0-2** `ui/product.py`、`semantic_ingestion/`：验证无时间戳普通纪要的来源位置映射、引用 Host 校验及现有分段/重试/幂等复用。交付**增量 API/Schema 差异与 3 个典型案例**。

**P0-3** `orchestration/`、`ui/unified_ask.py`、`ui/task_provider.py`、PR #116/#117/#119：核对集成顺序、权限/冲突/路由与运行时 Trace 是否共用同一真实执行上下文，避免重复 Selector 和重复预算系统。交付**最小改动文件列表与真实回归范围**。

这三项是有限工程实现核验，不需要再就已经确定的产品规则开启选择题。

# 附录 C · 最终交付原则

- **仓库只读审查阶段不修改 LinkLoom 代码，不新建产品文件，不自动合并 PR**；本文作为后续开发输入单独保存。
- **已有数据和历史报告不得因本 Spec 而伪造新结果**，新 Eval 不得改变冻结 Gold 来制造进步。
- **每个实施 PR**：明确 `REUSE/EXTEND/NEW/REMOVE` 文件，记录迁移与回滚、界面行为、测试覆盖和用户可见收益。
- **保持项目简单**：不为微小异常单独建立大状态机，不为“省钱”削掉防错回执，不为“多 Agent”强迫每次问题走并行，不为“严谨”而无限加测。
