# 正式待办 MVP：第 0 阶段基线

## 范围和授权

本文件对应用户在本会话中给出的“正式待办闭环（MVP）”任务。按用户指定的 0–5 阶段推进，每阶段独立提交，汇报后等待确认。第 0 阶段只建立基线，不实现业务代码、不修改现有测试。

用户最新消息授权在目标不存在时执行准备命令 `git fetch origin` 和 `git worktree add -b feat/task-entity D:/webproject/linkloom-tasks origin/master`。已执行；此后沿用该工作树，不另建分支或工作树，不手动修改 Git 配置，不推送。

## Git 基线

- 基线建立开始于 2026-10-29，完整测试及本阶段完成于 2026-10-30（Asia/Shanghai）。
- 工作目录：`D:/webproject/linkloom-tasks`。
- `git branch --show-current`：`feat/task-entity`。
- 开始第 0 阶段时的 `git rev-parse HEAD`：`23b75f56dd5d46037272588eb226d86fb56f9588`。
- `origin/master`：`23b75f56dd5d46037272588eb226d86fb56f9588`；已用 `git ls-remote origin refs/heads/master` 核对远端同值。
- 提交标题：`Merge pull request #28 from yefuyou/cleanup`，已包含 PR #28。
- 初始 `git status --short`：无输出，退出码 0，工作区干净。
- `git merge-base HEAD master`：`c2b4e7721e4e8b17c7ffdbd3e2035e9db42b4dd2`。本地 `master` 引用滞后 `origin/master` 127 个提交；未移动本地 `master`。实际基线采用已核对远端的 `origin/master`。
- 基线 HEAD 与 `origin/master` 相同，已在远端主干中。此前 `routing-cases` 尚未合并的基线说明已被用户替换，不适用于本次基线。

本会话的默认项目目录仍为原项目；本阶段的测试和 Git 命令均显式在上述目标工作目录执行。目标目录位于当前沙箱可写根之外，所需执行使用了获准的沙箱外工具调用；没有修复或改写 `.git` 指针。

## 测试环境和执行范围

- Windows；Python 3.13.5（Anaconda），pytest 8.3.4。
- 本机默认编码 cp936，UTF-8 mode 为 0，stdout 编码 gbk。
- 测试运行未设置 `PYTHONIOENCODING` 或 `PYTHONUTF8`，没有绕过已知编码问题。
- 现有依赖可用：pydantic 2.13.5、httpx 0.28.1、google-genai 2.22.0、openai 2.28.0；未改依赖或安装配置。
- 为本次测试进程设置 `PYTHONPATH`，将本工作树的 `src` 放在最前，使子进程读取本基线代码；附加 `work/task-mvp` 仅用于加载观察型进度插件。
- 与现有 CI 的离线测试边界一致，本次测试进程将 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`GOOGLE_API_KEY`、`GEMINI_API_KEY`、`DEEPSEEK_API_KEY` 置空；未启用真实模型调用。
- 标准复现命令：`python -m pytest -q --junitxml=work/task-mvp/baseline-pytest.xml`。需要和本次一样先将测试进程的模型凭证置空、将 `PYTHONPATH` 指向本工作树 `src`，不覆盖默认编码。
- 本次实际完整运行：`python -m pytest -q -p baseline_progress --junitxml=work/task-mvp/baseline-20261030.xml`。临时插件只通过 pytest hook 记录进度，不改变收集范围、断言、参数化、skip、结果或测试逻辑。没有 `-k`、`-m`、`--ignore` 或提前停止选项。
- 首次运行的 `work/task-mvp/baseline-pytest.log` 在约 13% 时中断，未生成最终 XML，不能作为完整基线。本次保留旧日志，以下统计仅采用随后完成的全量运行，不拼接两次运行的结果。
- 有效完整运行的输出：`work/task-mvp/baseline-20261030.stdout.log`、`work/task-mvp/baseline-20261030.stderr.log`；JUnit：`work/task-mvp/baseline-20261030.xml`；逐用例进度、启动元数据和退出状态使用同名前缀的 `.progress.jsonl`、`.process.json`、`.exit.json`；摘要为 `.summary.json`。
- 日志和辅助脚本留在已忽略的 `work/task-mvp/` 中，不加入提交。31 个跳过均来自原有测试，没有新增 skip/xfail。
- 另外执行 `.github/workflows/offline-pr.yml` 中全部六个前端 DOM 脚本和三个 JavaScript 语法检查，使用 Node.js v18.18.0；结果保留在 `work/task-mvp/baseline-frontend.json`。
- 本阶段没有进行浏览器人工验收或付费 Provider 验收；前端 DOM 脚本通过不等于浏览器验收。

<!-- TEST_RESULTS_START -->
## 第 0 阶段完整测试结果（历史）

