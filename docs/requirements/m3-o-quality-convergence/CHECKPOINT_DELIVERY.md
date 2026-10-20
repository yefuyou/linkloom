# M3-O A/B 分阶段离线交付

**STAGED_LIVE_TECHNICAL_ADMISSION = READY（仅离线技术核验）。**

基线 `29ba35fe519bd238abc8d989043f7bd398afbd55`；分支
`codex/linkloom-m3o-ab-checkpoint`。最终 Manifest、验证与独立复审结果见
`checkpoint-evidence/verification.json`。提交/远端 SHA 由最终交付消息记录。

本轮只做执行控制补丁和离线验证。真实 Provider/countTokens 均为 **0**；
没有创建真实 grant，也未初始化 `~/.linkloom/m3o-ab-v1` 或 `m3o-ab-v2` 的
实际预算账本。旧 26 次/$3 LIVE 授权不适用于新 Manifest。

新 Manifest 的规范化身份 SHA-256（执行器及授权使用）：
`a8991788425ef021459bf1fa4c933cccca906cd913a515171813d25cc2dd6c03`。
文件字节 SHA-256 另记于 `checkpoint-evidence/verification.json`，两者用途不同。

## 实际实现

- `evaluation/task_profile_ab.py`：复用原请求生成与 Host 投影为 `discover()`；
  默认加载 ab-v2，拒绝 v1 grant；LIVE 必须显式开启并指定阶段；预算命名空间
  独立为 m3o-ab-v2；原 Mock/Replay 仍使用同一生成和评分合同。
- `evaluation/task_profile_checkpoint.py`：现有预算 SQLite 中增加 campaign、
  artifacts、events 三张小表，保存阶段状态、产物 hash、门与审计；不增加另一套
  数据库服务或运行框架。内核文件锁覆盖整个阶段，进程退出自动释放。
- `scripts/run_m3o_ab.py`：三个明确阶段，输出门状态和本次新增发送数。
- `ab-v2/`：五份实验输入与 ab-v1 字节完全相同，新增 Schema 与控制合同；
  Manifest 绑定数据、Schema、14 个实现文件和关键控制策略。
- 评分器、Provider 协议、F/G、Prompt、Tool Schema、模型与采样参数均未改。

## 状态与执行

| 命令阶段 | 行为与持久边界 |
|---|---|
| FIRST_PAIR | 只处理冻结第一来源 D01 的 A/B；恰好两次发送后返回 FIRST_DONE，不在同一命令继续 |
| VERIFY_FIRST_PAIR | 零发送；核对恰好两条 COMPLETE、无第三条预留；以同一次文件读取完成 hash 和 JSON 解析；复验 raw SDK/normalized、完整 Usage/Thinking、模型、Schema、原始请求、Host/来源绑定。通过后事务记录 FIRST_PAIR_GATE=PASS；失败持久化 VERIFY_FAILED |
| CONTINUE_REMAINING | 只在 VERIFIED 门和原输出/账本/Manifest 一致时进入；从 D02 起原序再执行 24 次；每对核验后才进入下一对，禁止重发第一对 |
| 最终只读收尾 | 26 条 COMPLETE 及完整证据可重新验证时，只恢复最终报告，不打开 SDK；短前缀或不确定结果不允许恢复发送 |

FIRST_RUNNING → FIRST_DONE → VERIFIED → CONTINUE_RUNNING → FINALIZING → COMPLETED。
FIRST/VERIFY/CONTINUE 的失败有持久记录。发送阶段中断且不足完整 26 条时，拒绝
再次发送；若两条首对完整持久化而尚未写门，允许独立只读核验。

保守预算仍为每条 USD 0.079872、最多 26 条 / USD 3；26 条总预留 USD 2.076672。
未知响应保留全部预留；不退款、不重发、不重置、不切换 Campaign 恢复额度。
已有空/损坏账本在只读检查阶段拒绝，绝不将它作为新库初始化。

