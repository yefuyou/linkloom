# F 真实模型 A/B 准备交付

本地移交更新：下文记录上一轮准备状态。当前已授权仅提交、推送到独立分支，
由本地Codex接手执行；本Work不获取Key、不运行Provider、不续写#22账本。
导入和后续执行要求见 [LOCAL_HANDOFF.md](LOCAL_HANDOFF.md)。

状态：**PREPARED_BLOCKED_SHARED_BUDGET_CONTINUATION**。
候选和请求已冻结；本轮新增Provider调用0，未生成ON真实响应，未提交、推送或合并。
未修改#18、#19、Canary Runner、Grounding、Discovery、Reviewer。

## 候选与默认开关

继续 `codex/f-cross-turn-discovery`，基线仍为
`1bfd910bb3a53a902a708ca2d00a39f4dde68091`，生产Diff保持38行新增、0删除。
`extraction.py` SHA-256：
`824adc88c85fd9b5005d7d27595b2c507f5c6e1643636b27abe6759025be296a`。

OFF不传新版本，仍默认 `semantic-extraction/v1`；
ON显式传 `prompt_version="semantic-extraction/meeting-discourse-v1"`。
profile仍决定原有schema；没有Product默认开关变更。冻结Diff、配置及源文件哈希分别在
[candidate.diff](ab-preparation-v1/candidate.diff) 和
[code-freeze.json](ab-preparation-v1/code-freeze.json)。

准备脚本只有离线入口，不含 `--execute`、SDK构造、网络发送、预算Session构造或账本写入。
它通过现有Extractor的发送前guard捕获请求后立即阻止调用。这类
`preflight_blocked` 是离线捕获行为，不是一次真实Provider失败或新增预留。

## 原配置 Canary 已完成

读取并验证了更新后的独立结果提交
`3e7890866a7e17ce79fcb84f6defd95b1c07b6a5`，其中封存运行代码为
`1ade9b2b359b0249ed21678ad09e6aa4d5e82c83`。

- Run ID：`f7023e44-4511-4865-b884-f0d001dfd885`。
- 两场完整会议28/28成功，0 UNKNOWN，0未执行计划请求；非形式化语义验收。
- 对证据ZIP及745个成员逐项核对哈希，再核对live/output_seal。
- 当前OFF任务请求重建后，与全部28份计划请求及实际发送请求逐字一致。
- 该Canary使用TASK_RECALL_V3，决策发现为N/E；0 DECISION不是失败率。

模型/生成设置从原plan读取：`gemini-3.8-flash`、4096输出token、low thinking、
无temperature、单次F/SDK尝试、无Compactor。没有重新核查Google Cloud赠金。

