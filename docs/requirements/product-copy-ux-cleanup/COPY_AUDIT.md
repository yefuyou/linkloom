# COPY_AUDIT

基线 master：`7720390a796662ff42c27b5eef6f8685e5155802`。审计范围为 42 个页面/用户状态，发现并处理 60 处基线问题。

统计单位是一处可定位的渲染位置或紧密相关的双语文案组。共享 helper 与页面具体文本分别记录；同一位置的多轮编辑、翻译补齐、新控件 ID 和测试更新不增加问题数量。
删除 12，合并 12，改写 24，移动 12。完整原始片段和唯一编号见 [COPY_AUDIT.json](COPY_AUDIT.json)。

先读取真实代码、模板和状态转换，再使用当前合成 HTTP 数据完成 Chromium 截图/交互。旧截图只作为基线导出的当前重现，未修改历史资产。分类：A 核心，B 辅助，C 冗余，D 内部研发，E 必要风险/权限。

## 问题与处理

| ID | 文件与基线位置 | 原始文案/结构 | 类别 | 理解问题 | 处理 | 结果与理由 | 权限/业务语义 |
|---|---|---|---|---|---|---|---|
| C01 | src/linkloom/ui/static/product.js:67 | <p class="eyebrow">${escapeHtml(kicker)}</p><h1>${escapeHtml(title)}</h1><p class="lede">${escapeHtml(description)}</p> | C | 页头固定三层，操作区域被下推。 | 合并 | heading 只输出标题与可选操作；必要状态在具体操作处保留。 | 不涉及 |
| C02 | src/linkloom/ui/static/product.js:143 | Bring the project record together | C | Sources 下再次使用营销标题。 | 改写 | 直接使用 Sources / 来源。 | 不涉及 |
| C03 | src/linkloom/ui/static/product.js:142 | Source text is kept as an immutable version; this does not edit files in your workspace. | C | 空状态混入存储实现说明。 | 改写 | 给出导入会议或决策记录的下一步。 | 不涉及 |
| C04 | src/linkloom/ui/static/product.js:145 | LinkLoom will show INGESTING, then the candidate and review counts. | D | 输入说明叙述内部运行状态。 | 删除 | 保留一行一消息的时间戳格式。 | 不涉及 |
| C05 | src/linkloom/ui/static/product.js:146 | Source saving is available; semantic extraction is not. | C | 全局状态、说明和错误框重复解释未配置提取。 | 合并 | 操作处说明本地保存与提取开关；配置放入展开区域。 | 涉及，保留原规则 |
| C06 | src/linkloom/ui/static/product.js:147 | Files are read in this browser and submitted to the local LinkLoom server. | D | 实现路径挤占文件输入提示。 | 改写 | 显示可接受格式与实际 256 KB 上限。 | 不涉及 |
| C07 | src/linkloom/ui/static/product.js:150 | Save source for later extraction | C | 按钮过长。 | 改写 | Save source / 保存来源；提取状态在旁边说明。 | 涉及，保留原规则 |
| C08 | src/linkloom/ui/static/product.js:139 | ${source.segment_summary.total} segments · ${source.segment_summary.processed} processed · ${source.segment_summary.failed} extraction failed | D | 列表卡片中出现完整处理指标。 | 移动 | 列表保留失败数量；来源详情保留处理信息。 | 涉及，保留原规则 |
| C09 | src/linkloom/ui/static/product.js:139 | Version <strong>${escapeHtml(source.source_version.slice(0, 18))}…</strong> | D | 截断版本标识不帮助选择来源。 | 移动 | 版本与哈希保留在来源详情。 | 不涉及 |
| C10 | src/linkloom/ui/static/product.js:159 | Current version ${source.source_version}. Source changes create a new version; prior evidence remains available. | D | 版本哈希占据页头说明。 | 移动 | 标题只用来源名称，版本信息放入详情。 | 不涉及 |
| C11 | src/linkloom/ui/static/product.js:160 | Segment ${segment.ordinal + 1}: ${escapeHtml(segment.state)} · ${segment.attempt_count} attempt(s) | D | 每个处理片段和次数直接展开。 | 移动 | 折叠到处理详情，失败与重试入口仍可见。 | 涉及，保留原规则 |
| C12 | src/linkloom/ui/static/product.js:162 | Submitting changed text creates a new immutable source version and runs reconciliation. | D | 更新提示使用内部术语。 | 改写 | 说明保存新版本与重新核查受影响决策；外发和费用提示直接保留。 | 涉及，保留原规则 |
| C13 | src/linkloom/ui/static/product.js:198 | Decide what becomes authoritative | C | 审核标题抽象且与导航重复。 | 改写 | Review / 审核；候选类型和审核状态用明确标签。 | 涉及，保留原规则 |
| C14 | src/linkloom/ui/static/product.js:192 | New source-grounded decisions will appear here when the materialization policy requires human authorization. | D | 空状态展示落库策略。 | 改写 | 导入来源后审核提取的候选。 | 不涉及 |
| C15 | src/linkloom/ui/static/product.js:192 | ${escapeHtml(candidate.review_reasons.join(" · ") \|\| "Human review required")} | D | 审核原因直接展示内部枚举。 | 改写 | 映射为关系、对象、日期或冲突的明确审核事项。 | 涉及，保留原规则 |
| C16 | src/linkloom/ui/static/product.js:217 | Canonical relation cannot be corrected in this review form. | E | 同一阻断在四段中重复解释。 | 合并 | 明确“关系待确认，暂无法批准”，保留重提取和拒绝路径。 | 涉及，保留原规则 |
| C17 | src/linkloom/ui/static/product.js:218 | Effective time never defaults to today. | C | 连续否定说明字段处理规则。 | 合并 | 根据原文补充对象和生效时间，不确定字段留空；校验不变。 | 涉及，保留原规则 |
| C18 | src/linkloom/ui/static/product.js:230 | Approve and materialize | D | 用户按钮使用落库术语。 | 改写 | Approve and save / 批准并保存。 | 涉及，保留原规则 |
| C19 | src/linkloom/ui/static/product.js:196 | Supporting evidence only. This proposal is not approved and is not part of Decision Memory. | C | 同一未批准状态反复说明。 | 合并 | 未批准，作为参考材料保留；保留 Supporting proposal 徽标。 | 涉及，保留原规则 |
| C20 | src/linkloom/ui/static/product.js:251 | Compare the original approval with current sources. A new candidate needs a separate explicit human approval. | C | 复核页头再次重复批准规则。 | 移动 | 单独批准要求保留在候选审核操作处。 | 涉及，保留原规则 |
| C21 | src/linkloom/ui/static/product.js:264 | What the team has decided | C | 决策记忆页再次堆叠用途说明。 | 改写 | Decision Memory / 决策记忆，直接展示记录。 | 不涉及 |
| C22 | src/linkloom/ui/static/product.js:284 | ${badge(item.status)}${badge(item.memory_state)} | C | 历史同一记录并列两个相近状态。 | 合并 | 显示当前生命周期徽标；原始两个状态保留在批准记录详情。 | 涉及，保留原规则 |
| C23 | src/linkloom/ui/static/product.js:281 | · Evidence ${escapeHtml(item.evidence_ref \|\| "Unavailable")} | D | 版本与完整证据 ID 混入主要来源正文。 | 移动 | 主要区保留来源名、行号和引文；完整引用可展开。 | 不涉及 |
| C24 | src/linkloom/ui/static/product.js:286 | Review receipt: ${escapeHtml(item.authorization?.receipt_id \|\| "Unavailable")} | D | 时间线主要区展示长授权 ID。 | 移动 | 保留时间与历史，批准记录承载完整 ID。 | 涉及，保留原规则 |
| C25 | src/linkloom/ui/static/product.js:295 | Historical validity uses currently recorded review decisions; this is not a snapshot of what was known at that time. | E | 以否定句重复解释历史查询。 | 改写 | 历史日期查询依据目前保存的审核记录。 | 涉及，保留原规则 |
| C26 | src/linkloom/ui/static/product.js:367 | <p class="eyebrow">Ask</p> | C | Ask 页头堆叠眉题、大标题和标语。 | 合并 | 单一 Ask / 提问标题与语言控件。 | 不涉及 |
| C27 | src/linkloom/ui/static/product.js:376 | Automatic shortcuts / ask me to clarify | D | 领域选择暴露路由实现。 | 改写 | Automatic / 自动选择。 | 不涉及 |
| C28 | src/linkloom/ui/static/product.js:379 | Select a domain for unfamiliar wording. Reads the current workspace sources; conclusions remain unconfirmed. Refresh this page after source changes. | C | 输入处提前重复结果免责。 | 合并 | 选择领域以缩小范围；来源变更后刷新。 | 涉及，保留原规则 |
| C29 | src/linkloom/ui/static/product.js:380 | Task stages configured. Relationship proposals require independent confirmation. | D | 阶段配置状态被写成普通操作说明。 | 改写 | 任务调查就绪，仅在任务领域显示相关配置状态。 | 涉及，保留原规则 |
| C30 | src/linkloom/ui/static/product.js:381 | --allow-external-provider --allow-task-investigation --task-max-requests 16 | D | 启动参数直接铺在所有问题的表单中。 | 移动 | 配置指令放入配置详情。 | 不涉及 |
| C31 | src/linkloom/ui/static/product.js:382 | input / ${config.agent.task_provider.budgets.max_output_tokens} output tokens | D | 细粒度 Token 预算占据主界面。 | 移动 | Token 详情折叠；费用、请求数和时限仍直接可见。 | 涉及，保留原规则 |
| C32 | src/linkloom/ui/static/product.js:383 | Only the current snapshot, for this question. Does not approve business relationships. | C | 来源授权区混入重复业务批准说明。 | 改写 | 准确说明本次问题和来源版本授权范围。 | 涉及，保留原规则 |
| C33 | src/linkloom/ui/static/product.js:397 | Your reviewer ID (local human assertion) | D | 用户输入标签出现内部身份术语。 | 改写 | Reviewer name (local record) / 审核人姓名（本地记录）。 | 涉及，保留原规则 |
| C34 | src/linkloom/ui/static/product.js:529 | The answer is not verified yet. | C | 调查中状态又重复解释尚未完成。 | 删除 | 保留正在调查标题和动态进度。 | 涉及，保留原规则 |
| C35 | src/linkloom/ui/static/product.js:515 | The Provider outcome is uncertain. No automatic retry or verified conclusion was released. | E | 失败说明使用 Provider，连续解释不做什么。 | 改写 | AI 服务响应状态不明，请检查运行详情后再决定重试；自动重试已停止。 | 涉及，保留原规则 |
| C36 | src/linkloom/ui/static/product.js:591 | Terminology is not business evidence. | C | 使用术语的记录反复附带无行动价值声明。 | 删除 | 术语授权边界已在纠正操作处保留。 | 涉及，保留原规则 |
| C37 | src/linkloom/ui/static/product.js:622 | The relationship is supported by verified source reads and checked by Reviewer. | D | 关系答案直接展示 Reviewer。 | 改写 | 来源证据支持该关系；审核原始记录可展开。 | 涉及，保留原规则 |
| C38 | src/linkloom/ui/static/product.js:624 | ${result.metrics?.model_turns \|\| 0} ${t("model turns", "轮模型交互")} | D | 模型轮次与业务关系并列突出。 | 移动 | 轮次保留在调查详情 metrics 中。 | 不涉及 |
| C39 | src/linkloom/ui/static/product-task-result.js:61 | Source records found; the formal task state remains unconfirmed | C | 结论前后四段重复解释待确认。 | 合并 | 先显示实际记录数，再用一行区分部分结果、任务待确认和离线演示。 | 涉及，保留原规则 |
| C40 | src/linkloom/ui/static/product-task-result.js:62 | Records are kept separate. Source statements and model interpretations do not establish the current owner or effective plan. | C | 主结果又用长句重复状态。 | 删除 | 逐记录卡片保留字段与原文，未确认状态集中显示。 | 涉及，保留原规则 |
| C41 | src/linkloom/ui/static/product-task-result.js:64 | Checked citations do not verify the interpretation. Full proposals and evidence are expandable below. | C | 模型摘要重复解释引用与判断。 | 删除 | 模型建议标题明确待确认；引用核验状态在提案详情。 | 涉及，保留原规则 |
| C42 | src/linkloom/ui/static/product-task-result.js:37 | Model support cannot replace that evidence. | D | 阻断理由采用辩解式句型。 | 改写 | 指出缺少当前关系证据，并引导核对任务分配原文。 | 涉及，保留原规则 |
| C43 | src/linkloom/ui/static/product-task-result.js:33 | Unknown does not mean refuted. | C | 未知状态用逻辑解释代替用户结果。 | 改写 | 现有证据尚无法确定任务关系；未知仍区别于否定。 | 涉及，保留原规则 |
| C44 | src/linkloom/ui/static/product-task-result.js:67 | Model-reported gap: | C | 模型缺口与主区待确认事项重复出现。 | 移动 | 完整缺口与正反证集中保留在关系提案详情。 | 涉及，保留原规则 |
| C45 | src/linkloom/ui/static/product-task-result.js:73 | Fields are shown per message. Field interpretations still need review; nothing is inherited from another record. | C | 卡片前解释多条实现规则。 | 合并 | 提取字段，请逐条核对原文；字段隔离与来源引用保持。 | 涉及，保留原规则 |
| C46 | src/linkloom/ui/static/product-task-result.js:82 | t('Not established:', '尚未确定：') | C | 缺失字段列表重复每个字段的待确认值。 | 删除 | 每字段待确认值保留，技术详情仍列全部未知字段。 | 涉及，保留原规则 |
| C47 | src/linkloom/ui/static/product-task-result.js:97 | Citations checked; interpretation and business approval remain unconfirmed. | C | 每项提案重复业务批准解释。 | 合并 | 引用已核验，判断待复核；关系批准边界在详情统一说明。 | 涉及，保留原规则 |
| C48 | src/linkloom/ui/static/product-task-result.js:107 | Offline scripted or recorded adapters; real-model understanding was not evaluated. | D | 离线身份被写成实验能力声明。 | 改写 | 主状态标明离线演示，详情说明预设或回放响应。 | 涉及，保留原规则 |
| C49 | src/linkloom/ui/static/product-task-result.js:105 | ${(view.next_actions \|\| []).map(a => `<p>${esc(nextAction(a))}</p>`).join('')} | B | 用户下一步埋在技术调查详情中。 | 移动 | 下一步置于来源卡片之后、技术详情之前；保留全部不同操作。 | 涉及，保留原规则 |
| C50 | src/linkloom/ui/static/app.js:226 | <p class="eyebrow">${escapeHtml(t("initial.eyebrow"))}</p> | C | 旧版初始页眉题重复标题。 | 删除 | 移除装饰眉题。 | 不涉及 |
| C51 | src/linkloom/ui/static/app.js:229 | <div class="value-order" | C | 初始页额外展示三段答案结构。 | 删除 | 保留主标题、简介和输入。 | 不涉及 |
| C52 | src/linkloom/ui/static/app.js:236 | <p class="query-label">${escapeHtml(t("query.kicker"))}</p> | C | 表单标题前重复提问标签。 | 删除 | 保留具体输入说明。 | 不涉及 |
| C53 | src/linkloom/ui/static/app.js:343 | <p class="eyebrow">${escapeHtml(t("running.eyebrow"))}</p> | C | 进度标题前重复进度名称。 | 删除 | 保留读取进度与来源。 | 不涉及 |
| C54 | src/linkloom/ui/static/app.js:698 | <p class="eyebrow">${escapeHtml(t("error.kicker"))}</p> | C | 错误页重复强调失败与证据无关。 | 删除 | 保留失败标题、重试和技术详情。 | 涉及，保留原规则 |
| C55 | src/linkloom/ui/static/app.js:743 | <p class="eyebrow">${escapeHtml(t("empty.kicker"))}</p> | C | 空状态眉题重复没有记录。 | 删除 | 保留空状态标题和行动。 | 不涉及 |
| C56 | src/linkloom/ui/static/i18n.js:47 | Reading the project record, not guessing the answer. | D | 旧版进度标题采用对比式自我说明。 | 改写 | Reading project records / 正在查阅项目记录。 | 不涉及 |
| C57 | src/linkloom/ui/static/i18n.js:68 | Partial decision · Confirmed record | E | 部分结果可能被读成正式决策确认。 | 改写 | Partial result · sourced record / 部分结果 · 来源记录。 | 涉及，保留原规则 |
| C58 | README.md:180 | ## Current status and limits | D | 产品介绍混入过时基线和多段工程验收限制。 | 合并 | 用 Data and review 解释当前使用方式、权限和存储；验证协议链接到开发资料。 | 涉及，保留原规则 |
| C59 | README.zh-CN.md:158 | 专门的过期重审界面尚未实现 | D | 当前说明仍称已有复核功能尚未实现。 | 改写 | 按实际已存在的对照、候选和单独批准流程介绍。 | 涉及，保留原规则 |
| C60 | docs/V1_DEMO.md:42 | Approve and materialize | D | 演示按钮与产品术语不一致。 | 改写 | 与批准并保存、已处理、来源已变化等产品标签一致。 | 涉及，保留原规则 |

