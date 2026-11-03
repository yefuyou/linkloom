# 跨会议“可能是同一件事”外部出题材料

基线：`aa20f2f6d5d1f9ad4a686cb8fdc3832a66202e0e`；日期：2026-11-03。
用途：在新功能实现/预测/调参之前，导出既有合成开发输入，供实现会话之外的人起草开发题，由用户逐条确认。标注统一为“外部起草，人工确认”。当前会话及其子 Agent 不代写新题与标签。

本材料不是题集、gold 或新功能效果证据；既有 fixture 模拟审核不能称真实人工审核。仅导出下列四份已跟踪开发输入中的全部 sources，不读取真实数据库、真实笔记库、AMI/held-out 数据、模型请求日志或凭据；不导出原文件中的 questions、queries、expected、旧关系判定、模型响应。

## 出题交付要求

- 至少 30 个经用户确认的主判定对；建议至少 30 个候选检索场景，12 个改名/简称/口语化正例（各至少 4）、10 个相似非同一事项负例、8 个跨项目同名干扰例。全部都只是待外部作者起草的配额，不是预先给定答案。
- 为每个场景写明 pending 候选和同工作区的正式任务池，建议至少 10 项，附状态和模拟审核来源；标注正确 task_id 集合或 NONE。保证能评估 top-10/top-3，而非只比较单个正对。
- 每题附：case_id、author、reviewer、confirmed、workspace_id、candidate 原文字段/来源、task pool、expected target/NONE、两侧逐字引用与位置、不确定性说明。题目和标签都须实现会话之外起草、用户确认后冻结。
- 保留来源、项目、负责人和截止的差异；不能因同名、同负责人或同日期就判同一事项。正例须跨会议，包含不同称呼；负例须有真实相似干扰项。
- 不能把不同原工作区静默混池。若新建合成 workspace，要明确新的来源清单和项目边界，由用户确认；不据名称推断项目或任务身份。
- 以下原文中有显式“同一任务”等容易信号；它们只是一部分材料。请补充自然口语、简称和跨项目同名，避免全部题目都能靠显式词命中。可由外部作者增加合成会议和背景任务，连同新题一起交用户确认。
- 原文日期保留原样；未来日期是已有合成 fixture 内容，不改成现实日期，不推断缺失年份/会议时间。
- 不向新功能提供题目预期或旧关系问题。确认后固定题集与数据哈希；阈值只在该开发集确定，后来另出的验收题不得用于调阈值。

## 当前产品输入边界

正式 Task 当前包含 id、workspace_id、title、owner、deadline、deadline_text、status、source_decision_id、created_at、updated_at；没有独立 description/project 字段。描述用于检索时取已授权原文投影；两侧引用独立保留 source/version/hash/位置。出题者需明确候选/正式任务角色及模拟审核，以下原始 sources 本身不代表已经创建正式任务或获得人工授权。

## 导出清单与完整原文

以下每个 JSON 代码块只包含来源输入。文件 SHA-256 按导出时工作树文件原始字节计算；Git blob ID 指向基线版本。content_sha256 按原文 content 的 UTF-8 字节计算，保留 JSON 解码后的换行和字符，不 trim。dataset+case_id+source_id 共同定位来源；不同文件中的重名来源不合并。

### tests/fixtures/task_semantic/inputs/dev.json

