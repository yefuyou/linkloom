# Task Identity Dev Fixtures

本目录只物化 M2 Task Identity 的 Dev 子集：D01、D02、D03、D04、D05、D07。

Gold 先于实现独立确定，依据评测规格中的原文语义和必要证据制定，没有从系统实现、预测或测试结果反推标签。输入与 Gold 路径隔离：

- 输入：tests/fixtures/task_identity/inputs/dev.json
- Gold：tests/fixtures/task_identity/gold/dev.json

输入只包含合成来源原文、已核对观察和 SAME_TASK 候选端点，不包含 expected、verdict 或 Gold 原因。Gold 只记录六个固定 B→A 身份问题的 verdict、必要原文证据和禁止推断。

D04 只物化 B→A 的任务身份观察；D07 保留 A/B 两来源的全部五行原文，观察分别指向 A.txt 第二条和 B.txt 第四条，且 B 的项目字段保持为空。D03 的待核对说明保留在 B.md 正文中，但 B 观察的 task.text 只取第一条任务原话。

task.deadline_text 只保存原文日期 token（例如 2026年10月14日），不包含“前”等日期角色词；日期角色和其他任务语义仍由完整原文 content/task.text 核验。

这是非模型的离线关系评测输入与独立 Gold，不代表模型质量或端到端任务发现能力。fixture 物化阶段未调用 Provider、未运行评测；保留验收集未物化、未执行，也未触碰真实 Obsidian vault。
