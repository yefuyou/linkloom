# 自动接纳与工程归因

`v2_contracts.admission` 只有 REJECTED、EXPERIMENTAL_ACCEPTED、INTEGRATION_CANDIDATE 三种输出，永远附带 production_activation=false。正式默认配置不被写入。

Gate A 比较 Dev/Validation/Stress/一次 Final，要求无原正确案例退化、无保护边界违规、调用与累计上下文字节不超过 Baseline 的110%，并在 Validation 和 Final 有准确率提升或至少10%效率提升。冲突判定要求实际 Read 到必要 passage、正确保留冲突和上游事实。数据资格缺陷单独否决，不能靠候选高分掩盖。

Gate B 使用准确基础提交92c0fd2的 Git archive，在同一环境、同一源码与测试、同长度临时路径中分别运行 Base 与实验候选。候选仅通过 pytest 实验插件注入 Host 查询策略，API key/live flag 从测试环境移除。逐 node 比较已有失败、新失败、collection、环境失败和未执行项；不删除历史测试，不复制其他工作树的脏脚本。

Core、Historical、Candidate Differential 和 Full Repository Attempt 分别报告。Core 没有新增失败也不等于全仓绿；Full collect-only 不等于执行完成。未完成 Core 会阻止接纳。历史已有失败保留，不自动归罪于候选；只有相同节点和条件的配对结果才支持归因。

原始文件采用 exclusive-create；恢复只复用完整离线产物，损坏即停止。冻结输入逐文件核验；Final 有冻结身份全局锁及每策略一次尝试标记，不能重跑后调参。真实请求 ledger 和批次限制独立校验，未知结果不重放。

本版冻结集已知存在3处 span 标注缺陷，DATASET_QUALIFICATION.json 中 qualified=false。因此本轮即使发现局部收益，最终可靠泛化接纳仍必须 REJECTED；其他退化原因也会继续记录，不能只报这一项。

## 实际工程证据

从可信的同一92c0fd2快照恢复了已提交 docs 夹具；没有从其他工作树复制文件。完整快照与 tar 的内容检查保护两次测试之间的输入一致性。最初 Harness 的 collection hook、内存地址归因噪声、漏提取 docs 和 Windows 路径问题的失败尝试均保留在 ignored work，未拿它们冒充候选失败。

| 层 | 同时通过 | Base已有失败 | 缺失环境/产物 | 收集错误 | 候选新增失败 |
|---|---:|---:|---:|---:|---:|
| Core（236节点） | 194 | 10 | 32 | 0 | 0 |
| Historical（176节点，含错误） | 88 | 61 | 23 | 4 | 0 |
| Full Repository Attempt | 未执行 | N/A | N/A | 4 | N/A |

Full 仅收集到2290项，全部未做全量执行；四个收集错误来自基础提交缺失的两个历史脚本。对这两条脚本路径执行 `git log --all` 未找到可信提交，未制造替代脚本。其他缺失包含 ignored `work/phase9_gemini/.../observed.json`，不能冒用其他工作树产物填充。历史 Gemini/多动作测试契约失败保留，按相同节点和失败指纹归为基础已有问题，不等同候选因果失败。

本次 Core 没有收集缺口，但32项输入不可用，所以 core_complete=false。预付费检查只要求已实际完成配对诊断，不要求所有历史问题都绿；接纳则继续要求完整核心证据。两者不是同一个门槛。

回归插件只注入 Host query strategy；同 query_mode、read_mode=all、limit=Baseline 的 query-only 候选具有相同被测变更，因此复用该 Host 证据。控制器阅读配置的影响由独立离线试验承担，不能宣称 Core 证明了真实模型会按同样策略阅读。

最终五个非Baseline配置全部 REJECTED。query-only 在已测离线案例无原正确案例退化，但数据资格缺陷和 Core 未完整仍否决；其余候选还触发逐例退化门槛。Final 仅一次，已将暴露身份提交到 FINAL_EXPOSURE.json；新实验不能把这份数据再当陌生 Final。原实验可安全读取封存产物恢复，实测恢复未新增模型或 Runtime 执行。
