# M2 最小实施计划（待确认，不执行）

日期：2026-10-18。本轮只交付三份设计文档；下述代码、fixture、Gold 文件、侧库及检查均是建议，尚未创建或执行。范围由 M2_RELATION_DESIGN.md 与 M2_EVAL_SPEC.md 定义，用户确认后才进入开发。

## 1. 推荐的第一个开发切口

**只做固定候选观察对的 SAME_TASK 证据门槛，不做任务合并。**

输入两条 M1 观察、各自原始 artifact、source snapshot，以及离线候选 proof；输出一个可序列化的 RelationAssessment，明确 SUPPORTED / REFUTED / UNKNOWN / CONFLICT / NOT_ASSESSABLE。观察本身不改动，M1.relation_status 仍为 UNCONFIRMED。

优先做它的原因：身份错误会污染后续影响聚合和计划修订；固定候选对能先验证最关键语义，不把检索、模型抽取和关系判断同时引入。独立任务观察保留，因此判定撤销不会需要“拆回”已经合并的任务。

### 预计修改范围

- 新增 `D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_relation_models.py`：有限端点引用、关系判定、proof、原因码及 canonical 序列化/稳定 ID。本切口只启用 SAME_TASK，其余枚举不等于实现。
- 新增 `D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_relation_validation.py`：证据读回、作用域内任务编号/明确回指的有限证明类型、否定/条件/冲突门槛；无法安全解释的措辞保留 UNKNOWN。
- 新增聚焦单元/契约测试及 M2 专用输入/Gold fixture，预计共约 4–6 个新增文件，实际拆分以可读性为准。Gold loader 与被测输入 loader 分开。
- 原则上不修改 M1 生产文件；若确需共用一个既有只读 helper，只做必要的小接口暴露，不改变现有 payload、ID、日期或 Supporting Only 行为。

外部任务编号从原文和 proof 读取，不在 M1 上增加必填 task_id；无编号的明确回指同样可以确认身份。不得写死陈晨、星河、星帆或原五行语句，也不能把否定词命中当作适用于整篇文档的否定。

### 第一切口完成标准

1. 先物化并独立核对 Dev 语义/Gold，再写验收测试与实现；使用 D01、D02、D03、D04 的 B→A、D05、D07 的 SAME_TASK 子题。应分别为支持、反证、未知、支持、支持、未知，必要证据全部可回读。
2. 不同措辞/任务名可通过明确编号或回指确认；同负责人/项目/相似度/时间接近无法单独通过。显式不同任务为 REFUTED，证据缺口为 UNKNOWN，二者不混用。
3. 同编号跨项目/系统、跨 workspace、反问/引用/条件句、错误版本/hash、抽取冲突均不能获得错误身份确认。固定 schema/证明类型以保守覆盖为目标，不声称解决任意自然语言共指。
4. 输出包含原文、两端引用、桥接证据、状态及原因；示例既展示一个成功身份确认，也展示原五行不能确认的真实输出。序列化/重复判定结果稳定，导入时间不改变 ID。
5. 子集 Precision=1、Recall=1、错误/无据身份确认=0，六个语义题的状态全部正确。明确只验收身份子集，不宣称 M2 整体通过。

该切口可在纯函数/内存中完成；暂不做日期变化、关系持久化或 Agent 接线。确认本设计不等于已经完成开发，也不授权本轮继续执行。

## 2. 后续 M2 内部顺序

| 顺序 | 内容与数据流 | 完成条件 |
| --- | --- | --- |
| 0：语义与 Gold 落地 | 12 个案例→独立审阅原文/命题/证据→分离 Dev/保留输入与 Gold→记录版本/hash | 先有 Gold 再有实现输出；不改 M1/旧冻结集；被测模型/检索无法读取 Gold |
| 1：上述身份切口 | M1 已验证观察+固定候选对→proof 校验→SAME_TASK 判定 | 六个 Dev 子题及工程契约通过，不产生物理合并 |
| 2：上下文与直接关系 | 项目标题/别名/明确引用→IN_PROJECT/PROJECT_ALIAS；工作关联→RELATED_TO；任务+具体变更→AFFECTED_BY | D03、D06、D07、D08、H02 对应题可解释；派生项目不回写 M1；SOURCE_CLAIM 与正式决策分开 |
| 3：修订与时间 | 身份判定+明确修订证据→REVISES_PLAN→RevisionComparison | D04 可比、D05/D07 阻断、H01 不可比、H03 冲突；缺年/角色/边界检查不借元数据 |
| 4：持久化与只读查询 | 判定/事件追加到独立侧库→依赖核验→按来源/项目/观察查询当前与历史 | 重开/重放幂等；版本变更失效；H04 撤销不复活；关系不藏在审计 JSON |
| 5：有界候选发现与整体验收 | 显式任务标识/来源链接/项目作用域优先生成候选，再可选离线语义候选；统一核验输出 | 单列 candidate recall 与端到端 recall；全部安全门槛通过，展示原文和结构化输出，未证实项保留未知 |