| 检查 | 通过 | 失败 | 跳过 | 错误 |
|---|---:|---:|---:|---:|
| 全量 pytest（共 3,176 个） | 3,140 | 5 | 31 | 0 |
| 现有前端 DOM 脚本 | 6 | 0 | 0 | 0 |
| 现有 JavaScript 语法检查 | 3 | 0 | 0 | 0 |

- 完整运行开始：`2026-10-30 00:10:01 +0800`；结束：`2026-10-30 00:38:20 +0800`。
- pytest 摘要耗时 `1691.12s (0:28:11)`，退出码 1；共 6 条测试警告。全程收集并记录了 3,176 个用例，没有收集错误或提前结束。
- XML、逐用例结果和进程退出状态已交叉核对；全量失败集合恰好等于用户预告的五个既有子进程编码用例，未发现清单外失败。
- 本阶段建立比较起点；没有实现新任务逻辑，也没有更改任何现有源码、测试、断言或跳过条件。

### 既有失败用例（不在本分支修复）

1. `tests/integration/test_m3p_product_review.py::test_new_service_reads_saved_result_without_model_calls`
2. `tests/integration/test_m3p_product_review.py::test_http_hot_and_new_process_hide_invalidated_results[update]`
3. `tests/integration/test_m3p_product_review.py::test_http_hot_and_new_process_hide_invalidated_results[remove]`
4. `tests/integration/test_m3p_product_review.py::test_http_hot_and_new_process_hide_invalidated_results[relation_retract]`
5. `tests/integration/test_m3p_product_review.py::test_revoked_evidence_without_source_change_cannot_be_resurrected`

