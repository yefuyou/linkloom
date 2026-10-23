# AMI 真实 Provider 首轮验收

本轮在原 Head 8ce79f519832f3d5f4eb0655bb8a85e1b7cc55a6 的独立分支执行。结论：现有 F 能识别材料、充电电池、外形调查等决策，也能抽到黏土模型行动项；真实会议的工作分派仍有漏抽，Discovery 存在确定的引号范围误过滤。当前证据不支持宣称真实会议任务能力已验收通过。未改生产 Guard、P4、Reviewer、Compactor、原 Gold 或历史成绩。未运行 Product 端到端。

## 三类证据

|类别|结果|边界|
|---|---|---|
|工程回归|AMI 21、预算/Discovery/提示契约 53、新 Runner 边界 2，共 76 passed|本地验证；不是 CI 或语义准确率|
|公开人工 Gold|既有 BM25/Dense/Hybrid 官方链接检索成绩逐 split 复现一致|摘要句到官方关联 dialogue act 检索，不是 F 任务抽取|
|真实 Provider 探索|2 场完整会议，28 次调用，75 条 claim；Discovery 0 valid / 0 partial|非盲测；未调提示、未重跑、未追加付费|

## 输入与预算

ES2002a 8 块、ES2005c 20 块，完整 canonical 转写，保留原生 speaker、dialogue act ID、相对时间和源 XML 哈希。没有把官方摘要、Gold、查询文本放进 F 请求；未使用检索补充上下文。选择会议已知有官方标注，因此不能称 blind holdout。原始 words/dialogueActs 是转写结构输入；官方 Actions/Decisions 只在输出封存后用于诊断。

直接复用 ProviderNeutralSemanticExtractor、GeminiProviderAdapter、TaskObservationDiscoveryService、TaskProviderSession；通用 F 提示，task_profile=null，FrozenRelationResolver 无 Gold 条目。未另建抽取器。来源均按 manual_text 摄入，原生说话人和相对时间保留于内容与 sidecar，不自动转成 Owner 或绝对日期。

用户确认赠金可用后停止赠金/费用归属调查，credit_attribution=USER_CONFIRMED；本报告没有独立核验项目账单。模型 gemini-3.8-flash，真实成功返回 28/28，SDK attempts=1，F attempts=1，无失败、无自动重试。两场会议不是两次 API：已有 F 每块一次。

Runner 逐次先 fsync 预留 USD 0.10，总 28 次 / USD 2.80；同时限制输入 1,400,000、输出 114,688 token、总 2700 秒、单次 90 秒。请求按序必须与离线计划完全一致，超限停止，失败 poison session，目录不可恢复续跑。预检使用字节数+4096 作保守输入估计；计划上限 575,592 输入，价格保护按输入 1 / 输出 5 USD 每百万 token，高于本次参考表价。预留与估价均小于 USD 3；这是应用 Runner 约束，不是云账户硬额度。

