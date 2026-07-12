# 安全与权限合同

## 当前允许

读取：synthetic fixtures、被批准的未来 vault root、由 Index 指向的相对路径、本地非秘密配置。Gold Dataset 仅供 Evaluator，不能输入给推理模块。

写入：明确输出目录中的 reports、predictions、cache、Change Plan、测试临时目录。所有写入必须在批准 output root 内。

## 当前禁止

禁止改源 Markdown、真实 Vault、Scanner 输入目录、Gold Dataset、用户配置外路径。禁止网络/API key/真实 Provider 直到 WP-7 的独立批准。

## 路径与内容安全

- 所有输入/输出先 `resolve`；拒绝 traversal、绝对路径泄漏、output-inside-input。
- 不跟随 symlink；Scanner/Loader 都要显式报告而非默默跨根目录。
- Loader 只能由 Index 相对路径读取，并以 raw-byte SHA-256 比对；变化即停止该文档推理。
- 模型输入不带本机绝对路径；普通日志不打印全文；报告证据只保留必要片段。

## 模型与成本安全

API key 只能来自环境变量，不入 config、日志、report 或 Git。Provider 必须有最大请求数、最大重试、最大费用和超预算 fail-closed；Provider Error 单独记录，不冒充质量分数。当前已有明文凭据配置是阻塞发现，不是本轮可用能力。

## 未来写回门禁

Safe Writeback 不属于 MVP。未来每个动作都必须有：逐项人工确认、plan ID、指定 vault path 与操作、preview diff、目标 hash 校验、可恢复备份、审计记录、失败停止与已测试回滚。禁止批量静默修改或 AI 自我批准。