原报告：[Canary结果](https://github.com/yefuyou/linkloom/blob/3e7890866a7e17ce79fcb84f6defd95b1c07b6a5/docs/evaluation/canary_trust_v2/REPORT.md)。

## 已冻结的可比较请求

完整包见 [PLAN.json](ab-preparation-v1/PLAN.json) 和 `ab-preparation-v1/requests/`。
共10组、20份新试验请求；没有把旧OFF响应重新命名为新配对试验。

| Pair | 原封存窗口 | 契约 | 主要诊断 |
| --- | --- | --- | --- |
| task-a06 | ES2002a chunk0006 | TASK_RECALL_V3 | 工业设计、营销、界面任务、会议时间负例 |
| task-c06 | ES2005c chunk0006 | TASK_RECALL_V3 | 旧遥控器检查/复用的连续引文 |
| task-c17 | ES2005c chunk0017 | TASK_RECALL_V3 | 黏土模型真实工作、UNKNOWN角色 |
| general-a06 | ES2002a chunk0006 | general/null | 相同分派与会议时间误报，保留旧general契约对照 |
| general-c05 | ES2005c chunk0005 | general/null | 语音识别强语气后的are we?限定 |
| general-c10 | ES2005c chunk0010 | general/null | 外形调查建议与采纳 |
| general-c11 | ES2005c chunk0011 | general/null | 语音识别选择/已有状态/确认程度 |
| general-c14 | ES2005c chunk0014 | general/null | 材料、颜色、有机形态的复合部分 |
| general-c15 | ES2005c chunk0015 | general/null | 电池和底座方案，窗口内证据不足处 |
| general-c16 | ES2005c chunk0016 | general/null | 电池确认与底座功能的后续 |

两臂的source_segment及原始完整窗口、角色头、native ID、换行、时间、message map、
模型、schema、temperature、thinking和输出预算均相同。只允许候选提示增量、general
prompt_version标签及随指纹自然改变的run/turn/task ID变化。没有修改输入去提示答案。

OFF任务请求与已完成Canary的v3请求一致；OFF general请求与同一Canary代码封存的
offline-null-plan一致。后者是**离线计划**，不是新的真实general响应；真实decision
对照仍须两臂独立发送及既有准入资格，不能用v3的结果替代。

来源仅来自Canary封存的转写。构造脚本不读取query、摘要、qrels或Evaluator案例标签；
本地交付不包含Evaluator-only Gold、官方摘要或标准答案数据文件。
请求包有各文件哈希和每臂request/prompt/schema/config哈希。

## 12项预登记诊断与独立统计

Evaluator侧冻结工业设计、营销、界面省略分派、会议时间、语音识别tag question、
语音识别后续、黏土模型、公众调查、材料/颜色、电池/底座前后两块、旧遥控器引用等12项。
它们全部是已曝光开发案例。每项记录native ID、原话和判断边界，不能称为盲测或正式Gold。

界面省略与语音识别确认程度保留人工判定空间：不要求凭名词片段造任务；
也不把definitely后接are we?截成已确认决定。电池和底座跨块的缺口仍明确保留，
没有为通过评测而拼成新窗口。

待真实执行后分别填写 [REVIEW_TEMPLATE.json](ab-preparation-v1/REVIEW_TEMPLATE.json)：

| 统计层 | 记录内容 |
| --- | --- |
| 任务发现 | 原始PLAN/ACTION与task候选、解析损失；与最终接纳分开 |
| 决策发现 | 原始DECISION及其人工确认程度；task-only路径为N/E |
| 精确引用 | task.text和description_parts的连续、唯一、逐字检验；不修正坏输出 |
| Discovery | VALID、PARTIAL、拒绝原因；保持同一固定实现 |
| 真实工作核验 | 每个新增工作提供来源ID/精确原话/人工判断；UNKNOWN字段不补造 |
| 假阳性 | 单列会议时间、未采纳讨论、错误确认、错误角色/日期；PARTIAL误报仍是误报 |

general Schema的DECISION没有逐claim精确引用字段，不能伪造“DECISION精确引用通过率”。
其任务引用可机械统计，决策本身需要独立的来源核验，缺相应证据字段时引用统计为N/E。
结果模板均为null/NOT_RUN，未执行不能写0通过或0误报。

独立保留每臂精确请求、SDK原始响应、规范化结果、实际model_version/response ID、
finish reason、Token/预算回执及代码身份；不覆盖旧Canary输出。任何UNKNOWN保留预留，
不自动重发。完整窗口和生成参数在真实发送前须再次对冻结哈希检查。

生产PR门槛已预登记：至少增加可由来源核验的真实工作；仅PARTIAL数量增加不足以通过；
新增明显假阳性则暂不提交。引用正确、语义正确、业务批准仍分别判断，本试验不做业务批准。

## 共享预算阻塞

以下是已封存原账本的算术余额，**不是本任务获得的可写预算分配**：

| 项目 | 原上限 | 原已保留 | 算术余额 |
| --- | ---: | ---: | ---: |
| 请求 | 30 | 28 | 2 |
| 输入Token保守上界 | 1,400,000 | 718,615 | 681,385 |
| 输出Token预留 | 122,880 | 114,688 | 8,192 |
| USD预留 | 8 | 7 | 1 |

完整20请求按原有保守费率/单次最低预留计算需要USD 5、81,920输出Token，超过剩余。
不得按约USD 0.142的实际Token表价估算退回USD 7预留，也不能把新Session从0开始，
或让另一个AttemptJournal重新获得30次/USD 8。

原Runner是fresh-run、一次性封存，没有resume路径。当前可见的账本是只读ZIP里的副本，
没有可核验的活跃共享续用接口。原始计时上限也不能在新进程中悄悄重置。
因此本次不实例化新Session，不从副本“接着扣费”，不消耗上述两次理论余量。

仅从请求和输出余额看，至多容纳1组两臂；即使选task-a06也只覆盖其中任务/时间诊断，
不能把未执行的general语音识别试验宣布完成。当前未选择分配或静默缩减试验范围。

真实执行的下一项输入是：由Canary预算持有任务提供**同一预算下可核验的续用入口/预留票据**，
记录父Run ID、已保留28次/USD 7、剩余额度、计时处理及单写入者，明确哪一完整pair获分配。
这属于预算交接信息，不需要再次确认赠金。没有该交接前保持NOT_RUN，不另开费用池。

## 固定运行时代码边界

本工作树仍是1bfd基线加38行候选；Canary修复运行时是1ade。两者Extractor基线的Git对象
字节相同，因此请求可逐字复现。实际归档运行源码采用CRLF、本地采用LF；Extractor及
7个保护文件只存在换行字节差异，text_scopes另有实现差异。原始哈希分别保留，
没有将换行归一化用于来源文本或伪造运行身份。本地旧的引号实现不等于完成Canary的运行时。
后续两臂必须使用同一1ade固定的Grounding/Discovery/Reviewer及来源校验，仅叠加这38行
候选并切换Prompt版本，重新证明代码身份与离线准入；本次没有改写或搬入这些受保护代码。
准备包记录两套源文件哈希，不将本地旧代码投影算为真实A/B接纳结果。

## 验证与复现

- 新准备测试9项，加既有候选30项：**39 passed**，零失败、错误或跳过。
- 验证了来源/模型变更不可冒充Prompt差异、所有请求哈希、继承预留和UNKNOWN不退款。
- 原生产候选哈希与38行Diff保持；保护文件与本地基线一致；默认仍OFF。
- 没有重跑全仓测试，未将39项离线测试称为语义召回提升。

```sh
PYTHONPATH=src:. python scripts/prepare_f_ab.py \
  --reference work/f-ab-canary-reference \
  --output /tmp/linkloom-f-ab-new
PYTHONPATH=src:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tests/unit/test_f_ab_preparation.py tests/unit/test_f_cross_turn_candidate.py
```

reference目录是上述结果提交中原始`evidence.zip`和`EVIDENCE_MANIFEST.json`的只读副本。
输出必须为新目录。新脚本、测试和准备包留在独立分支，不推送到主线，不修改原Canary。
