# Integration D 独立复审

结论：**READY_FOR_INTEGRATION**，P1=0、未解决 P2=0。
此结论针对 S1/S2 组合边界；不代表 master 当前可以直接合并或业务结果成为 P4。

Reviewer 均独立只读，没有编辑代码、修改 Git 或调用 Provider：

- `/root/combined_reviewer`（gpt-5.6-sol/max）：源码、Schema/权限/重规划、
  新离线测试、651 用例对账、Chromium 和 LIVE runner 预检。完成这些检查后遇到模型额度限制，
  最终收据由下一位 Reviewer 接续。
- `/root/final_receipt_review`（gpt-5.6-luna/max）：实际 LIVE 原件、预算、来源证明、
  浏览器/冷读安全、报告与已修正问题的最终验收；明确返回 READY_FOR_INTEGRATION。

## 源码与架构

共同祖先和两固定 Head 正确。正常 merge，没有文本冲突或 ours/theirs 整文件覆盖。
共同文件同时保留 LIVE F v3 与 S1 discover UNKNOWN/唯一重规划约束。
G 的原步骤 ID、依赖、完整来源和端点投影继续受校验；原 24,000-byte 上限不变。
UNKNOWN/CONFLICT 不满足积极依赖，关系提案不能提升 P4。
没有重名 profile、新 Provider/Runtime/权限层或额外生产修改。
旧 v2 prompt/schema、Frozen、历史 Gold 和 CI 路径保持不变。

## 复核证据

- 两批 JUnit 独立解析：首次 651/27，短路径复验 27/0；失败 ID 集合完全相同，
  因而是 **651 个不同用例全部 PASS**，没有重复计数。原失败 XML 保留。
- C 完整离线轨迹显示 UNKNOWN discover、唯一重规划、保留原 ID/依赖与来源 handle，
  含 framing 预留 16,053 bytes；依赖步骤 BLOCKED，独立读取 COMPLETED。
- 实际 LIVE 的 **15/15** request/response/wire 原件 hash 与 manifest 一致；
  五次请求/响应 canonical hash 一致，实际模型/LOW/SDK attempts=1 正确。
- 五次真实响应均 SUCCEEDED，usage 合计 32,919 / 1,705 / 34,899；
  新预留 $1.25、累计 107/$28.93，低于 $50。13/13 前驱账本 hash 未变。
- Product 为 PARTIAL、error=null、Reviewer accepted；只执行预期的两个工具，
  没有 conclusions 或 publishable proposals。
- 两条观察的 offset、SHA256 quote hash 独立与 LIVE_CASE 核对；
  乔雨/2037-08-19 正确，另一任务四字段未知。
- 保存结果复验 PASS，额外真实请求 0，Provider journal SHA 不变；
  无 Provider 冷读成功，撤权/更新 BLOCKED，跨 Workspace 409，浏览器错误 0。
- 证据 manifest **24/24** 一致；报告引用的三张 PNG 存在且文件签名有效。
  所有源代码的 staged diff check 通过；JSON/XML 按原始字节保存。

## 已解决的问题

1. P2：未来 regression 运行可能覆盖保留的首次 XML。现入口在已有 XML 时直接拒绝；
   保存结果复验入口也有收据防覆盖门。短且唯一的 basetemp 避免默认再次触发 MAX_PATH。
2. 报告准确性：截图已实际归档并链接；“两个版本”改为“两条观察各自的版本/hash/span”。
3. 验收脚本元数据：最初用错误 URI/时间重建 RawArtifact 的断言失败保留；
   使用真实 source_uri/version_created_at 后，在同一已保存真实结果上完成 Provider-free 复验。
   没有额外付费重发或把初次 harness FAIL 改写为 PASS。

## 限制

真实 G 只有一个计划，未命中 C 的重规划分支，故无 LIVE C 通过声明。
实际 Google 账单/计费项目未知；预算扫描是本次列举路径的时间点复核，非跨进程事务锁。
没有 CI、部署、真实 vault 修改或 P4 提升证据。短路径规避是本地验证配置，
没有扩大范围修改 Runtime 的通用路径策略。

提交/非强推仅限新组合分支；不创建 PR、不自动合并。
待 M3-S 上游进入 master 后再准备独立增量 PR。