```json
{
  "input_path": "tests/fixtures/task_semantic/inputs/dev.json",
  "input_sha256": "4cdb8365457f42cb530d1832a717f7819d204334d0f6b6aa51fa33dd453d6617",
  "git_blob_id": "ce1c92e32b9fb284d29971e287262876550bb875",
  "cases": [
    {
      "case_id": "revision",
      "workspace_id": "semantic-dev-revision",
      "sources": [
        {
          "source_id": "plan.md",
          "artifact_id": "plan.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 许禾: 我负责栖湾项目任务 RG-71 的网关接入，计划在2026年12月10日前完成。\n",
          "content_sha256": "bfbe5b8855a6bacb2c2354a28f33fd98108c5d6403729a79594e63ff951ec673"
        },
        {
          "source_id": "update.md",
          "artifact_id": "update.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-02T09:00:00+08:00] 许禾: 我负责栖湾项目任务 RG-71 的网关接入。请把plan.md中的工作认作本条的同一事项；旧的交付安排到此作废，新的任务计划在2026年12月14日前完成，由此版接替，不是另开任务。\n",
          "content_sha256": "2320fa4aa68dc2bbe56d435e30b3aa8e0f5b74583ffbf1e45b272e22f41d5450"
        }
      ]
    },
    {
      "case_id": "causality",
      "workspace_id": "semantic-dev-causality",
      "sources": [
        {
          "source_id": "choice.md",
          "artifact_id": "choice.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-02T10:00:00+08:00] 李棠: 澄川项目的短信供应商此次换成了远铃，之前使用的是岚声；这份资料尚未经过人工审核。\n",
          "content_sha256": "42d70d3c72f2ee9d2bf9369800616efd5b8ac85e543e643732aed4bbd4aaa74e"
        },
        {
          "source_id": "work.md",
          "artifact_id": "work.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-02T10:05:00+08:00] 许禾: 我负责澄川项目任务 CC-81 的签名适配，计划在2026年12月16日前完成；choice.md所述更换直接导致签名规范变化，所以这项工作必须重做签名映射。\n[2026-12-02T10:06:00+08:00] 许禾: 我负责澄川项目任务 CC-82 的日志归档，计划在2026年12月16日前完成；就choice.md这次供应商更换而言，归档工作完全不受影响，无须调整。\n[2026-12-02T10:07:00+08:00] 许禾: 我负责澄川项目任务 CC-83 的缓存优化，计划在2026年12月16日前完成。\n",
          "content_sha256": "0d1f5431eb92ac3e3801ec50be078c8e6aca1f5c9fac224c24b534d2fe143e35"
        }
      ]
    },
    {
      "case_id": "conflict",
      "workspace_id": "semantic-dev-conflict",
      "sources": [
        {
          "source_id": "first.md",
          "artifact_id": "first.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 许禾: 我负责岑湖项目任务 CH-91 的回调联调，计划在2026年12月11日前完成。second.md的15日版本只是待议草案，不会取代这里的执行安排。\n",
          "content_sha256": "ce86f794915f57a470938d0fa6568f406581e6364a4bd63434660319ff00a146"
        },
        {
          "source_id": "second.md",
          "artifact_id": "second.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-03T09:00:00+08:00] 许禾: 我负责岑湖项目任务 CH-91 的回调联调，计划在2026年12月15日前完成。这就是first.md里的同一任务；这里是应执行的新计划，覆盖first.md的11日安排。\n",
          "content_sha256": "fdf54dae9b4c1212f63b46994fb3c120b9ec35c6b4953ce7e77b952c23ad94f1"
        }
      ]
    },
    {
      "case_id": "correction",
      "workspace_id": "semantic-dev-correction",
      "sources": [
        {
          "source_id": "original.md",
          "artifact_id": "original.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 许禾: 我负责眠沙项目任务 MS-17 的报表接入，计划在2026年12月20日前完成。\n",
          "content_sha256": "e700136a3a504817b1bf779635fb449da7d219a8d0dde5b638e89a0fa28b4506"
        },
        {
          "source_id": "correction.md",
          "artifact_id": "correction.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-04T09:00:00+08:00] 许禾: 我负责眠沙项目任务 MS-17 的报表接入，仍是original.md中同一个事项。这里纠正一次资料笔误：22日其实是文档整理日期，业务任务从来没改过截止，计划在2026年12月20日前完成。\n",
          "content_sha256": "32d46666a2e76a647344bd6aef0db283b5262e10f04bc315f0f4963e37fc542f"
        }
      ]
    },
    {
      "case_id": "independent",
      "workspace_id": "semantic-dev-independent",
      "sources": [
        {
          "source_id": "tasks.md",
          "artifact_id": "tasks.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 许禾: 我负责青汀项目任务 QT-41 的生产网关联调，计划在2026年12月10日前完成。\n[2026-12-02T09:00:00+08:00] 许禾: 我负责青汀项目任务 QT-42 的沙箱网关联调，计划在2026年12月14日前完成。这是另一份独立工作，请勿与QT-41合为一个任务；两项联调共享测试结果，存在明确工作联系。\n",
          "content_sha256": "d3971d20c852e9ff106cc883ccd72193ccb72dc26c8da4e0c05b986f1324790e"
        }
      ]
    },
    {
      "case_id": "alias",
      "workspace_id": "semantic-dev-alias",
      "sources": [
        {
          "source_id": "glossary.md",
          "artifact_id": "glossary.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T08:00:00+08:00] 李棠: 在本期工作区，白桨只是苇岸项目的内部称呼，两个名称指向同一个项目。\n",
          "content_sha256": "555275cf06315589ce51e7cc84c24f24c1b4a098b44e7ea1ef953a9441a927f1"
        },
        {
          "source_id": "task.md",
          "artifact_id": "task.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 许禾: 我负责白桨的收据适配，计划在2026年12月12日前完成；这项工作归属白桨。\n",
          "content_sha256": "f64f879fe53c45ad28255f3e0ef7ce35732590a88156e51b4589638d8fcf1803"
        }
      ]
    },
    {
      "case_id": "conditional",
      "workspace_id": "semantic-dev-conditional",
      "sources": [
        {
          "source_id": "change.md",
          "artifact_id": "change.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 李棠: 清芦项目的短信供应商由晓声改成晚铃。\n",
          "content_sha256": "fe6372f94cdd9903ff8ce47b21f643a5d20c837f914b9d9f8b1642e180ba52c4"
        },
        {
          "source_id": "task.md",
          "artifact_id": "task.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-02T09:00:00+08:00] 许禾: 我负责清芦项目任务 QL-55 的验收，计划在2026年12月18日前完成；如果change.md的切换将来改变签名规范，我们才考虑增加补测，目前没有证据证明需要调整。\n",
          "content_sha256": "f1909bdce56055c72ba9fe1daf3e5b2b017e840b3abe8acb2817c8e3ea4500e4"
        }
      ]
    },
    {
      "case_id": "missing-year",
      "workspace_id": "semantic-dev-missing-year",
      "sources": [
        {
          "source_id": "old.md",
          "artifact_id": "old.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 李棠: 岚溪项目任务 LX-61：计划在12月20日前完成校验，负责人待定。\n",
          "content_sha256": "295b732c65b19f622497b070490c4679477c23d760f8d7667e47851fe88d046d"
        },
        {
          "source_id": "new.md",
          "artifact_id": "new.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-02T09:00:00+08:00] 李棠: 岚溪项目任务 LX-61仍然是old.md的原任务；原先交付计划被本条撤换，改成计划在12月23日前完成，负责人仍未确定。\n",
          "content_sha256": "929bdeba4df89ed11c3b736487b099ca06d5218905391d6e19c72e237bcee9b0"
        }
      ]
    }
  ]
}
```

