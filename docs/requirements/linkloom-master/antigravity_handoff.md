# Antigravity 实施交接

你是 Linkloom 的主要 **Worker**，不是 Planner 或 Reviewer。你可在已批准 Master 工作包（WP）内连续完成合理的小任务，不必为每个函数询问；不得跨 WP、改变验收标准或替代独立 Reviewer。

## 启动步骤

1. 读根目录 `AGENTS.md`、`SPEC.md`、`DEV_SPEC.md`、`docs/PRODUCT_ROADMAP.md`。
2. 按 [README.md](README.md) 顺序读本 Master Requirements。
3. 读 Scanner feature docs/code/tests；读 `relation_eval/`、relation_vault、Gold Dataset 和安全 mock config。不得打开、执行、复制或打印含凭据的真实 Provider config，直到 credential blocker 被解决；运行当前测试并如实记录。
4. 输出 current-state acknowledgement：完成/实验/未完成/冲突/当前 WP。
5. 如果没有人类明确批准某个 WP，只能停在 acknowledgement；否则从获批 WP 开始。

## 必须复用，禁止重复实现

- 不重写 Scanner，不重建 sample vault。
- 不重新生成 relation_vault 或第二套 Gold Dataset。
- 不创建第二套 relation_eval、schema、scorer 或无必要框架。
- `relation_eval` 的可验证逻辑可以小步迁移，但必须保持 v1 数据集和结果含义可追溯。

## 实施自由度

在数据合同、允许文件范围和非目标内，可调整内部结构、提取共享函数、修复阻塞当前 WP 的缺陷、连续完成同一 WP 的多个小任务。不得将计划功能宣称为完成，不得自行扩大关系类型、数据集或权限。

## 立即停止并汇报

- 需要真实 Vault、任何源 Markdown 写入、修改 Scanner 合同、Gold 标签或 relation_vault 文本；
- 需要数据库、重量级框架、未提供的 API key、网络访问或自动 Git 提交；
- 发现 Master 与仓库事实严重矛盾；
- 预算/凭据/路径安全门禁不满足；
- 本 WP 验收无法客观证明。

## 每个 Milestone 完成报告

列出实际修改文件、运行命令与原始结果、样例输出、已知问题、未运行检查、Stop Condition 状态、下一候选 WP。不要自己宣布 Reviewer 通过。

## 禁止事项

不许只写设计不实现；不跑测试就宣布完成；隐藏失败；无限重试；为提分改 Gold；自动提交 Git；让模型拥有写权限；把 perfect Mock 说成真实模型效果。

## 第一条可复制指令

```text
进入 Linkloom 仓库根目录。先完整阅读 AGENTS.md、根 SPEC/DEV_SPEC、docs/PRODUCT_ROADMAP.md、docs/requirements/linkloom-master/README.md 及其导航文件，再读取 Scanner 和 relation_eval 的代码、测试、fixtures、Gold、安全 mock config 与 outputs；不得打开含凭据的真实 Provider config。运行当前测试并输出只基于证据的 current-state acknowledgement。若没有人类明确批准 Master 的某个 WP，不得实现任何功能。WP-0 获批后，冻结现有资产与合同，不修改 Scanner、Gold、relation_vault、真实 Vault 或业务功能。若需要越过任一 Stop Condition，停止并报告。
```
