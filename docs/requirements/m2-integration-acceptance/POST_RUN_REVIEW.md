# 首跑后的独立复核与评分补核

日期：2026-10-18。独立只读 Reviewer：`/root/final_reviewer`。

## 时间交叉与不可变证据

1. Reviewer 先给出 PRE-RUN PASS，主 Agent 将该消息记录在
   [PRE_RUN_REVIEW.md](PRE_RUN_REVIEW.md)。该文件是当时收到的结论。
2. 实现和验收工具固定为 `312d717893b3c5d4d759701b4fc3364650401c22`；
   冻结清单随后单独提交为 `06a44af7171f3173a746e569ac0e98152366a0ec`。
3. 主 Agent 开始首次 predict。H01–H03 已写出时，Reviewer 的后续消息到达，
   撤回先前 PASS：原 scorer 没有完整检查业务阻断理由，且 ChangeRef 端点
   未深比较来源版本、hash 和 Unicode span。这两项均属 Required。
4. 首跑按原冻结实现完成 H04。原 scorer 原样产生 FAIL；没有改源码、Gold、
   原 scorer 或首次预测，也没有重跑 M2。保留集从此已执行、已曝光。
5. Reviewer 在自己读取任何预测前，依据原 SPEC 给出稳定的补核期望：
   修订声明、缺年份、缺直接桥接、冲突、明确无业务修订的 reason codes，
   以及全部三题 H02 ChangeRef 的完整证据和有效依赖要求。
6. 新增独立 [audit_m2_final.py](../../../scripts/audit_m2_final.py) 只审计同一批
   首次预测。首次评分和首次补核均保留；最终补核另存 reviewed 文件。
   这是曝光后的评分审计，不是新的盲测或新的业务运行。

首次补核 `post-exposure-audit.json` 是 v1；独立端点覆盖与 dependency 有效性
修正后，`post-exposure-audit-reviewed.json` 是最终 v2，包含 audit_script_sha256。
两者均保留，v2 取代 v1 作为最终补核证据，不覆盖任何首次业务输出。

## 补核结果

独立 Reviewer 已复算首次预测、Gold、来源快照、freeze 哈希，233 个冻结
文件 0 mismatch；与主 Agent 的独立补核一致。H02 三题 ChangeRef 的全部
类型字段、null 审核 ID、完整 EvidenceQuote、当前有效 dependency 和权限
检查均通过。UNKNOWN 的 H02-3 有正确的直接桥接缺口说明；其余修订/影响
问题未满足预期状态或理由。原始 FAIL 与补核后的 FAIL 一致。

Reviewer 要求候选 Recall 的目标从 public question + 独立物化快照构造，
不能使用预测 assessment 的端点。补核脚本已修正，并加入错误预测端点仍
不能获得候选 Recall 分的 Dev 反例；有效 dependency 还核验 observation_valid。
最终补核的谓词提示候选 Recall 为 6/8，固定候选判定 Recall 为 5/8。

补核脚本开发保留两次工程失败：局部变量覆盖了 snapshot 参数，随后又覆盖了
多 snapshot 的 object 端点。均未改变业务预测；多问题、多 snapshot 的 Dev
探针已覆盖，最终三个补核测试通过。首个补核文件仍保留，不冒充最终版本。

## 复核边界

独立 Reviewer 允许将**诚实记录 M2 FAIL 的进度集成基线**提交并安全快进推送；
该许可不等于 M2 语义验收通过。B/C/P4 的集成契约、候选遗漏非否定、完整
当前观察上下文、RELATED_TO 双证明方向、P4 撤销/过期以及 M3 的 Gold 隔离
和 NOT_RUN 边界均属于本轮复核范围。

原冻结 scorer 在运行器工程失败时会跳过该案例的原子分母，不能用来认证未来
故障运行的 Recall。首跑四题均正常产出，没有触发该路径；本次分母完整。
后续使用 scorer 前应补齐故障分母与归因。当前材料只支持本次明确 FAIL，
不将原 scorer 当成无缺口的通用验收产品。

最终逐项结论与实际证据见 [DELIVERY.md](DELIVERY.md) 和
[曝光后最终补核](evidence/holdout/post-exposure-audit-reviewed.json)。

最终独立审查结论：**APPROVE 提交此 FAIL 进度基线**。远端目标仍精确为
`eee882f4c676e900ee8895ac5b09f5fb2b3e6e36` 且可正常 fast-forward 时，允许
非强制推送。Reviewer 终检 staged diff、233 个冻结文件、完整首次预测副本、
v2 审计脚本 hash、381 项测试证据及上述时间交叉均一致，无当前集成交付阻塞。
M2 语义失败和原 scorer 故障分母路径仍为后续 Required，不批准生产 M3。
