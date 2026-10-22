# AMI 官方链接真实检索首轮集成报告

在已经暴露的 ES2005 诊断系列上，本地 Dense / Hybrid 比固定参考 BM25 更有效地找回官方链接对应的原始 DA。Hybrid 的 Recall@5 从 30.09% 提升到 46.76%，Recall@10 从 37.04% 提升到 60.65%；LinkLoom BM25 与参考指标完全一致。

这些是 `PUBLIC_HUMAN_GOLD` **官方链接检索指标**，不是全部相关发言的 Recall，也不是 LinkLoom Product 端到端成绩。完整会议系列隔离仍保持，但已暴露的 ES2005 不再称为首次盲测。

实现和最终运行 Git SHA：`3dda2d25bb4806d64e4b1832cf2eb30d26d28856`。分支 `codex/ami-public-gold-benchmark`；基线远端 master 为 `46b819fa39ace26de2c5470f7438b56df523c560`。已有 PR #13/#14/#15 保持不变。最终运行是 `data-04 / run-03 / score-03`，前序失败及修正见 `failed_attempts.json`。

## 数据、来源与可逆映射

官方 AMI manual ZIP 22,887,865 bytes，CC BY 4.0；归档 SHA-256、所读原始成员及其哈希见 [source_manifest.json](source_manifest.json)，原始许可见 `evals/ami_public_v1/frozen/LICENCE.txt`。来源版本以归档 hash 固定，保留文件名 1.6.2 与内部 README 1.7 的差异。

| 会议 | 系列划分 | 词 | 官方 DA | 非文本 DA | 未分段词 | F 分块 |
|---|---|---:|---:|---:|---:|---:|
| ES2002a | 开发 | 3105 | 475 | 41 | 5 | 8 |
| ES2002b | 开发 | 8110 | 1003 | 27 | 1 | 18 |
| ES2002c | 开发 | 8757 | 1034 | 38 | 4 | 18 |
| ES2002d | 开发 | 9075 | 1362 | 57 | 3 | 23 |
| ES2005a | 诊断 | 891 | 152 | 12 | 0 | 3 |
| ES2005b | 诊断 | 7199 | 969 | 94 | 2 | 17 |
| ES2005c | 诊断 | 8003 | 1181 | 80 | 2 | 20 |
| ES2005d | 诊断 | 4666 | 805 | 91 | 0 | 13 |

合计 49,806 个原生词、6,981 个官方 DA、2,047 个非文本事件、440 个非文本 DA、120 个预算内分块。17 个未被 DA 引用的词保留在完整 canonical 和 F 分块中；为与参考同口径，首轮候选仅为全部原生 DA。13 条 ACTIONS + 43 条 DECISIONS 共 56 条查询，51 条有官方关联发言，5 条 N/E；100 个 query–DA 关联对、97 个不同目标 DA。

适配器只解析 words、DA child references 和 meeting metadata；NXT range 按 XML 文档顺序展开，保留原生 ID、Speaker ID、原文词、相对秒、原始 href、member/archive hash。DA 文本按单空格拼词；canonical 原生 DA 按时间和稳定 ID 排序，未分段词独立记录在末尾。说话重叠不把不同说话者的词穿插到同一个 DA。未知日期、时区和单边时间保持未知，不推断绝对时间。

每个词的 Unicode codepoint span 可恢复原文，按 native ID 和 XML member hash 回查原件。完整 turn 优先分块，同时守 8,000 chars / 16,000 UTF-8 bytes；超长 turn 机械拆分，不按 Gold 选择。实际 49,806 个词位置和全部 120 个分块经过完整 hash/offset/预算/roundtrip 检查，见 [source_validation.json](source_validation.json)。可逆指 canonical 转写与原件引用映射，不指重新序列化原 XML。

## 官方 Gold 隔离

预测进程实际只打开 `manifest.json`、`queries.jsonl`、`passages.jsonl`。原摘要、section、qrels、summlink 与 ZIP 均未开放给预测；固定官方摘要句只作为已声明的查询文本，绝不作为检索文档。预测完成先封存，再由独立评分进程打开 Gold。来源与上下文引用、选择记录、piece 完整性均由评分器重新校验。

实际网络尝试 0、被拒绝数据访问 0、付费调用 0、成本 USD 0。127 次 Python 标准库 ZIP 探测被精确放行；其他 ZIP 和 Gold 路径仍拦截。严格字段白名单与 canary 测试通过。这是受审计的 Python 进程边界，并非对恶意 native code 的 OS 沙箱。

