# 产品文案与 UI 信息层级交付

本专项沿用 [Draft PR #25](https://github.com/yefuyou/linkloom/pull/25) 和
`codex/product-copy-ux-cleanup`。实际 UI、中英文、README、演示与回归修改均已完成，
组合版本已完成离线产品验收和真实 Chromium 验收。保持 Draft、未合并。

## 范围与统计

任务开始基线：`7720390a796662ff42c27b5eef6f8685e5155802`。
已按正常合并方式保留原 #25 提交 `3d4c05025c64aed0f51a15b6aa7d476febc972f8`，
并纳入当前 master `f2ce6b82d635a7b78b17a6d100556c354f7a461a`。
master 与其他 PR 未修改，无 force push、rebase、reset 或历史改写。

本轮审计 **42 个页面/状态组、522 个静态文本条目**，记录并处理 **60 处基线问题**：
**删除 12、合并 12、改写 24、移动 12**。一处双语文案或紧密相关的渲染结构计一次；
同一位置的多轮编辑、翻译包装和测试更新不重复计算。前轮 37 组/100 项统计保留在
[原交付](prior-delivery/DELIVERY.md)，两套统计不相加。

- [COPY_AUDIT.md](COPY_AUDIT.md)：原文、位置、分类、问题、处理理由和语义边界。
- [COPY_AUDIT.json](COPY_AUDIT.json)、[COPY_INVENTORY.json](COPY_INVENTORY.json)：可复核的唯一编号与分类。
- [COPY_GUIDELINES.md](COPY_GUIDELINES.md)：标题、结果、术语、风险与详情的统一规范。
- [INTEGRATION.md](INTEGRATION.md)：两份实现的冲突处理与主线兼容。

## 实际修改

1. **单标题页面**：Sources、Review、Decision Memory 和 Ask 移除固定眉题与副标题。
   工作区、详情和候选仍保留必要字段指导、类型及状态。
2. **结果优先**：任务调查先显示真实记录数，再显示部分结果/资料不足、任务待确认与离线标记。
   逐条展示负责人、日期、原文及字段证据；无记录时不生成空来源容器。
3. **降低重复**：同一不确定性集中表达。模型缺口、回执 ID、版本、哈希、配置命令与处理指标可展开；
   用户下一步从技术详情移至来源之后。
4. **双语与交互**：界面、已知关系、候选类型、生命周期与错误修复提示中英文一致。
   URL/本地语言偏好保留；切换语言保留表单与历史查询日期，旧答案及逐次许可重新失效，
   迟到的响应不能将旧字段恢复到另一工作区。原始关系值继续用于请求。
5. **诊断分层**：已知错误提供具体修复指导；未知错误主区显示通用双语提示，原文与错误码留在详情。
   原生 TypeError 和继承对象键的回归已覆盖。
6. **公开介绍**：两份 README 与演示文档使用当前产品术语和实际功能，引用本轮界面截图。
   旧版回放的重复标签也已精简。

保留原 #25 的第五套 DOM/CI 门禁、语言偏好、完整候选类型、来源保存与原始失败状态区分，
以及双语错误映射。前轮 AUDIT_DATA、XML、模板快照、浏览器失败收据和捕获脚本保持原样。

## 业务语义与必要提示

- 导入和来源更新仍立即告知外部 AI 数据发送及可能费用。
- Ask 保留未勾选的逐次许可、来源版本清单和配置的费用/请求数/时间上限。
  仅启动参数及细粒度 Token 上限折叠。
- 未解析关系的批准仍禁用；候选与模型建议保持待确认，关系调查和业务批准分别表达。
- UNKNOWN、否定证据、CONFLICT、前置阻断与执行失败分别显示。
- 来源或权限变化后隐藏旧任务证据；日期比较仍要求 current_valid。
- 历史查询依据目前记录的批准；无结束日期不被描述为“持续有效”。
- 离线任务演示在主状态行直接标明，未当作真实模型验收。

独立复核逐字节核对 **222 个非静态生产源码文件**，全部与当前 origin/master 相同。
F/G/Runtime/Reviewer/P4、任务关系及批准规则没有本专项改写。主线 M2 门禁保持。
不引入 UI 框架，不调用付费 Provider，不读取或配置真实 Key，不使用私人数据。

## 最终组合验证

| 检查 | 结果 | 记录 |
|---|---|---|
| 生产 JavaScript 与浏览器脚本语法 | PASS | `node --check` |
| Task / Unified Ask / First use / Stale revalidation / Product copy DOM | **5/5 PASS** | [DOM 记录](evidence/integrated/copy-dom.txt)及同目录其余日志 |
| 相关 Python、产品 HTTP 与主线 M2 回归 | **178 passed，零失败、零跳过** | [JUnit](evidence/integrated/product.xml)、[输出](evidence/integrated/python.txt) |
| 既有离线 HTTP 与保存结果冷读 | PASS，0 Provider requests | [收据](evidence/integrated/http.txt) |
| Chromium 153.0.8010.0 | **33 步 PASS，139 个本地 HTTP 响应，0 页面脚本错误** | [步骤清单](evidence/integrated/BROWSER_STEPS.md)、[机器收据](evidence/integrated/BROWSER_RESULT.json) |
| Git 空白检查 | PASS | `git diff --check` |
| 独立只读复核 | 无遗留 P0/P1/P2 | [复核记录](INDEPENDENT_REVIEW.md) |

浏览器实际操作包括：创建/切换工作区、语言偏好重载、保留未提交字段、查看及本地导入来源、
空状态、审核与单独批准、当前及历史 Decision Memory、已审核 Ask、任务部分/未知/冲突、
P4 日期比较、来源更新后阻断、保存结果重新核验、来源展开、旧版回放和网络错误。
历史日期切语言同时检查 HTTP 响应与界面日期。所有步骤都检查横向溢出，桌面为
1440×1000，手机为 390×844。浏览器证据和 DOM 检查分别记录。

本环境首次缺少 Chromium，官方包下载失败，随后通过注册表安装隔离 Chromium 并补齐
CJK 字体和 SwiftShader 测试库。没有新增生产依赖。浏览器和测试服务在同一执行环境启动。
开发过程中的失败记录不混入最终 PASS 数量；原始前轮收据不覆盖。

## 截图

初始基线来源于固定 master 的当前重新渲染。早先本地验收截图仍在 evidence 根目录，
组合版本的 10 张关键截图及新收据放在 evidence/integrated；其余步骤可由脚本复现。

| 页面/状态 | 修改前 | 最终组合版本 |
|---|---|---|
| Sources / English | [原界面](evidence/before-sources-en.png) | [新界面](evidence/integrated/after-sources-en.png) |
| 任务调查 / 中文 | [原界面](evidence/before-task-zh.png) | [新界面](evidence/integrated/after-task-zh.png) |
| 中文 Sources | — | [截图](evidence/integrated/after-sources-zh.png) |
| 英文任务结果 | — | [截图](evidence/integrated/after-task-en.png) |
| 手机任务结果 | — | [截图](evidence/integrated/after-task-mobile-zh.png) |
| 来源变化阻断 / 资料不足 | — | [阻断](evidence/integrated/after-blocked-zh.png)、[资料不足](evidence/integrated/after-unknown-zh.png) |
| 审核 / 决策历史 / Ask | — | [审核](evidence/integrated/after-review-zh.png)、[历史](evidence/integrated/after-decisions-zh.png)、[Ask](evidence/integrated/after-ask-en.png) |

## 未执行项与剩余问题

- 历史 `tests/smoke/test_temporal_product_http_lifecycle.py` 的 runner 导入 Windows-only
  `msvcrt`，当前 Linux 无法收集执行；没有降低或跳过断言。独立离线 HTTP、HTTP 集成与
  真实浏览器产品流程均已通过。
- 未运行完整仓库测试、Windows 环境及真实付费模型质量评估。本轮验证范围是上述组合回归。
- 原生文件选择器随浏览器语言；在已验收桌面和手机尺寸未发现新的明显布局问题。
- 保持 Draft，未授权合并 master。

## 复现

安装项目开发依赖及测试环境的 Playwright/Chromium，使用新合成目录运行：

```bash
node tests/ui/task_answer_dom_test.cjs
node tests/ui/unified_ask_dom_test.cjs
node tests/ui/first_use_dom_test.cjs
node tests/ui/stale_revalidation_dom_test.cjs
node tests/ui/product_copy_dom_test.cjs
node tests/ui/product_copy_browser.cjs work/new-copy-browser-run
python scripts/run_m3p_offline_acceptance.py --output work/new-copy-http-run
git diff --check
```

`NODE_PATH` 可指向测试环境的 Playwright，`LINKLOOM_CHROMIUM_EXECUTABLE` 可选择已安装浏览器。
178 项用例及模块见 JUnit；环境为 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、
`LINKLOOM_RETRIEVAL_MODE=bm25`、`PYTHONPATH=src:.:tests/integration`。
