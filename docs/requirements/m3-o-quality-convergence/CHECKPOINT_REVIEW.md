# M3-O 分阶段执行独立审查

结论：**PASS；未关闭的范围内 P1/P2 = 0**。
审查由独立只读 Agent `/root/m3o_independent_review` 完成，模型
`gpt-5.6-luna`、推理强度 max；主 Agent 核对测试、Manifest 与合成账本。
本记录报告已完成的离线审查，不构成 LIVE 授权。

## 发现与关闭

| 事项 | 处理与证据 |
|---|---|
| v1 当前入口随代码变动不可执行 | 按用户要求保留旧 Manifest 字节，以原始 SHA 29ba35fe519bd238abc8d989043f7bd398afbd55 导出实际运行原 CLI 的 Mock/Replay；26 份请求和分数与 v2 相同。Reviewer 接受，无需新增 v1 执行器 |
| COMPLETE 后、报告落盘前崩溃无法收尾；预存报告检查晚于发送 | RED 复现后增加 FINALIZING 和完整 26 条证据的只读收尾；续跑发送前拒绝已有报告。少于 26 条的中断不会补发 |
| 已存在空账本被隐式初始化 | RED 复现后对后续阶段先以 SQLite 只读 URI 校验完整性、绑定及表；FIRST 拒绝任何既有库 |
| Windows 沙箱 hardlink 发布失败 | 改为 Windows 原生无覆盖 os.rename；保留 POSIX 无覆盖 link，最终普通沙箱全套及目标文件竞态测试通过 |
| 失败门可通过恢复文件重开、hash 与解析两次读取 | RED 后固定失败门，hash/JSON 使用同一份读取字节；相关 tamper 测试通过 |

## 最终核对

- 实际 LIVE 分阶段入口配合官方 SDK MockTransport，FIRST/VERIFY/CONTINUE
  为 2/0/24，最终只读收尾为 0；同一账本 26 个唯一 COMPLETE，预留 USD 2.076672。
- 内核锁覆盖整阶段；独立进程不能并发越门。8 个 os._exit 崩溃点覆盖首 A、首 B、
  已写门、继续发送前、发送未知、COMPLETE 未写旁路、完整 26 条与最后一对审计。
- 未知、缺少证据、授权过期、Manifest/来源不符、额外身份、损坏/空库均停止；
  没有重发、退款、换库恢复额度或隐藏自动重试。
- 原始请求、raw SDK、normalized、Usage/Thinking、模型版本、Host/来源及
  Schema 配对均复验；执行不读取评分标签。实际失败不会被 Mock 成功替换。
- v1 文件原样保留；五份实验输入逐字节相同；模型、Prompt、Schema、上限、
  thinking、temperature、预算、评分规则均不变。新 grant 必须绑定 v2，未创建真实 grant。
- 最终 `native-acceptance.xml` 53 passed；`regression.xml` 100 passed；
  Mock/Replay A13/B13；Manifest 代码和数据 hash 匹配，摘要见 verification.json。

Reviewer 最终确认上述证据，结论为无剩余可操作 P1/P2。
真实 Provider/countTokens 请求为 0；未宣称真实模型效果、账户计费或 CI 通过，
未重新归类或处理无关全量失败。