## 同口径指标

每条查询给定同一个会议范围，候选是该会议全部官方 DA；不能把“正确会议”命中当作独立会议发现能力。三个检索器均调用现有 `retrieval_v2` 类，BM25Index / VectorIndex / HybridRetriever 未修改。Dense 使用本机已缓存 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`，sentence-transformers 5.1.2，CPU 4 threads；Hybrid 固定 candidate_k=20、RRF k=60。未根据诊断错误修改参数、英文词表或查询。

Recall 是查询宏平均；Hit 分母为可评分查询数；MRR 截止 Top 10。全部 56 条均执行，N/E 不作负例也不进入分母。原始参考预测、查询、corpus projection、runner 和 seal 字节经过重新核验，同一评分器重新计算参考结果。

### ES2005 诊断：20 调度 / 18 可评分 / 2 N/E

| 系统 | R@1 | R@5 | R@10 | Hit@1 | Hit@5 | Hit@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 参考 BM25 | 11.57% | 30.09% | 37.04% | 4/18 | 8/18 | 10/18 | 0.3382 |
| bm25 | 11.57% | 30.09% | 37.04% | 4/18 | 8/18 | 10/18 | 0.3382 |
| dense | 27.78% | 46.76% | 53.70% | 7/18 | 13/18 | 15/18 | 0.5042 |
| hybrid | 21.30% | 46.76% | 60.65% | 7/18 | 12/18 | 15/18 | 0.5173 |

### ES2002 开发：36 调度 / 33 可评分 / 3 N/E

| 系统 | R@1 | R@5 | R@10 | Hit@1 | Hit@5 | Hit@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 参考 BM25 | 36.36% | 54.29% | 56.31% | 16/33 | 23/33 | 24/33 | 0.5818 |
| bm25 | 36.36% | 54.29% | 56.31% | 16/33 | 23/33 | 24/33 | 0.5818 |
| dense | 28.79% | 47.98% | 61.11% | 13/33 | 21/33 | 25/33 | 0.5049 |
| hybrid | 39.39% | 53.54% | 62.12% | 17/33 | 22/33 | 25/33 | 0.5907 |

### 全部：56 调度 / 51 可评分 / 5 N/E

| 系统 | R@1 | R@5 | R@10 | Hit@1 | Hit@5 | Hit@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 参考 BM25 | 27.61% | 45.75% | 49.51% | 20/51 | 31/51 | 34/51 | 0.4958 |
| bm25 | 27.61% | 45.75% | 49.51% | 20/51 | 31/51 | 34/51 | 0.4958 |
| dense | 28.43% | 47.55% | 58.50% | 20/51 | 34/51 | 40/51 | 0.5046 |
| hybrid | 33.01% | 51.14% | 61.60% | 24/51 | 34/51 | 40/51 | 0.5648 |

Dense 在开发集 R@5 低于 BM25（47.98% 对 54.29%）；Hybrid 开发集 R@5 也略低（53.54%）。因此本轮支持的是此固定诊断系列上的改善，不证明所有会议或所有指标一致改善，更没有新的未见系列泛化结论。

## 检索到最终上下文的损失

真实 `ContextAssembler` 调用内部 `select_evidence`，使用默认 max_evidence=5、max_chars=12000、max_tokens=3000。没有将 Gold 目标或 Gold group hints 传入选择器。Required Evidence Coverage 检查最终 rendered context 中完整目标原文，含查询宏平均和目标对微平均；不会以检索 Top K 代替上下文。

| 范围 / 方法 | 目标对 | Top10 找回 | 选择后 / 最终 | REC 宏 | REC 微 | 选择损失 | 后续渲染损失 |
|---|---:|---:|---:|---:|---:|---:|---:|
| diagnostic / bm25 | 43 | 15 | 12 | 30.09% | 27.91% | 3 | 0 |
| diagnostic / dense | 43 | 18 | 14 | 46.76% | 32.56% | 4 | 0 |
| diagnostic / hybrid | 43 | 21 | 16 | 46.76% | 37.21% | 5 | 0 |
| all / bm25 | 100 | 44 | 39 | 45.75% | 39.00% | 5 | 0 |
| all / dense | 100 | 48 | 37 | 47.55% | 37.00% | 11 | 0 |
| all / hybrid | 100 | 52 | 43 | 51.14% | 43.00% | 9 | 0 |

诊断系列 Hybrid 的 43 个目标对中，22 个未进 Top10，另外 5 个因默认五条证据上限（LOW_RANK）在选择阶段丢弃，最终保留 16 个。所有方法的源分块丢失、后续渲染损失、上下文截断均为 0；现有 encoder max_seq_length=128，但本轮所有 DA 都未触发 encoder 截断。把上下文上限调大可能改变结果，但本轮保持原配置。

## 八条代表性失败

全部查询的真命中、漏检目标、原文、来源、Top10、选择／上下文状态均保存在 [scores.json](scores.json)；八条完整案例在 [bad_cases.json](bad_cases.json)。以下只节选原文作诊断，可能原因基于字面重叠和组件轨迹，无模型语义裁判。非官方链接的 Top10 发言可能仍有语义相关性，不能称为错误或负例。

### 1. bm25 / ES2005a:ES2005a.JacquelinePalmer.s.6

查询：The User Interface Designer will research possible shapes and colors for the device.
Gold ID：`ES2005a.JacquelinePalmer.s.6`；目标 2 条，Top10 完整命中 1 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005a.C.dialog-act.vkaraisk.48`：Shapes and colours and um basically how to make it attractive .
  来源 `dialogueActs/ES2005a.C.dialog-act.xml`，canonical [14756, 14819)；排名 [7]；原因 SELECTION_LOW_RANK。
