# M3-O 独立只读审查

审查者为用户指定默认模型 `gpt-5.6-luna`、推理强度 `max` 的独立 Agent
`/root/m3o_independent_review`。审查对照指定基线，读取实际源码、冻结包、测试和
证据；审查者没有修改文件、运行真实 Provider、提交或推送。

首轮结论 REQUEST_CHANGES，共两项 P1、三项 P2。主开发 Agent 补最小复现后修复：

| 问题 | 真实修复 | 证据 |
|---|---|---|
| P1 不支持的时间范围仍进入通用 Runtime 读取 | Host 准备标记 BLOCKED；原服务不启动 Runtime；两个通用工具执行器均在读快照前拒绝不支持范围 | review-red.xml 的 inspect PARTIAL/关系 INVESTIGATING → review-green.xml；最终受影响回归含执行器直接拒绝测试 |
| P1 额外来源绑定撤权后回执仍有效 | 每个绑定 span 检查 Workspace、原文、注册存在性、hash 和 version，签发与重验共用；撤掉 counter-source 后拒绝回执及新读取 | review-red.xml 撤权仍接受 → review-green.xml |
| P2 执行阶段打开评分文件字节 | 执行只读 manifest、代码、来源与配置；评分阶段才打开并校验 rubric、labels、groups；直接拦截 Path.open 的测试 | review-red.xml → review-green.xml |
| P2 Parser/Identity 不在实现冻结内 | 加入 artifacts、document_text、timestamped_text；同时绑定 ModelRequest 和 Gemini Adapter，并固定 LF 字节 | freeze_manifest.json 的 code_files 与 .gitattributes |
| P2 顶层 Schema 错误把原始候选丢掉 | 保留最多 64 条原始 claim 中的任务对象，独立报告 Schema 失败和截断，不归因为模型遗漏 | review-red.xml 的 extra_root/over_limit 两例 → review-green.xml |
| 复审 P2 混合计划的 inspect 暴露计划外观察 | 只要存在就绪 inspect 就限定绑定目标，关系单独计划保留既有行为；任意外部 anchor 被拒绝 | mixed-red.xml 复现未拒绝 → mixed-green-final.xml 34 通过 |

`review-red.xml` 为 6 失败 / 11 通过；`review-green.xml` 为 17 通过。随后加入
执行器直接拒绝和签发时来源撤权检查。格式不合规的正确拒绝也从“错误拒绝”中
分离：`host_filtered_units` 记录丢失，`format_failure` 解释原因，Schema 合法才计
错误拒绝；`format-rejection-red.xml` → `a-accepted.xml` 13 通过。

主开发还纠正了合法跨消息分组的 raw 计分、冻结 hash/parse 竞态和 Windows
checkout 字节可复现性。审查者确认这些修复方向与证据成立，不将 Mock 计分当
模型语义提升。初版和全部中间证据保留在本地 `work/m3-o/`。

最终独立复审：**PASS**。审查者确认六项 P1/P2 全部闭合，没有发现剩余 M3-O
范围内缺陷；最后 34 项包括真实混合计划的产品 Runtime 路径、默认/指定目标
读取及 COMPLETED 回执范围。此结论不等同于真实模型质量或全仓库全绿。

保留边界：本轮没有真实语义评测；没有独立业务关系确认进入 P4；没有新的自动
重规划循环。全量测试不绿，首轮 241 失败 / 4 收集错误；14 项环境失败复跑
通过，227 项保留，不能把全部保留问题都断言为已证明的历史基线。未修改旧
Gold/响应/账本来隐藏失败。