### tests/fixtures/task_relations_p2/inputs/dev.json

```json
{
  "input_path": "tests/fixtures/task_relations_p2/inputs/dev.json",
  "input_sha256": "2aec4181a1360bfa2fb69b1d5ad32f6c03b68bfa8e4ee676f6904e243f32e4b2",
  "git_blob_id": "799bdca2e1a13457e0678399e346011c6e093e5a",
  "cases": [
    {
      "case_id": "D03",
      "workspace_id": "m2-task-relations-p2-dev-d03",
      "sources": [
        {
          "source_id": "A.md",
          "artifact_id": "A.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目的短信接口联调，计划在2026年10月14日前完成。\n",
          "content_sha256": "ec28a40585b6391f319ff165548f19e1dc2693b8671403884be878431dccb74e"
        },
        {
          "source_id": "B.md",
          "artifact_id": "B.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目的新接口调整，计划在2026年10月18日前完成。\n说明：B1的接口调整与A.md的联调工作相关，是否属于同一业务任务尚待核对。\n",
          "content_sha256": "bb1d725124b781a4edde7f38a292b95f32a157b4b8d10776327480ca0c40937a"
        }
      ]
    },
    {
      "case_id": "D06",
      "workspace_id": "m2-task-relations-p2-dev-d06",
      "sources": [
        {
          "source_id": "A.md",
          "artifact_id": "A.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-10T10:00:00+08:00] 林悦: 星河项目正式选择云桥科技为短信供应商，自2026年10月10日起生效。\n[2026-10-15T10:00:00+08:00] 林悦: 星河项目短信供应商由云桥科技改为星帆通信，自2026年10月15日起生效，替代原选择。\n",
          "content_sha256": "96cf55e253ef797bd8ca9f50aca805cd21f55e2cdf962223eb81797ddc6e4f92"
        },
        {
          "source_id": "B.md",
          "artifact_id": "B.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T10:05:00+08:00] 陈晨: 我负责星河项目任务 ADAPT-2 的签名参数适配；因为A.md记载的从云桥科技改用星帆通信，此任务必须调整签名参数，计划在2026年10月18日前完成。\n",
          "content_sha256": "692ed9244563c52b44a7ea07401ec9ad9553e8361d7b6daa345d4e28765cf8a4"
        }
      ]
    },
    {
      "case_id": "D07",
      "workspace_id": "m2-task-relations-p2-dev-d07",
      "sources": [
        {
          "source_id": "A.txt",
          "artifact_id": "A.txt",
          "source_type": "timestamped_text",
          "content": "[2026-10-10T09:00:00+08:00] 林悦: 星河项目今天正式确定，短信服务供应商选择云桥科技，自2026年10月10日起生效。选择原因是报价较低，且支持国内短信发送。\n[2026-10-10T09:05:00+08:00] 陈晨: 我负责星河项目的短信接口接入，计划在2026年10月14日前完成联调。\n",
          "content_sha256": "a8ea56b3f56c00d524b289b4eb5f5b3cb9a7c5d04411ad0afc53e6f532881662"
        },
        {
          "source_id": "B.txt",
          "artifact_id": "B.txt",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T10:00:00+08:00] 林悦: 星河项目的短信服务供应商现正式改为星帆通信，自2026年10月15日起生效，替代2026年10月10日选择云桥科技的决定。原因是云桥科技在联调中多次延迟，星帆通信的到达率更稳定。\n[2026-10-15T10:05:00+08:00] 陈晨: 我继续负责短信接口接入，将按新供应商星帆通信的接口调整，计划在2026年10月18日前完成。\n[2026-10-15T10:10:00+08:00] 周宁: 我建议以后考虑增加邮件通知作为备用渠道，但这只是建议，本次会议尚未批准，也没有确定上线日期。\n",
          "content_sha256": "7857add46076081cb6ecd2058d20d523ffb2800c7ae7821c229da23a77de505c"
        }
      ]
    },
    {
      "case_id": "D08",
      "workspace_id": "m2-task-relations-p2-dev-d08",
      "sources": [
        {
          "source_id": "A.md",
          "artifact_id": "A.md",
          "source_type": "manual_text",
          "content": "项目说明：本工作区的“银梭”是“星河项目”的内部代号，两者在本期短信接入资料中指同一项目。",
          "content_sha256": "d9519dc68ade83e60ce8535d6d8d2f44d47c21f8d9513043d11b7fb34fe8db1d"
        },
        {
          "source_id": "B.md",
          "artifact_id": "B.md",
          "source_type": "timestamped_text",
          "content": "# 银梭\n本节短信接入任务仅属于银梭。\n[2026-10-16T09:00:00+08:00] 陈晨: 我负责签名参数适配，计划在2026年10月18日前完成。\n# 天河项目\n本节任务仅属于天河项目。\n[2026-10-16T09:10:00+08:00] 陈晨: 我负责重试参数适配，计划在2026年10月19日前完成。\n",
          "content_sha256": "883bcf2da858ac6c954c2dee1f3b87a1b2d4838655a1e0d35e72dd6e82f8b87f"
        }
      ]
    },
    {
      "case_id": "V01",
      "workspace_id": "m2-task-relations-p2-dev-v01",
      "sources": [
        {
          "source_id": "projects.md",
          "artifact_id": "projects.md",
          "source_type": "manual_text",
          "content": "# 星河项目\n本节短信接入任务仅属于星河项目。\n签名参数适配任务，计划在2026年10月18日前完成。\n\n# 天河项目\n本节任务仅属于天河项目。\n重试参数适配任务，计划在2026年10月19日前完成。\n",
          "content_sha256": "49cc23a3a60426e45df52b2c1c67e9a7fce74547806c0ca779279c0d38e413d3"
        }
      ]
    },
    {
      "case_id": "V02",
      "workspace_id": "m2-task-relations-p2-dev-v02-a",
      "sources": [
        {
          "source_id": "alias.md",
          "artifact_id": "alias.md",
          "source_type": "manual_text",
          "content": "项目说明：本工作区的“银梭”是“星河项目”的内部代号，两者在本期短信接入资料中指同一项目。",
          "content_sha256": "d9519dc68ade83e60ce8535d6d8d2f44d47c21f8d9513043d11b7fb34fe8db1d"
        }
      ]
    },
    {
      "case_id": "V03",
      "workspace_id": "m2-task-relations-p2-dev-v03",
      "sources": [
        {
          "source_id": "no-impact.md",
          "artifact_id": "no-impact.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T10:00:00+08:00] 林悦: 星河项目短信供应商由云桥科技改为星帆通信，自2026年10月15日起生效。\n[2026-10-15T10:05:00+08:00] 陈晨: 我负责星河项目任务 AUTH-4 的日志归档；AUTH-4无需因本次供应商更换做任何调整，计划在2026年10月18日前完成。\n",
          "content_sha256": "d5d543e00fcf634394c6428b48184a3cddb6582e749cd51e8842224ba5cb95f1"
        }
      ]
    },
    {
      "case_id": "V04",
      "workspace_id": "m2-task-relations-p2-dev-v04",
      "sources": [
        {
          "source_id": "current.md",
          "artifact_id": "current.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T10:00:00+08:00] 林悦: 星河项目供应商现为星帆通信，自2026年10月15日起生效。\n[2026-10-15T10:05:00+08:00] 陈晨: 我负责星河项目任务 STALE-1 的适配测试，因供应商变化需要调整测试，计划在2026年10月18日前完成。\n",
          "content_sha256": "b4bb7382e263d80cafc5ba3e2337848b15b0fc48beff04f90a8c1acb9abea0a5"
        }
      ]
    },
    {
      "case_id": "V05",
      "workspace_id": "m2-task-relations-p2-dev-v05",
      "sources": [
        {
          "source_id": "same-supplier.md",
          "artifact_id": "same-supplier.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T10:00:00+08:00] 林悦: 星河项目短信供应商由云桥科技改为星帆通信，自2026年10月15日起生效。\n[2026-10-15T10:05:00+08:00] 陈晨: 我负责星河项目任务 SUP-5 的通知日志适配，目前使用星帆通信，计划在2026年10月18日前完成。\n",
          "content_sha256": "5e3ce75554cacd0239ff68b8e2b37deb6e290d58165a8ce3940f0c65092e2058"
        }
      ]
    },
    {
      "case_id": "V06",
      "workspace_id": "m2-task-relations-p2-dev-v06",
      "sources": [
        {
          "source_id": "source-claim.md",
          "artifact_id": "source-claim.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T10:00:00+08:00] 林悦: 星河项目短信供应商由云桥科技改为星帆通信，自2026年10月15日起生效。\n[2026-10-15T10:05:00+08:00] 陈晨: 我负责星河项目任务 CLAIM-1 的签名参数适配；因为该供应商更换，此任务必须调整签名参数，计划在2026年10月18日前完成。\n",
          "content_sha256": "41750ed09cd2f4feff26bd062c8823da9a74ac5f6b0ecb64ebe6425927f1ebb4"
        }
      ]
    },
    {
      "case_id": "V07",
      "workspace_id": "m2-task-relations-p2-dev-v07",
      "sources": [
        {
          "source_id": "project-register.md",
          "artifact_id": "project-register.md",
          "source_type": "manual_text",
          "content": "项目登记：本工作区的短信适配事项归属星河项目。",
          "content_sha256": "7c9bf333230d57c5878c93d9e9cc0e350965f7b4fc93e379137fc8a99ec781e1"
        },
        {
          "source_id": "task.md",
          "artifact_id": "task.md",
          "source_type": "manual_text",
          "content": "任务说明：本任务所属项目为 project-register.md 登记的星河项目。\n签名参数适配任务，计划在2026年10月18日前完成。",
          "content_sha256": "51c35408f0fdcbdc1614ba155caba083d91adc56cb6d8da090e7762bfb367a00"
        }
      ]
    }
  ]
}
```

