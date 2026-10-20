# Usage 兼容修复独立只读审查

最终结论：**PASS（离线技术准入）**；未解决 P1/P2 为 0。此文件不构成 LIVE 授权。
Reviewer：`/root/m3o_independent_review`，独立只读检查；Worker 不自批。

## 发现与处理

| 事项 | 证据及处理 |
|---|---|
| SDK Usage 的宽松字典相等掩盖数值类型差异 | 2 项 RED：100.0/True 与 100/1 宽松相等；改为类型敏感 canonical JSON digest，Reviewer 内存探针确认返回 UNKNOWN |
| 持久 accounting 回执的宽松相等 | 3 项 RED：prompt.value=100.0、safe=1、bound.upper=0.0；VERIFY 严格 digest 复算比较，非 JSON 数据关闭，Reviewer 探针确认拒绝 |
| 已唯一推导 tool-use 的费率 | RED 后按输入费率计入精确已知量；仅未分配联合残差按最高费率，避免重复计费或误标未知 |
| 测试环境的落盘/启动失败 | 保留中间 XML/trace；短临时根关闭 M3-L 落盘失败，完整 100 项通过。仅测试子进程等待扩至120/180秒，退出71、账本/no-resend断言与生产配置不变；9项精确组结果见 verification.json |

## 已审视的合同

- SDK Usage 原样保留；normalized P/C/Total 严格一致；发送和 VERIFY 共用纯函数并重算。
- Total 的四分项残差、DERIVED 与 BOUNDED 以及联合约束；输入与生成的独立最坏上界。
- Decimal 计费不重复分配残差；单条固定预留、26 次/$3、模型/wire/证据校验不放宽。
- 复用现有执行器，不增加另一套 Harness/Runtime；v3 六份实验输入与 Schema 等同 v2。
- 旧 v1/v2 Manifest 与数据、旧 D01-A UNKNOWN_STOP 及账本/产物保持原样。
- 未创建真实 Grant 或 v3 生产账本，本轮 Provider/countTokens 为 0。

## 最终证据与限制

Reviewer 最终复核确认：Manifest `ad81805d7a1b93ec0ec835e302e13a1e941d796dc224d9f341d28f094f54fe3d`
的代码指纹全部匹配；两处严格数字类型问题已关闭。六份冻结实验数据、26 个请求内容及
Mock/Replay 评分与 v2 相同，没有增加运行层或改动模型实验变量。

`crash-final.xml` 9/9、`regression-final.xml` 100/100 通过；`final-code.xml` 的
94 PASS 与精确故障组覆盖全部 99 个 A/B 用例，5 个原超时节点均有最终通过记录。
去重共 199 个用例。没有将超时批次写成单批全绿。

同一临时账本的 FIRST_PAIR / VERIFY / CONTINUE / 完成后只读恢复分别模拟发送
2 / 0 / 24 / 0；26 条均 COMPLETE。旧 D01-A、UNKNOWN_STOP、预留和所有历史记录未变。
本轮真实 Provider/countTokens 为 0，未创建真实 Grant 或 v3 生产账本。

剩余限制：测试环境的中间超时记录保留；真实 Provider 的语义模型质量及最终账单未评估。
这些限制不构成本次 Usage 合同缺陷。未来 v3 D01-A 必须作为新请求等待单独授权。
