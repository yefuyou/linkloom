# M3-E 独立只读复审

结论：**PASS，当前实现与离线证据可接受，无剩余阻断项。**

审阅者为独立子 Agent `context_runtime_review`，按项目规则使用
`gpt-5.6-luna / max`，主实现者未参与其判断。审阅过程仅阅读源码、文档和
离线工件，并运行已有证据的只读核验；没有编辑文件、Git 写入或真实 Provider 调用。
本报告由主 Agent 根据其独立报告落盘。

范围为本轮相对正常 C/D merge 的实现、Runtime 接口、E 测试、229 项合同 JUnit、
旧回归已知失败对照，以及 7 条实际 Fake/REPLAY 轨迹。
重点检查上下文丢失、恢复授权、Checkpoint/Ledger/模型工件一致性、
重复模型/工具请求、预算不足时的行为和原工作流兼容性。

## 初审问题与修正

| 初审问题 | 修正与证据 | 复审 |
| --- | --- | --- |
| 把所有引用同时当作支持/反证，丢失真实 polarity | `_polarities` 递归读取实际 receipt 的 supporting/counter、span_ref 和显式 role；仅映射真实引用，support-only/counter-only/shared/unclassified 互斥组；独立回归验证未分类来源不会被标为反证 | 通过 |
| 每段回补独立读取可能混合多个来源快照 | `TaskEvidenceBridge.session` 冻结一次已验证快照，整批 catalog/recovery 使用同一份数据；结束后重新读取并对比完整合同 hash，中途变化拒绝 | 通过 |
| direct recovery 未校验完整 Workspace/竞争观察/端点/queries/source handles | 所有 fresh capture 先复用 D build_semantic_context；五种合同破坏均无法成为有效原文 | 通过 |
| 无分配空间时 coverage 列表为空 | coverage_before_compaction 与最终 BLOCKED 均明确子目标、missing IDs 和原因，不把缺原文或无空间描述为完整调查 | 通过 |
| 恢复配置未绑定注入 Tokenizer 的实现身份 | 显式 tokenizer_id 和确定性 probes 纳入配置；注入计数器未声明 ID 则拒绝，冷恢复配置变化在新请求前拒绝 | 通过 |

专门的审阅回归位于 `tests/unit/test_context_runtime_review_guards.py`（11 项），
并补充了冷恢复 Tokenizer 变更测试。E 共 39 项；最终 E/C/D/P4 集合 229 passed。

## 关键复核结果

- 独立运行 `python scripts/evaluate_m3_context_runtime.py --verify work/e-evidence-reviewed-02/portable`：
  7/7 ZIP PASS、294 个精确模型 JSON 工件通过 hash/身份校验。
- 长历史真实请求 131,573 bytes 经自动压缩后为 43,310 bytes；7 次压缩，
  原 Ledger 8 条、该案例原模型目录 93 个工件均保留，没有覆盖原始业务证据。
- 冷恢复后没有重复的外层/内层请求；原模型工件逐字节不变，
  语义工具 observation 已耐久化和 Final 已耐久化两个边界都有实际测试。
- WorkingContext 的目标、未完成问题、失败、冲突、负面证据和原文引用在压缩前后不变。
  摘要只能成为操作提示，唯一业务证据由授权 loader 重读原文获得。
- 记录身份通过真实 request 的 audit、raw Ledger/Checkpoint 副本 SHA、模型工件 SHA、
  显式来源映射和完整授权合同绑定；源码与证据一致。
- 来源改变/撤销/授权丢失/截断、P4 生命周期撤销、篡改的上下文工件和预算不足均拒绝。
- 旧回归 134 passed / 16 failed，失败 ID 与既知 D14+C2 的并集一致；
  没有把已有失败隐藏或宣称旧套件全绿。

## 保留的低优先级建议

`RuntimeWorkingContext._restore` 对当前 model/ledger 记录按唯一 ID 对账，
Runtime 内尚未额外要求快照记录为当前记录的**同序 append-only 前缀**。
正常 Runtime 的唯一 ID、loop 行为与现有 SHA 检查有效，portable verifier 也逐项
核对了本次实际轨迹的同序前缀；未发现重复 Provider 请求。
独立审阅将此列为非阻断的后续不可信 Checkpoint 防护增强，本轮未扩大恢复协议范围。

保留 D 的 terminal Ledger/observation 提交歧义窗口安全失败限制。
没有新增真实 Provider 验收、独立业务语义验收、P4 Promotion 或生产成功率结论。