静态模板、显式显示调用与旧版双语消息目录共 522 个文本条目，分类与基线位置见 [COPY_INVENTORY.json](COPY_INVENTORY.json)。该目录包含保留文本，不作为问题数量；动态用户数据按角色保留并转义。

## 页面与状态清单

每一状态的主要标题、结论与操作按 A 保留；输入格式、来源名称/引文按 B 保留；权限、当前有效性、批准与部分结果按 E 保留。C/D 的具体调整见上表。技术详情内的机器状态、原始结构和哈希继续可查。用户提供的名称、引文和字段值原样转义显示，不参与措辞改写。

| ID | 页面 | 状态 | 实际渲染入口 | 验证方式 |
|---|---|---|---|---|
| S01 | Workspace | 新建/无工作区 | showNoWorkspace | browser |
| S02 | Workspace | 切换工作区与隔离 | workspacePicker change | DOM/browser |
| S03 | Workspace | 创建/详情表单切换语言 | interface-language change | DOM/browser |
| S04 | Sources | 来源列表 | loadSources | browser |
| S05 | Sources | 初始空状态 | loadSources | browser |
| S06 | Sources | 已启用提取与外发提示 | loadSources providerReady | DOM/browser |
| S07 | Sources | 本地保存/提取未启用 | loadSources/inspectSource | DOM/browser |
| S08 | Sources | 处理与失败/重试 | inspectSource/pollSource | code/HTTP |
| S09 | Sources | 原文与版本详情 | inspectSource | browser |
| S10 | Sources | 更新或移除 | source-update/delete-source | HTTP/browser |
| S11 | Review | 候选队列/空队列 | loadReview | browser |
| S12 | Review | 参考提案 | loadReview proposalItems | DOM/code |
| S13 | Review | 关系未确认阻断批准 | inspectCandidate relationGuidance | DOM |
| S14 | Review | 对象和生效日期补充 | inspectCandidate resolutionFields | DOM |
| S15 | Review | 来源变化需复核 | loadReview attentionCards | browser |
| S16 | Review | 原批准与新来源对照 | inspectRevalidation | browser |
| S17 | Review | 新候选单独批准/保留待处理 | inspectCandidate/inspectRevalidation | DOM/browser |
| S18 | Review | 复核完成/只读 | inspectRevalidation | DOM/browser |
| S19 | Decision Memory | 列表与空状态 | loadDecisions | browser |
| S20 | Decision Memory | 当前/历史日期 | inspectDecision | HTTP/browser |
| S21 | Decision Memory | 时间线/替代/提案/批准详情 | inspectDecision | DOM/browser |
| S22 | Ask | 输入/领域/语言/高级选项 | showAsk | DOM/browser |
| S23 | Ask | 术语候选与授权 | loadFeedback/changeFeedback | DOM |
| S24 | Ask | 正在调查 | renderAskResult INVESTIGATING | DOM/code |
| S25 | Ask | 已审核答案 | renderAskResult COMPLETED | DOM/browser |
| S26 | Ask | 原因证据不足 | renderAskResult reasonUnknown | DOM |
| S27 | Ask | 歧义/日期澄清/无匹配 | renderAskResult admissionMessages | DOM/HTTP |
| S28 | Ask | 来源或工作区变化 | renderAskResult admissionMessages | DOM/HTTP |
| S29 | Ask | 外发权限与费用上限 | showAsk/permission BLOCKED | DOM/code |
| S30 | Ask | 服务响应不确定/失败 | renderAskResult/showAskError | DOM/browser |
| S31 | Ask | 来源关系完成/部分/冲突 | renderSourceInvestigation | DOM/HTTP |
| S32 | Task Investigation | 部分记录与模型支持建议 | renderTaskAnswer | DOM/browser |
| S33 | Task Investigation | 无记录/资料不足 | renderTaskAnswer UNKNOWN | DOM/browser |
| S34 | Task Investigation | 模型否定/冲突/未知 | proposalState | DOM/browser |
| S35 | Task Investigation | 前置关系阻断 | reason/open steps | DOM/browser |
| S36 | Task Investigation | 日期比较与当前生效性 | comparisons/currentness | DOM/browser |
| S37 | Task Investigation | 来源更新/撤权与保存结果重核验 | renderAskResult BLOCKED | DOM/HTTP/browser |
| S38 | Task Investigation | 离线演示/运行详情 | renderTaskAnswer prep.execution_kind | DOM/browser |
| S39 | Legacy Ask | 初始输入 | renderInitial | browser |
| S40 | Legacy Ask | 读取进度 | renderRunning | code |
| S41 | Legacy Ask | 答案/部分/证据不足 | renderResult | unit/browser |
| S42 | Legacy Ask | 空状态/错误/来源核验 | renderEmpty/renderError/inspectorHtml | unit/browser |