### tests/fixtures/task_revisions/inputs/dev.json

```json
{
  "input_path": "tests/fixtures/task_revisions/inputs/dev.json",
  "input_sha256": "97532f81b75e41e49343e32a6211bfed46b6d7db36c428bb1f03f13fdf364fcb",
  "git_blob_id": "e6ce23ce9bf2463b5cedd2fb065dc311a52fc0e0",
  "cases": [
    {
      "case_id": "D01",
      "workspace_id": "m2-task-identity-dev-d01",
      "sources": [
        {
          "source_id": "A.md",
          "artifact_id": "A.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 SMS-17 的短信接口接入，计划在2026年10月14日前完成。\n",
          "content_sha256": "50eb3d2e5acaa29f0b4a77973f69692309a15c950593912760ba2bccba2b9f88"
        },
        {
          "source_id": "B.md",
          "artifact_id": "B.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-11T09:00:00+08:00] 陈晨: 我负责星河项目的通知网关接入；本条对应A.md的SMS-17，是同一个任务，仅更换称呼，不修订任务计划。\n",
          "content_sha256": "c5ea1cf3afd06d5973ff7d91f63a07ab532946b0272b6284db7dd4ee2a7b2108"
        }
      ]
    },
    {
      "case_id": "D04",
      "workspace_id": "m2-task-identity-dev-d04",
      "sources": [
        {
          "source_id": "A.md",
          "artifact_id": "A.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4 的签名接口联调，计划在2026年10月14日前完成。\n",
          "content_sha256": "7ed1f68cfa65f67b5fe84a07d6fabb7ce1cbeb3bf91e49282f3962d7cd4a7d2f"
        },
        {
          "source_id": "B.md",
          "artifact_id": "B.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4；本条就是A.md的同一任务，现修订其计划，将原定2026年10月14日前完成改为2026年10月18日前完成，本计划替代原计划。\n",
          "content_sha256": "20fd3f55d9f72258ace32757206e7514c424b4970cd74545d31288216ca644ac"
        },
        {
          "source_id": "C.md",
          "artifact_id": "C.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-16T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4，仍是B.md的同一任务；本条替代B.md计划，仅在原任务范围内增加回执校验，截止边界不变，仍在2026年10月18日前完成。\n",
          "content_sha256": "e42f30d5536e368e54f67cd39f04a999433304798510ecd80ad41acd79696094"
        }
      ]
    },
    {
      "case_id": "D05",
      "workspace_id": "m2-task-identity-dev-d05",
      "sources": [
        {
          "source_id": "A.md",
          "artifact_id": "A.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-5 的接口联调，计划在2026年10月14日前完成。\n",
          "content_sha256": "c0800b0ef3a18f410d3e70fbf49939eef1940eb2c941c1bd3e4ffe3baaa27d2c"
        },
        {
          "source_id": "B.md",
          "artifact_id": "B.md",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-5 的接口联调，计划在2026年10月18日前完成。\n",
          "content_sha256": "4e2fed0b47740a7934f2670191ddb9d4cf6e6552a54dd12393a7c015fb05d889"
        }
      ]
    },
    {
      "case_id": "D07",
      "workspace_id": "m2-task-identity-dev-d07",
      "sources": [
        {
          "source_id": "A.txt",
          "artifact_id": "A.txt",
          "source_type": "timestamped_text",
          "content": "[2026-10-10T09:00:00+08:00] 林悦: 星河项目今天正式确定，短信服务供应商选择云桥科技，自2026年10月10日起生效。选择原因是报价较低，且支持国内短信发送。\n[2026-10-10T09:05:00+08:00] 陈晨: 我负责星河项目的短信接口接入，计划在2026年10月14日前完成联调。\n",
          "content_sha256": "a8ea56b3f56c00d524b289b4eb5f5b3cb9a7c5d04411ad0afc53e6f532881662"
        },
        {
          "source_id": "B.txt",
          "artifact_id": "B.txt",
          "source_type": "timestamped_text",
          "content": "[2026-10-15T10:00:00+08:00] 林悦: 星河项目的短信服务供应商现正式改为星帆通信，自2026年10月15日起生效，替代2026年10月10日选择云桥科技的决定。原因是云桥科技在联调中多次延迟，星帆通信的到达率更稳定。\n[2026-10-15T10:05:00+08:00] 陈晨: 我继续负责短信接口接入，将按新供应商星帆通信的接口调整，计划在2026年10月18日前完成。\n[2026-10-15T10:10:00+08:00] 周宁: 我建议以后考虑增加邮件通知作为备用渠道，但这只是建议，本次会议尚未批准，也没有确定上线日期。\n",
          "content_sha256": "7857add46076081cb6ecd2058d20d523ffb2800c7ae7821c229da23a77de505c"
        }
      ]
    },
    {
      "case_id": "V01",
      "workspace_id": "m2-task-revisions-dev-v01",
      "sources": [
        {
          "source_id": "old.md",
          "artifact_id": "old.md",
          "source_type": "timestamped_text",
          "content": "[2026-11-03T09:00:00+08:00] 许澄: 我负责青岚平台项目任务 SYNC-41 的数据同步，计划在11月14日前完成。\n",
          "content_sha256": "716489b8b6c674af0337482fcfa0ca1b9f67e75e32ccd47756cfa070d54f6f95"
        },
        {
          "source_id": "new.md",
          "artifact_id": "new.md",
          "source_type": "timestamped_text",
          "content": "[2026-11-06T09:00:00+08:00] 许澄: 我负责青岚平台项目任务 SYNC-41；本条是old.md的同一任务，现修订计划，将原定11月14日前完成改为2026年11月18日前完成，本计划替代原计划。\n",
          "content_sha256": "faa22ccbe06fe3d9ad6074e0eaa029e2131206c4a984a65b69063bc6df02f126"
        }
      ]
    },
    {
      "case_id": "V02",
      "workspace_id": "m2-task-revisions-dev-v02",
      "sources": [
        {
          "source_id": "before.md",
          "artifact_id": "before.md",
          "source_type": "timestamped_text",
          "content": "[2026-11-07T09:00:00+08:00] 罗宁: 我负责澄明门户项目任务 AUTH-52 的认证联调，计划在2026年11月20日前完成。\n",
          "content_sha256": "51f3de8593428031c76feed7f8f39ab999e1b28e47682d44c11a4d387c8a9bcf"
        },
        {
          "source_id": "after.md",
          "artifact_id": "after.md",
          "source_type": "timestamped_text",
          "content": "[2026-11-09T09:00:00+08:00] 罗宁: 我负责澄明门户项目任务 AUTH-52；本条是before.md的同一任务，现修订截止边界并替代原计划，截止日期为2026年11月20日，按该日或之前完成。\n",
          "content_sha256": "58702d503839918ccf09b32556bbd603942d57d77f913aeb3b6c3e55639709df"
        }
      ]
    },
    {
      "case_id": "V03",
      "workspace_id": "m2-task-revisions-dev-v03",
      "sources": [
        {
          "source_id": "recorded.md",
          "artifact_id": "recorded.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-01T09:00:00+08:00] 林嘉: 我负责赤松云项目任务 REL-63 的回归验证，计划在2026年12月09日前完成。\n",
          "content_sha256": "849240f3ad570cc9568f2548a4cdd7222de6e7573325996d104f3a81e9cb933d"
        },
        {
          "source_id": "corrected.md",
          "artifact_id": "corrected.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-03T09:00:00+08:00] 林嘉: 我负责赤松云项目任务 REL-63；本条是recorded.md的同一任务，资料更正为2026年12月05日前完成，修正录入错误，不是业务计划修订，业务截止一直是2026年12月05日前。\n",
          "content_sha256": "bf06230de390e059cd584ded38f85d3dcb343d12446d8bf22423e9fb22bbf16a"
        }
      ]
    },
    {
      "case_id": "V04",
      "workspace_id": "m2-task-revisions-dev-v04",
      "sources": [
        {
          "source_id": "base.md",
          "artifact_id": "base.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-04T09:00:00+08:00] 韩璇: 我负责云杉结算项目任务 BILL-74 的对账导出，计划在2026年12月10日前完成。\n",
          "content_sha256": "c8a44edcba2ff103d5e56f46d1d8084c9331d3f24331884c3b83a0e7aef92e29"
        },
        {
          "source_id": "revision-a.md",
          "artifact_id": "revision-a.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-07T09:00:00+08:00] 韩璇: 我负责云杉结算项目任务 BILL-74；本条是base.md的同一任务，现将原定2026年12月10日前完成改为2026年12月14日前完成，替代原计划。\n",
          "content_sha256": "e3ff86ff03387d3e602a398ec5d699032633d3426f472f4205a2fe4aad9a208c"
        },
        {
          "source_id": "revision-b.md",
          "artifact_id": "revision-b.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-08T09:00:00+08:00] 韩璇: 我负责云杉结算项目任务 BILL-74；本条也是base.md的同一任务，现将原定2026年12月10日前完成改为2026年12月12日前完成，替代原计划；该来源仍有效。\n",
          "content_sha256": "bfcf1b02e38adcea588f5e4d0c25e0c46682598b674fcde4874676f25c2e100c"
        }
      ]
    },
    {
      "case_id": "V05",
      "workspace_id": "m2-task-revisions-dev-v05",
      "sources": [
        {
          "source_id": "current.md",
          "artifact_id": "current.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-10T09:00:00+08:00] 周宁: 我负责澄海数据项目任务 MIG-85 的迁移校验，计划在2026年12月20日前完成。\n",
          "content_sha256": "fc59f9f01c852b45f9de73888d50350558b675293ad224b3a4aaac3dc37976ab"
        },
        {
          "source_id": "future.md",
          "artifact_id": "future.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-10T10:00:00+08:00] 周宁: 我负责澄海数据项目任务 MIG-85；本条是current.md的同一任务，现修订计划，将原定2026年12月20日前完成改为2026年12月25日前完成，本计划自2026年12月20日起生效并替代原计划。\n",
          "content_sha256": "5f40f1e45b45f93107e73ca7dd712a92089fa6af48f3c6a40232ebfc3d303489"
        }
      ]
    },
    {
      "case_id": "V06",
      "workspace_id": "m2-task-revisions-dev-v06",
      "sources": [
        {
          "source_id": "old.md",
          "artifact_id": "old.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-11T09:00:00+08:00] 沈禾: 我负责霁川分析项目任务 QA-96 的验收脚本，计划在2026年12月30日前完成。\n",
          "content_sha256": "aa949e526177e141e56a0e88f720dfe0621ece41040cbbb11d0325a6b893bc75"
        },
        {
          "source_id": "new.md",
          "artifact_id": "new.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-13T09:00:00+08:00] 沈禾: 我负责霁川分析项目任务 QA-96；本条是old.md的同一任务，现将原定2026年12月30日前完成改为2026年12月24日前完成，本计划替代原计划。\n",
          "content_sha256": "2960c93df776bdd00c8423661970be3363545dc0aae1355b843992596a70bf55"
        }
      ]
    },
    {
      "case_id": "V07",
      "workspace_id": "m2-task-revisions-dev-v07",
      "sources": [
        {
          "source_id": "prior.md",
          "artifact_id": "prior.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-15T09:00:00+08:00] 郁宁: 我负责北辰仓项目任务 DOC-107 的接口文档，计划在2027年01月15日前完成。\n",
          "content_sha256": "17126f676657fc500dd9f2cf0431578336f8675f02c0f215eae4ad3f4f0ecfad"
        },
        {
          "source_id": "revision.md",
          "artifact_id": "revision.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-17T09:00:00+08:00] 郁宁: 我负责北辰仓项目任务 DOC-107；本条是prior.md的同一任务，现修订截止计划，将原定2027年01月15日前完成改为2027年01月15日前完成，本计划替代原计划。\n",
          "content_sha256": "2dcaeb8773a2c98f0e87031cddf1fd77481fa8da145aba7e25994f16bbc70924"
        }
      ]
    }
  ]
}
```

