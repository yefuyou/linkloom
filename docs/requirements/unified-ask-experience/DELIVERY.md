# Unified Ask 专项交付（2026-10-14）

本轮目标：用户在一个 Ask 表单完成可信决策查询与受限 Agent 调查。仅本地合成数据与 FakeTransport；没有真实 Gemini 请求、真实 vault 修改、合并或 Release。

## A. 架构变化

AS-IS：

```text
Product Ask 表单 -> POST /api/ask -> ProductApplication.ask
  -> 关键词 Decision Memory 搜索 -> recover_decision
Timeline Agent 表单 -> POST /api/agent/decisions/ask
  -> ProductInvestigationService.start_temporal -> 固定 Workspace/槽位/日期
  -> 原有 RuntimeEngine -> 只读工具 -> Temporal Validator -> Reviewer -> projection
Legacy demo -> /api/runs；/legacy-ask 保留
```

旧普通 Ask 输入 query/可选 as_of，读取应用当前 Workspace；旧 Agent 输入 query/workspace_id/subject/relation/可选 as_of 并验证选定 Workspace。两条路径使用同一 store。Agent 启动和工具读取会重新 recovery 来核验权威状态，没有第二套决策查询引擎。旧响应分别是 answer 与 run snapshot，页面原先要求用户分别选择。

TO-BE：

```text
统一 Product Ask 表单
  -> POST /api/product/ask
  -> UnifiedAskService（ui/unified_ask.py）
     -> 本地 classify_task + 明确 Workspace/subject/relation/as_of 校验
     -> DETERMINISTIC: ProductApplication.recover_decision
     -> AGENT: 现有 ProductInvestigationService.start_temporal / Runtime
  -> product-ask/v1 结果适配
  -> GET /api/product/ask/runs/<id>?workspace_id=<id>
  -> 同一结果区：路径、状态、结论、Evidence、收据、Reviewer、未知信息、Run ID
```

Workspace 初始校验、setup 与轮询拒绝由薄服务负责；收据及原始版本 Evidence 的权威校验仍由 ProductApplication / 既有 Temporal Validator 负责。Agent Final 必须通过原有 Validator、实际 Reviewer，并具有 FOUND temporal view。Reviewer 身份、检查、理由和时间来自 Runtime artifact.review；失败/拒绝时也如实保留。确定性查询不伪称经过 Reviewer。

任务分类覆盖查询现行/历史决策、生效时间、收据，以及原因、比较、冲突、来源支持和不确定性。混合问题优先调查；无法可靠分类或缺失范围时澄清，不使用模型猜测范围。自动入口同时要求已配置服务与逐请求外部模型授权。旧 Agent endpoint 本身是显式选择调查，保留既有服务级授权语义（CLI --allow-temporal-agent 要求 --allow-external-provider）；旧 /api/ask 始终不会自动调用模型。

统一失败状态包括 NEEDS_SCOPE、FAILED、PROVIDER_UNCERTAIN。运行中和中断结果不把中间 Evidence 或模型草案当作结论；已审核 Timeline 不受影响。UI 对问题、日期、决策槽位、Workspace 和导航变动使旧请求与旧轮询失效。

## B. 实际产品能力

| 用户问题 | 路由 | 合成验收结果 | 证据 |
|---|---|---|---|
| 当前正式方案 | DETERMINISTIC | Wrenwell；不调用模型 | HUMAN_APPROVAL 收据、源版本与原始引文 |
| 2026-10-06 的正式方案 | DETERMINISTIC | Birchline | 当日 valid time 与已记录授权一致 |
| 为什么 Birchline 被 Wrenwell 替换 | AGENT | 确认替换事实；原因无证据时明确未知 | 实际工具读取当前/先前来源；Validator 与 Reviewer |
| Cedar 提案是否现行 | DETERMINISTIC | 现行仍为 Wrenwell；Cedar 标为未批准 | PROPOSED_NOT_APPROVED、没有正式收据 |
| Provider 异常 | AGENT | 确认失败或 UNCERTAIN；不展示 Final | 真实 Runtime 安全终态适配、结论/Evidence/收据为空 |