实际输入 **147,690**、候选输出 **8,245**、报告 thought **117**、总 **156,052 token**。部分响应 thought 字段未报告；总数差额 117，与已报告 thought 一致。按 [Gemini 官方价格页](https://ai.google.dev/gemini-api/docs/pricing) 的 0.75 / 3.75 USD 每百万输入/含思考输出估计 **USD 0.142125**；这是表价估计，actual_billed_usd=null，未宣称实际扣款或赠金抵扣额。

## 公开 Gold 复核

官方 AMI 1.6.2 归档 SHA256 b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d；重新 prepare 验证原生位置、成员/文本哈希和官方链接：8 场、6,981 passages、56 query，其中 51 ELIGIBLE / 5 N/E。N/E 保留登记，不进入检索指标分母。dev 33/3，diagnostic 18/2；不是 train/test 泛化评测。

|方法|macro Recall@5|macro Recall@10|MRR@10|最终证据 micro coverage|
|---|---:|---:|---:|---:|
|BM25|0.457516|0.495098|0.495829|39/100|
|Dense|0.475490|0.584967|0.504630|37/100|
|Hybrid|0.511438|0.616013|0.564807|43/100|

Recall 对每 query 的命中官方目标比例取平均，分母 51 query；Hit/MRR 也以 51 为分母。micro 是 100 个 query-target pair，非 97 个去重 dialogue act。dev 57、diagnostic 43 pairs。top10 命中 pairs=44/48/52；默认 max_evidence=5 选择后39/37/43，选择损失5/11/9，assembly/render 未进一步损失。最终 coverage macro 与 micro 不可互换。五条 N/E 均为 NO_OFFICIAL_LINK：ES2002c s14、s15；ES2002d s8；ES2005a s8；ES2005b s10，逐条记录见 reproduction/revalidated_qrels.jsonl。完整各 split 及 N/E 原因见 reproduction/scores.json、bad_cases.json。

检索器输入只有 manifest、passages、queries；官方摘要句是此公开检索协议的 query，Gold/evaluator_only 不进入预测。Python audit hook+严格 payload whitelist 是应用隔离，非 OS 沙箱；记录 denied_data_opens=0、network_attempts=0。Dense 只用已缓存模型，本轮复现 Provider 调用0。fresh-score 与历史每方法每 split 完全相同，comparison.json 留证。

## F 输出与逐例诊断

75 claims：FACT18、PLAN5、QUESTION9、PROPOSAL23、OPINION14、SPECULATION1、DECISION5、ACTION0。全部28次F解析成功。5 个任务候选 Owner 均 null（UNKNOWN）；未把 speaker 自动当负责人，也未强行补绝对日期。null 表示未解析，不表示官方没有负责人。日期正确性未建立可评分 Gold。

|官方案例（ID末尾）|本轮发现|损失定位|
|---|---|---|
|ES2002a s4 工业设计工作|完整明确分派存在于 B196，F 无任务|模型候选发现漏抽|
|ES2002a s5 UI 技术功能|B199 是省略片段，未抽|跨话轮恢复诊断，不强算漏召回|
|ES2002a s6 市场需求|B202+B203 分离角色/工作，未抽|跨话轮角色与工作绑定诊断|
|ES2002a s7 售价25欧|相关约束被视为 FACT，而非 DECISION|brief既有要求与会议新决策的本体差异|
|ES2002a s8 国际销售|相关内容按事实/讨论处理，无 DECISION|官方链接本身含问题，不能机械视为已批准决策|
|ES2002a s9 成本12.5欧|成本约束按 FACT，无 DECISION|约束/决策分类诊断|
|ES2005c s12 黏土模型|chunk17 claim0 为 PLAN，逐字工作原文保留，Owner/日期 UNKNOWN|F已发现；Discovery NON_ASSERTED_TASK误过滤|
|ES2005c s13 橡胶/乳胶外壳|chunk14 claim0 DECISION spongy latex rubber|语义对应；官方复合链接不作严格逐字P/R|
|ES2005c s14 多外形公众选择|chunk10 claim0 DECISION，设计2–3种外形+调查|有后续认可，不能仅因原句含 can 就判假阳性|
|ES2005c s15 黄色/有机形态|没有完整覆盖复合决定的 DECISION|复合语义漏项/分类诊断|
|ES2005c s16 语音识别|明确 definitely going for 原文存在，未形成对应 DECISION|模型分类/发现不足|
|ES2005c s17 充电电池+底座|chunk16 claim0 抽到电池，未完整保留充电底座复合含义|部分语义覆盖|

另有 DECISION：不装LCD（chunk16 claim1）、basic chip set（chunk18 claim0）。它们没有在本次所选官方摘要句中单列，不因 Gold 未覆盖就算假阳性。basic chip set 原句为 And we're using this our basic chip set；邻近成本条件和设计讨论仍需完整人工判定。本轮未证实一个可严格计数的“提议误判已批准”总量；23 PROPOSAL 与5 DECISION分开保存，模型置信度不当作正确率。

正式 F semantic Precision/Recall = **NOT_EVALUATED**。官方 Gold 是摘要句与支持话轮关联，既非穷举全部任务/决策，也不逐字段标注 Owner/日期/批准状态，不能拿51检索分母来计算F质量。封存后只选出两条单一关联话轮包含完整工作陈述的 action 做精确原文诊断：工业设计、黏土模型，F精确quote覆盖1/2，Discovery0/2。该子集事后确定，仅 EXPLORATORY_DIAGNOSTIC，不是完整任务Recall，也没有Precision。其他案例只报告诊断。所有13条官方案例、原生引用、claim IDs见 analysis/official_cases.json。

## 模型与程序损失

5 PLAN 候选全部被拒绝：4 NON_ASSERTED_TASK、1 TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS。生产逻辑没有被改动。

确定的程序问题：text_scopes.quoted_ranges 把口语撇号当引号开头并扩展到块尾。黏土模型 chunk17 的 `'Kay` 开始于868，引用范围延伸至7945；真实任务位于974之后，于是 non_asserted(full chunk)=true，同一任务单独传入=false。finance、三十分钟会议和 technical considerations 也受 `'round` / `'Cause` / `'Kay` 范围覆盖。详细区间与最小对照见 analysis/veto_diagnostics.json。这个对照定位引号范围的因果影响，未声称修复后所有任务均应接纳。

模型问题：工业设计明确分派未发现；语音识别决策漏分类；“会议将在三十分钟后举行”被输出为 PLAN.task，是日期/事件角色错误；旧遥控器检查与复用的复合候选跨 canonical 话轮头拼接，完整 task.text 不是唯一连续原文，grounding拒绝合理，不能归罪于filter。Owner保持UNKNOWN避免speaker=owner错误，但说明多角色绑定能力尚无证据。没有因本次未见的行为虚构Speaker错误率。

Discovery专用于任务投影，仅PLAN/ACTION进入，因此DECISION不生成任务是契约行为。Product未运行，不能把下游未保留记为0或归罪Product。当前零可见任务至少包含一个已证实的程序过滤损失；也不能全算作模型漏抽。

下一步优先：另立小范围修复引号边界，增加真实口语撇号回归；其次用独立冻结案例改善F对多角色工作分派、决策确认和复合原文引用的发现/提示。暂无证据要求改P4或Product。修复和新批次需单独实施，本轮停在证据交付。

## 原始材料与复现

- live/actual_requests.jsonl：28条实际模型请求；planned_requests.json：精确离线计划。
- live/sdk_raw_responses.jsonl：完整SDK响应，含模型原始JSON文本、finish reason与usage。
- live/model_responses.jsonl、discovery.jsonl：Adapter归一输出、F claims、Discovery失败原因。
- live/sources.json、native_sidecars.json：原始输入、XML来源、speaker/时间/位置/哈希。
- live/provider/budget.json、plan.json、completion.json、output_seal.json：逐次预留、限额与封存。
- analysis/claims.json、tasks.json、per_call_usage.json：逐案例全输出索引与费用计算。
- reproduction/：本地通过的JUnit、重算检索指标、预测隔离manifest、比较结果。第一次pytest因Windows临时目录设置失败，修正既有插件配置后21通过，不隐瞒为首次全绿。

使用现有 evals/ami_public_v1/ATTRIBUTION.md 的 AMI CC BY4.0 归属；此目录转写/输出为源资料衍生证据。原归档不重复打包。环境 google-genai2.22.0；模型调用与提示不保证未来逐字可重复，封存输出可离线分析复现。

在仓库根执行（ARCHIVE指定官方本地zip，勿把 --execute 当免费命令）：

```powershell
$env:PYTHONPATH='src;.'
python -m linkloom.evaluation.ami_public.prepare --archive $ARCHIVE --reference-root evals/ami_public_v1/frozen --output work/ami-fresh-data
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
python -m linkloom.evaluation.ami_public.predict --inputs work/ami-fresh-data/inputs --output work/ami-fresh-run
python -m linkloom.evaluation.ami_public.score --data work/ami-fresh-data --run work/ami-fresh-run --reference-run evals/ami_public_v1/frozen/reference_run --output work/ami-fresh-score
python scripts/run_ami_semantic_canary.py --inputs work/ami-fresh-data/inputs --output work/ami-free-plan
python scripts/analyze_ami_semantic_canary.py --run docs/evaluation/ami_semantic_canary_v1/live --data work/ami-fresh-data --output work/ami-offline-analysis
```

付费入口为 run_ami_semantic_canary.py 同参数另选新目录并加 --execute，需要现有 GEMINI_API_KEY；本轮已经运行，不应为复核重调。离线分析先校验封存再打开Gold。执行时生产代码Head为8ce79，Runner尚未提交，脚本内容哈希见 reproduction/comparison.json；交付提交不冒充调用时Head。

工程回归可在新临时目录合并运行（保持旧日志）：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:LINKLOOM_PYTEST_BASETEMP=(Join-Path (Get-Location) 'work/ami-review-tests')
python -m pytest -q -p scripts.pytest_windows_tempdir --basetemp=work/ami-review-tests tests/unit/test_ami_nxt.py tests/unit/test_ami_public_evaluation.py tests/unit/test_m3q_provider_budget.py tests/unit/test_task_observation_discovery.py tests/unit/test_semantic_ingestion_prompt_contract.py tests/unit/test_ami_semantic_canary.py
```