- `ES2005a.D.dialog-act.vkaraisk.38`：So you want me to look at shapes and everything you said ?
  来源 `dialogueActs/ES2005a.D.dialog-act.xml`，canonical [14600, 14658)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005a.C.dialog-act.vkaraisk.45`：And obviously , other instructions will come from the personal coach .
- #2 `ES2005a.C.dialog-act.vkaraisk.17`：The the keyrings , yeah yeah .
- #3 `ES2005a.B.dialog-act.vkaraisk.5`：like what is the competition in the market ?

### 2. bm25 / ES2005a:ES2005a.JacquelinePalmer.s.5

查询：The Marketing Expert will examine the features and design of competitors' remotes.
Gold ID：`ES2005a.JacquelinePalmer.s.5`；目标 1 条，Top10 完整命中 0 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005a.C.dialog-act.vkaraisk.42`：So you do the looking around at other remote controls .
  来源 `dialogueActs/ES2005a.C.dialog-act.xml`，canonical [13396, 13451)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005a.C.dialog-act.vkaraisk.45`：And obviously , other instructions will come from the personal coach .
- #2 `ES2005a.B.dialog-act.vkaraisk.6`：What are the available prices of the other remote controls in the prices ?
- #3 `ES2005a.B.dialog-act.vkaraisk.4`：being as a Marketing Exper Expert I will like to say like before deciding the cost of this remote control or any other things we must see the market potential for this product

### 3. dense / ES2005b:ES2005b.JacquelinePalmer.s.13

查询：The remote will have an organic shape and feature some yellow coloring and glow-in-the-dark material.
Gold ID：`ES2005b.JacquelinePalmer.s.13`；目标 4 条，Top10 完整命中 1 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005b.B.dialog-act.dharshi.136`：Glow in dark .
  来源 `dialogueActs/ES2005b.B.dialog-act.xml`，canonical [107868, 107882)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。
- `ES2005b.C.dialog-act.dharshi.261`：and we can have sort of a a a trimming as well , of the glow in the dark material , just as gimmickyness .
  来源 `dialogueActs/ES2005b.C.dialog-act.xml`，canonical [97997, 98103)；排名 [6]；原因 SELECTION_LOW_RANK。
- `ES2005b.C.dialog-act.dharshi.300`：Um , speech recognition , limited buttons , organic design .
  来源 `dialogueActs/ES2005b.C.dialog-act.xml`，canonical [107295, 107355)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。
