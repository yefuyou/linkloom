# Evaluation Qualification 交付

本专项只修改评测基础设施、测试和新合成数据。生产代码、旧冻结实验、Gold、历史成绩和旧 Final 的已曝光身份保持原样。真实 Gemini 请求为 **0**；新 Final 执行为 **0**。

1. **V3 为何不合格？** D06/F07/F10 的 query 与 goal 不一致；D04 不满足多跳准入；D03 提前结束调查，D05/D07 漏读段落；八个 Final 用例没有脚本化目标动作，只产生空 Final。D08 缺少字节上下文观测，D10 没有付费预留账本观测；部分 Gold 的完成目标或来源依据不匹配。D02/D09 的历史通过保留。逐项分类见 `V3_AUDIT.json`，不将未观察行为当通过，也未据此断言生产没有缺陷。
2. **修复了什么？** 新 Preflight 在 builder 写入前复用生产计划与事实校验器，核验身份、来源跨度、目标可达预算、依赖、结果及分区泄漏。独立 Runner 读取真实 Search/Read 结果，绑定已验证实体，执行并记录非法参数、Final 修正、SQLite 冷恢复、未知 Provider 结果、Workspace、Freshness、Budget 等边界。修复了重复处理 Final 观测、错误冲突来源、错误执行计数和 Windows 路径问题，没有放宽产品规则。
3. **独立 Core Gate：通过。** 索引导出的干净目录、947 个受版本控制文件、新建虚拟环境、33 项固定运行依赖，12 个目标共 **41 项通过**，跳过/错误/缺收集均为 0；分类器另有 **7 项通过**。外部连接在 DNS/Socket 边界禁止，HTTP 仅使用 localhost。结果见 `CORE_GATE.json`、`CLEAN_EXPORT.json` 和原始 JUnit。
4. **完整 249 差分：未通过。** 精确比较 `37e45b169f19c4edd29db59f637e690caab31ad6` 与 `499bb3733bb10c0fc5c3145da9b8a9d3d587274d`。两侧原始 pytest 均 **209 通过 / 40 失败**；分类为 209 稳定通过、2 原有断言失败、38 输入缺失不可执行，新增失败/修复成功/未执行/环境故障均 0。38 项依赖 11 个无可信原件的历史文件，未制作替代品。全部 249 项均已尝试，旧针对性测试没有代替完整复跑。详见 `CORE_REGRESSION.json`、`INPUT_PROVENANCE.json`。
5. **新数据：独立审查通过，READY_FOR_APPROVAL。** 16 个新案例（6 Dev / 10 Final），与旧 20 个暴露案例比较；忽略名称、文件顺序、空行和段落换序后检查图及来源分组。发现并纠正的草案泄漏和 Freshness 预算问题保留在诊断记录中。Gold 为作者可见，隔离于模型输入；本轮不作盲测或泛化成绩声明。最终准备状态由 `READY_FOR_APPROVAL.json` 与独立审查给出。
6. **仍不能启动下一轮 Gemini。** 真实调用预算为 0；用户尚未批准新 Final 或付费执行。38 个历史输入和 2 个原有断言失败仍未解决。合成 Gate 证明执行契约可测，不证明真实模型迁移、付费不确定结果账本或真实 A/B 效果；后续须先批准冻结身份、执行协议与预算。

## 复现入口

Python 3.13，新环境安装 `tests/fixtures/evaluation_qualification/requirements-core.txt` 后：

```text
python scripts/build_qualification_validation.py --check
python scripts/evaluation_core_gate.py --output work/core-gate-new-run
python scripts/evaluation_core_regression.py --run-id full249-new-run
```

Core Gate 默认要求依赖已受 Git 管理；拒绝跳过、空目标和数量不符。249 命令在存在失败或不可执行时返回非零。Windows 使用唯一短临时路径并保留证据；两侧串行、BM25 配置一致。旧失败和环境诊断尝试保留，见 `DIAGNOSTIC_RUNS.json`。本分支不创建 PR、不合并，不启动付费实验。