## 保留的风险与权限信息

- **外发与费用**：导入/来源更新处保留外部 AI 与费用说明；Ask 逐次许可复选框、来源清单及费用/请求数/时限直接可用。仅启动参数和细粒度 Token 上限展开显示。
- **批准边界**：候选类型和审核状态保留；未解析关系的批准按钮仍禁用。任务模型建议明确待确认；关系调查与业务批准区分集中说明。
- **来源与历史**：来源变化和证据/权限更新的阻断原因直接显示，旧任务详情隐藏；历史查询说明依据当前记录的批准。开放日期区间使用“未设结束日期”，避免暗示仍然有效。
- **不确定性**：资料不足、否定证据、证据冲突、前置阻断分别呈现。日期比较仍以 current_valid 为前提，当前生效计划保持待确认。
- **运行身份**：离线任务演示在主状态行直接标注，预设/回放结构位于详情。未把离线结果当作真实模型验收。

## 实际界面证据与范围

参见 [DELIVERY.md](DELIVERY.md) 的最终测试收据和前后截图。浏览器交互使用一次性公开合成数据库、现有离线阶段及本地 Chromium。未接触真实私人数据、密钥或付费 Provider。

后端返回的原始机器结构（包括 task_result.py 的 NOT_EVALUATED）仅出现在展开的技术记录中，未改变字段或业务定义。研发路线图、历史报告、冻结 Gold、账本和原始回执均未修改。


## 同专项整合

后续整合沿用既有 Draft PR #25，保留其语言偏好、候选类型、保存状态、错误映射及第五套DOM门禁。
本轮42状态/60问题与前轮37组/100项独立统计，未相加。原始前轮审计和回执保持原样。
组合版本在当前 master f2ce6b8 上通过178项Python/HTTP、五DOM和33步Chromium；见 DELIVERY.md。