此表是依赖顺序，不要求每步都向用户重新审批；未来实际开发以用户确认的范围为准。保留集在实现和阈值固定后验收，不能一边观察保留失败一边宣称未见样本。

## 3. 关系存储与查询的最小边界

后续建议仅新增一个小型 repository/query 模块（可先同文件，避免提前分层），复用 stdlib sqlite3；必须显式传入新侧库路径，不默认打开/迁移原数据库。

- `task_relation_assessment`：不可变 assessment_id、workspace、typed endpoints、predicate、规范 payload、证据依赖及规则版本；唯一约束保证重放不重复。
- `task_relation_event`：生命周期变更/撤销/纠错追加事件；不覆盖原判定。按 workspace、observation/source refs 建必要索引，不建通用实体/边表体系。
- 来源、观察、DecisionRecord 不复制；引用现有 store/registry/artifact reader。两库不依赖隐含跨库原子事务：每次判定绑定明确 snapshot；查询再次核验依赖，不能仅相信侧库缓存 ACTIVE。
- 写操作发生在摄取后显式关系评估阶段。查询禁止自动建库、修改状态、触发 Provider 或“顺便重算并落库”；无来源目录时不能宣称 CURRENT。
- `list(workspace_id, source_id/project/observation_id, include_history=False)` 返回判定、原文引用、来源当前性、项目绑定依据和阻断原因；`compare_revision` 是只读派生计算。项目检索不能仅用 M1 的字面 project 过滤遗漏未知字段观察。

新增侧库是复用 M1 持久化观察的轻量关系层，不取代 Candidate Persistence。未来接入真实 workspace 的写入边界另按用户授权处理；本轮没有建库、schema migration 或数据搬迁。

## 4. 验证与回归计划

开发后只在全新临时来源/数据库执行离线 scripted/recorded 路径，禁用真实 Provider。按变更逐步跑聚焦检查，最后跑相关回归，不重复执行已通过且没有影响的宽泛检查。

优先复用现有测试入口：

- [M1 单元测试](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/unit/test_task_observation.py) 与 [M1 集成测试](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/integration/test_task_observation_flow.py)：观察 ID、证据、字段 unknown、幂等与查询当前性。
- [供应商原案例/审核集成](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/integration/test_semantic_quality_incident_flow.py)：审核前无正式决策、审核后替代链、Supporting Only 语义保持。
- Decision Memory 的相关现有来源校验、历史/有效期与授权测试，按实际触达接口选择；不为关系层重构其 store/runtime。

M2 整体验收须同时满足：12 案例预声明问题、关系指标及安全零容忍门槛、实际结构化演示、版本/撤销序列、只读查询和相关旧回归。每个结果注明本地离线验证，不写 CI/真实 Provider/生产已验证。设计阶段没有新的测试通过数。

尚未覆盖的能力明确保留：任意自然语言实体消歧、无界影响传播、完整业务任务管理、全项目穷尽性证明、任意时刻双时态数据库查询。不将这些包装成 M2 必需工作。

## 5. 失败处理与验收交付

若发现身份/因果/修订证据不足，收敛为 UNKNOWN 或 CONFLICT，并列出可补查的具体来源缺口；不得放松门槛来提高 Recall。若正例普遍无法确认，先修明确证明类型或候选召回，不以全弃答交付。证据变化导致 STALE 时保留历史，重验生成新判定，不就地改旧观察。

完成 M2 开发后的交付应包括：修改文件与职责；至少 D04、D07、D08、H04 的真实输出和原文；独立 Gold 版本及输入隔离证据；指标分子/分母和错误案例；幂等/来源变化/撤销/旧回归结果；仍不能证明的任务关系及检索范围。

**当前停点：三份草案等待用户确认。没有开发 M2，也不自动进入第一切口。**
