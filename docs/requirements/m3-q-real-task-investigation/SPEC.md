# M3-Q：正常产品任务 Provider 与语义候选

授权：用户 2026-10-21 完整请求。唯一实施 Agent；独立 Reviewer 只读复审。
精确依赖：PR #6 OPEN/Draft，Head `67b91a1520f6b129985905702f2a2bd19a631882`。
本文件记录已授权边界，不新增逐步审批。真实付费请求未授权。

## 产品与架构

普通 CLI 显式配置 Gemini 3.8 Flash、任务阶段开关及请求/输入/输出/费用/时长预算。
配置不发送请求。Unified Ask 单次授权绑定当前 Workspace、完整来源快照与问题；
来源发送许可、任务执行许可与业务关系批准分离。当前无认证业务批准流程。

复用 `TaskObservationDiscoveryService` F、`InvestigationPlanner` G、
`SemanticProposer`、`SemanticAnalysis`、现有 RuntimeEngine/SingleAgentModelLoop/
ToolRuntime/ContextAssembler/Grounding/Reviewer 和已完成结果冷读。
Host 的单次预算 Adapter 包裹现有模型合同，覆盖 F/G/语义/外层模型，绝不成为第二个 Loop。
未知结果不重试；持久化请求预留与结果，失败/超时/UNCERTAIN 不发布成功 PARTIAL。
SDK 本身禁用重试并限制传输时长。预算是保守预留上限；未知费用不能冒充实际结算。

G 完整关系步骤可作为**未确认语义问题**提供给 LLM。执行依赖仍只认既有 P4
当前有效证明。精确引文验证不证明语义；不写 P4、不自动合并、不批准业务关系。
定向增加证据正反结构一致性校验，不增加中文业务正则。

## 验收

1. 正常 Host 支持未配置提示、明确配置指引、来源清单、预算展示、单次显式授权。
2. 正式 HTTP + 持久化 SQLite + 实际 Runtime，以可控 Provider 替身完成双会议调查。
3. SUPPORTED/REFUTED/CONFLICT/UNKNOWN 保持分离；关系候选带精确来源，缺前提继续阻断。
4. 未授权、跨 Workspace、来源变更、证据撤权、未知 Provider、Reviewer 拒绝均不泄漏旧文/trace。
5. 冷读重验不调用 Provider；实际 Chromium 覆盖入口、授权、运行、展开、来源、刷新。
6. Core/Task/Legacy/Frozen、DOM/JS、受影响 SDK 合同通过；保留 RED 和实际失败。
7. 独立复审无 P1/P2 后才可普通提交/推送新分支；不创建面向 master 的 PR。
8. 没有付费验收时标记 REAL_PROVIDER_NOT_EVALUATED；M2 原始 0/4 不覆盖、不冒充盲测。

## 非目标与停止边界

无真实 vault、认证业务批准、新权威数据库、新 Planner/Runtime、评测平台扩建、
Gemini 2.5 新调用、历史冻结数据修改。若 H01–H04 的可信证明修复需要重写 P1/P2/P3，
保留其失败并交付 P0 与最小后续方案，不降低门禁。

## 实施顺序与检查

先写 Host Provider/权限/预算 RED，再接线；随后完整候选与证据极性 RED/GREEN；
再正式 HTTP、冷读与浏览器；最后运行既有 workflow 的本地门禁并独立复审。
实际证据与局限写入 DELIVERY.md，不用测试数量衡量语义产品效果。
