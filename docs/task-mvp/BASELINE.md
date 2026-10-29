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