获得对新 Manifest 的**另行明确 LIVE 授权**后，使用同一新输出目录执行三个
独立命令（本轮未执行下列 LIVE 命令）：

```
python scripts/run_m3o_ab.py --mode live --enable-live --stage FIRST_PAIR --grant <本地新授权文件> --output <新输出目录>
python scripts/run_m3o_ab.py --mode live --enable-live --stage VERIFY_FIRST_PAIR --grant <同一授权文件> --output <同一输出目录>
python scripts/run_m3o_ab.py --mode live --enable-live --stage CONTINUE_REMAINING --grant <同一授权文件> --output <同一输出目录>
```

VERIFY 要求授权仍有效；LOW thinking 的 3072 上限包括思考。READY 只表示离线
技术门通过，不证明账户/网络/模型语义质量，也不是 Google 云端账单硬限额。

## v1 可复现性与新旧差异

ab-v1 全部文件及其旧 Manifest 字节原样保留；新代码的 hash 属于 v2，不能
把 ab-v1 路径塞给 v2 loader。原始 SHA 是 v1 的可执行复现入口，例如在仓库根：

```
git archive --format=zip --output=work/v1-repro.zip 29ba35fe519bd238abc8d989043f7bd398afbd55
Expand-Archive -LiteralPath work/v1-repro.zip -DestinationPath work/v1-repro
python work/v1-repro/scripts/run_m3o_ab.py --output work/v1-mock
python work/v1-repro/scripts/run_m3o_ab.py --mode replay --replay work/v1-mock --output work/v1-replay
```

各输出路径须新建。已在本任务 `work/checkpoint/v1-repro` 实际导出并运行，
摘要仍为 `e9230527c73e3922d558a9820ec3aba366c0b544b929418e250872a08a41f0fd`，
Mock/Replay A13/B13、真实调用 0。新旧 26 份 canonical 请求和 Mock 分数已逐项
比较相同。没有增加重复的 v1 执行器或当前版本选择器。

有意差异只有：v2 身份/预算命名空间、阶段控制、持久证据门、并发及失败关闭、
最终报告的无发送恢复。新授权必须使用新 experiment 和 Manifest 摘要；不得
复制旧 grant，不改产品默认 F Profile。

## 验证与审查

本地完整 XML/日志、子进程合成账本、Mock/Replay 和中间 RED 留在
`work/checkpoint/`，不提交数据库、授权文件或整批日志。精简证据单独提交。

已记录 RED：旧执行器无阶段入口；失败门可被恢复文件清除；产物 hash/parse
重复读；已有空账本可被初始化；最终报告预存在发送后才失败；26 条已完成
后崩溃无法只读收尾。修复和最终结果见精简证据与审查记录。

测试涉及官方 SDK MockTransport、子进程 os._exit 故障和跨进程内核锁；
这些只证明控制与证据合同，不代表真实模型质量。选定 M3-L 来源/预算/发送
边界和 M3-N 抽取兼容回归通过；未扩展处理先前 227 项未决全量失败。

最终分阶段测试与旧 A/B 合同 **53 passed**；M3-L/M3-N 关联回归
**100 passed**；编译与 `git diff --check` 通过。各历史批次有重叠，不累加。
最终普通沙箱运行覆盖 Windows 原生无覆盖发布，不依赖放宽文件权限。
完整模拟依次为 **2 / 0 / 24** 次 SDK MockTransport 发送；只读收尾为 0。
同一合成账本具有 26 个唯一 COMPLETE 身份，首对不重发，总预留 USD 2.076672。
来源顺序保持冻结列表（包括非连续编号及补充来源），没有按编号重排或跳样。

独立只读 Reviewer 最终 **PASS，未关闭 P1/P2 = 0**；问题、修复和复审证据见
`CHECKPOINT_REVIEW.md`。原工作树 203 项改动的状态指纹与基线保持一致；
不清理其他工作树。剩余限制为真实账户、网络、Provider 计费与模型语义质量
均未验收；新 Manifest 的真实运行仍须用户另行明确批准。本轮交付后停止。
