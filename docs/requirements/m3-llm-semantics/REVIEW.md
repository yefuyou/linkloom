# 独立只读复核

Reviewer：独立 `final_reviewer` 子 Agent。主 Agent 实现与修复；Reviewer 不写仓库、不调用真实 Provider。
审阅分为真实生成前与生成后；后续离线 scorer 另做执行隔离复核。结论只针对本轮范围。

## 冻结前：PASS

审阅确认：旧 assess_same_task / assess_relation / assess_plan_revision 不在新判定链中，
新模块不生成 RelationAssessment、不执行 Runtime、不写 P4。typed handles、当前来源/授权、
SOURCE_CLAIM 与正式审批回执分离，MODEL_PROPOSED / EVIDENCE_VALID / 独立语义验收边界正确。

发现并在真实调用前修复：

1. freeze 漏掉连接脚本与 Provider/guard 等实际执行依赖；现清单绑定 17 个文件，变化在 connect 前拒绝。
2. `str.count` 漏算重叠引用；改为检查下一个 Unicode 起点，`aaa` 引用 `aa` 回归拒绝。
3. inventory 完整性标志有默认值；现为调用方必填，截断/不完整/空查询均阻止调用。
4. 错工具、多函数/自由文本失败原输出留痕不足；现于结构检查前保存有界 public 输出，
   超限明确记录 truncation/hash/prefix，不保留 Provider continuation 或私有思维链。

主环境最终 22 项聚焦检查通过。Reviewer 自己运行 16 项核心/adapter 检查通过；6 项 smoke
检查曾因 Reviewer 沙箱无法创建系统 pytest 临时目录而 setup error，没有断言失败。
主环境使用仓库已有 Windows 临时目录插件，22 项均通过。没有把环境失败隐藏为通过数。

旧 Provider 扩展测试 62 passed / 83 failed：Reviewer 验证旧 Provider/model_adapter 和旧失败测试
相对基线 diff 为零。主要为测试模型 ID、temperature 与固定 Gemini 3.8 配置不兼容，另有旧 nullable
schema wire 快照不一致。属于既有基线问题，本轮未改写旧测试以消除失败。

## 三次真实调用后：PASS（限定范围）

Reviewer 独立重新计算 freeze 的 17 个执行依赖、输入、request/schema/snapshot 和
evaluation-freeze 的 Gold/input hash，全部相符；聚合预测所绑 canonical freeze hash 相符。
原独立目录只有 attempt/receipt 1–3、三个 case 与聚合结果，均 RETURNED、串行、无重试或第四次。
回执与聚合逐字一致，requested/actual 模型均 gemini-3.8-flash。

Reviewer 逐条回读原文：11 个 resolved proof 的 Unicode offset/quote/hash 精确，typed endpoints
与原模型 handle 一致，7/7 为 EVIDENCE_VALID，semantic_status=NOT_EVALUATED、publishable=false。
变更权威仍为 SOURCE_CLAIM_ONLY。独立读取冻结 Gold 后，verdict 对齐 revision 2/2、causality 3/3、
conflict 2/2；日期结果只为条件性 +4 与 BLOCKED/null。

该 verdict 复核与完整引用覆盖分别报告：两项因果负面/未知提案的显式来源集合不完整，
Gold 完整来源覆盖 5/7；没有将已验证引用等同于完整语义证明，也没有改原预测来补证据。
原提案的阶段不被离线 Review 改写；独立标签复核不产生 P4 发布授权。

其余五个新 Dev、旧 H01–H04 和保留集没有真实执行。没有向模型提供 Gold 标签、理由或 required_sources。
使用量 12,358 input / 1,193 candidates / 13,551 total，thinking 明细全部 null，完整成本保持 null；
USD 0.01374225 仅是公开单价参考值。提交的实际预测与本地原件 hash 相同。

不能外推为候选发现 Recall、真实业务完整影响清单、端到端 Agent 成绩、生产 Adapter、P4 持久化
或新保留集成绩。后续正式接线仍需独立语义验收和显式发布策略。

## 最终离线 scorer / 交付审查：PASS，无阻塞项

Reviewer 在内存中重新评分，与已暂存 semantic-score.json 完全一致，并复算 provenance 中
input/Gold/evaluator/tests 和全部列出 artifact 的 SHA，全部一致。最终 3/8 部分执行和各分子/分母正确。

Reviewer 另外执行了无文件写入的篡改探针：duplicate/extra case、root/case 调用数不一致、
malformed receipt、EXECUTED 但零调用，均不能取得 business score；OUTPUT_INVALID 夹带的 proposal
被清空并按 Gold 漏判。四状态与各谓词计数汇总正确，EVIDENCE_INVALID 即使引文真实也不获 Groundedness。
Fake/scripted 仅进入诊断，Gold 仅由独立离线评分进程读取。主环境 scorer 11 项测试通过。

最终 staged diff 只包含离线 scorer/tests、文档/证据及一行 usage 按请求去重说明，差异检查通过。
Reviewer 允许精确提交、正常推送该独立分支。最终结论为“工程边界验收通过 + 三题固定端点诊断部分执行”，
不能宣称完整 Dev、holdout、端到端或生产语义验收通过。