- `ES2005b.D.dialog-act.dharshi.215`：if it's not too expensive s I think it's a good gimmick .
  来源 `dialogueActs/ES2005b.D.dialog-act.xml`，canonical [108085, 108142)；排名 Top10 之外；原因 NO_SHARED_QUERY_TERMS。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005b.D.dialog-act.dharshi.236`：Yeah , d with the colour , um does it have to be all yellow , do you know ?
- #2 `ES2005b.A.dialog-act.dharshi.93`：Um as you can see the the remote control have parts and those parts look like that
- #3 `ES2005b.C.dialog-act.dharshi.260`：Uh then you know they're gonna be fairly brightly coloured anyway ,

### 4. dense / ES2005b:ES2005b.JacquelinePalmer.s.11

查询：The remote will feature speech recognition and programmable channel and volume preferences.
Gold ID：`ES2005b.JacquelinePalmer.s.11`；目标 3 条，Top10 完整命中 0 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005b.C.dialog-act.dharshi.277`：So we'll stick with sort of programmability um for the buttons that we do have .
  来源 `dialogueActs/ES2005b.C.dialog-act.xml`，canonical [102493, 102573)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。
- `ES2005b.C.dialog-act.dharshi.299`：so we'll go with speech recognition , yeah ?
  来源 `dialogueActs/ES2005b.C.dialog-act.xml`，canonical [106819, 106863)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。