### tests/fixtures/task_semantic/integration-dev-v2.json

```json
{
  "input_path": "tests/fixtures/task_semantic/integration-dev-v2.json",
  "input_sha256": "e5d666c74ad4f87b6ac0c52a545c8f00319ef85f2123c4e60c5f7443a644ab98",
  "git_blob_id": "a20e0002f1ee22e17c1bec875359f335dd51cae1",
  "cases": [
    {
      "case_id": "m3d-causality-investigation-v2",
      "workspace_id": "semantic-dev-causality",
      "sources": [
        {
          "source_id": "choice.md",
          "artifact_id": "choice.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-02T10:00:00+08:00] 李棠: 澄川项目的短信供应商此次换成了远铃，之前使用的是岚声；这份资料尚未经过人工审核。\n",
          "content_sha256": "42d70d3c72f2ee9d2bf9369800616efd5b8ac85e543e643732aed4bbd4aaa74e"
        },
        {
          "source_id": "work.md",
          "artifact_id": "work.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-02T10:05:00+08:00] 许禾: 我负责澄川项目任务 CC-81 的签名适配，计划在2026年12月16日前完成；choice.md所述更换直接导致签名规范变化，所以这项工作必须重做签名映射。\n[2026-12-02T10:06:00+08:00] 许禾: 我负责澄川项目任务 CC-82 的日志归档，计划在2026年12月16日前完成；就choice.md这次供应商更换而言，归档工作完全不受影响，无须调整。\n[2026-12-02T10:07:00+08:00] 许禾: 我负责澄川项目任务 CC-83 的缓存优化，计划在2026年12月16日前完成。\n",
          "content_sha256": "0d1f5431eb92ac3e3801ec50be078c8e6aca1f5c9fac224c24b534d2fe143e35"
        },
        {
          "source_id": "investigation",
          "artifact_id": "investigation-v2.md",
          "source_type": "timestamped_text",
          "content": "[2026-12-11 10:00] 调查员：已核对 choice.md 与 work.md，调查范围包含签名适配、日志归档和缓存压测；压测与供应商更换之间仍无明确影响桥接。",
          "content_sha256": "e7f71d60e593f9d78190aee539f4a3742a88f25872531d46bce64143287a6872"
        }
      ]
    }
  ]
}
```

合计：31 组开发输入，58 份来源条目；保留重复，不声称是 58 个独立任务。尚无新题目、标签或用户确认。