五个用例的共同现象：父进程以 UTF-8 读取按本机 cp936/gbk 默认编码写出的子进程输出，`subprocess._readerthread` 报 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc7 in position 64: invalid continuation byte`。随后 `child.stdout` 为 `None`，`json.loads(child.stdout)` 产生 `TypeError: the JSON object must be str, bytes or bytearray, not NoneType`。日志中的五条线程异常警告与上述五个失败逐一对应。这里登记的是编码解码失败及其后续表面异常，不是新的正式待办功能失败。

### 现有跳过项分组

| 原因 | 数量 |
|---|---:|
| 本地冻结、封存或历史验证材料不在此工作树 | 21 |
| 原有 CLI agent/memory 清理阶段标记 | 3 |
| 真实 Provider 未显式启用或测试凭证为空 | 5 |
| Windows 符号链接支持或权限不可用 | 2 |

完整 JUnit 报告 SHA-256：`7443e38b58b23646ca9365b8f84bfd9c85a20c1b251e17d14a5537aaa8fb43cb`。
<!-- TEST_RESULTS_END -->

## 主干同步：PR #30（2026-10-30 起的有效回归门槛）

按用户授权，PR #30 合并后已执行 `git fetch origin refs/heads/master:refs/remotes/origin/master` 和 `git merge --no-edit origin/master`。

- 上游 PR #30 合并提交：`57098a63537857d40be087642ad5b9d93d2cddfa`。
- 本分支的主干同步提交：`46442bf8969c5a9f6ce80136b94a56b1c03eccd2`。
- 同步的上游文件只有 `src/linkloom/cli.py`、`tests/e2e/test_cli_encoding.py` 和 `tests/integration/test_m3p_product_review.py`；未与本阶段新增待办文件冲突。编码修复来自上游，本功能分支未自行重写修复。
- 5 个既有失败的定向复测：`5 passed in 104.67s`。父进程仍采用默认 cp936；上游修复为测试子进程显式提供 UTF-8。
- 随后的完整 pytest：**3,169 通过、0 失败、31 跳过、0 错误**，共 3,200 个用例，耗时 `2024.70s (0:33:44)`；退出码 0。
- 原有 3,176 个用例逐项对照：**3,145 通过、31 跳过**；旧 5 个失败全部消失，没有丢失原有用例，没有原有用例新增失败或跳过。
- 本轮新增的 24 个用例由 PR #30 的 1 个编码用例和待办模块的 23 个用例构成，均通过。
- 现有前端 DOM 脚本 **6/6 通过**、JavaScript 语法检查 **3/3 通过**；没有改动 Ask 静态文件。
- 本轮完整套件的运行快照为“待办 store 并发首次初始化修复前”。该问题由独立并发探针发现；修复后的数据层验证在第 1 阶段结果中另列。
- 完整结果：`work/task-mvp/stage1.xml`、`stage1.stdout.log`、`stage1.stderr.log`、`stage1.comparison.json`、`stage1.frontend.json`。这些运行产物不加入提交。

**后续阶段以 0 失败为回归门槛。上文第 0 阶段的 5 个失败仅是历史记录，不再作为后续允许失败集合。**

## 现有模块逐条复核

以下位置均以基线 HEAD 的实际文件为准。路径相对本工作树根目录；旧行号引用来自前次审计。

| 模块 / 作用 | 旧引用 | 实际位置 | 复核结论与偏差 |
|---|---|---|---|
| 待办观察及负责人、期限、证据 | `semantic_ingestion/task_observation.py:118` | `src/linkloom/semantic_ingestion/task_observation.py:118`；字段 119–140 | 类位置准确。`description` 对应待办内容，owner/deadline 可空；`deadline_text`、精度和字段证据已存在。 |
| PLAN/ACTION 转为观察 | `validation.py:428` | `src/linkloom/semantic_ingestion/validation.py:428` | 准确，仅 PLAN/ACTION 构建 TaskObservation。 |
| 观察嵌入候选 JSON | `candidate_persistence.py:92` | `src/linkloom/semantic_ingestion/candidate_persistence.py:92`；字段写入 98 | 准确，使用候选 v3 payload；不是独立正式任务表。 |
| 候选 JSON 的数据库位置 | `semantic_candidate.payload_json` | `src/linkloom/decision_memory/store.py:126`；payload_json 在 144 | 表和字段存在。 |
| 待办候选保留为参考材料 | `materialization.py:612` | `src/linkloom/semantic_ingestion/materialization.py:612`；赋值在 616 | 原引用是判断分支起点，精确状态赋值为 616；语义仍是 SUPPORTING_ONLY。 |
| 待办只读查询 | `task_observation_query.py:20` | `src/linkloom/semantic_ingestion/task_observation_query.py:20`；list 方法在 29 | 原引用为类起点，方法起点为 29。按 workspace/source/version/project 查询，核对候选完整性和来源当前性，返回抽取冲突标记。 |
| 决定候选 HTTP 批准流程（仅参照） | `ui/product.py:1475` | `src/linkloom/ui/product.py:1475` | 准确，读取审核人、理由和候选指纹，再批准和物化；本 MVP 不复用这条写入路径。 |
| 决定审核服务（仅参照） | `materialization.py:755` | `src/linkloom/semantic_ingestion/materialization.py:755`；HUMAN_APPROVAL 事件在 796–802 | 准确，包含指纹校验、审核人、理由和事件。 |
| 决定审核事件 / 回执（仅参照） | `decision_memory/store.py:157` | `src/linkloom/decision_memory/store.py:157`；回执表 178 | 准确。事件包括 workspace、actor、reason、candidate_fingerprint；只追加触发器在 230、237。 |
| 存储风格（仅参照） | `TemporalDecisionStore` | `src/linkloom/decision_memory/store.py:280`；外键启用 298，增量迁移入口 341，事务 353 | 类存在，事务和工作区参数化查询可作风格参照，不修改其决定行为。 |
| 来源名称及版本 | `ui/product.py:92` | `src/linkloom/ui/product.py:92`；版本表 107 | 准确，来源名在 ll_source，版本保留来源名、内容和 hash。 |
| 原文行号和引文 | `ui/product.py:124` | `src/linkloom/ui/product.py:124`；字段 131–133 | 准确，按 workspace/source/version 保存证据。 |
| 待办字段证据和会议时间 | `task_observation.py:134` | `src/linkloom/semantic_ingestion/task_observation.py:134`；recorded_at 为 135，event_time 为 136 | 原引用准确指向字段证据；会议时间应取 event_time，不能取 recorded_at。 |
| 不可变原文定位结构 | `EvidenceSpan` | `src/linkloom/semantic_ingestion/artifacts.py:205`；版本 215，行号 224–225，引文 226 | 可保留来源版本、行号和逐字原文；缺失会议时间保持空。 |
| HTTP 路由和静态资源 | `ui/server.py` | `src/linkloom/ui/server.py:22`；GET 入口 196，静态读取 200 | 存在。正式 tasks 路由尚未占用。 |
| 静态资源打包 | `pyproject.toml:20` | `pyproject.toml:20`；static/* 在 21 | 准确，新静态文件可由现有规则打包。 |
| 不碰：ActionRecord / action / action_evidence | `store.py:65`、`models.py:150` | `src/linkloom/decision_memory/store.py:65`、75；`src/linkloom/decision_memory/models.py:150` | 引用准确；不迁移、不合并，不把这套旧行动存储冒充新正式任务。 |
| 不碰：关系旁库 | SAME_TASK、REVISES_PLAN、task_relation_*、task_revision_comparison | `src/linkloom/semantic_ingestion/task_relation_models.py:207`；`task_relation_store.py:98`、103、107 | 现有实现保留；正式任务的合并由人决定，不自动推断同一任务。 |
| 不碰：Ask、legacy-ask 及任务调查 / 核验 | `ui/server.py` 及相关静态和服务文件 | `src/linkloom/ui/server.py:29`；`src/linkloom/ui/static/product.html:27`；`src/linkloom/ui/task_review.py`、`task_investigation.py` | 路由和文件存在，本 MVP 不修改其行为或静态文件。 |

未发现上述复用模块缺失或语义与审计前提相反的情况；行号精确化已逐项记录。

## 后续实施必须保留的边界

- `TaskObservation` 的现有业务权限仍为 SUPPORTING_ONLY / UNCONFIRMED；新的正式任务由独立模块和人工审核建立，不能改动决定物化或关系证明的规则。
- 会议时间来自原文 `event_time`。`recorded_at`、导入时间和审核时间分别保留其原本含义。
- 现有 deadline 只有原文支持的完整日期才可用 ISO 日期表示，见 `src/linkloom/semantic_ingestion/task_observation.py:157–164`；缺失年份、角色或精度时，不猜日期，保留 deadline_text。
- 现有导入入口使用带时间戳的文本解析，见 `src/linkloom/ui/product.py:1256–1276`；端到端夹具应通过现有导入入口，不新增抽取逻辑。
- 新增任务表只能增量加入，不迁移 ActionRecord，不改动关系旁库、决定流程、Ask 或 legacy-ask。
- 用户最新补充已取消 README；docs/task-mvp 仅在开发期间保留，最后阶段将内容浓缩进 PR 描述并从分支删除该目录。

## 阶段对照规则

后续阶段使用同一测试范围和默认编码，比较失败用例集合，而不只比较失败数量。原有用例不得新增失败，不通过修改旧断言、添加 skip/xfail 或修复无关问题改变基线。新增子进程测试按用户要求显式设置 `PYTHONIOENCODING=utf-8`；本功能分支不自行修复五个编码问题；按用户最新授权，PR #30 合并后将 origin/master 合并进本分支，并确认它们消失。第 0 阶段上述结果保留为历史基线。

第 0 阶段只提交本文件。第 1 阶段及以后每阶段独立提交、用中文报告文件和验证结果、对照基线，再等待用户确认。

## 第 1 阶段结果：数据模型和 store

本阶段仅新增数据层和对应单元测试，尚未实现旧 TaskObservation 适配、review.py 服务、HTTP 或页面。新增核心代码不导入 TaskObservation，不改动决定、ActionRecord、关系旁库或 Ask。

- `src/linkloom/tasks/models.py`：Task、TaskEvent、ObservationLink、TaskStatus，以及统一输入 TaskCandidateInput 和多段 EvidenceReference。证据以 JSON 列表存储，逐段保留 workspace、来源版本、发言或行位置、原文及哈希；不同版本不会压成第一段的版本。
- `src/linkloom/tasks/store.py`：仅增加任务相关表和元数据；快照、只追加事件和候选链接同事务。支持明确选择的字段变更、手动状态变更、候选重复处理拒绝、工作区隔离、同时间事件的稳定顺序及完整快照重放。
- 独立复核发现并修复首次并发建库竞争：schema 探测、创建和验证现在都在 BEGIN IMMEDIATE 内，DDL 逐条 execute，不使用会隐式提交事务的 executescript。新增回归先复现旧实现失败，再验证 8 个新库 × 4 连接全部首次打开成功及跨连接读写、重放。
- 修复后的完整数据层聚焦验证：`python -m pytest -q tests/unit/test_task_models.py tests/unit/test_task_store.py --junitxml=work/task-mvp/stage1-final-focused.xml`，**24 通过、0 失败、0 跳过、0 错误**，主 Agent 核验耗时 `1.62s`。
- 独立 Reviewer 再次运行原探针：12 个新库 × 4 并发连接，**48 次打开全部成功、0 次初始化失败**；无关数据和 user_version 保持不变，未知 schema 仍被拒绝且原数据保留。修复复核未发现残留 P1/P2。
- 验证覆盖多段/跨版本证据往返、哈希和定位错误、字段选择、重复候选、工作区隔离、事件插入失败整体回滚、只追加约束、快照重放、原有数据/版本保留及并发审核和首次打开。

上面的 PR #30 完整回归是初始化修复前的 3,200 例快照；修复后运行的是全部 24 个新增数据层用例及独立并发探针，未再次执行原有完整套件。该修复仅改尚未接入产品的新 store 和其新增测试，原有源码及测试未再改动。两次验证范围分别记录，不混作同一轮全量结果。

本阶段没有编写 README。BASELINE.md 和 SPEC.md 仅作开发期间资料，最终阶段按用户要求浓缩进 PR 描述并删除 docs/task-mvp。第 1 阶段提交后停止，等待用户确认第 2 阶段。

## 第 2 阶段开始前：并发修复后的完整回归

2026-10-30，按用户要求在第 1 阶段提交 ed15d494db6c7e98c55be9836107681c065d3fdc 上重新执行完整 pytest。运行前后工作区均干净，未修改业务代码；默认 cp936 环境，不设置全局 PYTHONIOENCODING / PYTHONUTF8 覆盖。

- 结果：3,170 通过、0 失败、31 跳过、0 错误，3,201 collected；pytest 2104.28s (0:35:04)，退出码 0。
- 原始 3,176 例逐项比较：3,145 通过 / 31 跳过，5 个既有编码失败全部通过，无原有用例缺失、无新增失败或跳过。
- 相对原始基线新增 25 例均通过：PR #30 的 1 例与第 1 阶段任务数据层 24 例（含并发首次初始化修复回归）。
- 完整报告与逐项比较：work/task-mvp/stage2_preflight.xml、stage2_preflight.comparison.json、stage2_preflight.exit.json；这些运行产物不提交。
- 本次结果已先向用户汇报，再进入审核服务实现。后续仍以 0 失败为门槛。

审核服务因用户补充人工重绑定和失效出口，按“预计超过一天先拆分”的约定拆为 2A、2B；本轮完成 2A 后提交并等待确认。最新边界详见临时 SPEC.md；不写 README。

## 第 2A 阶段结果：人工审核、字段来源和待复核

按用户“预计超过一天先拆分”的约定，第 2 阶段拆为 2A、2B；本次只交付 2A。2B 的同源新版人工重绑定和人工“已失效”出口，提交后等待用户确认再实现。

### 文件和行为

- 修改 src/linkloom/tasks/models.py：统一候选 title 可空，正式 Task.title 仍必填；可选 source_fingerprint 用于绑定完整来源候选，缺该字段的旧 v1 JSON 及指纹保持兼容。
- 新增 src/linkloom/tasks/review_models.py：通用 CandidateSnapshot / CandidateSource 契约，核心与抽取器解耦，多段证据有序保存。
- 新增 src/linkloom/tasks/legacy_adapter.py：只接旧 TaskObservation；事务内读取候选和持久来源目录，验证候选完整 payload 指纹、工作区、当前来源版本、证据目录及完整原文。原始 payload 完整保留；审核指纹还包含冲突变体上下文和逐来源 epoch。
- 新增 src/linkloom/tasks/review_storage.py：独立审核 schema v1、只追加审核事件及来源切换日志；初始化和读写共用 TaskStore 的 BEGIN IMMEDIATE，不改变 base task schema v1。来源日志仅观察 ll_source 版本 / active 实际切换，追加任务专有日志，覆盖两版本已存在时未查询的 A→B→A。为后续增量迁移仅预留 2B 枚举值，未开放相关服务入口。
- 新增 src/linkloom/tasks/review.py：确认（允许带字段修订）、拒绝、确认后字段修改、候选预览/列表和任务查询。确认只追加一次审核审计和最终 created 快照；后续每个实际字段变化追加任务事件。人工字段明确为 human，证据是背景 context，保留原始引文、哈希和会议时间；人工修改事件本身不冒充会议事件。
- needs_review 与业务 status 分开；来源或指纹失配后追加一次系统失效审计并锁存，快照及历史不回滚。同 ID 当前候选显式重确认必须带审核版本令牌，复用原 task_id，保留未修改值及来源，不重复 created。默认待确认列表包含可重新审核的同 ID 候选。
- 新增 tests/unit/test_task_legacy_adapter.py 和 test_task_review.py；更新现有临时 BASELINE.md、SPEC.md。没有改原测试断言、旧决定/Ask/ActionRecord/关系旁库代码，没有实现 HTTP / 页面 / v2 适配或 README。

### 验证

- Worker 聚焦四文件：64 passed / 0 failed / 0 skipped，10.68s；含原 24 个数据层用例及新增 40 个审核用例。
- 独立 Reviewer 同四文件：64 passed，8.00s；另验证另一 workspace 的同名来源 active 往返不会影响本 workspace 的任务、审核令牌或 approved 状态。未发现阻断 P1/P2。
- 覆盖原值/带修改确认、空标题人工补充、拒绝与重复提交、人工清空/同值审计、并发审核/修改、事件或链接/审核写入失败全事务回滚、来源/证据失效、同 ID 重审、事件重放、多段多版本与逐源 epoch、旧 DTO 兼容、只追加约束以及审核 schema 四轮 × 四连接并发首次初始化。
- 冻结实现后的完整 pytest：3,210 passed / 31 skipped / 0 failed / 0 errors，3,241 collected，966.09s (0:16:06)，退出码 0。默认编码，PYTHONIOENCODING 和 PYTHONUTF8 均未覆盖。
- 与本阶段开始前的 3,201 例逐项对照：3,170 passed / 31 skipped，缺失 0、新增失败 0、新增跳过 0；新增 40 例全部通过。原始基线中的 5 个编码失败本轮仍全部通过。
- 全量运行开始至结果核对，全部 9 个任务源码/新增测试文件哈希未变；提交代码对应本轮完整测试快照。
- 完整产物：work/task-mvp/stage2a.xml、stage2a.comparison.json、stage2a.verification.json、stage2a.exit.json；独立聚焦产物 stage2a-independent-review.xml。均为 ignored 本地运行材料，不提交。
- 完整 JUnit SHA-256：f2c42660cc1deb09d4617a5d79d98155c65e00dc66f64f2a01050381f6dfcf9d。

### 已知边界

- 只有旧 base link、没有新审核快照的 v1 任务只读显示 needs_review，不伪造历史、来源或有效审核；其人工迁移出口留 2B。2A 不实现跨 ID 重绑定、跨会议合并或人工“已失效”。
- 旧抽取器对“联调报告由李明负责……”语序可能生成 owner=null；已用原语序验证人工补李明且来源为 human，不修改抽取器。缺年份日期仍保留 deadline_text，不自动补年份。
- 独立复核的非阻断项：include_reviewed=True 的扩展已处理列表对已拒绝候选返回 reviewed=True、review_status=null；拒绝审核完整保存在审计中，默认待确认列表不受影响。后续若展示已处理候选应补齐该投影。
- 并发验证为跨连接线程；未额外做多进程压力测试。所有数据库为自动测试的合成临时数据，未操作真实笔记库。

本阶段的关键结论：原始候选指纹、人工字段值和来源有效性需要分别记录；只比较当前版本不足以防止旧确认在版本往返后复活。2A 完成后停止等待确认，下一步为 2B。README 已取消，docs/task-mvp 仍仅作临时资料，最终阶段浓缩至 PR 描述并按授权移除。

## 第 2B 阶段结果：人工重绑定、已失效及拒绝状态投影

2026-10-30，用户确认 2A 后，按批准范围在 feat/task-entity 的 51d325ba622bb3b7de044612f9e9064737df15a8 上开发；开始时工作区干净。基线为 3,210 passed / 31 skipped / 0 failed（3,241 collected）。本阶段没有创建分支 / worktree、改变 Git 配置、推送远端或操作真实笔记库。

### 文件及实现

- src/linkloom/tasks/review_models.py：新增抽取器无关 SourceVersionProof / SourceVersionReader，保存来源、真实版本目录顺序、完整内容哈希、逐段证据指纹、原文时间与上下文令牌。
- src/linkloom/tasks/legacy_adapter.py：旧 TaskObservation 适配器增加事务内历史/当前版本证明；验证完整原文与每段证据，严格用同源目录 rowid 判断新旧，不按版本哈希或导入时间排序。
- src/linkloom/tasks/review_storage.py：增量加入 task_review_binding_detail 及工作区关联索引/只追加约束。既有 task / review schema 元数据仍为 v1，旧表、旧审核行和旧触发器不重建或回写。详情合并到对应单次审核历史，保留旧/新指纹、字段选择、版本证明、人工声明及撤销目标。
- src/linkloom/tasks/review.py：新增 preview_rebind / rebind、preview_invalidation / invalidate、undo_invalidation。重绑定必须人工指定新候选和原任务，严格同源/较新/当前且原文验证通过；展示 title / owner / deadline 组差异，全部差异必须显式选 current / candidate / manual。一次重绑定审核记录保存旧新指纹和选择；实际字段值变化另追加 task_event，旧候选、旧链接、旧证据全保留。字段来源按实际选择区分 source / human，不将保留的相异旧值冒充新版支持。
- “已失效”与任务业务 status 分开。机器验证同源、较新、当前 active 和完整性；同会/任务不存在必须由审核人分别明确声明为 True，存为 human_statement，保存人、理由、时间。新版无任何原会议时间锚点也可人工声明，但不能覆盖机器验证失败。查询明确区分 machine_validation 和 human_declarations。
- 专用撤销操作必须指向本任务当前失效审核 ID 和审核版本令牌，仅追加撤销关联记录，恢复 needs_review；保留原失效记录、全部任务历史和业务状态，不自动 approved。普通查询、编辑或确认不能解除 invalidated。
- 当前绑定与历史链接分开；恢复旧原文不能通过旧 link 重确认或倒绑。只包含单 create / 纯 status 历史且可完整验证旧证据、重放一致的 v1 数据可走明确 legacy 出口；旧指纹标为 opaque，未知候选为 null，不伪造旧审核或字段来源，重绑定需逐字段明确确认。
- include_reviewed=True 补齐持久拒绝审核快照、rejected 状态、reviewer、reason、reviewed_at；来源改版或删除后仍可查询，旧拒绝理由不附到新变体。默认待确认列表仍排除已拒绝项。
- 新增 tests/unit/test_task_rebind.py；原测试断言、models.py / store.py、决定 / Ask / ActionRecord / 关系旁库行为未改。没有接 HTTP / 页面 / 新 v2 抽取器，没有写 README。现有临时 SPEC.md / BASELINE.md 更新批准范围和结果。

### 验证与独立复核

- 新增测试覆盖真实旧 pipeline 的新版候选持久化、较新版本 rowid（含与哈希字典序相反的情况）、跨 source/workspace/meeting 拒绝、证据完整性、全部差异选择、字段来源及事件时间、预览 / 审核令牌过期、旧链接防倒绑、两种 legacy 出口、无锚点人工声明、撤销目标/锁存/时间顺序、持久拒绝投影、各插入点异常回滚及三种操作跨连接并发仅一次提交。
- 同时验证只追加/工作区 FK、真实 2A schema dump 的纯增量升级、旧审核记录/两套 meta v1 / user_version / 无关数据不变，以及审核阶段零模型调用。
- 独立 Reviewer 初轮五文件聚焦：129 passed in 21.34s，并发现一个 P2：legacy/null candidate 任务失效→撤销→恢复旧原文后，可用普通 reconfirm 绕过逐字段确认，产生 approved 且字段来源未知。
- 已最小修复：普通 reconfirm 在任何字段或审核写入前拒绝 candidate=null 的旧数据，提示使用受控 legacy 出口。新增公开服务回归先复现旧代码 DID NOT RAISE，再验证修复后任务、事件、链接、审核、详情、来源/版本/证据和候选九类表逐行不变，needs_review / unknown 来源保持。
- 修复后聚焦五文件：130 passed / 0 failed / 0 skipped，23.93s，包含 2A 原 64 例及新增 66 例。报告 work/task-mvp/stage2b_focused_after_legacy_guard.xml。
- 独立 Reviewer 仅复核修复：新回归 1 passed in 0.84s；原独立探针重跑 blocked=true / no_mutation=true / needs_review。无剩余阻断 P1/P2；报告 stage2b-independent-legacy-guard.xml。复核未修改实现或测试。
- 最终冻结快照全量 pytest：3,276 passed / 31 skipped / 0 failed / 0 errors，3,307 collected；1167.09s (0:19:27)，退出码 0。默认编码，PYTHONIOENCODING 和 PYTHONUTF8 均未覆盖。
- 与 2A 的 3,241 例逐项对照：原有 3,210 passed / 31 skipped，缺失 0、新增失败 0、新增跳过 0；新增 66 例全部通过。最初基线的 5 个编码失败本轮仍全部通过。
- 最终全量开始至结果核对，共 12 个任务源码/聚焦测试文件的 SHA-256 未变化，提交对应已验证的最终代码。
- 完整报告/比较：work/task-mvp/stage2b.xml、stage2b.comparison.json、stage2b.verification.json、stage2b.exit.json；JUnit SHA-256 为 88a8543c0d1e9240a53ef87fd27789540054160d9a06f477835c06eef9d04dfb。全部运行产物均留在 ignored work 目录，不提交。

### 范围与限制

- 重绑定仍采用保守规则：所有证据属于单一来源版本，旧新候选的原文记录时间非空且一致；没有独立 meeting_id，不能推断同会不同发言时间或跨来源多段的新旧绑定。证据列表数据契约仍支持多段，不将其压成单引用。
- 旧 v1 若存在来源字段变更 / merge 链、重放不一致或证据无法验证，明确拒绝，不猜旧当前绑定。仅人工失效的同会与不存在判断可由人工声明承担，机器不将其显示为已验证。
- 并发实测为多线程跨连接，没有另外进行多进程压力测试。原前端文件未改，本阶段只做审核服务，没有界面验收或部署。

本阶段确认的设计结论：当前绑定、历史链接、人工作出的业务判断和机器可验证的原文事实需要分别保存；撤销也是追加审核记录，不能删改原失效历史。2B 完成后停止等待确认，不自动进入第 3 阶段。临时 docs/task-mvp 仍按用户要求保留到最终阶段，再浓缩至 PR 描述并移除；不写 README。


## 第 3A 阶段结果：持久候选与可扩展置信度

### 同步后基线

- 第 2B 阶段已验收提交：49e090fe45e23efd04e711b441b1b0e6ab00e1bf。
- 按用户要求将 origin/master 同步至 3065cf4ef650f9c724661baa5ed2d4f141fc8279（PR #31 路由、#32 规模测试、#33 task-eval/v2），两次合并均无冲突，最终合并 HEAD 为 72b326f4e772ebe9475a495466a37ddae9499d80；工作区开始时干净。
- 同步后完整基线：3808 passed / 31 skipped / 0 failed，3839 collected。原 5 个 cp936 编码失败全部通过。相对 2B 新增 532 个用例通过，主干 7 个路由参数名变更逐项对应，无原用例缺失/新增失败/新增跳过。报告 work/task-mvp/master_sync_v2.verification.json；3 项 JS 语法和 6 项 DOM 测试通过。

### 改动与验证

- 新增 tasks/candidate_store.py（中立 GeneratedBatchAdapter、持久批次/候选表、可信格式 registry、幂等接收和事务内重验）、meeting_adapter.py（只接已生成 v2 批次）、candidate_source.py（组合旧/新候选）。修改 tasks/models.py 和 review.py，新增两份单元测试。只创建增量表/索引/只追加约束，不修改旧任务/审核 schema 元数据及已有表数据，不写 semantic_candidate。
- 完整批次回执保留原始候选、覆盖信息、排除/拒绝诊断、负责人列表、期限类型/备选、多段 supports 和原文。每段按原始完整发言验证 source_ref、版本、位置、引文、哈希、目录及原文时间；失败整体回滚。唯一负责人映射至单值 owner，多负责人保持未知并保留全列表；不换算 deadline；deadline_text 验证后原样保留，包括原文空白。多段时间不同则顶层 meeting_time=None，逐段保留真实时间，不以导入时间补齐或推断同会。
- 可选中立 ConfidenceMetadata 和 DTO confidence/confidence_reason 预留 HIGH/LOW + 理由，缺失为未知且省略新增 JSON 键。v2 任务关联 reason 不充当置信度理由。LOW 在当前及历史拒绝列表标记。置信度/理由变更及完整冲突变体均绑定指纹，使旧审批进入待复核。完全相同候选跨批次去重，不做同任务推断。
- 独立 Reviewer 发现单 adapter 不能读新旧格式共存的问题，已修为按 format_version 分派的可信 registry。仅用独立 fixture 协议证明 v2 与另一格式同库重开/查询/确认/事件重放，不读取 task-eval 分支，不假设或实现未合并 v3。未来仅新增薄格式适配并注册，可复用存储、元数据和审核逻辑。
- 首轮聚焦 178 passed / 4 failed：4 例均为新夹具未结束 INGESTING，触发既有 SOURCE_BUSY；已补完整夹具状态，未改产品保护规则或削弱断言。最终新增 54/54 通过，新旧聚焦 184/184 通过（108.11 秒），JUnit work/task-mvp/stage3a-focused-registry-final.xml。
- 独立审查最终无剩余阻断 P1/P2；3/3 探针通过：同事务未提交的证据撤回立即可见且回滚恢复、entry 篡改只追加一次待复核且保留快照和历史/幂等重收拒绝、仅置信度理由改变也使旧审批失效。两种从旧 HEAD 捕获的 DTO 原 JSON/指纹完全不变。探针 work/task-mvp/review-stage3a-boundaries-ecd4b79cba3c4203aa06c7a97c946943.py。
- 冻结七个源码/测试文件后运行完整 pytest：3862 passed / 31 skipped / 0 failed，3893 collected，45:05，exit 0。逐项对比同步后 3839 例：原有 3808 passed / 31 skipped 保持；新增 54 例均通过，无缺失/新增失败/新增跳过；原 5 个编码回归均通过；七文件哈希无漂移。报告 work/task-mvp/stage3a.xml、stage3a.comparison.json、stage3a.verification.json、stage3a.code-snapshot.json。git diff --check 通过。

### 边界与后续

3A 只接收 Host 已生成的 v2 批次，不触发抽取/模型，不接 HTTP/页面/3B 通用合并，不修改决定、Ask、ActionRecord 或关系旁库。历史 get_batch 只核对回执字节完整性，不宣称原文当前有效；可确认集合必须重新校验当前来源。多段时间未知时仍沿用 2B 保守拒绝重新绑定的限制；既有 list_tasks 的重复来源重验成本留作 MVP 规模限制，不扩大性能重构。

本阶段学习：格式中立不仅需要统一 DTO，还需要新旧格式同库读取和可追溯的置信度变体。3A 独立提交后等待用户确认，再进入 3B；第 3 阶段总时限仍为 3 天，预计超时先报告可推迟部分。不写 README；最终阶段按用户约定把过程内容浓缩入 PR 描述并移除 docs/task-mvp/。