- `ES2005b.C.dialog-act.dharshi.300`：Um , speech recognition , limited buttons , organic design .
  来源 `dialogueActs/ES2005b.C.dialog-act.xml`，canonical [107295, 107355)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005b.A.dialog-act.dharshi.37`：And uh a novel feature which uh we just brought up was this this automatic speech recognition feature or noise detection feature for when you lose the remote ,
- #2 `ES2005b.A.dialog-act.dharshi.128`：What you could do then would be you have uh a remote controlled by the T_V_ speaker ,
- #3 `ES2005b.A.dialog-act.dharshi.135`：and then the remote control would know um what's being produced by the television .

### 5. hybrid / ES2005a:ES2005a.JacquelinePalmer.s.5

查询：The Marketing Expert will examine the features and design of competitors' remotes.
Gold ID：`ES2005a.JacquelinePalmer.s.5`；目标 1 条，Top10 完整命中 1 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005a.C.dialog-act.vkaraisk.42`：So you do the looking around at other remote controls .
  来源 `dialogueActs/ES2005a.C.dialog-act.xml`，canonical [13396, 13451)；排名 [6]；原因 SELECTION_LOW_RANK。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005a.B.dialog-act.vkaraisk.4`：being as a Marketing Exper Expert I will like to say like before deciding the cost of this remote control or any other things we must see the market potential for this product
- #2 `ES2005a.B.dialog-act.vkaraisk.6`：What are the available prices of the other remote controls in the prices ?
- #3 `ES2005a.C.dialog-act.vkaraisk.50`：And you look at competition and design .

### 6. bm25 / ES2005a:ES2005a.JacquelinePalmer.s.7

查询：The Industrial Designer will research possible materials and the necessary internal components of the device.
Gold ID：`ES2005a.JacquelinePalmer.s.7`；目标 1 条，Top10 完整命中 0 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005a.C.dialog-act.vkaraisk.44`：and you could look into um basically how how it's made I_E_ like how you make it all in one , how what sort of materials are available to you whatever .
  来源 `dialogueActs/ES2005a.C.dialog-act.xml`，canonical [13837, 13989)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005a.C.dialog-act.vkaraisk.17`：The the keyrings , yeah yeah .
- #2 `ES2005a.B.dialog-act.vkaraisk.6`：What are the available prices of the other remote controls in the prices ?
- #3 `ES2005a.C.dialog-act.vkaraisk.45`：And obviously , other instructions will come from the personal coach .

### 7. bm25 / ES2005c:ES2005c.JacquelinePalmer.s.12

查询：The User Interface Designer and the Industrial Designer will build a prototype out of clay for the next meeting.
Gold ID：`ES2005c.JacquelinePalmer.s.12`；目标 1 条，Top10 完整命中 0 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005c.C.dialog-act.dharshi.303`：and what I would like you guys to do is work on giving me a model in clay .
  来源 `dialogueActs/ES2005c.C.dialog-act.xml`，canonical [136029, 136104)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005c.D.dialog-act.dharshi.197`：So that's um the user interface
- #2 `ES2005c.A.dialog-act.dharshi.215`：Um the user interface , the buttons , I guess we talked about this already .
- #3 `ES2005c.C.dialog-act.dharshi.2`：So the meeting will have about the same format as the last time .

### 8. dense / ES2005c:ES2005c.JacquelinePalmer.s.13

查询：The remote will feature a rubber or latex casing.
Gold ID：`ES2005c.JacquelinePalmer.s.13`；目标 4 条，Top10 完整命中 2 条，最终保留 0 条。正确会议：True（范围已给定）。

- `ES2005c.A.dialog-act.dharshi.186`：s I guess we decided on material ,
  来源 `dialogueActs/ES2005c.A.dialog-act.xml`，canonical [111970, 112004)；排名 Top10 之外；原因 NO_SHARED_QUERY_TERMS。
- `ES2005c.A.dialog-act.dharshi.188`：So that that spongy latex rubber everything feel ,
  来源 `dialogueActs/ES2005c.A.dialog-act.xml`，canonical [112211, 112261)；排名 [9]；原因 SELECTION_LOW_RANK。
- `ES2005c.C.dialog-act.dharshi.121`：so maybe s uh a sort of uh plastic initial plastic with a a latex kinda sheath ?
  来源 `dialogueActs/ES2005c.C.dialog-act.xml`，canonical [64615, 64695)；排名 [8]；原因 SELECTION_LOW_RANK。
- `ES2005c.D.dialog-act.dharshi.209`：I like the rubber , the stress balls , I think ,
  来源 `dialogueActs/ES2005c.D.dialog-act.xml`，canonical [65034, 65082)；排名 Top10 之外；原因 RANKED_OUTSIDE_TOP10。

实际 Top3（完整 Top10 见 JSON）：

- #1 `ES2005c.D.dialog-act.dharshi.218`：w like , 'cause there's gonna be the the cover the the rubber or the plastic casing and then the buttons in probably two different colours
- #2 `ES2005c.A.dialog-act.dharshi.51`：the way I'm gonna do this is uh we're gonna take a look at some old remote controls , see how they work ,
- #3 `ES2005c.D.dialog-act.dharshi.38`：A remote control ? Yeah .

这些案例体现三类边界：摘要把角色或多个主题合并，原发言措辞短或依赖邻文；单 DA 排名可能偏向内容更完整但未被官方链接的发言；已检索到的关联证据排在第 6–10 位时会被默认 max_evidence=5 丢弃。本轮没有据此修改参数。

## 阶段状态与下一步

- PUBLIC_HUMAN_GOLD：来源定位、官方摘要到关联 DA 检索、真实 Context Assembly 证据保留已评测。Action/Decision 完整抽取或分类未执行，不能由检索命中推导准确率。
- MACHINE_SILVER：跨会议 SAME_TASK / REVISES_PLAN 等仍未运行；保留未来两个不同模型独立提案和委托复核接口，不需要用户人工标注才能推进公开 Gold 部分。
- SYNTHETIC_CONTRACT：21 项新 adapter/evaluation 契约测试，加既有检索／上下文／来源回归，共 94 passed；不算真实语义准确率。
- NOT_EVALUATED：F / SemanticAnalysis、负责人、截止日期、跨会议关系、citation entailment、Product E2E。

接入 F 的最小下一步：将 120 个确定性分块包装为现有 ordinary-document `RawArtifact`，复用 `parse_source_text` 与 observation discovery；原生 speaker、relative time 和 DA/word span 保留在 sidecar，不能补绝对时间。沿用现有输入预算、三次调用批次及 Source / Workspace / Evidence Guard。先离线验接口；真实模型调用仍另行授权。限量官方关联类型评测需冻结独立合同，不冒充完整任务、owner 或 relation Gold。

## 验证与交付限制

94 项相关本地测试通过，wheel 构建成功；独立审查已通过实现与最终封存运行，最终重放的数值与前一修正评分完全一致。审查意见在 `docs/requirements/ami-public-retrieval/REVIEW.md`。测试 JUnit 在 [offline-tests.xml](offline-tests.xml)，代码与输入 hash、模型文件 hash 在 [run_manifest.json](run_manifest.json)。跨平台离线 CI 已加入新契约测试，但本分支未推送，因此远端 CI = NOT_RUN。

新实现未修改既有 Retriever、ContextAssembler、生产语义 Guard、历史 Gold/Seal/Ledger、README 成绩或已有 PR。实现及新证据只在独立本地分支提交；未 push、未合并。原工作区的用户改动未动。

局限：8 场仅两个会议系列，ES2005 是已暴露诊断系列，二者均为官方 SA train；ES2005a 原数据不完整，仍保留。官方链接不穷举所有相关发言；没有语义 judge。离线 Python3.13.5 实测，CI 的其他 OS/Python 组合尚未运行。