## C. 测试与实际演示

- 本轮相关产品验证：60 项既有产品/契约回归通过；最终离线 E2E 14 项通过（合计 74 个测试场景）。较早的合并验收为 71 passed；随后仅修复合成夹具并重跑扩展后的 E2E，未重复无变化的既有回归。全部模型调用为离线 FakeTransport。
- Node DOM 竞态检查：问题/日期/范围失效、较新问题胜出、Workspace 切换、旧 Agent 轮询结论/错误丢弃，PASS。
- JavaScript 语法检查，PASS。
- 独立源码/架构/验收复核，PASS；Reviewer 记录与证据版本合并问题均已修复。
- 实际 Chrome：当前查询、历史 as_of、打开 Timeline、展开 HUMAN_APPROVAL 收据、查看原始来源、正常 Fake Agent 调查、INVESTIGATING、Provider UNCERTAIN、确认失败、Workspace 切换清空旧回答，以及留空日期的当前 Agent 调查，已逐项执行；不是以单元测试代替浏览器。
- 原仓库全量 unit/integration 收集受四个基线旧测试导入缺失脚本阻断：三个 memoryagentbench 测试缺 run_memoryagentbench_g3_pilot；Stage C 测试缺 run_stage_c_context_memory_loop。未扩展范围修复，未宣称全库通过。
- 仅复制两份标记 synthetic 的旧 CASES/GOLD 到本地 work/ 以执行既有时序回归，没有复制或上传 Provider Ledger。

离线复现：

```powershell
$env:PYTHONPATH = "src"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
$env:LINKLOOM_RETRIEVAL_MODE = "bm25"
python -m pytest tests/integration/test_unified_ask_flow.py tests/unit/test_unified_ask.py -q
node tests/ui/unified_ask_dom_test.cjs
python tests/integration/run_unified_ask_demo.py --port 8794
```

浏览器预览：http://127.0.0.1:8794/。演示数据库与本地 XML 留在 work/；不加入 Commit。演示的 `uncertain provider` / `fail provider` 字样只用于离线夹具，不属于产品路由规则。

## D. 工程交付

基线：ebc211555d98595faf73269e318eddb4d6ec2110。独立分支：codex/unified-ask-experience。既有远端：https://github.com/yefuyou/linkloom.git；基线已核对存在于远端 temporal-agent-integration 分支。Commit 与推送回读结果由最终聊天报告给出。

修改文件：

- ui/unified_ask.py：薄路由、统一结果与轮询范围保护。
- ui/server.py：新增产品 HTTP 路由，保留旧端点。
- ui/investigation.py：外部能力声明、冻结来源版本展示信息；继续复用现有 Runtime。
- ui/backend.py：真实 Reviewer 记录投影。
- ui/static/product.js：单一 Ask 表单、统一状态/证据/收据/Reviewer、Timeline 回跳、竞态保护。
- tests/unit/test_unified_ask.py：分类、真实拒绝审核与证据版本契约。
- tests/ui/unified_ask_dom_test.cjs：前端竞态验收。
- tests/integration/test_unified_ask_flow.py、unified_ask_fixture.py、run_unified_ask_demo.py：离线 HTTP E2E 与可复现合成服务。
- docs/PRODUCT_ROADMAP.md 与本目录 SPEC / implementation_plan / DELIVERY：当前专项边界及交付证据。

## E. 剩余产品问题

本轮没有遗留的已知验收阻断项。两个产品边界仍可影响使用：本地分类器只识别已定义的任务族，自由问题可能需要澄清；调查仍限定于已有可信决策槽位，不能调查任意未审核主题。真实 Provider 运行、网络可靠性与部署不在本轮验收状态内。
