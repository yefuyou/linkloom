# F A/B 本地移交 SPEC

授权：用户“LinkLoom F：A/B实验移交本地Codex”。只完成实验交接并停止。

- 生产候选保持原38行新增、默认OFF；不改模型、Grounding、Discovery、Reviewer或Canary。
- 提交现有准备器、测试、公开原生输入/模型响应fixture及20份原样冻结请求、PLAN、
  配置/补丁、准备报告、原测试结果、SHA-256清单和本地导入说明。
- 不提交官方摘要、qrels、Evaluator-only Gold、预登记标准答案、逐例Gold矩阵、
  历史证据ZIP、预算账本副本或任何Key/凭据/私有资料。已有这些本地文件保持原样。
- 用专项路径的Git属性保持跨平台字节；不改变其他文件的Git处理策略。
- 只允许提交/推送codex/f-cross-turn-discovery；不修改生产PR，不合并master，不force push。
- 不运行真实Provider、不获取/读取/配置Key，不续写#22账本，也不等待线上获取Key。
- 从远端回读精确提交和逐文件字节，确认与SHA-256清单一致。

验收：39项离线测试、纯标准库交付校验、定向敏感内容检查、远端ref与文件哈希一致。
真实模型A/B仍NOT_RUN；本次交付不赋予新费用池或修改旧账本的授权。
